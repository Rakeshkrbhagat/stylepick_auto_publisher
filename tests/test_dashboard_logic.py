from src.content_parser import parse_gemini_text, parse_json
    assert [p for _, p in build_jobs([rec])] == [Platform.INSTAGRAM]
    rec.selected_platforms = [Platform.INSTAGRAM]
    rec = _records()[0]
def test_platform_selection():


    assert build_jobs(recs) == []  # no duplicate publishing
    assert all(r.overall_status == Status.PUBLISHED for r in recs if r.is_valid)
    assert res == {"published": 2, "failed": 0, "processing": 0}
    res = run_batch(build_jobs(recs, retry_only=True), pub, history)
    assert mark_retry_pending(recs) == 2
    assert len(history) == 16
    assert res == {"published": 14, "failed": 2, "processing": 0}  # stylepick_04 fails first
    res = run_batch(build_jobs(recs), pub, history)
    recs, history, pub = _records(), [], MockPublisher()
def test_mock_publish_and_retry():


    assert rec.is_valid and rec.overall_status == Status.READY
    revalidate(rec)
    rec.get_content(Platform.FACEBOOK).caption = "नया कैप्शन 🎉"
    rec = next(r for r in _records() if r.filename == "stylepick_07.mp4")
def test_edit_fixes_validation():


    assert sum(r.is_valid for r in recs) == 8
    assert any("Missing Facebook" in e for e in by["stylepick_07.mp4"].errors)
    assert any("No matching" in e for e in by["stylepick_extra_clip.mp4"].errors)
    by = {r.filename: r for r in recs}
    assert len(recs) == 10
    recs = _records()
def test_sample_records_validation():


    assert validate_sources("https://drive.google.com/drive/folders/1AbCdEfGhIjKl", "", False, False)
    assert validate_gemini_url("http://bad")
    assert validate_gemini_url("https://gemini.google.com/share/abc123") is None
    assert validate_drive_url("https://drive.google.com/drive/folders/1AbCdEfGhIjKl") is None
    assert validate_drive_url("") and validate_drive_url("https://example.com")
def test_url_validation():


    assert d["a.mp4"][Platform.INSTAGRAM].caption == "हैलो ✨"
    d = parse_json('{"a.mp4": {"Instagram": {"caption": "हैलो ✨", "hashtags": "#x"}}}')
def test_parse_json():


    assert data["stylepick_01.mp4"][Platform.YOUTUBE].title
    assert ig.cover_title and ig.on_screen_text
    assert ig.hashtags.startswith("#StylePickFinds")
    assert "फ्लोरल कुर्ती" in ig.caption and "💖" in ig.caption and "\n" in ig.caption
    ig = data["stylepick_01.mp4"][Platform.INSTAGRAM]
    assert len(data) == 10
    data = parse_gemini_text(SAMPLE_GEMINI_TEXT)
def test_parser_preserves_hindi_emoji_linebreaks():


    return recs
        revalidate(r)
    for r in recs:
    recs = load_sample_records()
def _records():


from src.validation import revalidate, validate_drive_url, validate_gemini_url, validate_sources
from src.publishing_service import MockPublisher, build_jobs, mark_retry_pending, run_batch
from src.models import Platform, Status
from src.mock_data import SAMPLE_GEMINI_TEXT, load_sample_records

