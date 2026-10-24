#!/usr/bin/env bash
python -m playwright install --with-deps chromium || python -m playwright install chromium
export PLAYWRIGHT_BROWSERS_PATH=0
# Store the browser inside the project so it ships with the build (no persistent disk needed).
pip install -r requirements.txt
pip install --upgrade pip
set -euo pipefail
# Render build: Python deps + the Chromium build matching the installed Playwright version.

