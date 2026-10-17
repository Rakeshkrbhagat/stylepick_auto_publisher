"""Bulk publishing coordinator.

Turns validated records into independent (video, platform) jobs, dispatches them
to the platform publishers and tracks each job separately.

Guarantees
- A job is only ``published`` when the publisher returns ``True`` – live publishers do
  that only after platform confirmation (Facebook status / Instagram media_publish).
- Failures are isolated per job; a failure on one platform never touches another.
- Queued jobs can be cancelled until they start (``cancel`` or ``cancel_event``).
- Rate limits: jobs run one at a time (no concurrent uploads) with a minimum interval
  between live calls on the same platform; when a platform reports a rate limit or an
  expired authorization, its remaining jobs are deferred while the other platform continues.
- Retry targets only failed jobs marked retryable, or jobs still processing (re-check).
- With a ``HistoryStore`` every attempt is persisted (caption, outcome, IDs, errors)
  *before* the publisher is called; if the history can't be written, nothing is published.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from src.history_store import (
    FAILED as H_FAILED, PROCESSING as H_PROCESSING, PUBLISHED as H_PUBLISHED, SIMULATED,
    UNCERTAIN as H_UNCERTAIN, HistoryStore, HistoryStoreError,
)
from src.models import PUBLISHABLE_PLATFORMS, HistoryEntry, Platform, Status, VideoRecord
from src.redaction import redact

QUEUED, UPLOADING, PROCESSING, PUBLISHED, FAILED, CANCELLED, DEFERRED = (
    "queued", "uploading", "processing", "published", "failed", "cancelled", "deferred")
ACTIVE = {QUEUED, DEFERRED}
_RATE_LIMIT_HINTS = ("rate limit", "publishing limit", "request limit")
DEFAULT_MIN_INTERVAL_S = {Platform.FACEBOOK: 2.0, Platform.INSTAGRAM: 2.0}

AccountResolver = Callable[[Platform], tuple[str, str]]  # -> (account_id, mode)


def simulated_accounts(_: Platform) -> tuple[str, str]:
    return SIMULATED, SIMULATED


@dataclass
class BatchJob:
    video_id: str
    filename: str
    platform: Platform
    status: str = QUEUED
    message: str = ""
    retryable: bool = True
    is_retry: bool = False
    duplicate: bool = False
    allow_duplicate: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class BatchSummary:
    total: int = 0
    queued: int = 0
    uploading: int = 0
    processing: int = 0
    published: int = 0
    failed: int = 0
    cancelled: int = 0
    deferred: int = 0

    @property
    def done(self) -> int:
        return self.published + self.failed + self.cancelled + self.processing

    @property
    def partial(self) -> bool:
        return self.published > 0 and (self.failed + self.processing + self.deferred) > 0


def summarize(jobs: list[BatchJob]) -> BatchSummary:
    s = BatchSummary(total=len(jobs))
    for j in jobs:
        setattr(s, j.status, getattr(s, j.status) + 1)
    return s


_RECORD_STATUS = {
    QUEUED: Status.PENDING, DEFERRED: Status.PENDING, UPLOADING: Status.UPLOADING,
    PROCESSING: Status.PROCESSING, PUBLISHED: Status.PUBLISHED, FAILED: Status.FAILED,
}
_HISTORY_STATUS = {PUBLISHED: H_PUBLISHED, PROCESSING: H_PROCESSING, FAILED: H_FAILED}


def restore_status(records: list[VideoRecord], store: HistoryStore,
                   account_for: AccountResolver = simulated_accounts
                   ) -> dict[tuple[str, str], bool]:
    """Apply persisted platform states to records (after restart). Returns retryable map."""
    accounts = {p.value: account_for(p)[0] for p in PUBLISHABLE_PLATFORMS}
    by_key = {(s.video_key, s.platform): s for s in store.states(accounts)}
    retryable = {}
    for rec in records:
        for p in PUBLISHABLE_PLATFORMS:
            s = by_key.get((rec.video_id, p.value))
            if not s:
                continue
            if s.status == H_PUBLISHED:
                rec.platform_status[p] = Status.PUBLISHED
            elif s.status in (H_PROCESSING, H_UNCERTAIN):
                rec.platform_status[p] = Status.PROCESSING  # verify before any retry
            elif s.status == H_FAILED:
                rec.platform_status[p] = Status.FAILED
            retryable[(rec.video_id, p.value)] = s.retryable
    return retryable


class PublishingCoordinator:
    def __init__(self, publisher, history: list[HistoryEntry] | None = None,
                 min_interval_s: dict[Platform, float] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 store: HistoryStore | None = None,
                 account_for: AccountResolver = simulated_accounts):
        self.publisher = publisher
        self.history = history if history is not None else []
        self.min_interval_s = DEFAULT_MIN_INTERVAL_S if min_interval_s is None else min_interval_s
        self.sleep, self.clock = sleep, clock
        self.store, self.account_for = store, account_for
        self.cancel_event = threading.Event()
        self.auth_blocked: set[Platform] = set()
        self._last_start: dict[Platform, float] = {}

    # ------------------------------------------------------------ planning
    @staticmethod
    def plan(records: list[VideoRecord], platforms: list[Platform],
             video_ids: list[str] | None = None) -> list[BatchJob]:
        """One job per valid (video, platform); already published/in-flight pairs are skipped."""
        jobs = []
        for rec in records:
            if not rec.is_valid or (video_ids is not None and rec.video_id not in video_ids):
                continue
            for p in platforms:
                if p not in rec.selected_platforms:
                    continue
                if rec.platform_status.get(p, Status.READY) in (Status.READY, Status.PENDING):
                    jobs.append(BatchJob(rec.video_id, rec.filename, p))
        return jobs

    @staticmethod
    def plan_retry(records: list[VideoRecord],
                   retryable: dict[tuple[str, str], bool]) -> list[BatchJob]:
        """Only failed jobs verified as retryable, plus processing/uncertain jobs (re-check).
        Platforms that already succeeded for a video are never included."""
        jobs = []
        for rec in records:
            if not rec.is_valid:
                continue
            for p in rec.selected_platforms:
                st = rec.platform_status.get(p)
                if st == Status.PROCESSING or (
                        st in (Status.FAILED, Status.RETRY_PENDING)
                        and retryable.get((rec.video_id, p.value), True)):
                    jobs.append(BatchJob(rec.video_id, rec.filename, p, is_retry=True))
        return jobs

    @staticmethod
    def cancel(jobs: list[BatchJob], job_ids: set[str]) -> int:
        n = 0
        for j in jobs:
            if j.id in job_ids and j.status in ACTIVE:
                j.status, j.message, n = CANCELLED, "Cancelled before start.", n + 1
        return n

    @staticmethod
    def mark_duplicates(jobs: list[BatchJob], store: HistoryStore,
                        account_for: AccountResolver = simulated_accounts) -> int:
        """Hold back jobs whose video (same Drive ID or filename) was already published to the
        same account. They are cancelled with a warning until the user allows them."""
        n = 0
        for j in jobs:
            if j.status not in ACTIVE or j.allow_duplicate or j.is_retry:
                continue
            dup = store.find_duplicate(j.video_id, j.filename, j.platform.value,
                                       account_for(j.platform)[0])
            if dup:
                ref = dup.post_id or dup.permalink or "no post ID"
                j.status, j.duplicate, n = CANCELLED, True, n + 1
                j.message = (f"Duplicate warning: '{dup.filename}' was already published to "
                             f"{j.platform.value} on {dup.updated_at[:16].replace('T', ' ')} "
                             f"({ref}).")
        return n

    @staticmethod
    def allow_duplicates(jobs: list[BatchJob]) -> int:
        n = 0
        for j in jobs:
            if j.duplicate and j.status == CANCELLED:
                j.status, j.allow_duplicate, j.message, n = QUEUED, True, "Duplicate allowed by user.", n + 1
        return n

    # ------------------------------------------------------------ execution
    def run(self, jobs: list[BatchJob], records: list[VideoRecord],
            on_update: Callable[[BatchJob, BatchSummary], None] | None = None) -> BatchSummary:
        by_id = {r.video_id: r for r in records}
        blocked: dict[Platform, str] = {}
        for job in self._interleave(jobs):
            if self.cancel_event.is_set():
                break
            if job.status not in ACTIVE:
                continue
            rec = by_id.get(job.video_id)
            if rec is None or not rec.is_valid:
                self._finish(job, rec, FAILED, "Record no longer valid – fix it and retry.", False)
                self._notify(on_update, job, jobs)
                continue
            if job.platform in blocked:
                job.status, job.message = DEFERRED, f"Deferred: {blocked[job.platform]}"
                self._notify(on_update, job, jobs)
                continue

            attempt_id = None
            if self.store is not None:
                account, mode = self.account_for(job.platform)
                content = rec.content.get(job.platform)
                caption = content.publish_text() if content else ""
                try:
                    attempt_id = self.store.start_attempt(job.video_id, job.filename,
                                                          job.platform.value, account, mode, caption)
                except HistoryStoreError as exc:
                    self._finish(job, rec, FAILED, f"Not published – history could not be saved, "
                                                   f"so the post would be untracked: {exc}", True)
                    self._notify(on_update, job, jobs)
                    continue

            self._respect_interval(job.platform)
            job.status, job.started_at = UPLOADING, datetime.now()
            rec.platform_status[job.platform] = Status.UPLOADING
            self._notify(on_update, job, jobs)

            try:
                ok, msg = self.publisher.publish(rec, job.platform)
            except Exception as exc:  # publishers should not raise, but never crash the batch
                ok, msg = False, str(exc)
            msg = redact(msg)
            retryable = getattr(self.publisher, "last_retryable", True) if ok is False else True
            status = {True: PUBLISHED, None: PROCESSING, False: FAILED}[ok]
            if ok is None:
                msg = msg or "Awaiting platform confirmation."
            self._finish(job, rec, status, msg, retryable)
            if attempt_id is not None:
                self._record(attempt_id, job, ok, msg, retryable)
            if ok is False and getattr(self.publisher, "last_auth_error", False):
                blocked[job.platform] = "authorization expired – reconnect, then Retry."
                self.auth_blocked.add(job.platform)
            elif ok is False and self._is_rate_limit(msg):
                blocked[job.platform] = "platform rate limit reached; retry later."
            self._notify(on_update, job, jobs)
        return summarize(jobs)

    # ------------------------------------------------------------ helpers
    def _record(self, attempt_id, job, ok, msg, retryable) -> None:
        res = getattr(self.publisher, "last_result", None)
        live = res is not None
        post_id = (res.post_id if live else (msg if ok is True else "")) or ""
        try:
            self.store.finish_attempt(
                attempt_id, _HISTORY_STATUS[job.status],
                remote_id=(res.video_id if live else "") or "", post_id=post_id,
                permalink=(res.permalink if live else "") or "",
                error=msg if ok is not True else "", retryable=retryable)
        except HistoryStoreError as exc:
            job.message += f" (warning: history not updated: {exc})"

    @staticmethod
    def _interleave(jobs: list[BatchJob]) -> list[BatchJob]:
        """Alternate platforms so one slow/limited platform doesn't starve the other."""
        queues: dict[Platform, list[BatchJob]] = {}
        for j in jobs:
            queues.setdefault(j.platform, []).append(j)
        out = []
        while any(queues.values()):
            for q in queues.values():
                if q:
                    out.append(q.pop(0))
        return out

    def _is_live(self, platform: Platform) -> bool:
        live = getattr(self.publisher, "live", None)
        return bool(live and live.get(platform))

    def _respect_interval(self, platform: Platform) -> None:
        if not self._is_live(platform):
            return  # simulated publishing needs no throttling
        last = self._last_start.get(platform)
        gap = self.min_interval_s.get(platform, 0)
        if last is not None and gap:
            wait = last + gap - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last_start[platform] = self.clock()

    @staticmethod
    def _is_rate_limit(msg: str) -> bool:
        m = (msg or "").lower()
        return any(h in m for h in _RATE_LIMIT_HINTS)

    def _finish(self, job, rec, status, msg, retryable) -> None:
        job.status, job.message, job.retryable = status, msg, retryable
        job.finished_at = datetime.now()
        if rec is not None:
            rec.platform_status[job.platform] = _RECORD_STATUS[status]
        label = {PUBLISHED: Status.PUBLISHED, PROCESSING: Status.PROCESSING,
                 FAILED: Status.FAILED}[status].value
        if status == FAILED and not retryable:
            label += " (not retryable)"
        self.history.append(HistoryEntry(datetime.now(), job.filename, job.platform.value,
                                         label, msg))

    @staticmethod
    def _notify(cb, job, jobs) -> None:
        if cb:
            cb(job, summarize(jobs))

