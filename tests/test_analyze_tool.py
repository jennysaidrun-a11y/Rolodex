"""The claude.ai version's /analyze helper, run against an ArtifactData-style export."""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from test_app import CARD, RESEARCH

TOOL = Path(__file__).resolve().parent.parent / "tools" / "analyze.py"


def run(*args, ok=True):
    r = subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True)
    assert (r.returncode == 0) == ok, r.stderr
    return json.loads(r.stdout or r.stderr) if (r.stdout or r.stderr).strip().startswith(("{", "[")) else r.stdout


def export(tmp_path, suppliers):
    (tmp_path / "suppliers").mkdir()
    for sid, doc in suppliers.items():
        (tmp_path / "suppliers" / f"{sid}.json").write_text(json.dumps({"id": sid, "data": doc, "version": 3}))
    return tmp_path


def test_read_then_research_then_directory(tmp_path):
    ex = export(tmp_path, {
        "abc": {"company": "New card", "status": "unread", "categories": [], "notes": [],
                "docs": [{"id": "d1", "kind": "card", "photos": ["a" * 32, "b" * 32], "read_at": None},
                         {"id": "d2", "kind": "pamphlet", "photos": ["c" * 32], "read_at": None}]},
        "old": {"company": "Bag Co", "status": "active", "last_checked": "2026-01-01T00:00:00",
                "next_check": "2026-04-01", "categories": ["Packaging"],
                "tags": [{"group": "Certification", "name": "SQF"}], "docs": [], "notes": []},
        "fresh": {"company": "Later Co", "status": "active", "next_check": "2099-01-01", "docs": []},
    })
    plan = run("plan", ex)
    assert [(r["supplier_id"], r["doc_id"]) for r in plan["read"]] == [("abc", "d1"), ("abc", "d2")]
    assert [r["supplier_id"] for r in plan["research"]] == ["old"]
    assert plan["planned_supplier_ids"] == ["abc", "old"]
    shown = run("show-read", ex, "abc", "d2")
    assert "front cover of a supplier's pamphlet" in shown["instructions"] and shown["photo_asset_ids"] == ["c" * 32]

    bad = tmp_path / "bad.json"; bad.write_text(json.dumps({"company": "X"}))
    assert "$.contact_name: missing" in run("apply-read", ex, "abc", "d1", bad, tmp_path / "u.json", ok=False)["errors"]
    good = tmp_path / "card.json"; good.write_text(json.dumps(CARD))
    res = run("apply-read", ex, "abc", "d1", good, tmp_path / "u1.json")
    assert "(3)" in res["next"]
    update = json.loads((tmp_path / "u1.json").read_text())
    assert update["company"] == "Midwest Flour Co" and update["status"] == "queued"
    assert update["docs"][0]["read_at"] and not update["docs"][1]["read_at"]
    cover = tmp_path / "cover.json"; cover.write_text(json.dumps(dict(CARD, document_title="Bakery Flours 2026")))
    run("apply-read", ex, "abc", "d2", cover, tmp_path / "u2.json")
    assert [r["supplier_id"] for r in run("plan", ex)["research"]] == ["abc", "old"]

    shown = run("show-research", ex, "abc")
    assert "card_id d2: Bakery Flours 2026" in shown["instructions"] and "- Certification: SQF" in shown["instructions"]
    result = tmp_path / "research.json"
    result.write_text(json.dumps(dict(RESEARCH, tags=[{"group": "Certification", "name": "sqf"}],
                                      brochures=[{"card_id": "d2", "title": "Bakery Flours 2026",
                                                  "pdf_url": "https://mwf.example/f.pdf", "summary": "Specs."}])))
    run("apply-research", ex, "abc", result, tmp_path / "u3.json", "--pdf", "d2=" + "e" * 32)
    update = json.loads((tmp_path / "u3.json").read_text())
    assert update["tags"] == [{"group": "Certification", "name": "SQF"}]        # snapped to existing spelling
    assert update["needs_attention"] is True and update["status"] == "active" and update["progress"] is None
    assert update["next_check"] == (date.today() + timedelta(days=90)).isoformat()
    pamphlet = update["docs"][1]
    assert pamphlet["pdf_url"] == "https://mwf.example/f.pdf" and pamphlet["pdf_asset"] == "e" * 32
    assert len(update["checks"]) == 1
    assert [r["supplier_id"] for r in run("plan", ex)["research"]] == ["old"]

    run("fail", ex, "old", tmp_path / "u4.json", "Website", "is", "gone")
    assert json.loads((tmp_path / "u4.json").read_text())["research_error"] == "Website is gone"

    directory = run("directory", ex)
    assert {d["company"] for d in directory} == {"Midwest Flour Co", "Bag Co", "Later Co"}
    assert next(d for d in directory if d["id"] == "abc")["tags"] == ["SQF"]


def test_catalog_photos(tmp_path):
    import http.server
    import threading
    import io
    from PIL import Image
    site = tmp_path / "site"; site.mkdir()
    buf = io.BytesIO(); Image.new("RGB", (400, 300), "tan").save(buf, "JPEG"); (site / "bag.jpg").write_bytes(buf.getvalue() * 2)
    (site / "logo.svg").write_text("<svg/>")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(site), **k)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        (tmp_path / "ex").mkdir()
        ex = export(tmp_path / "ex", {"s1": {"company": "Bag Co", "status": "queued", "next_check": "2000-01-01", "docs": [],
                                            "profile": {"products": [{"name": "Old", "image_asset": "f" * 32}]},
                                            "last_checked": "2026-01-01T00:00:00"}})
        result = tmp_path / "r.json"
        result.write_text(json.dumps(dict(RESEARCH, products=[
            {"name": "Bread bag", "details": "1.5 mil", "image_url": base + "/bag.jpg", "page_url": base + "/bag"},
            {"name": "Logo", "details": "", "image_url": base + "/logo.svg", "page_url": ""},
            {"name": "Twist ties", "details": "", "image_url": "", "page_url": ""}])))
        got = run("fetch-images", result, tmp_path / "img")
        assert [x["product"] for x in got["saved"]] == [0] and got["skipped"][0]["product"] == 1
        assert (tmp_path / "img" / "p0.jpg").exists()
        mapping = tmp_path / "map.json"; mapping.write_text(json.dumps({"0": "a" * 32}))
        out = run("apply-research", ex, "s1", result, tmp_path / "u.json", "--images", mapping)
        products = json.loads((tmp_path / "u.json").read_text())["profile"]["products"]
        assert [p["image_asset"] for p in products] == ["a" * 32, "", ""]
        assert out["old_product_photos_to_delete"] == ["f" * 32]   # the replaced photo, for deletion
    finally:
        srv.shutdown()
