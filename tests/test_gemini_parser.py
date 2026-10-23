"""Gemini parsing & retrieval tests based on representative responses (no network)."""

import pytest

from src.content_parser import detect_platform, detect_video, parse_gemini_response
from src.gemini_parser import (
    GeminiRetrievalError, extract_share_id, extract_text_from_html, fetch_shared_conversation,
    validate_share_url,
)
from src.mock_data import SAMPLE_GEMINI_TEXT, build_records
from src.models import Platform
from src.validation import validate_gemini_url, validate_sources

MARKDOWN_RESPONSE = """Here is the content for your videos! 🎉

## Video 1: reel_01.mp4

### 1. YouTube Shorts 🎬
**Hook:** Ruko! Ye kurti dekhe bina mat jao 😱
**Title:** फ्लोरल कुर्ती under ₹999 #Shorts
**Description:**
Budget mein best kurti ✨
Link description mein 👇
* **Hashtags:** #DailyFinds #Kurti #Shorts
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
**Hashtags:** #DailyFinds #Kurti #OOTD #देसीस्टाइल
**On-Screen Text (overlay):** सिर्फ ₹999 👉
**Optimized Transcript:** Kurti under ₹999! Link bio mein 🔗

### 3. Facebook Reels 📘
**SEO Title:** Floral Kurti Under ₹999 | Budget Fashion
**Description:** फ्लोरल कुर्ती – budget mein best 😍
Comment "LINK" for details 👇
**Hashtags:** #DailyFinds #IndianFashion
"""


def test_markdown_response_all_fields():
    r = parse_gemini_response(MARKDOWN_RESPONSE)
    v = r.content["reel_01.mp4"]
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
    assert fb.hashtags == "#DailyFinds #IndianFashion"
    assert not [i for i in r.issues if i.severity == "error"]
    assert not [i for i in r.issues if i.video == "reel_01.mp4"]


def test_sample_ten_videos():
    r = parse_gemini_response(SAMPLE_GEMINI_TEXT)
    assert len(r.content) == 10
    ig = r.content["reel_01.mp4"][Platform.INSTAGRAM]
    assert "💖" in ig.caption and "\n\n" in ig.caption and ig.transcript
    missing = [i for i in r.issues if i.video == "reel_07.mp4"]
    assert [i.field for i in missing] == ["description"]


def test_missing_fields_flagged_not_guessed():
    text = "=== VIDEO: a.mp4 ===\nInstagram Reels\nCaption: hello ✨\n"
    r = parse_gemini_response(text)
    ig = r.content["a.mp4"][Platform.INSTAGRAM]
    assert ig.caption == "hello ✨" and ig.hashtags == ""
    fields = {(i.platform, i.field) for i in r.issues}
    assert ("Instagram", "hashtags") in fields
    assert ("Facebook", "") in fields  # section missing
    assert not [i for i in r.issues if i.platform == "YouTube"]  # YouTube is never required


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
    ("=== VIDEO: reel_01.mp4 ===", "reel_01.mp4"), ("## Video 3", "Video 3"),
    ("**Video 2 – my clip.MOV**", "my clip.MOV"), ("Video dekho!", None),
    ("## Response 10", "Video 10"), ("**Response 1:**", "Video 1"), ("Reel #02", "Video 2"),
    ("### Request No. 7", "Video 7"), ("**Reel Cover Title:** Kurti", None),
    ("Response: here you go", None),
])
def test_detect_video(line, key):
    assert detect_video(line) == key


def test_results_feed_caption_matching():
    content = parse_gemini_response(MARKDOWN_RESPONSE).content
    recs = build_records([{"id": "x", "name": "Reel_01.MP4"}], content)
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


NEW_SHARE = "https://share.gemini.google/O06TBoSPT1GT"


@pytest.mark.parametrize("url, cid", [
    (SHARE, "abc123def"),
    (NEW_SHARE, "O06TBoSPT1GT"),
    (NEW_SHARE + "/", "O06TBoSPT1GT"),
    ("  https://gemini.google.com/share/abc123def?hl=en  ", "abc123def"),
])
def test_both_share_formats_accepted_and_id_extracted(url, cid):
    assert validate_share_url(url) == url.strip()
    assert extract_share_id(url) == cid
    assert validate_gemini_url(url) is None
    assert not validate_sources("https://drive.google.com/drive/folders/1AbCdEfGhIjKl", url,
                                False, False)


@pytest.mark.parametrize("bad", [
    "https://example.com/x", "http://share.gemini.google/O06TBoSPT1GT",
    "https://share.gemini.google/", "https://share.gemini.google.evil.com/O06TBoSPT1GT",
    "https://gemini.google.com/share/", "not a url",
])
def test_invalid_urls_give_useful_message(bad):
    msg = validate_gemini_url(bad)
    assert msg and "share.gemini.google" in msg and "gemini.google.com/share" in msg


def test_retrieval_preserves_supplied_url_format():
    seen = []
    page = "".join(f"<p>{line}</p>" for line in MARKDOWN_RESPONSE.splitlines())
    fetch_shared_conversation(NEW_SHARE, http_get=lambda u, timeout: (
        seen.append(u), Resp(200, page, url=NEW_SHARE))[1])
    assert seen == [NEW_SHARE]


@pytest.mark.parametrize("status, msg", [(404, "not found"), (403, "not publicly")])
def test_new_format_inaccessible_offers_fallback(status, msg):
    with pytest.raises(GeminiRetrievalError, match=msg) as e:
        fetch_shared_conversation(NEW_SHARE, http_get=lambda u, timeout: Resp(status, url=NEW_SHARE))
    assert "paste" in str(e.value) and "import" in str(e.value)


def test_new_format_empty_page_is_not_treated_as_empty_conversation():
    with pytest.raises(GeminiRetrievalError, match="JavaScript"):
        fetch_shared_conversation(NEW_SHARE, http_get=lambda u, timeout: Resp(
            200, "<html><body></body></html>", url=NEW_SHARE))


def test_fetch_success_from_html():
    page = "<html><body><script>var x=1;</script>" + "".join(
        f"<p>{line}</p>" for line in MARKDOWN_RESPONSE.splitlines()) + "</body></html>"
    text = fetch_shared_conversation(SHARE, http_get=lambda u, timeout: Resp(200, page))
    r = parse_gemini_response(text)
    assert r.content["reel_01.mp4"][Platform.INSTAGRAM].cover_title == "Kurti Under ₹999 🔥"


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


# ---------------- Story 3: Instagram / Facebook extraction from numbered responses
IG_CAPTION = ("नवरात्रि का पर्व केवल उत्सव नहीं, बल्कि अपनी भूलचूक के लिए क्षमा मांगने और मां का "
              "आशीर्वाद पाने का पवित्र अवसर है।")
IG_TAGS = "#navratri #sanatandharma #spirituality #hindurituals #bhakti"
FB_DESC = ("हम हर वर्ष नवरात्रि क्यों मनाते हैं और इस दौरान मां दुर्गा से क्षमा मांगने का क्या विधान है, "
           "जानें इस वीडियो में।")
FB_TAGS = "#navratriupay #sanatandharma #hindurituals #spiritualgrowth #maadurga"

NUMBERED_RESPONSE = f"""Response 1

🎬 YOUTUBE SHORTS
**Title:** Navratri ka rahasya 🔱
**Description:** YouTube only text – must not be published
**Hashtags:** #shorts #ytonly
**SEO Keywords:** navratri, durga

📸 INSTAGRAM REELS
**Instagram Caption:**
{IG_CAPTION}
**Instagram Hashtags:** {IG_TAGS}
**SEO Keywords:** navratri puja, maa durga
**Search Phrases:** navratri kyu manate hai
**Share Trigger:** Apne parivaar ke saath share karein 🙏
**Save Trigger:** Save karein
**Comment Trigger:** Comment mein "Jai Mata Di" likhein
**Optimized Transcript:** Namaste… yeh transcript publish nahi hona chahiye

📘 FACEBOOK REELS
**Facebook Description:**
{FB_DESC}
**Facebook Hashtags:** {FB_TAGS}
**SEO Keywords:** navratri facebook
**Comment Trigger:** Neeche comment karein
"""


def test_numbered_response_extracts_only_ig_and_fb_fields():
    r = parse_gemini_response(NUMBERED_RESPONSE)
    v = r.content["Video 1"]
    ig, fb = v[Platform.INSTAGRAM], v[Platform.FACEBOOK]
    assert ig.caption == IG_CAPTION and ig.hashtags == IG_TAGS
    assert fb.description == FB_DESC and fb.hashtags == FB_TAGS
    assert ig.publish_text() == f"{IG_CAPTION}\n\n{IG_TAGS}"
    assert fb.publish_text() == f"{FB_DESC}\n\n{FB_TAGS}"
    for text in (ig.publish_text(), fb.publish_text()):
        for banned in ("SEO", "navratri puja", "Search", "share karein", "Save karein",
                       "Jai Mata Di", "transcript", "YouTube only", "#shorts", "INSTAGRAM",
                       "FACEBOOK", "Trigger", "Neeche"):
            assert banned not in text
    assert IG_TAGS not in fb.publish_text() and FB_TAGS not in ig.publish_text()
    assert not [i for i in r.issues if i.video == "Video 1" and i.platform in ("Instagram", "Facebook")]


@pytest.mark.parametrize("ig_cap, ig_tag, fb_desc, fb_tag", [
    ("Caption", "Hashtags", "Description", "Hashtags"),
    ("Instagram Caption", "Instagram Hashtags", "Facebook Description", "Facebook Hashtags"),
    ("IG Caption (Hindi)", "Hashtags (5)", "FB Description", "Trending Hashtags"),
    ("Reel Caption", "Hashtag", "Post Description", "Hashtags"),
])
def test_field_label_variations(ig_cap, ig_tag, fb_desc, fb_tag):
    text = (f"## Video 4\n### Instagram Reels\n{ig_cap}: पहली पंक्ति ✨\nदूसरी पंक्ति 🙏\n"
            f"{ig_tag}: #a #b\n### Facebook Reels\n- **{fb_desc}:** एफबी विवरण 😍\n"
            f"- **{fb_tag}:** #c #d\n")
    v = parse_gemini_response(text).content["Video 4"]
    assert v[Platform.INSTAGRAM].caption == "पहली पंक्ति ✨\nदूसरी पंक्ति 🙏"
    assert v[Platform.INSTAGRAM].hashtags == "#a #b"
    fb = v[Platform.FACEBOOK]
    assert (fb.description or fb.caption) == "एफबी विवरण 😍" and fb.hashtags == "#c #d"


def test_labels_for_other_platform_are_not_cross_used():
    text = ("## Video 2\n### Instagram Reels\nInstagram Caption: IG text\nFacebook Description: "
            "wrong place\nInstagram Hashtags: #ig\n### Facebook Reels\nInstagram Caption: also "
            "wrong\nFacebook Description: FB text\nFacebook Hashtags: #fb\n")
    v = parse_gemini_response(text).content["Video 2"]
    ig, fb = v[Platform.INSTAGRAM], v[Platform.FACEBOOK]
    assert ig.caption == "IG text" and ig.description == "" and ig.hashtags == "#ig"
    assert fb.description == "FB text" and fb.caption == "" and fb.hashtags == "#fb"


def test_youtube_fields_never_reach_instagram_or_facebook():
    text = ("## Video 3\n### YouTube Shorts\nCaption: YT caption\nHashtags: #yt\n"
            "### Instagram Reels\nHashtags: #ig\n### Facebook Reels\nDescription: FB\n")
    r = parse_gemini_response(text)
    v = r.content["Video 3"]
    assert v[Platform.INSTAGRAM].caption == "" and v[Platform.INSTAGRAM].hashtags == "#ig"
    assert v[Platform.FACEBOOK].hashtags == ""
    flagged = {(i.platform, i.field) for i in r.issues if i.video == "Video 3"}
    assert ("Instagram", "caption") in flagged and ("Facebook", "hashtags") in flagged


def test_heading_ends_caption_and_hashtags_kept_verbatim():
    text = ("## Video 5\n### Instagram Reels\n**Caption:**\nLine 1 ✨\n\nLine 3 🙏\n"
            "#### Engagement ideas\nAsk them to share\n**Hashtags:** #नवरात्रि #Navratri2026\n")
    ig = parse_gemini_response(text).content["Video 5"][Platform.INSTAGRAM]
    assert ig.caption == "Line 1 ✨\n\nLine 3 🙏"
    assert ig.hashtags == "#नवरात्रि #Navratri2026"


def test_extracted_content_is_editable_before_scheduling():
    content = parse_gemini_response(NUMBERED_RESPONSE).content
    recs = build_records([{"id": "x", "name": "R-YT-1.mp4"}], content)
    rec = recs[0]
    assert rec.is_valid
    rec.get_content(Platform.INSTAGRAM).caption = "संपादित ✨"
    assert rec.content[Platform.INSTAGRAM].publish_text() == f"संपादित ✨\n\n{IG_TAGS}"
    assert content["Video 1"][Platform.INSTAGRAM].caption == IG_CAPTION  # source untouched

