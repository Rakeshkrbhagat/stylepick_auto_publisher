"""Facebook Login (desktop flow via localhost redirect), Page and Instagram account selection.

Credentials: user/page tokens live only in the OS credential vault (``JsonSecureStore``);
the app secret comes from the environment/.env or the vault (never source code).

Least privilege: only the scopes for the enabled platforms (``META_PLATFORMS``) are
requested (verify against current Meta docs before going live):
- Facebook Page Reels: ``pages_show_list``, ``pages_read_engagement``, ``pages_manage_posts``
- Instagram Reels: ``instagram_basic``, ``instagram_content_publish``
  (+ ``pages_show_list``, ``pages_read_engagement``)

Recovery: invalid/expired/revoked tokens are detected via ``/debug_token``; the stored
state is flagged ``needs_reauth`` and the UI offers a one-click reconnect.
"""

from __future__ import annotations

import secrets
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from src.meta_errors import (
    MetaAuthCancelledError, MetaError, MetaNotConfiguredError, MetaNotConnectedError,
    MetaPermissionError, MetaTokenExpiredError,
)
from src.meta_graph import GraphClient
from src.secure_store import CredentialStoreError, JsonSecureStore

REQUIRED_PERMISSIONS = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
INSTAGRAM_PERMISSIONS = ["pages_show_list", "pages_read_engagement", "instagram_basic",
                         "instagram_content_publish"]
PLATFORM_SCOPES = {"facebook": REQUIRED_PERMISSIONS, "instagram": INSTAGRAM_PERMISSIONS}
ALL_SCOPES = list(dict.fromkeys(REQUIRED_PERMISSIONS + INSTAGRAM_PERMISSIONS))
PUBLISH_TASKS = {"CREATE_CONTENT", "MANAGE"}
TOKEN_KEY = "meta_tokens"


def scopes_for(platforms) -> list[str]:
    out: list[str] = []
    for p in platforms:
        out += PLATFORM_SCOPES.get(str(p).strip().lower(), [])
    return list(dict.fromkeys(out)) or ALL_SCOPES


@dataclass
class FacebookPage:
    id: str
    name: str
    access_token: str = field(repr=False, default="")
    tasks: list[str] = field(default_factory=list)

    @property
    def can_publish(self) -> bool:
        return bool(PUBLISH_TASKS & set(self.tasks))


@dataclass
class InstagramAccount:
    id: str
    username: str
    page_id: str
    page_name: str
    access_token: str = field(repr=False, default="")
    page_can_publish: bool = True

    @property
    def label(self) -> str:
        return f"@{self.username} (via {self.page_name})"


@dataclass
class TokenStatus:
    valid: bool
    expires_at: int = 0  # 0 = never
    scopes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    message: str = ""

    def missing_for(self, required: list[str]) -> list[str]:
        return [p for p in required if p not in self.scopes]


class MetaTokenStore(JsonSecureStore):
    def __init__(self, backend=None):
        super().__init__(TOKEN_KEY, backend=backend)


class MetaAuthManager:
    def __init__(self, app_id: str, app_secret: str, graph: GraphClient,
                 store: MetaTokenStore | None = None, redirect_port: int = 8765,
                 platforms=("facebook", "instagram")):
        self.app_id, self._secret, self.graph = app_id, app_secret, graph
        self.store = store or MetaTokenStore()
        self.redirect_uri = f"http://localhost:{redirect_port}/"
        self.redirect_port = redirect_port
        self.scopes = scopes_for(platforms)

    def __repr__(self) -> str:  # never expose the secret
        return f"MetaAuthManager(app_id={self.app_id!r}, scopes={self.scopes})"

    # ---------------- state
    def configured(self) -> bool:
        return bool(self.app_id and self._secret)

    def _data(self) -> dict:
        try:
            return self.store.load()
        except (CredentialStoreError, Exception):
            return {}

    def _save(self, data: dict) -> None:
        try:
            self.store.save(data)
        except CredentialStoreError as exc:
            raise MetaError(str(exc)) from None

    def _update(self, **kw) -> None:
        data = self._data()
        data.update(kw)
        self._save(data)

    def user_token(self) -> str:
        tok = self._data().get("user_token")
        if not tok:
            raise MetaNotConnectedError()
        return tok

    def is_connected(self) -> bool:
        return bool(self._data().get("user_token"))

    def needs_reauth(self) -> bool:
        return bool(self._data().get("needs_reauth"))

    def mark_needs_reauth(self, reason: str = "") -> None:
        if self.is_connected():
            self._update(needs_reauth=True, reauth_reason=reason[:200])

    def selected_page(self) -> FacebookPage | None:
        p = self._data().get("page")
        return FacebookPage(**p) if p else None

    def selected_instagram(self) -> InstagramAccount | None:
        a = self._data().get("instagram")
        return InstagramAccount(**a) if a else None

    # ---------------- login
    def login_url(self, state: str) -> str:
        q = urlencode({"client_id": self.app_id, "redirect_uri": self.redirect_uri,
                       "state": state, "response_type": "code", "scope": ",".join(self.scopes),
                       "auth_type": "rerequest"})
        return f"https://www.facebook.com/{self.graph.version}/dialog/oauth?{q}"

    def connect(self, timeout_s: int = 300) -> None:
        """Login (also used for re-authorization). Keeps the selected Page/IG account if the
        same user reconnects, but replaces all tokens."""
        if not self.configured():
            raise MetaNotConfiguredError()
        state = secrets.token_urlsafe(24)
        result: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs = parse_qs(urlparse(self.path).query)
                if "code" in qs or "error" in qs:
                    result.update({k: v[0] for k, v in qs.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("Facebook login finished. You can close this tab and return to "
                                 "StylePick.".encode())

            def log_message(self, *a):  # do not log the callback URL (contains the code)
                pass

        server = HTTPServer(("localhost", self.redirect_port), Handler)
        server.timeout = 2
        webbrowser.open(self.login_url(state))
        deadline = time.time() + timeout_s
        try:
            while not result and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not result or "error" in result:
            raise MetaAuthCancelledError()
        if not secrets.compare_digest(result.get("state", ""), state):
            raise MetaError("Facebook login failed: state mismatch (possible CSRF). Try again.")
        self.complete_login(result["code"])

    def complete_login(self, code: str) -> None:
        short = self.graph.request("GET", "oauth/access_token", params={
            "client_id": self.app_id, "client_secret": self._secret,
            "redirect_uri": self.redirect_uri, "code": code})["access_token"]
        long = self.graph.request("GET", "oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": self.app_id,
            "client_secret": self._secret, "fb_exchange_token": short})["access_token"]
        # page/IG tokens are re-fetched after reconnect -> drop stale selections' tokens
        self._save({"user_token": long})

    def disconnect(self) -> None:
        data = self._data()
        if data.get("user_token"):
            try:  # revoke this app's permissions for the user
                self.graph.request("DELETE", "me/permissions", data["user_token"], retries=0)
            except MetaError:
                pass
        self.store.delete()

    # ---------------- verification
    def check_token(self, token: str | None = None,
                    required: list[str] | None = None) -> TokenStatus:
        """Validate a token via /debug_token and check required permissions.
        Invalid tokens flag the account for re-authorization."""
        token = token or self.user_token()
        required = REQUIRED_PERMISSIONS if required is None else required
        try:
            info = self.graph.request("GET", "debug_token", f"{self.app_id}|{self._secret}",
                                      params={"input_token": token}).get("data", {})
        except MetaTokenExpiredError as exc:
            self.mark_needs_reauth(str(exc))
            return TokenStatus(False, message=str(exc))
        if not info.get("is_valid"):
            msg = info.get("error", {}).get("message", "Token is invalid or expired.")
            self.mark_needs_reauth(msg)
            return TokenStatus(False, message=msg)
        scopes = info.get("scopes", [])
        missing = [p for p in required if p not in scopes]
        exp = int(info.get("data_access_expires_at") or info.get("expires_at") or 0)
        if exp and exp < time.time():
            self.mark_needs_reauth("Token expired.")
            return TokenStatus(False, exp, scopes, missing, "Token expired.")
        return TokenStatus(True, exp, scopes, missing,
                           "Missing permissions: " + ", ".join(missing) if missing else "OK")

    def require_valid(self, required: list[str] | None = None) -> TokenStatus:
        st = self.check_token(required=required)
        if not st.valid:
            raise MetaTokenExpiredError(f"Facebook token invalid: {st.message} Please reconnect.")
        if st.missing:
            raise MetaPermissionError(f"Missing permissions: {', '.join(st.missing)}. "
                                      "Reconnect and approve all requested permissions.")
        return st

    # ---------------- pages
    def list_pages(self) -> list[FacebookPage]:
        token, pages, after = self.user_token(), [], None
        while True:
            params = {"fields": "id,name,access_token,tasks", "limit": 100}
            if after:
                params["after"] = after
            resp = self.graph.request("GET", "me/accounts", token, params=params)
            pages += [FacebookPage(p["id"], p.get("name", ""), p.get("access_token", ""),
                                   p.get("tasks", [])) for p in resp.get("data", [])]
            after = resp.get("paging", {}).get("cursors", {}).get("after") \
                if resp.get("paging", {}).get("next") else None
            if not after:
                return pages

    def select_page(self, page: FacebookPage) -> None:
        if not page.can_publish:
            raise MetaPermissionError(f"Your role on '{page.name}' cannot create content "
                                      "(needs CREATE_CONTENT task).")
        if not page.access_token:
            raise MetaPermissionError("No Page access token returned – check Page permissions.")
        self._update(page=asdict(page))

    def page_for_publishing(self) -> FacebookPage:
        page = self.selected_page()
        if not page:
            raise MetaNotConnectedError("No Facebook Page selected. Choose a Page in the sidebar.")
        st = self.check_token(page.access_token)
        if not st.valid:
            raise MetaTokenExpiredError(f"Page token invalid: {st.message} Reconnect Facebook.")
        if st.missing:
            raise MetaPermissionError(f"Page token missing permissions: {', '.join(st.missing)}.")
        return page

    # ---------------- instagram
    def list_instagram_accounts(self, pages: list[FacebookPage] | None = None
                                ) -> tuple[list[InstagramAccount], list[str]]:
        accounts, notes = [], []
        for page in pages if pages is not None else self.list_pages():
            try:
                info = self.graph.request(
                    "GET", page.id, page.access_token,
                    params={"fields": "instagram_business_account{id,username,name}"})
            except MetaPermissionError:
                notes.append(f"'{page.name}': no permission to read the linked Instagram account.")
                continue
            ig = info.get("instagram_business_account")
            if not ig:
                notes.append(f"'{page.name}': no Instagram Professional (Business/Creator) account "
                             "linked. Personal accounts are not supported by the publishing API.")
                continue
            accounts.append(InstagramAccount(ig["id"], ig.get("username") or ig.get("name", ""),
                                             page.id, page.name, page.access_token,
                                             page.can_publish))
        return accounts, notes

    def select_instagram(self, account: InstagramAccount) -> None:
        if not account.access_token:
            raise MetaPermissionError("No access token for the linked Page.")
        if not account.page_can_publish:
            raise MetaPermissionError(f"Your role on '{account.page_name}' cannot create content, "
                                      "so it cannot publish to the linked Instagram account.")
        self._update(instagram=asdict(account))

    def instagram_for_publishing(self) -> InstagramAccount:
        acc = self.selected_instagram()
        if not acc:
            raise MetaNotConnectedError("No Instagram account selected. Choose one in the sidebar.")
        st = self.check_token(acc.access_token, required=INSTAGRAM_PERMISSIONS)
        if not st.valid:
            raise MetaTokenExpiredError(f"Instagram token invalid: {st.message} Reconnect Facebook.")
        if st.missing:
            raise MetaPermissionError("Instagram publishing permissions missing: "
                                      f"{', '.join(st.missing)}. Reconnect and approve them.")
        return acc

