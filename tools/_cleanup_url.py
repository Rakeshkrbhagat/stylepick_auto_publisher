"""One-off: delete the obsolete Gemini share-URL extraction files, then itself.

Run once from the project root:  python3 tools/_cleanup_url.py
"""
import os

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for f in ("src/gemini_parser.py", "src/gemini_render.py", "tests/test_gemini_embedded.py",
          "probe.py", "probe_dom.py", "probe_out.txt", "rendered.txt", "zz.txt",
          "tools/_cleanup_url.py"):
    p = os.path.join(root, f)
    if os.path.exists(p):
        os.remove(p)
        print("removed", f)

