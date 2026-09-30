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
import threading
import time
import urllib.request
import webbrowser
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
    print(f"Open http://localhost:{PORT} in your browser. Leave this window open (minimized is fine).\n", flush=True)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "ROLODEX_GIT_SYNC": os.environ.get("ROLODEX_GIT_SYNC", "1")}
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "rolodex.app:app", "--host", HOST, "--port", PORT],
                            cwd=ROOT, env=env)


def app_up() -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def desktop_shortcut() -> None:
    """Put a "Supplier Rolodex" icon on the Windows desktop that starts run.bat (once)."""
    if os.name != "nt":
        return
    cache = ROOT / "data" / "cache"
    ico = cache / "rolodex.ico"
    try:
        cache.mkdir(parents=True, exist_ok=True)
        if not ico.exists():
            from PIL import Image
            Image.open(ROOT / "rolodex" / "static" / "icon-512.png").save(ico, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    except Exception:
        ico = None
    script = (
        "$d=[Environment]::GetFolderPath('Desktop'); $p=Join-Path $d 'Supplier Rolodex.lnk';"
        "if (-not (Test-Path $p)) { $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
        f"$s.TargetPath='{ROOT / 'run.bat'}'; $s.WorkingDirectory='{ROOT}'; $s.WindowStyle=7;"
        + (f"$s.IconLocation='{ico}';" if ico else "")
        + "$s.Description='Start the Supplier Rolodex'; $s.Save() }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=60)


def open_window() -> None:
    """Open the Rolodex in its own app window (Edge or Chrome "app mode": no tabs or address bar,
    its own taskbar icon), falling back to a normal browser tab."""
    url = f"http://localhost:{PORT}/"
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            for exe in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                path = Path(base) / exe
                if base and path.exists():
                    subprocess.Popen([str(path), f"--app={url}", "--window-size=1280,900"])
                    return
    webbrowser.open(url)


def open_browser_once() -> None:
    """Open the app in the browser when started from the icon, not again after each update restart
    (run.bat's window, our parent, stays the same across those)."""
    mark = ROOT / "data" / "cache" / "opened-for.txt"
    parent = str(os.getppid())
    try:
        if mark.read_text() == parent:
            return
    except OSError:
        pass
    for _ in range(60):
        if app_up():
            break
        time.sleep(1)
    open_window()
    try:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(parent)
    except OSError:
        pass


def stop_app(app: subprocess.Popen) -> None:
    app.terminate()
    try:
        app.wait(15)
    except subprocess.TimeoutExpired:
        app.kill()


def main() -> int:
    try:
        desktop_shortcut()
    except Exception as e:
        print(f"Couldn't add the desktop icon: {e}", flush=True)
    if app_up():   # already running (the icon was clicked again): just show it
        print("The Rolodex is already running; opening it.", flush=True)
        open_window()
        return 0
    running = code_version()
    app = start_app()
    threading.Thread(target=open_browser_once, daemon=True).start()
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
