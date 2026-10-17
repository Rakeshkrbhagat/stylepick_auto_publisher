"""Facebook Reels publishing tests.

Unit tests use a fake Graph session (no network). The integration test at the
bottom runs only with an authorized test configuration:

    META_TEST_PAGE_ID, META_TEST_PAGE_TOKEN, META_TEST_VIDEO (path to a 9:16 MP4)
    [META_GRAPH_VERSION]

It creates the reel as a DRAFT so nothing is posted publicly.
"""

import os
from pathlib import Path

import pytest

from src.facebook_publisher import FacebookReelsPublisher, VideoMeta, validate_video
from src.job_store import FAILED, PUBLISHED, UNKNOWN, JobStore
from src.live_publisher import RoutingPublisher
from src.meta_auth import FacebookPage, MetaAuthManager
from src.meta_errors import (
    MetaPermissionError, MetaRateLimitError, MetaTokenExpiredError, MetaUnsupportedContentError,
    from_graph_error,
)
from src.meta_graph import GraphClient
from src.models import Platform, PlatformContent, Status, VideoRecord
from src.publishing_service import run_batch

PAGE = "111"


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


class FakeSession:
    """Routes requests by (method, url-fragment) to queued responses; records calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, params=None, data=None, headers=None, timeout=None):
        self.calls.append((method, url, dict(params or {}),
                           data if isinstance(data, dict) else None, headers))
        for (m, frag, pred), queue in self.routes.items():
            if m == method and frag in url and (pred is None or pred(params or {}, data)):
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"unexpected {method} {url} {params} {data}")


def phase(d):
    return lambda p, data: isinstance(data, dict) and data.get("upload_phase") == d


def status_body(pub="complete", vs="ready"):
    return {"status": {"video_status": vs, "uploading_phase": {"status": "complete"},
                       "processing_phase": {"status": "complete"},
                       "publishing_phase": {"status": pub}},
            "permalink_url": "/reel/999"}


def fields(f):
    return lambda p, data: p.get("fields") == f


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "stylepick_01.mp4"
    p.write_bytes(b"\x00" * 2048)
    return p


def make(routes, tmp_path, **kw):
    session = FakeSession(routes)
    graph = GraphClient("v23.0", session=session, sleep=lambda s: None)
    store = JobStore(tmp_path / "jobs.db")
    pub = FacebookReelsPublisher(graph, PAGE, "PAGE_TOKEN", store, poll_interval_s=0,
                                 sleep=lambda s: None, **kw)
    return pub, session, store


def happy_routes(status_seq=None):
    return {
        ("POST", f"{PAGE}/video_reels", phase("start")): [FakeResp(200, {"video_id": "999"})],
        ("POST", "rupload.facebook.com", None): [FakeResp(200, {"success": True})],
        ("POST", f"{PAGE}/video_reels", phase("finish")): [FakeResp(200, {"success": True})],
        ("GET", "/999", fields("status,permalink_url")):
            status_seq or [FakeResp(200, status_body("in_progress", "processing")),
                           FakeResp(200, status_body())],
        ("GET", "/999", fields("post_id")): [FakeResp(200, {"post_id": "111_555"})],
    }


def test_publish_happy_path_attaches_caption_and_stores_ids(tmp_path, video):
    pub, session, store = make(happy_routes(), tmp_path)
    caption = "Floral Kurti 💖\nफ्लोरल कुर्ती\n\n#StylePickFinds"
    res = pub.publish("vid01", video, caption)
    assert res.ok and res.video_id == "999" and res.post_id == "111_555"
    assert res.permalink == "https://www.facebook.com/reel/999"
    finish = next(c for c in session.calls if c[3] and c[3].get("upload_phase") == "finish")
    assert finish[3]["description"] == caption and finish[3]["video_id"] == "999"
    assert finish[3]["video_state"] == "PUBLISHED" and finish[2]["access_token"] == "PAGE_TOKEN"
    upload = next(c for c in session.calls if "rupload" in c[1])
    assert upload[4]["Authorization"] == "OAuth PAGE_TOKEN" and upload[4]["file_size"] == "2048"
    job = store.get("vid01", "facebook", PAGE)
    assert job.status == PUBLISHED and job.post_id == "111_555" and job.attempts == 1


def test_retry_after_publish_does_not_duplicate(tmp_path, video):
    pub, session, _ = make(happy_routes(), tmp_path)
    pub.publish("vid01", video, "caption")
    n = len(session.calls)
    res = pub.publish("vid01", video, "edited caption")
    assert res.ok and res.reused and "Already published" in res.message
    assert len(session.calls) == n  # no API calls at all


def test_finish_network_error_rechecks_instead_of_reuploading(tmp_path, video):
    routes = happy_routes([FakeResp(200, status_body())])
    routes[("POST", f"{PAGE}/video_reels", phase("finish"))] = [ConnectionError("reset")]
    pub, session, store = make(routes, tmp_path)
    res = pub.publish("vid01", video, "caption")
    assert res.ok  # status check showed it was published
    starts = [c for c in session.calls if c[3] and c[3].get("upload_phase") == "start"]
    finishes = [c for c in session.calls if c[3] and c[3].get("upload_phase") == "finish"]
    assert len(starts) == 1 and len(finishes) == 1


def test_unknown_outcome_retry_only_polls(tmp_path, video):
    routes = happy_routes([FakeResp(500, {"error": {"code": 2, "message": "down"}})])
    routes[("POST", f"{PAGE}/video_reels", phase("finish"))] = [ConnectionError("reset")]
    pub, session, store = make(routes, tmp_path)
    pub.graph.request.__func__  # sanity: real client
    res = pub.publish("vid01", video, "caption")
    assert res.status == UNKNOWN and store.get("vid01", "facebook", PAGE).status == UNKNOWN
    # second attempt: remote now published -> only status polls, no new upload
    session.routes[("GET", "/999", fields("status,permalink_url"))] = [FakeResp(200, status_body())]
    before = len([c for c in session.calls if "rupload" in c[1]])
    res = pub.publish("vid01", video, "caption")
    assert res.ok and res.reused
    assert len([c for c in session.calls if "rupload" in c[1]]) == before


def test_processing_timeout_reports_processing(tmp_path, video):
    routes = happy_routes([FakeResp(200, status_body("in_progress", "processing"))])
    pub, _, store = make(routes, tmp_path, poll_timeout_s=0)
    res = pub.publish("vid01", video, "caption")
    assert res.status == "processing" and store.get("vid01", "facebook", PAGE).status == "processing"


def test_remote_processing_error_marks_failed_and_allows_restart(tmp_path, video):
    bad = {"status": {"video_status": "error",
                      "processing_phase": {"status": "error",
                                           "errors": [{"message": "Aspect ratio not supported"}]}}}
    pub, session, store = make(happy_routes([FakeResp(200, bad)]), tmp_path)
    res = pub.publish("vid01", video, "caption")
    assert res.status == FAILED and "Aspect ratio" in res.message
    session.routes[("GET", "/999", fields("status,permalink_url"))] = [FakeResp(200, status_body())]
    res = pub.publish("vid01", video, "caption")
    assert res.ok and store.get("vid01", "facebook", PAGE).attempts == 2


def test_expired_token_and_rate_limit_errors(tmp_path, video):
    routes = {("POST", f"{PAGE}/video_reels", phase("start")):
              [FakeResp(400, {"error": {"code": 190, "message": "Session has expired"}})]}
    pub, _, store = make(routes, tmp_path)
    with pytest.raises(MetaTokenExpiredError):
        pub.publish("vid01", video, "c")
    assert store.get("vid01", "facebook", PAGE).status == FAILED

    routes = {("POST", f"{PAGE}/video_reels", phase("start")):
              [FakeResp(400, {"error": {"code": 32, "message": "Page request limit reached"}})]}
    pub, session, _ = make(routes, tmp_path / "b")
    with pytest.raises(MetaRateLimitError):
        pub.publish("vid02", video, "c")
    assert len(session.calls) == 4  # 1 + 3 backoff retries


def test_error_mapping():
    assert isinstance(from_graph_error({"code": 200, "message": "x"}), MetaPermissionError)
    assert isinstance(from_graph_error({"code": 100, "error_subcode": 1363022}),
                      MetaUnsupportedContentError)
    assert from_graph_error({"code": 2}).retryable


def test_validate_video(tmp_path, video):
    with pytest.raises(MetaUnsupportedContentError, match="MP4/MOV"):
        validate_video(tmp_path / "x.avi")
    with pytest.raises(MetaUnsupportedContentError, match="3–90"):
        validate_video(video, VideoMeta(duration_s=120))
    with pytest.raises(MetaUnsupportedContentError, match="vertical"):
        validate_video(video, VideoMeta(width=1920, height=1080))
    validate_video(video, VideoMeta(duration_s=30, width=1080, height=1920))


def test_unsupported_content_makes_no_api_calls(tmp_path):
    pub, session, _ = make({}, tmp_path)
    bad = tmp_path / "clip.avi"
    bad.write_bytes(b"x")
    with pytest.raises(MetaUnsupportedContentError):
        pub.publish("v", bad, "c")
    assert session.calls == []


def test_routing_publisher_uses_facebook_text_and_maps_results(tmp_path, video):
    pub, session, _ = make(happy_routes(), tmp_path)
    rec = VideoRecord("vid01", "stylepick_01.mp4", content={
        Platform.FACEBOOK: PlatformContent(description="FB desc 😍", hashtags="#a #b"),
        Platform.INSTAGRAM: PlatformContent(caption="IG")})
    router = RoutingPublisher(pub, lambda r: (video, None))
    history = []
    res = run_batch([(rec, Platform.FACEBOOK), (rec, Platform.INSTAGRAM)], router, history)
    assert res["published"] == 2
    finish = next(c for c in session.calls if c[3] and c[3].get("upload_phase") == "finish")
    assert finish[3]["description"] == "FB desc 😍\n\n#a #b"
    assert rec.platform_status[Platform.FACEBOOK] == Status.PUBLISHED
    assert "post_id=111_555" in history[0].message


# ---------------- auth
class MemStore:
    def __init__(self):
        self.d = {}

    def load(self):
        return dict(self.d)

    def save(self, d):
        self.d = dict(d)

    def delete(self):
        self.d = {}


def test_auth_token_check_pages_and_selection():
    routes = {
        ("GET", "oauth/access_token", lambda p, d: "code" in p): [FakeResp(200, {"access_token": "S"})],
        ("GET", "oauth/access_token", lambda p, d: "fb_exchange_token" in p):
            [FakeResp(200, {"access_token": "LONG"})],
        ("GET", "debug_token", None): [FakeResp(200, {"data": {
            "is_valid": True, "expires_at": 0, "scopes": ["pages_show_list", "pages_read_engagement"]}})],
        ("GET", "me/accounts", None): [FakeResp(200, {"data": [
            {"id": "1", "name": "StylePick", "access_token": "PT", "tasks": ["CREATE_CONTENT"]},
            {"id": "2", "name": "Other", "access_token": "PT2", "tasks": ["ANALYZE"]}]})],
    }
    auth = MetaAuthManager("APP", "SECRET", GraphClient("v23.0", session=FakeSession(routes),
                                                          sleep=lambda s: None), store=MemStore())
    auth.complete_login("CODE")
    assert auth.user_token() == "LONG"
    st = auth.check_token()
    assert st.valid and st.missing == ["pages_manage_posts"]
    with pytest.raises(MetaPermissionError):
        auth.require_valid()
    pages = auth.list_pages()
    assert [p.can_publish for p in pages] == [True, False]
    with pytest.raises(MetaPermissionError):
        auth.select_page(pages[1])
    auth.select_page(pages[0])
    assert auth.selected_page().id == "1"
    assert isinstance(auth.selected_page(), FacebookPage)


# ---------------- authorized integration test (opt-in)
INTEGRATION = all(os.getenv(k) for k in ("META_TEST_PAGE_ID", "META_TEST_PAGE_TOKEN", "META_TEST_VIDEO"))


@pytest.mark.skipif(not INTEGRATION, reason="Set META_TEST_PAGE_ID/TOKEN/VIDEO to run live test")
def test_live_reel_draft_upload(tmp_path):
    graph = GraphClient(os.getenv("META_GRAPH_VERSION", "v23.0"))
    pub = FacebookReelsPublisher(graph, os.environ["META_TEST_PAGE_ID"],
                                 os.environ["META_TEST_PAGE_TOKEN"], JobStore(tmp_path / "j.db"),
                                 poll_interval_s=5, poll_timeout_s=120, video_state="DRAFT")
    res = pub.publish("integration-test", Path(os.environ["META_TEST_VIDEO"]),
                      "StylePick integration test ✨ #test")
    assert res.video_id and res.status in ("published", "processing")

