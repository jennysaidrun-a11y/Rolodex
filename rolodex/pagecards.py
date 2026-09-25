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
        self.text: list[str] = []

    def cls(self) -> str:
        return self.attrs.get("class", "") or ""

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def all_text(self) -> str:
        parts = []
        for n in self.walk():
            if n.tag not in SKIP_TAGS and not HIDDEN_TEXT.search(n.cls()):
                parts += n.text
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
            self.cur.text.append(data)


def parse(page: str) -> Node:
    b = _Builder()
    try:
        b.feed(page)
        b.close()
    except Exception:   # a broken page still gives what was read so far
        pass
    return b.root


def _is_chrome(n: Node) -> bool:
    return n.tag in CHROME_TAGS or n.tag in SKIP_TAGS or bool(CHROME_CLASS.search(n.cls())) \
        or n.attrs.get("role") in ("navigation", "banner", "contentinfo")


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
