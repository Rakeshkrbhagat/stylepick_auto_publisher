"""Publishing orchestration with pluggable publishers.
    return n
                n += 1
                rec.platform_status[p] = Status.RETRY_PENDING
            if s in (Status.FAILED, Status.PROCESSING):
        for p, s in rec.platform_status.items():
    for rec in records:
    n = 0
def mark_retry_pending(records: list[VideoRecord]) -> int:


    return summary
        history.append(HistoryEntry(datetime.now(), rec.filename, p.value, final.value, msg))
        summary[{True: "published", False: "failed", None: "processing"}[ok]] += 1
        step(i + 1, rec, p, final)
        final = {True: Status.PUBLISHED, False: Status.FAILED, None: Status.PROCESSING}[ok]
            ok, msg = False, str(exc)
        except Exception as exc:  # publishers should raise only user-facing errors
            ok, msg = publisher.publish(rec, p)
        try:
            step(i, rec, p, s)
        for s in (Status.VALIDATING, Status.UPLOADING):
    for i, (rec, p) in enumerate(jobs):

            on_progress(i, total, rec, p, status)
        if on_progress:
        rec.platform_status[p] = status
    def step(i, rec, p, status):

    summary = {"published": 0, "failed": 0, "processing": 0}
    total = len(jobs)
) -> dict[str, int]:
    on_progress: ProgressCallback | None = None,
    history: list[HistoryEntry],
    publisher: Publisher,
    jobs: list[tuple[VideoRecord, Platform]],
def run_batch(


    return jobs
                jobs.append((rec, p))
            elif status in (Status.READY, Status.PENDING):
                    jobs.append((rec, p))
                if status in RETRYABLE:
            if retry_only:
            status = rec.platform_status.get(p, Status.READY)
                continue
            if p not in rec.selected_platforms:
        for p in targets:
        targets = platforms if platforms is not None else rec.selected_platforms
            continue
        if not rec.is_valid:
    for rec in records:
    jobs = []
) -> list[tuple[VideoRecord, Platform]]:
    records: list[VideoRecord], platforms: list[Platform] | None = None, retry_only: bool = False
def build_jobs(


RETRYABLE = (Status.FAILED, Status.RETRY_PENDING, Status.PROCESSING)
ProgressCallback = Callable[[int, int, VideoRecord, Platform, Status], None]


        return True, f"mock_post_{record.video_id}_{platform.value.lower()}"
            return False, "Simulated error: upload timed out."
        if record.simulate_failure and self._attempts[key] == 1:
            time.sleep(self.delay)
        if self.delay:
        self._attempts[key] = self._attempts.get(key, 0) + 1
        key = (record.video_id, platform)
    def publish(self, record: VideoRecord, platform: Platform) -> tuple[bool | None, str]:

        self._attempts: dict[tuple[str, Platform], int] = {}
        self.delay = delay
    def __init__(self, delay: float = 0.0):

    """Simulates publishing. Never contacts external services."""
class MockPublisher:


    def publish(self, record: VideoRecord, platform: Platform) -> tuple[bool | None, str]: ...
class Publisher(Protocol):


from src.models import HistoryEntry, Platform, Status, VideoRecord

from typing import Callable, Protocol
from datetime import datetime
import time

from __future__ import annotations

"""
False (failed) or None (submitted, still processing remotely).
A publisher returns ``(ok, message)`` where ``ok`` is True (published),


