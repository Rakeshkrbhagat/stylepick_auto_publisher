@echo off
pause
python -m streamlit run app.py --server.address localhost --browser.gatherUsageStats false
call .venv\Scripts\activate.bat
)
  call install.bat
  echo Not installed yet - running install.bat first.
if not exist .venv\Scripts\activate.bat (
cd /d "%~dp0"
REM Launches the StylePick dashboard in your browser.

