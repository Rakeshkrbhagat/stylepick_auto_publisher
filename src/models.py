"""Domain models shared by the UI and integration layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Platform(str, Enum):
    INSTAGRAM = "Instagram"
    FACEBOOK = "Facebook"
    YOUTUBE = "YouTube"  # reference only, not publishable yet


PUBLISHABLE_PLATFORMS = (Platform.INSTAGRAM, Platform.FACEBOOK)


class Status(str, Enum):
    PENDING = "Pending"
    VALIDATING = "Validating"
    READY = "Ready"
    UPLOADING = "Uploading"
    PROCESSING = "Processing"
    PUBLISHED = "Published"
    FAILED = "Failed"
    RETRY_PENDING = "Retry Pending"
    INVALID = "Invalid"
    SKIPPED = "Skipped"


STATUS_ICONS = {
    Status.PENDING: "⏳",
    Status.VALIDATING: "🔍",
    Status.READY: "✅",
    Status.UPLOADING: "⬆️",
    Status.PROCESSING: "⚙️",
    Status.PUBLISHED: "🎉",
    Status.FAILED: "❌",
    Status.RETRY_PENDING: "🔁",
    Status.INVALID: "⚠️",
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
        """Main body text used when publishing (caption/description + hashtags)."""
        body = self.caption or self.description
        return "\n\n".join(p for p in [body.strip(), self.hashtags.strip()] if p)


@dataclass
class VideoRecord:
    video_id: str
    filename: str
    thumbnail_url: str = ""
    content: dict[Platform, PlatformContent] = field(default_factory=dict)
    selected_platforms: list[Platform] = field(
        default_factory=lambda: list(PUBLISHABLE_PLATFORMS)
    )
    platform_status: dict[Platform, Status] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    simulate_failure: bool = False  # mock-only hook
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
        if self.errors:
            return Status.INVALID
        statuses = [
            self.platform_status.get(p, Status.READY) for p in self.selected_platforms
        ]
        if not statuses:
            return Status.PENDING
        for s in (Status.UPLOADING, Status.PROCESSING, Status.VALIDATING,
                  Status.FAILED, Status.RETRY_PENDING):
            if s in statuses:
                return s
        if all(s == Status.PUBLISHED for s in statuses):
            return Status.PUBLISHED
        if Status.PUBLISHED in statuses:
            return Status.READY
        return statuses[0]


@dataclass
class HistoryEntry:
    timestamp: datetime
    filename: str
    platform: str
    status: str
    message: str = ""

