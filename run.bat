@echo off
REM Start the rolodex on port 8000 (Windows).
cd /d "%~dp0"
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
uvicorn rolodex.app:app --host 0.0.0.0 --port 8000
