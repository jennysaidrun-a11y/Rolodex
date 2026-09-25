"""The web app: works in a phone browser (add to home screen) and on any computer.

    uvicorn rolodex.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hmac
import io
import logging
import os
import threading
import uuid
from urllib.parse import urlencode
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool

from . import claude, config, db, docview, gitsync, present, recheck, runner
from .images import fetch_image

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")
# Links come from web research: only ever render http(s) ones.
# Link that drops one filter (e.g. a tag or category pill's ✕) and keeps the rest.
templates.env.globals["without"] = lambda request, key, value: "/?" + urlencode(
    [(k, v) for k, v in request.query_params.multi_items() if not (k == key and v == value)])
templates.env.globals["use_api"] = config.USE_API
templates.env.globals["git_sync"] = config.GIT_SYNC
templates.env.globals["analysis_progress"] = lambda: runner.progress()
templates.env.globals["sync_status"] = lambda: gitsync.last_result
templates.env.globals["img"] = lambda url, w=400: ("/img?" + urlencode({"u": url, "w": w})) if str(url).lower().startswith(("http://", "https://")) else ""
templates.env.filters["link"] = lambda u: u if str(u).lower().startswith(("http://", "https://")) else ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    db.init()
    if config.USE_API and config.AUTO_RECHECK:
        recheck.start_background()
    if not config.USE_API and config.AUTO_RECHECK and not runner.available():
        runner.start_rechecks()
    if config.GIT_SYNC:
        gitsync.start_background()
    if os.environ.get("ROLODEX_CATALOG_COMPLETE", "1") == "1":
        from . import catalog, logos

        def upgrade():
            logos.ensure_all()
            catalog.complete_outdated()
        threading.Thread(target=upgrade, name="catalog-upgrade", daemon=True).start()
    yield


app = FastAPI(title="Supplier Rolodex", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

OPEN_PATHS = ("/login", "/static/")


@app.middleware("http")
async def require_login(request: Request, call_next):
    if config.APP_PASSWORD and not request.session.get("ok") and not request.url.path.startswith(OPEN_PATHS):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# Added after the login check so it runs first and request.session is ready.
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=60 * 60 * 24 * 180)


def page(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def back_to(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


# ---------- login ----------

@app.get("/login")
def login_form(request: Request):
    return page(request, "login.html", error="")


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if hmac.compare_digest(password, config.APP_PASSWORD):
        request.session["ok"] = True
        return back_to("/")
    return page(request, "login.html", error="Wrong password.")


# ---------- browse / search / ask ----------

@app.get("/")
def home(request: Request, q: str = "", category: list[str] = Query([]), attention: bool = False,
         tag: list[str] = Query([])):
    return page(request, "index.html", suppliers=db.search(q, category, attention, tag), q=q,
                selected_categories=category, attention=attention, categories=db.category_list(),
                selected_tags=tag, tag_counts=db.tag_counts(),
                filtering=bool(q or category or attention or tag),
                waiting_cards=len(db.unread_cards()), waiting_scans=len(runner.waiting()[1]),
                run=runner.progress(), can_run=runner.available(),
                attention_count=len(db.search(attention=True)), total=len(db.all_suppliers()))


@app.post("/ask")
async def ask(request: Request, question: str = Form(...)):
    suppliers = db.all_suppliers()
    by_id = {s["id"]: s for s in suppliers}
    notes = {s["id"]: [n["text"] for n in db.notes_for(s["id"])] for s in suppliers}
    error = ""
    if config.USE_API:
        try:
            result = await run_in_threadpool(claude.ask, question, suppliers, notes)
        except claude.ClaudeError as e:
            result, error = {"answer": "", "matches": []}, str(e)
    elif not runner.available():
        try:
            directory = claude.directory(suppliers, notes, {s["id"]: [x["name"] for x in db.catalog_sections(s["id"])]
                                                            for s in suppliers})
            result = await run_in_threadpool(runner.ask, question, directory)
        except RuntimeError as e:
            result, error = {"answer": "", "matches": []}, str(e)
    else:
        result = {"answer": "", "matches": []}
        error = ("Plain-English questions need Claude Code (in the Codespace) or the Claude API. "
                 "Use the keyword, category and tag filters, or /find-supplier in Claude Code.")
    matches = [(by_id[m["supplier_id"]], m["why"]) for m in result["matches"] if m["supplier_id"] in by_id]
    return page(request, "ask.html", question=question, answer=result["answer"], matches=matches, error=error)


# ---------- adding a card ----------

def _save_photo(upload: UploadFile) -> str:
    """Store a card photo as an upright JPEG, at most 1600 px on the long side."""
    try:
        img = Image.open(io.BytesIO(upload.file.read()))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, f"Couldn't open the photo '{upload.filename}'. Please use a JPEG or PNG.")
    img.thumbnail((1600, 1600))
    name = f"{uuid.uuid4().hex}.jpg"
    img.save(config.CARDS_DIR / name, "JPEG", quality=85)
    return name


@app.get("/add")
def add_form(request: Request, supplier: int | None = None, added: int = 0):
    return page(request, "add.html", supplier=db.get_supplier(supplier) if supplier else None, added=added,
                can_run=runner.available())


@app.post("/add")
async def add_card(request: Request, kind: str = Form("card"), supplier: int | None = Form(None),
                   front: UploadFile | None = File(None), back: UploadFile | None = File(None),
                   many: list[UploadFile] = File([]), then: str = Form(""), added: int = Form(0)):
    """A business card (front/back) or a pamphlet (a photo of its cover; research finds the PDF),
    for a new supplier or one already on file. `many`: several photos, one company each.
    then=another goes straight back to the form for the next card."""
    kind = "pamphlet" if kind == "pamphlet" else "card"
    many = [u for u in many if u is not None and u.filename]
    if many and not config.USE_API:
        for u in many:
            sid = db.create_supplier({"company": recheck.PLACEHOLDER_COMPANY})
            db.update_supplier(sid, status="unread")
            db.add_card(sid, [_save_photo(u)], kind)
        return back_to("/add?" + urlencode({"added": added + len(many)}))
    uploads = [u for u in ([front, back] if kind == "card" else [front]) if u is not None and u.filename]
    if not uploads:
        raise HTTPException(400, "Please add a photo.")
    existing = db.get_supplier(supplier) if supplier else None
    photos = [_save_photo(u) for u in uploads]

    if existing:
        supplier_id = existing["id"]
    else:
        supplier_id = db.create_supplier({"company": recheck.PLACEHOLDER_COMPANY})
        db.update_supplier(supplier_id, status="unread")
    card_id = db.add_card(supplier_id, photos, kind)
    if not config.USE_API:   # Claude Code reads it on the next analysis
        if then == "another":
            return back_to("/add?" + urlencode({"added": added + 1}))
        return back_to(f"/supplier/{supplier_id}")

    try:
        reading = await run_in_threadpool(claude.read_card, [config.CARDS_DIR / p for p in photos],
                                          db.category_list(), kind)
        error = ""
    except claude.ClaudeError as e:
        reading, error = {}, f"Couldn't read it automatically ({e}). Type the details in below."
    recheck.save_reading(card_id, reading)
    if existing:
        recheck.queue(supplier_id)   # new information: refresh the profile
        return back_to(f"/supplier/{supplier_id}")
    db.update_supplier(supplier_id, status="new")   # staff check the details, then it gets researched
    return back_to(f"/supplier/{supplier_id}/edit?" + urlencode({"new": 1, "error": error}))


# ---------- a supplier ----------

def _get(supplier_id: int) -> dict:
    s = db.get_supplier(supplier_id)
    if s is None:
        raise HTTPException(404, "No such supplier.")
    return s


@app.get("/supplier/{supplier_id}")
def supplier(request: Request, supplier_id: int, tab: str = ""):
    s = _get(supplier_id)
    return page(request, "supplier.html", s=s, p=s["profile"], tab=tab, cards=db.cards_for(supplier_id),
                notes=db.notes_for(supplier_id), checks=db.checks_for(supplier_id),
                catalog_preview=db.catalog_products(supplier_id, None, "", 8)[0])


@app.get("/supplier/{supplier_id}/edit")
def edit_form(request: Request, supplier_id: int, new: bool = False, error: str = ""):
    s = _get(supplier_id)
    return page(request, "edit.html", s=s, new=new, error=error, cards=db.cards_for(supplier_id),
                all_categories=list(dict.fromkeys(db.category_names() + s["categories"])), tag_groups=db.TAG_GROUPS,
                duplicates=db.possible_duplicates(s["company"], s["website"], s["email"], exclude=supplier_id))


@app.post("/supplier/{supplier_id}/edit")
def edit(supplier_id: int, company: str = Form(...), contact_name: str = Form(""),
         contact_title: str = Form(""), phone: str = Form(""), email: str = Form(""), website: str = Form(""),
         address: str = Form(""), categories: list[str] = Form([]), other_categories: str = Form(""),
         keep_tags: list[str] = Form([]), new_tags: str = Form(""), new_tag_group: str = Form("Other")):
    s = _get(supplier_id)
    # New categories typed here join the managed list so they show up everywhere.
    cats = categories + [db.add_category(c) for c in other_categories.split(",") if c.strip()]
    # Tags: unticking a research tag removes it for good (rescans won't bring it back);
    # typed-in tags are staff tags and are kept across rescans.
    kept = {t.lower() for t in keep_tags}
    added = db.clean_tags([{"group": new_tag_group, "name": n} for n in new_tags.split(",")])
    added_names = {t["name"].lower() for t in added}
    removed = ({n.lower() for n in s["removed_tags"]}
               | {t["name"].lower() for t in s["tags"] if t["name"].lower() not in kept}) - added_names
    staff = [t for t in s["staff_tags"] if t["name"].lower() in kept and t["name"].lower() not in added_names]
    db.update_supplier(supplier_id, company=company.strip() or "Unknown company", contact_name=contact_name,
                       contact_title=contact_title, phone=phone, email=email, website=website, address=address,
                       categories=list(dict.fromkeys(cats)), staff_tags=staff + added,
                       removed_tags=sorted(removed))
    if s["status"] == "new":   # first save of a new card: go research it
        recheck.queue(supplier_id)
    return back_to(f"/supplier/{supplier_id}")


# ---------- managing the category list ----------

@app.get("/categories")
def categories_page(request: Request, error: str = ""):
    return page(request, "categories.html", categories=db.category_list(), error=error)


@app.post("/categories")
def category_add(name: str = Form(...), description: str = Form("")):
    if name.strip():
        db.add_category(name, description)
    return back_to("/categories")


@app.post("/categories/update")
def category_update(old: str = Form(...), name: str = Form(...), description: str = Form("")):
    db.update_category(old, name, description)
    return back_to("/categories")


@app.post("/categories/delete")
def category_delete(name: str = Form(...)):
    db.delete_category(name)
    return back_to("/categories")


@app.post("/categories/move")
def category_move(name: str = Form(...), step: int = Form(...)):
    db.move_category(name, 1 if step > 0 else -1)
    return back_to("/categories")


@app.post("/supplier/{supplier_id}/notes")
def add_note(supplier_id: int, text: str = Form(...), author: str = Form("")):
    _get(supplier_id)
    if text.strip():
        db.add_note(supplier_id, text.strip(), author.strip())
    return back_to(f"/supplier/{supplier_id}?tab=notes")


@app.post("/supplier/{supplier_id}/recheck")
def recheck_now(supplier_id: int):
    _get(supplier_id)
    recheck.queue(supplier_id)
    return back_to(f"/supplier/{supplier_id}")


@app.post("/scan")
def scan_selected(ids: list[int] = Form([]), return_to: str = Form("/")):
    """Manual scan of the suppliers ticked on the list; unreviewed or already-running ones are skipped."""
    for supplier_id in ids:
        s = db.get_supplier(supplier_id)
        if s and s["status"] not in ("unread", "new", "queued", "researching"):
            recheck.queue(supplier_id)
    return back_to(return_to if return_to.startswith("/") and not return_to.startswith("//") else "/")


@app.post("/supplier/{supplier_id}/reviewed")
def mark_reviewed(supplier_id: int):
    _get(supplier_id)
    db.update_supplier(supplier_id, needs_attention=0)
    return back_to(f"/supplier/{supplier_id}")


@app.post("/supplier/{supplier_id}/delete")
def delete(supplier_id: int):
    _get(supplier_id)
    for name in db.delete_supplier(supplier_id):
        (config.CARDS_DIR / name).unlink(missing_ok=True)
        (config.DOCS_DIR / name).unlink(missing_ok=True)
    return back_to("/")


@app.get("/docs/{name}")
def pdf_copy(name: str):
    path = config.DOCS_DIR / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf")


@app.get("/cards/{name}")
def card_photo(name: str):
    path = config.CARDS_DIR / Path(name).name   # no directory tricks
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# ---------- analysis by Claude Code (claude-code mode) ----------

@app.get("/analysis")
def analysis_status():
    return runner.progress()


@app.post("/analysis/start")
def analysis_start(return_to: str = Form("/")):
    why = runner.start("button")
    return back_to(_safe(return_to) + ("" if not why else ("&" if "?" in return_to else "?") + urlencode({"msg": why})))


@app.post("/analysis/cancel")
def analysis_cancel(return_to: str = Form("/")):
    runner.cancel()
    return back_to(_safe(return_to))


@app.get("/analysis/log")
def analysis_log(request: Request):
    return page(request, "log.html", log=runner._log_tail(), run=runner.progress())


@app.post("/sync")
def sync(return_to: str = Form("/")):
    gitsync.sync_now()
    return back_to(_safe(return_to))


def _safe(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") else "/"


# ---------- product catalogs ----------

PER_PAGE = 48


@app.get("/img")
def image(u: str, w: int = 0):
    """A supplier's product photo, fetched from their site once and then served from the cache."""
    found = fetch_image(u, w)
    if found is None:
        return FileResponse(HERE / "static" / "no-photo.svg", media_type="image/svg+xml",
                            headers={"Cache-Control": "max-age=3600"})
    path, media_type = found
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "max-age=2592000"})


@app.get("/supplier/{supplier_id}/catalog")
def catalog_page(request: Request, supplier_id: int, section: str = "", q: str = "", page_no: int = Query(1, alias="page")):
    s = _get(supplier_id)
    sections = db.catalog_sections(supplier_id)
    by_id = {x["id"]: x for x in sections}
    current = by_id.get(section)
    ids = db.section_and_below(sections, section) if current else None
    page_no = max(page_no, 1)
    products, total = db.catalog_products(supplier_id, ids, q, PER_PAGE, (page_no - 1) * PER_PAGE)
    trail = []
    x = current
    while x and len(trail) < 12:
        trail.insert(0, x)
        x = by_id.get(x["parent_id"])
    children = [x for x in sections if x["parent_id"] == (section if current else "")]
    return page(request, "catalog.html", s=s, sections=sections, current=current, trail=trail, children=children,
                products=products, total=total, q=q, page_no=page_no, pages=max(1, -(-total // PER_PAGE)),
                open_ids={t["id"] for t in trail})


@app.get("/supplier/{supplier_id}/catalog/item/{product_id:path}")
def catalog_item(request: Request, supplier_id: int, product_id: str):
    s = _get(supplier_id)
    p = db.catalog_product(supplier_id, product_id)
    if p is None:
        raise HTTPException(404, "That product isn't in the catalog any more.")
    sections = db.catalog_sections(supplier_id)
    by_id = {x["id"]: x for x in sections}
    trail, x = [], by_id.get(p["section_id"])
    while x and len(trail) < 12:
        trail.insert(0, x)
        x = by_id.get(x["parent_id"])
    prev_id, next_id = db.catalog_neighbours(supplier_id, p)
    return page(request, "product.html", s=s, p=p, v=present.view(p), trail=trail, prev_id=prev_id, next_id=next_id)


def _doc(supplier_id: int, product_id: str, index: int) -> tuple[dict, dict, dict]:
    """Only documents listed on a catalog product can be viewed (the viewer is not an open proxy)."""
    s = _get(supplier_id)
    p = db.catalog_product(supplier_id, product_id)
    if p is None or not 0 <= index < len(p["files"]) or not p["files"][index].get("url", "").startswith(("http://", "https://")):
        raise HTTPException(404, "That document isn't in the catalog any more.")
    row = next((d for d in present.documents(p["files"]) if d["index"] == index), None)
    return s, p, row or {"kind": "Document", "language": "", "region": "", "url": p["files"][index]["url"]}


@app.get("/supplier/{supplier_id}/doc/{index}")
async def doc_view(request: Request, supplier_id: int, index: int, p: str):
    """A safety data sheet or spec sheet, shown in the app page by page (read from their site, not saved)."""
    s, prod, d = _doc(supplier_id, p, index)
    data = await run_in_threadpool(docview.fetch, d["url"])
    pages = await run_in_threadpool(docview.page_count, data) if data else 0
    return page(request, "doc.html", s=s, p=prod, d=d, index=index, pages=pages)


@app.get("/supplier/{supplier_id}/doc/{index}/page/{number}")
async def doc_page(supplier_id: int, index: int, number: int, p: str, w: int = 1200):
    _, _, d = _doc(supplier_id, p, index)
    data = await run_in_threadpool(docview.fetch, d["url"])
    png = await run_in_threadpool(docview.render, data, number, min(max(w, 400), 2000)) if data else None
    if png is None:
        raise HTTPException(404, "No such page.")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/products")
def product_search(request: Request, q: str = "", page_no: int = Query(1, alias="page")):
    page_no = max(page_no, 1)
    products, total = db.catalog_products(None, None, q, PER_PAGE, (page_no - 1) * PER_PAGE) if q.strip() else ([], 0)
    return page(request, "products.html", q=q, products=products, total=total, page_no=page_no,
                pages=max(1, -(-total // PER_PAGE)))


@app.post("/products/ask")
async def product_ask(request: Request, question: str = Form(...)):
    """Search every catalog in plain English: Claude (Sonnet) reads the product list and picks."""
    products = db.all_catalog_products()
    by_key = {f"{p['supplier_id']}/{p['id']}": p for p in products}
    result, error = {"answer": "", "matches": []}, ""
    if not products:
        error = "There are no catalogs yet. Analyze a supplier first."
    elif config.USE_API:
        try:
            result = await run_in_threadpool(claude.ask_products, question, products)
        except claude.ClaudeError as e:
            error = str(e)
    elif not runner.available():
        try:
            result = await run_in_threadpool(runner.ask_products, question, claude.product_list(products))
        except RuntimeError as e:
            error = str(e)
    else:
        error = "Asking Claude needs Claude Code (in the Codespace) or the Claude API. Use the keyword search."
    matches, seen = [], set()
    for m in result.get("matches", []):
        key = str(m.get("key", ""))
        if key in by_key and key not in seen:
            seen.add(key)
            matches.append(dict(by_key[key], why=str(m.get("why", ""))))
    return page(request, "products.html", q="", question=question, answer=result.get("answer", ""),
                products=matches, total=len(matches), page_no=1, pages=1, error=error)
