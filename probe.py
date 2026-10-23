import json

from src.content_parser import parse_gemini_response
from src.gemini_parser import fetch_shared_conversation
from src.matching import build_records
from src.models import Platform

text = fetch_shared_conversation("https://share.gemini.google/IzUZ7TduLL3c")
open("rendered.txt", "w", encoding="utf-8").write(text)
r = parse_gemini_response(text)
print("KEYS", len(r.content), list(r.content)[:3])
for k, v in list(r.content.items()):
    ig, fb = v.get(Platform.INSTAGRAM), v.get(Platform.FACEBOOK)
    print(k, "| IG:", repr((ig.caption if ig else "")[:60]), repr(ig.hashtags if ig else ""),
          "| FB:", repr((fb.description or fb.caption)[:50] if fb else ""), repr(fb.hashtags if fb else ""))
for i in r.issues:
    if i.severity == "error" or "not found" in i.message:
        print("ISSUE", i.severity, i.video, i.message[:110])
recs = build_records([{"id": "a", "name": "R-YT-10.mp4"}, {"id": "b", "name": "M-New-1.mp4"}], r.content)
print([(x.filename, x.content_key, x.is_valid, x.errors) for x in recs])

