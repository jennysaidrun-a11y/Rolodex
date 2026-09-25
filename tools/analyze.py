"""Helper for the /analyze and /find-supplier skills: the claude.ai version of the rolodex.

The rolodex page lives on claude.ai and keeps its records in the page's database
(`suppliers/<id>` documents plus `config/categories`). Claude Code exports them with the
ArtifactData tool (`list` with `out_dir`), runs this script to see what needs doing and to
build each update, and writes the update back with ArtifactData (`update` with `file_path`).
No API key, no packages beyond the Python standard library.

    python tools/analyze.py plan EXPORT_DIR
    python tools/analyze.py show-read EXPORT_DIR SUPPLIER_ID DOC_ID
    python tools/analyze.py apply-read EXPORT_DIR SUPPLIER_ID DOC_ID READING.json OUT.json
    python tools/analyze.py show-research EXPORT_DIR SUPPLIER_ID
    python tools/analyze.py apply-research EXPORT_DIR SUPPLIER_ID RESULT.json OUT.json [--pdf DOC_ID=ASSET_ID ...]
    python tools/analyze.py fail EXPORT_DIR SUPPLIER_ID OUT.json MESSAGE
    python tools/analyze.py directory EXPORT_DIR

EXPORT_DIR is the `out_dir` given to ArtifactData: it holds `suppliers/<id>.json` and
`config/categories.json`. apply-* also update the exported file, so later steps see the change.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rolodex import claude, db   # noqa: E402  (prompts, schemas, tag helpers; no database is opened)
from rolodex.tasks import validate   # noqa: E402

RECHECK_DAYS = 90
PLACEHOLDER = "New card"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load(path: Path) -> tuple[dict, int | None]:
    """An exported document: {"id", "data", "version"}, or the bare document."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        return raw["data"], raw.get("version")
    return raw, None


def _save(path: Path, doc: dict, version: int | None) -> None:
    path.write_text(json.dumps({"id": path.stem, "data": doc, "version": version}, indent=2), encoding="utf-8")


class Export:
    def __init__(self, root: str):
        self.root = Path(root)
        self.suppliers: dict[str, dict] = {}
        self.versions: dict[str, int | None] = {}
        for f in sorted((self.root / "suppliers").glob("*.json")):
            doc, version = _load(f)
            doc.setdefault("id", f.stem)
            doc["id"] = f.stem
            self.suppliers[f.stem] = doc
            self.versions[f.stem] = version
        cats = self.root / "config" / "categories.json"
        listed = _load(cats)[0].get("list", []) if cats.exists() else []
        self.categories = listed or [{"name": n, "description": d} for n, d in db.DEFAULT_CATEGORIES]

    def supplier(self, sid: str) -> dict:
        if sid not in self.suppliers:
            sys.exit(f"No supplier {sid} in {self.root}/suppliers (export it first).")
        s = self.suppliers[sid]
        for key, default in (("categories", []), ("tags", []), ("staff_tags", []), ("removed_tags", []), ("notes", []),
                             ("docs", []), ("checks", []), ("profile", {})):
            s.setdefault(key, default)
        for key in ("company", "contact_name", "contact_title", "phone", "email", "website", "address", "summary",
                    "status", "attention_note", "last_checked", "next_check"):
            s.setdefault(key, "")
        s.setdefault("needs_attention", False)
        return s

    def store(self, sid: str, patch: dict) -> None:
        self.suppliers[sid].update(patch)
        _save(self.root / "suppliers" / f"{sid}.json", self.suppliers[sid], self.versions.get(sid))

    def vocabulary(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for s in self.suppliers.values():
            for t in s.get("tags", []) + s.get("staff_tags", []):
                seen.setdefault(t["name"].lower(), {"group": t["group"], "name": t["name"]})
        return sorted(seen.values(), key=lambda t: (t["group"], t["name"].lower()))


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _write_update(path: str, patch: dict, version: int | None) -> None:
    Path(path).write_text(json.dumps(patch, indent=2, ensure_ascii=False), encoding="utf-8")
    _out({"update_file": path,
          "next": "ArtifactData update suppliers/<id> with this file_path, and if_version = the version "
                  "the export (or your last update of it) reported" + (f" ({version})" if version else "")})


def _due(s: dict) -> bool:
    return (s.get("status") not in ("unread", "new", "researching") and bool(s.get("next_check"))
            and s["next_check"] <= date.today().isoformat())


def cmd_plan(ex: Export) -> None:
    read, research = [], []
    for sid, s in ex.suppliers.items():
        unread = [d for d in s.get("docs", []) if not d.get("read_at")]
        for d in unread:
            read.append({"supplier_id": sid, "doc_id": d["id"], "kind": d.get("kind", "card"),
                         "photo_asset_ids": d.get("photos", []), "company": s.get("company", "")})
        if not unread and _due(s):
            research.append({"supplier_id": sid, "company": s.get("company", ""),
                             "reason": "queued" if s.get("status") == "queued" else f"due (last {s.get('last_checked', '')[:10]})"})
    _out({"read": read, "research": research,
          "note": "Read every card/pamphlet first; apply-read queues the supplier, so run plan again for research."
          if read else ("Nothing is waiting." if not research else "")})


def cmd_show_read(ex: Export, sid: str, doc_id: str) -> None:
    s = ex.supplier(sid)
    d = next((d for d in s["docs"] if d["id"] == doc_id), None)
    if d is None:
        sys.exit(f"No card/pamphlet {doc_id} on supplier {sid}.")
    _out({"supplier_id": sid, "doc_id": doc_id, "kind": d.get("kind", "card"),
          "photo_asset_ids": d.get("photos", []),
          "how_to_see_photos": "Artifact action read with the page url and path=<asset id>, then Read the saved file. "
                               "For a card the first photo is the front, the second the back.",
          "instructions": claude.CONTEXT + "\n\n" + claude.card_prompt(ex.categories, d.get("kind", "card")),
          "output_schema": claude.card_schema(ex.categories)})


def cmd_apply_read(ex: Export, sid: str, doc_id: str, reading_file: str, out: str) -> None:
    s = ex.supplier(sid)
    reading = json.loads(Path(reading_file).read_text(encoding="utf-8"))
    errors = validate(reading, claude.card_schema(ex.categories))
    if errors:
        sys.exit(json.dumps({"saved": False, "errors": errors}, indent=2))
    d = next((d for d in s["docs"] if d["id"] == doc_id), None)
    if d is None:
        sys.exit(f"No card/pamphlet {doc_id} on supplier {sid}.")
    patch = {k: reading[k] for k in ("company", "contact_name", "contact_title", "phone", "email", "website", "address")
             if reading.get(k) and (not s[k] or (k == "company" and s[k] == PLACEHOLDER))}
    patch["categories"] = list(dict.fromkeys(s["categories"] + reading.get("categories", [])))
    what = "pamphlet" if d.get("kind") == "pamphlet" else "card"
    notes = list(s["notes"])
    if reading.get("other_text"):
        notes.append({"at": _now(), "author": what, "text": f"From the {what}: {reading['other_text']}"})
    if reading.get("products_mentioned"):
        notes.append({"at": _now(), "author": what,
                      "text": f"Products in the {what}: " + ", ".join(reading["products_mentioned"])})
    patch["notes"] = notes
    patch["docs"] = [dict(x, read_at=_now(), title=reading.get("document_title") or x.get("title", ""), raw=reading)
                     if x["id"] == doc_id else x for x in s["docs"]]
    patch["status"] = "queued"
    patch["next_check"] = date.today().isoformat()
    ex.store(sid, patch)
    _write_update(out, patch, ex.versions.get(sid))


def cmd_show_research(ex: Export, sid: str) -> None:
    s = ex.supplier(sid)
    notes = [n.get("text", "") for n in s["notes"]]
    pamphlets = [{"id": d["id"], "title": d.get("title", "")} for d in s["docs"] if d.get("kind") == "pamphlet"]
    _out({"supplier_id": sid, "company": s["company"],
          "instructions": claude.CONTEXT + "\n\n" + claude.research_prompt(
              s, notes, ex.vocabulary(), ex.categories, pamphlets),
          "output_schema": claude.research_schema(ex.categories)})


def cmd_apply_research(ex: Export, sid: str, result_file: str, out: str, pdfs: dict[str, str]) -> None:
    s = ex.supplier(sid)
    result = json.loads(Path(result_file).read_text(encoding="utf-8"))
    errors = validate(result, claude.research_schema(ex.categories))
    if errors:
        sys.exit(json.dumps({"saved": False, "errors": errors}, indent=2))
    checked = datetime.now()
    attention = bool(result.get("needs_attention"))
    patch = {
        "profile": result,
        "summary": result.get("summary", ""),
        "tags": db.clean_tags(result.get("tags", []), ex.vocabulary()),
        "status": "closed" if result.get("business_status") == "closed" else "active",
        "needs_attention": attention,
        "attention_note": result.get("attention_reason", "") if attention else "",
        "research_error": "",
        "last_checked": checked.isoformat(timespec="seconds"),
        "next_check": (checked + timedelta(days=RECHECK_DAYS)).date().isoformat(),
        "checks": s["checks"] + [{"at": checked.isoformat(timespec="seconds"),
                                  "changes": "; ".join(result.get("changes_since_last_check", []))}],
    }
    if not s["last_checked"]:   # first research may add categories; after that they belong to staff
        patch["categories"] = list(dict.fromkeys(s["categories"] + result.get("categories", [])))
    found = {str(b.get("card_id")): b for b in result.get("brochures", []) if b.get("pdf_url")}
    patch["docs"] = [dict(d, pdf_url=found[d["id"]]["pdf_url"] if d["id"] in found else d.get("pdf_url", ""),
                          pdf_asset=pdfs.get(d["id"], d.get("pdf_asset", "")))
                     if d.get("kind") == "pamphlet" else d for d in s["docs"]]
    ex.store(sid, patch)
    _write_update(out, patch, ex.versions.get(sid))


def cmd_fail(ex: Export, sid: str, out: str, message: str) -> None:
    ex.supplier(sid)
    patch = {"status": "error", "research_error": message,
             "next_check": (date.today() + timedelta(days=1)).isoformat()}
    ex.store(sid, patch)
    _write_update(out, patch, ex.versions.get(sid))


def cmd_directory(ex: Export) -> None:
    suppliers = []
    for sid in ex.suppliers:
        s = dict(ex.supplier(sid))
        s["all_tags"] = db.effective_tags(s)
        suppliers.append(s)
    notes = {s["id"]: [n.get("text", "") for n in s["notes"]] for s in suppliers}
    print(claude.directory(suppliers, notes))


def main(argv: list[str]) -> None:
    pdfs = {}
    if "--pdf" in argv:
        i = argv.index("--pdf")
        for item in argv[i + 1:]:
            doc_id, _, asset = item.partition("=")
            pdfs[doc_id] = asset
        argv = argv[:i]
    match argv:
        case ["plan", root]:
            cmd_plan(Export(root))
        case ["show-read", root, sid, doc_id]:
            cmd_show_read(Export(root), sid, doc_id)
        case ["apply-read", root, sid, doc_id, reading, out]:
            cmd_apply_read(Export(root), sid, doc_id, reading, out)
        case ["show-research", root, sid]:
            cmd_show_research(Export(root), sid)
        case ["apply-research", root, sid, result, out]:
            cmd_apply_research(Export(root), sid, result, out, pdfs)
        case ["fail", root, sid, out, *message]:
            cmd_fail(Export(root), sid, out, " ".join(message) or "Couldn't research this supplier.")
        case ["directory", root]:
            cmd_directory(Export(root))
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
