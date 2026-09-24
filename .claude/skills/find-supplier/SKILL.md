---
name: find-supplier
description: Answer a plain-English question about the bakery's suppliers from the rolodex database, e.g. "who sells compostable bread bags?" or "which flour suppliers are SQF certified in the Midwest?". Use for /find-supplier or any question about which supplier to use.
---

# Find a supplier

1. Load the directory (every supplier, compact JSON with categories, tags, products, locations,
   certifications, regulatory history, staff notes and attention flags):

   ```
   python -m rolodex.tasks directory
   ```

2. Answer from that data only. Recommend the suppliers that fit best, one sentence each on
   why, and mention anything that argues against one (recall, lapsed certification, out of
   area, closed, needs attention, not researched recently). Give each one's app link as
   `/supplier/<id>` so staff can open it.
3. If nothing on file fits, say so plainly and suggest what kind of supplier to look for. Offer
   to search the web for candidates, but don't add anything to the rolodex; suppliers get in
   by someone adding a card or pamphlet in the app.
