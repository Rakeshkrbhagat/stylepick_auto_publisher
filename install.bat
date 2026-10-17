@echo off
REM StylePick Auto Publisher - one-time Windows installer (Python 3.10+ required)
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python 3.10+ is not installed. Get it from https://www.python.org/downloads/windows/
  echo and tick "Add python.exe to PATH" during setup.
  pause & exit /b 1
)
if not exist .venv (
  echo Creating virtual environment...
  %PY% -m venv .venv || (echo Failed to create venv & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Dependency install failed - see TROUBLESHOOTING.md & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
if not exist credentials mkdir credentials
echo.
echo Installed. Edit .env, put client_secret.json in credentials\, then run run.bat
pause

