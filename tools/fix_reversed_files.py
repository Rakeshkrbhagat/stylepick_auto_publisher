"""Repair source files whose lines were saved in reverse order (first line kept in place).

Usage:  python tools/fix_reversed_files.py          (dry run)
        python tools/fix_reversed_files.py --write  (fix files)

A file is only rewritten if it does NOT compile as-is but DOES compile after
un-reversing, so correct files are never touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def compiles(text: str, name: str) -> bool:
    try:
        compile(text, name, "exec")
        return True
    except SyntaxError:
        return False


def unreverse(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    fixed = [lines[0], *reversed(lines[1:])]
    while fixed and not fixed[-1].strip():
        fixed.pop()
    return "\n".join(fixed) + "\n"


def main(write: bool) -> int:
    fixed, broken = [], []
    for path in sorted(ROOT.rglob("*.py")):
        if any(p in (".venv", "venv", "__pycache__") for p in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if compiles(text, str(path)):
            continue
        candidate = unreverse(text)
        if compiles(candidate, str(path)):
            fixed.append(path)
            if write:
                path.write_text(candidate, encoding="utf-8", newline="\n")
        else:
            broken.append(path)
    for p in fixed:
        print(("FIXED     " if write else "REVERSED  ") + str(p.relative_to(ROOT)))
    for p in broken:
        print("BROKEN    " + str(p.relative_to(ROOT)) + "  (needs manual repair)")
    if not fixed and not broken:
        print("All Python files compile.")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main("--write" in sys.argv))

