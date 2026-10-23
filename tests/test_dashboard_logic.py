from src.content_parser import parse_gemini_text, parse_json
from src.mock_data import SAMPLE_GEMINI_TEXT, load_sample_records
from src.models import Platform, Status
from src.validation import revalidate, validate_drive_url, validate_gemini_url, validate_sources


def _records():
    recs = load_sample_records()
    for r in recs:
        revalidate(r)
    return recs


def test_parser_preserves_hindi_emoji_linebreaks():
    data = parse_gemini_text(SAMPLE_GEMINI_TEXT)
    assert len(data) == 10
    ig = data["reel_01.mp4"][Platform.INSTAGRAM]
    assert "फ्लोरल कुर्ती" in ig.caption and "💖" in ig.caption and "\n" in ig.caption
    assert ig.hashtags.startswith("#DailyFinds")


def test_parse_json():
    d = parse_json('{"a.mp4": {"Instagram": {"caption": "हैलो ✨", "hashtags": "#x"}}}')
    assert d["a.mp4"][Platform.INSTAGRAM].caption == "हैलो ✨"


def test_url_validation():
    assert validate_drive_url("") and validate_drive_url("https://example.com")
    assert validate_drive_url("https://drive.google.com/drive/folders/1AbCdEfGhIjKl") is None
    assert validate_gemini_url("https://gemini.google.com/share/abc123") is None
    assert validate_gemini_url("http://bad")
    assert validate_sources("https://drive.google.com/drive/folders/1AbCdEfGhIjKl", "", False, False)


def test_sample_records_validation():
    recs = _records()
    assert len(recs) == 10
    by = {r.filename: r for r in recs}
    assert any("No matching" in e for e in by["reel_extra_clip.mp4"].errors)
    assert any("Missing Facebook" in e for e in by["reel_07.mp4"].errors)
    assert by["reel_07.mp4"].overall_status == Status.NEEDS_REVIEW
    assert sum(r.is_valid for r in recs) == 8


def test_edit_fixes_validation():
    rec = next(r for r in _records() if r.filename == "reel_07.mp4")
    rec.get_content(Platform.FACEBOOK).caption = "नया कैप्शन 🎉"
    revalidate(rec)
    assert rec.is_valid and rec.overall_status == Status.READY

