"""Bulk publishing coordinator tests (no network)."""

from src.meta_errors import MetaNetworkError, MetaPermissionError, MetaRateLimitError
from src.mock_data import load_sample_records
from src.models import Platform, Status
from src.publishing_coordinator import (
    CANCELLED, DEFERRED, FAILED, PUBLISHED, PublishingCoordinator, summarize,
)

FB, IG = Platform.FACEBOOK, Platform.INSTAGRAM


class ScriptedPublisher:
    """Returns scripted outcomes per (video_id, platform); default = confirmed publish."""

    def __init__(self, script=None, live=None):
        self.script, self.calls, self.last_retryable = script or {}, [], True
        self.live = live or {}

    def publish(self, rec, platform):
        self.calls.append((rec.video_id, platform))
        out = self.script.get((rec.video_id, platform), (True, "confirmed"))
        if isinstance(out, list):
            out = out.pop(0) if len(out) > 1 else out[0]
        if isinstance(out, Exception):
            self.last_retryable = getattr(out, "retryable", True)
            return False, str(out)
        self.last_retryable = True
        return out


def records():
    return load_sample_records()  # 10 videos, 8 valid (vid07 + vid10 invalid)


def test_plan_all_selected_and_platform_choice():
    recs = records()
    assert len(PublishingCoordinator.plan(recs, [FB, IG])) == 16
    assert {j.platform for j in PublishingCoordinator.plan(recs, [IG])} == {IG}
    jobs = PublishingCoordinator.plan(recs, [FB, IG], ["vid01", "vid02"])
    assert {(j.video_id, j.platform) for j in jobs} == {
        ("vid01", FB), ("vid01", IG), ("vid02", FB), ("vid02", IG)}
    assert not PublishingCoordinator.plan(recs, [FB], ["vid10"])  # invalid video excluded


def test_independent_tracking_and_partial_success():
    recs = records()
    pub = ScriptedPublisher({("vid01", IG): MetaPermissionError("no permission")})
    jobs = PublishingCoordinator.plan(recs, [FB, IG], ["vid01", "vid02"])
    s = PublishingCoordinator(pub).run(jobs, recs)
    assert (s.published, s.failed, s.total) == (3, 1, 4) and s.partial
    v1 = next(r for r in recs if r.video_id == "vid01")
    assert v1.platform_status[FB] == Status.PUBLISHED  # FB success kept despite IG failure
    assert v1.platform_status[IG] == Status.FAILED


def test_never_published_without_confirmation():
    recs = records()
    pub = ScriptedPublisher({("vid01", FB): (None, "still processing")})
    jobs = PublishingCoordinator.plan(recs, [FB], ["vid01"])
    s = PublishingCoordinator(pub).run(jobs, recs)
    assert s.processing == 1 and s.published == 0
    assert recs[0].platform_status[FB] == Status.PROCESSING


def test_cancel_only_unstarted_jobs():
    recs = records()
    jobs = PublishingCoordinator.plan(recs, [FB], ["vid01", "vid02", "vid03"])
    assert PublishingCoordinator.cancel(jobs, {jobs[1].id}) == 1
    pub = ScriptedPublisher()
    s = PublishingCoordinator(pub).run(jobs, recs)
    assert s.cancelled == 1 and s.published == 2
    assert ("vid02", FB) not in pub.calls
    assert recs[1].platform_status[FB] == Status.READY  # untouched
    assert PublishingCoordinator.cancel(jobs, {jobs[0].id}) == 0  # finished job can't be cancelled


def test_cancel_event_stops_remaining_jobs():
    recs = records()
    jobs = PublishingCoordinator.plan(recs, [FB], ["vid01", "vid02", "vid03"])
    coord = PublishingCoordinator(ScriptedPublisher())
    coord.run(jobs, recs, on_update=lambda j, s: coord.cancel_event.set()
              if j.status == PUBLISHED else None)
    assert [j.status for j in jobs] == [PUBLISHED, "queued", "queued"]


def test_rate_limit_defers_platform_but_other_continues():
    recs = records()
    pub = ScriptedPublisher({("vid01", IG): MetaRateLimitError("Instagram publishing limit reached")})
    jobs = PublishingCoordinator.plan(recs, [FB, IG], ["vid01", "vid02", "vid03"])
    s = PublishingCoordinator(pub).run(jobs, recs)
    assert s.published == 3 and s.failed == 1 and s.deferred == 2
    assert all(j.status == DEFERRED for j in jobs if j.platform == IG and j.video_id != "vid01")
    assert not [c for c in pub.calls if c[1] == IG and c[0] != "vid01"]
    # deferred jobs are picked up again by the next plan
    assert {j.video_id for j in PublishingCoordinator.plan(recs, [IG], ["vid02", "vid03"])} == {
        "vid02", "vid03"}


def test_throttling_only_for_live_platforms():
    recs = records()
    slept, now = [], [0.0]
    pub = ScriptedPublisher(live={FB: object()})
    coord = PublishingCoordinator(pub, min_interval_s={FB: 5, IG: 5},
                                  sleep=lambda s: slept.append(s), clock=lambda: now[0])
    coord.run(PublishingCoordinator.plan(recs, [FB, IG], ["vid01", "vid02", "vid03"]), recs)
    assert slept == [5, 5]  # FB jobs 2 and 3 waited; simulated IG never throttled


def test_retry_only_retryable_failures_and_processing():
    recs = records()
    pub = ScriptedPublisher({
        ("vid01", FB): [MetaNetworkError("network down"), (True, "ok")],
        ("vid02", FB): MetaPermissionError("missing pages_manage_posts"),
        ("vid03", FB): (None, "processing"),
    })
    coord = PublishingCoordinator(pub, min_interval_s={})
    jobs = PublishingCoordinator.plan(recs, [FB], ["vid01", "vid02", "vid03", "vid04"])
    coord.run(jobs, recs)
    retryable = {(j.video_id, j.platform.value): j.retryable for j in jobs}
    assert retryable[("vid02", "Facebook")] is False
    retry = PublishingCoordinator.plan_retry(recs, retryable)
    assert {j.video_id for j in retry} == {"vid01", "vid03"}  # vid02 needs fixing, vid04 published
    pub.script[("vid03", FB)] = (True, "confirmed")
    s = coord.run(retry, recs)
    assert s.published == 2
    assert PublishingCoordinator.plan(recs, [FB], ["vid01", "vid03", "vid04"]) == []  # no duplicates


def test_publisher_exception_isolated():
    recs = records()

    class Boom(ScriptedPublisher):
        def publish(self, rec, platform):
            if rec.video_id == "vid01":
                raise RuntimeError("crash")
            return super().publish(rec, platform)

    jobs = PublishingCoordinator.plan(recs, [FB], ["vid01", "vid02"])
    s = PublishingCoordinator(Boom()).run(jobs, recs)
    assert (s.failed, s.published) == (1, 1)


def test_summary_counts():
    recs = records()
    jobs = PublishingCoordinator.plan(recs, [FB], ["vid01", "vid02"])
    jobs[0].status, jobs[1].status = FAILED, CANCELLED
    s = summarize(jobs)
    assert (s.failed, s.cancelled, s.done, s.total) == (1, 1, 2, 2)
