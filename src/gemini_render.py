"""Isolated headless-browser renderer for public Gemini share pages.

Run as a subprocess (``python -m src.gemini_render <url> <seconds>``) so the web app can
enforce a hard time limit and never hang. Prints one JSON object to stdout:
{"ok": true, "text": "..."} or {"ok": false, "error": "...", "kind": "..."}.
Anonymous visit only: no cookies, no Google sign-in, no private endpoints.
"""

from __future__ import annotations

import json
import sys

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")


def render(url: str, seconds: float) -> dict:
    try:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import TimeoutError as PwTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "kind": "missing",
                "error": "browser rendering is not installed on this server (playwright "
                         "package missing)"}
    ms = int(seconds * 1000)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, timeout=ms,
                                         args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = browser.new_context(locale="en-US", user_agent=UA).new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=ms)
                if "accounts.google.com" in page.url:
                    return {"ok": False, "kind": "signin",
                            "error": "the link redirects to Google sign-in, so it is not "
                                     "publicly shared"}
                try:
                    page.wait_for_function(
                        "() => /instagram|facebook/i.test(document.body.innerText)", timeout=ms)
                except PwTimeout:
                    return {"ok": False, "kind": "empty",
                            "error": "the page rendered, but no Instagram/Facebook text "
                                     f"appeared within {int(seconds)} s"}
                page.wait_for_timeout(1500)  # let remaining turns finish rendering
                return {"ok": True, "text": page.inner_text("body")}
            finally:
                browser.close()
    except PwTimeout:
        return {"ok": False, "kind": "timeout",
                "error": f"the page did not load within {int(seconds)} s"}
    except PwError as exc:
        msg = (str(exc).splitlines() or [type(exc).__name__])[0]
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            return {"ok": False, "kind": "no_browser",
                    "error": "the headless browser is not installed yet on this server"}
        return {"ok": False, "kind": "failed",
                "error": f"the headless browser failed ({msg[:120]})"}


if __name__ == "__main__":
    out = render(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 20)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))

