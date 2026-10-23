"""Google OAuth 2.0 (installed/desktop app flow) with tokens stored in the OS credential vault.

Least privilege: ``drive.readonly`` only. The narrower ``drive.file`` scope only grants
access to files created/opened by this app, so it cannot list an existing folder chosen
by pasting a URL. The app never writes to Drive.

Recovery: an expired access token is refreshed automatically; if the refresh token was
revoked or expired, the stored token is deleted and ``AuthExpiredError`` asks the user
to reconnect.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.drive_errors import AuthCancelledError, AuthError, AuthExpiredError, NotConnectedError
from src.redaction import redact
from src.secure_store import CredentialStoreError, SecureStore

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
TOKEN_KEY = "google_oauth_token"


class TokenStore(SecureStore):
    """OAuth token JSON in Windows Credential Manager / macOS Keychain / Secret Service."""

    def __init__(self, backend=None):
        super().__init__(TOKEN_KEY, backend=backend)


class GoogleAuthManager:
    def __init__(self, client_secrets_file: Path, store: TokenStore | None = None):
        self.client_secrets_file = Path(client_secrets_file)
        self.store = store or TokenStore()
        self._creds = None

    # ----- status -----
    def client_configured(self) -> bool:
        return self.client_secrets_file.is_file()

    def _load_raw(self) -> str | None:
        try:
            return self.store.load()
        except CredentialStoreError as exc:
            raise AuthError(str(exc)) from None

    def _save(self, creds) -> None:
        try:
            self.store.save(creds.to_json())
        except CredentialStoreError as exc:
            raise AuthError(str(exc)) from None

    def get_credentials(self, refresh: bool = True):
        """Return valid credentials, refreshing if needed. Raises Drive auth errors."""
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self._creds is None:
            raw = self._load_raw()
            if not raw:
                raise NotConnectedError()
            try:
                info = json.loads(raw)
                granted = set(info.get("scopes") or SCOPES)
                if not set(SCOPES) <= granted:
                    raise ValueError("scope mismatch")
                self._creds = Credentials.from_authorized_user_info(info, SCOPES)
            except (ValueError, KeyError) as exc:
                self.store.delete()
                raise AuthExpiredError("Stored Google authorization is invalid or uses "
                                       "different permissions. Please reconnect.") from exc
        if self._creds.valid:
            return self._creds
        if refresh and self._creds.expired and self._creds.refresh_token:
            try:
                self._creds.refresh(Request())
            except RefreshError as exc:  # revoked or expired refresh token
                self._creds = None
                self.store.delete()
                raise AuthExpiredError() from exc
            except TransportError as exc:
                raise AuthError("Network error while refreshing Google authorization – "
                                "check your connection and retry.") from exc
            self._save(self._creds)
            return self._creds
        self._creds = None
        self.store.delete()
        raise AuthExpiredError()

    def is_connected(self) -> bool:
        try:
            self.get_credentials()
            return True
        except Exception:
            return False

    def account_email(self) -> str | None:
        try:
            from googleapiclient.discovery import build

            svc = build("drive", "v3", credentials=self.get_credentials(), cache_discovery=False)
            return svc.about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]
        except Exception:
            return None

    # ----- connect / disconnect -----
    def connect(self) -> None:
        """Run the local-server OAuth flow (opens the system browser)."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.client_configured():
            raise AuthError(
                f"OAuth client file not found at '{self.client_secrets_file}'. "
                "Create a 'Desktop app' OAuth client in Google Cloud Console and save its JSON there "
                "(or set GOOGLE_CLIENT_SECRETS_FILE).")
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets_file), SCOPES)
        try:
            creds = flow.run_local_server(
                port=0, open_browser=True, prompt="consent", timeout_seconds=300,
                authorization_prompt_message="",
                success_message="Google account connected. You can close this tab and return to "
                                "the app.")
        except Exception as exc:
            msg = str(exc).lower()
            if (any(s in msg for s in ("access_denied", "cancel", "timed out"))
                    or isinstance(exc, (AttributeError, TimeoutError))):
                raise AuthCancelledError() from None
            raise AuthError(f"Google sign-in failed: {redact(exc)}") from None
        if creds is None:
            raise AuthCancelledError()
        if not set(SCOPES) <= set(creds.scopes or SCOPES):
            raise AuthError("Google access was not granted for Drive (read-only). Please reconnect "
                            "and allow access.")
        self._creds = creds
        self._save(creds)

    # ----- hosted (web redirect) flow -----
    def _web_flow(self, redirect_uri: str, state: str | None = None):
        from google_auth_oauthlib.flow import Flow

        if not self.client_configured():
            raise AuthError(f"OAuth client file not found at '{self.client_secrets_file}'.")
        # Confidential "Web application" client: authenticated with its client secret, so no
        # PKCE verifier has to be kept between the redirect and the callback (no storage).
        return Flow.from_client_secrets_file(str(self.client_secrets_file), SCOPES, state=state,
                                             redirect_uri=redirect_uri,
                                             autogenerate_code_verifier=False)

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        flow = self._web_flow(redirect_uri, state)
        url, _ = flow.authorization_url(access_type="online", prompt="consent",
                                        include_granted_scopes="true", state=state)
        return url

    def complete_web_flow(self, redirect_uri: str, state: str, code: str) -> None:
        flow = self._web_flow(redirect_uri, state)
        try:
            flow.fetch_token(code=code)
        except Exception as exc:
            raise AuthError(f"Google sign-in failed: {redact(exc)}") from None
        creds = flow.credentials
        if not set(SCOPES) <= set(creds.scopes or SCOPES):
            raise AuthError("Google access was not granted for Drive (read-only).")
        self._creds = creds
        self._save(creds)

    def disconnect(self) -> None:
        """Revoke the token at Google (best effort) and remove it from the OS vault."""
        import requests

        try:
            raw = self.store.load()
        except CredentialStoreError:
            raw = None
        if raw:
            try:
                data = json.loads(raw)
            except ValueError:
                data = {}
            token = data.get("refresh_token") or data.get("token")
            if token:
                try:
                    requests.post("https://oauth2.googleapis.com/revoke",
                                  data={"token": token}, timeout=10)
                except requests.RequestException:
                    pass
        self.store.delete()
        self._creds = None

