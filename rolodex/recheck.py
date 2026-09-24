"""Research suppliers and keep every profile no older than RECHECK_DAYS (default 90).

Runs inside the web app as a background thread, or on its own from a scheduled task:

    python -m rolodex.recheck          # research everything that's due, then exit
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.request
from datetime import date, timedelta

from . import claude, config, db

log = logging.getLogger("rolodex.recheck")
_lock = threading.Lock()   # one research run at a time keeps API spend and rate limits predictable


_wake = threading.Event()
_loop_running = False


PLACEHOLDER_COMPANY = "New card"


def save_reading(card_id: int, card: dict) -> int:
    """Apply what was read off a card or pamphlet to its supplier; returns the supplier id.
    Fields staff already typed in (or an earlier card filled) are left alone."""
    doc = db.get_card(card_id)
    supplier_id = doc["supplier_id"]
    s = db.get_supplier(supplier_id)
    fields = {k: card[k] for k in ("company", "contact_name", "contact_title", "phone", "email", "website", "address")
              if card.get(k) and (not s[k] or (k == "company" and s[k] == PLACEHOLDER_COMPANY))}
    fields["categories"] = list(dict.fromkeys(s["categories"] + card.get("categories", [])))
    db.update_supplier(supplier_id, **fields)
    db.mark_card_read(card_id, card)
    what = "pamphlet" if doc["kind"] == "pamphlet" else "card"
    if card.get("other_text"):
        db.add_note(supplier_id, f"From the {what}: {card['other_text']}", what)
    if card.get("products_mentioned"):
        db.add_note(supplier_id, f"Products in the {what}: " + ", ".join(card["products_mentioned"]), what)
    return supplier_id


def save_research(supplier_id: int, result: dict) -> None:
    """Apply a research result: new profile, tags, attention flag, next check date."""
    s = db.get_supplier(supplier_id)
    # The first research may find categories the card didn't show. After that, categories
    # belong to staff, so a rescan never re-adds one they removed.
    if not s["last_checked"]:
        merged = list(dict.fromkeys(s["categories"] + result.get("categories", [])))
        db.update_supplier(supplier_id, categories=merged)
    db.record_check(supplier_id, result)
    _save_brochures(supplier_id, result.get("brochures", []))


MAX_PDF_BYTES = 40 * 1024 * 1024


def download_pdf(url: str, name: str) -> str:
    """Save a copy of a PDF into data/docs/; returns the file name, or "" if it isn't a reachable PDF."""
    if not re.match(r"^https?://", url or "", re.I):
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (supplier rolodex)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(MAX_PDF_BYTES + 1)
    except Exception:
        log.warning("couldn't download %s", url)
        return ""
    if not data.startswith(b"%PDF") or len(data) > MAX_PDF_BYTES:
        return ""
    (config.DOCS_DIR / name).write_bytes(data)
    return name


def _save_brochures(supplier_id: int, brochures: list[dict]) -> None:
    """Link each pamphlet to the PDF research found for it, and keep our own copy of it."""
    pamphlets = {c["id"]: c for c in db.pamphlets_for(supplier_id)}
    for b in brochures:
        card = pamphlets.get(b.get("card_id"))
        if card and b.get("pdf_url") and b["pdf_url"] != card["pdf_url"]:
            db.set_card_pdf(card["id"], b["pdf_url"], download_pdf(b["pdf_url"], f"pamphlet-{card['id']}.pdf"))


def research_one(supplier_id: int) -> None:
    with _lock:
        s = db.get_supplier(supplier_id)
        # Re-read under the lock: another run may have just researched it.
        if s is None or s["status"] in ("unread", "new") or (s["next_check"] or "9999") > date.today().isoformat():
            return
        db.update_supplier(supplier_id, status="researching")
        try:
            notes = [n["text"] for n in db.notes_for(supplier_id)]
            result = claude.research(s, notes, db.tag_vocabulary(), db.category_list(),
                                     db.pamphlets_for(supplier_id))
        except Exception as e:   # keep the loop alive; the error shows on the profile page
            log.exception("research failed for supplier %s", supplier_id)
            msg = str(e) if isinstance(e, claude.ClaudeError) else f"Unexpected error: {e}"
            db.update_supplier(supplier_id, status="error", research_error=msg,
                               next_check=(date.today() + timedelta(days=1)).isoformat())
            return
        save_research(supplier_id, result)


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
    if not config.USE_API:
        return   # waits for the next /analyze run in Claude Code
    if _loop_running:
        _wake.set()
    else:
        threading.Thread(target=research_one, args=(supplier_id,), daemon=True).start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init()
    print(f"Researched {run_due()} supplier(s).")
