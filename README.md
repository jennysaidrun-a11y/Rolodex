# Supplier Rolodex

The box of business cards, made searchable. Snap the front and back of a rep's card (or the pages of
a pamphlet) on your phone. Claude reads it and researches the company, building a profile:
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
- Claude does the reading and research in one of two ways (set `ANALYSIS` in `.env`):
  - **`claude-code`** (the default while there's no API key): the app just saves photos and queues
    scans. Someone opens Claude Code in this folder on the rolodex computer and runs **`/analyze`**;
    Claude Code reads every waiting card and pamphlet, researches those suppliers and every one due
    for its 3-month recheck, and saves the results. Run it daily or whenever new cards come in. The
    home page shows how much is waiting. Plain-English questions: **`/find-supplier`** in Claude Code.
  - **`api`** (once there's an Anthropic API key): the app reads cards the moment they're added,
    researches in the background, rechecks on its own and answers **Ask Claude** questions in the
    app. Reading a card costs a few cents; researching a supplier with web search is roughly $0.25
    to $1.

## Set up (once)

1. Install Python 3.11 or newer (Windows: from python.org, tick "Add python.exe to PATH").
2. Download this repository onto the server computer.
3. Copy `.env.example` to `.env` and fill in:
   - `ANTHROPIC_API_KEY`: the company's key, once you have one (leave it out to use Claude Code)
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

- **+ Add**: choose **Business card** (front, and back if it has anything) or **Pamphlet / brochure**
  (as many pages as you like; add them in one pick or several). Take photos or pick them from your
  phone's library. To add a card or pamphlet to a supplier already on file, use **+ Add** in the
  **Cards & pamphlets** section of its page; what's read from it fills in blanks, goes into notes,
  and triggers a rescan. In API mode you check the details of a new card before research starts;
  in Claude Code mode `/analyze` reads and researches in one go, and you can edit afterwards.
- **Categories** sort suppliers by the kind of thing they sell (Flour & grains, Packaging, Sanitation &
  chemicals, and so on). The search page opens with **Browse by category** tiles; **Categories** in the
  filter lets you pick several at once (a supplier in any of them shows up) and combines with tags and
  keywords. The **Categories** page (top menu) is where you add, rename, reorder, describe or delete
  them. Claude sorts each new card and supplier into this list using the descriptions. Renaming
  updates every supplier; renaming onto an existing category merges the two. The first research can
  add categories the card didn't show; after that, categories are yours and rescans leave them alone.
- **Tags** come from the research: each scan tags the supplier by Product (Bread Bags, High-Gluten
  Flour), Certification (SQF, Organic, Kosher), Capability (Custom Printing, Nut-Free Facility),
  Service area and Other, reusing tags already in the rolodex so spellings stay consistent. Open
  **Filter by tags** on the search page and tap as many as you need; a supplier has to have every
  tag you pick. Tap a tag on a profile to see every supplier with it. On **Edit**, untick a tag to
  remove it (rescans won't bring it back) or type your own (e.g. "Sample Received", "Preferred").
- **Notes** on each supplier record who stopped by, what they offered, quotes and samples. Notes
  are searchable and Claude reads them when answering questions.
- **Needs attention** (checkbox on the search page) lists suppliers whose last check found something
  to look at. Tap **Mark as reviewed** once someone has.
- **Manual scan**: **Scan now** on a supplier's page refreshes its profile without waiting for the
  3-month recheck. To scan several at once, tap **Select** above the supplier list, tick the
  companies (or **Select all**, which follows the current search and filters), then
  **Scan selected**. Scans run one after another in the background.

## Settings (`.env`)

| Setting | Default | What it does |
|---|---|---|
| `ANALYSIS` | `claude-code` without an API key, else `api` | Who reads cards and researches (see above) |
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
- `rolodex/recheck.py`: research queue, the 3-month recheck loop, and `save_reading` / `save_research`
- `rolodex/tasks.py`: command-line bridge that Claude Code uses (`list`, `show`, `save`, `fail`,
  `directory`); the skills in `.claude/skills/` drive it
- `rolodex/db.py`: SQLite tables: suppliers, cards, notes, checks (history of every research run)
