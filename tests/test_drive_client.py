"""Google Drive integration tests using a fake Drive service (no network)."""

import hashlib
import json

import pytest

from src.drive_client import (
    DriveClient, DriveVideo, classify_file, cleanup_work_dir, extract_folder_id, verify_file,
    with_retries,
)
from src.drive_errors import (
    AccessDeniedError, AuthExpiredError, FolderNotFoundError, InvalidFolderUrlError,
    NotConnectedError, QuotaError,
)
from src.google_auth import GoogleAuthManager
from src.mock_data import SAMPLE_GEMINI_TEXT
from src.content_parser import parse_gemini_text
from src.video_source import MockDriveSource, sync_records

FID = "1AbCdEfGhIjKlMnOp"


# ---------------- fakes
class _Req:
    def __init__(self, fn):
        self.execute = fn


class FakeFiles:
    def __init__(self, folder, pages, fail_get=None):
        self.folder, self.pages, self.fail_get, self.list_calls = folder, pages, fail_get, 0

    def get(self, fileId, fields, supportsAllDrives):
        def run():
            if self.fail_get:
                raise self.fail_get
            return self.folder
        return _Req(run)

    def list(self, **kw):
        page = self.pages[self.list_calls]
        self.list_calls += 1
        assert f"'{FID}' in parents" in kw["q"] and "trashed = false" in kw["q"]
        return _Req(lambda: page)


class FakeService:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def http_error(status, reason=""):
    from googleapiclient.errors import HttpError
    import httplib2

    body = json.dumps({"error": {"errors": [{"reason": reason}], "message": reason}}).encode()
    return HttpError(httplib2.Response({"status": status}), body)


def _ten_videos_page():
    vids = [{"id": f"v{i:02d}", "name": f"stylepick_{i:02d}.mp4", "mimeType": "video/mp4",
             "size": "1000", "modifiedTime": "2026-10-01T10:00:00Z", "webViewLink": "https://x"}
            for i in range(1, 9)]
    vids += [{"id": "v09", "name": "stylepick_09.MOV", "mimeType": "video/quicktime", "size": "5"}]
    return vids


# ---------------- URL parsing
@pytest.mark.parametrize("url", [
    f"https://drive.google.com/drive/folders/{FID}",
    f"https://drive.google.com/drive/folders/{FID}?usp=sharing",
    f"https://drive.google.com/drive/u/1/folders/{FID}",
    f"https://drive.google.com/open?id={FID}",
    FID,
])
def test_extract_folder_id(url):
    assert extract_folder_id(url) == FID


@pytest.mark.parametrize("url", ["", "https://example.com/folders/x",
                                 f"https://drive.google.com/file/d/{FID}/view", "not a url"])
def test_extract_folder_id_invalid(url):
    with pytest.raises(InvalidFolderUrlError):
        extract_folder_id(url)


def test_classify_file():
    assert classify_file({"name": "a.mp4", "mimeType": "video/mp4"}) is None
    assert classify_file({"name": "a.mov", "mimeType": "video/quicktime"}) is None
    assert classify_file({"name": "a.mkv", "mimeType": "video/x-matroska"})
    assert classify_file({"name": "a.jpg", "mimeType": "image/jpeg"})
    assert classify_file({"name": "f", "mimeType": "application/vnd.google-apps.folder"})


# ---------------- listing
def test_list_videos_paginates_and_filters():
    page1 = {"files": _ten_videos_page()[:5], "nextPageToken": "t2"}
    page2 = {"files": _ten_videos_page()[5:] + [
        {"id": "v10", "name": "stylepick_10.mp4", "mimeType": "video/mp4", "size": "7"},
        {"id": "d1", "name": "notes.pdf", "mimeType": "application/pdf"},
        {"id": "d2", "name": "raw.mkv", "mimeType": "video/x-matroska"},
    ]}
    files = FakeFiles({"id": FID, "name": "Batch", "mimeType": "application/vnd.google-apps.folder"},
                      [page1, page2])
    listing = DriveClient(service=FakeService(files)).list_videos(
        f"https://drive.google.com/drive/folders/{FID}")
    assert files.list_calls == 2
    assert len(listing.videos) == 10
    assert {s.name for s in listing.skipped} == {"notes.pdf", "raw.mkv"}
    v = listing.videos[0]
    assert (v.id, v.name, v.size, v.web_view_link) == ("v01", "stylepick_01.mp4", 1000, "https://x")


@pytest.mark.parametrize("err, exc", [
    (http_error(404, "notFound"), FolderNotFoundError),
    (http_error(403, "insufficientFilePermissions"), AccessDeniedError),
    (http_error(401, "authError"), AuthExpiredError),
])
def test_folder_errors(err, exc):
    files = FakeFiles({}, [], fail_get=err)
    with pytest.raises(exc):
        DriveClient(service=FakeService(files), sleep=lambda s: None).list_videos(FID)


def test_trashed_folder():
    files = FakeFiles({"id": FID, "mimeType": "application/vnd.google-apps.folder",
                       "trashed": True}, [])
    with pytest.raises(FolderNotFoundError):
        DriveClient(service=FakeService(files)).list_videos(FID)


def test_retries_rate_limit_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise http_error(429, "rateLimitExceeded")
        return "ok"

    assert with_retries(fn, sleep=lambda s: None) == "ok" and calls["n"] == 3


def test_retries_exhausted_raises_quota():
    def fn():
        raise http_error(403, "userRateLimitExceeded")

    with pytest.raises(QuotaError):
        with_retries(fn, attempts=2, sleep=lambda s: None)


# ---------------- download verification & cleanup
def test_verify_and_cleanup(tmp_path):
    data = b"video-bytes"
    p = tmp_path / "v1" / "a.mp4"
    p.parent.mkdir()
    p.write_bytes(data)
    good = DriveVideo("v1", "a.mp4", "video/mp4", len(data), md5_checksum=hashlib.md5(data).hexdigest())
    assert verify_file(p, good)
    assert not verify_file(p, DriveVideo("v1", "a.mp4", "video/mp4", 999))
    assert not verify_file(p, DriveVideo("v1", "a.mp4", "video/mp4", len(data), md5_checksum="x"))
    assert cleanup_work_dir(tmp_path, retention_hours=1, now=p.stat().st_mtime + 7200) == 1
    assert not p.exists()


def test_local_path_preserves_filename(tmp_path):
    c = DriveClient(service=object(), work_dir=tmp_path)
    v = DriveVideo("abc", "मेरा वीडियो 01.mp4", "video/mp4")
    assert c.local_path(v) == tmp_path / "abc" / "मेरा वीडियो 01.mp4"


# ---------------- auth
class MemStore:
    def __init__(self, value=None):
        self.value = value

    def load(self):
        return self.value

    def save(self, v):
        self.value = v

    def delete(self):
        self.value = None


def test_not_connected(tmp_path):
    auth = GoogleAuthManager(tmp_path / "missing.json", store=MemStore())
    assert not auth.client_configured()
    with pytest.raises(NotConnectedError):
        auth.get_credentials()
    assert not auth.is_connected()


# ---------------- integration with caption matching
def test_mock_source_feeds_caption_matching():
    listing = MockDriveSource().list_videos(f"https://drive.google.com/drive/folders/{FID}")
    assert len(listing.videos) == 10
    recs = sync_records([], listing.videos, parse_gemini_text(SAMPLE_GEMINI_TEXT))
    assert [r.video_id for r in recs] == [v.id for v in listing.videos]
    assert sum(r.is_valid for r in recs) == 8
    # selection subset preserves existing edits
    recs[0].get_content(recs[0].selected_platforms[0]).caption = "edited ✨"
    again = sync_records(recs, listing.videos[:3], parse_gemini_text(SAMPLE_GEMINI_TEXT))
    assert len(again) == 3 and again[0] is recs[0]

