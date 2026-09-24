"""End-to-end tests of the web app with the Claude calls stubbed out (no API key or network needed)."""

import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from rolodex import app as app_module
from rolodex import claude, config, db, recheck

CARD = {"company": "Midwest Flour Co", "contact_name": "Dana Reyes", "contact_title": "Territory Manager",
        "phone": "555-201-3344", "email": "dana@midwestflour.com", "website": "www.midwestflour.com",
        "address": "12 Mill Rd, Salina, KS", "categories": ["Flour & grains"],
        "products_mentioned": ["Bread flour", "Whole wheat"], "other_text": "SQF Level 2"}

RESEARCH = {"business_status": "active", "summary": "Regional flour mill supplying bakeries.",
            "categories": ["Flour & grains"], "products": [{"name": "High-gluten flour", "details": "50 lb bags"}],
            "pricing": [], "stock_and_lead_times": "", "minimum_order": "1 pallet",
            "locations": [{"kind": "Mill", "address": "Salina, KS"}], "service_area": "Midwest",
            "certifications": [{"name": "SQF", "status": "current", "source": "https://example.com/sqf"}],
            "reviews": {"summary": "Well regarded.", "sources": []},
            "regulatory": [{"date": "2026-03", "kind": "Recall", "description": "Undeclared sesame",
                            "source": "javascript:alert(1)"}],
            "news": [], "changes_since_last_check": [], "needs_attention": True,
            "attention_reason": "Recall in March 2026", "sources": ["https://example.com"],
            "tags": [{"group": "Product", "name": "High-Gluten Flour"}, {"group": "Certification", "name": "SQF"},
                     {"group": "Service area", "name": "Midwest"}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "rolodex.db")
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    monkeypatch.setattr(config, "AUTO_RECHECK", False)
    monkeypatch.setattr(claude, "read_card", lambda front, back, cats: dict(CARD))
    monkeypatch.setattr(claude, "research", lambda s, notes, vocab=None, cats=None: dict(RESEARCH))
    # Run queued research inline so the test can see the result.
    monkeypatch.setattr(recheck, "queue", lambda sid: (
        db.update_supplier(sid, status="queued", next_check=date.today().isoformat()), recheck.research_one(sid)))
    with TestClient(app_module.app) as c:
        yield c


def photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (3000, 1800), "white").save(buf, "JPEG")
    return buf.getvalue()


def add_card(client) -> int:
    r = client.post("/add", files={"front": ("front.jpg", photo(), "image/jpeg")}, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].split("/")[2])


def test_card_to_researched_profile(client):
    sid = add_card(client)
    s = db.get_supplier(sid)
    assert s["company"] == "Midwest Flour Co" and s["status"] == "new"
    card = db.cards_for(sid)[0]
    assert max(Image.open(config.CARDS_DIR / card["front"]).size) == 1600   # resized
    assert any("SQF Level 2" in n["text"] for n in db.notes_for(sid))

    page = client.get(f"/supplier/{sid}/edit?new=1")
    assert "Save and research" in page.text and "Dana Reyes" in page.text

    client.post(f"/supplier/{sid}/edit", data={"company": "Midwest Flour Company", "phone": "555-201-3344",
                                               "categories": ["Flour & grains", "Other ingredients"]})
    s = db.get_supplier(sid)
    assert s["company"] == "Midwest Flour Company"
    assert s["status"] == "active" and s["needs_attention"] == 1
    assert s["next_check"] == (date.today() + timedelta(days=config.RECHECK_DAYS)).isoformat()
    assert s["categories"] == ["Flour & grains", "Other ingredients"]

    page = client.get(f"/supplier/{sid}").text
    assert "Recall in March 2026" in page and "High-gluten flour" in page
    assert "javascript:" not in page                      # unsafe links from research are dropped

    client.post(f"/supplier/{sid}/reviewed")
    assert db.get_supplier(sid)["needs_attention"] == 0


def test_search_notes_and_duplicates(client):
    sid = add_card(client)
    client.post(f"/supplier/{sid}/notes", data={"text": "Offered free samples of rye", "author": "Jenny"})
    assert "Midwest Flour Co" in client.get("/?q=rye samples").text
    assert "Midwest Flour Co" not in client.get("/?q=packaging").text
    assert "Midwest Flour Co" in client.get("/?category=Flour %26 grains").text

    dup = add_card(client)
    assert "might already be in the rolodex" in client.get(f"/supplier/{dup}/edit?new=1").text
    client.post(f"/supplier/{dup}/delete")
    assert db.get_supplier(dup) is None and len(db.all_suppliers()) == 1


def test_recheck_only_when_due(client, monkeypatch):
    sid = add_card(client)
    assert db.due_for_recheck() == []                    # unreviewed cards aren't researched
    client.post(f"/supplier/{sid}/edit", data={"company": "Midwest Flour Co"})
    assert db.due_for_recheck() == []                    # researched: next check in 90 days
    db.update_supplier(sid, next_check=date.today().isoformat())
    calls = []
    monkeypatch.setattr(claude, "research", lambda s, notes, vocab=None, cats=None: calls.append(s["id"]) or dict(RESEARCH))
    assert recheck.run_due() == 1 and calls == [sid]


def test_research_failure_is_shown_and_retried(client, monkeypatch):
    def boom(s, notes, vocab=None, cats=None):
        raise claude.ClaudeError("The Anthropic API key is missing or wrong.")
    monkeypatch.setattr(claude, "research", boom)
    sid = add_card(client)
    client.post(f"/supplier/{sid}/edit", data={"company": "Midwest Flour Co"})
    s = db.get_supplier(sid)
    assert s["status"] == "error" and s["next_check"] == (date.today() + timedelta(days=1)).isoformat()
    assert "API key is missing" in client.get(f"/supplier/{sid}").text


def test_ask(client, monkeypatch):
    sid = add_card(client)
    monkeypatch.setattr(claude, "ask", lambda q, suppliers, notes: {
        "answer": "Midwest Flour fits.", "matches": [{"supplier_id": sid, "why": "Bread flour"}, {"supplier_id": 999, "why": "x"}]})
    page = client.post("/ask", data={"question": "Who sells bread flour?"}).text
    assert "Midwest Flour fits." in page and f"/supplier/{sid}" in page and "/supplier/999" not in page


def test_password(client, monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "crumb")
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert "Wrong password" in client.post("/login", data={"password": "nope"}).text
    client.post("/login", data={"password": "crumb"})
    assert client.get("/", follow_redirects=False).status_code == 200


def test_directory_for_ask_is_compact():
    s = {"id": 1, "company": "A", "status": "active", "categories": [], "contact_name": "", "phone": "",
         "email": "", "address": "", "summary": "", "profile": RESEARCH, "needs_attention": 0,
         "attention_note": "", "last_checked": None}
    assert '"regulatory":["2026-03 Recall: Undeclared sesame"]' in claude._directory([s], {})


def test_manual_scan_of_selected_suppliers(client, monkeypatch):
    a, b, unreviewed = add_card(client), add_card(client), add_card(client)
    for sid in (a, b):
        client.post(f"/supplier/{sid}/edit", data={"company": f"Supplier {sid}"})
    calls = []
    monkeypatch.setattr(claude, "research", lambda s, notes, vocab=None, cats=None: calls.append(s["id"]) or dict(RESEARCH))

    page = client.get("/").text
    assert 'id="select-toggle"' in page and 'action="/scan"' in page

    r = client.post("/scan", data={"ids": [a, b, unreviewed], "return_to": "/?q=flour"}, follow_redirects=False)
    assert r.headers["location"] == "/?q=flour"
    assert sorted(calls) == [a, b]                        # the unreviewed card is skipped
    assert db.get_supplier(unreviewed)["status"] == "new"

    r = client.post("/scan", data={"ids": [a], "return_to": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_tags_from_research_filter_and_edit(client, monkeypatch):
    flour = add_card(client)
    client.post(f"/supplier/{flour}/edit", data={"company": "Midwest Flour Co"})
    assert [t["name"] for t in db.get_supplier(flour)["all_tags"]] == ["High-Gluten Flour", "SQF", "Midwest"]

    # The next supplier's research sees the existing tags and odd spellings snap to them.
    seen = {}
    def research(s, notes, vocab=None, cats=None):
        seen["vocab"] = vocab
        return dict(RESEARCH, tags=[{"group": "Product", "name": "bread bags"}, {"group": "Certification", "name": "sqf "}])
    monkeypatch.setattr(claude, "research", research)
    bags = add_card(client)
    client.post(f"/supplier/{bags}/edit", data={"company": "Bag Co", "website": "bagco.com", "email": ""})
    assert {"group": "Certification", "name": "SQF"} in seen["vocab"]
    assert [t["name"] for t in db.get_supplier(bags)["all_tags"]] == ["bread bags", "SQF"]

    # Filter: every selected tag must match.
    names = lambda url: [s["company"] for s in db.search(tags=url)]
    assert names(["SQF"]) == ["Bag Co", "Midwest Flour Co"]
    assert names(["SQF", "Midwest"]) == ["Midwest Flour Co"]
    page = client.get("/?tag=SQF&tag=Midwest").text
    assert "Midwest Flour Co" in page and "Bag Co" not in page and "Filter by tags (2 selected)" in page
    assert 'href="/?tag=SQF"' in page                     # removing the Midwest filter keeps SQF

    # Staff remove a research tag and add their own; a rescan doesn't undo that.
    client.post(f"/supplier/{flour}/edit", data={"company": "Midwest Flour Co", "keep_tags": ["SQF", "High-Gluten Flour"],
                                                 "new_tags": "Sample Received, preferred", "new_tag_group": "Other"})
    monkeypatch.setattr(claude, "research", lambda s, notes, vocab=None, cats=None: dict(RESEARCH))
    client.post(f"/supplier/{flour}/recheck")
    tags = [t["name"] for t in db.get_supplier(flour)["all_tags"]]
    assert "Midwest" not in tags and {"Sample Received", "preferred", "SQF"} <= set(tags)
    assert "Sample Received" in client.get(f"/supplier/{flour}").text


def test_old_database_gets_tag_columns(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "rolodex.db")
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path / "cards")
    conn = sqlite3.connect(tmp_path / "rolodex.db")
    conn.execute("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, company TEXT NOT NULL, contact_name TEXT DEFAULT '', "
                 "contact_title TEXT DEFAULT '', phone TEXT DEFAULT '', email TEXT DEFAULT '', website TEXT DEFAULT '', "
                 "address TEXT DEFAULT '', categories TEXT DEFAULT '[]', summary TEXT DEFAULT '', profile TEXT DEFAULT '{}', "
                 "status TEXT DEFAULT 'new', needs_attention INTEGER DEFAULT 0, attention_note TEXT DEFAULT '', "
                 "research_error TEXT DEFAULT '', created_at TEXT NOT NULL, last_checked TEXT, next_check TEXT)")
    conn.execute("INSERT INTO suppliers (company, created_at) VALUES ('Old Co', '2026-01-01')")
    conn.commit(); conn.close()
    db.init()
    assert db.all_suppliers()[0]["all_tags"] == []


def test_categories_filter_browse_and_manage(client, monkeypatch):
    seen = {}
    def read_card(front, back, cats):
        seen["cats"] = cats
        return dict(CARD)
    monkeypatch.setattr(claude, "read_card", read_card)
    flour = add_card(client)
    assert seen["cats"][0] == {"name": "Flour & grains", "description": "Flour, grains, meals, starches", "count": 0}

    # First research may add categories; a typed-in category joins the managed list.
    monkeypatch.setattr(claude, "research", lambda s, notes, vocab=None, cats=None: dict(RESEARCH, categories=["Other ingredients"]))
    client.post(f"/supplier/{flour}/edit", data={"company": "Midwest Flour Co", "categories": ["Flour & grains"],
                                                 "other_categories": "Nuts & seeds"})
    assert db.get_supplier(flour)["categories"] == ["Flour & grains", "Nuts & seeds", "Other ingredients"]
    assert "Nuts & seeds" in db.category_names()

    # Staff remove one; a rescan doesn't bring it back.
    client.post(f"/supplier/{flour}/edit", data={"company": "Midwest Flour Co", "categories": ["Flour & grains", "Nuts & seeds"]})
    client.post(f"/supplier/{flour}/recheck")
    assert db.get_supplier(flour)["categories"] == ["Flour & grains", "Nuts & seeds"]

    bags = add_card(client)
    client.post(f"/supplier/{bags}/edit", data={"company": "Bag Co", "website": "bagco.com", "categories": ["Packaging"]})

    # Browse grid shows only categories in use; picking several categories matches ANY of them.
    home = client.get("/").text
    assert "Browse by category" in home and 'href="/?category=Packaging"' in home and "?category=Staffing" not in home
    names = lambda cats: sorted(s["company"] for s in db.search(categories=cats))
    assert names(["Packaging"]) == ["Bag Co"]
    assert names(["Packaging", "Flour & grains"]) == ["Bag Co", "Midwest Flour Co"]
    page = client.get("/?category=Packaging&category=Flour+%26+grains").text
    assert "Categories (2 selected)" in page and "Browse by category" not in page
    assert 'href="/?category=Flour+%26+grains"' in page    # the Packaging pill's ✕ keeps the other filter

    # Manage: rename carries to suppliers, renaming onto an existing one merges, delete strips it.
    client.post("/categories/update", data={"old": "Nuts & seeds", "name": "Seeds & nuts", "description": "Seeds"})
    assert db.get_supplier(flour)["categories"] == ["Flour & grains", "Seeds & nuts"]
    client.post("/categories/update", data={"old": "Seeds & nuts", "name": "flour & GRAINS", "description": ""})
    assert db.get_supplier(flour)["categories"] == ["Flour & grains"] and "Seeds & nuts" not in db.category_names()
    client.post("/categories/delete", data={"name": "Packaging"})
    assert db.get_supplier(bags)["categories"] == ["Other ingredients"] and "Packaging" not in db.category_names()
    client.post("/categories/move", data={"name": "Sweeteners", "step": -1})
    assert db.category_names()[:2] == ["Sweeteners", "Flour & grains"]
    client.post("/categories", data={"name": "  Co-packers ", "description": "Contract bakeries"})
    assert db.category_names()[-1] == "Co-packers" and "Contract bakeries" in client.get("/categories").text
