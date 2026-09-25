"""Bring the suppliers from the old claude.ai page into this app's database (one-off).

    python tools/import_artifact.py EXPORT_DIR

EXPORT_DIR holds what `ArtifactData list ... out_dir=EXPORT_DIR` saved: suppliers/<id>.json,
suppliers/<id>/catalog/<section>.json and config/categories.json, plus assets/<asset id>.<ext> for
the card photos (`Artifact read path=<asset id>`). Suppliers already here (same company) are skipped.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rolodex import config, db  # noqa: E402


def load(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("data", d)


def ts(value) -> str | None:
    return str(value).replace("Z", "")[:19] if value else None


def main(export: Path) -> None:
    db.init()
    cats = export / "config" / "categories.json"
    if cats.exists():
        for c in load(cats).get("list", []):
            db.add_category(c["name"], c.get("description", ""))
    have = {s["company"].lower() for s in db.all_suppliers()}
    for f in sorted((export / "suppliers").glob("*.json")):
        x = load(f)
        if x.get("company", "").lower() in have:
            print("skip (already here):", x.get("company"))
            continue
        sid = db.create_supplier({k: x.get(k, "") or "" for k in db.EDITABLE if k != "categories"} | {"categories": x.get("categories", [])})
        profile = x.get("profile") or {}
        for p in profile.get("products", []):
            p.pop("image_asset", None)
            for k in ("sheet", "i", "n"):
                p.pop(k, None)
        db.update_supplier(sid, profile=profile, tags=x.get("tags", []), staff_tags=x.get("staff_tags", []),
                           removed_tags=x.get("removed_tags", []), status=x.get("status") or "active",
                           needs_attention=1 if x.get("needs_attention") else 0, attention_note=x.get("attention_note", ""),
                           research_error=x.get("research_error", ""), created_at=ts(x.get("created_at")) or db.now(),
                           last_checked=ts(x.get("last_checked")), next_check=x.get("next_check"))
        with db.connect() as conn:
            for n in x.get("notes", []):
                conn.execute("INSERT INTO notes (supplier_id, created_at, author, text) VALUES (?, ?, ?, ?)",
                             (sid, ts(n.get("at")) or db.now(), n.get("author", ""), n.get("text", "")))
            for c in x.get("checks", []):
                conn.execute("INSERT INTO checks (supplier_id, checked_at, result, changes) VALUES (?, ?, ?, ?)",
                             (sid, ts(c.get("at")) or db.now(), "{}", c.get("changes", "")))
            for d in x.get("docs", []):
                photos = []
                for asset in d.get("photos", []):
                    src = next((export / "assets").glob(asset + ".*"), None)
                    if src:
                        shutil.copyfile(src, config.CARDS_DIR / (asset + src.suffix))
                        photos.append(asset + src.suffix)
                if not photos:
                    continue
                conn.execute("INSERT INTO cards (supplier_id, front, pages, kind, title, pdf_url, raw, read_at, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (sid, photos[0], json.dumps(photos[1:]), d.get("kind", "card"), d.get("title", ""),
                              d.get("pdf_url", ""), json.dumps(d.get("raw") or {}), ts(d.get("read_at")),
                              ts(d.get("added_at")) or db.now()))
        sections, products = [], []
        docs = [(p.stem, load(p)) for p in (export / "suppliers" / f.stem / "catalog").glob("*.json")]
        for doc_id, sec in sorted(docs, key=lambda t: (t[1].get("order", 0), t[1].get("name", ""))):
            if not sec.get("part_of"):
                sections.append({"id": doc_id, "name": sec.get("name", doc_id), "parent_id": sec.get("parent_id", "")})
            for p in sec.get("products", []):
                products.append({**p, "section_id": sec.get("part_of") or doc_id, "image_url": p.get("image_src", "")})
        if sections or products:
            summary = db.save_catalog(sid, {"source": (x.get("catalog") or {}).get("source", ""),
                                            "note": (x.get("catalog") or {}).get("note", ""),
                                            "sections": sections, "products": products})
            crawled = ts((x.get("catalog") or {}).get("crawled_at"))
            if crawled:
                db.update_supplier(sid, catalog={**summary, "crawled_at": crawled})
        print(f"imported {x.get('company')}: {len(x.get('docs', []))} card(s), {len(products)} catalog products")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
