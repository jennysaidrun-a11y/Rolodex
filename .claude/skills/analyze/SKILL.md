---
name: analyze
description: Read the business cards and pamphlet covers waiting in the Supplier Rolodex (the claude.ai page) and research every supplier that is queued or due for its 3-month recheck. Use when the user says /analyze, "run the analysis", "process the new cards", or "do the rechecks".
---

# Analyze the rolodex

The rolodex is a claude.ai page: **https://claude.ai/artifact/L1xZMaHagpDhwLrykRnxF2**
(the URL is also in CLAUDE.md). Its records live in the page's database: one `suppliers/<id>`
document per supplier and `config/categories`. Photos and PDFs are the page's assets. You do the
reading and web research here and write results back; `python tools/analyze.py` (standard
library only) prepares each job and builds each update so the page gets exactly the shape it expects.

Tools: load `ArtifactData` and `Artifact` with ToolSearch if they are not loaded. Work in a scratch
folder, e.g. `work/` (git-ignored). `PAGE` below means the URL above.

## 1. Export and plan

```
ArtifactData list  url=PAGE collection=suppliers  out_dir=work/export  (page with query.cursor until done)
ArtifactData get   url=PAGE collection=config doc_id=categories out_dir=work/export
python tools/analyze.py plan work/export
```

The export result lists each supplier's `version`: keep it, it goes in `if_version` when you
write that supplier back (use the newer version an update returns for any later write).

`read` = cards/pamphlet covers not read yet (do these first). `research` = suppliers queued or
due. If both are empty, say so and stop.

**Progress for the page.** The page shows a progress bar from `config/analysis`. Right after this
first `plan`, merge in the plan: `ArtifactData update url=PAGE collection=config doc_id=analysis
data={"planned": <planned_supplier_ids>, "current": ""}` (if the document doesn't exist yet, `set`
it with `"state": "running"` too). Before starting each supplier (reading or researching), update
it with `{"current": "<company name>"}`. The bar fills as suppliers finish; you don't count.

## 2. Read each card or pamphlet cover

```
python tools/analyze.py show-read work/export <supplier_id> <doc_id>
```

- For each id in `photo_asset_ids`: `Artifact action=read url=PAGE path=<asset id>`, then Read the
  saved image. Card: first photo is the front, second the back. Pamphlet: the cover.
- Follow `instructions`; write JSON matching `output_schema` (every field; empty string/list when
  absent; categories only from the enum) to `work/read-<doc_id>.json`, then:

```
python tools/analyze.py apply-read work/export <supplier_id> <doc_id> work/read-<doc_id>.json work/update.json
ArtifactData update url=PAGE collection=suppliers doc_id=<supplier_id> file_path=work/update.json if_version=<version>
```

If `apply-read` prints errors, fix the JSON and run it again. If the update is refused because the
document changed (someone edited it in the page), re-export that one supplier
(`ArtifactData get ... out_dir=work/export`) and redo the step. Run `plan` again afterwards: the
suppliers you just read are now queued for research.

## 3. Research each supplier

```
python tools/analyze.py show-research work/export <supplier_id>
```

- Follow `instructions`: WebSearch / WebFetch, a source URL for each fact. Never invent prices,
  certifications or dates. Confirm it is the same business as the card. Reuse existing tags.
- **Pamphlets** (listed in the instructions by card_id): find each one's PDF online (the supplier's
  literature/downloads pages first), read it with WebFetch and use it. Save a copy for the page:
  `curl -sSL -o work/<card_id>.pdf "<pdf url>"`, check it starts with `%PDF`, then
  `Artifact url=PAGE asset=true file_path=work/<card_id>.pdf` and note the returned asset id.
- Write the JSON to `work/research-<supplier_id>.json`, then:

```
python tools/analyze.py apply-research work/export <supplier_id> work/research-<supplier_id>.json work/update.json [--pdf <card_id>=<asset id> ...]
ArtifactData update url=PAGE collection=suppliers doc_id=<supplier_id> file_path=work/update.json if_version=<version>
```

If a company can't be found, record it instead (tried again next run):
`python tools/analyze.py fail work/export <supplier_id> work/update.json "why"` and write that update.

With more than 3 suppliers to research, give each to a subagent (one supplier each, with the
`show-research` output and these two commands) so they run in parallel and your context stays small.

## 4. Report

A short summary: cards/pamphlets read, suppliers researched, PDFs saved, and anything flagged
needs-attention (with the reason). Don't paste profiles; they're on the page.
