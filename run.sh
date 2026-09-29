#!/usr/bin/env sh
# Start the rolodex on this computer (Linux / macOS), then open http://localhost:8000.
# It gets the latest version from GitHub, keeps itself up to date (tools/start_local.py)
# and saves your data to GitHub every few minutes.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
while :; do
  python tools/start_local.py update
  pip install -q -r requirements.txt
  python tools/start_local.py
  [ $? -eq 3 ] || break
done
