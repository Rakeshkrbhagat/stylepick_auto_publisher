"""Credential storage, redaction, least-privilege and re-authorization tests."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import pytest

from src.drive_errors import AuthExpiredError, NotConnectedError
from src.google_auth import SCOPES, GoogleAuthManager, TokenStore
from src.redaction import install_log_redaction, redact
from src.secure_store import CredentialStoreError, JsonSecureStore, SecureStore

ROOT = Path(__file__).resolve().parents[1]


class MemoryKeyring:
    """Stand-in for a secure OS keyring backend."""

    def __init__(self):
        self.data = {}

    def get_password(self, s, k):
        return self.data.get((s, k))

    def set_password(self, s, k, v):
        self.data[(s, k)] = v

    def delete_password(self, s, k):
        del self.data[(s, k)]


class PlaintextKeyring(MemoryKeyring):
    pass



# ---------- secure storage ----------
def test_secure_store_roundtrip_and_delete():
    s = SecureStore("k", backend=MemoryKeyring())
    s.set("value")
    assert s.get() == "value"
    s.delete()
    assert s.get() is None
    s.delete()  # idempotent


def test_insecure_backend_refused():
    with pytest.raises(CredentialStoreError):
        SecureStore("k", backend=PlaintextKeyring()).set("secret")


def test_corrupt_json_entry_is_removed():
    s = JsonSecureStore("j", backend=MemoryKeyring())
    s.set("{not json")
    assert s.load() == {}
    assert s.get() is None


# ---------- redaction ----------
@pytest.mark.parametrize("text", [
    "https://graph.facebook.com/me?access_token=EAAabcdefghijklmnopqrstuvwxyz123",
    '{"refresh_token": "1//0abcdefghijklmnopqrstuvwxyz"}',
    "Authorization: Bearer ya29.a0AfH6SMBabcdefgh",
    "client_secret=GOCSPX-abcdefghijklmnop",
    "app token 1234567890|abcdefghijklmnopqrstuv",
])
def test_redact_masks_secrets(text):
    out = redact(text)
    assert "***" in out
    for secret in ("EAAabcdef", "1//0abc", "ya29.a0", "GOCSPX-", "abcdefghijklmnopqrstuv"):
        assert secret not in out


def test_log_records_are_redacted(caplog):
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("x").info("calling %s", "url?access_token=SECRET123456")
    assert "SECRET123456" not in caplog.text


# ---------- least privilege ----------
def test_google_scope_is_readonly():
    assert SCOPES == ["https://www.googleapis.com/auth/drive.readonly"]



def test_google_not_connected_and_disconnect(tmp_path, monkeypatch):
    mgr = GoogleAuthManager(tmp_path / "missing.json", store=TokenStore(MemoryKeyring()))
    with pytest.raises(NotConnectedError):
        mgr.get_credentials()
    mgr.store.save('{"token": "x"}')
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: None)
    mgr.disconnect()
    assert mgr.store.load() is None


def test_google_invalid_stored_token_forces_reauth(tmp_path):
    mgr = GoogleAuthManager(tmp_path / "c.json", store=TokenStore(MemoryKeyring()))
    mgr.store.save('{"scopes": ["https://www.googleapis.com/auth/drive"], "token": "t"}')
    with pytest.raises(AuthExpiredError):
        mgr.get_credentials()
    assert mgr.store.load() is None



# ---------- source control hygiene ----------
def test_gitignore_covers_secrets():
    gi = (ROOT / ".gitignore").read_text()
    for pat in (".env", "credentials/", "client_secret*.json", "token*.json", "data/"):
        assert pat in gi


def test_no_hardcoded_secrets_in_source():
    pattern = re.compile(r"EAA[A-Za-z0-9]{20,}|ya29\.[A-Za-z0-9_\-]{10,}|GOCSPX-[A-Za-z0-9_\-]{10,}")
    for f in list((ROOT / "src").rglob("*.py")) + [ROOT / "app.py"]:
        assert not pattern.search(f.read_text(encoding="utf-8")), f


def test_no_secret_files_tracked():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    for line in out.splitlines():
        assert not re.search(r"(^|/)(\.env|client_secret.*\.json|token.*\.json)$", line), line
