"""Phone access over Tailscale (a private network only your own approved devices can join).

The app itself listens only on this computer (127.0.0.1). When Tailscale is installed and on,
this also answers on the computer's Tailscale address, so your phone (signed in to the same
Tailscale account) can open it; nobody on the office network or the internet can. Checked every
minute, so turning Tailscale on later just works. Same approach as File Filler.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("rolodex.phone")
TAILNET = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]
PORT = int(os.environ.get("PORT", "8000"))
_state: dict = {"ip": "", "name": "", "server": None}


def _tailscale() -> str:
    return shutil.which("tailscale") or next(
        (p for p in (r"C:\Program Files\Tailscale\tailscale.exe",) if Path(p).is_file()), "")


def _run(*args: str) -> str:
    exe = _tailscale()
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def tailscale_ip() -> str:
    """This computer's Tailscale address (100.x.y.z), or "" when Tailscale isn't installed or is off."""
    out = _run("ip", "-4").split()
    return out[0] if out and out[0].startswith("100.") else ""


def _dns_name() -> str:
    """This computer's Tailscale name (e.g. ub-qa-manager.tailxxxx.ts.net), if MagicDNS is on."""
    try:
        return (json.loads(_run("status", "--json") or "{}").get("Self", {}).get("DNSName") or "").rstrip(".")
    except ValueError:
        return ""


def is_tailnet(addr: str) -> bool:
    try:
        a = ipaddress.ip_address((addr or "").split("%")[0])
    except ValueError:
        return False
    return any(a.version == n.version and a in n for n in TAILNET)


def url() -> str:
    """The address to open on your phone, or "" while phone access isn't running."""
    if not _state["server"]:
        return ""
    return f"http://{_state['name'] or _state['ip']}:{PORT}"


def phone_server_ip() -> str:
    return _state["ip"] if _state["server"] else ""


def _start(app) -> None:
    if _state["server"]:
        return
    ip = tailscale_ip()
    if not ip:
        return
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host=ip, port=PORT, lifespan="off", log_level="warning"))
    _state.update(ip=ip, name=_dns_name(), server=server)
    threading.Thread(target=server.run, name="phone-server", daemon=True).start()
    log.info("On your phone (Tailscale): %s", url())


def start_background(app) -> None:
    def loop():
        while not _state["server"]:
            try:
                _start(app)
            except Exception as e:   # never take the app down over phone access
                log.warning("Phone access not started: %s", e)
            time.sleep(60)
    threading.Thread(target=loop, name="phone-access", daemon=True).start()
