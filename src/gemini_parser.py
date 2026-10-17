"""Gemini shared-conversation retrieval.

Only *public share links* (gemini.google.com/share/<id>, share.gemini.google/<id> or
g.co/gemini/share/<id>)
are fetched, using a plain anonymous HTTPS GET – the same thing a browser does for
anyone with the link. No login, cookies or private endpoints are used.

Gemini share pages are usually rendered client-side with JavaScript, so the
returned HTML often does not contain the conversation text. In that case a
``GeminiRetrievalError`` explains the problem and the UI offers the paste-text
or file-import fallback.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Callable

from src.content_parser import ParseResult, parse_gemini_response

SHARE_URL_RE = re.compile(
    r"^https://(?:gemini\.google\.com/share/|g\.co/gemini/share/|share\.gemini\.google/)"
    r"(?P<id>[A-Za-z0-9_-]{6,})/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_PRIVATE_URL_RE = re.compile(r"^https://gemini\.google\.com/(?:app|u/\d+/app)(?:/|$)")
SUPPORTED_FORMATS = "https://gemini.google.com/share/<id> or https://share.gemini.google/<id>"

FALLBACK_HINT = (
    "Open the shared link in your browser, copy the full Gemini response "
    "(YouTube Shorts, Instagram Reels and Facebook Reels sections) and paste it into "
    "**Gemini response text**, or save it as a .txt file and import it."
)


class GeminiRetrievalError(Exception):
    def __init__(self, reason: str):
        super().__init__(f"{reason} {FALLBACK_HINT}")
        self.reason = reason


def validate_share_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise GeminiRetrievalError("No Gemini conversation URL was provided.")
    if _PRIVATE_URL_RE.match(url):
        raise GeminiRetrievalError(
            "This is a private Gemini chat URL (/app/…), which only works when signed in. "
            "In Gemini use Share → Create public link, and paste that /share/ link instead."
        )
    if not SHARE_URL_RE.match(url):
        raise GeminiRetrievalError(
            f"Not a Gemini shared conversation URL (expected {SUPPORTED_FORMATS})."
        )
    return url


def extract_share_id(url: str) -> str:
    """Conversation ID of a supported share URL (raises GeminiRetrievalError if invalid)."""
    return SHARE_URL_RE.match(validate_share_url(url)).group("id")


class _TextExtractor(HTMLParser):
    """Collect visible text, keeping line breaks for block elements."""

    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "pre", "section"}
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def extract_text_from_html(page: str) -> str:
    """Visible text, or – if the page embeds its data in scripts – JSON string literals."""
    ex = _TextExtractor()
    ex.feed(page)
    visible = ex.text()
    if parse_gemini_response(visible).has_content:
        return visible
    # Some pages embed the response as escaped JSON strings inside <script> tags.
    candidates = []
    for lit in re.findall(r'"((?:[^"\\]|\\.){200,})"', page):
        try:
            s = json.loads(f'"{lit}"')
        except json.JSONDecodeError:
            continue
        if re.search(r"instagram|facebook|youtube", s, re.I):
            candidates.append(html.unescape(s))
    for c in sorted(candidates, key=len, reverse=True):
        if parse_gemini_response(c).has_content:
            return c
    return visible


def fetch_shared_conversation(url: str, http_get: Callable | None = None, timeout: float = 15) -> str:
    """Fetch a public Gemini share link and return its text content.

    Raises GeminiRetrievalError with a user-facing reason when retrieval isn't possible.
    """
    url = validate_share_url(url)
    if http_get is None:
        import requests

        def http_get(u, timeout):  # noqa: E306
            return requests.get(u, timeout=timeout, allow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (StylePick Auto Publisher)",
                "Accept-Language": "en,hi;q=0.8",
            })
    try:
        resp = http_get(url, timeout=timeout)
    except Exception as exc:  # network errors, timeouts
        raise GeminiRetrievalError(f"Could not reach Gemini ({type(exc).__name__}).") from exc
    status = getattr(resp, "status_code", 0)
    if status == 404:
        raise GeminiRetrievalError("The shared conversation was not found (deleted or link disabled).")
    if status in (401, 403):
        raise GeminiRetrievalError("The conversation is not publicly shared or access was denied.")
    if status == 429:
        raise GeminiRetrievalError("Gemini is rate-limiting requests. Wait a moment and retry.")
    if status != 200:
        raise GeminiRetrievalError(f"Gemini returned HTTP {status}.")
    final_url = str(getattr(resp, "url", url))
    if "accounts.google.com" in final_url:
        raise GeminiRetrievalError("Gemini requires sign-in for this link; it is not publicly shared.")
    text = extract_text_from_html(resp.text or "")
    if not parse_gemini_response(text).has_content:
        raise GeminiRetrievalError(
            "The shared page loaded, but its content is rendered by JavaScript and is not "
            "machine-readable, so captions could not be extracted automatically."
        )
    return text


def retrieve_and_parse(url: str, http_get: Callable | None = None) -> ParseResult:
    return parse_gemini_response(fetch_shared_conversation(url, http_get))
