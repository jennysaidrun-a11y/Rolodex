# Supplier Rolodex

The box of business cards, made searchable. Snap the front and back of a rep's card on your phone.
Claude reads it, you check the details, then Claude researches the company and builds a profile:
what they supply, pricing, stock and lead times, locations, certifications, reviews, recalls and other
regulatory history, and news, each with a source link. Every profile is rechecked every 3 months, and
anything that changed (a recall, a lost certification, a closure) is flagged **Needs attention**.

To find a supplier, search by keyword or category, or ask in plain English ("who can supply compostable
bread bags, SQF certified, delivering to us?") and Claude answers from what's on file.

## What it runs on

- One computer on the company system (Windows or Linux) runs the app and keeps the data:
  `data/rolodex.db` (one file) and `data/cards/` (the card photos). **Back up the `data` folder.**
- Phones and other computers open it in a browser. On a phone, use **Add to Home Screen** and it
  opens like an app.
- Claude calls need an Anthropic API key from https://console.anthropic.com. Reading a card costs
  a few cents; researching a supplier with web search is roughly $0.25 to $1. Check actual spend
  in the Anthropic console after the first batch of cards.

## Set up (once)

1. Install Python 3.11 or newer (Windows: from python.org, tick "Add python.exe to PATH").
2. Download this repository onto the server computer.
3. Copy `.env.example` to `.env` and fill in:
   - `ANTHROPIC_API_KEY`: the company's key
   - `APP_PASSWORD`: one password everyone uses to sign in (the app is reachable from outside)
   - `SECRET_KEY`: any long random string
4. Start it: double-click `run.bat` (Windows) or run `./run.sh` (Linux). The first start installs
   what it needs. Then open http://localhost:8000.

To keep it running after a reboot, add `run.bat` to Windows Task Scheduler ("At startup"), or run
`run.sh` as a systemd service on Linux.

## Reaching it from phones and outside the office

On the office Wi-Fi, phones can open `http://<server computer's IP>:8000`. To reach it from
anywhere (trade shows, a supplier's site), put it behind a tunnel instead of opening a port on the
firewall. Either works and gives you an `https://` address:

- **Cloudflare Tunnel** (free): install `cloudflared` on the server and run
  `cloudflared tunnel --url http://localhost:8000` for a quick test URL, or set up a named tunnel on
  a company domain for a permanent address.
- **Tailscale Funnel**: `tailscale funnel 8000`.

## Using it

- **+ Add card**: take the front photo, optionally the back, then tap **Read card**. Check and fix the
  details (it warns if the company looks like one already on file), then tap
  **Save and research**. Research takes a few minutes and runs in the background.
- **Notes** on each supplier record who stopped by, what they offered, quotes and samples. Notes
  are searchable and Claude reads them when answering questions.
- **Needs attention** (checkbox on the search page) lists suppliers whose last check found something
  to look at. Tap **Mark as reviewed** once someone has.
- **Recheck now** refreshes a profile without waiting for the 3-month recheck.

## Settings (`.env`)

| Setting | Default | What it does |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-5` | Claude model used for reading, research and questions |
| `RECHECK_DAYS` | `90` | Days between automatic rechecks |
| `AUTO_RECHECK` | `1` | The app rechecks due suppliers itself every 15 minutes. Set `0` and schedule `python -m rolodex.recheck` instead if you prefer |
| `ROLODEX_DATA_DIR` | `./data` | Where the database and card photos live (e.g. a backed-up drive) |

## For developers

```
pip install -r requirements-dev.txt
pytest                                   # Claude calls are stubbed; no key needed
uvicorn rolodex.app:app --reload
```

- `rolodex/app.py`: web pages (FastAPI + Jinja templates in `rolodex/templates/`)
- `rolodex/claude.py`: every Claude call: `read_card`, `research` (web search), `ask`
- `rolodex/recheck.py`: research queue and the 3-month recheck loop
- `rolodex/db.py`: SQLite tables: suppliers, cards, notes, checks (history of every research run)
