"""Gemini shared-conversation retrieval.
    return parse_gemini_response(fetch_shared_conversation(url, http_get))
def retrieve_and_parse(url: str, http_get: Callable | None = None) -> ParseResult:


    return text
        )
            "machine-readable, so captions could not be extracted automatically."
            "The shared page loaded, but its content is rendered by JavaScript and is not "
        raise GeminiRetrievalError(
    if not parse_gemini_response(text).has_content:
    text = extract_text_from_html(resp.text or "")
        raise GeminiRetrievalError("Gemini requires sign-in for this link; it is not publicly shared.")
    if "accounts.google.com" in final_url:
    final_url = str(getattr(resp, "url", url))
        raise GeminiRetrievalError(f"Gemini returned HTTP {status}.")
    if status != 200:
        raise GeminiRetrievalError("Gemini is rate-limiting requests. Wait a moment and retry.")
    if status == 429:
        raise GeminiRetrievalError("The conversation is not publicly shared or access was denied.")
    if status in (401, 403):
        raise GeminiRetrievalError("The shared conversation was not found (deleted or link disabled).")
    if status == 404:
    status = getattr(resp, "status_code", 0)
        raise GeminiRetrievalError(f"Could not reach Gemini ({type(exc).__name__}).") from exc
    except Exception as exc:  # network errors, timeouts
        resp = http_get(url, timeout=timeout)
    try:
            })
                "Accept-Language": "en,hi;q=0.8",
                "User-Agent": "Mozilla/5.0 (StylePick Auto Publisher)",
            return requests.get(u, timeout=timeout, allow_redirects=True, headers={
        def http_get(u, timeout):  # noqa: E306

        import requests
    if http_get is None:
    url = validate_share_url(url)
    """
    Raises GeminiRetrievalError with a user-facing reason when retrieval isn't possible.

    """Fetch a public Gemini share link and return its text content.
def fetch_shared_conversation(url: str, http_get: Callable | None = None, timeout: float = 15) -> str:


    return visible
            return c
        if parse_gemini_response(c).has_content:
    for c in sorted(candidates, key=len, reverse=True):
            candidates.append(html.unescape(s))
        if re.search(r"instagram|facebook|youtube", s, re.I):
            continue
        except json.JSONDecodeError:
            s = json.loads(f'"{lit}"')
        try:
    for lit in re.findall(r'"((?:[^"\\]|\\.){200,})"', page):
    candidates = []
    # Some pages embed the response as escaped JSON strings inside <script> tags.
        return visible
    if parse_gemini_response(visible).has_content:
    visible = ex.text()
    ex.feed(page)
    ex = _TextExtractor()
    """Visible text, or – if the page embeds its data in scripts – JSON string literals."""
def extract_text_from_html(page: str) -> str:


        return re.sub(r"\n{3,}", "\n\n", t).strip()
        t = "".join(self.parts)
    def text(self) -> str:

            self.parts.append(data)
        if not self._skip:
    def handle_data(self, data):

            self.parts.append("\n")
        elif tag in self._BLOCK:
            self._skip -= 1
        if tag in self._SKIP and self._skip:
    def handle_endtag(self, tag):

            self.parts.append("\n")
        elif tag in self._BLOCK:
            self._skip += 1
        if tag in self._SKIP:
    def handle_starttag(self, tag, attrs):

        self._skip = 0
        self.parts: list[str] = []
        super().__init__(convert_charrefs=True)
    def __init__(self):

    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre", "section"}

    """Collect visible text, keeping line breaks for block elements."""
class _TextExtractor(HTMLParser):


    return url
        )
            "Not a Gemini shared conversation URL (expected https://gemini.google.com/share/<id>)."
        raise GeminiRetrievalError(
    if not SHARE_URL_RE.match(url):
        )
            "In Gemini use Share → Create public link, and paste that /share/ link instead."
            "This is a private Gemini chat URL (/app/…), which only works when signed in. "
        raise GeminiRetrievalError(
    if _PRIVATE_URL_RE.match(url):
        raise GeminiRetrievalError("No Gemini conversation URL was provided.")
    if not url:
    url = (url or "").strip()
def validate_share_url(url: str) -> str:


        self.reason = reason
        super().__init__(f"{reason} {FALLBACK_HINT}")
    def __init__(self, reason: str):
class GeminiRetrievalError(Exception):


)
    "**Gemini response text**, or save it as a .txt file and import it."
    "(YouTube Shorts, Instagram Reels and Facebook Reels sections) and paste it into "
    "Open the shared link in your browser, copy the full Gemini response "
FALLBACK_HINT = (

_PRIVATE_URL_RE = re.compile(r"^https://gemini\.google\.com/(?:app|u/\d+/app)(?:/|$)")
)
    r"^https://(?:gemini\.google\.com/share/|g\.co/gemini/share/)[A-Za-z0-9_-]{6,}/?(?:[?#].*)?$"
SHARE_URL_RE = re.compile(

from src.content_parser import ParseResult, parse_gemini_response

from typing import Callable
from html.parser import HTMLParser
import re
import json
import html

from __future__ import annotations

"""
or file-import fallback.
``GeminiRetrievalError`` explains the problem and the UI offers the paste-text
returned HTML often does not contain the conversation text. In that case a
Gemini share pages are usually rendered client-side with JavaScript, so the

anyone with the link. No login, cookies or private endpoints are used.
are fetched, using a plain anonymous HTTPS GET – the same thing a browser does for
Only *public share links* (gemini.google.com/share/<id> or g.co/gemini/share/<id>)


