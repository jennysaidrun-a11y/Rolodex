"""Research suppliers and keep every profile no older than RECHECK_DAYS (default 90).

Runs inside the web app as a background thread, or on its own from a scheduled task:

    python -m rolodex.recheck          # research everything that's due, then exit
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

from . import claude, db

log = logging.getLogger("rolodex.recheck")
_lock = threading.Lock()   # one research run at a time keeps API spend and rate limits predictable


_wake = threading.Event()
_loop_running = False


def research_one(supplier_id: int) -> None:
    with _lock:
        s = db.get_supplier(supplier_id)
        # Re-read under the lock: another run may have just researched it.
        if s is None or s["status"] == "new" or (s["next_check"] or "9999") > date.today().isoformat():
            return
        db.update_supplier(supplier_id, status="researching")
        try:
            notes = [n["text"] for n in db.notes_for(supplier_id)]
            result = claude.research(s, notes, db.tag_vocabulary())
        except Exception as e:   # keep the loop alive; the error shows on the profile page
            log.exception("research failed for supplier %s", supplier_id)
            msg = str(e) if isinstance(e, claude.ClaudeError) else f"Unexpected error: {e}"
            db.update_supplier(supplier_id, status="error", research_error=msg,
                               next_check=(date.today() + timedelta(days=1)).isoformat())
            return
        # Research may confirm categories the card didn't show; keep the ones staff set too.
        merged = list(dict.fromkeys(s["categories"] + result.get("categories", [])))
        db.update_supplier(supplier_id, categories=merged)
        db.record_check(supplier_id, result)


def run_due() -> int:
    due = db.due_for_recheck()
    for s in due:
        research_one(s["id"])
    return len(due)


def start_background(interval_seconds: int = 900) -> None:
    """Check for due suppliers every 15 minutes, or right away when something is queued."""
    global _loop_running

    def loop():
        while True:
            try:
                run_due()
            except Exception:
                log.exception("recheck loop error")
            _wake.wait(interval_seconds)
            _wake.clear()
    _loop_running = True
    threading.Thread(target=loop, name="recheck", daemon=True).start()


def queue(supplier_id: int) -> None:
    """Research this supplier as soon as possible."""
    db.update_supplier(supplier_id, status="queued", next_check=date.today().isoformat())
    if _loop_running:
        _wake.set()
    else:
        threading.Thread(target=research_one, args=(supplier_id,), daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init()
    print(f"Researched {run_due()} supplier(s).")
