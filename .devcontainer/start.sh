#!/usr/bin/env bash
# Runs every time the Codespace starts (output in /tmp/rolodex.log): gets the latest code and data,
# keeps the app running on port 8000, and keeps it up to date by itself.
# Every minute it checks GitHub; when there's a newer version of the app it gets it and restarts
# the app, waiting first for any analysis in progress to finish. Your data is saved to GitHub by
# the app itself (rolodex/gitsync.py). Run `bash .devcontainer/start.sh` any time to update right away.
# Everything is inside main() so updating this file while it runs is safe.
main() {
  cd "$(dirname "$0")/.." || exit 1
  local pidfile=/tmp/rolodex-start.pid
  local old; old=$(cat "$pidfile" 2>/dev/null)
  [ -n "$old" ] && [ "$old" != "$$" ] && kill "$old" 2>/dev/null   # an older copy of this script
  echo $$ > "$pidfile"
  stop_app
  get_updates
  local running; running=$(code_version)
  start_app
  while sleep "${ROLODEX_CHECK_SECONDS:-60}"; do
    get_updates
    if [ "$(code_version)" != "$running" ]; then
      if analysis_running; then
        echo "New version waiting; restarting after the analysis finishes."
      else
        echo; echo "New version: $(git log --oneline -1). Restarting..."
        git diff --quiet "$running" -- requirements.txt 2>/dev/null || pip install --user -q -r requirements.txt
        stop_app
        exec bash .devcontainer/start.sh   # the newest copy of this script takes over
      fi
    fi
    kill -0 "$APP" 2>/dev/null || start_app   # restart the app if it stopped
  done
}

# Merge the newest main. Only the app writes data/, and only code changes come from GitHub, so
# this doesn't conflict; if git is busy (the app saving data) it just tries again next minute.
get_updates() {
  git fetch -q origin main 2>/dev/null || return
  if ! git merge -q --no-edit origin/main >/dev/null 2>&1; then
    git merge --abort 2>/dev/null
  fi
}

# Everything except the saved data: a change here means a new version of the app.
code_version() { git log -1 --format=%H -- . ':(exclude)data' 2>/dev/null; }

analysis_running() { pgrep -f "Run the rolodex analysis" >/dev/null; }

start_app() {
  echo; echo "Running version $(git log --oneline -1)"; echo
  (while true; do
     python -m uvicorn rolodex.app:app --host 0.0.0.0 --port 8000
     echo "The app stopped; restarting in 3 seconds."
     sleep 3
   done) &
  APP=$!
}

stop_app() {
  [ -n "$APP" ] && kill "$APP" 2>/dev/null
  pkill -f "uvicorn rolodex.app:app" 2>/dev/null
  sleep 1
}

main "$@"; exit
