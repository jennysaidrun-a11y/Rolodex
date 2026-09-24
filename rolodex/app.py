"""The web app: works in a phone browser (add to home screen) and on any computer.

    uvicorn rolodex.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hmac
import io
import logging
import uuid
from urllib.parse import urlencode
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool

from . import claude, config, db, recheck

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")
# Links come from web research: only ever render http(s) ones.
templates.env.filters["link"] = lambda u: u if str(u).lower().startswith(("http://", "https://")) else ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    db.init()
    if config.AUTO_RECHECK:
        recheck.start_background()
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
def home(request: Request, q: str = "", category: str = "", attention: bool = False):
    return page(request, "index.html", suppliers=db.search(q, category, attention), q=q, category=category,
                attention=attention, categories=db.categories(),
                attention_count=len(db.search(attention=True)), total=len(db.all_suppliers()))


@app.post("/ask")
async def ask(request: Request, question: str = Form(...)):
    suppliers = db.all_suppliers()
    by_id = {s["id"]: s for s in suppliers}
    notes = {s["id"]: [n["text"] for n in db.notes_for(s["id"])] for s in suppliers}
    try:
        result = await run_in_threadpool(claude.ask, question, suppliers, notes)
        error = ""
    except claude.ClaudeError as e:
        result, error = {"answer": "", "matches": []}, str(e)
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
def add_form(request: Request):
    return page(request, "add.html")


@app.post("/add")
async def add_card(request: Request, front: UploadFile = File(...), back: UploadFile | None = File(None)):
    front_name = _save_photo(front)
    back_name = _save_photo(back) if back and back.filename else None
    try:
        card = await run_in_threadpool(claude.read_card, config.CARDS_DIR / front_name,
                                       config.CARDS_DIR / back_name if back_name else None)
        error = ""
    except claude.ClaudeError as e:
        card, error = {"company": "Unread card"}, f"Couldn't read the card automatically ({e}). Type it in below."
    supplier_id = db.create_supplier(card)
    db.add_card(supplier_id, front_name, back_name, card)
    if card.get("other_text"):
        db.add_note(supplier_id, f"From the card: {card['other_text']}", "card")
    if card.get("products_mentioned"):
        db.add_note(supplier_id, "Products on the card: " + ", ".join(card["products_mentioned"]), "card")
    return back_to(f"/supplier/{supplier_id}/edit?" + urlencode({"new": 1, "error": error}))


# ---------- a supplier ----------

def _get(supplier_id: int) -> dict:
    s = db.get_supplier(supplier_id)
    if s is None:
        raise HTTPException(404, "No such supplier.")
    return s


@app.get("/supplier/{supplier_id}")
def supplier(request: Request, supplier_id: int):
    s = _get(supplier_id)
    return page(request, "supplier.html", s=s, p=s["profile"], cards=db.cards_for(supplier_id),
                notes=db.notes_for(supplier_id), checks=db.checks_for(supplier_id))


@app.get("/supplier/{supplier_id}/edit")
def edit_form(request: Request, supplier_id: int, new: bool = False, error: str = ""):
    s = _get(supplier_id)
    return page(request, "edit.html", s=s, new=new, error=error, cards=db.cards_for(supplier_id),
                all_categories=sorted(set(claude.CATEGORIES) | set(db.categories())),
                duplicates=db.possible_duplicates(s["company"], s["website"], s["email"], exclude=supplier_id))


@app.post("/supplier/{supplier_id}/edit")
def edit(supplier_id: int, company: str = Form(...), contact_name: str = Form(""),
         contact_title: str = Form(""), phone: str = Form(""), email: str = Form(""), website: str = Form(""),
         address: str = Form(""), categories: list[str] = Form([]), other_categories: str = Form("")):
    s = _get(supplier_id)
    cats = categories + [c.strip() for c in other_categories.split(",") if c.strip()]
    db.update_supplier(supplier_id, company=company.strip() or "Unknown company", contact_name=contact_name,
                       contact_title=contact_title, phone=phone, email=email, website=website, address=address,
                       categories=list(dict.fromkeys(cats)))
    if s["status"] == "new":   # first save of a new card: go research it
        recheck.queue(supplier_id)
    return back_to(f"/supplier/{supplier_id}")


@app.post("/supplier/{supplier_id}/notes")
def add_note(supplier_id: int, text: str = Form(...), author: str = Form("")):
    _get(supplier_id)
    if text.strip():
        db.add_note(supplier_id, text.strip(), author.strip())
    return back_to(f"/supplier/{supplier_id}#notes")


@app.post("/supplier/{supplier_id}/recheck")
def recheck_now(supplier_id: int):
    _get(supplier_id)
    recheck.queue(supplier_id)
    return back_to(f"/supplier/{supplier_id}")


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
    return back_to("/")


@app.get("/cards/{name}")
def card_photo(name: str):
    path = config.CARDS_DIR / Path(name).name   # no directory tricks
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
