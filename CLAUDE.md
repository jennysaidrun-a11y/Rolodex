# Supplier Rolodex

Supplier rolodex for a commercial bakery: business-card and pamphlet-cover photos in, researched
supplier profiles and a copy of each supplier's product catalog out, rechecked every 90 days,
searchable by keyword, category, tag, product or question.

**It runs in a GitHub Codespace** (`.devcontainer/`): the Python app (`rolodex/`, FastAPI / SQLite /
Jinja, no JS build step, must stay usable on a phone) on port 8000, with Claude Code installed and
signed in. The company computer can't install anything, so everything is used through the browser.
The Codespace updates itself: `.devcontainer/start.sh` checks `main` every minute and restarts the
app when code (anything outside `data/`) changes, so pushing to `main` is how a change goes live.

- **Analysis** (`config.ANALYSIS = "claude-code"`, the default without an API key): the app starts
  Claude Code headless (`rolodex/runner.py`, `claude -p` with a fixed `--allowedTools` list) for
  "Done adding: analyze now" and for due rechecks while it runs. Claude Code follows
  `.claude/skills/analyze/SKILL.md`, which works only through `python -m rolodex.tasks` and
  `python -m rolodex.catalog`, and reports progress with `tasks begin/current/progress/finish`
  (the `analysis` table and `suppliers.progress` drive the progress bar; Cancel kills the process
  group). **Ask Claude** also goes through `runner.ask` (directory JSON on stdin), and **Ask Claude**
  on the Products page (`/products/ask`) through `runner.ask_products` (every catalog product,
  compact, on stdin; answers with product keys). Both use `config.SEARCH_MODEL` (Sonnet). `api`
  mode calls the Claude API from `rolodex/claude.py` instead.
- Prompts and schemas for reading and research live in `rolodex/claude.py` (`card_prompt`,
  `research_prompt`, `card_schema`, `research_schema`), shared by both modes; saving goes through
  `recheck.save_reading` / `save_research`. If you add a profile field, add it to the research
  schema, `supplier.html` and `claude.directory`.
- **Repeat cards** never create duplicates: `tasks save card` merges a card whose company matches an
  existing supplier (`db.possible_duplicates`: name without Inc/LLC, website or company-email domain,
  phone) into it and queues that supplier for a "what's new since last_checked" check.
- **Catalogs**: `catalog_sections` / `catalog_products` tables, replaced whole by `db.save_catalog`.
  Photos are stored as URLs on the supplier's site and served through `/img` (`rolodex/images.py`:
  public hosts only, cached in `data/cache/img`, resized). `rolodex/catalog.py` crawls Shopify,
  WooCommerce and sitemap + JSON-LD/microdata sites; anything else Claude builds by hand per the skill.
  After every save `catalog.complete` runs: `expand` opens each entry's page and turns category pages
  with a grid of model cards into a section of those models (`rolodex/pagecards.py`, stdlib HTML
  parsing, skips menus/footers/related/resources grids), `fill_photos` fills missing photos, and
  `fill_specs` reads each product's own page for specs/dimensions (`specs`, `[[label, value]]`),
  datasheet links (`files`) and the full description. Catalogs are copied in English: `crawl` uses a
  site's English version (`english_root`: `<html lang>`, hreflang, `/en/`), and the skill translates
  sites that have none.
  Tests keep it offline with `ROLODEX_CATALOG_COMPLETE=0` (tests/conftest.py).
- **Data**: `data/rolodex.db` (live, not in git) is copied to `data/rolodex-backup.db` and committed
  with `data/cards/` and `data/docs/` by `rolodex/gitsync.py` (every few minutes in a Codespace).
  `db.init()` restores from the backup on a fresh checkout. Don't commit `data/rolodex.db` or `data/cache/`.
- "Cards" rows are business cards or pamphlets (`kind`), with any number of photos; `read_at` is
  NULL until read. Suppliers created from an unread photo have status `unread`.
- Categories: a managed list (`category_list` table). Filter semantics: categories match ANY, tags
  match ALL. Research only adds categories on a supplier's first check. Tags: research tags plus
  staff tags and removed tags stored separately so rescans never undo staff edits.
- Research facts need a source URL; never invent pricing. Links from research are rendered only if
  http(s) (`link` filter).
- Run `pytest` before committing; tests stub Claude (fake `claude` scripts for the runner).

- **Look**: the shared United Bakery theme (tokens from the project's bakery-theme/theme.md, same in every
  bakery app) lives in `rolodex/static/bakery-theme.css`, loaded after `style.css`; change both together.

`artifact/` (the earlier claude.ai page) and `routine/` (its cloud routine) are retired; their data
was imported with `tools/import_artifact.py`.
