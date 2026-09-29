@echo off
REM Start the rolodex on this computer (Windows): double-click this file, then open
REM http://localhost:8000. It gets the latest version from GitHub, keeps itself up to date
REM (tools\start_local.py) and saves your data to GitHub every few minutes. Leave the window open.
cd /d "%~dp0"
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
:start
python tools\start_local.py update
python -m pip install -q -r requirements.txt
python tools\start_local.py
if errorlevel 3 if not errorlevel 4 goto start
pause
