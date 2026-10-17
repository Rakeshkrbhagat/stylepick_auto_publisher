"""Redaction of tokens and secrets from log records and user-visible error messages."""

from __future__ import annotations

import logging
import re

MASK = "***"

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # query/form parameters: access_token=..., client_secret=..., code=...
    (re.compile(r"(?i)\b(access_token|refresh_token|id_token|client_secret|fb_exchange_token|"
                r"input_token|app_secret|code|token)=([^&\s\"'<>]+)"), rf"\1={MASK}"),
    # JSON fields: "access_token": "..."
    (re.compile(r"(?i)(\"(?:access_token|refresh_token|id_token|client_secret|token|"
                r"app_secret|user_token)\"\s*:\s*\")[^\"]*"), rf"\1{MASK}"),
    # Authorization headers
    (re.compile(r"(?i)\b(OAuth|Bearer)\s+[A-Za-z0-9._\-|~+/=]{8,}"), rf"\1 {MASK}"),
    # app access token "APPID|SECRET"
    (re.compile(r"\b\d{6,}\|[A-Za-z0-9_\-]{16,}\b"), MASK),
    # well-known token shapes: Meta (EAA…), Google access (ya29.…) and refresh (1//…)
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bya29\.[A-Za-z0-9_\-.]+"), MASK),
    (re.compile(r"\b1//[A-Za-z0-9_\-]{20,}"), MASK),
    # Google OAuth client secrets
    (re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{10,}"), MASK),
]


def redact(text: object) -> str:
    s = str(text)
    for pattern, repl in _PATTERNS:
        s = pattern.sub(repl, s)
    return s


_installed = False


def install_log_redaction() -> None:
    """Redact every log record (all loggers, incl. third-party) before it is emitted."""
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
            if record.exc_info and not record.exc_text:
                record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        except Exception:  # never break logging
            pass
        return record

    logging.setLogRecordFactory(factory)
    _installed = True

