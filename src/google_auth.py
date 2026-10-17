"""Google OAuth 2.0 (installed/desktop app flow) with tokens stored in the OS credential vault.
        self._creds = None
        self.store.delete()
                    pass
                except requests.RequestException:
                                  data={"token": token}, timeout=10)
                    requests.post("https://oauth2.googleapis.com/revoke",
                try:
            if token:
            token = data.get("refresh_token") or data.get("token")
                data = {}
            except ValueError:
                data = json.loads(raw)
            try:
        if raw:
            raw = None
        except CredentialStoreError:
            raw = self.store.load()
        try:

        import requests
        """Revoke the token at Google (best effort) and remove it from the OS vault."""
    def disconnect(self) -> None:

        self._save(creds)
        self._creds = creds
                            "and allow access.")
            raise AuthError("Google access was not granted for Drive (read-only). Please reconnect "
        if not set(SCOPES) <= set(creds.scopes or SCOPES):
            raise AuthCancelledError()
        if creds is None:
            raise AuthError(f"Google sign-in failed: {redact(exc)}") from None
                raise AuthCancelledError() from None
                    or isinstance(exc, (AttributeError, TimeoutError))):
            if (any(s in msg for s in ("access_denied", "cancel", "timed out"))
            msg = str(exc).lower()
        except Exception as exc:
                                "StylePick.")
                success_message="Google account connected. You can close this tab and return to "
                authorization_prompt_message="",
                port=0, open_browser=True, prompt="consent", timeout_seconds=300,
            creds = flow.run_local_server(
        try:
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets_file), SCOPES)
                "(or set GOOGLE_CLIENT_SECRETS_FILE).")
                "Create a 'Desktop app' OAuth client in Google Cloud Console and save its JSON there "
                f"OAuth client file not found at '{self.client_secrets_file}'. "
            raise AuthError(
        if not self.client_configured():

        from google_auth_oauthlib.flow import InstalledAppFlow
        """Run the local-server OAuth flow (opens the system browser)."""
    def connect(self) -> None:
    # ----- connect / disconnect -----

            return None
        except Exception:
            return svc.about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]
            svc = build("drive", "v3", credentials=self.get_credentials(), cache_discovery=False)

            from googleapiclient.discovery import build
        try:
    def account_email(self) -> str | None:

            return False
        except Exception:
            return True
            self.get_credentials()
        try:
    def is_connected(self) -> bool:

        raise AuthExpiredError()
        self.store.delete()
        self._creds = None
            return self._creds
            self._save(self._creds)
                                "check your connection and retry.") from exc
                raise AuthError("Network error while refreshing Google authorization – "
            except TransportError as exc:
                raise AuthExpiredError() from exc
                self.store.delete()
                self._creds = None
            except RefreshError as exc:  # revoked or expired refresh token
                self._creds.refresh(Request())
            try:
        if refresh and self._creds.expired and self._creds.refresh_token:
            return self._creds
        if self._creds.valid:
                                       "different permissions. Please reconnect.") from exc
                raise AuthExpiredError("Stored Google authorization is invalid or uses "
                self.store.delete()
            except (ValueError, KeyError) as exc:
                self._creds = Credentials.from_authorized_user_info(info, SCOPES)
                    raise ValueError("scope mismatch")
                if not set(SCOPES) <= granted:
                granted = set(info.get("scopes") or SCOPES)
                info = json.loads(raw)
            try:
                raise NotConnectedError()
            if not raw:
            raw = self._load_raw()
        if self._creds is None:

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError, TransportError
        """Return valid credentials, refreshing if needed. Raises Drive auth errors."""
    def get_credentials(self, refresh: bool = True):

            raise AuthError(str(exc)) from None
        except CredentialStoreError as exc:
            self.store.save(creds.to_json())
        try:
    def _save(self, creds) -> None:

            raise AuthError(str(exc)) from None
        except CredentialStoreError as exc:
            return self.store.load()
        try:
    def _load_raw(self) -> str | None:

        return self.client_secrets_file.is_file()
    def client_configured(self) -> bool:
    # ----- status -----

        self._creds = None
        self.store = store or TokenStore()
        self.client_secrets_file = Path(client_secrets_file)
    def __init__(self, client_secrets_file: Path, store: TokenStore | None = None):
class GoogleAuthManager:


        super().__init__(TOKEN_KEY, backend=backend)
    def __init__(self, backend=None):

    """OAuth token JSON in Windows Credential Manager / macOS Keychain / Secret Service."""
class TokenStore(SecureStore):


TOKEN_KEY = "google_oauth_token"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

from src.secure_store import CredentialStoreError, SecureStore
from src.redaction import redact
from src.drive_errors import AuthCancelledError, AuthError, AuthExpiredError, NotConnectedError

from pathlib import Path
import json

from __future__ import annotations

"""
to reconnect.
revoked or expired, the stored token is deleted and ``AuthExpiredError`` asks the user
Recovery: an expired access token is refreshed automatically; if the refresh token was

by pasting a URL. The app never writes to Drive.
access to files created/opened by this app, so it cannot list an existing folder chosen
Least privilege: ``drive.readonly`` only. The narrower ``drive.file`` scope only grants


