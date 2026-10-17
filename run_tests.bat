@echo off
REM Runs the offline test suite (no network, no public posts).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pytest -q tests
pause

