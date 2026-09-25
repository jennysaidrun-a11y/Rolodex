"""Product photos from suppliers' sites, fetched once and kept in data/cache/img (not saved to git;
anything missing is fetched again)."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import socket
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_BYTES = 15 * 1024 * 1024
SIZES = (0, 200, 400, 800)
FAILED_RETRY_SECONDS = 6 * 3600   # 0 = as on their site
TYPES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"GIF8": "image/gif", b"RIFF": "image/webp"}


def _public_host(url: str) -> bool:
    """Only fetch from the public internet (never this machine or the local network)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def _sniff(data: bytes) -> str:
    for magic, kind in TYPES.items():
        if data.startswith(magic):
            return kind
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis", b"mif1", b"msf1"):
        return "image/avif"   # many sites send AVIF whatever the file is called
    head = data[:300].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:1000].lower()):
        return "image/svg+xml"
    return ""


class _NoPrivateRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_host(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoPrivateRedirects)


def fetch_image(url: str, width: int = 0) -> tuple[Path, str] | None:
    """(file, media type) for the photo at url, resized to width (one of SIZES), or None."""
    width = min((s for s in SIZES if s >= width), default=0) if width else 0
    folder = config.CACHE_DIR / "img"
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    original = folder / key
    failed = folder / (key + ".failed2")
    if failed.exists():
        if time.time() - failed.stat().st_mtime < FAILED_RETRY_SECONDS:
            return None
        failed.unlink(missing_ok=True)   # try again: the site may have been down
    if not original.exists():
        if not _public_host(url):
            return None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.8",
                                                       "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/"})
            with _opener.open(req, timeout=20) as r:
                data = r.read(MAX_BYTES + 1)
        except Exception:
            failed.touch()
            return None
        if len(data) > MAX_BYTES or not _sniff(data):
            failed.touch()
            return None
        original.write_bytes(data)
    kind = _sniff(original.read_bytes()[:1000])
    if kind in ("image/svg+xml", "image/gif") or (not width and kind != "image/avif"):
        return original, kind
    width = width or 1600   # AVIF at full size: still turned into a JPEG every browser shows
    small = folder / f"{key}-{width}.jpg"
    if not small.exists():
        try:
            img = ImageOps.exif_transpose(Image.open(original))
            img.thumbnail((width, width))
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, "white")
                bg.paste(img, mask=img.split()[-1])
                img = bg
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=82)
            small.write_bytes(buf.getvalue())
        except (UnidentifiedImageError, OSError, ValueError):
            return original, kind
    return small, "image/jpeg"
