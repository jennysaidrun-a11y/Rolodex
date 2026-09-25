# Supplier Rolodex

The box of business cards, made searchable. Snap a rep's business card, or the front cover of a
pamphlet, on your phone. Claude reads it and researches the company: what they supply, pricing,
stock and lead times, locations, certifications, reviews, recalls and other regulatory history,
news, and the pamphlet's PDF, each with a source link. It also copies the supplier's **product
catalog** from their website, so you can browse it like their online store (their departments and
sections, every product with photos, item numbers and listed prices), and search every supplier's
products at once. Profiles are rechecked every 3 months for anything new, and anything that
changed (a recall, a lost certification, a closure) is flagged **Needs attention**. Find suppliers
by keyword, category and tags, or ask in plain English.

## Where it runs: a GitHub Codespace

Nothing to install on the company computer. The app runs in a **Codespace**, a computer in the cloud
that GitHub runs for this repository, and you use it in the browser, on any computer or phone.

**Open it:** https://github.com/codespaces, then this repository's codespace, or the first time:
the repository page, green **Code** button, **Codespaces** tab, **Create codespace on main**.
It opens VS Code in the browser and starts the app by itself. Go to the **Ports** tab (bottom
panel), port **8000** "Supplier Rolodex", and click the globe icon to open the app. That address
works on your phone too once you're signed in to GitHub there; add it to your home screen.

**First time only: sign in Claude Code.** In the Codespace open a terminal (Terminal menu, New
Terminal), type `claude`, follow the sign-in link with your Claude account, then type `/exit`.
Claude Code then does the reading, research and catalog copying when you tap **Done adding:
analyze now**, answers **Ask Claude** questions, and runs the 3-month rechecks while the app is open.
(Once there's an API key, add `ANTHROPIC_API_KEY` as a Codespaces secret for this repository and
Claude Code uses it instead.)

- **Your data is saved to GitHub**: the database copy (`data/rolodex-backup.db`), card photos
  (`data/cards/`) and pamphlet PDFs (`data/docs/`) are committed to this repository every few
  minutes and right after each analysis (the note at the bottom of every page says when; **Save to
  GitHub now** does it immediately). A new or rebuilt Codespace starts from that copy, so a stopped
  or deleted Codespace loses nothing.
- **It updates itself.** Every minute the Codespace checks GitHub for a newer version of the app;
  when there is one it gets it and restarts the app (after any analysis in progress finishes). You
  never need to pull, push or rebuild anything.
- **Catalog photos** aren't stored in the repository: the app shows them from the supplier's site
  and keeps a copy in `data/cache/`, fetching any it doesn't have again.
- **It sleeps when idle.** A Codespace stops after 30 minutes with nobody typing in it (the app with
  it). Start it again from https://github.com/codespaces. Set **Default idle timeout** to 240
  minutes at https://github.com/settings/codespaces so an analysis has time to finish.
- **Cost:** personal GitHub accounts get 120 core-hours a month free (60 hours of this 2-core
  Codespace) and 15 GB of storage. A stopped Codespace uses no hours.
- **Who can open it:** the app's address is private to your GitHub account by default. To let a
  coworker in, add them to the repository, or in the Ports tab set port 8000 to **Public** and set
  `APP_PASSWORD` (a Codespaces secret) so the page asks for a password.

The Codespace setup is in `.devcontainer/` (Python 3.12, Node and Claude Code; `start.sh` pulls the
latest code and data, keeps the app running on port 8000 and restarts it on each new version
of the code on `main`; the log is `/tmp/rolodex.log`).

The same app also runs on any computer with Python (`run.bat` on Windows, `run.sh` on Linux); see
**Running it on your own computer** below.

The first version was a claude.ai page (`artifact/`, https://claude.ai/artifact/L1xZMaHagpDhwLrykRnxF2)
with a cloud routine doing the analysis (`routine/`). Its suppliers were copied into this app with
`tools/import_artifact.py`; the page and routine are no longer used.

## Using it

- **+ Add**: choose **Business card** (front, and back if it has anything) or **Pamphlet / brochure**
  (just a photo of the front cover: Claude reads the title, finds the full PDF online, usually on
  the supplier's website, uses it in the profile and saves a copy you can open from the supplier's
  page). Take the photo or pick it from your phone's library. To add a card or pamphlet to a
  supplier already on file, use **+ Add** in the **Cards & pamphlets** section of its page; what's
  read fills in blanks, goes into notes, and triggers a rescan. In API mode you check a new card's
  details before research starts; with Claude Code it's read and researched in one go when you tap
  **Done adding: analyze now**, and you can edit afterwards. **Save & add another** takes you straight
  back for the next card; **Several at once** adds a batch of library photos, one company each.
  A card from a company that's already in the rolodex goes onto that supplier and triggers a check
  for anything new, rather than creating a duplicate.
- **Analyze now**: the bar at the top of every page shows the analysis running (which supplier,
  which step) and has **Cancel**; anything unfinished stays queued. **Last analysis** (bottom of the
  page) shows Claude Code's output if something went wrong.
- **Catalog**: **Browse all N products** on a supplier's page opens their catalog like a store:
  departments on the left (a folding list on phones), subsections, a photo grid, search by name or
  item number, and a page per product with its photos, details, listed price and a link to it on
  their site. **Products** (top menu) searches every supplier's catalog at once. Catalogs are
  copied from Shopify and WooCommerce stores and from sites whose product pages carry structured
  data automatically (`python -m rolodex.catalog`); for other sites Claude builds them by hand
  from their pages. Some sites don't have a photo for every product (Summit's lubricants, for
  example); those show "No photo".
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

## Running it on your own computer

1. Install Python 3.11 or newer (Windows: from python.org, tick "Add python.exe to PATH").
2. Download this repository. Copy `.env.example` to `.env` if you want to set anything below.
3. Double-click `run.bat` (Windows) or run `./run.sh` (Linux). Then open http://localhost:8000.
4. For the analysis, install Claude Code on that computer and sign in (`claude`), or set an
   `ANTHROPIC_API_KEY`.

## Settings (`.env`, or Codespaces secrets)

| Setting | Default | What it does |
|---|---|---|
| `ANALYSIS` | `claude-code` without an API key, else `api` | Who reads cards and researches (see above) |
| `CLAUDE_MODEL` | `claude-opus-5` | Claude model used for reading, research and questions |
| `RECHECK_DAYS` | `90` | Days between automatic rechecks |
| `AUTO_RECHECK` | `1` | While the app is running it starts the rechecks itself when suppliers are due. Set `0` to only run them with the button |
| `ROLODEX_DATA_DIR` | `./data` | Where the database and card photos live (e.g. a backed-up drive) |
| `ROLODEX_GIT_SYNC` | `1` in a Codespace, else `0` | Commit and push the data folder to GitHub every few minutes |
| `APP_PASSWORD` | (none) | One shared password for the app, if you make it public |

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
  `begin`/`current`/`progress`/`finish` for the progress bar, `save catalog`, `directory`); the
  skills in `.claude/skills/` drive it
- `rolodex/runner.py`: starts Claude Code headless for **Analyze now**, the rechecks and **Ask Claude**;
  progress and cancel
- `rolodex/catalog.py`: copies a supplier's catalog (Shopify, WooCommerce, sitemap + structured data)
  and finds missing product photos; `rolodex/images.py`: the cached photo proxy behind `/img`
- `rolodex/gitsync.py`: saves the data folder to GitHub
- `rolodex/db.py`: SQLite tables: suppliers, cards, notes, checks (history of every research run),
  catalog_sections / catalog_products, analysis (the current run)
