"""Input and record validation (no network calls)."""

from __future__ import annotations

from src.drive_client import extract_folder_id
from src.drive_errors import InvalidFolderUrlError
from src.models import MatchStatus, Platform, VideoRecord


INSTAGRAM_CAPTION_LIMIT = 2200
INSTAGRAM_HASHTAG_LIMIT = 30
FACEBOOK_CAPTION_LIMIT = 63206


def validate_drive_url(url: str) -> str | None:
    try:
        extract_folder_id(url)
    except InvalidFolderUrlError as exc:
        return str(exc)
    return None


def validate_sources(drive_url: str, has_pdf: bool, has_text: bool = False) -> list[str]:
    errors = [e for e in [validate_drive_url(drive_url)] if e]
    if not (has_pdf or has_text):
        errors.append("Upload the Gemini Conversation PDF (or paste the Gemini response text).")
    return errors


def validate_record(rec: VideoRecord) -> list[str]:
    if rec.skipped:
        return []  # explicitly excluded by the user
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
        errors.append("No target platform selected.")
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
    """Refresh validation errors (workflow status is derived from them)."""
    rec.errors = validate_record(rec)
