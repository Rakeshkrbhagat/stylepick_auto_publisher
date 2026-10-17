"""Persistent publishing history and per-platform status (SQLite).

Tables
- ``publish_attempts`` – one row per publishing attempt (live or simulated): video ID,
  filename, platform, account, mode, caption used, outcome, remote/post IDs, URL, error.
- ``platform_state``   – latest known state per (video, platform, account); survives
  restarts and drives duplicate protection.

Safety
- Every write runs in a single transaction (``with conn``) – a failure rolls back and
  never leaves half-written records. WAL journaling + busy timeout for robustness.
- Attempts left in ``started`` state (app closed mid-publish) are marked
  ``uncertain`` on startup; they must be verified before any retry.
- All sqlite errors are raised as ``HistoryStoreError`` with a clear message.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STARTED, PUBLISHED, PROCESSING, FAILED, UNCERTAIN = (
    "started", "published", "processing", "failed", "uncertain")
SIMULATED = "simulated"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS publish_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_key TEXT NOT NULL, filename TEXT NOT NULL, platform TEXT NOT NULL,
    account_id TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
    caption TEXT, remote_id TEXT, post_id TEXT, permalink TEXT, error TEXT,
    retryable INTEGER DEFAULT 1, started_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_video ON publish_attempts (video_key, platform);
CREATE TABLE IF NOT EXISTS platform_state (
    video_key TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
    filename TEXT NOT NULL, status TEXT NOT NULL, caption TEXT, post_id TEXT,
    permalink TEXT, error TEXT, retryable INTEGER DEFAULT 1, last_attempt_id INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (video_key, platform, account_id)
);
"""


class HistoryStoreError(Exception):
    pass


@dataclass
class Attempt:
    id: int
    video_key: str
    filename: str
    platform: str
    account_id: str
    mode: str
    status: str
    caption: str
    remote_id: str
    post_id: str
    permalink: str
    error: str
    retryable: bool
    started_at: str
    finished_at: str


@dataclass
class PlatformState:
    video_key: str
    platform: str
    account_id: str
    filename: str
    status: str
    caption: str
    post_id: str
    permalink: str
    error: str
    retryable: bool
    last_attempt_id: int
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HistoryStore:
    def __init__(self, database_path: str | Path = "data/publishing.db"):
        self.database_path = str(database_path)
        self._lock = threading.Lock()
        try:
            if self.database_path != ":memory:":
                Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.database_path, timeout=10, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            if self.database_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            ok = self._conn.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise HistoryStoreError(f"Publishing database integrity check failed: {ok}. "
                                        "Make a backup of the file before repairing it.")
            with self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise HistoryStoreError(f"Cannot open publishing database '{self.database_path}': {exc}") from exc
        self.recovered = self.recover_interrupted()

    # ------------------------------------------------------------ writes
    def _tx(self, fn):
        with self._lock:
            try:
                with self._conn:  # BEGIN ... COMMIT / ROLLBACK on error
                    return fn(self._conn)
            except sqlite3.Error as exc:
                raise HistoryStoreError(f"Publishing database error (no records changed): {exc}") from exc

    def start_attempt(self, video_key: str, filename: str, platform: str, account_id: str,
                      mode: str, caption: str) -> int:
        def fn(c):
            cur = c.execute(
                "INSERT INTO publish_attempts (video_key, filename, platform, account_id, mode, "
                "status, caption, started_at) VALUES (?,?,?,?,?,?,?,?)",
                (video_key, filename, platform, account_id, mode, STARTED, caption, _now()))
            return cur.lastrowid
        return self._tx(fn)

    def finish_attempt(self, attempt_id: int, status: str, *, remote_id: str = "",
                       post_id: str = "", permalink: str = "", error: str = "",
                       retryable: bool = True) -> None:
        if status not in (PUBLISHED, PROCESSING, FAILED, UNCERTAIN):
            raise ValueError(status)

        def fn(c):
            c.execute("UPDATE publish_attempts SET status=?, remote_id=?, post_id=?, permalink=?, "
                      "error=?, retryable=?, finished_at=? WHERE id=?",
                      (status, remote_id, post_id, permalink, error, int(retryable), _now(),
                       attempt_id))
            row = c.execute("SELECT * FROM publish_attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None:
                raise sqlite3.IntegrityError(f"attempt {attempt_id} not found")
            prev = c.execute("SELECT status FROM platform_state WHERE video_key=? AND platform=? "
                             "AND account_id=?", (row["video_key"], row["platform"],
                                                  row["account_id"])).fetchone()
            if prev and prev["status"] == PUBLISHED and status != PUBLISHED:
                return  # never downgrade a confirmed publication
            c.execute(
                "INSERT OR REPLACE INTO platform_state (video_key, platform, account_id, filename, "
                "status, caption, post_id, permalink, error, retryable, last_attempt_id, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["video_key"], row["platform"], row["account_id"], row["filename"], status,
                 row["caption"], post_id, permalink, error, int(retryable), attempt_id, _now()))
        self._tx(fn)

    def recover_interrupted(self) -> int:
        """Attempts still 'started' after a restart have an unknown outcome."""
        def fn(c):
            rows = c.execute("SELECT id FROM publish_attempts WHERE status=?", (STARTED,)).fetchall()
            return [r["id"] for r in rows]
        ids = self._tx(fn)
        for i in ids:
            self.finish_attempt(i, UNCERTAIN, error="App closed before the result was known – "
                                                    "will be verified before retrying.")
        return len(ids)

    # ------------------------------------------------------------ reads
    def _read(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._conn.execute(sql, args).fetchall()
            except sqlite3.Error as exc:
                raise HistoryStoreError(f"Publishing database read error: {exc}") from exc

    def states(self, account_ids: dict[str, str]) -> list[PlatformState]:
        """Latest states for the given {platform: account_id}."""
        out = []
        for platform, account in account_ids.items():
            out += [PlatformState(**{**dict(r), "retryable": bool(r["retryable"])})
                    for r in self._read("SELECT * FROM platform_state WHERE platform=? AND "
                                        "account_id=?", (platform, account))]
        return out

    def state(self, video_key: str, platform: str, account_id: str) -> PlatformState | None:
        rows = self._read("SELECT * FROM platform_state WHERE video_key=? AND platform=? AND "
                          "account_id=?", (video_key, platform, account_id))
        return PlatformState(**{**dict(rows[0]), "retryable": bool(rows[0]["retryable"])}) \
            if rows else None

    def find_duplicate(self, video_key: str, filename: str, platform: str,
                       account_id: str) -> PlatformState | None:
        """Previously published state for the same Drive file ID, or the same filename."""
        rows = self._read(
            "SELECT * FROM platform_state WHERE platform=? AND account_id=? AND status=? AND "
            "(video_key=? OR lower(filename)=lower(?)) ORDER BY updated_at DESC LIMIT 1",
            (platform, account_id, PUBLISHED, video_key, filename))
        return PlatformState(**{**dict(rows[0]), "retryable": bool(rows[0]["retryable"])}) \
            if rows else None

    def attempts(self, platform: str | None = None, status: str | None = None,
                 text: str = "", mode: str | None = None, limit: int = 500) -> list[Attempt]:
        sql, args = "SELECT * FROM publish_attempts WHERE 1=1", []
        if platform:
            sql += " AND platform=?"
            args.append(platform)
        if status:
            sql += " AND status=?"
            args.append(status)
        if mode:
            sql += " AND mode=?"
            args.append(mode)
        if text:
            sql += " AND (filename LIKE ? OR video_key LIKE ? OR caption LIKE ? OR error LIKE ?)"
            args += [f"%{text}%"] * 4
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [Attempt(**{**{k: (v if v is not None else "") for k, v in dict(r).items()},
                           "retryable": bool(r["retryable"])})
                for r in self._read(sql, tuple(args))]

    def close(self) -> None:
        self._conn.close()

