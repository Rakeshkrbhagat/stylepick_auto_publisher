"""Google Drive integration: folder URL parsing, video listing and secure download.

Uses the official Drive API v3 with the authenticated user's credentials, so
private folders work without public sharing. A Drive sharing URL is only used
to extract the folder ID – it is never treated as a download URL.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from src.drive_errors import (
    AccessDeniedError, AuthExpiredError, DownloadError, DriveError, FolderNotFoundError,
    InvalidFolderUrlError, NetworkError, QuotaError,
)

FOLDER_MIME = "application/vnd.google-apps.folder"
SUPPORTED_MIME_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov"}
SUPPORTED_EXTENSIONS = {".mp4", ".mov"}
LIST_FIELDS = ("nextPageToken, files(id, name, mimeType, size, modifiedTime, "
               "webViewLink, md5Checksum, thumbnailLink)")
CHUNK_SIZE = 8 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


# ---------------------------------------------------------------- data
@dataclass
class DriveVideo:
    id: str
    name: str
    mime_type: str
    size: int | None = None
    modified_time: str = ""
    web_view_link: str = ""
    md5_checksum: str = ""
    thumbnail_link: str = ""

    def to_dict(self) -> dict:
        """Shape consumed by the caption-matching module (``id`` + ``name``)."""
        return asdict(self)


@dataclass
class SkippedFile:
    name: str
    mime_type: str
    reason: str


@dataclass
class FolderListing:
    folder_id: str
    folder_name: str
    videos: list[DriveVideo]
    skipped: list[SkippedFile]


# ---------------------------------------------------------------- URL parsing
def extract_folder_id(url: str) -> str:
    """Extract a folder ID from common Drive folder URL formats (or a bare ID)."""
    url = (url or "").strip()
    if not url:
        raise InvalidFolderUrlError("Google Drive folder URL is required.")
    if _ID_RE.match(url):
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.netloc not in (
        "drive.google.com", "www.drive.google.com"
    ):
        raise InvalidFolderUrlError()
    # /drive/folders/<id>, /drive/u/0/folders/<id>, /embeddedfolderview?id=, /open?id=
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", parsed.path)
    if m:
        return m.group(1)
    qid = parse_qs(parsed.query).get("id", [""])[0]
    if _ID_RE.match(qid):
        return qid
    if "/file/d/" in parsed.path:
        raise InvalidFolderUrlError("This is a link to a single file, not a folder. "
                                    "Paste the URL of the folder containing your videos.")
    raise InvalidFolderUrlError()


def classify_file(f: dict) -> str | None:
    """Return None if the file is a supported video, else a skip reason."""
    mime = f.get("mimeType", "")
    ext = Path(f.get("name", "")).suffix.lower()
    if mime == FOLDER_MIME:
        return "Sub-folder (not scanned)"
    if mime.startswith("application/vnd.google-apps"):
        return "Google Docs/shortcut file"
    if mime in SUPPORTED_MIME_TYPES or (mime.startswith("video/") and ext in SUPPORTED_EXTENSIONS):
        return None
    if mime.startswith("video/"):
        return f"Unsupported video format ({ext or mime}); use MP4 or MOV"
    return "Not a video file"


# ---------------------------------------------------------------- error mapping
def map_http_error(exc: Exception, context: str = "") -> DriveError:
    from googleapiclient.errors import HttpError

    if isinstance(exc, DriveError):
        return exc
    if isinstance(exc, HttpError):
        status = exc.resp.status
        reason = ""
        try:
            reason = exc.error_details[0].get("reason", "") if exc.error_details else ""
        except Exception:
            pass
        if status == 401:
            return AuthExpiredError()
        if status == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded",
                                        "quotaExceeded", "dailyLimitExceeded"):
            return QuotaError()
        if status == 403:
            return AccessDeniedError()
        if status == 404:
            return FolderNotFoundError() if context == "folder" else DownloadError(
                "File not found – it may have been deleted or moved.")
        if status >= 500:
            return NetworkError(f"Google Drive is temporarily unavailable (HTTP {status}). Retry.")
        return DriveError(f"Google Drive error (HTTP {status}): {exc}")
    try:
        from google.auth.exceptions import RefreshError, TransportError
        if isinstance(exc, RefreshError):
            return AuthExpiredError()
        if isinstance(exc, TransportError):
            return NetworkError()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError, OSError)):
        return NetworkError()
    return DriveError(f"Unexpected Google Drive error: {exc}")


def with_retries(fn: Callable, *, attempts: int = 5, base_delay: float = 1.0,
                 context: str = "", sleep: Callable[[float], None] = time.sleep):
    """Exponential backoff with jitter for retryable errors (Google retry guidance)."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            err = map_http_error(exc, context)
            if not err.retryable or attempt == attempts - 1:
                raise err from exc
            sleep(min(base_delay * 2 ** attempt + random.random(), 32))


# ---------------------------------------------------------------- client
class DriveClient:
    def __init__(self, credentials=None, service=None, work_dir: Path | str = "data/downloads",
                 sleep: Callable[[float], None] = time.sleep):
        if service is None:
            from googleapiclient.discovery import build

            service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.service = service
        self.work_dir = Path(work_dir)
        self._sleep = sleep

    # ----- folder & listing -----
    def get_folder(self, folder_id: str) -> dict:
        req = self.service.files().get(
            fileId=folder_id, fields="id, name, mimeType, trashed, capabilities(canListChildren)",
            supportsAllDrives=True,
        )
        meta = with_retries(req.execute, context="folder", sleep=self._sleep)
        if meta.get("trashed"):
            raise FolderNotFoundError("This folder is in the trash. Restore it in Google Drive first.")
        if meta.get("mimeType") != FOLDER_MIME:
            raise InvalidFolderUrlError("The link points to a file, not a folder.")
        if meta.get("capabilities", {}).get("canListChildren") is False:
            raise AccessDeniedError()
        return meta

    def list_videos(self, folder_url: str) -> FolderListing:
        folder_id = extract_folder_id(folder_url)
        folder = self.get_folder(folder_id)
        videos, skipped, token = [], [], None
        while True:
            req = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=LIST_FIELDS, pageSize=100, pageToken=token, orderBy="name",
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            )
            resp = with_retries(req.execute, context="folder", sleep=self._sleep)
            for f in resp.get("files", []):
                reason = classify_file(f)
                if reason:
                    skipped.append(SkippedFile(f.get("name", ""), f.get("mimeType", ""), reason))
                else:
                    videos.append(DriveVideo(
                        id=f["id"], name=f["name"], mime_type=f.get("mimeType", ""),
                        size=int(f["size"]) if f.get("size") else None,
                        modified_time=f.get("modifiedTime", ""),
                        web_view_link=f.get("webViewLink", ""),
                        md5_checksum=f.get("md5Checksum", ""),
                        thumbnail_link=f.get("thumbnailLink", ""),
                    ))
            token = resp.get("nextPageToken")
            if not token:
                break
        return FolderListing(folder_id, folder.get("name", ""), videos, skipped)

    # ----- download -----
    def local_path(self, video: DriveVideo) -> Path:
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", video.name).strip() or video.id
        return self.work_dir / video.id / safe  # original filename preserved

    def get_local_file(self, video: DriveVideo) -> Path | None:
        p = self.local_path(video)
        return p if p.is_file() and verify_file(p, video) else None

    def download(self, video: DriveVideo, progress: Callable[[float], None] | None = None,
                 attempts: int = 4) -> Path:
        """Chunked download to a .part file; verified then atomically renamed."""
        from googleapiclient.http import MediaIoBaseDownload

        existing = self.get_local_file(video)
        if existing:
            return existing
        dest = self.local_path(video)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        last_err: DriveError | None = None
        for attempt in range(attempts):
            try:
                with open(part, "wb") as fh:
                    req = self.service.files().get_media(fileId=video.id, supportsAllDrives=True)
                    dl = MediaIoBaseDownload(fh, req, chunksize=CHUNK_SIZE)
                    done = False
                    while not done:
                        status, done = dl.next_chunk(num_retries=3)
                        if progress and status:
                            progress(status.progress())
                if not verify_file(part, video):
                    raise DownloadError("Downloaded file failed size/checksum verification.")
                os.replace(part, dest)
                if progress:
                    progress(1.0)
                return dest
            except Exception as exc:  # noqa: BLE001
                part.unlink(missing_ok=True)
                last_err = map_http_error(exc, "file")
                if isinstance(last_err, (NetworkError, QuotaError)):
                    last_err = DownloadError(f"Download of '{video.name}' interrupted: {last_err}")
                if not last_err.retryable or attempt == attempts - 1:
                    break
                self._sleep(min(2 ** attempt + random.random(), 32))
        raise last_err or DownloadError()


# ---------------------------------------------------------------- helpers
def verify_file(path: Path, video: DriveVideo) -> bool:
    if not path.is_file():
        return False
    if video.size is not None and path.stat().st_size != video.size:
        return False
    if video.md5_checksum:
        h = hashlib.md5()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest() == video.md5_checksum
    return path.stat().st_size > 0


def cleanup_work_dir(work_dir: Path | str, retention_hours: float, now: float | None = None) -> int:
    """Delete downloaded videos older than the retention period. Returns folders removed."""
    root = Path(work_dir)
    if not root.is_dir():
        return 0
    cutoff = (now or time.time()) - retention_hours * 3600
    removed = 0
    for child in root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
        elif child.is_file() and child.suffix == ".part":
            child.unlink(missing_ok=True)
    return removed


def human_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

