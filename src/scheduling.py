"""Proposed schedule calculation: original date + 1 calendar day, same wall-clock time.

This module only calculates dates. It never contacts any platform: a *proposed* schedule is
a suggestion for the user, who schedules the post themselves in Instagram / Facebook.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


class ScheduleParseError(ValueError):
    pass


def get_tz(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleParseError(f"Unknown timezone '{name}'.") from exc


def propose(original: datetime) -> datetime:
    """Exactly one calendar day later with the identical wall-clock time and tzinfo.

    Calendar arithmetic on the date only: 28 Feb 2024 -> 29 Feb 2024, 29 Feb -> 1 Mar,
    31 Dec -> 1 Jan; the time is never shifted, even across a DST change."""
    return datetime.combine(original.date() + timedelta(days=1), original.timetz())


def to_local(dt: datetime, tz_name: str) -> datetime:
    """Interpret naive datetimes as wall-clock time in the user's timezone."""
    tz = get_tz(tz_name)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def parse_original(date_text: str, time_text: str = "", default_year: int | None = None,
                   tz=None) -> datetime:
    """Parse ('9 September', '10:30 AM'), ('2026-09-09 10:30'), ('September 9, 2026', '22:15')."""
    text = f"{date_text or ''} {time_text or ''}".strip()
    if not text:
        raise ScheduleParseError("No original date was provided.")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.I).replace(",", " ")
    t, s = _extract_time(s)
    d = _extract_date(s, default_year or datetime.now().year)
    return datetime.combine(d, t, tzinfo=tz)


def _extract_time(s: str) -> tuple[time, str]:
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", s, re.I) or \
        re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?()(?!\d)", s)
    if not m:
        raise ScheduleParseError(f"No time found in '{s.strip()}' (e.g. 10:30 AM).")
    h, mi, sec = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
    ampm = (m.group(4) or "").lower()
    if ampm:
        if not 1 <= h <= 12:
            raise ScheduleParseError(f"Invalid 12-hour time '{m.group(0)}'.")
        h = h % 12 + (12 if ampm == "p" else 0)
    try:
        return time(h, mi, sec), (s[:m.start()] + " " + s[m.end():])
    except ValueError as exc:
        raise ScheduleParseError(f"Invalid time '{m.group(0)}'.") from exc


def _extract_date(s: str, year: int) -> date:
    s = s.strip()
    try:
        if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", s):  # day/month/year
            return date(int(m[3]), int(m[2]), int(m[1]))
        words = s.lower().split()
        month = next((_MONTHS[w] for w in words if w in _MONTHS), None)
        nums = [int(w) for w in words if w.isdigit()]
        if month:
            days = [n for n in nums if 1 <= n <= 31]
            years = [n for n in nums if n >= 1000]
            if days:
                return date(years[0] if years else year, month, days[0])
    except ValueError as exc:
        raise ScheduleParseError(f"Invalid date '{s}'.") from exc
    raise ScheduleParseError(f"Could not read a date from '{s}' (e.g. 9 September).")


def fmt(dt: datetime | None) -> str:
    """'10 September 2026, 10:30 AM'."""
    if not dt:
        return "—"
    return f"{dt.day} {dt:%B %Y}, {dt:%I:%M %p}".replace(", 0", ", ")


def to_iso(dt: datetime | None) -> str:
    """Store wall-clock time without offset (timezone kept per workspace)."""
    return dt.replace(tzinfo=None).isoformat(timespec="minutes") if dt else ""


def from_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None

