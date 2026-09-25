"""SQLite storage: one file (data/rolodex.db) plus the card photos in data/cards/."""

from __future__ import annotations

import json
import re
import shutil
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
    status TEXT DEFAULT 'new',          -- unread | new | queued | researching | active | closed | error
    needs_attention INTEGER DEFAULT 0,  -- 1 when a check found something to look at
    attention_note TEXT DEFAULT '',
    research_error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    last_checked TEXT,
    next_check TEXT
);
CREATE TABLE IF NOT EXISTS category_list (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    description TEXT DEFAULT '',        -- what belongs here; Claude reads it when categorizing
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    front TEXT NOT NULL,                -- first photo
    back TEXT,                          -- back of a card (older rows)
    pages TEXT DEFAULT '[]',            -- JSON list of further photos (card back, pamphlet pages)
    kind TEXT DEFAULT 'card',           -- card | pamphlet (a photo of its cover)
    title TEXT DEFAULT '',              -- pamphlet title read off the cover
    pdf_url TEXT DEFAULT '',            -- where research found the pamphlet's PDF
    pdf_file TEXT DEFAULT '',           -- our saved copy, in data/docs/
    raw TEXT DEFAULT '{}',              -- what Claude read off it
    read_at TEXT,                       -- NULL until Claude has read it
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL,
    result TEXT NOT NULL,
    changes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS catalog_sections (
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    id TEXT NOT NULL,                   -- slug of the section path on their site
    name TEXT NOT NULL,
    parent_id TEXT DEFAULT '',          -- '' for a top-level section
    position INTEGER DEFAULT 0,         -- their order
    PRIMARY KEY (supplier_id, id)
);
CREATE TABLE IF NOT EXISTS catalog_products (
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    id TEXT NOT NULL,                   -- their item number / SKU, or a slug
    section_id TEXT DEFAULT '',
    name TEXT NOT NULL,
    sku TEXT DEFAULT '',
    details TEXT DEFAULT '',            -- one short line: size, pack, material
    description TEXT DEFAULT '',        -- longer text from the product page
    price TEXT DEFAULT '',              -- as listed, with unit ('' if not shown)
    page_url TEXT DEFAULT '',
    image_url TEXT DEFAULT '',          -- main photo on their site (shown through /img, cached)
    images TEXT DEFAULT '[]',           -- JSON list of further photo URLs
    position INTEGER DEFAULT 0,
    PRIMARY KEY (supplier_id, id)
);
CREATE INDEX IF NOT EXISTS catalog_products_section ON catalog_products (supplier_id, section_id, position);
CREATE TABLE IF NOT EXISTS analysis (
    id INTEGER PRIMARY KEY CHECK (id = 1),   -- one row: the current / last Claude Code run
    state TEXT DEFAULT 'idle',          -- idle | running | done | failed | cancelled
    started_at TEXT, finished_at TEXT,
    pid INTEGER,
    planned TEXT DEFAULT '[]',          -- JSON supplier ids this run is working through
    finished TEXT DEFAULT '[]',         -- JSON supplier ids it has finished (researched or failed)
    current TEXT DEFAULT '',            -- what it's on now
    summary TEXT DEFAULT '',
    trigger TEXT DEFAULT ''             -- button | recheck
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    author TEXT DEFAULT '',
    text TEXT NOT NULL
);
"""

DEFAULT_CATEGORIES = [
    ("Flour & grains", "Flour, grains, meals, starches"),
    ("Sweeteners", "Sugar, syrups, honey, sugar substitutes"),
    ("Dairy & eggs", "Milk, butter, cheese, egg products"),
    ("Fats & oils", "Shortening, oils, margarine, release agents"),
    ("Yeast & cultures", "Yeast, sourdough cultures, enzymes, dough conditioners"),
    ("Other ingredients", "Seeds, inclusions, flavors, spices, fillings, toppings"),
    ("Packaging", "Bags, film, boxes, trays, twist ties, clips"),
    ("Labels & printing", "Labels, printed packaging, coding and date printers"),
    ("Sanitation & chemicals", "Cleaning chemicals, CIP, sanitation tools and services"),
    ("Pest control", "Pest management services and supplies"),
    ("Equipment", "Mixers, ovens, dividers, slicers, conveyors, packaging machines"),
    ("Parts & maintenance", "Spare parts, belting, bearings, repair services"),
    ("Pallets & warehouse supplies", "Pallets, stretch wrap, racking, forklifts"),
    ("Freight & logistics", "Trucking, LTL, cold chain, warehousing"),
    ("Uniforms & PPE", "Uniforms, gloves, hairnets, safety gear"),
    ("Food safety & lab testing", "Testing labs, auditors, certification bodies, consultants"),
    ("Utilities & energy", "Gas, electric, water treatment, compressed air"),
    ("Staffing", "Temp and permanent staffing agencies"),
    ("IT & software", "ERP, scheduling, networks, scales and data systems"),
    ("Other services", "Anything else"),
]

TAG_GROUPS = ["Product", "Certification", "Capability", "Service area", "Other"]
# Columns added after the first release; init() adds them to an existing database.
_ADDED_COLUMNS = {
    "suppliers": {"tags": "TEXT DEFAULT '[]'", "staff_tags": "TEXT DEFAULT '[]'", "removed_tags": "TEXT DEFAULT '[]'",
                  "progress": "TEXT DEFAULT ''", "catalog": "TEXT DEFAULT '{}'"},
    "cards": {"pages": "TEXT DEFAULT '[]'", "kind": "TEXT DEFAULT 'card'", "read_at": "TEXT",
              "title": "TEXT DEFAULT ''", "pdf_url": "TEXT DEFAULT ''", "pdf_file": "TEXT DEFAULT ''"},
}

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
    for d in (config.DATA_DIR, config.CARDS_DIR, config.DOCS_DIR, config.CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # A fresh checkout (a new Codespace) starts from the copy saved in git.
    if not config.DB_PATH.exists() and config.BACKUP_PATH.exists():
        shutil.copyfile(config.BACKUP_PATH, config.DB_PATH)
    with connect() as conn:
        conn.executescript(SCHEMA)
        if not conn.execute("SELECT 1 FROM category_list LIMIT 1").fetchone():
            conn.executemany("INSERT INTO category_list (name, description, position) VALUES (?, ?, ?)",
                             [(n, d, i) for i, (n, d) in enumerate(DEFAULT_CATEGORIES)])
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    if (table, col) == ("cards", "read_at"):   # cards from before were read on upload
                        conn.execute("UPDATE cards SET read_at = created_at")
        # A research run cut off by a restart would otherwise stay "researching" forever.
        conn.execute("UPDATE suppliers SET status = 'queued', progress = '' WHERE status = 'researching'")
        conn.execute("INSERT OR IGNORE INTO analysis (id) VALUES (1)")


def backup() -> None:
    """Write a consistent copy of the database to BACKUP_PATH (safe while the app is writing)."""
    tmp = config.BACKUP_PATH.with_suffix(".tmp")
    src = sqlite3.connect(config.DB_PATH, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(config.BACKUP_PATH)


def _supplier(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    s = dict(row)
    s["categories"] = json.loads(s["categories"] or "[]")
    for key in ("tags", "staff_tags", "removed_tags"):
        s[key] = json.loads(s[key] or "[]")
    s["all_tags"] = effective_tags(s)
    s["profile"] = json.loads(s["profile"] or "{}")
    s["progress"] = json.loads(s.get("progress") or "null")
    s["catalog"] = json.loads(s.get("catalog") or "{}")
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
    for key in ("categories", "profile", "tags", "staff_tags", "removed_tags", "progress", "catalog"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE suppliers SET {cols} WHERE id = ?", [*fields.values(), supplier_id])


def delete_supplier(supplier_id: int) -> list[str]:
    """Delete a supplier; returns the card photo file names so the caller can remove them."""
    with connect() as conn:
        files = [f for c in conn.execute("SELECT * FROM cards WHERE supplier_id = ?", (supplier_id,))
                 for f in _card(c)["photos"] + ([c["pdf_file"]] if c["pdf_file"] else [])]
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


def search(q: str = "", categories: list[str] | None = None, attention: bool = False,
           tags: list[str] | None = None) -> list[dict]:
    """Keyword search (every word must appear in the profile or notes), narrowed by categories
    (a supplier in ANY selected category), the needs-attention flag and tags (must have EVERY tag)."""
    wanted = {t.lower() for t in tags or []}
    cats = {c.lower() for c in categories or []}
    suppliers = all_suppliers()
    with connect() as conn:
        notes: dict[int, list[str]] = {}
        for r in conn.execute("SELECT supplier_id, text FROM notes"):
            notes.setdefault(r["supplier_id"], []).append(r["text"])
    words = [w for w in re.split(r"\s+", q.lower().strip()) if w]
    out = []
    for s in suppliers:
        if cats and not cats & {c.lower() for c in s["categories"]}:
            continue
        if attention and not s["needs_attention"]:
            continue
        if wanted - {t["name"].lower() for t in s["all_tags"]}:
            continue
        hay = _haystack(s, notes.get(s["id"], []))
        if all(w in hay for w in words):
            out.append(s)
    return out


def category_list() -> list[dict]:
    """The managed category list, in display order: [{name, description, count}]."""
    counts: dict[str, int] = {}
    for s in all_suppliers():
        for c in s["categories"]:
            counts[c.lower()] = counts.get(c.lower(), 0) + 1
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT name, description FROM category_list ORDER BY position, name")]
    for r in rows:
        r["count"] = counts.get(r["name"].lower(), 0)
    return rows


def category_names() -> list[str]:
    return [c["name"] for c in category_list()]


def add_category(name: str, description: str = "") -> str:
    """Add to the list (no-op if it exists, any case); returns the name as stored."""
    name = re.sub(r"\s+", " ", name).strip()[:60]
    with connect() as conn:
        row = conn.execute("SELECT name FROM category_list WHERE name = ?", (name,)).fetchone()
        if row:
            return row["name"]
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM category_list").fetchone()[0]
        conn.execute("INSERT INTO category_list (name, description, position) VALUES (?, ?, ?)",
                     (name, description.strip(), pos))
    return name


def _replace_in_suppliers(old: str, new: str | None) -> None:
    for s in all_suppliers():
        if any(c.lower() == old.lower() for c in s["categories"]):
            cats = [new if c.lower() == old.lower() else c for c in s["categories"]]
            update_supplier(s["id"], categories=list(dict.fromkeys(c for c in cats if c)))


def update_category(old: str, new: str, description: str) -> None:
    """Rename and/or re-describe; a rename carries over to every supplier. Renaming onto an
    existing category merges the two."""
    new = re.sub(r"\s+", " ", new).strip()[:60] or old
    with connect() as conn:
        clash = conn.execute("SELECT name FROM category_list WHERE name = ?", (new,)).fetchone()
        if clash and clash["name"].lower() != old.lower():
            conn.execute("DELETE FROM category_list WHERE name = ?", (old,))
            new = clash["name"]
        else:
            conn.execute("UPDATE category_list SET name = ?, description = ? WHERE name = ?",
                         (new, description.strip(), old))
    _replace_in_suppliers(old, new)


def delete_category(name: str) -> None:
    """Remove from the list and from every supplier (the suppliers themselves stay)."""
    with connect() as conn:
        conn.execute("DELETE FROM category_list WHERE name = ?", (name,))
    _replace_in_suppliers(name, None)


def move_category(name: str, step: int) -> None:
    names = category_names()
    i = next((k for k, n in enumerate(names) if n.lower() == name.lower()), None)
    if i is None or not 0 <= i + step < len(names):
        return
    names[i], names[i + step] = names[i + step], names[i]
    with connect() as conn:
        conn.executemany("UPDATE category_list SET position = ? WHERE name = ?", list(enumerate(names)))


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


FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com", "live.com", "msn.com"}


def _company_key(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    words = [w for w in name.split() if w not in ("inc", "llc", "co", "corp", "corporation", "company", "ltd", "the")]
    return "".join(words)


def _phones(text: str) -> set[str]:
    return {d[-10:] for d in (re.sub(r"\D", "", p) for p in re.split(r"[/,;]", text or "")) if len(d) >= 10}


def possible_duplicates(company: str, website: str = "", email: str = "", exclude: int | None = None,
                        phone: str = "") -> list[dict]:
    """Suppliers that look like the same business: same name (ignoring Inc, LLC...), website
    domain, company email domain or phone number."""
    name = _company_key(company)
    email_domain = (email or "").lower().partition("@")[2]
    domains = {d for d in (_domain(website), "" if email_domain in FREE_MAIL else email_domain) if d}
    phones = _phones(phone)
    out = []
    for s in all_suppliers():
        if s["id"] == exclude:
            continue
        s_email = s["email"].lower().partition("@")[2]
        s_domains = {d for d in (_domain(s["website"]), "" if s_email in FREE_MAIL else s_email) if d}
        if (len(name) > 3 and name == _company_key(s["company"])) or (domains & s_domains) or (phones & _phones(s["phone"])):
            out.append(s)
    return out


def merge_supplier(source_id: int, target_id: int) -> None:
    """Move a supplier's cards and notes onto another (the same business) and delete it."""
    with connect() as conn:
        conn.execute("UPDATE cards SET supplier_id = ? WHERE supplier_id = ?", (target_id, source_id))
        conn.execute("UPDATE notes SET supplier_id = ? WHERE supplier_id = ?", (target_id, source_id))
        conn.execute("DELETE FROM suppliers WHERE id = ?", (source_id,))


def _card(row: sqlite3.Row) -> dict:
    c = dict(row)
    c["pages"] = json.loads(c.get("pages") or "[]")
    c["raw"] = json.loads(c.get("raw") or "{}")
    c["photos"] = [p for p in [c["front"], c["back"], *c["pages"]] if p]
    return c


def add_card(supplier_id: int, photos: list[str], kind: str = "card", raw: dict | None = None) -> int:
    """Store a business card or pamphlet (its photos in order). Unread until raw is given."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO cards (supplier_id, front, pages, kind, raw, read_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (supplier_id, photos[0], json.dumps(photos[1:]), kind, json.dumps(raw or {}),
             now() if raw is not None else None, now()))
        return cur.lastrowid


def mark_card_read(card_id: int, raw: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE cards SET raw = ?, title = ?, read_at = ? WHERE id = ?",
                     (json.dumps(raw), raw.get("document_title", ""), now(), card_id))


def set_card_pdf(card_id: int, pdf_url: str, pdf_file: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE cards SET pdf_url = ?, pdf_file = ? WHERE id = ?", (pdf_url, pdf_file, card_id))


def pamphlets_for(supplier_id: int) -> list[dict]:
    return [c for c in cards_for(supplier_id) if c["kind"] == "pamphlet"]


def get_card(card_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    return _card(row) if row else None


def unread_cards() -> list[dict]:
    """Cards and pamphlets waiting to be read, oldest first."""
    with connect() as conn:
        return [_card(r) for r in conn.execute("SELECT * FROM cards WHERE read_at IS NULL ORDER BY id")]


def cards_for(supplier_id: int) -> list[dict]:
    with connect() as conn:
        return [_card(r) for r in conn.execute(
            "SELECT * FROM cards WHERE supplier_id = ? ORDER BY id DESC", (supplier_id,))]


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
            "SELECT * FROM suppliers WHERE status NOT IN ('unread', 'new', 'researching') "
            "AND next_check IS NOT NULL AND next_check <= ? ORDER BY next_check, id", (today,))
        return [_supplier(r) for r in rows]


# ---------- product catalogs (a copy of each supplier's own product list) ----------

def save_catalog(supplier_id: int, catalog: dict) -> dict:
    """Replace a supplier's catalog. catalog = {source, note, sections: [{id, name, parent_id}],
    products: [{id, section_id, name, sku, details, description, price, page_url, image_url, images}]}.
    Sections and products keep the order given (their site's order). Returns the summary saved on
    the supplier."""
    sections = [x for x in catalog.get("sections", []) if str(x.get("id", "")).strip() and x.get("name")]
    known = {str(x["id"]) for x in sections}
    products, seen = [], set()
    for p in catalog.get("products", []):
        pid = str(p.get("id") or p.get("sku") or p.get("page_url") or p.get("name") or "").strip()[:180]
        if not pid or not p.get("name"):
            continue
        if pid in seen:   # the same product listed in another section too, as on their site
            pid = f"{pid}~{p.get('section_id', '')}"[:200]
            if pid in seen:
                continue
        seen.add(pid)
        products.append((supplier_id, pid, str(p.get("section_id", "")) if str(p.get("section_id", "")) in known else "",
                         str(p["name"])[:300], str(p.get("sku", ""))[:100], str(p.get("details", ""))[:300],
                         str(p.get("description", ""))[:4000], str(p.get("price", ""))[:100],
                         str(p.get("page_url", "")), str(p.get("image_url", "")),
                         json.dumps([u for u in p.get("images", []) if isinstance(u, str)][:12]), len(products)))
    summary = {"crawled_at": now(), "source": catalog.get("source", ""), "total": len(products),
               "with_photos": sum(1 for p in products if p[9]), "sections": len(sections),
               "note": catalog.get("note", ""), "method": catalog.get("platform") or "by hand"}
    with connect() as conn:
        conn.execute("DELETE FROM catalog_sections WHERE supplier_id = ?", (supplier_id,))
        conn.execute("DELETE FROM catalog_products WHERE supplier_id = ?", (supplier_id,))
        conn.executemany("INSERT OR REPLACE INTO catalog_sections VALUES (?, ?, ?, ?, ?)",
                         [(supplier_id, str(x["id"]), str(x["name"])[:200],
                           str(x.get("parent_id") or "") if str(x.get("parent_id") or "") in known else "", i)
                          for i, x in enumerate(sections)])
        conn.executemany("INSERT INTO catalog_products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", products)
    update_supplier(supplier_id, catalog=summary)
    return summary


def catalog_sections(supplier_id: int) -> list[dict]:
    """Sections in their order, each with `count` = products in it and everything below it."""
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, name, parent_id FROM catalog_sections WHERE supplier_id = ? ORDER BY position", (supplier_id,))]
        direct = dict(conn.execute("SELECT section_id, COUNT(*) FROM catalog_products WHERE supplier_id = ? "
                                   "GROUP BY section_id", (supplier_id,)).fetchall())
    children: dict[str, list[str]] = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r["id"])

    def total(sid: str, depth: int = 0) -> int:
        return direct.get(sid, 0) + (sum(total(c, depth + 1) for c in children.get(sid, [])) if depth < 20 else 0)
    for r in rows:
        r["count"] = total(r["id"])
    return rows


def section_and_below(sections: list[dict], section_id: str) -> list[str]:
    ids, frontier = [], [section_id]
    while frontier and len(ids) < 10000:
        sid = frontier.pop()
        ids.append(sid)
        frontier += [x["id"] for x in sections if x["parent_id"] == sid and x["id"] not in ids]
    return ids


def _product(row: sqlite3.Row) -> dict:
    p = dict(row)
    p["images"] = json.loads(p.get("images") or "[]")
    return p


def catalog_products(supplier_id: int | None = None, section_ids: list[str] | None = None, q: str = "",
                     limit: int = 48, offset: int = 0) -> tuple[list[dict], int]:
    """A page of products (their order) and the total, optionally within sections and matching
    every word of q (name, item number, details, description). supplier_id None searches all."""
    where, args = [], []
    if supplier_id is not None:
        where.append("p.supplier_id = ?")
        args.append(supplier_id)
    if section_ids is not None:
        where.append(f"p.section_id IN ({', '.join('?' * len(section_ids))})")
        args += section_ids
    for w in re.split(r"\s+", q.strip())[:8]:
        if w:
            where.append("(p.name || ' ' || p.sku || ' ' || p.details || ' ' || p.description) LIKE ?")
            args.append(f"%{w}%")
    sql_where = " WHERE " + " AND ".join(where) if where else ""
    order = "s.position, p.position" if supplier_id is not None else "sup.company COLLATE NOCASE, s.position, p.position"
    base = (" FROM catalog_products p LEFT JOIN catalog_sections s ON s.supplier_id = p.supplier_id AND s.id = p.section_id"
            " JOIN suppliers sup ON sup.id = p.supplier_id")
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*)" + base + sql_where, args).fetchone()[0]
        rows = conn.execute("SELECT p.*, sup.company AS company" + base + sql_where + f" ORDER BY {order} LIMIT ? OFFSET ?",
                            [*args, limit, offset]).fetchall()
    return [_product(r) for r in rows], total


def catalog_product(supplier_id: int, product_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM catalog_products WHERE supplier_id = ? AND id = ?",
                           (supplier_id, product_id)).fetchone()
    return _product(row) if row else None


def catalog_neighbours(supplier_id: int, product: dict) -> tuple[str | None, str | None]:
    """Previous and next product ids in the same section."""
    with connect() as conn:
        prev = conn.execute("SELECT id FROM catalog_products WHERE supplier_id = ? AND section_id = ? AND position < ? "
                            "ORDER BY position DESC LIMIT 1", (supplier_id, product["section_id"], product["position"])).fetchone()
        nxt = conn.execute("SELECT id FROM catalog_products WHERE supplier_id = ? AND section_id = ? AND position > ? "
                           "ORDER BY position LIMIT 1", (supplier_id, product["section_id"], product["position"])).fetchone()
    return (prev[0] if prev else None), (nxt[0] if nxt else None)


# ---------- the Claude Code analysis run ----------

def analysis() -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM analysis WHERE id = 1").fetchone()
    a = dict(row) if row else {"state": "idle"}
    a["planned"] = json.loads(a.get("planned") or "[]")
    a["finished"] = json.loads(a.get("finished") or "[]")
    return a


def update_analysis(**fields) -> None:
    for key in ("planned", "finished"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = json.dumps(fields[key])
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO analysis (id) VALUES (1)")
        conn.execute(f"UPDATE analysis SET {cols} WHERE id = 1", list(fields.values()))


def analysis_finished(supplier_id: int) -> None:
    """A running analysis has finished this supplier (for the progress bar)."""
    a = analysis()
    if a["state"] == "running":
        update_analysis(finished=list(dict.fromkeys(a["finished"] + [supplier_id])),
                        planned=list(dict.fromkeys(a["planned"] + [supplier_id])))
