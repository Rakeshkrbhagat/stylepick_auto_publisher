"""Instagram Reels publishing tests (fake Graph session, no network).

Optional live test: set META_TEST_IG_USER_ID, META_TEST_IG_TOKEN, META_TEST_VIDEO and
META_TEST_IG_PUBLISH=1 (this WILL create a public Reel – use a test account).
"""

import os
from pathlib import Path

import pytest

from src.facebook_publisher import VideoMeta
from src.instagram_publisher import InstagramReelsPublisher, validate_reel
from src.job_store import FAILED, PUBLISHED, UNKNOWN, JobStore
from src.live_publisher import RoutingPublisher
from src.meta_auth import FacebookPage, MetaAuthManager
from src.meta_errors import (
    MetaPermissionError, MetaRateLimitError, MetaTokenExpiredError, MetaUnsupportedContentError,
)
from src.meta_graph import GraphClient
from src.models import Platform, PlatformContent, Status, VideoRecord
from src.publishing_service import run_batch
from tests.test_facebook_publisher import FakeResp, FakeSession, MemStore

IG = "1784"
CAPTION = "Kurti 💖\nफ्लोरल कुर्ती ✨\n\n#StylePickFinds #OOTD"
STATUS_KEY = ("GET", "/C1", None)


def is_(**kv):
    return lambda p, d: all((d or {}).get(k) == v for k, v in kv.items())


def fields(f):
    return lambda p, d: p.get("fields") == f


def status_route():
    return ("GET", "/C1", fields("status_code,status"))


def routes(status_seq=None, publish=None, media_list=None):
    return {
        ("GET", f"{IG}/content_publishing_limit", None):
            [FakeResp(200, {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]})],
        ("POST", f"{IG}/media_publish", None): publish or [FakeResp(200, {"id": "MEDIA1"})],
        ("POST", f"{IG}/media", is_(media_type="REELS")): [FakeResp(200, {"id": "C1"})],
        ("POST", "ig-api-upload", None): [FakeResp(200, {"success": True})],
        status_route():
            status_seq or [FakeResp(200, {"status_code": "IN_PROGRESS"}),
                           FakeResp(200, {"status_code": "FINISHED"})],
        ("GET", "/MEDIA1", fields("permalink,shortcode")):
            [FakeResp(200, {"permalink": "https://www.instagram.com/reel/abc/"})],
        ("GET", f"{IG}/media", fields("id,caption,permalink,timestamp")):
            media_list or [FakeResp(200, {"data": []})],
    }


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "stylepick_01.mp4"
    p.write_bytes(b"\x00" * 4096)
    return p


def make(r, tmp_path, **kw):
    session = FakeSession(r)
    graph = GraphClient("v23.0", session=session, sleep=lambda s: None)
    pub = InstagramReelsPublisher(graph, IG, "TOKEN", JobStore(tmp_path / "jobs.db"),
                                  poll_interval_s=0, sleep=lambda s: None, **kw)
    return pub, session, pub.store


def calls(session, frag, method="POST"):
    return [c for c in session.calls if c[0] == method and frag in c[1]]


def creates(session):
    return [c for c in calls(session, f"{IG}/media") if c[3] and c[3].get("media_type") == "REELS"]


def test_happy_path_caption_container_publish_and_store(tmp_path, video):
    pub, session, store = make(routes(), tmp_path)
    res = pub.publish("vid01", video, CAPTION)
    assert res.ok and res.post_id == "MEDIA1" and res.permalink.endswith("/reel/abc/")
    create = creates(session)[0]
    assert create[3]["caption"] == CAPTION and create[3]["upload_type"] == "resumable"
    upload = calls(session, "ig-api-upload")[0]
    assert "/v23.0/C1" in upload[1] and upload[4]["Authorization"] == "OAuth TOKEN"
    assert upload[4]["file_size"] == "4096"
    assert calls(session, "media_publish")[0][3] == {"creation_id": "C1"}
    job = store.get("vid01", "instagram", IG)
    assert job.status == PUBLISHED and job.post_id == "MEDIA1" and job.remote_video_id == "C1"


def test_retry_after_success_makes_no_calls(tmp_path, video):
    pub, session, _ = make(routes(), tmp_path)
    pub.publish("vid01", video, CAPTION)
    n = len(session.calls)
    res = pub.publish("vid01", video, CAPTION)
    assert res.ok and res.reused and len(session.calls) == n


def test_publish_network_error_reconciles_without_duplicate(tmp_path, video):
    r = routes(status_seq=[FakeResp(200, {"status_code": "FINISHED"}),
                           FakeResp(200, {"status_code": "PUBLISHED"})],
               publish=[ConnectionError("reset")],
               media_list=[FakeResp(200, {"data": [
                   {"id": "OTHER", "caption": "old post"},
                   {"id": "MEDIA9", "caption": CAPTION, "permalink": "https://ig/p/9"}]})])
    pub, session, store = make(r, tmp_path)
    res = pub.publish("vid01", video, CAPTION)
    assert res.ok and res.post_id == "MEDIA9"
    assert len(calls(session, "media_publish")) == 1
    assert store.get("vid01", "instagram", IG).post_id == "MEDIA9"


def test_unknown_outcome_then_retry_uses_same_container(tmp_path, video):
    r = routes(status_seq=[FakeResp(200, {"status_code": "FINISHED"}),
                           FakeResp(500, {"error": {"code": 2, "message": "down"}})],
               publish=[ConnectionError("reset")])
    pub, session, store = make(r, tmp_path)
    res = pub.publish("vid01", video, CAPTION)
    assert res.status == UNKNOWN and store.get("vid01", "instagram", IG).status == UNKNOWN
    # later: container still FINISHED (publish never happened) -> publish the same container
    session.routes[status_route()] = [FakeResp(200, {"status_code": "FINISHED"})]
    session.routes[("POST", f"{IG}/media_publish", None)] = [FakeResp(200, {"id": "MEDIA1"})]
    res = pub.publish("vid01", video, CAPTION)
    assert res.ok and res.reused
    assert len(creates(session)) == 1 and len(calls(session, "ig-api-upload")) == 1


def test_processing_timeout_then_recheck(tmp_path, video):
    pub, session, _ = make(routes([FakeResp(200, {"status_code": "IN_PROGRESS"})]), tmp_path,
                           poll_timeout_s=0)
    assert pub.publish("vid01", video, CAPTION).status == "processing"
    session.routes[status_route()] = [FakeResp(200, {"status_code": "FINISHED"})]
    res = pub.publish("vid01", video, CAPTION)
    assert res.ok and len(calls(session, "ig-api-upload")) == 1


def test_container_error_marks_failed_and_restart_allowed(tmp_path, video):
    pub, session, store = make(routes([FakeResp(200, {"status_code": "ERROR",
                                                      "status": "Error: unsupported codec"})]),
                               tmp_path)
    res = pub.publish("vid01", video, CAPTION)
    assert res.status == FAILED and "unsupported codec" in res.message
    session.routes[status_route()] = [FakeResp(200, {"status_code": "FINISHED"})]
    res = pub.publish("vid01", video, CAPTION)
    assert res.ok and store.get("vid01", "instagram", IG).attempts == 2
    assert len(creates(session)) == 2  # failed container replaced by a new one


def test_quota_reached_blocks_before_upload(tmp_path, video):
    r = routes()
    r[("GET", f"{IG}/content_publishing_limit", None)] = [
        FakeResp(200, {"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]})]
    pub, session, _ = make(r, tmp_path)
    with pytest.raises(MetaRateLimitError):
        pub.publish("vid01", video, CAPTION)
    assert not creates(session)


def test_expired_token_reported(tmp_path, video):
    r = routes()
    r[("POST", f"{IG}/media", is_(media_type="REELS"))] = [
        FakeResp(400, {"error": {"code": 190, "message": "Error validating access token"}})]
    pub, _, store = make(r, tmp_path)
    with pytest.raises(MetaTokenExpiredError):
        pub.publish("vid01", video, CAPTION)
    assert store.get("vid01", "instagram", IG).status == FAILED


def test_validate_reel(tmp_path, video):
    with pytest.raises(MetaUnsupportedContentError, match="MP4 or MOV"):
        validate_reel(tmp_path / "a.avi", "c")
    with pytest.raises(MetaUnsupportedContentError, match="2200"):
        validate_reel(video, "x" * 2201)
    with pytest.raises(MetaUnsupportedContentError, match="30 hashtags"):
        validate_reel(video, " ".join(f"#t{i}" for i in range(31)))
    with pytest.raises(MetaUnsupportedContentError, match="15 min"):
        validate_reel(video, "c", VideoMeta(duration_s=1))
    validate_reel(video, CAPTION, VideoMeta(duration_s=30))


def test_routing_uses_instagram_caption_and_hashtags(tmp_path, video):
    pub, session, _ = make(routes(), tmp_path)
    rec = VideoRecord("vid01", "stylepick_01.mp4", content={
        Platform.INSTAGRAM: PlatformContent(caption="IG caption 💖", hashtags="#a #b"),
        Platform.FACEBOOK: PlatformContent(description="FB")})
    router = RoutingPublisher(None, lambda r: (video, None), instagram=pub)
    res = run_batch([(rec, Platform.INSTAGRAM), (rec, Platform.FACEBOOK)], router, [])
    assert res["published"] == 2  # IG live, FB mock
    assert creates(session)[0][3]["caption"] == "IG caption 💖\n\n#a #b"
    assert rec.platform_status[Platform.INSTAGRAM] == Status.PUBLISHED


# ---------------- account eligibility & permissions
def test_instagram_accounts_and_permissions():
    r = {
        ("GET", "/P1", None): [FakeResp(200, {"id": "P1", "instagram_business_account":
                                              {"id": IG, "username": "stylepickfinds"}})],
        ("GET", "/P2", None): [FakeResp(200, {"id": "P2"})],
        ("GET", "debug_token", None): [FakeResp(200, {"data": {
            "is_valid": True, "scopes": ["pages_show_list", "pages_read_engagement",
                                         "instagram_basic"]}})],
    }
    store = MemStore()
    auth = MetaAuthManager("APP", "SECRET", GraphClient("v23.0", session=FakeSession(r),
                                                          sleep=lambda s: None), store=store)
    store.save({"user_token": "U"})
    pages = [FacebookPage("P1", "StylePick", "PT1", ["CREATE_CONTENT"]),
             FacebookPage("P2", "No IG", "PT2", ["CREATE_CONTENT"])]
    accounts, notes = auth.list_instagram_accounts(pages)
    assert [a.username for a in accounts] == ["stylepickfinds"] and accounts[0].page_id == "P1"
    assert any("Personal accounts are not supported" in n for n in notes)
    auth.select_instagram(accounts[0])
    with pytest.raises(MetaPermissionError, match="instagram_content_publish"):
        auth.instagram_for_publishing()


# ---------------- authorized live test (opt-in, creates a public post)
LIVE = all(os.getenv(k) for k in ("META_TEST_IG_USER_ID", "META_TEST_IG_TOKEN", "META_TEST_VIDEO",
                                  "META_TEST_IG_PUBLISH"))


@pytest.mark.skipif(not LIVE, reason="Set META_TEST_IG_* and META_TEST_IG_PUBLISH=1 to run")
def test_live_instagram_reel(tmp_path):
    graph = GraphClient(os.getenv("META_GRAPH_VERSION", "v23.0"))
    pub = InstagramReelsPublisher(graph, os.environ["META_TEST_IG_USER_ID"],
                                  os.environ["META_TEST_IG_TOKEN"], JobStore(tmp_path / "j.db"),
                                  poll_timeout_s=300)
    res = pub.publish("integration-test", Path(os.environ["META_TEST_VIDEO"]),
                      "StylePick integration test ✨ #test")
    assert res.status in ("published", "processing")

