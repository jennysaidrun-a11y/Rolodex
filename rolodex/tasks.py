"""The bridge between the app and Claude Code (used by the /analyze and /find-supplier skills).

    python -m rolodex.tasks list                   what's waiting: cards/pamphlets to read, suppliers to research
    python -m rolodex.tasks show card <id>         photos, instructions and output schema for one card/pamphlet
    python -m rolodex.tasks show research <id>     instructions and output schema for one supplier
    python -m rolodex.tasks save card <id> FILE    save a reading (JSON file, or - for stdin)
    python -m rolodex.tasks save research <id> FILE
    python -m rolodex.tasks fail <supplier id> MESSAGE
    python -m rolodex.tasks save catalog <id> FILE  save a hand-made catalog (see the analyze skill)
    python -m rolodex.tasks no-catalog <id> WHY     they have no product list online
    python -m rolodex.tasks begin                   start of a run (shows the progress bar in the app)
    python -m rolodex.tasks current <supplier id>   the run is now on this supplier
    python -m rolodex.tasks progress <supplier id> <step> <of> LABEL
    python -m rolodex.tasks finish SUMMARY          end of a run: one or two sentences for the app
    python -m rolodex.tasks directory              every supplier, compact JSON, for answering questions

Everything prints JSON. `save` checks the JSON against the schema and prints what's wrong if it
doesn't fit, so it can be fixed and saved again.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta

from . import claude, config, db, recheck


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def validate(value, schema: dict, path: str = "$") -> list[str]:
    """Check value against the (small) JSON Schema subset the rolodex uses."""
    kind = schema.get("type")
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} is not one of {schema['enum']}"]
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected an object"]
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: missing")
        if schema.get("additionalProperties") is False:
            errors += [f"{path}.{k}: not an allowed field" for k in value if k not in schema["properties"]]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += validate(value[key], sub, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            return [f"{path}: expected a list"]
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    elif kind == "string" and not isinstance(value, str):
        errors.append(f"{path}: expected text")
    elif kind == "boolean" and not isinstance(value, bool):
        errors.append(f"{path}: expected true/false")
    elif kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        errors.append(f"{path}: expected a whole number")
    return errors


def cmd_list() -> None:
    cards = [{"card_id": c["id"], "supplier_id": c["supplier_id"], "kind": c["kind"], "photos": len(c["photos"]),
              "company": db.get_supplier(c["supplier_id"])["company"]} for c in db.unread_cards()]
    unread = {c["supplier_id"] for c in cards}
    research = [{"supplier_id": s["id"], "company": s["company"], "website": s["website"],
                 "reason": "first research" if not s["last_checked"] else f"recheck: look for what's new since {s['last_checked'][:10]}",
                 "catalog": (f"{s['catalog'].get('total', 0)} products, copied {s['catalog'].get('crawled_at', '')[:10]} "
                             f"({'automatically: ' + s['catalog']['method'] if s['catalog'].get('method', 'by hand') != 'by hand' else 'by hand'})")
                            if s["catalog"].get("total") else "none yet"}
                for s in db.due_for_recheck() if s["id"] not in unread]
    _out({"read": cards, "research": research,
          "next_step": "Read every card/pamphlet first (show card <card_id>), then research each supplier "
                       "(show research <supplier_id>)." if cards or research else "Nothing is waiting."})


def cmd_show(kind: str, item_id: int) -> None:
    categories = db.category_list()
    if kind == "card":
        c = db.get_card(item_id)
        if c is None:
            sys.exit(f"No card/pamphlet {item_id}.")
        s = db.get_supplier(c["supplier_id"])
        _out({"card_id": c["id"], "supplier_id": s["id"], "kind": c["kind"],
              "already_on_file": {k: s[k] for k in ("company", "contact_name", "phone", "email", "website")},
              "photos": [str((config.CARDS_DIR / p).resolve()) for p in c["photos"]],
              "instructions": claude.CONTEXT + "\n\n" + claude.card_prompt(categories, c["kind"]),
              "output_schema": claude.card_schema(categories)})
    elif kind == "research":
        s = db.get_supplier(item_id)
        if s is None:
            sys.exit(f"No supplier {item_id}.")
        notes = [n["text"] for n in db.notes_for(item_id)]
        _out({"supplier_id": s["id"], "company": s["company"],
              "instructions": claude.CONTEXT + "\n\n"
              + claude.research_prompt(s, notes, db.tag_vocabulary(), categories, db.pamphlets_for(item_id)),
              "output_schema": claude.research_schema(categories)})
    else:
        sys.exit("show card <id> | show research <id>")


def _read_json(source: str) -> dict:
    text = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        sys.exit(json.dumps({"saved": False, "errors": [f"not valid JSON: {e}"]}))


def cmd_save(kind: str, item_id: int, source: str) -> None:
    data = _read_json(source)
    categories = db.category_list()
    if kind == "card":
        c = db.get_card(item_id)
        if c is None:
            sys.exit(f"No card/pamphlet {item_id}.")
        errors = validate(data, claude.card_schema(categories))
        if errors:
            sys.exit(json.dumps({"saved": False, "errors": errors}, indent=2))
        # A card from a company already in the rolodex goes onto that supplier, which is then checked
        # for anything new (no duplicate entry, no warning).
        same = db.possible_duplicates(data.get("company", ""), data.get("website", ""), data.get("email", ""),
                                      exclude=c["supplier_id"], phone=data.get("phone", ""))
        merged_into = None
        if same:
            others = [x for x in db.cards_for(c["supplier_id"]) if x["id"] != item_id]
            if others:   # the new entry has other photos too: move just this one
                with db.connect() as conn:
                    conn.execute("UPDATE cards SET supplier_id = ? WHERE id = ?", (same[0]["id"], item_id))
            else:
                db.merge_supplier(c["supplier_id"], same[0]["id"])
            merged_into = same[0]["id"]
        supplier_id = recheck.save_reading(item_id, data)
        # Research it (or refresh it with what the card or pamphlet added) on this same run.
        db.update_supplier(supplier_id, status="queued", next_check=date.today().isoformat())
        a = db.analysis()
        if a["state"] == "running":
            db.update_analysis(planned=list(dict.fromkeys([i for i in a["planned"] if db.get_supplier(i)] + [supplier_id])))
        _out({"saved": True, "supplier_id": supplier_id,
              **({"merged_into_existing": db.get_supplier(supplier_id)["company"]} if merged_into else {}),
              "next_step": f"show research {supplier_id}"})
    elif kind == "research":
        if db.get_supplier(item_id) is None:
            sys.exit(f"No supplier {item_id}.")
        errors = validate(data, claude.research_schema(categories))
        if errors:
            sys.exit(json.dumps({"saved": False, "errors": errors}, indent=2))
        recheck.save_research(item_id, data)
        db.update_supplier(item_id, progress={"step": 6, "of": 6, "label": "Copying their catalog"})
        s = db.get_supplier(item_id)
        _out({"saved": True, "supplier_id": item_id, "next_check": s["next_check"],
              "needs_attention": bool(s["needs_attention"])})
    elif kind == "catalog":
        if db.get_supplier(item_id) is None:
            sys.exit(f"No supplier {item_id}.")
        errors = validate(data, CATALOG_SCHEMA)
        if errors:
            sys.exit(json.dumps({"saved": False, "errors": errors[:30]}, indent=2))
        summary = db.save_catalog(item_id, data)
        db.update_supplier(item_id, progress="")
        db.analysis_finished(item_id)
        _out({"saved": True, **summary})
    else:
        sys.exit("save card <id> FILE | save research <id> FILE | save catalog <id> FILE")


_S = {"type": "string"}
CATALOG_SCHEMA = {
    "type": "object", "required": ["source", "sections", "products"],
    "properties": {
        "source": _S, "note": _S,
        "sections": {"type": "array", "items": {"type": "object", "required": ["id", "name", "parent_id"],
                                                  "properties": {"id": _S, "name": _S, "parent_id": _S}}},
        "products": {"type": "array", "items": {
            "type": "object", "required": ["id", "section_id", "name", "image_url", "page_url"],
            "properties": {"id": _S, "section_id": _S, "name": _S, "sku": _S, "details": _S, "description": _S,
                           "price": _S, "page_url": _S, "image_url": _S,
                           "images": {"type": "array", "items": _S}}}},
    },
}


def _cancelled() -> None:
    if db.analysis()["state"] == "cancelled":
        sys.exit(json.dumps({"cancelled": True, "next_step": "The run was cancelled in the app. Stop now."}))


def cmd_fail(supplier_id: int, message: str) -> None:
    db.update_supplier(supplier_id, status="error", research_error=message, progress="",
                       next_check=(date.today() + timedelta(days=1)).isoformat())
    db.analysis_finished(supplier_id)
    _out({"saved": True, "supplier_id": supplier_id, "status": "error"})


def cmd_directory() -> None:
    suppliers = db.all_suppliers()
    notes = {s["id"]: [n["text"] for n in db.notes_for(s["id"])] for s in suppliers}
    print(claude.directory(suppliers, notes))


def main(argv: list[str]) -> None:
    db.init()
    match argv:
        case ["list"]:
            cmd_list()
        case ["show", kind, item_id]:
            cmd_show(kind, int(item_id))
        case ["save", kind, item_id, source]:
            cmd_save(kind, int(item_id), source)
        case ["fail", supplier_id, *message]:
            cmd_fail(int(supplier_id), " ".join(message) or "Couldn't research this supplier.")
        case ["directory"]:
            cmd_directory()
        case ["no-catalog", supplier_id, *why]:
            s = db.get_supplier(int(supplier_id))
            if s is None:
                sys.exit(f"No supplier {supplier_id}.")
            db.update_supplier(s["id"], progress="", catalog={**s["catalog"], "note": " ".join(why), "checked_at": db.now()})
            db.analysis_finished(s["id"])
            _out({"ok": True})
        case ["begin"]:
            a = db.analysis()
            if a["state"] != "running":   # started by hand (/analyze in a terminal), not by the app
                cards = db.unread_cards()
                planned = list(dict.fromkeys([c["supplier_id"] for c in cards] + [s["id"] for s in db.due_for_recheck()]))
                db.update_analysis(state="running", started_at=db.now(), finished_at=None, planned=planned,
                                   finished=[], current="", summary="", trigger="terminal")
            _out({"ok": True})
        case ["current", supplier_id]:
            _cancelled()
            s = db.get_supplier(int(supplier_id))
            if s is None:
                sys.exit(f"No supplier {supplier_id}.")
            db.update_analysis(current=s["company"])
            _out({"ok": True})
        case ["progress", supplier_id, step, of, *label]:
            _cancelled()
            s = db.get_supplier(int(supplier_id))
            if s is None:
                sys.exit(f"No supplier {supplier_id}.")
            fields = {"progress": {"step": int(step), "of": int(of), "label": " ".join(label)}}
            if int(step) < int(of) and s["status"] not in ("unread",):
                fields["status"] = "researching"   # until the research is saved
            db.update_supplier(s["id"], **fields)
            _out({"ok": True})
        case ["finish", *summary]:
            if db.analysis()["state"] == "running":
                db.update_analysis(state="done", finished_at=db.now(), current="", summary=" ".join(summary))
            _out({"ok": True})
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
