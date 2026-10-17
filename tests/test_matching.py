"""Matching tests: deterministic filename/ID matching, ambiguity, duplicates, manual mapping."""

from src.content_parser import parse_gemini_text
from src.matching import build_records, confirm_token, match_videos
from src.mock_data import SAMPLE_GEMINI_TEXT, mock_list_videos
from src.models import MatchStatus, Platform, PlatformContent, Status
from src.publishing_service import build_jobs


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
    assert by["vid01"].content_key == "stylepick_01.mp4" and by["vid01"].method == "Filename"
    assert by["vid10"].status == MatchStatus.UNMATCHED
    assert r1.unused_content == ["stylepick_10.mp4"]


def test_extension_and_case_and_file_id():
    r = match_videos([_v("a", "Clip_01.MOV"), _v("XYZ123", "random.mp4")],
                     ["clip_01.mp4", "XYZ123"]).by_video()
    assert r["a"].status == MatchStatus.MATCHED and r["a"].content_key == "clip_01.mp4"
    assert r["XYZ123"].content_key == "XYZ123" and r["XYZ123"].method == "Drive file ID"


def test_similar_and_numbered_need_confirmation():
    r = match_videos([_v("a", "StylePick 01.mp4"), _v("b", "reel_7.mp4")],
                     ["stylepick_01.mp4", "Video 7"]).by_video()
    assert r["a"].status == MatchStatus.NEEDS_CONFIRMATION and r["a"].content_key == "stylepick_01.mp4"
    assert r["b"].status == MatchStatus.NEEDS_CONFIRMATION and r["b"].content_key == "Video 7"
    r = match_videos([_v("a", "StylePick 01.mp4")], ["stylepick_01.mp4"],
                     confirmed={confirm_token("a", "stylepick_01.mp4")}).by_video()
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


def test_missing_caption_invalid_and_excluded_from_jobs():
    content = _content("a.mp4")
    content["a.mp4"][Platform.FACEBOOK] = PlatformContent()
    recs = build_records([_v("a", "a.mp4")], content)
    assert "Missing Facebook caption." in recs[0].errors
    assert build_jobs(recs) == []


def test_text_preserved_and_source_not_mutated():
    content = parse_gemini_text(SAMPLE_GEMINI_TEXT)
    original = content["stylepick_01.mp4"][Platform.INSTAGRAM].caption
    recs = build_records(mock_list_videos(""), content)
    assert recs[0].content[Platform.INSTAGRAM].caption == original  # verbatim
    recs[0].content[Platform.INSTAGRAM].caption = "edited"
    assert content["stylepick_01.mp4"][Platform.INSTAGRAM].caption == original


def test_edits_kept_until_mapping_changes():
    videos = [_v("a", "a.mp4")]
    content = _content("a.mp4", "b.mp4")
    recs = build_records(videos, content)
    recs[0].content[Platform.INSTAGRAM].caption = "my edit"
    recs = build_records(videos, content, existing=recs)
    assert recs[0].content[Platform.INSTAGRAM].caption == "my edit"
    recs = build_records(videos, content, overrides={"a": "b.mp4"}, existing=recs)
    assert recs[0].content[Platform.INSTAGRAM].caption == "IG b.mp4 ✨"


def test_valid_records_flow_to_publishing():
    recs = build_records(mock_list_videos(""), parse_gemini_text(SAMPLE_GEMINI_TEXT))
    jobs = build_jobs(recs)
    assert {r.content_key for r, _ in jobs} == {f"stylepick_{i:02d}.mp4" for i in (1, 2, 3, 4, 5, 6, 8, 9)}

