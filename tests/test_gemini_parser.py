"""Gemini parsing & retrieval tests based on representative responses (no network)."""

import pytest

from src.content_parser import detect_platform, detect_video, parse_gemini_response
from src.gemini_parser import (
    GeminiRetrievalError, extract_text_from_html, fetch_shared_conversation, validate_share_url,
)
from src.mock_data import SAMPLE_GEMINI_TEXT, build_records
from src.models import Platform

MARKDOWN_RESPONSE = """Here is the content for your videos! 🎉

## Video 1: stylepick_01.mp4

### 1. YouTube Shorts 🎬
**Hook:** Ruko! Ye kurti dekhe bina mat jao 😱
**Title:** फ्लोरल कुर्ती under ₹999 #Shorts
**Description:**
Budget mein best kurti ✨
Link description mein 👇
* **Hashtags:** #StylePickFinds #Kurti #Shorts
* **YouTube Tags:** kurti, budget kurti, फ्लोरल कुर्ती
**On-Screen Text:** सिर्फ ₹999 🔥
**Transcript:**
Namaste doston!
Aaj ka find – फ्लोरल कुर्ती.

---

### 2. Instagram Reels 📸
**Reel Cover Title:** Kurti Under ₹999 🔥
**Caption:**
Ye kurti sabko pasand aayegi 💖
हर occasion के लिए perfect ✨

Save karo 📌
**Hashtags:** #StylePickFinds #Kurti #OOTD #देसीस्टाइल
**On-Screen Text (overlay):** सिर्फ ₹999 👉
**Optimized Transcript:** Kurti under ₹999! Link bio mein 🔗

### 3. Facebook Reels 📘
**SEO Title:** Floral Kurti Under ₹999 | Budget Fashion
**Description:** फ्लोरल कुर्ती – budget mein best 😍
Comment "LINK" for details 👇
**Hashtags:** #StylePickFinds #IndianFashion
"""


def test_markdown_response_all_fields():
    r = parse_gemini_response(MARKDOWN_RESPONSE)
    v = r.content["stylepick_01.mp4"]
    yt, ig, fb = v[Platform.YOUTUBE], v[Platform.INSTAGRAM], v[Platform.FACEBOOK]
    assert yt.hook.startswith("Ruko!") and "फ्लोरल कुर्ती" in yt.title
    assert yt.description == "Budget mein best kurti ✨\nLink description mein 👇"
    assert yt.youtube_tags.startswith("kurti") and yt.on_screen_text == "सिर्फ ₹999 🔥"
    assert yt.transcript == "Namaste doston!\nAaj ka find – फ्लोरल कुर्ती."
    assert ig.cover_title == "Kurti Under ₹999 🔥"
    assert ig.caption == "Ye kurti sabko pasand aayegi 💖\nहर occasion के लिए perfect ✨\n\nSave karo 📌"
    assert "#देसीस्टाइल" in ig.hashtags
    assert ig.on_screen_text == "सिर्फ ₹999 👉" and ig.transcript.startswith("Kurti")
    assert fb.seo_title.startswith("Floral Kurti")
    assert fb.description == 'फ्लोरल कुर्ती – budget mein best 😍\nComment "LINK" for details 👇'
    assert fb.hashtags == "#StylePickFinds #IndianFashion"
    assert not [i for i in r.issues if i.severity == "error"]
    assert not [i for i in r.issues if i.video == "stylepick_01.mp4"]


def test_sample_ten_videos():
    r = parse_gemini_response(SAMPLE_GEMINI_TEXT)
    assert len(r.content) == 10
    ig = r.content["stylepick_01.mp4"][Platform.INSTAGRAM]
    assert "💖" in ig.caption and "\n\n" in ig.caption and ig.transcript
    missing = [i for i in r.issues if i.video == "stylepick_07.mp4"]
    assert [i.field for i in missing] == ["description"]


def test_missing_fields_flagged_not_guessed():
    text = "=== VIDEO: a.mp4 ===\nInstagram Reels\nCaption: hello ✨\n"
    r = parse_gemini_response(text)
    ig = r.content["a.mp4"][Platform.INSTAGRAM]
    assert ig.caption == "hello ✨" and ig.hashtags == ""
    fields = {(i.platform, i.field) for i in r.issues}
    assert ("Instagram", "hashtags") in fields and ("Instagram", "cover_title") in fields
    assert ("Facebook", "") in fields and ("YouTube", "") in fields  # sections missing


def test_ambiguous_field_left_empty():
    text = "=== VIDEO: a.mp4 ===\nFacebook Reels\nSEO Title: One\nSEO Title: Two\n"
    r = parse_gemini_response(text)
    assert r.content["a.mp4"][Platform.FACEBOOK].seo_title == ""
    assert any(i.severity == "error" and "ambiguous" in i.message for i in r.issues)


def test_facebook_title_maps_to_seo_title_and_caption_accepted():
    text = "=== VIDEO: a.mp4 ===\nFacebook Reels:\nTitle: T\nCaption: C\nHashtags: #x\n"
    r = parse_gemini_response(text)
    fb = r.content["a.mp4"][Platform.FACEBOOK]
    assert fb.seo_title == "T" and fb.caption == "C"
    assert not [i for i in r.issues if i.platform == "Facebook"]


def test_unassigned_and_numbered_videos_flagged():
    r = parse_gemini_response("Instagram Reels\nCaption: x\n")
    assert "(unassigned)" in r.content
    assert any("no video filename" in i.message for i in r.issues)
    r = parse_gemini_response("Video 2\nInstagram Reels\nCaption: x\n")
    assert "Video 2" in r.content and any("no filename" in i.message for i in r.issues)


def test_no_sections():
    r = parse_gemini_response("Just some chat text.")
    assert not r.has_content and r.issues[0].severity == "error"


@pytest.mark.parametrize("line, plat", [
    ("--- YouTube Shorts ---", Platform.YOUTUBE), ("### 2. Instagram Reels 📸", Platform.INSTAGRAM),
    ("**Facebook Reels:**", Platform.FACEBOOK), ("Instagram: follow us", None),
    ("Caption:", None), ("Facebook pe share karo", None),
])
def test_detect_platform(line, plat):
    assert detect_platform(line) == plat


@pytest.mark.parametrize("line, key", [
    ("=== VIDEO: stylepick_01.mp4 ===", "stylepick_01.mp4"), ("## Video 3", "Video 3"),
    ("**Video 2 – my clip.MOV**", "my clip.MOV"), ("Video dekho!", None),
])
def test_detect_video(line, key):
    assert detect_video(line) == key


def test_results_feed_caption_matching():
    content = parse_gemini_response(MARKDOWN_RESPONSE).content
    recs = build_records([{"id": "x", "name": "StylePick_01.MP4"}], content)
    assert recs[0].content[Platform.INSTAGRAM].cover_title


# ---------------- retrieval
class Resp:
    def __init__(self, status, text="", url="https://gemini.google.com/share/abc123"):
        self.status_code, self.text, self.url = status, text, url


SHARE = "https://gemini.google.com/share/abc123def"


def test_validate_share_url():
    assert validate_share_url(SHARE) == SHARE
    with pytest.raises(GeminiRetrievalError, match="private"):
        validate_share_url("https://gemini.google.com/app/abc123")
    with pytest.raises(GeminiRetrievalError):
        validate_share_url("https://example.com/x")


def test_fetch_success_from_html():
    page = "<html><body><script>var x=1;</script>" + "".join(
        f"<p>{line}</p>" for line in MARKDOWN_RESPONSE.splitlines()) + "</body></html>"
    text = fetch_shared_conversation(SHARE, http_get=lambda u, timeout: Resp(200, page))
    r = parse_gemini_response(text)
    assert r.content["stylepick_01.mp4"][Platform.INSTAGRAM].cover_title == "Kurti Under ₹999 🔥"


def test_fetch_js_rendered_page_gives_fallback():
    page = "<html><body><div id='app'></div><script>load()</script></body></html>"
    with pytest.raises(GeminiRetrievalError, match="JavaScript") as e:
        fetch_shared_conversation(SHARE, http_get=lambda u, timeout: Resp(200, page))
    assert "paste" in str(e.value)


@pytest.mark.parametrize("status, msg", [(404, "not found"), (403, "not publicly"),
                                         (429, "rate"), (500, "HTTP 500")])
def test_fetch_http_errors(status, msg):
    with pytest.raises(GeminiRetrievalError, match=msg):
        fetch_shared_conversation(SHARE, http_get=lambda u, timeout: Resp(status))


def test_fetch_network_error_and_signin_redirect():
    def boom(u, timeout):
        raise ConnectionError("down")

    with pytest.raises(GeminiRetrievalError, match="reach Gemini"):
        fetch_shared_conversation(SHARE, http_get=boom)
    with pytest.raises(GeminiRetrievalError, match="sign-in"):
        fetch_shared_conversation(SHARE, http_get=lambda u, timeout: Resp(
            200, "<p>login</p>", url="https://accounts.google.com/signin"))


def test_extract_text_from_embedded_json():
    import json
    blob = json.dumps(MARKDOWN_RESPONSE)
    page = f"<html><script>window.data=[{blob}];</script></html>"
    assert "Reel Cover Title" in extract_text_from_html(page)

