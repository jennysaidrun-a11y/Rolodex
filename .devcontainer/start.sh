#!/usr/bin/env bash
# Runs every time the Codespace starts: get the latest code and data, then keep the app running on port 8000.
cd "$(dirname "$0")/.."
git pull -q --no-rebase --no-edit || echo "git pull failed; starting with what's here"
while true; do
  python -m uvicorn rolodex.app:app --host 0.0.0.0 --port 8000
  echo "The app stopped; restarting in 3 seconds."
  sleep 3
done
