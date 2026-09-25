"""Turns a catalog product's copied text into clean tables for its page: specifications (with
dimensions and sizes), a sizes / item-numbers table from "Codes and Presentations" lists, the
description as paragraphs and bullet lists, and its documents (safety data sheets first)."""
import re
from urllib.parse import urlparse

from .pagecards import DIMENSIONS

LANGUAGES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
             "nl": "Dutch", "it": "Italian", "zhs": "Chinese", "zh": "Chinese", "zht": "Chinese",
             "ja": "Japanese", "ko": "Korean", "pl": "Polish", "sv": "Swedish", "da": "Danish",
             "fi": "Finnish", "no": "Norwegian", "cs": "Czech", "hu": "Hungarian", "ro": "Romanian",
             "tr": "Turkish", "ru": "Russian", "el": "Greek", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian"}
NATIVE = {"english": "English", "español": "Spanish", "espanol": "Spanish", "français": "French",
          "francais": "French", "deutsch": "German", "dutch": "Dutch", "nederlands": "Dutch",
          "português": "Portuguese", "italiano": "Italian", "简体中文": "Chinese", "繁體中文": "Chinese",
          "日本語": "Japanese", "한국어": "Korean", "polski": "Polish", "svenska": "Swedish"}
SDS = re.compile(r"safety\s*data|\bm?sds\b|sds[_-]|hoja de seguridad|fiche de (données de )?sécurité", re.I)
KINDS = [(SDS, "Safety data sheet"),
         (re.compile(r"product\s*(information|data)|\bpds\b|\bpi[_-]", re.I), "Product data sheet"),
         (re.compile(r"technical|\btds\b", re.I), "Technical data sheet"),
         (re.compile(r"spec(ification)?s?[\s_-]*sheet|ficha", re.I), "Spec sheet"),
         (re.compile(r"data[\s_-]*sheet", re.I), "Data sheet"),
         (re.compile(r"certif|kosher|halal|nsf", re.I), "Certificate"),
         (re.compile(r"brochure|catalog|flyer", re.I), "Brochure"),
         (re.compile(r"manual|instruction|guide", re.I), "Instructions")]
SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kb|mb)\b", re.I)
CODE = re.compile(r"^(?=[^\s]*\d)[A-Z0-9][A-Z0-9./_-]{2,}$", re.I)
SIZE_HEADING = re.compile(r"(codes?|presentations?|sizes?|models?|references?|item numbers?)\b.*:$", re.I)
# site boilerplate around download sections ("verify any downloaded files...", "happy to assist")
BOILERPLATE = re.compile(r"download(ed)?\s+(files|section)|no liability|legally binding|do not hesitate to contact|"
                         r"happy to assist|even closer to you|cannot guarantee that the files", re.I)
ID_LABEL = re.compile(r"^(article|item|part|product|model|catalog|cat\.?)[\s-]*(no\.?|number|#|code)$|^(sku|upc)$", re.I)


def _doc_kind(name: str, url: str) -> str:
    for pattern, kind in KINDS:
        if pattern.search(name):
            return kind
    file = urlparse(url).path.rsplit("/", 1)[-1]
    for pattern, kind in KINDS:
        if pattern.search(file):
            return kind
    return re.sub(r"\s*\(?\bpdf\b.*$", "", name, flags=re.I).strip(" -/") or "Document"


def documents(files: list[dict]) -> list[dict]:
    """One row per document: kind, language, region, size. Language links listed after a document
    ("Español", "Français") are that same document in another language. Safety data sheets first,
    English and United States first within each kind. `index` is the file's place in p.files."""
    rows, last = [], None
    for i, f in enumerate(files):
        name = re.sub(r"[﻿​]", "", f.get("name") or "").strip()
        url = f.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        native = NATIVE.get(name.lower())
        if native and last:
            row = {**last, "index": i, "language": native, "size": "", "url": url}
        else:
            language, region = "", ""
            m = re.search(r"\(([A-Za-z]{2,3})(?:,\s*([^)]+))?\)", name)
            if m and m.group(1).lower() in LANGUAGES:
                language, region = LANGUAGES[m.group(1).lower()], (m.group(2) or "").strip()
            elif native:
                language = native
            else:
                code = re.search(r"_([A-Z]{2})_([A-Z]{2,3})\.pdf$", urlparse(url).path)
                if code and code.group(2).lower() in LANGUAGES:
                    language = LANGUAGES[code.group(2).lower()]
            size = SIZE.search(name)
            row = {"index": i, "kind": _doc_kind(name, url), "language": language, "region": region,
                   "size": f"{size.group(1)} {size.group(2).upper()}" if size else "", "url": url}
        rows.append(row)
        last = row
    order = {kind: n for n, (_, kind) in enumerate(KINDS)}
    rows.sort(key=lambda r: (order.get(r["kind"], len(order)), r["language"] not in ("English", ""),
                             r["region"] not in ("United States", "USA", "US", ""), r["region"], r["language"]))
    return rows


def _size_row(line: str) -> list[str]:
    """'CH5235 – Stretch Film 500 mm x 23 mic (approx. 4.5 kg) – Without Handle' -> code, product, size."""
    parts = line.split(None, 1)
    code, rest = (parts[0], parts[1] if len(parts) > 1 else "") if CODE.match(parts[0]) else ("", line)
    rest = rest.strip(" –-:")
    dims = [m.group(0).strip() for m in DIMENSIONS.finditer(rest)]
    return [code, rest, ", ".join(dims)]


def view(p: dict) -> dict:
    """What the product page shows: specs rows, a sizes table (or None), description blocks, documents."""
    specs = [list(r) for r in p.get("specs") or []]
    details = p.get("details") or ""
    lines = [ln.strip() for ln in (p.get("description") or "").splitlines() if ln.strip()]
    # "Article-No: 340557 Synthetic, silicone based fluid..." copied as a spec and as the first line
    for row in specs:
        m = re.match(r"^(\S+)\s+(.{12,})$", row[1])
        if ID_LABEL.match(row[0]) and m and (m.group(2) in details or any(m.group(2) in ln for ln in lines[:3])):
            row[1] = m.group(1)
    if lines and re.match(r"^[\w .-]{2,25}:\s*\S+", lines[0]) and any(
            lines[0].lower().startswith(r[0].lower()) for r in specs):
        lines = lines[1:]
    # "Colors: Navy, Tan" / "ATPV 9.5 | ARC 2 | UL 2112" in the details line: table rows
    labels = {r[0].lower() for r in specs}
    for part in re.split(r"(?<=\.)\s+(?=[A-Z][\w ]{1,20}:)", details):
        m = re.match(r"^([A-Z][\w ]{1,20}):\s*(.+?)\.?$", part.strip())
        if m and m.group(1).lower() not in labels:
            specs.append([m.group(1), m.group(2)])
        elif part.count("|") >= 2 and "ratings" not in labels:
            specs.append(["Ratings", " · ".join(x.strip(" .") for x in re.split(r"[|;]", part) if x.strip(" ."))])
    # the description: headings ending in ':', the lines under them as lists; size lists as a table
    blocks, sizes, i = [], [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.endswith(":") and len(ln) < 60:
            items = []
            i += 1
            while i < len(lines) and not (lines[i].endswith(":") and len(lines[i]) < 60) and len(lines[i]) < 160:
                items.append(lines[i])
                i += 1
            if SIZE_HEADING.search(ln) and items:
                sizes += [_size_row(x) for x in items]
            elif items:
                blocks.append({"heading": ln.rstrip(":"), "points": items})
            continue
        m = re.match(r"^([A-Z][\w /-]{1,30}):\s+(.{1,80})$", ln)
        if m and not m.group(2).endswith(".") and m.group(1).lower() not in labels:
            specs.append([m.group(1), m.group(2)])     # a one-line "Label: value" is a spec
        elif not BOILERPLATE.search(ln):
            blocks.append({"text": ln})
        i += 1
    sizes = [r for r in sizes if any(r)]
    size_table = None
    if sizes:
        cols = [n for n, h in enumerate(["Item #", "Product", "Size"]) if any(r[n] for r in sizes)]
        size_table = {"headers": [["Item #", "Product", "Size"][n] for n in cols],
                      "rows": [[r[n] for n in cols] for r in sizes]}
        specs = [r for r in specs if r[0] != "Sizes mentioned"]
    for r in specs:
        if r[0] == "Sizes mentioned":
            r[0] = "Sizes"
    # dimensions first, like a spec sheet
    dim = re.compile(r"dimension|size|length|width|height|depth|diameter|thickness|gauge|capacity|weight|volume", re.I)
    specs.sort(key=lambda r: not (dim.search(r[0]) or DIMENSIONS.search(r[1])))
    return {"specs": specs, "sizes": size_table, "blocks": blocks, "docs": documents(p.get("files") or [])}
