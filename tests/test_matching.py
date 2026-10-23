"""Matching tests: deterministic filename/ID matching, ambiguity, duplicates, manual mapping."""

import pytest

from src.content_parser import parse_gemini_text
from src.matching import build_records, confirm_token, match_videos, trailing_number
from src.mock_data import SAMPLE_GEMINI_TEXT, mock_list_videos
from src.models import MatchStatus, Platform, PlatformContent, Status


def _content(*keys):
    return {k: {Platform.INSTAGRAM: PlatformContent(caption=f"IG {k} ✨"),
                Platform.FACEBOOK: PlatformContent(description=f"FB {k} 😍")} for k in keys}


def _v(i, name):
    return {"id": i, "name": name}


def test_sample_matching_deterministic():
    videos, content = mock_list_videos(""), parse_gemini_text(SAMPLE_GEMINI_TEXT)
    r1 = match_videos(videos, list(content))
    r2 = match_videos(list(reversed(videos)), list(reversed(list(content))))
    assert {m.video_id: m.content_key for m in r1.matches} == {m.video_id: m.content_key for m in r2.matches}
    by = r1.by_video()
    assert by["vid01"].content_key == "reel_01.mp4" and by["vid01"].method == "Filename"
    assert by["vid10"].status == MatchStatus.UNMATCHED
    assert r1.unused_content == ["reel_10.mp4"]


def test_extension_and_case_and_file_id():
    r = match_videos([_v("a", "Clip_01.MOV"), _v("XYZ123", "random.mp4")],
                     ["clip_01.mp4", "XYZ123"]).by_video()
    assert r["a"].status == MatchStatus.MATCHED and r["a"].content_key == "clip_01.mp4"
    assert r["XYZ123"].content_key == "XYZ123" and r["XYZ123"].method == "Drive file ID"


def test_similar_and_numbered_need_confirmation():
    r = match_videos([_v("a", "Reel 01.mp4"), _v("b", "reel_7.mp4")],
                     ["reel_01.mp4", "Video 7"]).by_video()
    assert r["a"].status == MatchStatus.NEEDS_CONFIRMATION and r["a"].content_key == "reel_01.mp4"
    assert r["b"].status == MatchStatus.MATCHED and r["b"].content_key == "Video 7"
    assert r["b"].method.startswith("Trailing number 7")
    r = match_videos([_v("a", "Reel 01.mp4")], ["reel_01.mp4"],
                     confirmed={confirm_token("a", "reel_01.mp4")}).by_video()
    assert r["a"].status == MatchStatus.MATCHED


def test_ambiguous_candidates_not_guessed():
    r = match_videos([_v("a", "clip.mp4")], ["clip.mov", "CLIP.webm"]).by_video()
    assert r["a"].status == MatchStatus.NEEDS_CONFIRMATION and r["a"].content_key is None
    assert len(r["a"].candidates) == 2


def test_duplicate_filenames_and_duplicate_claims():
    videos = [_v("a", "x.mp4"), _v("b", "x.mp4")]
    rep = match_videos(videos, ["x.mp4"])
    assert rep.duplicate_filenames == ["x.mp4"]
    assert all(m.status == MatchStatus.NEEDS_CONFIRMATION and m.duplicate_with for m in rep.matches)
    recs = build_records(videos, _content("x.mp4"))
    assert all(not r.is_valid for r in recs)
    assert any("multiple videos" in e for e in recs[0].errors)
    # skipping one resolves the duplicate; the other still needs confirmation
    recs = build_records(videos, _content("x.mp4"), skipped={"b"},
                         confirmed={confirm_token("a", "x.mp4")})
    assert recs[0].is_valid and recs[1].skipped and recs[1].overall_status == Status.SKIPPED


def test_manual_override_and_no_content():
    videos = [_v("a", "unknown.mp4"), _v("b", "b.mp4")]
    recs = build_records(videos, _content("b.mp4", "other.mp4"),
                         overrides={"a": "other.mp4", "b": ""})
    assert recs[0].is_valid and recs[0].match_method == "Manual"
    assert recs[0].content[Platform.INSTAGRAM].caption == "IG other.mp4 ✨"
    assert recs[1].match_status == MatchStatus.UNMATCHED and not recs[1].is_valid


def test_missing_caption_needs_review():
    content = _content("a.mp4")
    content["a.mp4"][Platform.FACEBOOK] = PlatformContent()
    recs = build_records([_v("a", "a.mp4")], content)
    assert "Missing Facebook caption." in recs[0].errors
    assert not recs[0].is_valid and recs[0].overall_status == Status.NEEDS_REVIEW


def test_text_preserved_and_source_not_mutated():
    content = parse_gemini_text(SAMPLE_GEMINI_TEXT)
    original = content["reel_01.mp4"][Platform.INSTAGRAM].caption
    recs = build_records(mock_list_videos(""), content)
    assert recs[0].content[Platform.INSTAGRAM].caption == original  # verbatim
    recs[0].content[Platform.INSTAGRAM].caption = "edited"
    assert content["reel_01.mp4"][Platform.INSTAGRAM].caption == original


def test_edits_kept_until_mapping_changes():
    videos = [_v("a", "a.mp4")]
    content = _content("a.mp4", "b.mp4")
    recs = build_records(videos, content)
    recs[0].content[Platform.INSTAGRAM].caption = "my edit"
    recs = build_records(videos, content, existing=recs)
    assert recs[0].content[Platform.INSTAGRAM].caption == "my edit"
    recs = build_records(videos, content, overrides={"a": "b.mp4"}, existing=recs)
    assert recs[0].content[Platform.INSTAGRAM].caption == "IG b.mp4 ✨"


def test_valid_records_ready_for_review_queue():
    recs = build_records(mock_list_videos(""), parse_gemini_text(SAMPLE_GEMINI_TEXT))
    assert {r.content_key for r in recs if r.is_valid} == {f"reel_{i:02d}.mp4" for i in (1, 2, 3, 4, 5, 6, 8, 9)}


# ---------------- trailing-number matching (Gemini responses numbered 1..N)
DRIVE_FILES = ["R-YT-1.mp4", "R-YT-2.mp4", "R-YT-10.mp4", "M-New-3.mp4", "M-New-4.MOV",
               "R-NEW-5.mp4"]
RESPONSES = [f"Video {n}" for n in (1, 2, 3, 4, 5, 10)]


def _videos(names):
    return [_v(f"id_{i}", n) for i, n in enumerate(names)]


@pytest.mark.parametrize("name, n", [
    ("R-YT-1.mp4", 1), ("M-New-2.mp4", 2), ("R-YT-10.mp4", 10), ("M-New-10.mov", 10),
    ("R-NEW-01.mp4", 1), ("clip 12 .mp4", 12), ("no-number.mp4", None), ("v2-final.mp4", None),
])
def test_trailing_number_extraction(name, n):
    assert trailing_number(name) == n


def test_number_n_maps_to_response_n():
    rep = match_videos(_videos(DRIVE_FILES), RESPONSES)
    got = {m.filename: m.content_key for m in rep.matches}
    assert got["R-YT-1.mp4"] == "Video 1"
    assert got["R-YT-2.mp4"] == "Video 2"
    assert got["R-YT-10.mp4"] == "Video 10"  # not response 1
    assert all(m.status == MatchStatus.MATCHED for m in rep.matches)
    assert not rep.duplicate_numbers and not rep.missing_responses and not rep.unmatched_responses


def test_m_new_2_maps_to_response_2():
    m = match_videos([_v("x", "M-New-2.mp4")], ["Video 1", "Video 2", "Video 12"]).matches[0]
    assert m.content_key == "Video 2" and m.status == MatchStatus.MATCHED
    assert (m.video_number, m.response_number) == (2, 2)


def test_independent_of_drive_listing_order():
    vids = _videos(DRIVE_FILES)
    a = {m.filename: m.content_key for m in match_videos(vids, RESPONSES).matches}
    for order in (list(reversed(vids)), sorted(vids, key=lambda v: v["name"]),
                  vids[3:] + vids[:3]):
        b = {m.filename: m.content_key
             for m in match_videos(order, list(reversed(RESPONSES))).matches}
        assert a == b


def test_missing_and_unmatched_numbers_reported():
    rep = match_videos(_videos(["R-YT-1.mp4", "R-YT-7.mp4", "intro.mp4"]),
                       ["Video 1", "Video 2"])
    by = {m.filename: m for m in rep.matches}
    assert by["R-YT-7.mp4"].status == MatchStatus.UNMATCHED
    assert "response number 7" in by["R-YT-7.mp4"].notes[0]
    assert by["intro.mp4"].status == MatchStatus.UNMATCHED
    assert rep.missing_responses == {7: ["R-YT-7.mp4"]}
    assert rep.unmatched_responses == [2]
    assert rep.unnumbered_videos == ["intro.mp4"]


def test_duplicate_numbers_flagged_then_user_selects_file():
    vids = [_v("a", "R-YT-1.mp4"), _v("b", "M-New-1.mp4"), _v("c", "R-YT-2.mp4")]
    rep = match_videos(vids, ["Video 1", "Video 2"])
    by = rep.by_video()
    assert rep.duplicate_numbers == {1: ["M-New-1.mp4", "R-YT-1.mp4"]}
    assert by["a"].status == by["b"].status == MatchStatus.NEEDS_CONFIRMATION
    assert by["a"].duplicate_with and by["c"].status == MatchStatus.MATCHED
    recs = build_records(vids, _content("Video 1", "Video 2"))
    assert not recs[0].is_valid and not recs[1].is_valid and recs[2].is_valid
    # user confirms M-New-1 for response 1 -> R-YT-1 released, nothing guessed
    by = match_videos(vids, ["Video 1", "Video 2"],
                      confirmed={confirm_token("b", "Video 1")}).by_video()
    assert by["b"].status == MatchStatus.MATCHED and by["b"].content_key == "Video 1"
    assert by["a"].status == MatchStatus.UNMATCHED and by["a"].content_key is None
    assert not by["b"].duplicate_with
    # or an explicit mapping
    by = match_videos(vids, ["Video 1", "Video 2"], overrides={"a": "Video 1", "b": ""}).by_video()
    assert by["a"].content_key == "Video 1" and by["a"].method == "Manual"


def test_numbered_gemini_responses_end_to_end():
    text = "\n".join(
        f"## Response {n}\n### Instagram Reels\nCaption: IG {n} ✨\nHashtags: #a\n"
        f"### Facebook Reels\nDescription: FB {n}\nHashtags: #b\n" for n in (10, 2, 1))
    content = parse_gemini_text(text)
    assert set(content) == {"Video 1", "Video 2", "Video 10"}
    recs = build_records(_videos(["R-YT-10.mp4", "M-New-2.mp4", "R-NEW-1.mp4"]), content)
    caps = {r.filename: r.content[Platform.INSTAGRAM].caption for r in recs}
    assert caps == {"R-YT-10.mp4": "IG 10 ✨", "M-New-2.mp4": "IG 2 ✨", "R-NEW-1.mp4": "IG 1 ✨"}
    assert all(r.match_status == MatchStatus.MATCHED for r in recs)
