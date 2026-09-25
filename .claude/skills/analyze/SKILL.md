---
name: analyze
description: Read the business cards and pamphlet covers waiting in the Supplier Rolodex (the claude.ai page) and research every supplier that is queued or due for its 3-month recheck. Use when the user says /analyze, "run the analysis", "process the new cards", or "do the rechecks".
---

# Analyze the rolodex

Follow `routine/analyze.md` in this repo exactly. It is the same procedure the page's
"Done adding: analyze now" button and the nightly routine run (trigger
`trig_01Dq17WHzafvRP6ZmAZvrhb6`, whose prompt is that file's text). It uses only built-in tools,
no scripts, so it runs in unattended sessions too.

If you change the procedure, change `routine/analyze.md` and update the trigger's prompt to match
(`update_trigger` with the file's full text).

`tools/analyze.py` is an optional helper for interactive sessions (plan, prompts, JSON checks);
the routine doesn't depend on it.
