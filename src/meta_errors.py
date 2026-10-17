"""User-facing errors for Meta (Facebook) Graph API calls."""
    return MetaPublishError(f"Facebook error ({code}): {msg}", **kw)
        return MetaUnsupportedContentError(f"Unsupported video: {msg}", **kw)
                                                 1363037, 1363040, 1363041)):
    if code == 6000 or (code == 100 and sub in (1363019, 1363022, 1363023, 1363024, 1363026,
        return e
        e = MetaNetworkError(f"Temporary Facebook error: {msg}", **kw)
    if code in (1, 2) or http_status >= 500 or err.get("is_transient"):
        return MetaPermissionError(f"Facebook permission error: {msg}", **kw)
    if code in _PERMISSION_CODES:
        return MetaRateLimitError(f"Facebook rate limit: {msg}", **kw)
    if code in _RATE_LIMIT_CODES or http_status == 429:
        return MetaTokenExpiredError(f"Facebook token invalid/expired: {msg}", **kw)
    if code in _TOKEN_CODES:
    kw = {"code": code, "subcode": sub, "fbtrace_id": trace}
    trace = err.get("fbtrace_id")
    msg = err.get("error_user_msg") or err.get("message") or "Unknown Facebook error"
    code, sub = err.get("code"), err.get("error_subcode")
def from_graph_error(err: dict, http_status: int = 0) -> MetaError:


_TOKEN_CODES = {102, 190, 463, 467}
_PERMISSION_CODES = {3, 10} | set(range(200, 300))
_RATE_LIMIT_CODES = {4, 17, 32, 341, 368, 613, 80001, 80002, 80005}


    default_message = "Facebook could not publish the video."
class MetaPublishError(MetaError):


    default_message = "This video is not supported for Facebook Reels."
class MetaUnsupportedContentError(MetaError):


    retryable = True
    default_message = "Network error while contacting Facebook. Check your connection and retry."
class MetaNetworkError(MetaError):


    retryable = True
    default_message = "Facebook rate limit reached. Wait a few minutes and retry."
class MetaRateLimitError(MetaError):


                       "pages_show_list, pages_read_engagement and pages_manage_posts.")
    default_message = ("Missing Facebook permission or Page role. Reconnect and grant "
class MetaPermissionError(MetaError):


    default_message = "Facebook access token expired or was revoked. Please reconnect Facebook."
class MetaTokenExpiredError(MetaError):


    default_message = "Facebook login was cancelled. Click 'Connect Facebook' to try again."
class MetaAuthCancelledError(MetaError):


    default_message = "Facebook account not connected. Click 'Connect Facebook' in the sidebar."
class MetaNotConnectedError(MetaError):


                       "(see README → Facebook setup).")
    default_message = ("Facebook app is not configured. Set META_APP_ID and META_APP_SECRET in .env "
class MetaNotConfiguredError(MetaError):


        self.code, self.subcode, self.fbtrace_id = code, subcode, fbtrace_id
        super().__init__(message or self.default_message)
                 subcode: int | None = None, fbtrace_id: str | None = None):
    def __init__(self, message: str | None = None, code: int | None = None,

    retryable = False
    default_message = "Facebook API error."
class MetaError(Exception):


from __future__ import annotations


