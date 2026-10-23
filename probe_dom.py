import json

from playwright.sync_api import sync_playwright

from src.gemini_render import UA

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    p = b.new_context(locale="en-US", user_agent=UA).new_page()
    p.goto("https://share.gemini.google/IzUZ7TduLL3c", wait_until="domcontentloaded", timeout=40000)
    p.wait_for_function("() => /instagram/i.test(document.body.innerText)", timeout=40000)
    p.wait_for_timeout(2000)
    info = p.evaluate("""() => {
      const counts = {};
      for (const el of document.querySelectorAll('*')) {
        const t = el.tagName.toLowerCase();
        if (t.includes('-') || /response|query|message|turn/.test(el.className || '')) {
          const k = t + ' .' + String(el.className || '').split(' ').slice(0, 3).join('.');
          counts[k] = (counts[k] || 0) + 1;
        }
      }
      return Object.entries(counts).filter(([k, v]) => /response|query|message|turn|user|model/i.test(k)).sort((a, b) => b[1] - a[1]).slice(0, 40);
    }""")
    print(json.dumps(info, indent=0, ensure_ascii=False))
    for sel in ["message-content", "user-query", "model-response", "response-container", ".model-response-text", "share-turn-viewer", "user-query-content"]:
        n = p.locator(sel).count()
        first = p.locator(sel).first.inner_text()[:120] if n else ""
        print(sel, n, repr(first))
    b.close()

