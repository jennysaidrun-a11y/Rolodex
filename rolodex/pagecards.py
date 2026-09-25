"""Read a web page the way a person scans it: the grid of product cards on a category page, and the
photo that goes with a product. Used by the catalog copy (rolodex/catalog.py) for sites without an
online store, where a page like "AEDs" shows several models, each with a title, a photo and a link.

Standard library only (html.parser), so it runs anywhere the app runs.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
CHROME_TAGS = {"header", "nav", "footer", "aside", "form"}
CHROME_CLASS = re.compile(r"(^|[\s_-])(nav|navbar|menu|megamenu|header|footer|breadcrumbs?|cookie|modal|"
                          r"social|share|newsletter|subscribe|login|search|skip)([\s_-]|$)", re.I)
HEADINGS = {"h2", "h3", "h4", "h5", "h6"}
TITLE_CLASS = re.compile(r"(title|name|heading|headline)", re.I)
SITEWIDE = re.compile(r"logo|banner|og[_-]?fb|share|placeholder|icon|sprite|favicon|default[_-]?og|spacer|pixel|"
                      r"avatar|badge|arrow|chevron|loader|header|hero-bg", re.I)
# Cards that lead to articles, videos, people or contact pages rather than products.
NOT_PRODUCT = re.compile(r"/(blog|news|newsroom|resources?|articles?|case-stud|webinars?|videos?|podcasts?|"
                         r"white-?papers?|about|about-us|careers?|jobs|contact|events?|press|locations?|login|"
                         r"account|cart|privacy|terms|faq|support)(/|$|[?#-])", re.I)
NOT_PRODUCT_TITLE = re.compile(r"^(contact|call|get a quote|request|learn more|read more|featured|related|"
                               r"resources?|shop now|sign up|subscribe|faq|why |how |what |see all|view all)", re.I)
HIDDEN_TEXT = re.compile(r"(visually-hidden|sr-only|screen-reader)", re.I)
# Blocks of other products (related, recently viewed...) and grids under a heading like "Resources".
RELATED = re.compile(r"related|similar|also|recommend|recent|upsell|cross-?sell|resources?|testimonial|blog|news", re.I)
SIDE_HEADING = re.compile(r"(related|similar|you may also|also like|recommended|recently viewed|resources|"
                          r"featured resources|learn more|news|blog|articles|testimonials|customers say|"
                          r"case stud|why choose|how it works|our story|about us)", re.I)
IMG_EXT = re.compile(r"\.(jpe?g|png|webp|avif|gif)(\?|$)", re.I)


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag: str, attrs: dict, parent: "Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node] = []
        self.text: list[tuple[int, str]] = []

    def cls(self) -> str:
        return self.attrs.get("class", "") or ""

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def all_text(self) -> str:
        """The node's text in reading order (text between child tags stays where it was)."""
        parts: list[str] = []

        def add(n: "Node", depth: int = 0) -> None:
            if n.tag in SKIP_TAGS or HIDDEN_TEXT.search(n.cls()) or depth > 200:
                return
            t = 0
            for i, c in enumerate(n.children):
                while t < len(n.text) and n.text[t][0] <= i:
                    parts.append(n.text[t][1])
                    t += 1
                add(c, depth + 1)
            parts.extend(x for _, x in n.text[t:])
        add(self)
        return re.sub(r"\s+", " ", html.unescape(" ".join(parts))).strip()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if data.strip():
            self.cur.text.append((len(self.cur.children), data))   # where it sits among the child tags


def parse(page: str) -> Node:
    b = _Builder()
    try:
        b.feed(page)
        b.close()
    except Exception:   # a broken page still gives what was read so far
        pass
    return b.root


def _in_main(n: Node) -> bool:
    a = n.parent
    while a is not None:
        if a.tag in ("main", "article") or a.attrs.get("role") == "main":
            return True
        a = a.parent
    return False


def _is_chrome(n: Node) -> bool:
    if n.tag in ("html", "body", "main", "article"):   # page-wide classes like "nav-dropdown-has-arrow"
        return False
    if n.tag in CHROME_TAGS or n.tag in SKIP_TAGS or n.attrs.get("role") in ("navigation", "banner", "contentinfo"):
        return True
    m = CHROME_CLASS.search(n.cls())
    # Inside the page's main content a "product-header" / "product-footer" block is content, not the site's own.
    return bool(m) and not (m.group(2).lower() in ("header", "footer") and _in_main(n))


def content_nodes(root: Node, skip_related: bool = False):
    """Every node outside the page's header, menus and footer (and, if asked, related-product blocks)."""
    stack = [root]
    while stack:
        n = stack.pop()
        if n is not root and (_is_chrome(n) or (skip_related and RELATED.search(n.cls() + " " + n.attrs.get("id", "")))):
            continue
        yield n
        stack.extend(reversed(n.children))


def _img_urls(n: Node, base: str) -> list[str]:
    out = []
    if n.tag in ("img", "source"):
        # Lazy-loading sites keep the real picture in data-src, data-mediasrc, data-lazy... attributes.
        keys = sorted(n.attrs, key=lambda k: (not k.startswith("data-"), "srcset" in k))
        for key in keys:
            v = n.attrs.get(key, "").strip()
            if not v or v.startswith("data:") or key in ("alt", "title", "class", "id", "style", "sizes"):
                continue
            if "srcset" in key:
                # the largest candidate: the last one listed ("url 1x, url 2x"; URLs may hold commas)
                v = re.split(r",\s+", v)[-1].strip().split(" ")[0]
            elif key != "src" and not IMG_EXT.search(v.split("?")[0] + "?"):
                continue
            v = re.sub(r"\{width\}", "800", re.sub(r"\{height\}", "800", v))   # size templates
            out.append(urljoin(base, html.unescape(v)))
    style = n.attrs.get("style", "")
    for m in re.finditer(r"background(?:-image)?\s*:[^;]*url\(\s*['\"]?([^'\")]+)", style, re.I):
        out.append(urljoin(base, html.unescape(m.group(1))))
    for key in ("data-bg", "data-background", "data-background-image"):
        if n.attrs.get(key):
            out.append(urljoin(base, html.unescape(n.attrs[key])))
    return [u for u in out if u.startswith(("http://", "https://")) and not SITEWIDE.search(urlparse(u).path)]


def images_in(n: Node, base: str) -> list[tuple[str, str]]:
    """(url, alt) for every picture inside n, in page order."""
    out = []
    for x in n.walk():
        if x.tag in SKIP_TAGS:
            continue
        for u in _img_urls(x, base):
            out.append((u, x.attrs.get("alt", "") or x.attrs.get("title", "")))
    seen, uniq = set(), []
    for u, alt in out:
        key = re.sub(r"\?.*$", "", u)
        if key not in seen:
            seen.add(key)
            uniq.append((u, alt))
    return uniq


def _titles(n: Node) -> list[str]:
    found = []
    for x in n.walk():
        if x.tag in HEADINGS or (x.tag in ("strong", "b", "span", "div", "p", "a") and TITLE_CLASS.search(x.cls())
                                 and len(x.all_text()) < 120 and not x.children):
            t = x.all_text()
            if 2 < len(t) < 140:
                found.append(t)
    return list(dict.fromkeys(re.sub(r"\s+", " ", t).strip() for t in found))


def _link(n: Node, base: str) -> str:
    up = n
    while up is not None:
        if up.tag == "a" and up.attrs.get("href"):
            return urljoin(base, html.unescape(up.attrs["href"]))
        up = up.parent
    for x in n.walk():
        if x.tag == "a" and x.attrs.get("href") and not x.attrs["href"].startswith(("#", "tel:", "mailto:", "javascript:")):
            return urljoin(base, html.unescape(x.attrs["href"]))
    return ""


def _signature(n: Node) -> str:
    classes = sorted(c for c in n.cls().split() if not re.search(r"\d", c))[:3]
    return n.tag + "." + ".".join(classes)


def product_cards(url: str, page: str) -> list[dict]:
    """The product (or model) cards on a category page: [{name, image_url, images, page_url, details}].

    A card is the smallest block holding one title and a picture. A real grid repeats the same kind of
    block, so only groups of 2+ alike blocks count; blocks that lead to articles, videos or contact
    pages are left out."""
    root = parse(page)
    host = urlparse(url).netloc.lower().removeprefix("www.")
    order = {id(n): i for i, n in enumerate(root.walk())}
    headings = [(order[id(n)], n) for n in root.walk() if n.tag in ("h1", "h2", "h3") and not _is_chrome(n)]
    candidates = []
    for n in content_nodes(root, skip_related=True):
        if n.tag in ("root", "html", "body", "main") or n.tag in HEADINGS:
            continue
        titles = _titles(n)
        if len(titles) != 1:
            continue
        imgs = images_in(n, url)
        if not imgs:
            continue
        candidates.append(n)
    # Keep the smallest blocks: drop any candidate that contains another candidate.
    ids = {id(c) for c in candidates}
    smallest = []
    for c in candidates:
        if not any(id(x) in ids for x in c.walk() if x is not c):
            smallest.append(c)
    # Widen each block to the whole card (a flip card keeps its text on the back, beside the photo side).
    chosen = {id(c) for c in smallest}

    def widen(c: Node) -> Node:
        while c.parent is not None and c.parent.tag not in ("root", "html", "body", "main") \
                and len(_titles(c.parent)) == 1 and not any(id(x) in chosen for x in c.parent.walk() if x is not c and id(x) != id(c.parent)):
            c = c.parent
        return c

    smallest = [widen(c) for c in smallest]
    groups: dict[str, list[Node]] = {}
    for c in smallest:
        groups.setdefault(_signature(c), []).append(c)
    cards, seen = [], set()
    for members in groups.values():
        if len(members) < 2:
            continue
        # The heading just above the grid says what it is: "Resources", "You may also like"... aren't products.
        first = min(order[id(m)] for m in members)
        inside = {id(x) for m in members for x in m.walk()}
        above = [h for i, h in headings if i < first and id(h) not in inside]
        if above and SIDE_HEADING.search(above[-1].all_text()):
            continue
        for c in members:
            name = _titles(c)[0]
            link = _link(c, url)
            if NOT_PRODUCT_TITLE.search(name):
                continue
            if not link or link.split("#")[0].rstrip("/") == url.split("#")[0].rstrip("/"):
                continue   # a card with no page of its own is a step or a feature, not a product
            if link:
                lh = urlparse(link).netloc.lower().removeprefix("www.")
                if (lh and lh != host) or NOT_PRODUCT.search(urlparse(link).path):
                    continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            imgs = [u for u, _ in images_in(c, url)]
            body = re.sub(r"\b(read|learn) more\b.*$", "", c.all_text().replace(name, ""), flags=re.I)
            body = re.sub(r"\s+", " ", body).strip()
            cards.append({"name": name, "image_url": imgs[0], "images": imgs[1:6],
                          "page_url": link,
                          "details": body[:200]})
    return cards


def _words(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "our", "your", "service", "services", "products", "product"}
    return {w for w in re.findall(r"[a-z0-9]{2,}", html.unescape(text).lower()) if w not in stop}


def named_photos(url: str, page: str, name: str) -> list[str]:
    """Pictures on the page whose alt text or file name carries the product's name (or model number):
    the site says they show this product, even when the same picture is used for its sister products."""
    want = _words(name)
    if not want:
        return []
    out = []
    for n in content_nodes(parse(page), skip_related=True):
        for u in _img_urls(n, url):
            alt = _words(n.attrs.get("alt", "") + " " + n.attrs.get("title", ""))
            if want <= alt or (len(want) > 1 and len(want & alt) >= max(2, len(want) - 1)):
                out.append(u)
    return list(dict.fromkeys(out))


def best_photo(url: str, page: str, name: str) -> list[str]:
    """Photos for a product from the body of its page when it has no structured data: pictures whose
    file name or alt text shares a word with the product name first, then the first content picture."""
    root = parse(page)
    imgs = []
    for n in content_nodes(root):
        for u in _img_urls(n, url):
            imgs.append((u, n.attrs.get("alt", "")))
    imgs = [(u, a) for u, a in dict.fromkeys(imgs) if IMG_EXT.search(urlparse(u).path) or "image" in u.lower()]
    want = _words(name)
    scored = []
    for i, (u, alt) in enumerate(imgs):
        hay = _words(urlparse(u).path.replace("-", " ").replace("_", " ") + " " + alt)
        scored.append((-(len(want & hay)), i, u))
    scored.sort()
    matching = [u for s, _, u in scored if s < 0]
    rest = [u for _, _, u in sorted(scored, key=lambda x: x[1]) if u not in matching]
    return list(dict.fromkeys(matching + rest))[:8]


# ---------- a product page's specs, datasheets and description ----------

NOT_SPEC_LABEL = re.compile(r"^(q|a|pros|cons|note|notes|disclaimer|warning|phone|tel|telephone|fax|e-?mail|address|hours|call|contact|share|follow|posted|"
                            r"categor(y|ies)|tags?|sku|price|quantity|qty|cart|reviews?|rating|home)\b", re.I)
_UNIT = r"(?:\"|''|inch(?:es)?|in\.?|microns?|mic|mil|mm|cm|ft|m|')"
DIMENSIONS = re.compile(r"\b\d+(?:[.,]\d+)?\s*" + _UNIT + r"?\s*[x×]\s*\d+(?:[.,]\d+)?"
                        r"(?:\s*" + _UNIT + r"?\s*[x×]\s*\d+(?:[.,]\d+)?)?\s*" + _UNIT + r"?(?![a-z])", re.I)
DESC_AREA = re.compile(r"description|tab-?panel|product-?(details|info|content)|specification|features|entry-content", re.I)
DOC_LINK = re.compile(r"\.pdf(\?|#|$)|datasheet|data-sheet|spec-?sheet|ficha|sds|msds|tds", re.I)


def _cells(row: Node) -> list[str]:
    return [c.all_text() for c in row.children if c.tag in ("th", "td")]


def _table_specs(table: Node) -> list[list[str]]:
    rows = [n for n in table.walk() if n.tag == "tr"]
    grid = [_cells(r) for r in rows]
    grid = [g for g in grid if any(g)]
    if not grid:
        return []
    if all(len(g) == 2 for g in grid):
        return [[a, b] for a, b in grid if a and b]
    head = grid[0]
    if len(head) >= 3 and all(n.tag == "th" for n in rows[0].children if n.tag in ("th", "td")):
        out = []
        for g in grid[1:41]:
            value = "; ".join(f"{h}: {c}" for h, c in zip(head[1:], g[1:]) if c)
            if g and g[0] and value:
                out.append([g[0], value])
        return out
    return []


def product_specs(url: str, page: str) -> dict:
    """What a product page says about the item beyond its name: spec tables and label/value lists
    (dimensions, material, pack size...), datasheet and spec-sheet links, and the page's own
    description text. Returns {specs: [[label, value]], files: [{name, url}], description}."""
    root = parse(page)
    specs, files, seen_files = [], [], set()
    nodes = list(content_nodes(root, skip_related=True))
    inside_table = set()
    for n in nodes:
        if n.tag == "table":
            for x in n.walk():
                inside_table.add(id(x))
            specs += _table_specs(n)
        elif n.tag == "dl":
            kids = [c for c in n.children if c.tag in ("dt", "dd")]
            for a, b in zip(kids, kids[1:]):
                if a.tag == "dt" and b.tag == "dd" and a.all_text() and b.all_text():
                    specs.append([a.all_text(), b.all_text()])
        elif n.tag in ("li", "p") and id(n) not in inside_table:
            text = n.all_text()
            m = re.match(r"^([A-Za-zÀ-ÿ][\w /()&.,%-]{1,40}?)\s*:\s*(.{1,240})$", text)
            if m and not any(c.tag in ("li", "p", "table") for c in n.walk() if c is not n):
                specs.append([m.group(1).strip(), m.group(2).strip()])
        elif n.tag == "a" and n.attrs.get("href") and DOC_LINK.search(n.attrs["href"]):
            href = urljoin(url, html.unescape(n.attrs["href"]))
            if href.startswith(("http://", "https://")) and href not in seen_files:
                seen_files.add(href)
                name = n.all_text() or urlparse(href).path.rsplit("/", 1)[-1]
                files.append({"name": name[:120], "url": href})
    clean_specs, seen = [], set()
    for label, value in specs:
        label, value = label.strip(" :")[:80], value.strip()[:300]
        key = (label.lower(), value.lower())
        if not label or not value or key in seen or NOT_SPEC_LABEL.search(label) or len(label) > 60:
            continue
        seen.add(key)
        clean_specs.append([label, value])
    # The product's own text: paragraphs around the page title (h1), outside menus and related blocks.
    description = ""
    h1 = next((n for n in nodes if n.tag == "h1"), None)
    if h1 is not None:
        area, texts = h1, []
        for _ in range(7):
            if area.parent is None:
                break
            area = area.parent
            texts = [p.all_text() for p in content_nodes(area, skip_related=True)
                     if p.tag == "p" and id(p) not in inside_table]
            texts = [t for t in texts if len(t) > 30]
            if sum(len(t) for t in texts) >= 200:
                break
        # plus the description / specifications tabs further down the page
        for area in nodes:
            if DESC_AREA.search(area.cls() + " " + area.attrs.get("id", "")) and area.tag in ("div", "section"):
                texts += [x.all_text() for x in content_nodes(area, skip_related=True)
                          if x.tag in ("p", "li", "h3", "h4") and id(x) not in inside_table and len(x.all_text()) > 3
                          and not any(c.tag in ("p", "li") for c in x.walk() if c is not x)]
        description = "\n".join(dict.fromkeys(texts))[:4000]
    if not any(DIMENSIONS.search(v) or re.search(r"dimension|size|measure|medida|length|width|height", l, re.I)
               for l, v in clean_specs):
        sizes = list(dict.fromkeys(m.group(0).strip() for m in DIMENSIONS.finditer(description or "")))
        if sizes:
            clean_specs.append(["Sizes mentioned", ", ".join(sizes[:8])])
    return {"specs": clean_specs[:60], "files": files[:8], "description": description}
