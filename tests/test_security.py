"""Credential storage, redaction, least-privilege and re-authorization tests."""
        assert not re.search(r"(^|/)(\.env|client_secret.*\.json|token.*\.json)$", line), line
    for line in out.splitlines():
        pytest.skip("git not available")
    except (OSError, subprocess.SubprocessError):
                             timeout=10).stdout
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
    try:
def test_no_secret_files_tracked():


        assert not pattern.search(f.read_text()), f
    for f in list((ROOT / "src").rglob("*.py")) + [ROOT / "app.py"]:
    pattern = re.compile(r"EAA[A-Za-z0-9]{20,}|ya29\.[A-Za-z0-9_\-]{10,}|GOCSPX-[A-Za-z0-9_\-]{10,}")
def test_no_hardcoded_secrets_in_source():


        assert pat in gi
    for pat in (".env", "credentials/", "client_secret*.json", "token*.json", "data/"):
    gi = (ROOT / ".gitignore").read_text()
def test_gitignore_covers_secrets():
# ---------- source control hygiene ----------


    assert ("DELETE", "me/permissions") in graph.calls
    assert not m.is_connected()
    m.disconnect()
    assert m.is_connected() and m.needs_reauth()
    m.mark_needs_reauth("expired")
    m._save({"user_token": "EAAtoken"})
    m = MetaAuthManager("1", "s", graph, store=MetaTokenStore(MemoryKeyring()))
    graph = FakeGraph()
def test_meta_reauth_flag_and_disconnect():


    assert mgr.store.load() is None
        mgr.get_credentials()
    with pytest.raises(AuthExpiredError):
    mgr.store.save('{"scopes": ["https://www.googleapis.com/auth/drive"], "token": "t"}')
    mgr = GoogleAuthManager(tmp_path / "c.json", store=TokenStore(kr))
    kr = MemoryKeyring()
def test_google_invalid_stored_token_forces_reauth(tmp_path):


    assert mgr.store.load() is None
    mgr.disconnect()
    monkeypatch.setattr(requests, "post", lambda *a, **k: None)
    import requests
    mgr.store.save('{"token": "x"}')
        mgr.get_credentials()
    with pytest.raises(NotConnectedError):
    mgr = GoogleAuthManager(tmp_path / "missing.json", store=TokenStore(kr))
    kr = MemoryKeyring()
def test_google_not_connected_and_disconnect(tmp_path, monkeypatch):
# ---------- disconnect & re-authorization ----------


    assert "TOPSECRET" not in repr(m)
    m = MetaAuthManager("1", "TOPSECRET", FakeGraph(), store=MetaTokenStore(MemoryKeyring()))
def test_meta_repr_hides_secret():


    assert "pages_manage_posts" in fb
    assert not any(s.startswith("instagram") for s in fb)
    fb = scopes_for(["facebook"])
def test_meta_scopes_limited_to_enabled_platforms():


    assert SCOPES == ["https://www.googleapis.com/auth/drive.readonly"]
def test_google_scope_is_readonly():
# ---------- least privilege ----------


    assert "SECRET123456" not in caplog.text
        logging.getLogger("x").info("calling %s", "url?access_token=SECRET123456")
    with caplog.at_level(logging.INFO):
    install_log_redaction()
def test_log_records_are_redacted(caplog):


        assert secret not in out
    for secret in ("EAAabcdef", "1//0abc", "ya29.a0", "GOCSPX-", "abcdefghijklmnopqrstuv"):
    assert "***" in out
    out = redact(text)
def test_redact_masks_secrets(text):
])
    "app token 1234567890|abcdefghijklmnopqrstuv",
    "client_secret=GOCSPX-abcdefghijklmnop",
    "Authorization: Bearer ya29.a0AfH6SMBabcdefgh",
    '{"refresh_token": "1//0abcdefghijklmnopqrstuvwxyz"}',
    "https://graph.facebook.com/me?access_token=EAAabcdefghijklmnopqrstuvwxyz123",
@pytest.mark.parametrize("text", [
# ---------- redaction ----------


    assert s.get() is None
    assert s.load() == {}
    s.set("{not json")
    s = JsonSecureStore("j", backend=kr)
    kr = MemoryKeyring()
def test_corrupt_json_entry_is_removed():


        SecureStore("k", backend=PlaintextKeyring()).set("secret")
    with pytest.raises(CredentialStoreError):
def test_insecure_backend_refused():


    s.delete()  # idempotent
    assert s.get() is None
    s.delete()
    assert s.get() == "value"
    s.set("value")
    s = SecureStore("k", backend=kr)
    kr = MemoryKeyring()
def test_secure_store_roundtrip_and_delete():
# ---------- secure storage ----------


        return {}
        self.calls.append((method, path))
    def request(self, method, path, token=None, **kw):

        self.calls = []
    def __init__(self):

    version = "v23.0"
class FakeGraph:


    pass
class PlaintextKeyring(MemoryKeyring):


        del self.data[(s, k)]
    def delete_password(self, s, k):

        self.data[(s, k)] = v
    def set_password(self, s, k, v):

        return self.data.get((s, k))
    def get_password(self, s, k):

        self.data = {}
    def __init__(self):

    """Stand-in for a secure OS keyring backend."""
class MemoryKeyring:


ROOT = Path(__file__).resolve().parents[1]

from src.secure_store import CredentialStoreError, JsonSecureStore, SecureStore
from src.redaction import install_log_redaction, redact
from src.meta_auth import MetaAuthManager, MetaTokenStore, scopes_for
from src.google_auth import SCOPES, GoogleAuthManager, TokenStore
from src.drive_errors import AuthExpiredError, NotConnectedError

import pytest

from pathlib import Path
import subprocess
import re
import logging

from __future__ import annotations


