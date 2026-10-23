"""Domain models shared by the UI and integration layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Platform(str, Enum):
    INSTAGRAM = "Instagram"
    FACEBOOK = "Facebook"
    YOUTUBE = "YouTube"  # parsed but ignored (never shown, never used)


# Platforms the user prepares content for. Nothing is ever published by this application.
TARGET_PLATFORMS = (Platform.INSTAGRAM, Platform.FACEBOOK)


class Status(str, Enum):
    """Workflow statuses. None of them means 'published' – the app never publishes."""

    READY = "Ready"
    NEEDS_REVIEW = "Needs Review"
    READY_TO_SCHEDULE = "Ready to Schedule"
    SCHEDULED_BY_USER = "Scheduled by User"
    NEEDS_ATTENTION = "Needs Attention"
    SKIPPED = "Skipped"


STATUS_ICONS = {
    Status.READY: "✅",
    Status.NEEDS_REVIEW: "📝",
    Status.READY_TO_SCHEDULE: "🗓️",
    Status.SCHEDULED_BY_USER: "☑️",
    Status.NEEDS_ATTENTION: "⚠️",
    Status.SKIPPED: "⏭️",
}


class MatchStatus(str, Enum):
    MATCHED = "Matched"
    NEEDS_CONFIRMATION = "Needs confirmation"
    UNMATCHED = "Unmatched"
    SKIPPED = "Skipped"


@dataclass
class PlatformContent:
    """Text fields for a single platform. Unused fields stay empty."""

    title: str = ""
    seo_title: str = ""
    hook: str = ""
    caption: str = ""
    description: str = ""
    hashtags: str = ""
    youtube_tags: str = ""
    cover_title: str = ""
    on_screen_text: str = ""
    transcript: str = ""

    def is_empty(self) -> bool:
        return not any(getattr(self, f) for f in self.__dataclass_fields__)

    def publish_text(self) -> str:
        """Body text the user copies (caption/description + hashtags)."""
        body = self.caption or self.description
        return "\n\n".join(p for p in [body.strip(), self.hashtags.strip()] if p)

    copy_text = publish_text


@dataclass
class VideoRecord:
    video_id: str
    filename: str
    thumbnail_url: str = ""
    content: dict[Platform, PlatformContent] = field(default_factory=dict)
    selected_platforms: list[Platform] = field(default_factory=lambda: list(TARGET_PLATFORMS))
    errors: list[str] = field(default_factory=list)
    # ---- matching ----
    content_key: str | None = None          # key in parsed Gemini content
    match_status: MatchStatus = MatchStatus.UNMATCHED
    match_method: str = ""
    match_notes: list[str] = field(default_factory=list)
    duplicate_with: list[str] = field(default_factory=list)  # other videos sharing content
    skipped: bool = False

    def get_content(self, platform: Platform) -> PlatformContent:
        return self.content.setdefault(platform, PlatformContent())

    def has_caption(self, platform: Platform) -> bool:
        c = self.content.get(platform)
        return bool(c and (c.caption or c.description).strip())

    @property
    def is_valid(self) -> bool:
        return not self.errors and not self.skipped

    @property
    def overall_status(self) -> Status:
        if self.skipped:
            return Status.SKIPPED
        return Status.NEEDS_REVIEW if self.errors else Status.READY


@dataclass
class HistoryEntry:
    timestamp: datetime
    filename: str
    platform: str
    status: str
    message: str = ""

