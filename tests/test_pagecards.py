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


PRODUCT = """<html><body class="nav-dropdown-has-arrow"><header class="site-header"><a href="/x.pdf">Menu PDF</a></header>
<main><div class="product-main"><h1>Bread Bag 8x4x18</h1><p>Clear poly bread bag for sliced loaves, 1.25 mil.</p>
<p><strong>CH100</strong> – Bread Bag 8 x 4 x 18 in – case of 1000</p></div>
<div class="product-footer"><div class="woocommerce-Tabs-panel" id="tab-description"><p>Food-safe LDPE.</p>
<a href="/files/bread-bag-datasheet.pdf">View Datasheet</a></div>
<table class="shop_attributes"><tr><th>Material</th><td>LDPE</td></tr><tr><th>Case count</th><td>1000</td></tr></table>
<dl><dt>Thickness</dt><dd>1.25 mil</dd></dl><ul><li>Color: Clear</li><li>Phone: 555-1212</li></ul></div></main>
<footer class="footer"><p>Address: 1 Main St</p></footer></body></html>"""


def test_product_specs_from_its_page():
    r = pagecards.product_specs("https://bags.example/p/bread-bag", PRODUCT)
    specs = dict(r["specs"])
    assert specs["Material"] == "LDPE" and specs["Case count"] == "1000" and specs["Thickness"] == "1.25 mil"
    assert specs["Color"] == "Clear" and "Phone" not in specs and "Address" not in specs
    assert r["files"] == [{"name": "View Datasheet", "url": "https://bags.example/files/bread-bag-datasheet.pdf"}]
    # text in reading order (the item code before the words after it), the tabs below included
    assert "CH100 – Bread Bag 8 x 4 x 18 in – case of 1000" in r["description"] and "Food-safe LDPE." in r["description"]


def test_complete_adds_specs_and_english(client, monkeypatch):  # noqa: F811
    sid = new_supplier()
    db.save_catalog(sid, {"source": "https://bags.example", "sections": [],
                          "products": [{"id": "bb", "name": "Bread Bag", "page_url": "https://bags.example/p/bread-bag",
                                        "image_url": "https://bags.example/i.jpg", "price": ""}]})
    monkeypatch.setattr(catalog.Site, "get", lambda self, url, check_robots=True, limit=0:
                        PRODUCT.encode() if url.endswith("/p/bread-bag") else b"")
    monkeypatch.setattr(catalog, "DELAY", 0)
    res = catalog.complete(sid, minutes=1)
    assert res["specs"]["improved"] == 1
    p = db.catalog_products(sid, None, "", 10)[0][0]
    assert ["Material", "LDPE"] in p["specs"] and p["files"][0]["url"].endswith("datasheet.pdf")
    assert db.catalog_products(sid, None, "LDPE", 10)[1] == 1   # keyword search sees the specs
    page = client.get(f"/supplier/{sid}/catalog/item/bb").text
    assert "Specifications" in page and "Case count" in page and "bread-bag-datasheet.pdf" in page


def test_english_version_of_a_site_is_preferred(monkeypatch):
    pages = {"https://tape.example/": '<html lang="es-AR"><head><link rel="alternate" hreflang="en-US" '
                                      'href="https://tape.example/en/"></head><body>Cintas adhesivas</body></html>',
             "https://tape.example/en/": '<html lang="en-US"><body>Adhesive tapes</body></html>'}
    monkeypatch.setattr(catalog.Site, "get", lambda self, url, check_robots=True, limit=0: pages.get(url, "").encode())
    monkeypatch.setattr(catalog, "DELAY", 0)
    site = catalog.Site("https://tape.example", 1)
    catalog.english_root(site)
    assert site.root == "https://tape.example/en" and site.language == "en-us"
    assert site.json("/wp-json/x") is None   # asked for under /en
