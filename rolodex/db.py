"""SQLite storage: one file (data/rolodex.db) plus the card photos in data/cards/."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL,
    contact_name TEXT DEFAULT '',
    contact_title TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    website TEXT DEFAULT '',
    address TEXT DEFAULT '',
    categories TEXT DEFAULT '[]',       -- JSON list, e.g. ["Flour", "Packaging film"]
    tags TEXT DEFAULT '[]',             -- JSON [{group, name}] from the latest research
    staff_tags TEXT DEFAULT '[]',       -- JSON [{group, name}] added by hand; kept across rescans
    removed_tags TEXT DEFAULT '[]',     -- JSON [name] research tags staff removed; stay removed
    summary TEXT DEFAULT '',            -- one-paragraph "what they do for a bakery"
    profile TEXT DEFAULT '{}',          -- JSON from the latest research check
    status TEXT DEFAULT 'new',          -- new | researching | active | closed | error
    needs_attention INTEGER DEFAULT 0,  -- 1 when a check found something to look at
    attention_note TEXT DEFAULT '',
    research_error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    last_checked TEXT,
    next_check TEXT
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    front TEXT NOT NULL,
    back TEXT,
    raw TEXT DEFAULT '{}',              -- what Claude read off the card
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL,
    result TEXT NOT NULL,
    changes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    author TEXT DEFAULT '',
    text TEXT NOT NULL
);
"""

TAG_GROUPS = ["Product", "Certification", "Capability", "Service area", "Other"]
# Columns added after the first release; init() adds them to an existing database.
_ADDED_COLUMNS = {"tags": "TEXT DEFAULT '[]'", "staff_tags": "TEXT DEFAULT '[]'", "removed_tags": "TEXT DEFAULT '[]'"}

EDITABLE = ("company", "contact_name", "contact_title", "phone", "email", "website", "address",
            "categories", "summary")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CARDS_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(suppliers)")}
        for col, decl in _ADDED_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE suppliers ADD COLUMN {col} {decl}")
        # A research run cut off by a restart would otherwise stay "researching" forever.
        conn.execute("UPDATE suppliers SET status = 'queued' WHERE status = 'researching'")


def _supplier(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    s = dict(row)
    s["categories"] = json.loads(s["categories"] or "[]")
    for key in ("tags", "staff_tags", "removed_tags"):
        s[key] = json.loads(s[key] or "[]")
    s["all_tags"] = effective_tags(s)
    s["profile"] = json.loads(s["profile"] or "{}")
    return s


def create_supplier(fields: dict) -> int:
    data = {k: fields.get(k, "") for k in EDITABLE}
    data["categories"] = json.dumps(fields.get("categories") or [])
    data["company"] = data["company"] or "Unknown company"
    with connect() as conn:
        cur = conn.execute(
            f"INSERT INTO suppliers ({', '.join(data)}, created_at) VALUES ({', '.join('?' * len(data))}, ?)",
            [*data.values(), now()])
        return cur.lastrowid


def update_supplier(supplier_id: int, **fields) -> None:
    if not fields:
        return
    for key in ("categories", "profile", "tags", "staff_tags", "removed_tags"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE suppliers SET {cols} WHERE id = ?", [*fields.values(), supplier_id])


def delete_supplier(supplier_id: int) -> list[str]:
    """Delete a supplier; returns the card photo file names so the caller can remove them."""
    with connect() as conn:
        files = [f for c in conn.execute("SELECT front, back FROM cards WHERE supplier_id = ?", (supplier_id,))
                 for f in (c["front"], c["back"]) if f]
        conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
    return files


def get_supplier(supplier_id: int) -> dict | None:
    with connect() as conn:
        return _supplier(conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone())


def all_suppliers() -> list[dict]:
    with connect() as conn:
        return [_supplier(r) for r in conn.execute("SELECT * FROM suppliers ORDER BY company COLLATE NOCASE")]


def _haystack(s: dict, notes: list[str]) -> str:
    p = s["profile"]
    parts = [s["company"], s["contact_name"], s["address"], s["website"], s["summary"],
             " ".join(s["categories"]), " ".join(t["name"] for t in s["all_tags"]), json.dumps(p.get("products", [])), json.dumps(p.get("locations", [])),
             json.dumps(p.get("certifications", [])), " ".join(notes)]
    return " ".join(parts).lower()


def search(q: str = "", category: str = "", attention: bool = False, tags: list[str] | None = None) -> list[dict]:
    """Keyword search (every word must appear in the profile or notes), narrowed by category,
    the needs-attention flag and tags (a supplier must have every selected tag)."""
    wanted = {t.lower() for t in tags or []}
    suppliers = all_suppliers()
    with connect() as conn:
        notes: dict[int, list[str]] = {}
        for r in conn.execute("SELECT supplier_id, text FROM notes"):
            notes.setdefault(r["supplier_id"], []).append(r["text"])
    words = [w for w in re.split(r"\s+", q.lower().strip()) if w]
    out = []
    for s in suppliers:
        if category and category not in s["categories"]:
            continue
        if attention and not s["needs_attention"]:
            continue
        if wanted - {t["name"].lower() for t in s["all_tags"]}:
            continue
        hay = _haystack(s, notes.get(s["id"], []))
        if all(w in hay for w in words):
            out.append(s)
    return out


def categories() -> list[str]:
    return sorted({c for s in all_suppliers() for c in s["categories"]}, key=str.lower)


def effective_tags(s: dict) -> list[dict]:
    """Research tags minus the ones staff removed, plus staff's own; one entry per name."""
    removed = {n.lower() for n in s["removed_tags"]}
    out: dict[str, dict] = {}
    for t in s["tags"] + s["staff_tags"]:
        key = t["name"].lower()
        if key not in out and (key not in removed or t in s["staff_tags"]):
            out[key] = t
    return sorted(out.values(), key=lambda t: (TAG_GROUPS.index(t["group"]) if t["group"] in TAG_GROUPS
                                                else len(TAG_GROUPS), t["name"].lower()))


def tag_counts() -> dict[str, list[tuple[str, int]]]:
    """Every tag in use, grouped, with how many suppliers have it (for the filter)."""
    counts: dict[tuple[str, str], int] = {}
    for s in all_suppliers():
        for t in s["all_tags"]:
            counts[(t["group"], t["name"])] = counts.get((t["group"], t["name"]), 0) + 1
    grouped: dict[str, list[tuple[str, int]]] = {}
    for (group, name), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0][1].lower())):
        grouped.setdefault(group, []).append((name, n))
    return {g: grouped[g] for g in TAG_GROUPS + sorted(set(grouped) - set(TAG_GROUPS)) if g in grouped}


def tag_vocabulary() -> list[dict]:
    """Tags already in use, so research reuses the same spelling instead of inventing near-duplicates."""
    seen: dict[str, dict] = {}
    for s in all_suppliers():
        for t in s["tags"] + s["staff_tags"]:
            seen.setdefault(t["name"].lower(), {"group": t["group"], "name": t["name"]})
    return sorted(seen.values(), key=lambda t: (t["group"], t["name"].lower()))


def clean_tags(tags: list[dict], vocabulary: list[dict] | None = None) -> list[dict]:
    """Trim, drop blanks and duplicates, and snap to the existing spelling of a tag (case-insensitive)."""
    known = {t["name"].lower(): t for t in (vocabulary if vocabulary is not None else tag_vocabulary())}
    out: dict[str, dict] = {}
    for t in tags:
        name = re.sub(r"\s+", " ", str(t.get("name", ""))).strip()[:40]
        if not name:
            continue
        group = t.get("group") if t.get("group") in TAG_GROUPS else "Other"
        out.setdefault(name.lower(), known.get(name.lower(), {"group": group, "name": name}))
    return list(out.values())


def _domain(url: str) -> str:
    url = re.sub(r"^https?://", "", (url or "").lower().strip())
    return url.removeprefix("www.").split("/")[0]


def possible_duplicates(company: str, website: str = "", email: str = "", exclude: int | None = None) -> list[dict]:
    """Suppliers that look like the same business (same website/email domain or same name)."""
    name = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    domains = {d for d in (_domain(website), (email or "").lower().partition("@")[2]) if d}
    out = []
    for s in all_suppliers():
        if s["id"] == exclude:
            continue
        s_name = re.sub(r"[^a-z0-9]", "", s["company"].lower())
        s_domains = {d for d in (_domain(s["website"]), s["email"].lower().partition("@")[2]) if d}
        if (name and len(name) > 3 and name == s_name) or (domains & s_domains):
            out.append(s)
    return out


def add_card(supplier_id: int, front: str, back: str | None, raw: dict) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO cards (supplier_id, front, back, raw, created_at) VALUES (?, ?, ?, ?, ?)",
                     (supplier_id, front, back, json.dumps(raw), now()))


def cards_for(supplier_id: int) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cards WHERE supplier_id = ? ORDER BY created_at DESC", (supplier_id,))]


def add_note(supplier_id: int, text: str, author: str = "") -> None:
    with connect() as conn:
        conn.execute("INSERT INTO notes (supplier_id, created_at, author, text) VALUES (?, ?, ?, ?)",
                     (supplier_id, now(), author, text))


def notes_for(supplier_id: int) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM notes WHERE supplier_id = ? ORDER BY created_at DESC", (supplier_id,))]


def record_check(supplier_id: int, result: dict) -> None:
    """Save a research result as the supplier's current profile and schedule the next recheck."""
    checked = datetime.now()
    changes = "\n".join(result.get("changes_since_last_check", []))
    attention = result.get("needs_attention", False)
    with connect() as conn:
        conn.execute("INSERT INTO checks (supplier_id, checked_at, result, changes) VALUES (?, ?, ?, ?)",
                     (supplier_id, checked.isoformat(timespec="seconds"), json.dumps(result), changes))
    update_supplier(
        supplier_id,
        profile=result,
        tags=clean_tags(result.get("tags", [])),
        summary=result.get("summary", ""),
        status="closed" if result.get("business_status") == "closed" else "active",
        needs_attention=1 if attention else 0,
        attention_note=result.get("attention_reason", "") if attention else "",
        research_error="",
        last_checked=checked.isoformat(timespec="seconds"),
        next_check=(checked + timedelta(days=config.RECHECK_DAYS)).date().isoformat(),
    )


def checks_for(supplier_id: int) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, checked_at, changes FROM checks WHERE supplier_id = ? ORDER BY checked_at DESC",
            (supplier_id,))]


def due_for_recheck() -> list[dict]:
    """Suppliers whose next check date has arrived, oldest first.

    A card that was just read stays 'new' (not due) until someone reviews it; saving the
    review sets next_check to today so it gets researched straight away.
    """
    today = datetime.now().date().isoformat()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM suppliers WHERE status NOT IN ('new', 'researching') "
            "AND next_check IS NOT NULL AND next_check <= ? ORDER BY next_check, id", (today,))
        return [_supplier(r) for r in rows]
