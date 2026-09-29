"""Run the rolodex on this computer and keep it up to date by itself (what .devcontainer/start.sh
does in a Codespace). Started by run.bat / run.sh.

Gets the latest version from GitHub, starts the app on http://localhost:8000 and checks GitHub
every minute. When there's a newer version it waits for any analysis in progress to finish, then
exits with RESTART so run.bat installs what's new and starts again. The app saves your data to
GitHub by itself every few minutes (rolodex/gitsync.py).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESTART = 3
CHECK_SECONDS = int(os.environ.get("ROLODEX_CHECK_SECONDS", "60"))
# Only this computer can open the app unless ROLODEX_HOST says otherwise (e.g. 0.0.0.0 for a phone
# on the same Wi-Fi): on a work PC, other machines on the network shouldn't reach it.
HOST = os.environ.get("ROLODEX_HOST", "127.0.0.1")
PORT = os.environ.get("PORT", "8000")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def get_updates() -> None:
    """Merge the newest main. Only the app writes data/ and only code comes from GitHub, so this
    doesn't conflict; if git is busy (the app saving data) it tries again next minute."""
    if git("fetch", "-q", "origin", "main").returncode:
        return
    if git("merge", "-q", "--no-edit", "origin/main").returncode:
        git("merge", "--abort")


def code_version() -> str:
    """Everything except the saved data: a change here means a new version of the app."""
    return git("log", "-1", "--format=%H", "--", ".", ":(exclude)data").stdout.strip()


def analysis_running() -> bool:
    db = Path(os.environ.get("ROLODEX_DATA_DIR", ROOT / "data")) / "rolodex.db"
    try:
        with sqlite3.connect(db, timeout=5) as conn:
            row = conn.execute("SELECT state FROM analysis WHERE id = 1").fetchone()
        return bool(row) and row[0] == "running"
    except sqlite3.Error:
        return False


def start_app() -> subprocess.Popen:
    print(f"\nRunning version {git('log', '--oneline', '-1').stdout.strip()}")
    print(f"Open http://localhost:{PORT} in your browser. Leave this window open.\n", flush=True)
    env = {**os.environ, "ROLODEX_GIT_SYNC": os.environ.get("ROLODEX_GIT_SYNC", "1")}
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "rolodex.app:app", "--host", HOST, "--port", PORT],
                            cwd=ROOT, env=env)


def stop_app(app: subprocess.Popen) -> None:
    app.terminate()
    try:
        app.wait(15)
    except subprocess.TimeoutExpired:
        app.kill()


def main() -> int:
    running = code_version()
    app = start_app()
    try:
        while True:
            time.sleep(CHECK_SECONDS)
            get_updates()
            if code_version() != running:
                if analysis_running():
                    print("New version waiting; restarting after the analysis finishes.", flush=True)
                else:
                    print(f"\nNew version: {git('log', '--oneline', '-1').stdout.strip()}. Restarting...", flush=True)
                    stop_app(app)
                    return RESTART
            if app.poll() is not None:
                print("The app stopped; starting it again.", flush=True)
                time.sleep(3)
                app = start_app()
    except KeyboardInterrupt:
        stop_app(app)
        return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["update"]:
        get_updates()
        sys.exit(0)
    sys.exit(main())
