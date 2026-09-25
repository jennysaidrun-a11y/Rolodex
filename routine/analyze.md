# Supplier Rolodex analysis (self-contained)

This is the whole procedure. It needs no repository and runs no downloaded code: use only
ArtifactData, Artifact, WebSearch, WebFetch, Read, and plain shell commands (date, curl, file,
mkdir). Work without asking questions. For every timestamp use the output of
`date -u +%Y-%m-%dT%H:%M:%SZ` (never guess). Work in /tmp/rolodex.

PAGE = https://claude.ai/artifact/L1xZMaHagpDhwLrykRnxF2  (load ArtifactData and Artifact with ToolSearch)

## Data on the page

- `config/analysis`: the run's status, which the page shows as a progress bar.
- `config/categories`: `{"list": [{"name", "description"}]}`, the only allowed category names.
- `suppliers/<id>`: one document per supplier. Fields: company, contact_name, contact_title, phone,
  email, website, address, categories [names], summary, profile {research result}, status
  (unread | queued | researching | active | closed | error), needs_attention, attention_note,
  research_error, tags [{group, name}], staff_tags, removed_tags, notes [{at, author, text}],
  checks [{at, changes}], docs [{id, kind: card|pamphlet, photos: [asset ids], added_at, read_at,
  title, raw, pdf_url, pdf_asset}], created_at, last_checked, next_check (YYYY-MM-DD), progress.

Every write is `ArtifactData update` (a merge) with `if_version` = the version you last saw for
that document; each write returns the new version to use next. Arrays you write replace the
whole array, so always send the full array (existing items + your change). If a write is refused
because the version changed (someone edited it on the page), `get` it again and redo that write.

## 1. Start

1. `ArtifactData update PAGE config/analysis {"state": "running", "started_at": "<ts>",
   "session_id": "<value of $CLAUDE_CODE_REMOTE_SESSION_ID>"}` (use `set` if it doesn't exist).
2. `ArtifactData get` config/categories, and `list` the suppliers collection (page with
   query.cursor until done; use `out_dir=/tmp/rolodex/export` and Read the files if large).
3. Work out what's waiting:
   - **to read**: every doc with `read_at` null (a business card or pamphlet cover photo).
   - **to research**: every supplier with no unread docs whose status is `queued`, or whose status
     is active/closed/error and `next_check` <= today.
   If nothing is waiting, go to step 5.
4. Update config/analysis with `{"planned": [ids of every supplier with work], "current": ""}`
   (keep ids already in `planned`).

**Before each supplier** (reading or researching): `get` config/analysis. If its state is
`cancelled`, stop at once and write nothing more anywhere. Otherwise update it with
`{"current": "<company>"}`.

## 2. Read each waiting card or pamphlet cover

For each photo id in the doc's `photos`: `Artifact action=read url=PAGE path=<id>`, then Read the
saved image. A card's first photo is the front, the second the back.

Read it as a bakery's purchasing assistant would. Copy contact details exactly as printed; leave a
field empty if it isn't there, never guess. Several phone numbers: direct/mobile first, joined
with " / ". Pick every category from config/categories that fits what they sell to a bakery.
Card: products_mentioned = products or services printed on it; other_text = other useful text
(taglines, certifications, handwritten notes); document_title = "". Pamphlet cover:
document_title = the title as printed plus any edition, year or product line; contact_name only
if a rep is named; products_mentioned = products or lines on the cover; other_text = anything else
useful (certifications, promotions with dates).

Then update the supplier (one write):
- company, contact_name, contact_title, phone, email, website, address: set only where the
  supplier's current value is empty (company also when it is "New card").
- categories: existing + what you picked (no duplicates).
- notes: existing + `{"at": ts, "author": "card"|"pamphlet", "text": "From the card: <other_text>"}`
  and `{"at", "author", "text": "Products in the card: a, b, c"}` (say "pamphlet" for pamphlets;
  skip either when empty).
- docs: the full array with this doc's `read_at` = ts, `title` = document_title, `raw` = what you read
  (company, contact_name, contact_title, phone, email, website, address, categories,
  products_mentioned, document_title, other_text).
- status "queued", next_check = today.

## 3. Research each supplier

With 2 or more suppliers, give each to its own subagent (one supplier each) with this whole
section, the page URL, the supplier's document and version, the category list and the existing
tag list; they run in parallel. Research one yourself only when it's the only one.

**Progress**: before each of these five steps, update the supplier with
`{"progress": {"step": <1-5>, "of": 5, "label": "<step name>"}}`.

1. **Company & catalog**: confirm it is the same business as the card (website, address or
   phone). What they sell to a bakery, categories, locations (HQ, plants, warehouses), service
   area, stock and lead times, minimum order. Go through their catalog/shop/product pages and list
   6-15 products a bakery would care about, each with page_url (the product page) and image_url
   (the direct URL of that product's own photo; not logos, banners, icons or placeholders). Many
   sites load photos with scripts that WebFetch doesn't show, so get the photo from the page's HTML:
   `curl -sL --max-time 20 "<page_url>" | grep -oiE '(og:image|twitter:image)"[^>]*content="[^"]+"|<img[^>]+(src|data-src)="[^"]+"' | head -40`
   and pick the product's image (og:image is usually the main product photo; make relative URLs
   absolute). Reject an image that is the same on every product page, or whose name says logo,
   banner, og_fb, share, placeholder or icon: that's a site-wide picture, not the product. If the supplier's page has none, use the photo of the same product (same brand and
   item) from a distributor listing (WebstaurantStore, Uline, Grainger, Amazon Business…) and keep
   the supplier's page_url. Aim for a photo on most products. Use a distributor's page for their
   brand if they have no site.
2. **Pricing** (required):
Pricing is required: the bakery needs a realistic idea of cost before calling the rep. Most B2B suppliers don't publish prices, so work outward until you have real numbers, and label each:
- supplier_price: the supplier's own price list, catalog, online store, or prices in their pamphlet PDF
- distributor_listing: the same product or brand (or a close equivalent, named in item) at a distributor or online seller: WebstaurantStore, Uline, Grainger, Amazon Business, KaTom, Restaurant Depot, Central Restaurant Products, bakery supply shops, their authorized distributors
- public_contract: government or institutional price lists and bid awards (GSA Advantage, state or school-district contracts)
- market_benchmark: commodity or index prices for ingredients (USDA AMS market reports, wheat/sugar/dairy/egg futures or trade-press prices), or industry rate surveys for services and freight
- estimate: only when nothing above exists; give a range and say what it's based on in item
Give 2-6 entries for their main products or services, each with price, unit (per lb, per case of 1000, per 5-gal pail, per hour, per month...), source URL and as_of (YYYY-MM). Never present a distributor, benchmark or estimate as the supplier's own price. pricing_summary: one or two sentences on what to expect to pay and how this supplier prices (published list, quote only, volume breaks, minimums, freight), so the buyer knows what to ask for.
3. **Certifications & regulatory**: food-safety certifications relevant to food manufacturing (SQF,
   BRCGS, FSSC 22000, organic, kosher, halal, non-GMO, allergen programs) with status; FDA/USDA
   recalls, warning letters, import alerts, OSHA or EPA actions, lawsuits relevant to supply.
4. **Reviews & news**: reputation (customer reviews, BBB, trade press); acquisitions, closures,
   new plants, leadership changes.
5. **Photos, PDFs & saving**:
   - Product photos: `mkdir -p /tmp/rolodex/img-<id>` and for each product with an image_url:
     `curl -sSL --max-time 20 -o /tmp/rolodex/img-<id>/p<index> "<image_url>"`; keep only files that
     `file` reports as JPEG, PNG, WebP or GIF image data, larger than 2 KB and under 5 MB. Upload all
     kept files in one call: `Artifact url=PAGE asset=true file_paths=[...]` and set each product's
     `image_asset` to its returned asset id ("" for products without a photo).
   - Pamphlets (docs with kind "pamphlet"): find each one's PDF online (the supplier's
     literature/downloads pages first, then a web search for its title), read it with WebFetch and
     use it. Save a copy: curl it to /tmp/rolodex/<doc id>.pdf, check `file` says PDF, upload with
     `Artifact url=PAGE asset=true file_path=...` and note the asset id.

**Keep it quick**: about 8-12 web searches per supplier in total. Stop a step once it's covered,
don't re-verify facts you already have a source for, and move on when two or three searches find
nothing. A source URL for every fact; never invent prices, certifications or dates; dates as
YYYY-MM or YYYY-MM-DD. If there was an earlier research (`profile` and `last_checked` are set),
note what changed in changes_since_last_check.

tags: 5-15 short filter tags (1-3 words, Title Case) that buyers would filter on, each in a group:
- Product: specific things they sell, e.g. Bread Bags, Corrugated Boxes, High-Gluten Flour, Sesame Seeds, Spiral Mixers, Conveyor Belting
- Certification: e.g. SQF, BRCGS, FSSC 22000, Organic, Kosher, Halal, Non-GMO Project, Gluten-Free Certified (only if confirmed current)
- Capability: e.g. Nut-Free Facility, Custom Printing, Private Label, Bulk Totes, Same-Week Delivery, Equipment Repair, 24/7 Service, Compostable
- Service area: e.g. Nationwide, Midwest, Kansas
- Other: e.g. Distributor, Manufacturer, Family-Owned, Recall History
Only tag what the sources support.
Reuse the existing tags' exact spellings whenever one fits (compare case-insensitively);
add a new tag only for something none covers.

**The research result** (`profile`) has exactly these fields:
business_status ("active"|"closed"|"acquired"|"unknown"), summary (2-3 sentences on who they are
and what they could supply a bakery), categories [names from config/categories],
products [{name, details, image_url, page_url, image_asset}],
pricing [{item, price, unit, kind ("supplier_price"|"distributor_listing"|"public_contract"|
"market_benchmark"|"estimate"), source, as_of}], pricing_summary, stock_and_lead_times,
minimum_order, locations [{kind, address}], service_area, certifications [{name, status, source}],
reviews {summary, sources [urls]}, regulatory [{date, kind, description, source}],
news [{date, headline, source}], changes_since_last_check [strings], needs_attention (true for a
recall or warning letter, a lost certification, a closure or acquisition, a website/phone that no
longer works, or a likely duplicate of another supplier), attention_reason,
sources [urls], brochures [{card_id: the pamphlet's doc id, or "0" for other useful PDFs such as
catalogs, spec sheets or allergen statements; title; pdf_url: a direct PDF link; summary}],
tags [{group, name}].

**Save** with one update of the supplier:
- profile = the result; summary = result.summary; tags = result.tags (spellings snapped to existing tags)
- status = "closed" if business_status is "closed", else "active"
- needs_attention = result.needs_attention; attention_note = attention_reason if flagged, else ""
- research_error = ""; last_checked = ts; next_check = today + 90 days (YYYY-MM-DD)
- checks = existing + {"at": ts, "changes": changes_since_last_check joined with "; "}
- categories = existing + result.categories (no duplicates), ONLY if last_checked was empty before
- docs = full array; for each pamphlet doc: pdf_url from its brochure entry, pdf_asset = its PDF's
  asset id (keep existing values when you found nothing new)
- progress = null
Then delete product photos from the previous research that aren't used any more:
`Artifact action=delete url=PAGE path=<old image_asset>`.

If a company can't be found or researched: update the supplier with {"status": "error",
"research_error": "<why>", "next_check": tomorrow, "progress": null}.

## 4. Pick up anything added meanwhile

List the suppliers again. If anything new is waiting, add those ids to `planned` and do steps 2-3
for them. Repeat until nothing is waiting.

## 5. Finish (always, unless the run was cancelled)

`ArtifactData set PAGE config/analysis {"state": "done", "finished_at": "<ts>", "summary": "<one or
two sentences: how many read, how many researched, anything flagged needs attention>"}` (state
"failed" with what went wrong if the run could not finish). Then `get` it and confirm; write again
if needed. Write nothing to config/analysis after this. Don't change code or commit anything.
