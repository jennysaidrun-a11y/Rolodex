---
name: analyze
description: Read the business cards and pamphlets waiting in the supplier rolodex and research every supplier that is queued or due for its 3-month recheck. Use when the user says /analyze, "run the analysis", "process the new cards", or "do the rechecks".
---

# Analyze the rolodex

The rolodex app only stores photos and queues work; you do the reading and research. Everything
goes through `python -m rolodex.tasks` (run from the repo root, on the computer that holds the
rolodex `data/` folder). Every command prints JSON.

## 1. See what's waiting

```
python -m rolodex.tasks list
```

`read` = cards/pamphlets to read (do these first: a read queues the supplier for research).
`research` = suppliers to research. If both are empty, say so and stop.

## 2. Read each card or pamphlet

```
python -m rolodex.tasks show card <card_id>
```

- Open every path in `photos` with the Read tool (they are JPEGs). For a card, the first photo
  is the front and the second the back; for a pamphlet they are pages in order.
- Follow `instructions` exactly and produce JSON matching `output_schema` (every field present;
  empty string / empty list when something isn't there; `categories` only from the enum).
- Write it to a scratch file and save:

```
python -m rolodex.tasks save card <card_id> <file.json>
```

If it prints `"saved": false`, fix the listed errors and save again. A photo you can't read
(blurry, not a business card): save what you can and put the problem in `other_text`.

## 3. Research each supplier

```
python -m rolodex.tasks show research <supplier_id>
```

- Follow `instructions`: use WebSearch / WebFetch to verify facts, with a source URL for each.
  Never invent prices, certifications or dates. Confirm it is the same business as the card.
- Reuse the existing tags listed in the instructions whenever one fits.
- Produce JSON matching `output_schema`, write it to a file, then:

```
python -m rolodex.tasks save research <supplier_id> <file.json>
```

If the company can't be found or researched, record it instead (it retries tomorrow):

```
python -m rolodex.tasks fail <supplier_id> "why"
```

With more than 3 suppliers to research, hand each one to a subagent (one supplier per agent,
give it the `show research` output and the save command) so the research runs in parallel
and your context stays small. Read cards yourself; they're quick.

## 4. Report

Finish with a short summary: cards/pamphlets read, suppliers researched, and anything flagged
`needs_attention` (with the reason). Don't paste the full profiles; they're in the app.
