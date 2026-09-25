---
name: find-supplier
description: Answer a plain-English question about the bakery's suppliers from the Supplier Rolodex (the claude.ai page), e.g. "who sells compostable bread bags?" or "which flour suppliers are SQF certified in the Midwest?". Use for /find-supplier or any question about which supplier to use.
---

# Find a supplier

The page itself has an **Ask about your suppliers** box that does this; use this skill when asked here.

1. Load `ArtifactData` (ToolSearch) and export the records:
   `ArtifactData list url=https://claude.ai/artifact/L1xZMaHagpDhwLrykRnxF2 collection=suppliers out_dir=work/export`
   (page with `query.cursor`), then `python tools/analyze.py directory work/export`.
2. Answer from that data only. Recommend the best fits, one sentence each on why, and mention anything
   against one (recall, lapsed certification, out of area, closed, needs attention, not researched).
3. If nothing fits, say so and suggest what kind of supplier to look for. Offer to search the web for
   candidates, but don't add suppliers; they get in by someone adding a card on the page.
