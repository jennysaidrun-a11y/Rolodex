"""Everything that calls Claude: reading cards, researching suppliers, answering questions."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import anthropic

from . import config
from .db import TAG_GROUPS

CATEGORIES = [
    "Flour & grains", "Sweeteners", "Dairy & eggs", "Fats & oils", "Yeast & cultures",
    "Other ingredients", "Packaging", "Labels & printing", "Sanitation & chemicals", "Pest control",
    "Equipment", "Parts & maintenance", "Pallets & warehouse supplies", "Freight & logistics",
    "Uniforms & PPE", "Food safety & lab testing", "Utilities & energy", "Staffing", "IT & software",
    "Other services",
]

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

CARD_SCHEMA = _schema({
    "company": STR,
    "contact_name": STR,
    "contact_title": STR,
    "phone": STR,
    "email": STR,
    "website": STR,
    "address": STR,
    "categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
    "products_mentioned": STR_LIST,
    "other_text": STR,
})

RESEARCH_SCHEMA = _schema({
    "business_status": {"type": "string", "enum": ["active", "closed", "acquired", "unknown"]},
    "summary": STR,
    "categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
    "products": {"type": "array", "items": _schema({"name": STR, "details": STR})},
    "pricing": {"type": "array", "items": _schema({"item": STR, "price": STR, "source": STR})},
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
    try:
        return client().beta.messages.create(model=model, **extra, **kwargs)
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


def read_card(front: Path, back: Path | None) -> dict:
    """Pull the contact details and what they sell off a business card (front and optional back)."""
    content = [{"type": "text", "text": "Front of the card:"}, _image(front)]
    if back:
        content += [{"type": "text", "text": "Back of the card:"}, _image(back)]
    content.append({"type": "text", "text": (
        "Read this business card. Copy contact details exactly as printed (leave a field empty if it isn't on "
        "the card; don't guess). If there are several phone numbers, put the direct/mobile first and join "
        "them with ' / '. Pick every category that fits what the company sells to a bakery, list any "
        "products or services printed on the card, and put any other useful text (taglines, "
        "certifications, handwritten notes) in other_text.")})
    response = _create(
        max_tokens=4000,
        system=CONTEXT,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CARD_SCHEMA}},
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


def research(supplier: dict, notes: list[str], vocabulary: list[dict] | None = None) -> dict:
    """Look the supplier up on the web and return a fresh profile, noting what changed since last time."""
    known = {k: supplier[k] for k in ("company", "contact_name", "phone", "email", "website", "address",
                                      "categories")}
    previous = supplier.get("profile") or {}
    prompt = (
        f"Research this supplier for our rolodex.\n\nWhat we have on file:\n{json.dumps(known, indent=2)}\n\n"
        + (f"Our staff notes:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" if notes else "")
        + (f"Profile from the last check ({supplier.get('last_checked')}):\n{json.dumps(previous, indent=2)}\n\n"
           if previous else "This is the first check.\n\n")
        + "Use web search to find and verify, with a source URL for each fact:\n"
        "- what they sell that a bakery would buy (products, brands, services) and the categories that fit\n"
        "- any published pricing (most suppliers don't publish it; say so rather than inventing numbers)\n"
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
        "changes_since_last_check: short bullets of what differs from the last check (empty on the first "
        "check). Set needs_attention for anything the bakery should look at: a recall or warning letter, a "
        "lost certification, a closure or acquisition, or a website/phone that no longer works. "
        "The summary is 2-3 sentences on who they are and what they could supply us.\n\n"
        + TAG_GUIDE
        + ("\nTags already used in the rolodex (reuse these exact spellings whenever one fits; add a new tag "
           "only for something none of them covers):\n"
           + "\n".join(f"- {t['group']}: {t['name']}" for t in vocabulary) if vocabulary else "")
    )
    messages = [{"role": "user", "content": prompt}]
    for _ in range(6):   # web search can pause a long turn; resume it a few times
        response = _create(
            max_tokens=16000,
            system=CONTEXT,
            messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 12}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": RESEARCH_SCHEMA}},
        )
        if response.stop_reason != "pause_turn":
            return _json_result(response)
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    raise ClaudeError("Research took too long and was stopped.")


def _directory(suppliers: list[dict], notes: dict[int, list[str]]) -> str:
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
            {"type": "text", "text": "Supplier directory (JSON):\n" + _directory(suppliers, notes),
             "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": question}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
    )
    return _json_result(response)
