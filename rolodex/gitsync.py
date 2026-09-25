"""Keep the rolodex's data in GitHub, so a Codespace can be deleted or rebuilt without losing it.

Every few minutes (and right after an analysis run) this writes a consistent copy of the database
to data/rolodex-backup.db and commits it with any new card photos and PDFs, then pulls and pushes.
Only the app writes these files, so pulls never conflict with them.
"""

from __future__ import annotations

import logging
import subprocess
import threading

from . import config, db

log = logging.getLogger("rolodex.gitsync")
_lock = threading.Lock()
_wake = threading.Event()
last_result = {"at": "", "ok": True, "message": "Not saved to GitHub yet."}

TRACKED = ["data/rolodex-backup.db", "data/cards", "data/docs"]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=config.ROOT, capture_output=True, text=True, timeout=120)


def sync_now() -> dict:
    """Commit the data folder and push it. Returns {"ok", "message"}."""
    global last_result
    with _lock:
        try:
            db.backup()
            _git("add", "--", *[p for p in TRACKED if (config.ROOT / p).exists()])
            if _git("diff", "--cached", "--quiet").returncode == 0 and not _ahead():
                last_result = {"at": db.now(), "ok": True, "message": "Saved to GitHub (no changes)."}
                return last_result
            if _git("diff", "--cached", "--quiet").returncode != 0:
                done = _git("-c", "user.name=Supplier Rolodex", "-c", "user.email=rolodex@users.noreply.github.com",
                            "commit", "-q", "-m", f"Rolodex data {db.now()}")
                if done.returncode:
                    raise RuntimeError(done.stderr.strip() or done.stdout.strip())
            pull = _git("pull", "-q", "--no-rebase", "--no-edit")
            if pull.returncode:
                _git("merge", "--abort")
                raise RuntimeError("git pull failed: " + (pull.stderr.strip() or pull.stdout.strip()))
            push = _git("push", "-q")
            if push.returncode:
                raise RuntimeError("git push failed: " + (push.stderr.strip() or push.stdout.strip()))
            last_result = {"at": db.now(), "ok": True, "message": "Saved to GitHub."}
        except Exception as e:
            log.warning("git sync failed: %s", e)
            last_result = {"at": db.now(), "ok": False, "message": f"Couldn't save to GitHub: {e}"}
        return last_result


def _ahead() -> bool:
    r = _git("rev-list", "--count", "@{u}..HEAD")
    return r.returncode == 0 and r.stdout.strip() not in ("", "0")


def request() -> None:
    """Save soon (e.g. after an analysis run finished)."""
    _wake.set()


def start_background() -> None:
    def loop():
        while True:
            _wake.wait(config.GIT_SYNC_SECONDS)
            _wake.clear()
            sync_now()
    threading.Thread(target=loop, name="gitsync", daemon=True).start()
