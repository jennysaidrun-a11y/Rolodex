"""Shows a supplier's PDF (safety data sheet, spec sheet) inside the app as page pictures, fetched
from the supplier's site on demand. Nothing is saved: the PDF is kept in memory for a few minutes
while someone reads it, then dropped, so the supplier's current version is always what shows."""
import io
import threading
import time
import urllib.request
from collections import OrderedDict

from .images import MAX_BYTES, UA, _opener, _public_host

KEEP_SECONDS = 15 * 60
KEEP_DOCS = 6
_docs: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()
_lock = threading.Lock()
_pdfium = threading.Lock()   # PDFium isn't thread-safe: one page at a time


def fetch(url: str) -> bytes | None:
    """The PDF at url (from memory if read in the last few minutes), or None if it isn't a PDF."""
    now = time.time()
    with _lock:
        hit = _docs.get(url)
        if hit and now - hit[0] < KEEP_SECONDS:
            _docs.move_to_end(url)
            return hit[1]
    if not _public_host(url):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf,*/*;q=0.8"})
        with _opener.open(req, timeout=30) as r:
            data = r.read(MAX_BYTES * 2 + 1)
    except Exception:
        return None
    if len(data) > MAX_BYTES * 2 or not data.lstrip()[:5].startswith(b"%PDF"):
        return None
    with _lock:
        _docs[url] = (now, data)
        _docs.move_to_end(url)
        while len(_docs) > KEEP_DOCS:
            _docs.popitem(last=False)
    return data


def page_count(data: bytes) -> int:
    import pypdfium2 as pdfium
    with _pdfium:
        pdf = pdfium.PdfDocument(data)
        try:
            return len(pdf)
        finally:
            pdf.close()


def render(data: bytes, number: int, width: int = 1200) -> bytes | None:
    """Page `number` (1-based) as a PNG about `width` pixels wide, or None if there's no such page."""
    import pypdfium2 as pdfium
    with _pdfium:
        return _render(pdfium, data, number, width)


def _render(pdfium, data: bytes, number: int, width: int) -> bytes | None:
    pdf = pdfium.PdfDocument(data)
    try:
        if not 1 <= number <= len(pdf):
            return None
        page = pdf[number - 1]
        scale = max(0.5, min(4.0, width / max(page.get_width(), 1)))
        image = page.render(scale=scale).to_pil()
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "PNG", optimize=True)
        return buf.getvalue()
    finally:
        pdf.close()
