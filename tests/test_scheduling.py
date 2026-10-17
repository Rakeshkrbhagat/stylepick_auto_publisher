import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import scheduling as s
from src.models import Platform


class ProposeTests(unittest.TestCase):
    def check(self, orig, expected):
        self.assertEqual(s.propose(orig), expected)

    def test_example(self):
        o = s.parse_original("9 September", "10:30 AM", default_year=2026)
        self.check(o, datetime(2026, 9, 10, 10, 30))

    def test_boundaries(self):
        self.check(datetime(2026, 1, 31, 8, 5), datetime(2026, 2, 1, 8, 5))
        self.check(datetime(2026, 12, 31, 23, 59), datetime(2027, 1, 1, 23, 59))
        self.check(datetime(2024, 2, 28, 10), datetime(2024, 2, 29, 10))
        self.check(datetime(2024, 2, 29, 10), datetime(2024, 3, 1, 10))
        self.check(datetime(2026, 2, 28, 10), datetime(2026, 3, 1, 10))

    def test_time_and_tz_preserved(self):
        tz = timezone(timedelta(hours=5, minutes=30))
        p = s.propose(datetime(2026, 9, 9, 10, 30, tzinfo=tz))
        self.assertEqual((p.hour, p.minute, p.tzinfo), (10, 30, tz))

    def test_parse_formats(self):
        self.assertEqual(s.parse_original("2026-09-09 22:15"), datetime(2026, 9, 9, 22, 15))
        with self.assertRaises(s.ScheduleParseError):
            s.parse_original("", "")


class Spy:
    def __init__(self):
        self.calls = 0

    def submit(self, p, path):
        self.calls += 1
        return s.ScheduleResult(s.SCHEDULED, "ok", "v1")


class SubmitTests(unittest.TestCase):
    def prop(self, platform):
        return s.build_proposal("v", "a.mp4", platform, "cap #tag", datetime(2026, 9, 9, 10, 30))

    def test_requires_approval_and_edit_resets(self):
        spy, p = Spy(), self.prop(Platform.FACEBOOK)
        s.submit_approved([p], {Platform.FACEBOOK: spy}, lambda _: Path("x"))
        self.assertEqual(spy.calls, 0)
        s.approve(p)
        s.edit_proposal(p, new_proposed=datetime(2026, 9, 11, 9, 0))
        self.assertEqual(p.status, s.PROPOSED)
        s.submit_approved([p], {Platform.FACEBOOK: spy}, lambda _: Path("x"))
        self.assertEqual(spy.calls, 0)

    def test_platforms_separate_and_instagram_unsupported(self):
        fb, ig, spy = self.prop(Platform.FACEBOOK), self.prop(Platform.INSTAGRAM), Spy()
        s.approve(fb), s.approve(ig)
        s.submit_approved([fb, ig], {Platform.FACEBOOK: spy,
                                     Platform.INSTAGRAM: s.instagram_scheduler()},
                          lambda _: Path("x"))
        self.assertEqual(fb.status, s.SCHEDULED)
        self.assertEqual(ig.status, s.UNSUPPORTED)
        self.assertIn("manually", ig.message)

    def test_facebook_never_published_and_window_enforced(self):
        class G:
            def __init__(self):
                self.calls = []

            def request(self, *a, **k):
                self.calls.append(k.get("data"))
                return {}
        g = G()
        sched = s.FacebookReelsScheduler(g, "pg", "tok", store=None,
                                         now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
        p = self.prop(Platform.FACEBOOK)
        p.proposed = p.proposed.replace(tzinfo=timezone.utc)
        res = sched.submit(p, Path("x"))
        self.assertEqual(res.status, s.UNSUPPORTED)
        self.assertEqual(g.calls, [])


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = s.AuditLog(Path(self.tmp.name) / "audit.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def prop(self):
        return s.build_proposal("v", "a.mp4", Platform.FACEBOOK, "cap", datetime(2026, 9, 9, 10, 30))

    def test_new_proposal_is_not_submittable(self):
        spy = Spy()
        s.submit_approved([self.prop()], {Platform.FACEBOOK: spy}, lambda _: Path("x"))
        self.assertEqual(spy.calls, 0)

    def test_skipped_never_submitted_even_if_approve_called(self):
        spy, p = Spy(), self.prop()
        s.skip(p, self.audit)
        s.approve(p, self.audit)
        s.submit_approved([p], {Platform.FACEBOOK: spy}, lambda _: Path("x"), self.audit)
        self.assertEqual((spy.calls, p.status), (0, s.SKIPPED))

    def test_retry_requires_reapproval(self):
        class Boom:
            def submit(self, p, path):
                raise RuntimeError("network")
        p = self.prop()
        s.approve(p)
        s.submit_approved([p], {Platform.FACEBOOK: Boom()}, lambda _: Path("x"), self.audit)
        self.assertEqual(p.status, s.FAILED)
        s.retry(p, self.audit)
        self.assertEqual(p.status, s.PROPOSED)
        spy = Spy()
        s.submit_approved([p], {Platform.FACEBOOK: spy}, lambda _: Path("x"))
        self.assertEqual(spy.calls, 0)
        s.approve(p, self.audit)
        s.submit_approved([p], {Platform.FACEBOOK: spy}, lambda _: Path("x"), self.audit)
        self.assertEqual(spy.calls, 1)

    def test_audit_records_approval_and_action(self):
        p = self.prop()
        s.approve(p, self.audit)
        s.submit_approved([p], {Platform.FACEBOOK: Spy()}, lambda _: Path("x"), self.audit)
        events = [e["event"] for e in self.audit.entries()]
        self.assertEqual(events, ["approved", "submit_result"])
        self.assertEqual(self.audit.entries()[-1]["status"], s.SCHEDULED)

    def test_review_row(self):
        from src.models import PlatformContent, VideoRecord
        rec = VideoRecord("v", "clip_7.mp4", content_key="7")
        rec.content[Platform.INSTAGRAM] = PlatformContent(caption="hi", hashtags="#a")
        row = s.review_row(rec, {"Instagram": None, "Facebook": self.prop()})
        self.assertEqual(row["Instagram caption"], "hi")
        self.assertEqual(row["Facebook status"], s.PROPOSED)
        self.assertIn("10 September 2026", row["Proposed"])


if __name__ == "__main__":
    unittest.main()

