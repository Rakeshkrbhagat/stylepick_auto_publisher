"""Results, OAuth state, and the 'no publishing / no storage' guarantees."""


from __future__ import annotations

import ast
import re
from datetime import datetime
from pathlib import Path

from src import oauth_state
from src.content_parser import parse_gemini_text
from src.matching import build_records
from src.mock_data import SAMPLE_GEMINI_TEXT, mock_list_videos
from src.results import build_result

ROOT = Path(__file__).resolve().parents[1]


def _records():
    return {r.filename: r for r in
            build_records(mock_list_videos(""), parse_gemini_text(SAMPLE_GEMINI_TEXT))}


def test_result_has_only_instagram_and_facebook_text():
    r = build_result(_records()["reel_02.mp4"], datetime(2026, 9, 9, 18, 30), sample=True)
    assert "सिल्क साड़ी" in r.instagram_caption and "\n" in r.instagram_caption
    assert r.instagram_hashtags.startswith("#") and r.facebook_hashtags.startswith("#")
    for text in (r.instagram_text, r.facebook_text):
        assert "#Shorts" not in text and "YouTube" not in text and "subscribe" not in text
    assert r.proposed == datetime(2026, 9, 10, 18, 30)
    assert r.file_number == 2 and not r.warnings and r.link == ""


def test_result_warnings_for_missing_content_and_date():
    recs = _records()
    r = build_result(recs["reel_extra_clip.mp4"], None)
    assert r.proposed is None and any("original date" in w for w in r.warnings)
    assert any("No matching" in w for w in r.warnings)
    assert build_result(recs["reel_01.mp4"], None).link.startswith("https://drive.google.com/")


def test_oauth_state_signed_and_expiring():
    key = b"k" * 32
    s = oauth_state.make_state(key, now=1000)
    assert oauth_state.verify_state(key, s, now=1100)
    assert not oauth_state.verify_state(key, s, now=1000 + 16 * 60)   # expired
    assert not oauth_state.verify_state(b"x" * 32, s, now=1100)       # wrong key
    assert not oauth_state.verify_state(key, s[:-2] + "AA", now=1100)  # tampered
    assert not oauth_state.verify_state(key, "garbage")


def test_signing_key_from_client_file(tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"web": {"client_secret": "abc"}}')
    assert oauth_state.signing_key(f) != oauth_state.signing_key(f, override="other")
    try:
        oauth_state.signing_key(tmp_path / "missing.json")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_session_token_store_is_memory_only():
    holder = {}
    s = oauth_state.SessionTokenStore(holder)
    s.save("tok")
    assert s.load() == "tok"
    s.delete()
    assert s.load() is None


# ---------------------------------------------------------------- guarantees
FORBIDDEN = re.compile(
    r"media_publish|video_reels|upload_phase|graph\.facebook\.com|rupload|"
    r"scheduled_publish_time|video_state|youtube/v3|googleapis\.com/upload", re.I)
REMOVED = ["facebook_publisher", "instagram_publisher", "live_publisher", "meta_publisher",
           "publishing_coordinator", "publishing_service", "job_store", "history_store",
           "meta_graph", "meta_auth", "workspace_store", "user_credentials"]


def _sources():
    return list((ROOT / "src").rglob("*.py")) + [ROOT / "app.py"]


def test_no_publishing_endpoints():
    for f in _sources():
        assert not FORBIDDEN.search(f.read_text(encoding="utf-8")), f


def test_removed_modules_absent_and_not_imported():
    for name in REMOVED:
        assert not (ROOT / "src" / f"{name}.py").exists(), name
    for f in _sources():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[-1] not in REMOVED, (f, node.module)


def test_no_database_and_readonly_scope():
    for f in _sources():
        text = f.read_text(encoding="utf-8")
        assert "sqlite3" not in text, f
        for m in re.finditer(r"requests\.(post|put|patch|delete)\(\s*\"([^\"]+)", text):
            assert m.group(2) == "https://oauth2.googleapis.com/revoke", (f, m.group(0))
    from src.google_auth import SCOPES
    assert SCOPES == ["https://www.googleapis.com/auth/drive.readonly"]


def test_ui_never_claims_published():
    for f in _sources():
        text = f.read_text(encoding="utf-8").lower()
        for phrase in ("published successfully", "posted successfully", "scheduled successfully",
                       "publish now"):
            assert phrase not in text, (f, phrase)
