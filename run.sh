#!/usr/bin/env sh
# Start the rolodex on port 8000 (Linux / macOS).
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
exec uvicorn rolodex.app:app --host 0.0.0.0 --port "${PORT:-8000}"
