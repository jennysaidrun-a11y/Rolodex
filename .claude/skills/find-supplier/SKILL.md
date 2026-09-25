---
name: find-supplier
description: Answer a plain-English question about the bakery's suppliers from the Supplier Rolodex, e.g. "who sells compostable bread bags?" or "which flour suppliers are SQF certified in the Midwest?". Use for /find-supplier or any question about which supplier to use.
---

# Find a supplier

The app's **Ask Claude** box does this too; use this skill when asked here.

1. `python -m rolodex.tasks directory` prints every supplier as compact JSON (profile, tags,
   certifications, notes). For products, search the catalogs:
   `python -c "from rolodex import db; db.init(); import json; print(json.dumps([(p['company'], p['name'], p['sku'], p['price']) for p in db.catalog_products(None, None, 'WORDS', 50)[0]]))"`
2. Answer from that data only. Recommend the best fits, one sentence each on why, and mention anything
   against one (recall, lapsed certification, out of area, closed, needs attention, not researched).
3. If nothing fits, say so and suggest what kind of supplier to look for. Offer to search the web for
   candidates, but don't add suppliers; they get in by someone adding a card in the app.
