"""Results for the simple results screen. Pure logic – no network, no storage, no posting.

The app never publishes or schedules anything; the proposed date is only a suggestion the
user applies manually in the native Instagram / Facebook apps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src import scheduling
from src.matching import response_number, trailing_number
from src.models import MatchStatus, Platform, VideoRecord


def drive_view_link(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def drive_preview_link(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/preview"


@dataclass
class VideoResult:
    video_id: str
    filename: str
    file_number: int | None
    response_number: int | None
    instagram_caption: str
    instagram_hashtags: str
    facebook_description: str
    facebook_hashtags: str
    original: datetime | None
    proposed: datetime | None
    link: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def instagram_text(self) -> str:
        return join(self.instagram_caption, self.instagram_hashtags)

    @property
    def facebook_text(self) -> str:
        return join(self.facebook_description, self.facebook_hashtags)


def join(body: str, tags: str) -> str:
    return "\n\n".join(p for p in ((body or "").strip(), (tags or "").strip()) if p)


def build_result(rec: VideoRecord, original: datetime | None, sample: bool = False) -> VideoResult:
    """Only Instagram (caption, hashtags) and Facebook (description, hashtags) are used –
    YouTube content is never included."""
    ig = rec.content.get(Platform.INSTAGRAM)
    fb = rec.content.get(Platform.FACEBOOK)
    warnings = list(rec.errors)
    if rec.match_status == MatchStatus.NEEDS_CONFIRMATION:
        warnings += [n for n in rec.match_notes if n not in warnings]
    if rec.match_status == MatchStatus.UNMATCHED:
        warnings += [n for n in rec.match_notes if n not in warnings]
    if original is None:
        warnings.append("No original date/time – enter one to calculate the proposed schedule.")
    return VideoResult(
        video_id=rec.video_id,
        filename=rec.filename,
        file_number=trailing_number(rec.filename),
        response_number=response_number(rec.content_key),
        instagram_caption=ig.caption if ig else "",
        instagram_hashtags=ig.hashtags if ig else "",
        facebook_description=((fb.description or fb.caption) if fb else ""),
        facebook_hashtags=fb.hashtags if fb else "",
        original=original,
        proposed=scheduling.propose(original) if original else None,
        link="" if sample else drive_view_link(rec.video_id),
        warnings=warnings,
    )


def summary_row(r: VideoResult) -> dict:
    return {
        "Video": r.filename,
        "File #": r.file_number if r.file_number is not None else "—",
        "Gemini #": r.response_number if r.response_number is not None else "—",
        "Original": scheduling.fmt(r.original),
        "Proposed Schedule Date/Time": scheduling.fmt(r.proposed),
        "Warnings": " ".join(r.warnings) or "—",
    }

