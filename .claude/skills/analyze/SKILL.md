---
name: analyze
description: Read the business cards and pamphlet covers waiting in the Supplier Rolodex, research every supplier that is queued or due for its 3-month recheck, and copy each one's product catalog from their website. Use when the user says /analyze, "run the analysis", "process the new cards", or "do the rechecks". The app's "Done adding: analyze now" button runs this too.
---

# Analyze the rolodex

Everything goes through `python -m rolodex.tasks` (prints JSON; `save` checks your JSON and says
what's wrong, so fix it and save again). Put your JSON files in `work/` (make it with `mkdir -p work`).
Work without asking questions. Don't change code or commit anything; the app saves the data to
GitHub by itself.

## 1. Start

`python -m rolodex.tasks begin`, then `python -m rolodex.tasks list`. It lists cards/pamphlets to
read and suppliers to research. If both are empty, run `finish Nothing was waiting.` and stop.

## 2. Read each card or pamphlet cover

For each item in `read`:
1. `python -m rolodex.tasks current <supplier_id>`
2. `python -m rolodex.tasks show card <card_id>`: Read every photo path it lists (a card's first
   photo is the front, the second the back), follow its instructions, and write the JSON (matching
   `output_schema`) to `work/card-<card_id>.json`.
3. `python -m rolodex.tasks save card <card_id> work/card-<card_id>.json`.
   If the company is already in the rolodex, the save puts the card on that supplier and queues it for
   a check of what's new (`merged_into_existing`). That's expected; never treat it as a duplicate.

## 3. Research each supplier

Run `list` again (merges change the ids). For each supplier in `research`: with 2 or more, give
each to its own subagent (Agent tool, one supplier each, in parallel) with this whole section and the
supplier id; research one yourself only when it's the only one.

Before each step run `python -m rolodex.tasks progress <id> <step> 6 <label>` (steps below). If a
tasks command prints `"cancelled": true`, stop at once and do nothing more.

1. **Company & products** (`progress <id> 1 6 Company and products`): `python -m rolodex.tasks current <id>`,
   then `python -m rolodex.tasks show research <id>` and follow its instructions and `output_schema`.
   When the supplier has been checked before, this is a check for what's new since then; the
   instructions include the last profile. For each highlight product, get the product's own photo
   URL from its page's HTML when WebFetch doesn't show it:
   `curl -sL --max-time 20 -A "Mozilla/5.0" "<page_url>" | grep -oiE '(og:image|twitter:image)"[^>]*content="[^"]+"|<img[^>]+(src|data-src)="[^"]+"' | head -40`
   (og:image is usually the product photo; make relative URLs absolute; skip logos, banners and
   images that are the same on every page).
2. **Pricing** (`progress <id> 2 6 Pricing`): required; follow the pricing rules in the instructions.
3. **Certifications & regulatory** (`progress <id> 3 6 Certifications and regulatory`).
4. **Reviews & news** (`progress <id> 4 6 Reviews and news`).
5. **Save** (`progress <id> 5 6 Saving the profile`): write `work/research-<id>.json` and
   `python -m rolodex.tasks save research <id> work/research-<id>.json`.
6. **Catalog** (saving the research moves the progress to this step): see below. Do it on every
   research, rechecks included, so the copy picks up new and discontinued products. Saving the
   catalog finishes the supplier. If they have no product or service list online at all:
   `python -m rolodex.tasks no-catalog <id> <why>`.

Keep research quick: about 8-12 web searches per supplier. A source URL for every fact; never invent
prices, certifications or dates.

If a company can't be found or researched: `python -m rolodex.tasks fail <id> <why>`.

## Catalog: a copy of their website's product list

The app shows it like a store: their departments and sections, every product with its photos,
item number, listed price and a link to the product page. Copy their organization and content,
not their design.

On a recheck, `list` shows how the last copy was made (`catalog`). Copy it again the same way:
automatically if that worked before; if it was made by hand, start from the current copy
(`python -m rolodex.tasks show catalog <id> > work/catalog-<id>.json`), go through their site again
and save the complete updated list: keep the products still there with their photos, add new ones,
leave out discontinued ones. Never save a copy with fewer photos than before for the same products.

1. Try the automatic copy first: `python -m rolodex.catalog <id>` (or `python -m rolodex.catalog <id> <site url>`
   when the products are on a different site than the one on file). It reads Shopify and WooCommerce
   stores and any site whose product pages carry structured data, obeys robots.txt, and saves
   straight into the rolodex, from the site's English version when it has one. It prints `"saved": true`
   with the counts, or `"found": 0`. If it prints a `next_step` about translating, do that (step 2's
   English rule) and save the translated copy.
2. When it finds nothing, or clearly far fewer products than their site shows, build it by hand
   (photos are just links here; the app fetches and keeps them, so include one for every product):
   - Their navigation menu and category pages give the sections and subsections (their names,
     their order). Use `curl -sL -A "Mozilla/5.0"` for the HTML (WebFetch drops image URLs).
   - Each product (or product line, when the site lists lines rather than single items): name,
     item number, one short details line (size, pack, material), the site's description, specs
     (dimensions, sizes, material, capacity, pack/case count... as label/value pairs, exactly as the
     site states them), datasheet or spec-sheet links (`files`), listed price with unit ("" if not
     shown; a $0 or $1 placeholder is not a price), page_url, image_url, images (more photos, optional).
   - English: when their site has an English version (a language switcher, `/en/`), copy that one.
     When it only exists in another language, translate section names, product names, details,
     descriptions and spec labels into English, keeping brand and model names and item numbers as
     they are. (A Spanish "Cintas adhesivas" section means "Adhesive tapes", not the company Cintas.)
   - Photos: every product should have one if the site shows one. Use the product's own photo when
     there is one; when the site only has a photo per product line or category (common on sites
     without an online store), use the photo it shows next to that product or line. Look at all the
     `<img>` tags on the page (`src`, `data-src`, `srcset`) and CSS `background-image` URLs, not just
     og:image. Skip only logos, icons, people/contact banners, and one image repeated across unrelated
     products.
   - Include every product the site lists; give it up to about 20 minutes and say what's missing in
     `note` if you stop early. Wait about half a second between requests; skip what robots.txt disallows.
   - Write `work/catalog-<id>.json`:
     `{"source": "<site>", "note": "", "sections": [{"id": "slug", "name": "Their name", "parent_id": "" or a section id}],
      "products": [{"id": "item # or slug", "section_id": "...", "name": "...", "sku": "", "details": "",
      "description": "", "specs": [["Dimensions", "12 x 9 x 4 in"], ["Material", "Kraft"]],
      "files": [{"name": "Datasheet", "url": "https://..."}], "price": "",
      "page_url": "...", "image_url": "...", "images": []}]}`
     and save it: `python -m rolodex.tasks save catalog <id> work/catalog-<id>.json`.
   - Every save then opens each entry's page by itself (`python -m rolodex.catalog complete <id>`,
     also run after the automatic copy): a category page that shows several models (a grid of cards
     with a title, photo and link, like an "AEDs" page listing each AED) becomes a section holding
     each model with its photo, and products still without a photo get one from their own page
     (lazy-loaded pictures included, and a product-family photo the page labels with the product's
     name). It also reads each product's own page for its spec table, dimensions, datasheet links and
     full description. So list categories as you find them; you don't have to open every one by
     hand. Check the `models`, `photos` and `specs` counts it prints and look again at anything it
     couldn't fill.
   - A supplier with no product list at all (a service company, say) gets a catalog of its services
     the same way, from its services pages.

## 4. Anything added meanwhile

Run `list` again; if anything new is waiting, do steps 2-3 for it. Repeat until nothing is waiting.

## 5. Finish

`python -m rolodex.tasks finish <one or two sentences: how many cards read, how many suppliers
researched, anything that needs attention>`.
