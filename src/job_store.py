"""Durable publishing job records (SQLite) – used for idempotent publishing.

One row per (video, platform, account). A row that reached the remote side
(``remote_video_id`` set and status finishing/processing/unknown/published) is
never re-uploaded blindly: retries first re-check the remote status.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

JOB_FIELDS = ("video_key", "platform", "account_id", "content_hash", "status", "remote_video_id",
              "post_id", "permalink", "attempts", "last_error", "created_at", "updated_at")

# status values
STARTING, UPLOADING, FINISHING, PROCESSING, UNKNOWN, PUBLISHED, FAILED = (
    "starting", "uploading", "finishing", "processing", "unknown", "published", "failed")
REMOTE_PENDING = {FINISHING, PROCESSING, UNKNOWN}


@dataclass
class Job:
    video_key: str
    platform: str
    account_id: str
    content_hash: str = ""
    status: str = STARTING
    remote_video_id: str = ""
    post_id: str = ""
    permalink: str = ""
    attempts: int = 0
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""


class JobStore:
    def __init__(self, database_path: str | Path = "data/publishing.db"):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self.initialize()

    def initialize(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS publish_jobs (
                video_key TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
                content_hash TEXT, status TEXT NOT NULL, remote_video_id TEXT, post_id TEXT,
                permalink TEXT, attempts INTEGER DEFAULT 0, last_error TEXT,
                created_at TEXT, updated_at TEXT,
                PRIMARY KEY (video_key, platform, account_id))""")
        self._conn.commit()

    def get(self, video_key: str, platform: str, account_id: str) -> Job | None:
        row = self._conn.execute(
            f"SELECT {', '.join(JOB_FIELDS)} FROM publish_jobs "
            "WHERE video_key=? AND platform=? AND account_id=?",
            (video_key, platform, account_id)).fetchone()
        return Job(*[v if v is not None else "" for v in row]) if row else None

    def save(self, job: Job, **changes) -> Job:
        for k, v in changes.items():
            setattr(job, k, v)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        job.created_at = job.created_at or now
        job.updated_at = now
        self._conn.execute(
            f"INSERT OR REPLACE INTO publish_jobs ({', '.join(JOB_FIELDS)}) "
            f"VALUES ({', '.join('?' * len(JOB_FIELDS))})",
            tuple(getattr(job, f) for f in JOB_FIELDS))
        self._conn.commit()
        return job

    def all(self, platform: str | None = None) -> list[Job]:
        q = f"SELECT {', '.join(JOB_FIELDS)} FROM publish_jobs"
        rows = self._conn.execute(q + (" WHERE platform=?" if platform else "")
                                  + " ORDER BY updated_at DESC",
                                  (platform,) if platform else ()).fetchall()
        return [Job(*[v if v is not None else "" for v in r]) for r in rows]

