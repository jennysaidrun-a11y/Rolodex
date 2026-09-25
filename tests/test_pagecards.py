"""Category pages with several models: each model becomes its own catalog product with its photo."""

from rolodex import catalog, db, pagecards

from test_catalog_and_runs import client, new_supplier  # noqa: F401  (fixture)

CARD = """<div class="card-two-sided"><div class="card-two-sided__front">
  <img class="lazy-img" src="" data-mediasrc="/img/{slug}.jpg?v=1" alt="">
  <h3 class="card__title" aria-hidden="true">{name}&#174;</h3></div>
  <div class="card-two-sided__back"><h3 class="card__title">{name}®</h3><p>{text}</p>
  <a href="/aed/{slug}">read more <span class="visually-hidden">about {name}</span></a></div></div>"""

CATEGORY = f"""<html><body><header><nav class="nav-rail"><ul>
  <li><a href="/aed/zoll-plus"><img src="/img/menu.jpg"><h3>Zoll AED Plus</h3></a></li>
  <li><a href="/aed/g5"><img src="/img/menu2.jpg"><h3>G5</h3></a></li></ul></nav></header>
<main><h2>AEDs &amp; Emergency Products</h2><div class="grid">
{CARD.format(slug="zoll-plus", name="AED Plus by ZOLL", text="Long battery life.")}
{CARD.format(slug="g5", name="Powerheart G5 AED", text="Real-time CPR feedback.")}
{CARD.format(slug="lifeline", name="Lifeline AED", text="Clear voice prompts.")}
</div>
<h2>How it works</h2><div class="steps">
  <div class="step"><img src="/img/s1.jpg"><h3>Planning</h3><p>We visit.</p></div>
  <div class="step"><img src="/img/s2.jpg"><h3>Installation</h3><p>We install.</p></div></div>
<h2>Featured Resources</h2><div class="res">
  <div class="tile"><a href="/blog/a"><img src="/img/b1.jpg"><h3>AED myths</h3></a></div>
  <div class="tile"><a href="/blog/b"><img src="/img/b2.jpg"><h3>AED laws</h3></a></div></div>
</main><footer><div class="f"><a href="/x"><img src="/img/f.jpg"><h4>Footer thing</h4></a></div></footer></body></html>"""

MODEL = """<html><body><main><h1>{name}</h1>
<img class="lazyload" data-srcset="/files/family-1.jpg?box_crop={{width}},{{height}}" alt="{name}">
<img data-srcset="/files/iso-9001.png?h=96 1x, /files/iso-9001.png?h=192 2x" alt="certificate-iso-9001">
<div class="related-products"><div class="i"><a href="/aed/other"><img src="/img/o.jpg"><h3>Other</h3></a></div>
<div class="i"><a href="/aed/other2"><img src="/img/o2.jpg"><h3>Other 2</h3></a></div></div></main></body></html>"""


def test_cards_on_a_category_page():
    cards = pagecards.product_cards("https://shop.example/aed", CATEGORY)
    assert [c["name"] for c in cards] == ["AED Plus by ZOLL®", "Powerheart G5 AED®", "Lifeline AED®"]
    assert cards[0]["image_url"] == "https://shop.example/img/zoll-plus.jpg?v=1"
    assert cards[0]["page_url"] == "https://shop.example/aed/zoll-plus"
    assert cards[0]["details"] == "Long battery life."


def test_photo_labelled_with_the_product_name():
    page = MODEL.format(name="AU-46")
    assert pagecards.named_photos("https://shop.example/p/au-46", page, "AU-46") == \
        ["https://shop.example/files/family-1.jpg?box_crop=800,800"]
    assert pagecards.product_cards("https://shop.example/p/au-46", page) == []   # related products aren't models


def test_expand_turns_a_category_entry_into_its_models(client, monkeypatch):  # noqa: F811
    sid = new_supplier()
    db.save_catalog(sid, {"source": "https://shop.example", "sections": [{"id": "fa", "name": "First Aid", "parent_id": ""}],
                          "products": [{"id": "aeds", "section_id": "fa", "name": "AEDs", "page_url": "https://shop.example/aed"},
                                       {"id": "gloves", "section_id": "fa", "name": "Gloves", "page_url": "https://shop.example/gloves",
                                        "image_url": "https://shop.example/img/gloves.jpg"}]})
    pages = {"/aed": CATEGORY, "/gloves": "<html><body><main><p>Gloves.</p></main></body></html>",
             **{f"/aed/{s}": MODEL.format(name=n) for s, n in [("zoll-plus", "AED Plus"), ("g5", "G5"), ("lifeline", "Lifeline")]}}
    monkeypatch.setattr(catalog.Site, "get", lambda self, url, check_robots=True, limit=0:
                        pages.get(url.replace("https://shop.example", "").rstrip("/") or "/", "").encode())
    monkeypatch.setattr(catalog, "DELAY", 0)
    res = catalog.complete(sid, minutes=1)
    assert res["models"]["expanded"] == 1 and res["models"]["added"] == 3
    products = db.catalog_products(sid, None, "", 100)[0]
    names = [p["name"] for p in products]
    assert "AEDs" not in names and "Gloves" in names and "Powerheart G5 AED®" in names
    assert all(p["image_url"] for p in products)
    sections = {s["name"]: s for s in db.catalog_sections(sid)}
    assert sections["AEDs"]["parent_id"] == "fa" and sections["AEDs"]["count"] == 3
