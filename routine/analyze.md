# Supplier Rolodex analysis (self-contained)

This is the whole procedure. It needs no repository and runs no downloaded code: use only
ArtifactData, Artifact, WebSearch, WebFetch, Read, plain shell commands (date, curl, file,
mkdir), and small Python scripts you write yourself. Work without asking questions. For every timestamp use the output of
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
  title, raw, pdf_url, pdf_asset}], created_at, last_checked, next_check (YYYY-MM-DD), progress,
  catalog {crawled_at, source, total, with_photos, sections [{id, name, parent_id}], note}.
- `suppliers/<id>/catalog/<section id>`: the supplier's catalog, one document per section of their
  site: {name, parent_id ("" for a top section), order, part_of (set when a big section is split
  across several documents), products [{id, name, sku, details, price, page_url, image_src,
  sheet, i, n}]}. A product photo is one square of a photo sheet: `sheet` = asset id of a
  1024x1024 JPEG holding a 4x4 grid of 256x256 photos, `i` = square 0-15 (row by row), `n` = 4.

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

**Already in the rolodex?** Before writing, compare what you read with every other supplier: the
same company name (ignoring Inc, LLC, Co, Corp, punctuation), the same website domain, the same email
domain (not gmail/yahoo/outlook/hotmail), or the same phone number. If one matches, this card belongs
to that existing supplier: add this doc (read) to the existing supplier's `docs`, apply the rules
below to the existing supplier instead, and then delete the new supplier's document
(`ArtifactData delete`; keep its photos, they're now on the existing one), or, if the new document
still has other unread docs, just remove this doc from it. Never create or flag a duplicate. The
existing supplier gets status "queued", so step 3 checks it for what's new.

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

**Progress**: before each of these six steps, update the supplier with
`{"progress": {"step": <1-6>, "of": 6, "label": "<step name>"}}`.

**Researched before?** When the supplier has `last_checked`, this is a check for new information:
start from the existing profile, keep what still holds, and look specifically for anything since
`last_checked`: news, recalls and regulatory actions, certification changes, new or discontinued
products, price changes, closures or ownership changes. List each in changes_since_last_check (say
"No changes found" when nothing is new).

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
longer works; never for being a possible duplicate), attention_reason,
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

6. **Catalog** (after saving the research): copy what they sell, organized the way their website
   organizes it, into `suppliers/<id>/catalog`. See "Catalog" below.

If a company can't be found or researched: update the supplier with {"status": "error",
"research_error": "<why>", "next_check": tomorrow, "progress": null}.

## Catalog

Mirror their product catalog: every product their website lists, grouped into their own sections
and subsections (their names, their order). Copy their organization and content, not their design.

1. **Find the product list**, cheapest way first (curl with a normal browser User-Agent, at most
   about 2 requests a second, and skip anything their robots.txt disallows):
   - Shopify stores: `<site>/collections.json?limit=250` and `<site>/collections/<handle>/products.json?limit=250&page=N`
     (or `<site>/products.json?limit=250&page=N`) give sections, products, prices and image URLs.
   - WooCommerce: `<site>/wp-json/wc/store/v1/products/categories` and `<site>/wp-json/wc/store/v1/products?per_page=100&page=N`.
   - Otherwise: `<site>/sitemap.xml` (or sitemap_index.xml / robots.txt Sitemap lines) for product and
     category URLs, and their navigation menu and category pages for the section tree; follow
     pagination. Read names, item numbers, short descriptions, listed prices and the product photo
     (og:image or the main product img, as in step 1) from each page.
   You may write small Python scripts of your own (standard library, plus Pillow for images) to
   fetch and parse pages; don't run code downloaded from anywhere. Give the crawl up to about 20
   minutes per supplier; if the catalog is bigger than that allows, keep what you have and say so in
   catalog.note.
2. **Photos**: download every product photo, then build photo sheets with Pillow (`pip install
   pillow` if `python3 -c "import PIL"` fails): fit each photo onto a 256x256 white square (keep its
   proportions), paste 16 squares row by row onto a 1024x1024 white JPEG (quality 80), and record
   each product's `sheet` file and square `i`. Reuse: if a product's `image_src` is unchanged from the
   previous catalog, keep its old sheet and square instead of downloading again (only build new
   sheets for new or changed photos). Upload sheets up to 25 per call
   (`Artifact url=PAGE asset=true file_paths=[...]`) and replace each file name with its asset id.
   Storage: the page holds at most 5,000 files in total; check with `Artifact action=list url=PAGE
   scope=assets` and keep at least 300 free. If the photos would pass that, give photos first to the
   sections a bakery is most likely to buy from and leave the rest without (note it in catalog.note).
3. **Write** one document per section to `suppliers/<id>/catalog` with `ArtifactData batch` (up to 50
   writes per batch), doc id = a short slug of the section path. A section with more than 400
   products is split into extra documents with the same name and parent and `part_of` = the first
   one's id. Products: id (their item/SKU or a slug), name, sku, details (one short line: size,
   pack, material), price (as listed, with unit; "" if not shown), page_url, image_src (the photo URL
   you downloaded), sheet, i, n. Delete section documents that no longer exist on their site.
4. **Summary**: update the supplier with `catalog` = {crawled_at: ts, source: <site or catalog URL>,
   total: <products>, with_photos: <products with a sheet>, sections: [{id, name, parent_id}] for
   top-level and second-level sections, note: "" or what was left out}. The profile's `products`
   highlights may point at catalog photos too (copy their sheet, i, n).
5. **Clean up**: delete sheet assets that no catalog product uses any more
   (`Artifact action=delete url=PAGE path=<id>`).

## 4. Pick up anything added meanwhile

List the suppliers again. If anything new is waiting, add those ids to `planned` and do steps 2-3
for them. Repeat until nothing is waiting.

## 5. Finish (always, unless the run was cancelled)

`ArtifactData set PAGE config/analysis {"state": "done", "finished_at": "<ts>", "summary": "<one or
two sentences: how many read, how many researched, anything flagged needs attention>"}` (state
"failed" with what went wrong if the run could not finish). Then `get` it and confirm; write again
if needed. Write nothing to config/analysis after this. Don't change code or commit anything.
