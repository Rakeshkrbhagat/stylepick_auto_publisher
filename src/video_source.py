"""Video source layer: real Google Drive or mock data, behind one interface.

The dashboard only talks to ``VideoSource``; caption matching consumes
``DriveVideo.to_dict()`` (stable ``id`` + original ``name``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from src.drive_client import DriveClient, DriveVideo, FolderListing, SkippedFile, extract_folder_id
from src.google_auth import GoogleAuthManager
from src.matching import build_records
from src.mock_data import mock_list_videos
from src.models import Platform, PlatformContent, VideoRecord


class VideoSource(Protocol):
    def list_videos(self, folder_url: str) -> FolderListing: ...
    def download(self, video: DriveVideo, progress: Callable[[float], None] | None = None) -> Path: ...


class GoogleDriveSource:
    def __init__(self, auth: GoogleAuthManager, work_dir: Path):
        self.auth, self.work_dir = auth, work_dir

    def _client(self) -> DriveClient:
        return DriveClient(credentials=self.auth.get_credentials(), work_dir=self.work_dir)

    def list_videos(self, folder_url: str) -> FolderListing:
        extract_folder_id(folder_url)  # validate before requiring auth
        return self._client().list_videos(folder_url)

    def download(self, video, progress=None) -> Path:
        return self._client().download(video, progress)


class MockDriveSource:
    """Offline sample data – no network calls."""

    def list_videos(self, folder_url: str) -> FolderListing:
        folder_id = extract_folder_id(folder_url)
        videos = [DriveVideo(id=f["id"], name=f["name"], mime_type="video/mp4",
                             size=12_000_000 + i * 750_000, modified_time="2026-10-01T10:00:00Z")
                  for i, f in enumerate(mock_list_videos(folder_url))]
        skipped = [SkippedFile("notes.docx", "application/vnd.openxmlformats", "Not a video file")]
        return FolderListing(folder_id, "Sample folder (mock)", videos, skipped)

    def download(self, video, progress=None) -> Path:
        if progress:
            progress(1.0)
        return Path(f"(mock)/{video.name}")


def sync_records(
    existing: list[VideoRecord], videos: list[DriveVideo],
    content: dict[str, dict[Platform, PlatformContent]],
    overrides: dict[str, str] | None = None,
    confirmed: set[str] | None = None,
    skipped: set[str] | None = None,
) -> list[VideoRecord]:
    """Build records for the selected videos, keeping edits/status of known file IDs."""
    return build_records([v.to_dict() for v in videos], content, overrides, confirmed, skipped,
                         existing=existing)

