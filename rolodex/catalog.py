"""Copy a supplier's product catalog from their website: their sections, every product, photo links.

    python -m rolodex.catalog <supplier id> [site url] [--minutes 20]   crawl and save into the rolodex
    python -m rolodex.catalog crawl <site url> [--out FILE] [--minutes N] crawl only, print or write the JSON
    python -m rolodex.catalog photos <supplier id>                      find photos for products without one
    python -m rolodex.catalog complete <supplier id>                    list the models on category pages, then photos
                                                                        (runs by itself after every catalog save)

Tries, cheapest first: a Shopify store's JSON, a WooCommerce store's API, then the sitemap with each
product page's structured data (schema.org Product + BreadcrumbList, which most shop platforms
emit). Obeys robots.txt and waits between requests. Prints a JSON summary; when it finds nothing,
the site needs a hand-made catalog (see the analyze skill) saved with `python -m rolodex.tasks save
catalog <id> FILE`.

Photos aren't downloaded here: the app shows them from the supplier's site through /img, which
keeps a cached copy.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urljoin, urlparse

from . import pagecards

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
DELAY = 0.5          # seconds between requests to the same site
MAX_PAGES = 3000     # product pages read in sitemap mode


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", html.unescape(str(text)).lower()).strip("-")[:80] or "section"


def clean(text: str, limit: int = 300) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(text or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


class Site:
    def __init__(self, url: str, minutes: float = 20):
        if "://" not in url:
            url = "https://" + url
        self.deadline = time.time() + minutes * 60
        self.last = 0.0
        self.requests = 0
        self.language = ""
        self.rebase(url)

    def rebase(self, url: str) -> None:
        """Crawl from url's site; a path (like /en) stays on the root so the English copy is used."""
        p = urlparse(url)
        self.host = f"{p.scheme}://{p.netloc}"
        self.root = self.host + p.path.rstrip("/")
        self.robots = urllib.robotparser.RobotFileParser()
        try:
            self.robots.parse(self.get(self.host + "/robots.txt", check_robots=False).decode("utf-8", "replace").splitlines())
        except Exception:
            self.robots.parse([])

    def out_of_time(self) -> bool:
        return time.time() > self.deadline

    def get(self, url: str, check_robots: bool = True, limit: int = 15_000_000) -> bytes:
        if check_robots and not self.robots.can_fetch(UA, url):
            raise PermissionError(f"robots.txt disallows {url}")
        wait = DELAY - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        self.requests += 1
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = r.read(limit)
            if r.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
                try:
                    data = gzip.decompress(data)
                except OSError:
                    pass
        # Re-rooted redirects (example.com -> www.example.com) keep working for later requests.
        return data

    def json(self, path: str):
        try:
            return json.loads(self.get(self.root + path if path.startswith("/") else urljoin(self.root + "/", path)))
        except (urllib.error.URLError, ValueError, PermissionError, TimeoutError, OSError):
            return None


# ---------- Shopify ----------

def shopify(site: Site) -> dict | None:
    first = site.json("/products.json?limit=250&page=1")
    if not isinstance(first, dict) or "products" not in first:
        return None
    products: dict[int, dict] = {}
    page, batch = 1, first["products"]
    while batch and not site.out_of_time():
        for p in batch:
            products[p["id"]] = p
        page += 1
        data = site.json(f"/products.json?limit=250&page={page}")
        batch = data.get("products") if isinstance(data, dict) else None
    sections, section_of = [], {}
    typed = [p for p in products.values() if (p.get("product_type") or "").strip()]
    if products and len(typed) >= 0.7 * len(products):
        # Product types are how the store itself classifies things; collections are often promotions.
        for p in products.values():
            t = clean(p.get("product_type") or "Other products", 200)
            section_of[p["id"]] = slug(t)
            if slug(t) not in {x["id"] for x in sections}:
                sections.append({"id": slug(t), "name": t, "parent_id": ""})
        sections.sort(key=lambda x: x["name"].lower())
    else:
        page, sizes = 1, {}
        while not site.out_of_time():
            data = site.json(f"/collections.json?limit=250&page={page}")
            cols = data.get("collections") if isinstance(data, dict) else None
            if not cols:
                break
            for c in cols:
                title = c.get("title") or c["handle"]
                if c.get("handle") in ("all", "frontpage", "home", "home-page") or PROMO.search(title) \
                        or not c.get("products_count", 1):
                    continue
                sections.append({"id": slug(c["handle"]), "name": clean(title, 200), "parent_id": ""})
                members, cpage = [], 1
                while not site.out_of_time():
                    cd = site.json(f"/collections/{c['handle']}/products.json?limit=250&page={cpage}")
                    items = cd.get("products") if isinstance(cd, dict) else None
                    if not items:
                        break
                    members += [p["id"] for p in items]
                    cpage += 1
                for pid in members:   # the smallest collection holding a product is the most specific
                    if pid not in section_of or len(members) < sizes[section_of[pid]]:
                        section_of[pid] = slug(c["handle"])
                sizes[slug(c["handle"])] = len(members)
            page += 1
        used = set(section_of.values())
        sections = [x for x in sections if x["id"] in used]
    out = []
    for p in products.values():
        variants = p.get("variants") or [{}]
        prices = sorted({v.get("price") for v in variants if v.get("price")}, key=lambda x: float(x))
        price = (f"${prices[0]}" + (f" - ${prices[-1]}" if len(prices) > 1 else "")) if prices else ""
        images = [i.get("src") for i in p.get("images", []) if i.get("src")]
        opts = [f"{o['name']}: {', '.join(o.get('values', [])[:6])}" for o in p.get("options", [])
                if o.get("name") and o.get("name") != "Title"]
        out.append({"id": str(p["id"]), "section_id": section_of.get(p["id"], "other"), "name": clean(p.get("title"), 300),
                    "sku": str(variants[0].get("sku") or ""), "details": "; ".join(opts)[:300] or clean(p.get("product_type"), 100),
                    "description": clean(p.get("body_html"), 4000), "price": price,
                    "page_url": f"{site.root}/products/{p.get('handle')}", "image_url": images[0] if images else "",
                    "images": images[1:12]})
    if any(p["section_id"] == "other" for p in out):
        sections.append({"id": "other", "name": "Other products", "parent_id": ""})
    return {"platform": "shopify", "sections": sections, "products": out}


# ---------- WooCommerce ----------

def woocommerce(site: Site) -> dict | None:
    first = site.json("/wp-json/wc/store/v1/products?per_page=100&page=1")
    if not isinstance(first, list) or not first:
        return None
    cats, page = [], 1
    while not site.out_of_time():
        data = site.json(f"/wp-json/wc/store/v1/products/categories?per_page=100&page={page}")
        if not isinstance(data, list) or not data:
            break
        cats += data
        page += 1
    by_id = {c["id"]: c for c in cats}
    sections = [{"id": str(c["id"]), "name": clean(c.get("name"), 200),
                 "parent_id": str(c["parent"]) if c.get("parent") in by_id else ""}
                for c in cats if c.get("count", 1)]
    out, page, batch = [], 1, first
    while batch and not site.out_of_time():
        for p in batch:
            prices = p.get("prices") or {}
            price = ""
            unit = int(prices.get("currency_minor_unit") or 2)
            # A $0 or $1 "price" is a quote-only shop's placeholder, not a price.
            if prices.get("price") and int(prices["price"]) / 10 ** unit > 1:
                price = f"{prices.get('currency_symbol', '$')}{int(prices['price']) / 10 ** unit:.2f}"
            images = [i.get("src") for i in p.get("images", []) if i.get("src")]
            pcats = [str(c["id"]) for c in p.get("categories", []) if c.get("id") in by_id]
            # The deepest category is the most specific section.
            depth = lambda cid: len(section_path(sections, cid))
            out.append({"id": str(p["id"]), "section_id": max(pcats, key=depth) if pcats else "",
                        "name": clean(p.get("name"), 300), "sku": str(p.get("sku") or ""),
                        "details": clean(p.get("short_description"), 300), "description": clean(p.get("description"), 4000),
                        "price": price, "page_url": p.get("permalink", ""), "image_url": images[0] if images else "",
                        "images": images[1:12],
                        "specs": [[clean(a.get("name"), 80), ", ".join(clean(t.get("name"), 80) for t in a.get("terms", []))]
                                  for a in p.get("attributes", []) if a.get("name") and a.get("terms")]})
        page += 1
        batch = site.json(f"/wp-json/wc/store/v1/products?per_page=100&page={page}")
        batch = batch if isinstance(batch, list) else None
    return {"platform": "woocommerce", "sections": sections, "products": out}


def section_path(sections: list[dict], sid: str) -> list[str]:
    by_id = {s["id"]: s for s in sections}
    path = []
    while sid in by_id and sid not in path and len(path) < 12:
        path.append(sid)
        sid = by_id[sid]["parent_id"]
    return path


# ---------- any site: sitemap + structured data ----------

PROMO = re.compile(r"sale|% ?off|\bnew\b|best ?sell|gift|clearance|deal|featured|trending|promo|bundle|last chance|\bfree\b", re.I)
PRODUCTISH = re.compile(r"/(product|products|p|item|items|shop|catalog|store|sku)[/-]|product", re.I)


def sitemap_urls(site: Site) -> list[str]:
    own = [site.root + "/sitemap.xml", site.root + "/sitemap_index.xml", site.root + "/wp-sitemap.xml"]
    starts = (own if site.root != site.host else []) + list(site.robots.site_maps() or []) or own
    seen, urls, queue = set(), [], list(starts)
    while queue and len(seen) < 200 and not site.out_of_time():
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            text = site.get(sm).decode("utf-8", "replace")
        except Exception:
            continue
        locs = [html.unescape(u.strip()) for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text)]
        if "<sitemapindex" in text:
            # Product sitemaps first.
            queue = sorted(locs, key=lambda u: 0 if "product" in u.lower() else 1) + queue
        else:
            urls += locs
    if site.root != site.host:   # the English copy only, not the other languages
        urls = [u for u in urls if u.startswith(site.root + "/")]
    return list(dict.fromkeys(urls))


def _lang(page: str) -> str:
    m = re.search(r"<html[^>]*\slang=[\"']?([A-Za-z-]+)", page[:5000], re.I)
    return m.group(1).lower() if m else ""


def english_root(site: Site) -> None:
    """Crawl the English version of a site when the main one is in another language (a Spanish
    home page with an /en/ copy, say), so product names come out in English. Sets site.language
    to the language that will be copied."""
    try:
        page = site.get(site.root + "/").decode("utf-8", "replace")
    except Exception:
        return
    site.language = _lang(page)
    if not site.language or site.language.startswith("en"):
        return
    links = []
    for tag in re.findall(r"<link[^>]+hreflang=[\"']?en[A-Za-z-]*[\"']?[^>]*>", page, re.I):
        m = re.search(r"href=[\"']([^\"']+)", tag)
        if m:
            links.append(urljoin(site.root + "/", html.unescape(m.group(1))))
    links.sort(key=lambda u: 0 if re.search(r"/en-us\b|/us\b", u, re.I) else 1)
    candidates = links + [site.host + p for p in ("/en/", "/en-us/", "/us/en/", "/en-US/")]
    for url in dict.fromkeys(candidates):
        if site.out_of_time():
            return
        try:
            other = site.get(url).decode("utf-8", "replace")
        except Exception:
            continue
        if _lang(other).startswith("en"):
            site.rebase(url)
            site.language = _lang(other)
            return


def _jsonld(page: str) -> list[dict]:
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack += d
            elif isinstance(d, dict):
                out.append(d)
                stack += [v for k, v in d.items() if k in ("@graph", "mainEntity", "itemListElement") and isinstance(v, (list, dict))]
    return out


def _types(d: dict) -> set[str]:
    t = d.get("@type", [])
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def _meta(page: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]*content=["\']([^"\']+)', page, re.I) or \
        re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{prop}["\']', page, re.I)
    return html.unescape(m.group(1)) if m else ""


def _image(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return _image(value.get("url") or value.get("contentUrl"))
    if isinstance(value, list):
        return [u for v in value for u in _image(v)]
    return []


def read_product_page(url: str, page: str) -> dict | None:
    items = _jsonld(page)
    product = next((d for d in items if "product" in _types(d)), None)
    crumbs = next((d for d in items if "breadcrumblist" in _types(d)), None)
    if product is None:
        product = _microdata_product(page)
        if product is None:
            return None
        crumbs = crumbs or _microdata_crumbs(page)
    offers = product.get("offers") or {}
    offers = offers[0] if isinstance(offers, list) and offers else offers
    price = ""
    if isinstance(offers, dict):
        low, high = offers.get("lowPrice"), offers.get("highPrice")
        value = offers.get("price") or low
        cur = offers.get("priceCurrency", "USD")
        if value not in (None, ""):
            sym = "$" if cur in ("USD", "CAD") else cur + " "
            price = f"{sym}{value}" + (f" - {sym}{high}" if high and str(high) != str(value) else "")
    images = [urljoin(url, u) for u in _image(product.get("image"))] or ([urljoin(url, _meta(page, "og:image"))] if _meta(page, "og:image") else [])
    path = []
    if crumbs:
        elems = crumbs.get("itemListElement") or []
        elems = sorted([e for e in elems if isinstance(e, dict)], key=lambda e: int(e.get("position", 0) or 0))
        names = []
        for e in elems:
            item = e.get("item")
            name = e.get("name") or (item.get("name") if isinstance(item, dict) else "")
            if name:
                names.append(clean(name, 200))
        if names and names[0].lower() in ("home", "homepage", "shop", "store"):
            names = names[1:]
        names = [n for i, n in enumerate(names) if i == 0 or n.lower() != names[i - 1].lower()]
        norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
        pname = norm(clean(product.get("name") or _meta(page, "og:title"), 300))
        while names and pname and (pname.startswith(norm(names[-1])) or norm(names[-1]).startswith(pname[:15])):
            names = names[:-1]   # the last crumb is the product itself
        path = names[:4]
    brand = product.get("brand")
    brand = brand.get("name") if isinstance(brand, dict) else brand
    details = "; ".join(x for x in [clean(brand, 60) if brand else "", clean(product.get("size") or "", 60)] if x)
    return {"name": clean(product.get("name") or _meta(page, "og:title"), 300),
            "sku": clean(product.get("sku") or product.get("mpn") or product.get("productID") or "", 100),
            "details": details, "description": clean(product.get("description") or _meta(page, "og:description"), 4000),
            "price": price, "page_url": url, "image_url": images[0] if images else "", "images": images[1:12],
            "path": path}


def _itemprop(page: str, name: str) -> str:
    m = re.search(rf'itemprop=["\']{name}["\'][^>]*content=["\']([^"\']+)', page, re.I) or \
        re.search(rf'content=["\']([^"\']+)["\'][^>]*itemprop=["\']{name}["\']', page, re.I) or \
        re.search(rf'itemprop=["\']{name}["\'][^>]*>([^<]{{1,300}})<', page, re.I)
    return clean(m.group(1), 300) if m else ""


def _microdata_product(page: str) -> dict | None:
    """Pages without JSON-LD: schema.org microdata, or an og:type=product page."""
    if not re.search(r'schema\.org/Product["\']', page, re.I) and _meta(page, "og:type").lower() != "product":
        return None
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S | re.I)
    name = _meta(page, "og:title") or (clean(h1.group(1)) if h1 else "")
    price = _itemprop(page, "price") or _meta(page, "product:price:amount") or _meta(page, "og:price:amount")
    return {"name": name, "sku": _itemprop(page, "sku") or _itemprop(page, "mpn"),
            "brand": _itemprop(page, "brand"), "description": _meta(page, "og:description"),
            "image": _meta(page, "og:image") or _itemprop(page, "image"),
            "offers": {"price": re.sub(r"[^0-9.]", "", price), "priceCurrency": "USD"} if price else {}}


def _microdata_crumbs(page: str) -> dict | None:
    m = re.search(r"BreadcrumbList", page)
    if not m:
        return None
    chunk = page[m.start(): m.start() + 6000]
    names = [clean(n, 200) for n in re.findall(r'itemprop=["\']name["\'][^>]*>([^<]+)<', chunk)]
    names = [n for n in names if n]
    return {"@type": "BreadcrumbList", "itemListElement": [{"position": i, "name": n} for i, n in enumerate(names)]}


def url_shape(url: str) -> str:
    """Pages built from the same template share a shape: /products/W, W_p_#.W, /shop/W/W ..."""
    path = urlparse(url).path
    path = re.sub(r"[A-Za-z0-9]+(?:-+[A-Za-z0-9]+)+", "W", path)   # hyphenated slugs
    path = re.sub(r"\d+", "#", path)
    return re.sub(r"[A-Za-z]{3,}", "W", path)


def from_sitemap(site: Site) -> dict | None:
    urls = sitemap_urls(site)
    if not urls:
        return None
    # Find which kinds of page are product pages: try a few of each URL shape, biggest groups first.
    groups: dict[str, list[str]] = {}
    for u in urls:
        groups.setdefault(url_shape(u), []).append(u)
    ranked = sorted(groups.values(), key=lambda g: (0 if any(PRODUCTISH.search(urlparse(u).path) for u in g[:5]) else 1, -len(g)))
    products, product_groups, read = [], [], set()
    for g in ranked[:25]:
        if site.out_of_time():
            break
        hits = 0
        for u in g[:3]:
            read.add(u)
            try:
                p = read_product_page(u, site.get(u, limit=3_000_000).decode("utf-8", "replace"))
            except Exception:
                continue
            if p and p["name"]:
                products.append(p)
                hits += 1
        if hits:
            product_groups.append(g)
    for u in [u for g in product_groups for u in g if u not in read][:MAX_PAGES]:
        if site.out_of_time():
            break
        try:
            p = read_product_page(u, site.get(u, limit=3_000_000).decode("utf-8", "replace"))
        except Exception:
            continue
        if p and p["name"]:
            products.append(p)
    if not products:
        return None
    sections, out = {}, []
    for i, p in enumerate(products):
        parent = ""
        for depth in range(len(p["path"])):
            sid = slug("/".join(p["path"][:depth + 1]))
            sections.setdefault(sid, {"id": sid, "name": p["path"][depth], "parent_id": parent})
            parent = sid
        p["section_id"] = parent or "other"
        p["id"] = p["sku"] or slug(urlparse(p["page_url"]).path) or str(i)
        out.append({k: v for k, v in p.items() if k != "path"})
    if any(p["section_id"] == "other" for p in out):
        sections["other"] = {"id": "other", "name": "Products", "parent_id": ""}
    return {"platform": "sitemap", "sections": list(sections.values()), "products": out}


SITEWIDE = re.compile(r"logo|banner|og[_-]?fb|share|placeholder|icon|sprite|favicon|default[_-]?og", re.I)


def page_photos(url: str, page: str, name: str = "") -> list[str]:
    """The product's photos on its page: structured data first, then og:image / twitter:image, then
    the pictures in the page body (those named like the product first)."""
    p = read_product_page(url, page)
    found = ([p["image_url"]] + p["images"]) if p and p["image_url"] else []
    for prop in ("og:image", "og:image:secure_url", "twitter:image"):
        if _meta(page, prop):
            found.append(urljoin(url, _meta(page, prop)))
    found = [u for u in dict.fromkeys(found) if u and not SITEWIDE.search(urlparse(u).path)]
    return found or pagecards.best_photo(url, page, name)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", html.unescape(str(text)).lower())


def expand(supplier_id: int, minutes: float = 10, depth: int = 2) -> dict:
    """Open each catalog entry's page; when it's a category page showing several products or models
    (a grid of cards, each with a title, photo and link), the entry becomes a section holding those
    products. Works for any site, store or not. Products already in the catalog aren't added twice."""
    from . import db
    s = db.get_supplier(supplier_id)
    sections = [dict(x) for x in db.catalog_sections(supplier_id)]
    products = db.catalog_products(supplier_id, None, "", 100000)[0]
    todo = [p for p in products if p["page_url"].startswith(("http://", "https://"))]
    if not todo:
        return {"expanded": 0, "added": 0}
    site = Site(todo[0]["page_url"], minutes)
    known_urls = {p["page_url"].split("#")[0].rstrip("/") for p in products}
    known_names = {_norm(p["name"]) for p in products}
    opened: set[str] = set()
    expanded = added = 0
    out = list(products)
    for level in range(depth):
        new_out, grew = [], False
        for p in out:
            url = p["page_url"].split("#")[0]
            if site.out_of_time() or not url.startswith(("http://", "https://")) or url in opened \
                    or (level and not p.get("_new")):
                new_out.append(p)
                continue
            opened.add(url)
            try:
                page = site.get(url, limit=3_000_000).decode("utf-8", "replace")
            except Exception:
                new_out.append(p)
                continue
            if read_product_page(url, page):   # a real product page, not a category
                new_out.append(p)
                continue
            cards = [c for c in pagecards.product_cards(url, page)
                     if c["page_url"].split("#")[0].rstrip("/") not in known_urls and _norm(c["name"]) not in known_names]
            if len(cards) < 2:
                new_out.append(p)
                continue
            sid = f"x-{slug(p['name'])}"[:80]
            while any(x["id"] == sid for x in sections):
                sid += "-"
            sections.append({"id": sid, "name": p["name"], "parent_id": p["section_id"]})
            expanded += 1
            grew = True
            for c in cards:
                known_urls.add(c["page_url"].split("#")[0].rstrip("/"))
                known_names.add(_norm(c["name"]))
                new_out.append({"id": f"{sid}/{slug(c['name'])}", "section_id": sid, "name": c["name"], "sku": "",
                                "details": c["details"][:300], "description": c["details"], "price": "",
                                "page_url": c["page_url"], "image_url": c["image_url"], "images": c["images"], "_new": True})
                added += 1
        out = new_out
        if not grew:
            break
    if expanded:
        db.save_catalog(supplier_id, {"source": s["catalog"].get("source", ""), "note": s["catalog"].get("note", ""),
                                      "platform": s["catalog"].get("method", ""), "sections": sections,
                                      "products": [{k: v for k, v in x.items() if k != "_new"} for x in out]})
    return {"expanded": expanded, "added": added, "pages_opened": len(opened)}


COMPLETE_VERSION = 2   # raise when complete() learns something new, so existing catalogs get it once
# 1: models from category pages, missing photos. 2: specs, dimensions, datasheets and descriptions.


def complete(supplier_id: int, minutes: float = 20) -> dict:
    """After any catalog copy: open category pages for the models on them, find missing photos, then
    read each product's page for its specs, dimensions, datasheets and description."""
    from . import db
    res = {"models": expand(supplier_id, minutes * 0.4), "photos": fill_photos(supplier_id, minutes * 0.2),
           "specs": fill_specs(supplier_id, minutes * 0.4)}
    s = db.get_supplier(supplier_id)
    db.update_supplier(supplier_id, catalog={**s["catalog"], "completed": COMPLETE_VERSION})
    return res


def fill_specs(supplier_id: int, minutes: float = 10) -> dict:
    """Read each product's own page for what the catalog list leaves out: spec tables and label/value
    lists (dimensions, material, pack size), datasheet links, and the page's description. Pages shared
    by several products (a category page) aren't used for specs, only a product's own page."""
    from . import db
    products, _ = db.catalog_products(supplier_id, None, "", 100000)
    pages: dict[str, list[dict]] = {}
    for p in products:
        url = p["page_url"].split("#")[0]
        if url.startswith(("http://", "https://")):
            pages.setdefault(url, []).append(p)
    own = {u: ps[0] for u, ps in pages.items() if len(ps) == 1}
    if not own:
        return {"checked": 0, "with_specs": 0}
    site = Site(next(iter(own)), minutes)
    checked = improved = 0
    with db.connect() as conn:
        for url, p in own.items():
            if site.out_of_time():
                break
            try:
                page = site.get(url, limit=3_000_000).decode("utf-8", "replace")
            except Exception:
                continue
            checked += 1
            found = pagecards.product_specs(url, page)
            have = {(a.lower(), b.lower()) for a, b in p["specs"]}
            specs = p["specs"] + [x for x in found["specs"] if (x[0].lower(), x[1].lower()) not in have]
            known = {f["url"] for f in p["files"]}
            files = p["files"] + [f for f in found["files"] if f["url"] not in known]
            desc = p["description"]
            if len(found["description"]) > len(desc) + 40:   # the page says more than the list did
                desc = found["description"]
            if specs != p["specs"] or files != p["files"] or desc != p["description"]:
                improved += 1
                conn.execute("UPDATE catalog_products SET specs = ?, files = ?, description = ? "
                             "WHERE supplier_id = ? AND id = ?",
                             (json.dumps(specs[:60]), json.dumps(files[:8]), desc[:4000], supplier_id, p["id"]))
    with_specs = sum(1 for p in db.catalog_products(supplier_id, None, "", 100000)[0] if p["specs"] or p["files"])
    return {"checked": checked, "improved": improved, "with_specs": with_specs}


def english_outdated() -> None:
    """Catalogs copied automatically from a site in another language before the crawler looked for the
    site's English version: copy them again from the English one (the app runs this once at start)."""
    from . import db
    import logging
    log = logging.getLogger("rolodex.catalog")
    for s in db.all_suppliers():
        cat = s.get("catalog") or {}
        if not cat.get("total") or "language" in cat or cat.get("method") in ("", "by hand") or not cat.get("source"):
            continue
        try:
            site = Site(cat["source"], 3)
            english_root(site)
            if site.language and not site.language.startswith("en") or site.root.rstrip("/") == cat["source"].rstrip("/"):
                db.update_supplier(s["id"], catalog={**cat, "language": site.language})
                continue
            result = crawl(site.root, 20)
            if result["products"] and result.get("language", "").startswith("en"):
                db.save_catalog(s["id"], result)
                log.info("Copied %s's catalog again in English: %s", s["company"], complete(s["id"]))
            else:
                db.update_supplier(s["id"], catalog={**cat, "language": site.language})
        except Exception as e:
            log.warning("Couldn't recopy %s's catalog in English: %s", s["company"], e)


def complete_outdated() -> None:
    """Bring catalogs copied before the current complete() up to date (the app runs this once at start)."""
    from . import db
    import logging
    english_outdated()
    for s in db.all_suppliers():
        cat = s.get("catalog") or {}
        if cat.get("total") and cat.get("completed", 0) < COMPLETE_VERSION:
            try:
                logging.getLogger("rolodex.catalog").info("Completing %s's catalog: %s", s["company"], complete(s["id"]))
            except Exception as e:
                logging.getLogger("rolodex.catalog").warning("Couldn't complete %s's catalog: %s", s["company"], e)


def fill_photos(supplier_id: int, minutes: float = 15) -> dict:
    """Find a photo for catalog products that have a page link but no photo, from each product's page.
    A picture that turns up on several different products' pages is the site's generic image: skipped."""
    from . import db
    products, _ = db.catalog_products(supplier_id, None, "", 100000)
    todo = [p for p in products if not p["image_url"] and p["page_url"].startswith(("http://", "https://"))]
    if not todo:
        return {"checked": 0, "added": 0}
    site = Site(todo[0]["page_url"], minutes)
    found: dict[str, list[str]] = {}
    named: dict[str, list[str]] = {}
    pages_cache: dict[str, str] = {}
    for p in todo:
        if site.out_of_time():
            break
        if p["page_url"] not in pages_cache:
            try:
                pages_cache[p["page_url"]] = site.get(p["page_url"], limit=3_000_000).decode("utf-8", "replace")
            except Exception:
                pages_cache[p["page_url"]] = ""
        page = pages_cache[p["page_url"]]
        found[p["id"]] = page_photos(p["page_url"], page, p["name"]) if page else []
        named[p["id"]] = pagecards.named_photos(p["page_url"], page, p["name"]) if page else []
    # Images used by 3+ different pages are site-wide (but several products sharing ONE page share its photo).
    users: dict[str, set[str]] = {}
    for p in todo:
        for u in found.get(p["id"], []):
            users.setdefault(u, set()).add(p["page_url"])
    added = 0
    with db.connect() as conn:
        for p in todo:
            # A picture the page labels with this product's name is its photo even if sister products share it.
            photos = named.get(p["id"]) or [u for u in found.get(p["id"], []) if len(users[u]) < 3]
            if photos:
                conn.execute("UPDATE catalog_products SET image_url = ?, images = ? WHERE supplier_id = ? AND id = ?",
                             (photos[0], json.dumps(photos[1:12]), supplier_id, p["id"]))
                added += 1
    s = db.get_supplier(supplier_id)
    with_photos = sum(1 for p in db.catalog_products(supplier_id, None, "", 100000)[0] if p["image_url"])
    db.update_supplier(supplier_id, catalog={**s["catalog"], "with_photos": with_photos})
    return {"checked": len(found), "added": added, "with_photos": with_photos, "total": len(products)}


def crawl(url: str, minutes: float = 20) -> dict:
    site = Site(url, minutes)
    english_root(site)
    result = None
    for method in (shopify, woocommerce, from_sitemap):
        try:
            result = method(site)
        except Exception as e:   # one broken method shouldn't stop the others
            print(f"{method.__name__}: {e}", file=sys.stderr)
            result = None
        if result and result["products"]:
            break
    result = result or {"platform": "", "sections": [], "products": []}
    result["source"] = site.root
    result["note"] = "Stopped at the time limit; the catalog may be incomplete." if site.out_of_time() else ""
    result["requests"] = site.requests
    result["language"] = site.language
    return result


def main(argv: list[str]) -> None:
    minutes = 20.0
    if "--minutes" in argv:
        i = argv.index("--minutes")
        minutes = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) == 2 and argv[0] == "crawl":
        result = crawl(argv[1], minutes)
        text = json.dumps(result, indent=1, ensure_ascii=False)
        if out:
            open(out, "w", encoding="utf-8").write(text)
            print(json.dumps({"platform": result["platform"], "products": len(result["products"]),
                              "sections": len(result["sections"]), "file": out}))
        else:
            print(text)
        return
    if len(argv) == 2 and argv[0] == "complete" and argv[1].isdigit():
        from . import db
        db.init()
        print(json.dumps(complete(int(argv[1]), minutes)))
        return
    if len(argv) == 2 and argv[0] == "photos" and argv[1].isdigit():
        from . import db
        db.init()
        print(json.dumps(fill_photos(int(argv[1]), minutes)))
        return
    if len(argv) in (1, 2) and argv[0].isdigit():
        from . import db
        db.init()
        s = db.get_supplier(int(argv[0]))
        if s is None:
            sys.exit(f"No supplier {argv[0]}.")
        url = argv[1] if len(argv) == 2 else s["website"]
        if not url:
            sys.exit(json.dumps({"saved": False, "error": "No website on file; pass the site URL."}))
        result = crawl(url, minutes)
        if not result["products"]:
            print(json.dumps({"saved": False, "found": 0, "source": result["source"],
                              "next_step": "No product data found automatically. Build the catalog by hand from their "
                                           "site and save it with: python -m rolodex.tasks save catalog <id> FILE"}))
            return
        db.save_catalog(s["id"], result)
        extra = complete(s["id"], min(15.0, minutes))
        summary = db.get_supplier(s["id"])["catalog"] | extra
        db.update_supplier(s["id"], progress="")
        db.analysis_finished(s["id"])
        lang = result.get("language", "")
        if lang and not lang.startswith("en"):
            summary["next_step"] = (f"Their site is in '{lang}' with no English version. Translate the catalog into "
                                    "English (see the skill) and save it again.")
        print(json.dumps({"saved": True, "platform": result["platform"], **summary}))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
