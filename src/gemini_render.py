"""Isolated headless-browser renderer for public Gemini share pages.

Run as a subprocess (``python -m src.gemini_render <url> <seconds>``) so the web app can
enforce a hard time limit and never hang. Prints one JSON object to stdout:
{"ok": true, "text": "..."} or {"ok": false, "error": "...", "kind": "..."}.
Anonymous visit only: no cookies, no Google sign-in, no private endpoints. A Google cookie
consent screen (shown to servers in some regions) is answered with "Reject all".
"""

from __future__ import annotations

import json
import re
import sys

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

# Text of the whole document, including open shadow roots (innerText skips those).
DEEP_TEXT_JS = r"""() => {
  const out = [];
  const blocks = (root) => {
    const t = root.innerText !== undefined ? root.innerText : (root.textContent || '');
    if (t) out.push(t);
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) {
        for (const c of el.shadowRoot.children) blocks(c);
      }
    }
  };
  blocks(document.body);
  return out.join('\n');
}"""

_CONSENT_BUTTONS = ("Reject all", "Alle ablehnen", "Tout refuser", "Rechazar todo",
                    "Accept all", "I agree")


def _answer_consent(page, ms: int) -> None:
    if "consent." not in page.url and not page.locator("form[action*='consent']").count():
        return
    for label in _CONSENT_BUTTONS:
        btn = page.get_by_role("button", name=label)
        if btn.count():
            btn.first.click(timeout=ms)
            page.wait_for_load_state("domcontentloaded", timeout=ms)
            return


def _snippet(text: str) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    return (s[:160] + "…") if len(s) > 160 else (s or "(empty page)")


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
            browser = pw.chromium.launch(
                headless=True, timeout=ms,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--no-zygote", "--renderer-process-limit=1",
                      "--disable-extensions", "--disable-background-networking",
                      "--disable-features=site-per-process,Translate,MediaRouter",
                      "--js-flags=--max-old-space-size=192",
                      "--disable-blink-features=AutomationControlled"])
            try:
                ctx = browser.new_context(locale="en-US", user_agent=UA,
                                          viewport={"width": 1024, "height": 768})
                # Text only: skip images, media and fonts to keep memory low.
                ctx.route("**/*", lambda r: r.abort()
                          if r.request.resource_type in ("image", "media", "font")
                          else r.continue_())
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=ms)
                _answer_consent(page, ms)
                if "accounts.google.com" in page.url:
                    return {"ok": False, "kind": "signin",
                            "error": "the link redirects to Google sign-in, so it is not "
                                     "publicly shared"}
                try:
                    page.wait_for_function(
                        "() => document.querySelector('message-content') || "
                        f"/instagram|facebook/i.test(({DEEP_TEXT_JS})())", timeout=ms)
                except PwTimeout:
                    seen = page.evaluate(DEEP_TEXT_JS)
                    return {"ok": False, "kind": "empty",
                            "error": "the page opened but showed no Instagram/Facebook text "
                                     f"within {int(seconds)} s (page title: "
                                     f"'{page.title()[:80]}', page text starts: "
                                     f"'{_snippet(seen)}')"}
                page.wait_for_timeout(2000)  # let remaining turns finish rendering
                # Scroll so lazily rendered turns are added, then collect all text.
                for _ in range(6):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(300)
                # Each <message-content> is one Gemini answer (user prompts are excluded).
                answers = page.locator("message-content").all_inner_texts()
                if answers:
                    text = "\n\n".join(f"=== Video {i} ===\n{a.strip()}"
                                       for i, a in enumerate(answers, start=1))
                else:
                    text = page.evaluate(DEEP_TEXT_JS)
                if not re.search(r"instagram", text, re.I) or \
                        not re.search(r"facebook", text, re.I):
                    return {"ok": False, "kind": "empty",
                            "error": "Gemini content did not render: no Instagram Reels / "
                                     f"Facebook Reels text found (starts: '{_snippet(text)}')"}
                return {"ok": True, "text": text, "turns": len(answers)}
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

