"""Minimal Graph API client (requests-based) with error mapping, safe retries and
token redaction in every error message."""

from __future__ import annotations

import random
import time
from typing import Any, Callable

from src.meta_errors import MetaError, MetaNetworkError, from_graph_error
from src.redaction import redact

GRAPH_HOST = "https://graph.facebook.com"
RUPLOAD_HOST = "https://rupload.facebook.com"


class GraphClient:
    def __init__(self, version: str, session=None, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 60):
        if session is None:
            import requests

            session = requests.Session()
        self.version, self.session, self.sleep, self.timeout = version, session, sleep, timeout

    def url(self, path: str) -> str:
        return f"{GRAPH_HOST}/{self.version}/{path.lstrip('/')}"

    def request(self, method: str, path: str, token: str | None = None, *,
                params: dict | None = None, data: dict | None = None,
                retries: int = 3) -> dict[str, Any]:
        """Call the Graph API. ``retries`` only applies to retryable errors; use
        ``retries=0`` for non-idempotent calls (e.g. publishing)."""
        params = dict(params or {})
        if token:
            params["access_token"] = token
        return self._send(method, self.url(path), params=params, data=data, retries=retries)

    def _send(self, method, url, *, params=None, data=None, headers=None, retries=3):
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(method, url, params=params, data=data,
                                            headers=headers, timeout=self.timeout)
            except Exception as exc:  # requests.RequestException / OSError
                # exception text may contain the full URL incl. access_token -> redact
                err: MetaError = MetaNetworkError(
                    f"Network error contacting Facebook ({type(exc).__name__}): {redact(exc)}")
            else:
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                if resp.status_code < 400 and "error" not in body:
                    return body
                raw = dict(body.get("error", {"message": f"HTTP {resp.status_code}"}))
                for k in ("message", "error_user_msg", "error_user_title"):
                    if k in raw:
                        raw[k] = redact(raw[k])
                err = from_graph_error(raw, resp.status_code)
            if not err.retryable or attempt >= retries:
                raise err
            self.sleep(min(2 ** attempt + random.random(), 60))
        raise MetaNetworkError()  # pragma: no cover

    def upload_binary(self, video_id: str, token: str, file_obj, file_size: int,
                      offset: int = 0) -> dict:
        """Upload a local file to the resumable upload host (rupload)."""
        url = f"{RUPLOAD_HOST}/video-upload/{self.version}/{video_id}"
        headers = {"Authorization": f"OAuth {token}", "offset": str(offset),
                   "file_size": str(file_size)}
        return self._send("POST", url, data=file_obj, headers=headers, retries=0)

