"""Catalogs (the store-style browser), repeat cards, Claude Code runs and saving to GitHub."""

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
    assert p["state"] == "running" and p["total"] == 1 and 0 < p["percent"] < 100 and p["steps"][0]["label"] == "Pricing"
    assert 'id="runbar" data-state="running"' in client.get("/").text
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
