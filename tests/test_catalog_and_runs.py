"""Catalogs (the store-style browser), repeat cards, Claude Code runs and saving to GitHub."""

import io
import json
import os
import stat
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from rolodex import app as app_module
from rolodex import catalog, config, db, gitsync, images, runner

from test_app import CARD, RESEARCH, photo, run_tasks

CATALOG = {
    "source": "https://bags.example", "note": "",
    "sections": [{"id": "bags", "name": "Bags", "parent_id": ""},
                 {"id": "bread-bags", "name": "Bread Bags", "parent_id": "bags"},
                 {"id": "film", "name": "Film", "parent_id": ""}],
    "products": [{"id": f"BB-{i}", "section_id": "bread-bags", "name": f"Bread bag {i} in", "sku": f"BB-{i}",
                  "details": "1000/case", "price": "$40.00 per case", "page_url": f"https://bags.example/p/{i}",
                  "image_url": f"https://bags.example/img/{i}.jpg", "images": []} for i in range(60)]
                + [{"id": "SF-1", "section_id": "film", "name": "Stretch film 18 in", "sku": "SF-1", "details": "",
                    "price": "", "page_url": "https://bags.example/p/sf", "image_url": "", "images": []}],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": tmp_path, "DB_PATH": tmp_path / "rolodex.db", "CARDS_DIR": tmp_path / "cards",
                       "DOCS_DIR": tmp_path / "docs", "CACHE_DIR": tmp_path / "cache",
                       "BACKUP_PATH": tmp_path / "rolodex-backup.db", "APP_PASSWORD": "", "AUTO_RECHECK": False,
                       "USE_API": False, "GIT_SYNC": False, "CLAUDE_COMMAND": "no-such-claude-command"}.items():
        monkeypatch.setattr(config, key, value)
    monkeypatch.setitem(app_module.templates.env.globals, "use_api", False)
    with TestClient(app_module.app) as c:
        yield c


def new_supplier(**fields) -> int:
    sid = db.create_supplier({"company": "Bag Co", "website": "bags.example", **fields})
    db.update_supplier(sid, status="active")
    return sid


def test_catalog_browser(client, capsys, monkeypatch):
    sid = new_supplier()
    ok, res = run_tasks(capsys, "save", "catalog", str(sid), "-", stdin={"source": "x", "sections": [], "products": [{}]},
                        monkeypatch=monkeypatch)
    assert not ok and any("missing" in e for e in res["errors"])
    ok, res = run_tasks(capsys, "save", "catalog", str(sid), "-", stdin=CATALOG, monkeypatch=monkeypatch)
    assert ok and res["total"] == 61 and res["with_photos"] == 60 and res["sections"] == 3

    page = client.get(f"/supplier/{sid}").text
    assert "Browse all 61 products" in page
    all_page = client.get(f"/supplier/{sid}/catalog").text
    assert "Page 1 of 2" in all_page and all_page.count('class="product"') == 48
    assert "/img?u=https%3A%2F%2Fbags.example%2Fimg%2F0.jpg&amp;w=400" in all_page
    # A top section includes its subsections; counts roll up.
    bags = client.get(f"/supplier/{sid}/catalog?section=bags").text
    assert "60 products" in bags and "Bread Bags" in bags and "Stretch film" not in bags
    assert "1 product" in client.get(f"/supplier/{sid}/catalog?section=film").text
    assert "3 products" in client.get(f"/supplier/{sid}/catalog?q=bag+1+in").text or \
        "products matching" in client.get(f"/supplier/{sid}/catalog?q=bag+1+in").text
    found = client.get(f"/supplier/{sid}/catalog?q=BB-42").text
    assert "1 product" in found and "Bread bag 42 in" in found
    item = client.get(f"/supplier/{sid}/catalog/item/BB-5").text
    assert "Item # BB-5" in item and "$40.00 per case" in item and "Next item" in item and "Bread Bags" in item
    assert client.get(f"/supplier/{sid}/catalog/item/nope").status_code == 404
    # Search across every supplier's catalog.
    assert "Stretch film 18 in" in client.get("/products?q=stretch").text
    # Replacing the catalog drops products that are gone; deleting the supplier drops it all.
    db.save_catalog(sid, {**CATALOG, "products": CATALOG["products"][:2]})
    assert db.catalog_products(sid)[1] == 2
    client.post(f"/supplier/{sid}/delete")
    assert db.catalog_products(None)[1] == 0


def test_image_proxy_only_fetches_public_sites(client, monkeypatch):
    assert images.fetch_image("http://127.0.0.1:8000/secret.png") is None
    assert images.fetch_image("http://169.254.169.254/latest/meta-data") is None
    assert images.fetch_image("file:///etc/passwd") is None
    r = client.get("/img", params={"u": "http://localhost/x.jpg"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")

    # A public photo is fetched once, resized and served from the cache after that.
    calls = []

    class Resp:
        def __init__(self, data): self.data = data
        def read(self, n): return self.data
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(images, "_public_host", lambda url: True)
    monkeypatch.setattr(images._opener, "open", lambda req, timeout: calls.append(req.full_url) or Resp(photo()))
    r = client.get("/img", params={"u": "https://bags.example/big.jpg", "w": 400})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    from PIL import Image
    import io
    assert max(Image.open(io.BytesIO(r.content)).size) == 400
    client.get("/img", params={"u": "https://bags.example/big.jpg", "w": 800})
    assert len(calls) == 1
    # Sites that send AVIF (whatever the file is called) still show, as a JPEG.
    from PIL import features
    if features.check("avif"):
        buf = io.BytesIO()
        Image.new("RGB", (900, 600), "orange").save(buf, "AVIF")
        monkeypatch.setattr(images._opener, "open", lambda req, timeout: Resp(buf.getvalue()))
        r = client.get("/img", params={"u": "https://bags.example/jug.jpg?box_crop=800,800", "w": 400})
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        r = client.get("/img", params={"u": "https://bags.example/jug.jpg?box_crop=800,800", "w": 0})
        assert r.headers["content-type"] == "image/jpeg"


def test_repeat_card_goes_onto_existing_supplier(client, capsys, monkeypatch):
    sid = new_supplier(company="Midwest Flour Co", website="www.midwestflour.com")
    db.update_supplier(sid, last_checked="2026-06-01T10:00:00", next_check="2026-09-01")
    r = client.post("/add", data={"kind": "card"}, files={"front": ("f.jpg", photo(), "image/jpeg")},
                    follow_redirects=False)
    new_id = int(r.headers["location"].split("/")[2])
    ok, pending = run_tasks(capsys, "list")
    card_id = pending["read"][0]["card_id"]
    ok, res = run_tasks(capsys, "save", "card", str(card_id), "-",
                        stdin=dict(CARD, company="Midwest Flour Company, Inc."), monkeypatch=monkeypatch)
    assert ok and res["supplier_id"] == sid and res["merged_into_existing"] == "Midwest Flour Co"
    assert db.get_supplier(new_id) is None                       # no duplicate entry
    assert len(db.cards_for(sid)) == 1 and db.get_supplier(sid)["status"] == "queued"
    ok, pending = run_tasks(capsys, "list")
    assert pending["research"][0]["supplier_id"] == sid and "what's new since 2026-06-01" in pending["research"][0]["reason"]


def fake_claude(tmp_path, script: str) -> str:
    path = tmp_path / "claude"
    path.write_text(f"#!{sys.executable}\n{script}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def wait_for(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_analyze_now_runs_claude_code_with_progress(client, tmp_path, monkeypatch):
    sid = new_supplier()
    db.update_supplier(sid, status="queued", next_check="2026-01-01")
    # A stand-in for Claude Code that uses the tasks commands the way the skill says.
    monkeypatch.setenv("ROLODEX_DATA_DIR", str(config.DATA_DIR))
    script = f"""
import json, subprocess, sys, time
run = lambda *a: subprocess.run([sys.executable, "-m", "rolodex.tasks", *a], check=True, capture_output=True)
run("begin"); run("current", "{sid}"); run("progress", "{sid}", "2", "6", "Pricing")
import os
end = time.time() + 20
while not os.path.exists("go") and time.time() < end:   # the test checks the progress bar, then says go
    time.sleep(0.1)
open("research.json", "w").write({json.dumps(json.dumps(RESEARCH))})
run("save", "research", "{sid}", "research.json")
open("catalog.json", "w").write({json.dumps(json.dumps(CATALOG))})
run("save", "catalog", "{sid}", "catalog.json")
run("finish", "Researched 1 supplier.")
"""
    monkeypatch.setattr(config, "CLAUDE_COMMAND", fake_claude(tmp_path, script))
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "rolodex").symlink_to(os.path.join(os.path.dirname(__file__), "..", "rolodex"))
    assert "Done adding: analyze now" in client.get("/").text
    client.post("/analysis/start", data={"return_to": "/"})
    assert wait_for(lambda: (db.get_supplier(sid)["progress"] or {}).get("label") == "Pricing")
    p = client.get("/analysis").json()
    assert p["state"] == "running" and p["total"] == 1 and p["percent"] == 17 and p["steps"][0]["label"] == "Pricing"
    assert p["eta"] == "working out time left"
    assert 'id="runbar" data-state="running"' in client.get("/").text
    assert runner._eta("2000-01-01T00:00:00", 50).endswith("hours left") and runner._eta(db.now(), 50) == "working out time left"
    (tmp_path / "go").touch()
    assert wait_for(lambda: client.get("/analysis").json()["state"] == "done")
    p = client.get("/analysis").json()
    assert p["done"] == 1 and p["summary"] == "Researched 1 supplier."
    s = db.get_supplier(sid)
    assert s["status"] == "active" and s["catalog"]["total"] == 61 and not s["progress"]
    assert "Researched 1 supplier." in client.get("/").text


def test_cancel_stops_the_run(client, tmp_path, monkeypatch):
    sid = new_supplier()
    db.update_supplier(sid, status="queued", next_check="2026-01-01")
    monkeypatch.setattr(config, "CLAUDE_COMMAND", fake_claude(tmp_path, "import time; time.sleep(60)"))
    client.post("/analysis/start", data={"return_to": "/"})
    assert runner.running()
    client.post("/analysis/cancel", data={"return_to": "/"})
    assert wait_for(lambda: not runner.running(), 5)
    assert client.get("/analysis").json()["state"] == "cancelled"
    assert db.get_supplier(sid)["status"] == "queued"


def test_claude_not_signed_in_is_explained(client, tmp_path, monkeypatch):
    sid = new_supplier()
    db.update_supplier(sid, status="queued", next_check="2026-01-01")
    monkeypatch.setattr(config, "CLAUDE_COMMAND", fake_claude(
        tmp_path, "print('Invalid API key · Please run /login'); raise SystemExit(1)"))
    client.post("/analysis/start", data={"return_to": "/"})
    assert wait_for(lambda: client.get("/analysis").json()["state"] == "failed")
    assert "type claude and sign in" in client.get("/analysis").json()["summary"]


def test_git_sync_commits_and_pushes_the_data(tmp_path, monkeypatch):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git = lambda *a, cwd=work: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)
    (work / "README.md").write_text("x")
    git("add", "."); git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"); git("push", "-q", "origin", "HEAD")
    data = work / "data"
    for key, value in {"ROOT": work, "DATA_DIR": data, "DB_PATH": data / "rolodex.db", "CARDS_DIR": data / "cards",
                       "DOCS_DIR": data / "docs", "CACHE_DIR": data / "cache", "BACKUP_PATH": data / "rolodex-backup.db"}.items():
        monkeypatch.setattr(config, key, value)
    db.init()
    db.create_supplier({"company": "Saved Co"})
    (data / "cards" / "a.jpg").write_bytes(photo())
    assert gitsync.sync_now()["ok"]
    log = subprocess.run(["git", "log", "--stat", "-1", "origin/HEAD"], cwd=work, capture_output=True, text=True).stdout
    git("fetch", "-q")
    shown = subprocess.run(["git", "show", "--stat", "FETCH_HEAD"], cwd=work, capture_output=True, text=True).stdout
    assert "data/rolodex-backup.db" in shown and "data/cards/a.jpg" in shown
    assert gitsync.sync_now()["message"] == "Saved to GitHub (no changes)."
    # A new checkout (a new Codespace) starts from the saved copy.
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", "-q", str(remote), str(fresh)], check=True, capture_output=True)
    monkeypatch.setattr(config, "DB_PATH", fresh / "data" / "rolodex.db")
    monkeypatch.setattr(config, "BACKUP_PATH", fresh / "data" / "rolodex-backup.db")
    monkeypatch.setattr(config, "DATA_DIR", fresh / "data")
    db.init()
    assert [s["company"] for s in db.all_suppliers()] == ["Saved Co"]


def test_sitemap_product_page_parsing():
    page = """<html><head><meta property="og:image" content="/img/big.jpg">
    <script type="application/ld+json">{"@context":"https://schema.org","@graph":[
      {"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Home"},
        {"@type":"ListItem","position":2,"name":"Packaging"},{"@type":"ListItem","position":3,"name":"Bread Bags"},
        {"@type":"ListItem","position":4,"name":"Poly Bread Bag 8x4x18"}]},
      {"@type":"Product","name":"Poly Bread Bag 8x4x18","sku":"PB-8418","brand":{"name":"Bagcraft"},
       "image":["https://cdn.example/pb.jpg","https://cdn.example/pb2.jpg"],
       "offers":{"@type":"Offer","price":"52.10","priceCurrency":"USD"}}]}</script></head></html>"""
    p = catalog.read_product_page("https://x.example/p/pb-8418", page)
    assert p["name"] == "Poly Bread Bag 8x4x18" and p["sku"] == "PB-8418" and p["price"] == "$52.10"
    assert p["path"] == ["Packaging", "Bread Bags"] and p["image_url"] == "https://cdn.example/pb.jpg"
    assert p["images"] == ["https://cdn.example/pb2.jpg"] and "Bagcraft" in p["details"]
    assert catalog.url_shape("https://x.example/A-B-C_p_12.html") == catalog.url_shape("https://x.example/DD-E_p_9.html")
    assert catalog.url_shape("https://x.example/A-B-C_p_12.html") != catalog.url_shape("https://x.example/Tools_c_6.html")


def test_add_card_after_card(client):
    r = client.post("/add", data={"kind": "card", "then": "another", "added": "0"},
                    files={"front": ("f.jpg", photo(), "image/jpeg")}, follow_redirects=False)
    assert r.headers["location"] == "/add?added=1"
    assert "1 added" in client.get("/add?added=1").text
    r = client.post("/add", data={"kind": "pamphlet", "added": "1"},
                    files=[("many", ("a.jpg", photo(), "image/jpeg")), ("many", ("b.jpg", photo(), "image/jpeg"))],
                    follow_redirects=False)
    assert r.headers["location"] == "/add?added=3"
    unread = db.unread_cards()
    assert len(unread) == 3 and len({c["supplier_id"] for c in unread}) == 3
    assert sorted(c["kind"] for c in unread) == ["card", "pamphlet", "pamphlet"]
    page = client.get("/add").text
    assert "Add photos" in page and 'name="front"' not in page and 'name="many" accept="image/*" multiple' in page
    # several photos picked on a supplier's page all go onto that supplier
    sid = new_supplier()
    r = client.post("/add", data={"kind": "card", "supplier": str(sid), "then": "another", "added": "0"},
                    files=[("many", ("c.jpg", photo(), "image/jpeg")), ("many", ("d.jpg", photo(), "image/jpeg"))],
                    follow_redirects=False)
    assert r.headers["location"] == f"/add?added=2&supplier={sid}"
    assert len(db.cards_for(sid)) == 2 and len(db.unread_cards()) == 5


def test_ask_uses_claude_code(client, tmp_path, monkeypatch):
    sid = new_supplier(company="Bag Co")
    script = f"""
import json, sys
directory = json.loads(sys.stdin.read())
assert directory[0]["company"] == "Bag Co" and "Bread bags?" in sys.argv[2]
print(json.dumps({{"result": 'Here: {{"answer": "Bag Co sells them.", "matches": [{{"supplier_id": {sid}, "why": "Bread bags."}}]}}'}}))
"""
    monkeypatch.setattr(config, "CLAUDE_COMMAND", fake_claude(tmp_path, script))
    assert "Ask Claude" in client.get("/").text
    page = client.post("/ask", data={"question": "Bread bags?"}).text
    assert "Bag Co sells them." in page and f"/supplier/{sid}" in page


def test_ask_claude_finds_catalog_products(client, tmp_path, monkeypatch):
    sid = new_supplier()
    db.save_catalog(sid, CATALOG)
    # Claude Code gets the whole catalog on stdin and Sonnet as the model; it answers with product keys.
    script = ("import sys, json\n"
              "args = sys.argv[1:]; rows = json.loads(sys.stdin.read())\n"
              "assert args[args.index('--model') + 1] == 'claude-sonnet-5'\n"
              "key = next(r['key'] for r in rows if r['name'].startswith('Stretch film'))\n"
              "answer = {'answer': 'Stretch film wraps pallets.', 'matches': [{'key': key, 'why': 'Pallet wrap'},"
              " {'key': '999/nope', 'why': 'x'}]}\n"
              "print(json.dumps({'result': json.dumps(answer)}))\n")
    monkeypatch.setattr(config, "CLAUDE_COMMAND", fake_claude(tmp_path, script))
    page = client.post("/products/ask", data={"question": "something to wrap pallets"}).text
    assert "Stretch film wraps pallets." in page and "Stretch film 18 in" in page and "Pallet wrap" in page
    assert f"/supplier/{sid}/catalog/item/SF-1" in page and "999" not in page


def test_ask_claude_products_api(client, monkeypatch):
    sid = new_supplier()
    db.save_catalog(sid, CATALOG)
    monkeypatch.setattr(config, "USE_API", True)
    seen = {}
    def ask_products(question, products):
        seen["n"] = len(products)
        return {"answer": "Try the film.", "matches": [{"key": f"{sid}/SF-1", "why": "Wraps pallets"}]}
    monkeypatch.setattr(app_module.claude, "ask_products", ask_products)
    page = client.post("/products/ask", data={"question": "pallet wrap"}).text
    assert seen["n"] == len(CATALOG["products"]) and "Try the film." in page and "Wraps pallets" in page


def test_catalog_review_adds_what_the_pages_show(client, capsys, monkeypatch):
    sid = new_supplier()
    db.save_catalog(sid, CATALOG)
    ok, pending = run_tasks(capsys, "list")
    assert [r["supplier_id"] for r in pending["review"]] == [sid]
    ok, review = run_tasks(capsys, "show", "review", str(sid))
    assert "missing" in review["products"][0] and "safety data sheets" in review["instructions"]
    import json as _json
    f = tmp_file = (config.DATA_DIR / "review.json")
    f.write_text(_json.dumps({"note": "Checked every page.", "products": [
        {"id": "SF-1", "specs": [["Width", "18 in"], ["Thickness", "80 gauge"]],
         "files": [{"name": "Safety data sheet (US)", "url": "https://bags.example/sds/sf1.pdf"}],
         "image_url": "https://bags.example/img/sf1.jpg"},
        {"id": "no-such-product", "specs": [["x", "y"]]}]}))
    ok, saved = run_tasks(capsys, "save", "products", str(sid), str(tmp_file))
    assert saved["saved"] and saved["changed"] == 1 and saved["with_files"] == 1
    p = db.catalog_product(sid, "SF-1")
    assert p["image_url"].endswith("sf1.jpg")
    ok, pending = run_tasks(capsys, "list")
    assert pending["review"] == []
    page = client.get(f"/supplier/{sid}/catalog/item/SF-1").text
    assert "80 gauge" in page and "Safety data sheet" in page and f"/supplier/{sid}/doc/0?p=SF-1" in page
    assert "sds/sf1.pdf" not in page   # viewed in the app, not linked


def test_product_tables_and_document_viewer(client, monkeypatch):
    from rolodex import docview, present
    import pypdfium2 as pdfium, io
    files = [{"name": "Safety data sheet (EN, Mexico) PDF / 331.2 KB All languages ﻿", "url": "https://oil.example/SDS_AU_MX_EN.pdf"},
             {"name": "Español", "url": "https://oil.example/SDS_AU_MX_ES.pdf"},
             {"name": "Product information (EN) PDF / 341.7 KB", "url": "https://oil.example/AU_PI_US_en.pdf"},
             {"name": "Safety data sheet (EN, United States) PDF / 313.77 KB", "url": "https://oil.example/SDS_AU_US_EN.pdf"}]
    docs = present.documents(files)
    assert [(d["kind"], d["language"], d["region"]) for d in docs] == [
        ("Safety data sheet", "English", "United States"), ("Safety data sheet", "English", "Mexico"),
        ("Safety data sheet", "Spanish", "Mexico"), ("Product data sheet", "English", "")]
    v = present.view({"details": "Film", "specs": [["Article-No", "340557 Synthetic fluid for compressors"]],
                      "description": "Article-No: 340557 Synthetic fluid for compressors\nStretch film for wrapping.\n"
                                     "Codes and Presentations:\nCH5235 – Stretch Film 500 mm x 23 mic – Without Handle\n"
                                     "Applications:\nPallets\nBoxes", "files": files})
    assert v["specs"][0] == ["Article-No", "340557"]
    assert v["sizes"]["rows"] == [["CH5235", "Stretch Film 500 mm x 23 mic – Without Handle", "500 mm x 23 mic"]]
    assert {"heading": "Applications", "points": ["Pallets", "Boxes"]} in v["blocks"]
    assert [b["text"] for b in v["blocks"] if "text" in b] == ["Stretch film for wrapping."]

    sid = new_supplier()
    db.save_catalog(sid, {"sections": [], "products": [{"id": "AU-46", "name": "AU-46", "files": files}]})
    page = client.get(f"/supplier/{sid}/catalog/item/AU-46").text
    assert "SDS_AU_US_EN.pdf" not in page and f"/supplier/{sid}/doc/3?p=AU-46" in page
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(612, 792); pdf.new_page(612, 792)
    buf = io.BytesIO(); pdf.save(buf)
    asked = []
    monkeypatch.setattr(docview, "fetch", lambda u: asked.append(u) or buf.getvalue())
    viewer = client.get(f"/supplier/{sid}/doc/3?p=AU-46").text
    assert "2 pages" in viewer and "United States" in viewer and asked == ["https://oil.example/SDS_AU_US_EN.pdf"]
    img = client.get(f"/supplier/{sid}/doc/3/page/2?p=AU-46")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get(f"/supplier/{sid}/doc/3/page/3?p=AU-46").status_code == 404
    assert client.get(f"/supplier/{sid}/doc/9?p=AU-46").status_code == 404   # only the product's own documents


def test_check_photos_replaces_broken_ones(client, monkeypatch):
    monkeypatch.setenv("ROLODEX_CHECK_PHOTOS", "1")
    sid = new_supplier()
    db.save_catalog(sid, {"sections": [], "products": [
        {"id": "a", "name": "A", "image_url": "https://x.example/broken.jpg", "images": ["https://x.example/good.jpg"]},
        {"id": "b", "name": "B", "image_url": "https://x.example/good.jpg"}]})
    monkeypatch.setattr(images, "fetch_image", lambda u, w=0: None if "broken" in u else ("f", "image/jpeg"))
    res = catalog.check_photos(sid, minutes=1)
    assert res["broken_replaced"] == 1 and db.catalog_product(sid, "a")["image_url"].endswith("good.jpg")
    assert db.get_supplier(sid)["catalog"]["photos_checked"] == catalog.PHOTO_CHECK_VERSION


def test_products_that_share_a_one_liner_get_reviewed(client, capsys):
    sid = new_supplier()
    db.save_catalog(sid, {"sections": [], "products": [
        {"id": "sh-32", "name": "SH-32", "details": "Synthetic air compressor"},
        {"id": "sh-46", "name": "SH-46", "details": "Synthetic air compressor."},
        {"id": "ps-1", "name": "PS-1", "details": "View Details"},
        {"id": "x", "name": "X", "details": "ISO VG 68 PAO compressor oil"}]})
    assert db.unclear_one_liners(sid) == {"sh-32", "sh-46", "ps-1"}
    s = db.get_supplier(sid)
    db.update_supplier(sid, status="active", catalog={**s["catalog"], "reviewed": db.REVIEW_VERSION})
    assert sid in [x["id"] for x in db.needs_review()]
    ok, review = run_tasks(capsys, "show", "review", str(sid))
    missing = {r["id"]: r["missing"] for r in review["products"]}
    assert "unique one-liner" in missing["sh-32"] and "unique one-liner" not in missing["x"]
    assert "no two products may share a one-liner" in review["instructions"]
    f = config.DATA_DIR / "one-liners.json"
    f.write_text(json.dumps({"products": [{"id": "sh-32", "details": "ISO VG 32 synthetic compressor oil"},
                                          {"id": "sh-46", "details": "ISO VG 46 synthetic compressor oil"}]}))
    ok, saved = run_tasks(capsys, "save", "products", str(sid), str(f))
    assert saved["one_liners_not_unique"] == ["ps-1"]
    f.write_text(json.dumps({"products": [{"id": "ps-1", "details": "Premium synthetic blend, ISO VG 100"}]}))
    ok, saved = run_tasks(capsys, "save", "products", str(sid), str(f))
    assert saved["one_liners_not_unique"] == [] and sid not in [x["id"] for x in db.needs_review()]
    db.save_catalog(sid, {"sections": [], "products": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
    s = db.get_supplier(sid)
    assert s["catalog"].get("one_liners") != db.ONE_LINER_VERSION or sid not in [x["id"] for x in db.needs_review()]


def test_a_catalog_review_doesnt_leave_a_supplier_queued(client, capsys):
    sid = new_supplier()
    db.update_supplier(sid, status="active", last_checked="2026-09-25T18:00:00", next_check="2099-01-01")
    run_tasks(capsys, "progress", str(sid), "1", "3", "Reviewing", "catalog")
    assert db.get_supplier(sid)["status"] == "active"
    db.update_supplier(sid, status="queued")   # left over from before this fix
    db.init()
    assert db.get_supplier(sid)["status"] == "active"


def test_ask_claude_asks_a_follow_up_question(client, monkeypatch):
    import html, re
    sid = new_supplier()
    db.save_catalog(sid, CATALOG)
    monkeypatch.setattr(config, "USE_API", True)
    asked = []
    def ask_products(question, products):
        asked.append(question)
        if "They answered" not in question:
            return {"answer": "A few films could work.", "matches": [{"key": f"{sid}/SF-1", "why": "Wraps pallets"}],
                    "follow_up": "What width do you need?", "options": ["18 in", "20 in"]}
        return {"answer": "The 18 in film.", "matches": [{"key": f"{sid}/SF-1", "why": "18 in wide"}],
                "follow_up": "", "options": []}
    monkeypatch.setattr(app_module.claude, "ask_products", ask_products)
    page = client.post("/products/ask", data={"question": "pallet wrap"}).text
    assert "What width do you need?" in page and 'value="18 in"' in page and "Best matches so far" in page
    history = html.unescape(re.search(r'name="history" value="([^"]*)"', page).group(1))
    page = client.post("/products/ask", data={"question": "pallet wrap", "history": history,
                                              "asked": "What width do you need?", "reply": "18 in", "typed": ""}).text
    assert asked[-1] == "pallet wrap\nYou asked: What width do you need?\nThey answered: 18 in"
    assert "The 18 in film." in page and "To narrow it down" not in page and "Matching products" in page
    assert "Claude asked:" in page


def test_a_run_that_stops_early_carries_on_by_itself(client, tmp_path, monkeypatch):
    ids = [new_supplier(company=f"Co {i}", website=f"co{i}.example") for i in range(3)]
    for sid in ids:
        db.update_supplier(sid, status="queued", next_check="2026-01-01")
    monkeypatch.setenv("ROLODEX_DATA_DIR", str(config.DATA_DIR))
    # a stand-in for Claude Code that researches one supplier per run and then stops
    script = f"""
import json, os, subprocess, sys
run = lambda *a: subprocess.run([sys.executable, "-m", "rolodex.tasks", *a], check=True, capture_output=True)
n = len(open("runs").read()) if os.path.exists("runs") else 0
open("runs", "a").write("x")
sid = str({ids}[n])
run("begin"); run("current", sid)
open("research.json", "w").write({json.dumps(json.dumps(RESEARCH))})
run("save", "research", sid, "research.json")
run("finish", "Researched 1 supplier.")
"""
    monkeypatch.setattr(config, "CLAUDE_COMMAND", fake_claude(tmp_path, script))
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "rolodex").symlink_to(os.path.join(os.path.dirname(__file__), "..", "rolodex"))
    client.post("/analysis/start", data={"return_to": "/"})
    assert wait_for(lambda: all(db.get_supplier(i)["status"] == "active" for i in ids), 30)
    assert wait_for(lambda: not runner.running(), 10)
    assert (tmp_path / "runs").read_text() == "xxx"


def test_the_same_photo_twice_is_skipped(client):
    from PIL import Image, ImageDraw
    import io as _io

    def card(text):
        img = Image.new("RGB", (800, 480), "white")
        ImageDraw.Draw(img).rectangle((40, 40, 300 + 40 * len(text), 200), fill="black")
        ImageDraw.Draw(img).ellipse((500, 250, 760 - 20 * len(text), 440), fill="gray")
        b = _io.BytesIO(); img.save(b, "JPEG"); return b.getvalue()
    a, b = card("A"), card("BBBB")
    r = client.post("/add", data={"kind": "card", "added": "0"}, follow_redirects=False,
                    files=[("many", ("a.jpg", a, "image/jpeg")), ("many", ("b.jpg", b, "image/jpeg")),
                           ("many", ("a2.jpg", a, "image/jpeg"))])
    assert r.headers["location"] == "/add?added=2&skipped=1"
    r = client.post("/add", data={"kind": "card", "added": "0"}, follow_redirects=False,
                    files=[("many", ("b.jpg", b, "image/jpeg"))])
    assert r.headers["location"] == "/add?added=0&skipped=1"
    assert len(db.unread_cards()) == 2
    assert "1 skipped: already in the rolodex" in client.get("/add?added=0&skipped=1").text


def test_a_repeat_card_for_a_freshly_researched_supplier_isnt_researched_again(client, capsys, monkeypatch):
    from datetime import date
    sid = new_supplier(company="Bag Co", website="bags.example")
    db.update_supplier(sid, last_checked=date.today().isoformat() + "T08:00:00", next_check="2099-01-01")
    card_id = db.add_card(db.create_supplier({"company": "(reading)"}), ["x.jpg"], "card")
    ok, out = run_tasks(capsys, "save", "card", str(card_id), "-", monkeypatch=monkeypatch,
                        stdin={**CARD, "company": "Bag Co Inc", "website": "bags.example"})
    assert ok and "nothing more to do" in out["next_step"] and db.get_supplier(sid)["status"] == "active"
    # a supplier last researched long ago is refreshed as before
    db.update_supplier(sid, last_checked="2020-01-01T08:00:00")
    card_id = db.add_card(db.create_supplier({"company": "(reading)"}), ["y.jpg"], "card")
    ok, out = run_tasks(capsys, "save", "card", str(card_id), "-", monkeypatch=monkeypatch,
                        stdin={**CARD, "company": "Bag Co", "website": "bags.example"})
    assert ok and out["next_step"] == f"show research {sid}" and db.get_supplier(sid)["status"] == "queued"


def test_page_digests_read_every_product_page(client, monkeypatch):
    sid = new_supplier()
    db.save_catalog(sid, CATALOG)
    page = """<html><body><nav><a href="/menu">Menu</a></nav><main><h1>Bread bag {n} in</h1>
      <p>Clear bread bag for 1 lb loaves.</p><table><tr><th>Width</th><td>{n} in</td></tr></table>
      <div class="tabs"><a href="/files/SDS_bag_{n}_US_EN.pdf">Safety data sheet (EN, United States)</a></div>
      <img src="/img/bag{n}.jpg" alt="Bread bag {n} in"></main><footer>Contact us</footer></body></html>"""

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_open(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("robots.txt"):
            return Resp(b"")
        n = url.rstrip("/").rsplit("/", 1)[-1]
        return Resp(page.replace("{n}", n).encode())
    monkeypatch.setattr(catalog, "_open", fake_open)
    monkeypatch.setattr(catalog, "_public", lambda url: True)
    monkeypatch.setattr(catalog, "DELAY", 0)
    rows = catalog.digest(sid, 0, 40)
    assert len(rows) == 40 and all("error" not in r for r in rows)
    r = next(r for r in rows if r["id"] == "BB-7")
    assert "Clear bread bag" in r["text"] and "Width | 7 in" in r["text"] and "Menu" not in r["text"]
    assert r["files"][0]["url"] == "https://bags.example/files/SDS_bag_7_US_EN.pdf"


def test_security_gate_for_web_reads(tmp_path, monkeypatch):
    # only public web pages; nothing on this computer or the local network, redirects included
    for url in ("http://127.0.0.1:8000/", "http://localhost/", "http://192.168.1.10/", "file:///etc/passwd",
                "http://169.254.169.254/latest/meta-data/"):
        assert catalog.fetch_page(url) == {"ok": False, "error": "Only public web pages can be read."}
    with pytest.raises(PermissionError):
        catalog.Site.__new__(catalog.Site).get.__func__(
            type("S", (), {"robots": type("R", (), {"can_fetch": lambda *a: True})(), "last": 0, "requests": 0})(),
            "http://10.0.0.5/products")
    # pages are only saved inside work/
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catalog, "_public", lambda url: True)

    class Resp(io.BytesIO):
        headers = {"Content-Type": "text/html"}
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(catalog, "_open", lambda req, timeout=0: Resp(b"<html>hi</html>"))
    assert catalog.fetch_page("https://x.example/", out="../elsewhere.html")["ok"] is False
    assert catalog.fetch_page("https://x.example/", out="work/p.html") == {"ok": True, "file": "work/p.html", "chars": 15}
    # the unattended Claude Code session can't run curl or read the database
    assert not any(t.startswith("Bash(curl") for t in runner.ALLOWED_TOOLS) and "Bash(curl:*)" in runner.DENIED_TOOLS
    assert "Read" not in runner.ALLOWED_TOOLS and "Read(./data/*.db)" in runner.DENIED_TOOLS
