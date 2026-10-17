"""Persistent history, restart recovery and duplicate-protection tests."""

import sqlite3

import pytest

from src.facebook_publisher import PublishResult
from src.history_store import (
    FAILED, PUBLISHED, SIMULATED, UNCERTAIN, HistoryStore, HistoryStoreError,
)
from src.mock_data import load_sample_records
from src.models import Platform, Status
from src.publishing_coordinator import PublishingCoordinator, restore_status
from src.publishing_service import MockPublisher
from tests.test_publishing_coordinator import ScriptedPublisher

FB, IG = Platform.FACEBOOK, Platform.INSTAGRAM


def store_at(tmp_path):
    return HistoryStore(tmp_path / "pub.db")


def test_every_attempt_recorded_with_caption_ids_and_errors(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()
    pub = ScriptedPublisher({("vid02", FB): MetaLikeError("Permission denied", retryable=False)})
    jobs = PublishingCoordinator.plan(recs, [FB, IG], ["vid01", "vid02"])
    PublishingCoordinator(pub, store=store).run(jobs, recs)
    rows = store.attempts()
    assert len(rows) == 4
    ok = next(a for a in rows if a.video_key == "vid01" and a.platform == "Facebook")
    assert ok.status == PUBLISHED and ok.post_id == "confirmed" and ok.mode == SIMULATED
    assert ok.filename == "stylepick_01.mp4" and ok.finished_at
    fb_text = recs[0].content[FB].publish_text()
    assert ok.caption == fb_text and "😍" in ok.caption  # caption used is stored verbatim
    bad = next(a for a in rows if a.video_key == "vid02" and a.platform == "Facebook")
    assert bad.status == FAILED and "Permission denied" in bad.error and not bad.retryable


class MetaLikeError(Exception):
    def __init__(self, msg, retryable=True):
        super().__init__(msg)
        self.retryable = retryable


def test_live_result_ids_and_url_stored(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()

    class LivePub(ScriptedPublisher):
        def publish(self, rec, platform):
            self.last_result = PublishResult("published", "VID9", "111_9", "https://fb/reel/9")
            return True, "Published"

    pub = LivePub()
    PublishingCoordinator(pub, store=store, min_interval_s={}).run(
        PublishingCoordinator.plan(recs, [FB], ["vid01"]), recs)
    a = store.attempts()[0]
    assert (a.remote_id, a.post_id, a.permalink) == ("VID9", "111_9", "https://fb/reel/9")


def test_filters(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()
    pub = ScriptedPublisher({("vid02", IG): MetaLikeError("boom")})
    PublishingCoordinator(pub, store=store).run(
        PublishingCoordinator.plan(recs, [FB, IG], ["vid01", "vid02"]), recs)
    assert {a.platform for a in store.attempts(platform="Instagram")} == {"Instagram"}
    assert [a.video_key for a in store.attempts(status="failed")] == ["vid02"]
    assert {a.video_key for a in store.attempts(text="stylepick_01")} == {"vid01"}
    assert store.attempts(mode="live") == []


def test_restart_preserves_states_and_marks_interrupted_uncertain(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()
    PublishingCoordinator(ScriptedPublisher({("vid02", FB): MetaLikeError("x")}), store=store).run(
        PublishingCoordinator.plan(recs, [FB], ["vid01", "vid02"]), recs)
    # simulate a crash mid-publish
    store.start_attempt("vid03", "stylepick_03.mp4", "Facebook", SIMULATED, SIMULATED, "cap")
    store.close()

    store2 = store_at(tmp_path)  # "restart"
    assert store2.recovered == 1
    fresh = load_sample_records()
    retryable = restore_status(fresh, store2)
    st = {r.video_id: r.platform_status[FB] for r in fresh}
    assert st["vid01"] == Status.PUBLISHED and st["vid02"] == Status.FAILED
    assert st["vid03"] == Status.PROCESSING  # uncertain -> verify before retry
    assert store2.state("vid03", "Facebook", SIMULATED).status == UNCERTAIN
    assert retryable[("vid02", "Facebook")] is True
    assert PublishingCoordinator.plan(fresh, [FB], ["vid01"]) == []  # no re-post after restart


def test_duplicate_warning_by_id_and_filename(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()
    PublishingCoordinator(ScriptedPublisher(), store=store).run(
        PublishingCoordinator.plan(recs, [FB], ["vid01"]), recs)
    fresh = load_sample_records()  # e.g. session reset without restore
    fresh[1].filename = "STYLEPICK_01.mp4"  # same file re-uploaded to Drive under a new ID
    jobs = PublishingCoordinator.plan(fresh, [FB], ["vid01", "vid02", "vid03"])
    assert PublishingCoordinator.mark_duplicates(jobs, store) == 2
    dup = [j for j in jobs if j.duplicate]
    assert {j.video_id for j in dup} == {"vid01", "vid02"}
    assert all(j.status == "cancelled" and "already published" in j.message for j in dup)
    assert PublishingCoordinator.allow_duplicates(jobs) == 2
    assert all(j.status == "queued" for j in jobs)


def test_facebook_retry_never_repeats_instagram_success(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()
    pub = ScriptedPublisher({("vid01", FB): [MetaLikeError("network"), (True, "ok")]})
    coord = PublishingCoordinator(pub, store=store)
    coord.run(PublishingCoordinator.plan(recs, [FB, IG], ["vid01"]), recs)
    retry = PublishingCoordinator.plan_retry(recs, {})
    assert [(j.video_id, j.platform) for j in retry] == [("vid01", FB)]
    coord.run(retry, recs)
    ig_calls = [c for c in pub.calls if c == ("vid01", IG)]
    assert len(ig_calls) == 1
    assert store.state("vid01", "Instagram", SIMULATED).status == PUBLISHED
    assert store.state("vid01", "Facebook", SIMULATED).status == PUBLISHED


def test_confirmed_publication_never_downgraded(tmp_path):
    store = store_at(tmp_path)
    a = store.start_attempt("v", "v.mp4", "Facebook", SIMULATED, SIMULATED, "c")
    store.finish_attempt(a, PUBLISHED, post_id="P1")
    b = store.start_attempt("v", "v.mp4", "Facebook", SIMULATED, SIMULATED, "c")
    store.finish_attempt(b, FAILED, error="late failure")
    s = store.state("v", "Facebook", SIMULATED)
    assert s.status == PUBLISHED and s.post_id == "P1"
    assert len(store.attempts()) == 2  # both attempts kept for audit


def test_db_error_blocks_publish_and_keeps_records(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()
    PublishingCoordinator(ScriptedPublisher(), store=store).run(
        PublishingCoordinator.plan(recs, [FB], ["vid01"]), recs)

    def broken(*a, **k):
        raise HistoryStoreError("disk I/O error")

    store.start_attempt = broken
    pub = ScriptedPublisher()
    jobs = PublishingCoordinator.plan(recs, [FB], ["vid02"])
    s = PublishingCoordinator(pub, store=store).run(jobs, recs)
    assert s.failed == 1 and pub.calls == []  # nothing published untracked
    assert "history could not be saved" in jobs[0].message
    assert store.state("vid01", "Facebook", SIMULATED).status == PUBLISHED


def test_failed_transaction_rolls_back(tmp_path):
    store = store_at(tmp_path)
    a = store.start_attempt("v", "v.mp4", "Facebook", SIMULATED, SIMULATED, "c")
    with pytest.raises(HistoryStoreError):
        store.finish_attempt(9999, PUBLISHED)  # unknown attempt -> error, nothing written
    assert store.state("v", "Facebook", SIMULATED) is None
    assert store.attempts()[0].id == a and store.attempts()[0].status == "started"


def test_corrupt_database_reported_not_overwritten(tmp_path):
    path = tmp_path / "pub.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(HistoryStoreError):
        HistoryStore(path)
    assert path.read_bytes().startswith(b"this is not")  # left untouched for recovery


def test_mock_publisher_post_id_recorded(tmp_path):
    store, recs = store_at(tmp_path), load_sample_records()
    PublishingCoordinator(MockPublisher(), store=store).run(
        PublishingCoordinator.plan(recs, [IG], ["vid01"]), recs)
    assert store.attempts()[0].post_id == "mock_post_vid01_instagram"
    assert sqlite3.sqlite_version

