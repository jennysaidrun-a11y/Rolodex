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
            "attention_reason": "Recall in March 2026", "sources": ["https://example.com"]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "rolodex.db")
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path / "cards")
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    monkeypatch.setattr(config, "AUTO_RECHECK", False)
    monkeypatch.setattr(claude, "read_card", lambda front, back: dict(CARD))
    monkeypatch.setattr(claude, "research", lambda s, notes: dict(RESEARCH))
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
    monkeypatch.setattr(claude, "research", lambda s, notes: calls.append(s["id"]) or dict(RESEARCH))
    assert recheck.run_due() == 1 and calls == [sid]


def test_research_failure_is_shown_and_retried(client, monkeypatch):
    def boom(s, notes):
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
