"""Embedded (script/RPC) Gemini responses split across several JSON strings are combined."""

import json

from src.gemini_parser import extract_text_from_html, fetch_shared_conversation
from src.models import Platform

R1 = ("INSTAGRAM REELS\nCaption\nपहला कैप्शन ✨ " + "x" * 60 + "\nHashtags\n#one\n"
      "FACEBOOK REELS\nDescription\nपहला विवरण\nHashtags\n#fb1")
R2 = ("YOUTUBE SHORTS\nTitle\nignored\nINSTAGRAM REELS\nCaption\nदूसरा कैप्शन 💖 " + "y" * 60
      + "\nHashtags\n#two\nFACEBOOK REELS\nDescription\nदूसरा विवरण\nHashtags\n#fb2")


class Resp:
    status_code = 200
    url = "https://gemini.google.com/share/abc123def456"

    def __init__(self, text):
        self.text = text


def _page():
    data = json.dumps([[None, R1], [None, R2]])  # escaped strings inside a script
    return f"<html><body><div>Loading…</div><script>var d = {json.dumps(data)};</script></body></html>"


def test_multiple_embedded_responses_are_combined():
    text = extract_text_from_html(_page())
    assert "पहला कैप्शन" in text and "दूसरा कैप्शन" in text


def test_fetch_parses_embedded_responses_in_order():
    from src.content_parser import parse_gemini_response
    text = fetch_shared_conversation("https://gemini.google.com/share/abc123def456",
                                     http_get=lambda u, timeout: Resp(_page()))
    r = parse_gemini_response(text)
    assert r.content["Video 1"][Platform.INSTAGRAM].hashtags == "#one"
    assert r.content["Video 2"][Platform.FACEBOOK].description == "दूसरा विवरण"


JS_SHELL = "<html><body><div id='app'>Loading…</div><script src='app.js'></script></body></html>"


def test_browser_rendering_used_when_html_is_empty():
    calls = []

    def renderer(u):
        calls.append(u)
        return "Gemini\nYou said: make captions\n" + R1 + "\n\n" + R2

    text = fetch_shared_conversation("https://share.gemini.google/abc123def456",
                                     http_get=lambda u, timeout: Resp(JS_SHELL),
                                     renderer=renderer)
    assert calls == ["https://gemini.google.com/share/abc123def456"]  # redirected share URL
    assert "पहला कैप्शन ✨" in text and "#fb2" in text


def test_every_failed_attempt_is_reported():
    import pytest

    from src.gemini_parser import BrowserRenderError, GeminiRetrievalError

    def broken(u):
        raise BrowserRenderError("the headless browser is not installed on this server")

    with pytest.raises(GeminiRetrievalError) as e:
        fetch_shared_conversation("https://gemini.google.com/share/abc123def456",
                                  http_get=lambda u, timeout: Resp(JS_SHELL), renderer=broken)
    msg = str(e.value)
    assert "No captions were extracted" in msg and "Direct download" in msg
    assert "not installed" in msg and "paste" in msg and "import" in msg

    with pytest.raises(GeminiRetrievalError, match="no YouTube Shorts"):
        fetch_shared_conversation("https://gemini.google.com/share/abc123def456",
                                  http_get=lambda u, timeout: Resp(JS_SHELL),
                                  renderer=lambda u: "Sign in to continue")
