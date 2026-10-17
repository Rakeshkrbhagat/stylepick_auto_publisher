"""Scheduled-post preparation: proposed date = original date + 1 calendar day, same time.

Safety rules (Story 4):
- Nothing is submitted until the user explicitly confirms a proposal (``approve``).
- No immediate-publish API action is ever used here. Facebook Reels are finished with
  ``video_state=SCHEDULED`` (+ ``scheduled_publish_time``) or ``video_state=DRAFT`` – never
  ``PUBLISHED``.
- If a platform/account/content type cannot be scheduled (e.g. Instagram Reels through the
  Instagram Content Publishing API, or a time outside Facebook's allowed window) the proposal is
  marked ``unsupported`` with a clear message and left unsubmitted for manual scheduling.
- Instagram and Facebook proposals are separate objects with separate status/results.

Verify the current Graph API scheduling rules (allowed window, Reels support for
``SCHEDULED``/``DRAFT``) in Meta's docs before going live; the window is configurable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from src.models import Platform

# ------------------------------------------------------------------ date logic
MODE_SCHEDULE, MODE_DRAFT = "schedule", "draft"

PROPOSED, APPROVED, SUBMITTING, SCHEDULED, DRAFT_SAVED, UNSUPPORTED, FAILED = (
    "proposed", "approved", "submitting", "scheduled", "draft_saved", "unsupported", "failed")
SKIPPED = "skipped"
FINAL = {SCHEDULED, DRAFT_SAVED}

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def propose(original: datetime) -> datetime:
    """Exactly one calendar day later, same wall-clock time (and same tzinfo).

    Uses calendar-date arithmetic, so month-end, year-end and leap days are handled
    (28 Feb 2024 -> 29 Feb 2024, 29 Feb 2024 -> 1 Mar 2024, 31 Dec -> 1 Jan) and the
    time of day is never shifted, even across a DST change."""
    next_day = original.date() + timedelta(days=1)
    return datetime.combine(next_day, original.timetz())


class ScheduleParseError(ValueError):
    pass


def parse_original(date_text: str, time_text: str = "", default_year: int | None = None,
                   tz=None) -> datetime:
    """Parse an original date/time such as ('9 September', '10:30 AM'), ('2026-09-09 10:30')
    or ('September 9, 2026', '22:15'). Missing year -> ``default_year`` (else current year)."""
    text = f"{date_text or ''} {time_text or ''}".strip()
    if not text:
        raise ScheduleParseError("No original date was provided.")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.strip(), flags=re.I).replace(",", " ")
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


# ------------------------------------------------------------------ proposals
@dataclass
class ScheduleProposal:
    video_id: str
    filename: str
    platform: Platform
    caption: str              # final text (caption/description + hashtags)
    original: datetime
    proposed: datetime
    mode: str = MODE_SCHEDULE  # schedule | draft
    status: str = PROPOSED
    message: str = ""
    remote_id: str = ""
    edited: bool = False      # proposed time changed by the user
    confirmed_at: str = ""
    history: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return self.video_id, self.platform.value

    @property
    def submitted(self) -> bool:
        return self.status in FINAL


def build_proposal(video_id: str, filename: str, platform: Platform, caption: str,
                   original: datetime, mode: str = MODE_SCHEDULE) -> ScheduleProposal:
    return ScheduleProposal(video_id, filename, platform, caption, original, propose(original),
                            mode=mode)


def edit_proposal(p: ScheduleProposal, new_proposed: datetime | None = None,
                  mode: str | None = None, caption: str | None = None) -> None:
    """User edit. Any change requires a fresh confirmation."""
    if p.submitted:
        raise ValueError("Already submitted – change it on the platform instead.")
    if new_proposed is not None and new_proposed != p.proposed:
        p.proposed, p.edited = new_proposed, True
    if mode is not None:
        p.mode = mode
    if caption is not None:
        p.caption = caption
    if p.status in (APPROVED, UNSUPPORTED, FAILED):
        p.status, p.message = PROPOSED, ""


def approve(p: ScheduleProposal) -> None:
    """Explicit user confirmation – the only way a proposal becomes submittable."""
    if p.submitted:
        return
    p.status = APPROVED
    p.confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ platform schedulers
@dataclass
class ScheduleResult:
    status: str          # scheduled | draft_saved | unsupported | failed
    message: str
    remote_id: str = ""


class Scheduler(Protocol):
    def submit(self, p: ScheduleProposal, path: Path) -> ScheduleResult: ...


class UnsupportedScheduler:
    """Used when a platform cannot schedule/draft through its API (never publishes)."""

    def __init__(self, reason: str):
        self.reason = reason

    def submit(self, p: ScheduleProposal, path: Path) -> ScheduleResult:
        return ScheduleResult(UNSUPPORTED, self.reason)


INSTAGRAM_UNSUPPORTED = (
    "Instagram: the Instagram Content Publishing API cannot schedule Reels or save them as drafts "
    "(media_publish publishes immediately), so nothing was submitted. Schedule this Reel manually "
    "in Meta Business Suite / Instagram using the proposed date and time.")


def instagram_scheduler() -> UnsupportedScheduler:
    return UnsupportedScheduler(INSTAGRAM_UNSUPPORTED)


class FacebookReelsScheduler:
    """Schedules (``video_state=SCHEDULED``) or drafts (``DRAFT``) a Page Reel. Never PUBLISHED."""

    PLATFORM_KEY = "facebook_schedule"

    def __init__(self, graph, page_id: str, page_token: str, store,
                 min_lead: timedelta = timedelta(minutes=10),
                 max_lead: timedelta = timedelta(days=29),
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.graph, self.page_id, self._token, self.store = graph, page_id, page_token, store
        self.min_lead, self.max_lead, self.now = min_lead, max_lead, now

    def submit(self, p: ScheduleProposal, path: Path) -> ScheduleResult:
        from src.facebook_publisher import FacebookReelsPublisher, content_hash, validate_video
        from src.job_store import FAILED as JOB_FAILED, Job
        from src.meta_errors import MetaError

        state = "SCHEDULED" if p.mode == MODE_SCHEDULE else "DRAFT"
        when = p.proposed if p.proposed.tzinfo else p.proposed.astimezone()  # local -> aware
        if p.mode == MODE_SCHEDULE:
            lead = when - self.now()
            if lead < self.min_lead or lead > self.max_lead:
                return ScheduleResult(UNSUPPORTED, (
                    f"Facebook can only schedule between {_td(self.min_lead)} and "
                    f"{_td(self.max_lead)} ahead; {fmt(p.proposed)} is outside that window. "
                    "Nothing was submitted – edit the date/time or save as draft."))

        job = self.store.get(p.video_id, self.PLATFORM_KEY, self.page_id)
        if job and job.status in (SCHEDULED, DRAFT_SAVED) and job.remote_video_id:
            return ScheduleResult(job.status, "Already submitted earlier – not submitted again.",
                                  job.remote_video_id)
        try:
            validate_video(path)
            job = job or Job(p.video_id, self.PLATFORM_KEY, self.page_id)
            job = self.store.save(job, status=SUBMITTING, content_hash=content_hash(p.caption),
                                  attempts=job.attempts + 1, last_error="")
            start = self.graph.request("POST", f"{self.page_id}/video_reels", self._token,
                                       data={"upload_phase": "start"})
            video_id = start.get("video_id")
            if not video_id:
                raise MetaError("Facebook did not return a video ID for the upload session.")
            self.store.save(job, remote_video_id=video_id)
            uploader = FacebookReelsPublisher(self.graph, self.page_id, self._token, self.store)
            uploader._upload(video_id, Path(path))
            data = {"upload_phase": "finish", "video_id": video_id, "video_state": state,
                    "description": p.caption}
            if state == "SCHEDULED":
                data["scheduled_publish_time"] = str(int(when.timestamp()))
            fin = self.graph.request("POST", f"{self.page_id}/video_reels", self._token,
                                     data=data, retries=0)
            if fin.get("success") is False:
                raise MetaError("Facebook rejected the scheduling request.")
        except MetaError as exc:
            if job:
                self.store.save(job, status=JOB_FAILED, last_error=str(exc))
            return ScheduleResult(FAILED, f"Facebook: {exc} Nothing was published.")
        final = SCHEDULED if state == "SCHEDULED" else DRAFT_SAVED
        self.store.save(job, status=final)
        msg = (f"Facebook Reel scheduled for {fmt(p.proposed)}. Facebook will publish it "
               "automatically at that time unless you change it in Meta Business Suite."
               if final == SCHEDULED else
               "Facebook Reel saved as a draft – it stays unpublished until you publish it.")
        return ScheduleResult(final, msg, video_id)


def _td(d: timedelta) -> str:
    return f"{d.days} days" if d.days else f"{int(d.total_seconds() // 60)} minutes"


def submit_approved(proposals: list[ScheduleProposal], schedulers: dict[Platform, Scheduler],
                    resolve_path: Callable[[ScheduleProposal], Path],
                    audit: AuditLog | None = None) -> list[ScheduleProposal]:
    """Submit ONLY approved proposals; each platform is handled and reported independently.
    Skipped/proposed/failed items are never submitted."""
    done = []
    for p in proposals:
        if p.status != APPROVED:
            continue  # never submit without explicit confirmation
        sched = schedulers.get(p.platform)
        if sched is None:
            p.status, p.message = UNSUPPORTED, f"{p.platform.value}: scheduling is not configured."
            if audit:
                audit.record(p, "submit_result")
            done.append(p)
            continue
        p.status = SUBMITTING
        try:
            res = sched.submit(p, resolve_path(p))
        except Exception as exc:  # isolate platforms from each other
            from src.redaction import redact
            res = ScheduleResult(FAILED, f"{p.platform.value}: {redact(exc)}")
        p.status, p.message, p.remote_id = res.status, res.message, res.remote_id
        if audit:
            audit.record(p, "submit_result")
        else:
            p.history.append(
                f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {res.status}")
        done.append(p)
    return done
