"""Instagram Reels publishing via the Instagram API with Facebook Login (Graph API).

Flow (verify against current Meta docs – "Content Publishing" / resumable uploads):

1. ``GET  /{ig-user-id}/content_publishing_limit``                  quota check
2. ``POST /{ig-user-id}/media`` media_type=REELS, upload_type=resumable, caption
                                                                      -> container id + upload uri
3. ``POST rupload.facebook.com/ig-api-upload/{ver}/{container-id}``  binary upload
4. ``GET  /{container-id}?fields=status_code,status``                poll until FINISHED
5. ``POST /{ig-user-id}/media_publish`` creation_id=container        -> media id
6. ``GET  /{media-id}?fields=permalink,shortcode``                    confirmation

Idempotency: the container ID is persisted before ``media_publish``. On retry the
container status decides what happens – PUBLISHED: reconcile (no new post),
FINISHED: publish that same container, IN_PROGRESS: keep polling,
ERROR/EXPIRED: only then upload again.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from src.facebook_publisher import PublishResult, VideoMeta, content_hash
from src.job_store import (
    FAILED, FINISHING, PROCESSING, PUBLISHED, STARTING, UNKNOWN, UPLOADING, Job, JobStore,
)
from src.meta_errors import (
    MetaError, MetaNetworkError, MetaPublishError, MetaRateLimitError, MetaUnsupportedContentError,
)
from src.meta_graph import RUPLOAD_HOST, GraphClient

PLATFORM = "instagram"
SUPPORTED_EXT = {".mp4", ".mov"}
# Reels requirements – verify against current docs
MIN_DURATION_S, MAX_DURATION_S = 3, 15 * 60
MAX_BYTES = 300 * 1024 ** 2
MAX_CAPTION = 2200
MAX_HASHTAGS = 30
REMOTE_RECHECK = {UPLOADING, FINISHING, PROCESSING, UNKNOWN}


def validate_reel(path: Path, caption: str, meta: VideoMeta | None = None) -> None:
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_EXT:
        raise MetaUnsupportedContentError(f"'{path.name}': Instagram Reels need MP4 or MOV.")
    if not path.is_file() or path.stat().st_size == 0:
        raise MetaUnsupportedContentError(f"'{path.name}': local video file missing or empty.")
    if path.stat().st_size > MAX_BYTES:
        raise MetaUnsupportedContentError(f"'{path.name}' exceeds {MAX_BYTES // 1024 ** 2} MB.")
    if len(caption) > MAX_CAPTION:
        raise MetaUnsupportedContentError(f"Instagram caption exceeds {MAX_CAPTION} characters.")
    if caption.count("#") > MAX_HASHTAGS:
        raise MetaUnsupportedContentError(f"Instagram allows at most {MAX_HASHTAGS} hashtags.")
    if meta and meta.duration_s is not None and not MIN_DURATION_S <= meta.duration_s <= MAX_DURATION_S:
        raise MetaUnsupportedContentError(
            f"'{path.name}' is {meta.duration_s:.0f}s; Reels must be {MIN_DURATION_S}s–15 min.")


class InstagramReelsPublisher:
    def __init__(self, graph: GraphClient, ig_user_id: str, token: str, store: JobStore,
                 poll_interval_s: float = 5, poll_timeout_s: float = 300,
                 sleep: Callable[[float], None] = time.sleep, share_to_feed: bool = True):
        self.graph, self.ig_user_id, self._token, self.store = graph, ig_user_id, token, store
        self.poll_interval_s, self.poll_timeout_s, self.sleep = poll_interval_s, poll_timeout_s, sleep
        self.share_to_feed = share_to_feed

    # ------------------------------------------------------------ public
    def check_quota(self) -> None:
        resp = self.graph.request("GET", f"{self.ig_user_id}/content_publishing_limit",
                                  self._token, params={"fields": "quota_usage,config"})
        data = (resp.get("data") or [{}])[0]
        used = int(data.get("quota_usage", 0) or 0)
        total = int((data.get("config") or {}).get("quota_total", 0) or 0)
        if total and used >= total:
            raise MetaRateLimitError(f"Instagram publishing limit reached ({used}/{total} posts in "
                                     "24 h). Try again later.")

    def publish(self, video_key: str, path: Path, caption: str,
                meta: VideoMeta | None = None) -> PublishResult:
        job = self.store.get(video_key, PLATFORM, self.ig_user_id)
        chash = content_hash(caption)
        if job and job.status == PUBLISHED:
            msg = "Already published to Instagram – not posted again."
            if job.content_hash and job.content_hash != chash:
                msg += " (Caption changed since publishing; delete the post to republish.)"
            return PublishResult(PUBLISHED, job.remote_video_id, job.post_id, job.permalink, msg, True)

        if job and job.remote_video_id and job.status in REMOTE_RECHECK:
            res = self._resume(job)
            if res is not None:
                res.reused = True
                return res  # container still usable / already published

        validate_reel(path, caption, meta)
        self.check_quota()
        job = job or Job(video_key, PLATFORM, self.ig_user_id)
        job = self.store.save(job, status=STARTING, content_hash=chash, remote_video_id="",
                              post_id="", permalink="", attempts=job.attempts + 1, last_error="")
        try:
            container = self._create_container(caption)
            self.store.save(job, status=UPLOADING, remote_video_id=container)
            self._upload(container, Path(path))
            return self._wait_and_publish(job)
        except MetaError as exc:
            if job.status in (FINISHING, UNKNOWN):
                self.store.save(job, status=UNKNOWN, last_error=str(exc))
                return PublishResult(UNKNOWN, job.remote_video_id,
                                     message=f"Outcome unknown, will re-check on retry: {exc}")
            self.store.save(job, status=FAILED, last_error=str(exc))
            raise

    # ------------------------------------------------------------ steps
    def _create_container(self, caption: str) -> str:
        resp = self.graph.request("POST", f"{self.ig_user_id}/media", self._token, data={
            "media_type": "REELS", "upload_type": "resumable", "caption": caption,
            "share_to_feed": "true" if self.share_to_feed else "false"})
        if not resp.get("id"):
            raise MetaPublishError("Instagram did not return a media container ID.")
        return resp["id"]

    def _upload(self, container_id: str, path: Path, attempts: int = 3) -> None:
        url = f"{RUPLOAD_HOST}/ig-api-upload/{self.graph.version}/{container_id}"
        size = path.stat().st_size
        for attempt in range(attempts):
            try:
                with open(path, "rb") as fh:
                    resp = self.graph._send("POST", url, data=fh, retries=0, headers={
                        "Authorization": f"OAuth {self._token}", "offset": "0",
                        "file_size": str(size)})
                if resp.get("success", True) is False:
                    raise MetaPublishError("Instagram did not accept the video upload.")
                return
            except MetaNetworkError:
                if attempt == attempts - 1:
                    raise
                self.sleep(2 ** attempt)

    def _container_status(self, container_id: str) -> tuple[str, str]:
        info = self.graph.request("GET", container_id, self._token,
                                  params={"fields": "status_code,status"})
        return info.get("status_code", ""), info.get("status", "")

    def _wait_and_publish(self, job: Job) -> PublishResult:
        deadline = time.monotonic() + self.poll_timeout_s
        while True:
            code, detail = self._container_status(job.remote_video_id)
            if code == "FINISHED":
                return self._publish_container(job)
            if code == "PUBLISHED":
                return self._reconcile(job)
            if code in ("ERROR", "EXPIRED"):
                msg = f"Instagram media processing {code.lower()}: {detail or 'no details'}"
                self.store.save(job, status=FAILED, last_error=msg)
                return PublishResult(FAILED, job.remote_video_id, message=msg)
            if time.monotonic() >= deadline:
                self.store.save(job, status=PROCESSING)
                return PublishResult(PROCESSING, job.remote_video_id,
                                     message="Instagram is still processing the video – use Retry "
                                             "to re-check (no new upload).")
            self.sleep(self.poll_interval_s)

    def _publish_container(self, job: Job) -> PublishResult:
        self.store.save(job, status=FINISHING)  # persisted BEFORE the non-idempotent call
        try:
            resp = self.graph.request("POST", f"{self.ig_user_id}/media_publish", self._token,
                                      data={"creation_id": job.remote_video_id}, retries=0)
        except MetaNetworkError:
            self.store.save(job, status=UNKNOWN)
            code, _ = self._container_status(job.remote_video_id)
            if code == "PUBLISHED":
                return self._reconcile(job)
            if code == "FINISHED":  # publish definitely did not happen -> safe to call once more
                return self._publish_container(job)
            raise
        media_id = resp.get("id")
        if not media_id:
            raise MetaPublishError("Instagram did not confirm the publication.")
        return self._confirm(job, media_id)

    def _confirm(self, job: Job, media_id: str) -> PublishResult:
        permalink = ""
        try:
            info = self.graph.request("GET", media_id, self._token,
                                      params={"fields": "permalink,shortcode"}, retries=1)
            permalink = info.get("permalink", "")
        except MetaError:
            pass
        self.store.save(job, status=PUBLISHED, post_id=media_id, permalink=permalink, last_error="")
        return PublishResult(PUBLISHED, job.remote_video_id, media_id, permalink,
                             "Published to Instagram.")

    def _reconcile(self, job: Job) -> PublishResult:
        """Container already published: find the media ID among recent posts (no new post)."""
        try:
            resp = self.graph.request("GET", f"{self.ig_user_id}/media", self._token,
                                      params={"fields": "id,caption,permalink,timestamp",
                                              "limit": 25})
            for m in resp.get("data", []):
                if content_hash(m.get("caption", "") or "") == job.content_hash:
                    self.store.save(job, status=PUBLISHED, post_id=m["id"],
                                    permalink=m.get("permalink", ""), last_error="")
                    return PublishResult(PUBLISHED, job.remote_video_id, m["id"],
                                         m.get("permalink", ""), "Published to Instagram (confirmed).")
        except MetaError:
            pass
        self.store.save(job, status=PUBLISHED, last_error="")
        return PublishResult(PUBLISHED, job.remote_video_id,
                             message="Published to Instagram (post ID could not be looked up).")

    def _resume(self, job: Job) -> PublishResult | None:
        """Decide what to do with an existing container. None = start a fresh upload."""
        try:
            code, _ = self._container_status(job.remote_video_id)
        except MetaError:
            return None if job.status == UPLOADING else PublishResult(
                UNKNOWN, job.remote_video_id, message="Could not check Instagram status; retry later.")
        if code == "PUBLISHED":
            return self._reconcile(job)
        if code == "FINISHED" and job.status != UPLOADING:
            return self._publish_container(job)
        if code == "IN_PROGRESS" and job.status != UPLOADING:
            return self._wait_and_publish(job)
        return None  # ERROR / EXPIRED / interrupted upload -> new container is safe

