"""Input and record validation (no network calls)."""

from __future__ import annotations

import re

from src.drive_client import extract_folder_id
from src.drive_errors import InvalidFolderUrlError
from src.models import PUBLISHABLE_PLATFORMS, MatchStatus, Platform, Status, VideoRecord

_GEMINI_RE = re.compile(r"^https://(gemini\.google\.com/(share|app)/|g\.co/gemini/share/)[\w-]+")

INSTAGRAM_CAPTION_LIMIT = 2200
INSTAGRAM_HASHTAG_LIMIT = 30
FACEBOOK_CAPTION_LIMIT = 63206


def validate_drive_url(url: str) -> str | None:
    try:
        extract_folder_id(url)
    except InvalidFolderUrlError as exc:
        return str(exc)
    return None


def validate_gemini_url(url: str) -> str | None:
    """Gemini URL is optional when response text or a file is provided."""
    if url.strip() and not _GEMINI_RE.match(url.strip()):
        return "Invalid Gemini conversation URL. Expected https://gemini.google.com/share/<id>"
    return None


def validate_sources(drive_url: str, gemini_url: str, has_text: bool, has_file: bool) -> list[str]:
    errors = [e for e in [validate_drive_url(drive_url)] if e]
    if has_text or has_file:
        return errors  # pasted text / file takes priority; URL is not used
    if not gemini_url.strip():
        errors.append(
            "Provide a Gemini conversation URL, paste the Gemini response, or upload a caption file."
        )
    elif err := validate_gemini_url(gemini_url):
        errors.append(err)
    return errors


def validate_record(rec: VideoRecord) -> list[str]:
    if rec.skipped:
        return []  # explicitly excluded from publishing
    errors: list[str] = []
    if rec.match_status == MatchStatus.NEEDS_CONFIRMATION:
        errors.append("Content mapping needs confirmation.")
    if rec.duplicate_with:
        errors.append("Same Gemini content is assigned to multiple videos: "
                      + ", ".join(rec.duplicate_with) + ".")
    if not rec.content or rec.match_status == MatchStatus.UNMATCHED:
        errors.append("No matching Gemini content found for this video filename.")
        return errors
    if not rec.selected_platforms:
        errors.append("No publishing platform selected.")
    for p in rec.selected_platforms:
        c = rec.content.get(p)
        if not rec.has_caption(p):
            errors.append(f"Missing {p.value} caption.")
            continue
        text = c.publish_text()
        if p == Platform.INSTAGRAM:
            if len(text) > INSTAGRAM_CAPTION_LIMIT:
                errors.append(f"Instagram caption exceeds {INSTAGRAM_CAPTION_LIMIT} characters.")
            if text.count("#") > INSTAGRAM_HASHTAG_LIMIT:
                errors.append(f"Instagram allows max {INSTAGRAM_HASHTAG_LIMIT} hashtags.")
        if p == Platform.FACEBOOK and len(text) > FACEBOOK_CAPTION_LIMIT:
            errors.append("Facebook caption too long.")
    return errors


def revalidate(rec: VideoRecord) -> None:
    """Refresh errors and reset non-final statuses to Ready/Pending."""
    rec.errors = validate_record(rec)
    for p in PUBLISHABLE_PLATFORMS:
        current = rec.platform_status.get(p)
        if current == Status.PUBLISHED:
            continue
        if rec.errors or rec.skipped:
            rec.platform_status[p] = Status.PENDING
        elif current not in (Status.FAILED, Status.RETRY_PENDING):
            rec.platform_status[p] = Status.READY

