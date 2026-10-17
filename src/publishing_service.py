"""Publishing orchestration with pluggable publishers.

A publisher returns ``(ok, message)`` where ``ok`` is True (published),
False (failed) or None (submitted, still processing remotely).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable, Protocol

from src.models import HistoryEntry, Platform, Status, VideoRecord


class Publisher(Protocol):
    def publish(self, record: VideoRecord, platform: Platform) -> tuple[bool | None, str]: ...


class MockPublisher:
    """Simulates publishing. Never contacts external services."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self._attempts: dict[tuple[str, Platform], int] = {}

    def publish(self, record: VideoRecord, platform: Platform) -> tuple[bool | None, str]:
        key = (record.video_id, platform)
        self._attempts[key] = self._attempts.get(key, 0) + 1
        if self.delay:
            time.sleep(self.delay)
        if record.simulate_failure and self._attempts[key] == 1:
            return False, "Simulated error: upload timed out."
        return True, f"mock_post_{record.video_id}_{platform.value.lower()}"


ProgressCallback = Callable[[int, int, VideoRecord, Platform, Status], None]
RETRYABLE = (Status.FAILED, Status.RETRY_PENDING, Status.PROCESSING)


def build_jobs(
    records: list[VideoRecord], platforms: list[Platform] | None = None, retry_only: bool = False
) -> list[tuple[VideoRecord, Platform]]:
    jobs = []
    for rec in records:
        if not rec.is_valid:
            continue
        targets = platforms if platforms is not None else rec.selected_platforms
        for p in targets:
            if p not in rec.selected_platforms:
                continue
            status = rec.platform_status.get(p, Status.READY)
            if retry_only:
                if status in RETRYABLE:
                    jobs.append((rec, p))
            elif status in (Status.READY, Status.PENDING):
                jobs.append((rec, p))
    return jobs


def run_batch(
    jobs: list[tuple[VideoRecord, Platform]],
    publisher: Publisher,
    history: list[HistoryEntry],
    on_progress: ProgressCallback | None = None,
) -> dict[str, int]:
    total = len(jobs)
    summary = {"published": 0, "failed": 0, "processing": 0}

    def step(i, rec, p, status):
        rec.platform_status[p] = status
        if on_progress:
            on_progress(i, total, rec, p, status)

    for i, (rec, p) in enumerate(jobs):
        for s in (Status.VALIDATING, Status.UPLOADING):
            step(i, rec, p, s)
        try:
            ok, msg = publisher.publish(rec, p)
        except Exception as exc:  # publishers should raise only user-facing errors
            ok, msg = False, str(exc)
        final = {True: Status.PUBLISHED, False: Status.FAILED, None: Status.PROCESSING}[ok]
        step(i + 1, rec, p, final)
        summary[{True: "published", False: "failed", None: "processing"}[ok]] += 1
        history.append(HistoryEntry(datetime.now(), rec.filename, p.value, final.value, msg))
    return summary


def mark_retry_pending(records: list[VideoRecord]) -> int:
    n = 0
    for rec in records:
        for p, s in rec.platform_status.items():
            if s in (Status.FAILED, Status.PROCESSING):
                rec.platform_status[p] = Status.RETRY_PENDING
                n += 1
    return n
