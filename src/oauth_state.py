"""Signed, stateless OAuth ``state`` values (no database or server-side session needed).

The state carries a timestamp and random nonce and is HMAC-SHA256 signed with a key derived
from the Google OAuth client secret (or OAUTH_STATE_SECRET if set), so a callback can be
verified even though Streamlit starts a new session after the Google redirect.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

MAX_AGE_SECONDS = 15 * 60


def signing_key(client_secrets_file: Path, override: str = "") -> bytes:
    if override:
        return hashlib.sha256(override.encode()).digest()
    try:
        data = json.loads(Path(client_secrets_file).read_text(encoding="utf-8"))
        secret = (data.get("web") or data.get("installed") or {}).get("client_secret", "")
    except (OSError, ValueError):
        secret = ""
    if not secret:
        raise ValueError("No OAuth client secret available to sign the sign-in request.")
    return hashlib.sha256(b"oauth-state:" + secret.encode()).digest()


def _sig(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_state(key: bytes, now: float | None = None) -> str:
    payload = f"{int(now if now is not None else time.time())}.{secrets.token_urlsafe(12)}"
    raw = f"{payload}.{_sig(key, payload)}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def verify_state(key: bytes, state: str, now: float | None = None) -> bool:
    try:
        raw = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)).decode()
        ts, nonce, sig = raw.split(".")
        payload = f"{ts}.{nonce}"
    except (ValueError, UnicodeDecodeError):
        return False
    if not hmac.compare_digest(sig, _sig(key, payload)):
        return False
    age = (now if now is not None else time.time()) - int(ts)
    return 0 <= age <= MAX_AGE_SECONDS


class SessionTokenStore:
    """Keeps the OAuth token only in the current browser session's memory (never on disk)."""

    def __init__(self, holder: dict, key: str = "google_token"):
        self.holder, self.key = holder, key

    def load(self) -> str | None:
        return self.holder.get(self.key)

    def save(self, value: str) -> None:
        self.holder[self.key] = value

    def delete(self) -> None:
        self.holder.pop(self.key, None)

