"""Settings, read from environment variables (or a .env file next to the app)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DATA_DIR = Path(os.environ.get("ROLODEX_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "rolodex.db"
CARDS_DIR = DATA_DIR / "cards"
DOCS_DIR = DATA_DIR / "docs"          # saved copies of suppliers' pamphlet / catalog PDFs

# One shared password for everyone at the company. The data isn't sensitive, but the
# app is reachable from outside and every card/recheck spends Claude API credit.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")

# Who does the reading and research:
#   "claude-code": the app only saves card photos and queues scans; someone runs /analyze in
#                  Claude Code on this computer to do the work (no API key needed).
#   "api":         the app calls the Claude API itself, right away and on schedule.
# Defaults to "api" when an ANTHROPIC_API_KEY is set, otherwise "claude-code".
ANALYSIS = os.environ.get("ANALYSIS") or ("api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-code")
USE_API = ANALYSIS == "api"

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
RECHECK_DAYS = int(os.environ.get("RECHECK_DAYS", "90"))
# The app rechecks due suppliers on its own in the background; set to 0 to turn off
# (e.g. when a scheduled task runs `python -m rolodex.recheck` instead).
AUTO_RECHECK = os.environ.get("AUTO_RECHECK", "1") == "1"
