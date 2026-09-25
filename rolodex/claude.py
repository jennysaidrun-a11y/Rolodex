"""Everything that calls Claude: reading cards, researching suppliers, answering questions."""

from __future__ import annotations

import base64
import json
from pathlib import Path

try:   # only needed for the API mode; the Claude Code tools use this module's prompts without it
    import anthropic
except ImportError:
    anthropic = None

from . import config
from .db import TAG_GROUPS

CONTEXT = (
    "You work for a commercial bakery (production lines for mixing, baking, packaging, warehouse and "
    "sanitation). Sales reps drop off business cards; the bakery keeps them in a supplier rolodex so that "
    "when a need comes up, possibly months or years later, it can find who sells what. Changes of supplier "
    "are slow and depend on customer requirements (certifications, audits, allergen control), so accuracy "
    "and sources matter more than salesmanship."
)

# Server-side refusal fallbacks are supported on these models; other models are called without them.
_FALLBACK_MODELS = {"claude-opus-5", "claude-opus-5-5", "claude-fable-5-1", "claude-fable-5"}

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if anthropic is None:
        raise ClaudeError("The anthropic package isn't installed (pip install anthropic).")
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


class ClaudeError(RuntimeError):
    pass


def _schema(properties: dict) -> dict:
    """An object schema where every field is required (what structured outputs expect)."""
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


STR = {"type": "string"}
STR_LIST = {"type": "array", "items": STR}

def _categories_field(categories: list[dict]) -> dict:
    """Categories must come from the rolodex's managed list (Categories page)."""
    names = [c["name"] for c in categories]
    item = {"type": "string", "enum": names} if names else STR
    return {"type": "array", "items": item}


def _category_guide(categories: list[dict]) -> str:
    return "Categories to choose from:\n" + "\n".join(
        f"- {c['name']}" + (f": {c['description']}" if c.get("description") else "") for c in categories)


# Where a price came from, most to least specific to this supplier.
PRICE_KINDS = ["supplier_price", "distributor_listing", "public_contract", "market_benchmark", "estimate"]

PRICING_GUIDE = (
    "Pricing is required: the bakery needs a realistic idea of cost before calling the rep. Most B2B "
    "suppliers don't publish prices, so work outward until you have real numbers, and label each:\n"
    "- supplier_price: the supplier's own price list, catalog, online store, or prices in their pamphlet PDF\n"
    "- distributor_listing: the same product or brand (or a close equivalent, named in item) at a "
    "distributor or online seller: WebstaurantStore, Uline, Grainger, Amazon Business, KaTom, Restaurant "
    "Depot, Central Restaurant Products, bakery supply shops, their authorized distributors\n"
    "- public_contract: government or institutional price lists and bid awards (GSA Advantage, state or "
    "school-district contracts)\n"
    "- market_benchmark: commodity or index prices for ingredients (USDA AMS market reports, wheat/sugar/"
    "dairy/egg futures or trade-press prices), or industry rate surveys for services and freight\n"
    "- estimate: only when nothing above exists; give a range and say what it's based on in item\n"
    "Give 2-6 entries for their main products or services, each with price, unit (per lb, per case of 1000, "
    "per 5-gal pail, per hour, per month...), source URL and as_of (YYYY-MM). Never present a distributor, "
    "benchmark or estimate as the supplier's own price. pricing_summary: one or two sentences on what to "
    "expect to pay and how this supplier prices (published list, quote only, volume breaks, minimums, "
    "freight), so the buyer knows what to ask for."
)


def card_schema(categories: list[dict]) -> dict:
    return _schema({
        "company": STR,
        "contact_name": STR,
        "contact_title": STR,
        "phone": STR,
        "email": STR,
        "website": STR,
        "address": STR,
        "categories": _categories_field(categories),
        "products_mentioned": STR_LIST,
        "document_title": STR,
        "other_text": STR,
    })


def research_schema(categories: list[dict]) -> dict:
    return _schema({
        "business_status": {"type": "string", "enum": ["active", "closed", "acquired", "unknown"]},
        "summary": STR,
        "categories": _categories_field(categories),
        "products": {"type": "array", "items": _schema({
            "name": STR, "details": STR, "image_url": STR, "page_url": STR})},
        "pricing": {"type": "array", "items": _schema({
            "item": STR, "price": STR, "unit": STR,
            "kind": {"type": "string", "enum": PRICE_KINDS},
            "source": STR, "as_of": STR})},
        "pricing_summary": STR,
        "stock_and_lead_times": STR,
        "minimum_order": STR,
        "locations": {"type": "array", "items": _schema({"kind": STR, "address": STR})},
        "service_area": STR,
        "certifications": {"type": "array", "items": _schema({"name": STR, "status": STR, "source": STR})},
        "reviews": _schema({"summary": STR, "sources": STR_LIST}),
        "regulatory": {"type": "array", "items": _schema({"date": STR, "kind": STR, "description": STR,
                                                          "source": STR})},
        "news": {"type": "array", "items": _schema({"date": STR, "headline": STR, "source": STR})},
        "changes_since_last_check": STR_LIST,
        "needs_attention": {"type": "boolean"},
        "attention_reason": STR,
        "sources": STR_LIST,
        "brochures": {"type": "array", "items": _schema({
            "card_id": STR, "title": STR, "pdf_url": STR, "summary": STR})},
        "tags": {"type": "array", "items": _schema({"group": {"type": "string", "enum": TAG_GROUPS}, "name": STR})},
    })


ANSWER_SCHEMA = _schema({
    "answer": STR,
    "matches": {"type": "array", "items": _schema({"supplier_id": {"type": "integer"}, "why": STR})},
})


def _create(**kwargs):
    model = config.CLAUDE_MODEL
    extra = {}
    if model in _FALLBACK_MODELS:
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    api = client()
    try:
        return api.beta.messages.create(model=model, **extra, **kwargs)
    except anthropic.AuthenticationError as e:
        raise ClaudeError("The Anthropic API key is missing or wrong (set ANTHROPIC_API_KEY).") from e
    except anthropic.RateLimitError as e:
        raise ClaudeError("Claude is rate limited right now; try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Claude API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ClaudeError("Couldn't reach the Claude API (network problem).") from e


def _json_result(response) -> dict:
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request.")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("Claude's answer was cut off (too long).")
    texts = [b.text for b in response.content if b.type == "text"]
    if not texts:
        raise ClaudeError("Claude returned no answer.")
    try:
        return json.loads(texts[-1])
    except json.JSONDecodeError as e:
        raise ClaudeError("Claude's answer wasn't valid JSON.") from e


def _image(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def card_prompt(categories: list[dict], kind: str = "card") -> str:
    """Instructions for reading a card or pamphlet; shared by the API path and the Claude Code workflow."""
    if kind == "pamphlet":
        return (
            "This photo is the front cover of a supplier's pamphlet or brochure (the full version is found "
            "online later, as a PDF). document_title: the pamphlet's title as printed, plus any edition, "
            "year or product line shown, so it can be searched for. Copy the company's contact details "
            "exactly as printed (leave a field empty if it isn't there; don't guess); contact_name/"
            "contact_title only if a specific rep is named. Pick every category that fits what they sell "
            "to a bakery. products_mentioned: products or product lines shown on the cover. other_text: "
            "anything else useful on the cover (certifications, slogans, promotions with dates).\n\n"
            + _category_guide(categories))
    return (
        "Read this business card. Copy contact details exactly as printed (leave a field empty if it isn't on "
        "the card; don't guess). If there are several phone numbers, put the direct/mobile first and join "
        "them with ' / '. Leave document_title empty. Pick every category that fits what the company sells to a bakery, list any "
        "products or services printed on the card, and put any other useful text (taglines, "
        "certifications, handwritten notes) in other_text.\n\n" + _category_guide(categories))


def read_card(photos: list[Path], categories: list[dict], kind: str = "card") -> dict:
    """Pull contact details and what they sell off a business card (front, back) or pamphlet (pages)."""
    content = []
    for i, photo in enumerate(photos):
        label = (["Front of the card:", "Back of the card:"][i] if kind == "card" and i < 2
                 else f"Page {i + 1}:")
        content += [{"type": "text", "text": label}, _image(photo)]
    content.append({"type": "text", "text": card_prompt(categories, kind)})
    response = _create(
        max_tokens=4000,
        system=CONTEXT,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": card_schema(categories)}},
    )
    return _json_result(response)


TAG_GUIDE = (
    "tags: 5-15 short filter tags (1-3 words, Title Case) that buyers would filter on, each in a group:\n"
    "- Product: specific things they sell, e.g. Bread Bags, Corrugated Boxes, High-Gluten Flour, "
    "Sesame Seeds, Spiral Mixers, Conveyor Belting\n"
    "- Certification: e.g. SQF, BRCGS, FSSC 22000, Organic, Kosher, Halal, Non-GMO Project, Gluten-Free "
    "Certified (only if confirmed current)\n"
    "- Capability: e.g. Nut-Free Facility, Custom Printing, Private Label, Bulk Totes, Same-Week Delivery, "
    "Equipment Repair, 24/7 Service, Compostable\n"
    "- Service area: e.g. Nationwide, Midwest, Kansas\n"
    "- Other: e.g. Distributor, Manufacturer, Family-Owned, Recall History\n"
    "Only tag what the sources support."
)


def research_prompt(supplier: dict, notes: list[str], vocabulary: list[dict] | None = None,
                    categories: list[dict] | None = None, pamphlets: list[dict] | None = None) -> str:
    """Instructions for researching a supplier; shared by the API path and the Claude Code workflow."""
    known = {k: supplier[k] for k in ("company", "contact_name", "phone", "email", "website", "address",
                                      "categories")}
    previous = supplier.get("profile") or {}
    return (
        f"Research this supplier for our rolodex.\n\nWhat we have on file:\n{json.dumps(known, indent=2)}\n\n"
        + (f"Our staff notes:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" if notes else "")
        + (f"Profile from the last check ({supplier.get('last_checked')}):\n{json.dumps(previous, indent=2)}\n\n"
           if previous else "This is the first check.\n\n")
        + "Use web search to find and verify, with a source URL for each fact:\n"
        "- what they sell that a bakery would buy (products, brands, services) and the categories that fit. "
        "products: 6-15 of their products or product lines a bakery would care about, from their own website "
        "(catalog, shop or product pages; a distributor's page for their brand if they have no site), each "
        "with page_url (the product page) and image_url (the direct URL of that product's own photo: "
        "a .jpg/.png/.webp file, not a logo, banner, icon or placeholder; empty if there isn't one)\n"
        "- pricing, following the pricing rules below\n"
        "- stock, lead times and minimum order, if published\n"
        "- locations (HQ, plants, warehouses) and the area they serve\n"
        "- certifications relevant to food manufacturing (SQF, BRCGS, FSSC 22000, organic, kosher, halal, "
        "non-GMO, allergen programs), with status\n"
        "- reviews or reputation (customer reviews, BBB, trade press)\n"
        "- regulatory history: FDA/USDA recalls, warning letters, import alerts, OSHA or EPA actions, lawsuits "
        "relevant to supply\n"
        "- recent news: acquisitions, closures, new plants, leadership changes\n\n"
        "Make sure it is the same business as the card (match website, address or phone). If you can't "
        "confirm a fact, leave it out. Dates as YYYY-MM or YYYY-MM-DD.\n"
        + ("\nWe have the front cover of these pamphlets from them:\n"
           + "\n".join(f"- card_id {p['id']}: {p['title'] or '(title not read)'}" for p in pamphlets)
           + "\nFind the PDF version of each online (their website's literature/downloads/resources pages "
           "first, then a web search). Read it and use what's in it (products, specs, certifications, "
           "minimum orders) in the profile. List each in brochures with its card_id and a direct link to "
           "the PDF file (pdf_url empty if you can't find it), plus a 1-2 sentence summary.\n"
           if pamphlets else "")
        + "brochures: also list other useful PDFs you find (catalogs, spec sheets, allergen or "
        "certification documents) with card_id \"0\". Only direct links to PDF files.\n"
        "changes_since_last_check: short bullets of what differs from the last check (empty on the first "
        "check). Set needs_attention for anything the bakery should look at: a recall or warning letter, a "
        "lost certification, a closure or acquisition, or a website/phone that no longer works. "
        "The summary is 2-3 sentences on who they are and what they could supply us.\n\n"
        + (_category_guide(categories) + "\n\n" if categories else "")
        + PRICING_GUIDE + "\n\n"
        + TAG_GUIDE
        + ("\nTags already used in the rolodex (reuse these exact spellings whenever one fits; add a new tag "
           "only for something none of them covers):\n"
           + "\n".join(f"- {t['group']}: {t['name']}" for t in vocabulary) if vocabulary else "")
    )


def research(supplier: dict, notes: list[str], vocabulary: list[dict] | None = None,
             categories: list[dict] | None = None, pamphlets: list[dict] | None = None) -> dict:
    """Look the supplier up on the web and return a fresh profile, noting what changed since last time."""
    messages = [{"role": "user", "content": research_prompt(supplier, notes, vocabulary, categories, pamphlets)}]
    for _ in range(6):   # web search can pause a long turn; resume it a few times
        response = _create(
            max_tokens=16000,
            system=CONTEXT,
            messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 12},
                   {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6}],   # opens PDFs
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": research_schema(categories or [])}},
        )
        if response.stop_reason != "pause_turn":
            return _json_result(response)
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    raise ClaudeError("Research took too long and was stopped.")


def directory(suppliers: list[dict], notes: dict[int, list[str]]) -> str:
    rows = []
    for s in suppliers:
        p = s["profile"]
        rows.append({
            "id": s["id"], "company": s["company"], "status": s["status"], "categories": s["categories"],
            "tags": [t["name"] for t in s.get("all_tags", [])],
            "contact": " ".join(x for x in (s["contact_name"], s["phone"], s["email"]) if x),
            "address": s["address"], "summary": s["summary"],
            "products": [x["name"] for x in p.get("products", [])],
            "locations": [x["address"] for x in p.get("locations", [])], "service_area": p.get("service_area", ""),
            "certifications": [f"{x['name']} ({x['status']})" for x in p.get("certifications", [])],
            "minimum_order": p.get("minimum_order", ""), "lead_times": p.get("stock_and_lead_times", ""),
            "regulatory": [f"{x['date']} {x['kind']}: {x['description']}" for x in p.get("regulatory", [])],
            "attention": s["attention_note"] if s["needs_attention"] else "",
            "last_checked": s["last_checked"], "staff_notes": notes.get(s["id"], []),
        })
    return json.dumps(rows, separators=(",", ":"))


def ask(question: str, suppliers: list[dict], notes: dict[int, list[str]]) -> dict:
    """Answer a plain-English question from the rolodex; returns {answer, matches:[{supplier_id, why}]}."""
    response = _create(
        max_tokens=8000,
        system=[
            {"type": "text", "text": CONTEXT + (
                " Answer questions using only the supplier directory below. Recommend the suppliers that best "
                "fit and say why in a sentence each; mention anything that argues against one (a recall, a "
                "lapsed certification, out of area). If nothing on file fits, say so plainly and suggest "
                "what kind of supplier to look for. Keep the answer short.")},
            {"type": "text", "text": "Supplier directory (JSON):\n" + directory(suppliers, notes),
             "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": question}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
    )
    return _json_result(response)
