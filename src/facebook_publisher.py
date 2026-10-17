"""Facebook Page Reels publishing via the official Graph API Reels endpoints.

Flow (see https://developers.facebook.com/docs/video-api/guides/reels-publishing –
verify against the current docs/version before use):

1. ``POST /{page-id}/video_reels`` ``upload_phase=start``      -> video_id
2. ``POST rupload.facebook.com/video-upload/{ver}/{video_id}``  (binary upload)
3. ``POST /{page-id}/video_reels`` ``upload_phase=finish``     (+ ``video_state``, ``description``)
4. ``GET /{video_id}?fields=status,permalink_url``              poll until published

Idempotency: every step is recorded in the ``JobStore`` *before* the
non-idempotent finish call. A retry for a job whose finish call may have
reached Facebook only re-checks the remote status – it never uploads again.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.job_store import (
    FAILED, FINISHING, PROCESSING, PUBLISHED, REMOTE_PENDING, STARTING, UNKNOWN, UPLOADING, Job,
    JobStore,
)
from src.meta_errors import (
    MetaError, MetaNetworkError, MetaPublishError, MetaUnsupportedContentError,
)
from src.meta_graph import GraphClient

PLATFORM = "facebook"
SUPPORTED_EXT = {".mp4", ".mov"}
# Reels requirements (verify against current Meta docs)
MIN_DURATION_S, MAX_DURATION_S = 3, 90
MIN_WIDTH, MIN_HEIGHT = 540, 960
MAX_BYTES = 1024 ** 3  # 1 GB conservative limit


@dataclass
class VideoMeta:
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class PublishResult:
    status: str               # published | processing | failed
    video_id: str = ""
    post_id: str = ""
    permalink: str = ""
    message: str = ""
    reused: bool = False      # True if returned from a previous run (no new upload)

    @property
    def ok(self) -> bool:
        return self.status == PUBLISHED


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def validate_video(path: Path, meta: VideoMeta | None = None) -> None:
    """Raise MetaUnsupportedContentError for files Reels will reject."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_EXT:
        raise MetaUnsupportedContentError(f"'{path.name}': only MP4/MOV are supported for Reels.")
    if not path.is_file():
        raise MetaUnsupportedContentError(f"'{path.name}': local video file not found.")
    size = path.stat().st_size
    if size == 0:
        raise MetaUnsupportedContentError(f"'{path.name}' is empty.")
    if size > MAX_BYTES:
        raise MetaUnsupportedContentError(f"'{path.name}' exceeds {MAX_BYTES // 1024 ** 2} MB.")
    if meta:
        if meta.duration_s is not None and not MIN_DURATION_S <= meta.duration_s <= MAX_DURATION_S:
            raise MetaUnsupportedContentError(
                f"'{path.name}' is {meta.duration_s:.0f}s; Reels must be "
                f"{MIN_DURATION_S}–{MAX_DURATION_S}s.")
        if meta.width and meta.height:
            if meta.width >= meta.height:
                raise MetaUnsupportedContentError(f"'{path.name}' is not vertical (9:16 required).")
            if meta.width < MIN_WIDTH or meta.height < MIN_HEIGHT:
                raise MetaUnsupportedContentError(
                    f"'{path.name}' is {meta.width}x{meta.height}; minimum is {MIN_WIDTH}x{MIN_HEIGHT}.")


class FacebookReelsPublisher:
    def __init__(self, graph: GraphClient, page_id: str, page_token: str, store: JobStore,
                 poll_interval_s: float = 5, poll_timeout_s: float = 300,
                 sleep: Callable[[float], None] = time.sleep, video_state: str = "PUBLISHED"):
        self.graph, self.page_id, self._token, self.store = graph, page_id, page_token, store
        self.poll_interval_s, self.poll_timeout_s, self.sleep = poll_interval_s, poll_timeout_s, sleep
        self.video_state = video_state  # "DRAFT" is useful for integration tests

    # ------------------------------------------------------------ public
    def publish(self, video_key: str, path: Path, description: str,
                meta: VideoMeta | None = None) -> PublishResult:
        job = self.store.get(video_key, PLATFORM, self.page_id)
        chash = content_hash(description)

        if job and job.status == PUBLISHED:
            msg = "Already published – not posted again."
            if job.content_hash and job.content_hash != chash:
                msg += " (Caption changed since publishing; delete the post on Facebook to republish.)"
            return PublishResult(PUBLISHED, job.remote_video_id, job.post_id, job.permalink, msg, True)

        if job and job.remote_video_id and job.status in REMOTE_PENDING:
            # The finish call may already have been accepted: check, never re-upload blindly.
            res = self.check_status(job)
            if res.status != FAILED:
                res.reused = True
                return res
            # Facebook reported a definitive error for that upload -> safe to start over.

        validate_video(path, meta)
        job = job or Job(video_key, PLATFORM, self.page_id)
        job = self.store.save(job, status=STARTING, content_hash=chash, remote_video_id="",
                              attempts=job.attempts + 1, last_error="")
        try:
            return self._run(job, Path(path), description)
        except MetaError as exc:
            if job.status in (FINISHING, UNKNOWN):  # outcome unknown – keep for status re-check
                self.store.save(job, status=UNKNOWN, last_error=str(exc))
                return PublishResult(UNKNOWN, job.remote_video_id,
                                     message=f"Outcome unknown, will re-check on retry: {exc}")
            self.store.save(job, status=FAILED, last_error=str(exc))
            raise

    def check_status(self, job: Job) -> PublishResult:
        """Poll the remote video until published/error/timeout and persist the outcome."""
        deadline = time.monotonic() + self.poll_timeout_s
        while True:
            info = self.graph.request("GET", job.remote_video_id, self._token,
                                      params={"fields": "status,permalink_url"})
            state, err = _interpret_status(info.get("status", {}))
            if state == PUBLISHED:
                permalink = info.get("permalink_url", "")
                if permalink.startswith("/"):
                    permalink = "https://www.facebook.com" + permalink
                post_id = self._lookup_post_id(job.remote_video_id)
                self.store.save(job, status=PUBLISHED, permalink=permalink, post_id=post_id,
                                last_error="")
                return PublishResult(PUBLISHED, job.remote_video_id, post_id, permalink,
                                     "Published to Facebook.")
            if state == FAILED:
                self.store.save(job, status=FAILED, last_error=err)
                return PublishResult(FAILED, job.remote_video_id, message=err)
            if time.monotonic() >= deadline:
                self.store.save(job, status=PROCESSING)
                return PublishResult(PROCESSING, job.remote_video_id,
                                     message="Still processing on Facebook – use Retry to re-check.")
            self.sleep(self.poll_interval_s)

    # ------------------------------------------------------------ steps
    def _run(self, job: Job, path: Path, description: str) -> PublishResult:
        start = self.graph.request("POST", f"{self.page_id}/video_reels", self._token,
                                   data={"upload_phase": "start"})
        video_id = start.get("video_id")
        if not video_id:
            raise MetaPublishError("Facebook did not return a video ID for the upload session.")
        self.store.save(job, status=UPLOADING, remote_video_id=video_id)

        self._upload(video_id, path)

        self.store.save(job, status=FINISHING)  # persisted BEFORE the non-idempotent call
        try:
            fin = self.graph.request("POST", f"{self.page_id}/video_reels", self._token, data={
                "upload_phase": "finish", "video_id": video_id,
                "video_state": self.video_state, "description": description,
            }, retries=0)
        except MetaNetworkError:
            self.store.save(job, status=UNKNOWN)
            return self.check_status(job)
        if fin.get("success") is False:
            raise MetaPublishError("Facebook rejected the publish request.")
        self.store.save(job, status=PROCESSING)
        return self.check_status(job)

    def _upload(self, video_id: str, path: Path, attempts: int = 3) -> None:
        size = path.stat().st_size
        for attempt in range(attempts):
            offset = self._uploaded_bytes(video_id) if attempt else 0
            try:
                with open(path, "rb") as fh:
                    fh.seek(offset)
                    resp = self.graph.upload_binary(video_id, self._token, fh, size, offset)
                if resp.get("success", True) is False:
                    raise MetaPublishError("Video upload was not accepted by Facebook.")
                return
            except MetaNetworkError:
                if attempt == attempts - 1:
                    raise
                self.sleep(2 ** attempt)

    def _uploaded_bytes(self, video_id: str) -> int:
        try:
            info = self.graph.request("GET", video_id, self._token, params={"fields": "status"})
            return int(info.get("status", {}).get("uploading_phase", {}).get("bytes_transferred", 0))
        except (MetaError, ValueError):
            return 0

    def _lookup_post_id(self, video_id: str) -> str:
        """Best effort: the Page post ID for the reel (falls back to the video ID)."""
        try:
            info = self.graph.request("GET", video_id, self._token, params={"fields": "post_id"},
                                      retries=0)
            return str(info.get("post_id") or video_id)
        except MetaError:
            return video_id


def _interpret_status(status: dict) -> tuple[str, str]:
    """Map the Graph video ``status`` object to published/failed/processing."""
    phases = {k: status.get(k, {}) or {} for k in
              ("uploading_phase", "processing_phase", "publishing_phase")}
    for name, ph in phases.items():
        if ph.get("status") == "error":
            errs = ph.get("errors") or [{}]
            msg = "; ".join(e.get("message", "") for e in errs if e) or f"{name} failed"
            return FAILED, f"Facebook {name.replace('_', ' ')} error: {msg}"
    if status.get("video_status") == "error":
        return FAILED, "Facebook reported a video processing error."
    pub = phases["publishing_phase"]
    if pub.get("status") == "complete" or pub.get("publish_status") == "published":
        return PUBLISHED, ""
    return PROCESSING, ""


def file_size(path: str | os.PathLike) -> int:
    return Path(path).stat().st_size

