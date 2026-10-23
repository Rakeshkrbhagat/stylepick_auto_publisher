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
from pathlib import Path
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
    "Open the shared link, select all of the Gemini responses (Ctrl/Cmd+A, then Ctrl/Cmd+C) "
    "and paste them into the **Gemini response** text box. Headings like INSTAGRAM REELS, "
    "Caption and Hashtags are recognised automatically. You can also save the text as a .txt "
    "file and import it."
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
    return _text_from_json_literals(page) or visible


def _text_from_json_literals(blob: str) -> str:
    """Join (in document order, de-duplicated) escaped JSON strings that look like responses.

    Each Gemini turn is usually a separate string, so they are combined before parsing."""
    seen, parts = set(), []

    def collect(text: str, depth: int) -> None:
        for lit in re.findall(r'"((?:[^"\\]|\\.){80,})"', text):
            try:
                s = json.loads(f'"{lit}"')
            except json.JSONDecodeError:
                continue
            if not re.search(r"instagram|facebook", s, re.I):
                continue
            # A nested JSON payload (e.g. RPC responses) – look inside it instead.
            if depth < 4 and re.search(r'"(?:[^"\\]|\\.){80,}"', s) and \
                    s.lstrip()[:1] in "[{":
                collect(s, depth + 1)
                continue
            s = html.unescape(s)
            if s not in seen:
                seen.add(s)
                parts.append(s)

    collect(blob, 0)
    # Drop strings fully contained in a longer one (e.g. the same text quoted twice).
    parts = [p for p in parts if not any(p != q and p in q for q in parts)]
    for candidate in ("\n\n".join(parts), *sorted(parts, key=len, reverse=True)):
        if candidate and parse_gemini_response(candidate).has_content:
            return candidate
    return ""


class BrowserRenderError(Exception):
    """Headless rendering was unavailable or failed (message is user-facing)."""


RENDER_TIME_LIMIT = 60  # hard cap (seconds) for the whole browser attempt


def render_with_browser(url: str, timeout: float = RENDER_TIME_LIMIT) -> str:
    """Render the public share link in a headless browser, in a separate process with a hard
    time limit, so the app can never get stuck. Anonymous visit – no sign-in, no cookies.

    If Chromium isn't installed yet, a one-time download is started in the background and the
    user is told to retry shortly (the request itself is never blocked by the download)."""
    import subprocess
    import sys

    if not browser_ready():
        start_browser_install()
        raise BrowserRenderError(_install_status_message())
    root = Path(__file__).resolve().parents[1]
    per_step = max(5.0, (timeout - 8) / 2)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "src.gemini_render", url, str(per_step)],
            capture_output=True, timeout=timeout, cwd=str(root))
    except subprocess.TimeoutExpired:
        raise BrowserRenderError(f"rendering took longer than {int(timeout)} s and was "
                                 "stopped") from None
    except OSError as exc:
        raise BrowserRenderError(f"could not start the renderer ({type(exc).__name__})") from None
    try:
        out = json.loads(done.stdout.decode("utf-8") or "{}")
    except ValueError:
        out = {}
    if out.get("ok"):
        return out.get("text", "")
    if out.get("kind") == "no_browser":
        start_browser_install()
        raise BrowserRenderError(_install_status_message())
    raise BrowserRenderError(out.get("error") or "the headless browser exited unexpectedly")


# ---------------------------------------------------------------- one-time browser install
_INSTALL = {"state": "idle", "error": ""}  # idle | running | done | failed


def browser_ready() -> bool:
    """True when Playwright and its Chromium build are present (no network, no launch)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    if _INSTALL["state"] == "done":
        return True
    try:
        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False


def start_browser_install() -> None:
    """Download Chromium once, in a background thread (never blocks a request)."""
    import threading

    try:
        import playwright  # noqa: F401
    except ImportError:
        _INSTALL.update(state="failed", error="playwright package missing")
        return
    if _INSTALL["state"] in ("running", "done"):
        return
    _INSTALL.update(state="running", error="")

    def work():
        import subprocess
        import sys
        try:
            done = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                                  capture_output=True, timeout=600)
            ok = done.returncode == 0
            _INSTALL.update(state="done" if ok else "failed",
                            error="" if ok else done.stderr.decode(errors="ignore")[-200:])
        except Exception as exc:  # pragma: no cover
            _INSTALL.update(state="failed", error=type(exc).__name__)

    threading.Thread(target=work, daemon=True).start()


def _install_status_message() -> str:
    if _INSTALL["state"] == "failed":
        return ("the headless browser could not be installed on this server "
                f"({_INSTALL['error'].strip() or 'unknown error'})")
    return ("the headless browser is being installed on this server (one-time, about 1–2 "
            "minutes) – try the link again shortly")


_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
                   "Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en,hi;q=0.8",
}


def fetch_shared_conversation(url: str, http_get: Callable | None = None, timeout: float = 15,
                              renderer: Callable | None = None) -> str:
    """Retrieve a public Gemini share link's text.

    1. Plain HTTPS GET of the share page (works only if the text is in the HTML).
    2. Supported alternative: render the public page in a headless browser (optional).
    Raises GeminiRetrievalError describing every attempt when no response text is found.
    """
    url = validate_share_url(url)
    live = http_get is None
    if http_get is None:
        import requests

        def http_get(u, timeout):  # noqa: E306
            return requests.get(u, timeout=timeout, allow_redirects=True, headers=_HEADERS)
    if renderer is None and live:
        renderer = render_with_browser if _browser_enabled() else None
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
    if parse_gemini_response(text).has_content:
        return text
    attempts = ["Direct download: the page HTML does not contain the conversation (it is "
                "rendered by JavaScript)."]
    if renderer is None:
        attempts.append("Browser rendering: disabled on this server.")
    else:
        try:
            rendered = renderer(final_url if "/share/" in final_url else url)
            if parse_gemini_response(rendered).has_content:
                return rendered
            attempts.append("Browser rendering: the page loaded, but no YouTube Shorts / "
                            "Instagram Reels / Facebook Reels sections were found in it.")
        except BrowserRenderError as exc:
            attempts.append(f"Browser rendering: {exc}.")
    raise GeminiRetrievalError("No captions were extracted from this link. "
                               + " ".join(attempts))


def _browser_enabled() -> bool:
    import os
    return os.getenv("GEMINI_BROWSER_RENDERING", "auto").strip().lower() not in (
        "0", "false", "off", "no")


def retrieve_and_parse(url: str, http_get: Callable | None = None) -> ParseResult:
    return parse_gemini_response(fetch_shared_conversation(url, http_get))
