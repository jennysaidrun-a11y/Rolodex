# Supplier Rolodex

Supplier rolodex for a commercial bakery: business-card and pamphlet-cover photos in, researched
supplier profiles out, rechecked every 90 days, searchable by keyword, category, tag or question.

**The live app is a claude.ai page: https://claude.ai/artifact/L1xZMaHagpDhwLrykRnxF2**
(source `artifact/index.html`, one self-contained file; publish updates with the Artifact tool,
`url` above, keeping its capabilities db, assets, sample, user). Its data is the page's database:
`suppliers/<id>` documents, `config/categories`, and `config/analysis` (state of the latest
analysis run: requested/running/done/failed, with a summary). The page's "Done adding: analyze
now" button fires the routine `trig_01Dq17WHzafvRP6ZmAZvrhb6` (also nightly at 23:07 UTC) through
the Claude Code Remote connector; that routine clones this repo and follows the analyze skill. `/analyze` and `/find-supplier`
(`.claude/skills/`) read and write it with ArtifactData, using `tools/analyze.py`, which reuses the
prompts and schemas in `rolodex/claude.py`. Keep the field names in `artifact/index.html`,
`tools/analyze.py` and the prompts in step.

The Python app (`rolodex/`, self-hosted, optional) is the same product on a server:

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
