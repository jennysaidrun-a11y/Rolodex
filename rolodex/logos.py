"""Each supplier's logo, shown as a small icon next to its name so staff recognise who is who.
Found on their website: the logo the site declares (structured data), the logo image in its header,
then its app icon or favicon. Stored as a URL on their site and shown through /img like product photos."""

from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import urljoin

from . import db, pagecards

log = logging.getLogger("rolodex.logos")


def _site_url(website: str) -> str:
    website = (website or "").strip()
    if not website:
        return ""
    return website if "://" in website else "https://" + website


def candidates(url: str, page: str) -> list[str]:
    """Logo URLs on a home page, best first."""
    found: list[str] = []
    # 1. Organization logo in JSON-LD
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
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
                if "@graph" in d:
                    stack.append(d["@graph"])
                logo = d.get("logo")
                if isinstance(logo, dict):
                    logo = logo.get("url") or logo.get("contentUrl")
                if isinstance(logo, str) and logo:
                    found.append(urljoin(url, logo))
    # 2. An image in the page header / brand link that says it's the logo
    root = pagecards.parse(page)
    for n in root.walk():
        if n.tag != "img":
            continue
        a, near_logo = n, False
        for _ in range(4):
            label = " ".join((a.cls(), a.attrs.get("id", ""), a.attrs.get("alt", ""), a.attrs.get("src", "")))
            if re.search(r"logo|brand", label, re.I):
                near_logo = True
                break
            a = a.parent
            if a is None:
                break
        if near_logo:
            for attr in ("src", "data-src", "data-lazy-src", "srcset", "data-srcset"):
                v = n.attrs.get(attr, "").strip().split(",")[0].strip().split(" ")[0]
                if v and not v.startswith("data:"):
                    found.append(urljoin(url, v))
                    break
    # 3. App icons and favicons, biggest first
    icons = []
    for tag in re.findall(r"<link[^>]+>", page, re.I):
        rel = re.search(r"rel=[\"']([^\"']+)", tag, re.I)
        href = re.search(r"href=[\"']([^\"']+)", tag, re.I)
        if not rel or not href or "icon" not in rel.group(1).lower():
            continue
        size = re.search(r"sizes=[\"'](\d+)", tag, re.I)
        big = int(size.group(1)) if size else (180 if "apple" in rel.group(1).lower() else 32)
        icons.append((big, urljoin(url, href.group(1))))
    found += [u for _, u in sorted(icons, reverse=True)]
    found.append(urljoin(url, "/favicon.ico"))
    return [u for u in dict.fromkeys(found) if u.startswith(("http://", "https://"))]


def find_logo(website: str) -> str:
    """The first logo on their site that actually loads as an image, or ''."""
    from .catalog import Site
    from .images import fetch_image
    url = _site_url(website)
    if not url:
        return ""
    try:
        site = Site(url, 1)
        page = site.get(site.root + "/", limit=3_000_000).decode("utf-8", "replace")
    except Exception:
        page = ""
    found = candidates(url, page)
    if len(found) <= 1:   # a bare splash / language page: look at their English home page too
        try:
            from .catalog import english_root
            english_root(site)
            other = site.get(site.root + "/", limit=3_000_000).decode("utf-8", "replace")
            found = list(dict.fromkeys(candidates(site.root + "/", other) + found))
        except Exception:
            pass
    for u in found[:8]:
        if fetch_image(u, 200):
            return u
    return ""


def enabled() -> bool:
    return os.environ.get("ROLODEX_FETCH_LOGOS", "1") == "1"


def ensure(supplier_id: int) -> str:
    """Find and save the supplier's logo if it has none yet; returns the logo URL."""
    s = db.get_supplier(supplier_id)
    if s is None or s.get("logo_url") or not enabled():
        return (s or {}).get("logo_url", "")
    try:
        logo = find_logo(s["website"] or (s["catalog"] or {}).get("source", ""))
    except Exception as e:
        log.warning("Couldn't find %s's logo: %s", s["company"], e)
        return ""
    if logo:
        db.update_supplier(supplier_id, logo_url=logo)
    return logo


def ensure_all() -> None:
    """Logos for every supplier that has a website but no logo yet (the app runs this at start)."""
    for s in db.all_suppliers():
        if not s.get("logo_url") and (s.get("website") or (s.get("catalog") or {}).get("source")):
            ensure(s["id"])
