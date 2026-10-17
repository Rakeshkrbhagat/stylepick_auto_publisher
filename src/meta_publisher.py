"""Meta publishing package entry point (kept for backwards compatibility)."""

from src.facebook_publisher import FacebookReelsPublisher, PublishResult, validate_video  # noqa: F401
from src.live_publisher import RoutingPublisher  # noqa: F401


class MetaPublisher:
    """Thin facade. Instagram publishing is not implemented yet."""

    def __init__(self, facebook: FacebookReelsPublisher | None = None):
        self.facebook = facebook

    def publish_facebook(self, video_key: str, video_path: str, caption: str) -> PublishResult:
        if not self.facebook:
            raise RuntimeError("Facebook publisher is not configured.")
        return self.facebook.publish(video_key, video_path, caption)

    def publish_instagram(self, video_path: str, caption: str) -> dict:
        raise NotImplementedError("Instagram publishing is not implemented yet.")

