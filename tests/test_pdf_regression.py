"""Regression tests: Gemini conversation PDFs (layout quirks + the supplied 'New Rituals 1.pdf')."""

from pathlib import Path

import pytest

from src.content_parser import parse_gemini_response
from src.models import Platform
from src.pdf_extract import normalize_pdf_text

FIXTURE = Path(__file__).parent / "fixtures" / "New Rituals 1.pdf"

# Text as PyMuPDF returns it for a printed Gemini chat: headings sharing a line with
# 'Response:' / 'Hook', page footers in the middle of a response, two responses.
PRINTED = """Gemini
You said
Generate content for video 1
Response: 🟥 YOUTUBE SHORTS 1. Hook
जो व्यक्ति आमले का वृक्षारोपण करता है
2. SEO Title
आमला वृक्ष लगाने के लाभ #shorts
🟣 INSTAGRAM REELS 1. Hook
जो व्यक्ति आमले का वृक्षारोपण करता है
5. Instagram Caption
क्या आप जानते हैं आमले का वृक्षारोपण ✨
सेव करें ताकि भूलें नहीं!
6. Hashtags
#amlatree
https://gemini.google.com/share/abc123
3/7
10/25/26, 3:41 PM Gemini
#sanatandharma
7. Instagram Search Phrases
amla tree spiritual benefits reels
🟦 FACEBOOK REELS 1. Hook
जो व्यक्ति
5. Facebook Reel Description
आंवले के वृक्षारोपण से जुड़ी यह अद्भुत बात 🙏
6. Hashtags #sanatandharma #amlatree
7. Facebook Search Phrases
amla tree lagane ke fayde facebook
You said
Generate content for video 2
Response: 🟥 YOUTUBE SHORTS
1. Hook
अगर लक्ष्मी जी को प्रसन्न करना है
🟣 INSTAGRAM REELS
Caption: पैसा चाहिए नवरात्रि में 💰 Hashtags: #navratri #mahadev
🟦 FACEBOOK REELS
Description: नवरात्रि के पवित्र दिनों में
Hashtags:
#navratri
#maalakshmi
"""


def _check(result):
    assert result.has_content, result.issues
    assert list(result.content) == ["Video 1", "Video 2"]
    v1, v2 = result.content["Video 1"], result.content["Video 2"]
    ig, fb = v1[Platform.INSTAGRAM], v1[Platform.FACEBOOK]
    assert ig.caption == "क्या आप जानते हैं आमले का वृक्षारोपण ✨\nसेव करें ताकि भूलें नहीं!"
    assert ig.hashtags == "#amlatree\n#sanatandharma"  # page footer removed, page joined
    assert fb.description == "आंवले के वृक्षारोपण से जुड़ी यह अद्भुत बात 🙏"
    assert fb.hashtags == "#sanatandharma #amlatree"
    assert "Search" not in ig.hashtags + fb.hashtags
    assert v2[Platform.INSTAGRAM].caption == "पैसा चाहिए नवरात्रि में 💰"
    assert v2[Platform.INSTAGRAM].hashtags == "#navratri #mahadev"
    assert v2[Platform.FACEBOOK].description == "नवरात्रि के पवित्र दिनों में"
    assert v2[Platform.FACEBOOK].hashtags == "#navratri\n#maalakshmi"
    for v in (v1, v2):  # user prompts never leak into a field
        for pc in v.values():
            assert "Generate content" not in (pc.caption + pc.description + pc.hashtags)


def test_printed_gemini_layout_is_parsed():
    _check(parse_gemini_response(normalize_pdf_text(PRINTED)))


def test_styled_heading_letters_and_split_colon():
    text = normalize_pdf_text("𝗜𝗡𝗦𝗧𝗔𝗚𝗥𝗔𝗠 𝗥𝗘𝗘𝗟𝗦\nCaption\n:\nनमस्ते 🙏\nHashtags\n:\n#a #b\n"
                              "FACEBOOK REELS (Hindi)\nDescription: हैलो\nHashtags: #c")
    r = parse_gemini_response(text)
    v = r.content["Video 1"]
    assert v[Platform.INSTAGRAM].caption == "नमस्ते 🙏" and v[Platform.INSTAGRAM].hashtags == "#a #b"
    assert v[Platform.FACEBOOK].description == "हैलो" and v[Platform.FACEBOOK].hashtags == "#c"


def test_prose_mentioning_instagram_reels_is_not_a_heading():
    out = normalize_pdf_text("Caption: share this on Instagram reels today")
    assert out == "Caption: share this on Instagram reels today"


def test_printed_layout_through_a_real_pdf():
    pymupdf = pytest.importorskip("pymupdf")
    from src.pdf_extract import extract_pdf_text
    doc = pymupdf.open()
    lines = PRINTED.splitlines()
    for chunk in (lines[:20], lines[20:]):  # response 1 continues onto page 2
        page = doc.new_page()
        y = 40
        for ln in chunk:
            page.insert_text((36, y), ln.encode("ascii", "ignore").decode() or " ", fontsize=8)
            y += 11
    raw = doc.tobytes()
    text = extract_pdf_text(raw)
    r = parse_gemini_response(text)
    assert set(r.content) == {"Video 1", "Video 2"}
    assert r.content["Video 1"][Platform.INSTAGRAM].hashtags.split() == [
        "#amlatree", "#sanatandharma"]


@pytest.mark.skipif(not FIXTURE.exists(), reason="place 'New Rituals 1.pdf' in tests/fixtures/")
def test_supplied_new_rituals_pdf():
    pytest.importorskip("pymupdf")
    from src.pdf_extract import extract_pdf_text
    r = parse_gemini_response(extract_pdf_text(FIXTURE.read_bytes()))
    assert r.has_content, r.issues
    assert len(r.content) >= 1
    for key, v in r.content.items():
        ig, fb = v.get(Platform.INSTAGRAM), v.get(Platform.FACEBOOK)
        assert ig and ig.caption and ig.hashtags.lstrip().startswith("#"), key
        assert fb and (fb.description or fb.caption) and fb.hashtags.lstrip().startswith("#"), key
        for pc in (ig, fb):
            assert "YOUTUBE" not in (pc.caption + pc.description + pc.hashtags).upper()

