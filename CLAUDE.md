# Supplier Rolodex

Web app for a commercial bakery: business-card photos in, researched supplier profiles out,
rechecked every 90 days, searchable by keyword or by asking Claude. See README.md.

- Python / FastAPI / SQLite / Jinja; no JS build step. Must stay usable on a phone.
- Two analysis modes (`config.ANALYSIS`): `api` (the app calls Claude) and `claude-code` (the app
  only stores photos and queues; the `/analyze` skill does the work through `rolodex/tasks.py`).
  Both use the same prompts and schemas from `claude.py` (`card_prompt`, `research_prompt`,
  `card_schema`, `research_schema`) and the same save path (`recheck.save_reading`, `save_research`).
- "Cards" rows are business cards or pamphlets (`kind`), with any number of photos; `read_at` is
  NULL until read. Suppliers created from an unread photo have status `unread`.
- All Claude calls live in `rolodex/claude.py` and use structured outputs (JSON schemas there).
  If you add a profile field, add it to `RESEARCH_SCHEMA`, the supplier page, and `_directory`.
- Categories: a managed list (`category_list` table, seeded from `db.DEFAULT_CATEGORIES`, edited on
  /categories). Card reading and research build their JSON-schema enum from it (`claude.card_schema`,
  `research_schema`). Filter semantics: categories match ANY, tags match ALL. Research only adds
  categories on a supplier's first check.
- Tags: research returns `tags` [{group, name}] (groups in `db.TAG_GROUPS`) and is shown the
  existing vocabulary to reuse; `db.clean_tags` snaps spellings. Staff tags and removed tags are
  stored separately so rescans never undo staff edits (`db.effective_tags`).
- Research facts need a source URL; never invent pricing. Links from research are rendered
  only if http(s) (`link` filter).
- Run `pytest` before committing; tests stub Claude, so no API key is needed.
