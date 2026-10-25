#!/usr/bin/env bash
# Render build: Python dependencies only (no browser needed).
set -euo pipefail
pip install --upgrade pip
pip install -r requirements.txt

