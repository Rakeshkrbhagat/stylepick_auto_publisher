"""User-facing errors for Meta (Facebook) Graph API calls."""

from __future__ import annotations


class MetaError(Exception):
    default_message = "Facebook API error."
    retryable = False

    def __init__(self, message: str | None = None, code: int | None = None,
                 subcode: int | None = None, fbtrace_id: str | None = None):
        super().__init__(message or self.default_message)
        self.code, self.subcode, self.fbtrace_id = code, subcode, fbtrace_id


class MetaNotConfiguredError(MetaError):
    default_message = ("Facebook app is not configured. Set META_APP_ID and META_APP_SECRET in .env "
                       "(see README → Facebook setup).")


class MetaNotConnectedError(MetaError):
    default_message = "Facebook account not connected. Click 'Connect Facebook' in the sidebar."


class MetaAuthCancelledError(MetaError):
    default_message = "Facebook login was cancelled. Click 'Connect Facebook' to try again."


class MetaTokenExpiredError(MetaError):
    default_message = "Facebook access token expired or was revoked. Please reconnect Facebook."


class MetaPermissionError(MetaError):
    default_message = ("Missing Facebook permission or Page role. Reconnect and grant "
                       "pages_show_list, pages_read_engagement and pages_manage_posts.")


class MetaRateLimitError(MetaError):
    default_message = "Facebook rate limit reached. Wait a few minutes and retry."
    retryable = True


class MetaNetworkError(MetaError):
    default_message = "Network error while contacting Facebook. Check your connection and retry."
    retryable = True


class MetaUnsupportedContentError(MetaError):
    default_message = "This video is not supported for Facebook Reels."


class MetaPublishError(MetaError):
    default_message = "Facebook could not publish the video."


_RATE_LIMIT_CODES = {4, 17, 32, 341, 368, 613, 80001, 80002, 80005}
_PERMISSION_CODES = {3, 10} | set(range(200, 300))
_TOKEN_CODES = {102, 190, 463, 467}


def from_graph_error(err: dict, http_status: int = 0) -> MetaError:
    code, sub = err.get("code"), err.get("error_subcode")
    msg = err.get("error_user_msg") or err.get("message") or "Unknown Facebook error"
    trace = err.get("fbtrace_id")
    kw = {"code": code, "subcode": sub, "fbtrace_id": trace}
    if code in _TOKEN_CODES:
        return MetaTokenExpiredError(f"Facebook token invalid/expired: {msg}", **kw)
    if code in _RATE_LIMIT_CODES or http_status == 429:
        return MetaRateLimitError(f"Facebook rate limit: {msg}", **kw)
    if code in _PERMISSION_CODES:
        return MetaPermissionError(f"Facebook permission error: {msg}", **kw)
    if code in (1, 2) or http_status >= 500 or err.get("is_transient"):
        e = MetaNetworkError(f"Temporary Facebook error: {msg}", **kw)
        return e
    if code == 6000 or (code == 100 and sub in (1363019, 1363022, 1363023, 1363024, 1363026,
                                                 1363037, 1363040, 1363041)):
        return MetaUnsupportedContentError(f"Unsupported video: {msg}", **kw)
    return MetaPublishError(f"Facebook error ({code}): {msg}", **kw)
