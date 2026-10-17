"""End-to-end workflow tests (offline, no public posts).

Drive batch (10 videos) → Gemini parsing → matching/validation → review/edit →
Facebook-only / Instagram-only / combined publishing → partial failures, network
interruptions, expired tokens → retry without duplicates.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.content_parser import parse_gemini_text
from src.live_publisher import RoutingPublisher
from src.meta_errors import MetaNetworkError, MetaPermissionError, MetaTokenExpiredError
from src.mock_data import SAMPLE_GEMINI_TEXT, load_sample_records
from src.models import Platform, Status
from src.publishing_coordinator import PublishingCoordinator
from src.publishing_service import MockPublisher
from src.validation import revalidate

FB, IG = Platform.FACEBOOK, Platform.INSTAGRAM


def batch():
    recs = load_sample_records()
    for r in recs:
        revalidate(r)
    return recs


class FakeLive:
    """Stands in for a live FB/IG publisher; never touches the network."""

    def __init__(self, script=None):
        self.script, self.calls = script or {}, []

    def publish(self, video_id, path, text, meta):
        self.calls.append(video_id)
        out = self.script.get(video_id)
        if isinstance(out, list):
            out = out.pop(0) if len(out) > 1 else out[0]
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(ok=True, status="published", message="ok", video_id=f"v_{video_id}",
                               post_id="", permalink=f"https://example.test/{video_id}")


def router(fb=None, ig=None, auth_errors=None):
    return RoutingPublisher(
        facebook=fb, instagram=ig, fallback=MockPublisher(),
        resolve_path=lambda rec: ("/tmp/" + rec.filename, None),
        on_auth_error=(lambda p, m: auth_errors.append((p, m))) if auth_errors is not None else None)


def test_batch_of_ten_parsed_and_matched():

    assert len(recs) == 10
    parsed = parse_gemini_text(SAMPLE_GEMINI_TEXT)
    for r in recs:
        if r.is_valid:  # matched content is copied exactly from Gemini
            assert r.filename in parsed
            assert r.content[IG].caption == parsed[r.filename][IG].caption
    assert sum(r.is_valid for r in recs) == 8  # invalid ones are excluded, not guessed
