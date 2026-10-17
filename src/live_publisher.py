"""Routes publishing per platform: live Facebook / Instagram publishers when enabled, mock otherwise.

``publish`` returns ``True`` only when the platform confirmed publication,
``None`` while still processing/uncertain and ``False`` on failure. After each call:
- ``last_retryable``  – whether a failure may be retried
- ``last_result``     – the platform result (IDs / permalink) for live calls
- ``last_auth_error`` – True when the failure was an expired/revoked token
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.facebook_publisher import VideoMeta
from src.meta_errors import MetaError, MetaTokenExpiredError
from src.models import Platform, VideoRecord
from src.publishing_service import MockPublisher
from src.redaction import redact

PathResolver = Callable[[VideoRecord], tuple[Path, VideoMeta | None]]


class RoutingPublisher:
    def __init__(self, facebook=None, resolve_path: PathResolver | None = None,
                 fallback: MockPublisher | None = None, instagram=None,
                 on_auth_error: Callable[[Platform, str], None] | None = None):
        self.live = {Platform.FACEBOOK: facebook, Platform.INSTAGRAM: instagram}
        self.resolve_path = resolve_path
        self.fallback = fallback or MockPublisher()
        self.on_auth_error = on_auth_error
        self.last_retryable, self.last_result, self.last_auth_error = True, None, False

    @property
    def facebook(self):
        return self.live[Platform.FACEBOOK]

    def publish(self, record: VideoRecord, platform: Platform) -> tuple[bool | None, str]:
        self.last_retryable, self.last_result, self.last_auth_error = True, None, False
        target = self.live.get(platform)
        if target is None:
            return self.fallback.publish(record, platform)
        content = record.content.get(platform)
        text = content.publish_text() if content else ""  # caption/description + hashtags
        if not text:
            self.last_retryable = False
            return False, f"Missing {platform.value} caption."
        try:
            path, meta = self.resolve_path(record)
            res = target.publish(record.video_id, path, text, meta)
        except MetaTokenExpiredError as exc:
            # retry is fine after reconnecting; nothing was confirmed
            self.last_auth_error = True
            msg = f"Authorization expired – reconnect Facebook, then Retry. ({redact(exc)})"
            if self.on_auth_error:
                self.on_auth_error(platform, msg)
            return False, msg
        except MetaError as exc:
            self.last_retryable = exc.retryable
            return False, redact(exc)
        self.last_result = res
        detail = redact(" ".join(x for x in [
            res.message, f"video_id={res.video_id}" if res.video_id else "",
            f"post_id={res.post_id}" if res.post_id else "", res.permalink] if x))
        if res.ok:  # platform-confirmed
            return True, detail
        if res.status in ("processing", "unknown"):
            return None, detail
        return False, detail  # remote processing error: a fresh upload is allowed on retry

