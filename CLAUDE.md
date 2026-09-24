# Supplier Rolodex

Web app for a commercial bakery: business-card photos in, researched supplier profiles out,
rechecked every 90 days, searchable by keyword or by asking Claude. See README.md.

- Python / FastAPI / SQLite / Jinja; no JS build step. Must stay usable on a phone.
- All Claude calls live in `rolodex/claude.py` and use structured outputs (JSON schemas there).
  If you add a profile field, add it to `RESEARCH_SCHEMA`, the supplier page, and `_directory`.
- Research facts need a source URL; never invent pricing. Links from research are rendered
  only if http(s) (`link` filter).
- Run `pytest` before committing; tests stub Claude, so no API key is needed.
