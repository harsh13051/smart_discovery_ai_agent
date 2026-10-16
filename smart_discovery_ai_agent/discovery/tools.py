"""The agent's tools: validated inputs, structured outputs, honest errors.

These are ordinary Python functions. The LLM decides *which* to call and with
what arguments; the filtering, budget maths and ranking inside them are
deterministic application logic, not model reasoning.

A ``ToolContext`` holds per-request working state (current candidates, the last
ranking) server-side, so the model only ever sees compact summaries and the UI
cards are built from real records, never from model-written text.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import ranking
from .prefs import AMENITIES, DIETS, KINDS, OCCASIONS, Prefs, PrefError, calculate_group_budget, prefs_from_args, validate
from .providers.demo import DemoProvider
from .providers.osm import GeocodeError, OSMProvider, ProviderError
from .schema import Restaurant, haversine_km, open_now


@dataclass
class ToolContext:
    osm: OSMProvider
    demo: Optional[DemoProvider] = None
    cache_lookup: Callable[[str], Optional[Restaurant]] = lambda _id: None
    cache_store: Callable[[list[Restaurant]], None] = lambda items: None
    saved_prefs: dict[str, Any] = field(default_factory=dict)          # profile defaults
    conversation_context: dict[str, Any] = field(default_factory=dict)  # from the DB
    user_location: Optional[tuple[float, float]] = None                # browser geolocation
    prefs: Prefs = field(default_factory=Prefs)
    candidates: dict[str, Restaurant] = field(default_factory=dict)
    kept: dict[str, Restaurant] = field(default_factory=dict)
    search_point: Optional[tuple[float, float]] = None
    search_meta: dict[str, Any] = field(default_factory=dict)
    verification_notes: list[str] = field(default_factory=list)
    ranking: list[dict[str, Any]] = field(default_factory=list)
    comparison: Optional[dict[str, Any]] = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _compact(r: Restaurant) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "kind": r.kind, "cuisines": r.cuisines,
            "distance_km": r.distance_km, "vegetarian": r.vegetarian,
            "has_price": r.cost_per_person is not None, "has_rating": r.rating is not None}


# ----------------------------------------------------------------- the tools
def extract_restaurant_preferences(ctx: ToolContext, **args: Any) -> dict[str, Any]:
    """Validate/normalise preferences the model (or rule extractor) pulled from the user's message."""
    try:
        base = ctx.prefs
        ctx.prefs = prefs_from_args(args, base)
    except PrefError as exc:
        return {"error": str(exc)}
    p = ctx.prefs
    if ctx.user_location and p.near_me and p.lat is None:
        p.lat, p.lon = ctx.user_location
    missing = []
    if not (p.location_text or p.lat is not None):
        missing.append("location")
    notes = []
    if p.budget_amount is not None and p.party_size is None:
        notes.append("Party size not stated; assuming 2 for cost estimates.")
    return {"preferences": p.to_dict(), "missing": missing, "notes": notes}


def retrieve_conversation_context(ctx: ToolContext) -> dict[str, Any]:
    return {"previous_preferences": ctx.conversation_context.get("prefs"),
            "last_shortlist": ctx.conversation_context.get("last_shortlist", []),
            "saved_profile_preferences": ctx.saved_prefs,
            "user_location_available": ctx.user_location is not None}


def search_restaurants(ctx: ToolContext, location_text: Optional[str] = None, lat: Optional[float] = None,
                       lon: Optional[float] = None, radius_km: Optional[float] = None,
                       use_demo: Optional[bool] = None) -> dict[str, Any]:
    p = ctx.prefs
    try:
        p = prefs_from_args({k: v for k, v in dict(location_text=location_text, lat=lat, lon=lon,
                                                    radius_km=radius_km, use_demo=use_demo).items() if v is not None}, p)
    except PrefError as exc:
        return {"error": str(exc)}
    ctx.prefs = p
    if p.use_demo:
        return _search_demo(ctx, p)
    try:
        if p.lat is not None:
            point, label = (p.lat, p.lon), p.location_text or "your location"
        elif p.near_me and ctx.user_location:
            point, label = ctx.user_location, "your location"
        elif p.location_text:
            g = ctx.osm.geocode(p.location_text)
            point, label = (g.lat, g.lon), g.label
        else:
            return {"error": "No location given. Ask the user where to search."}
        items, meta = ctx.osm.search(point[0], point[1], int(p.radius_km * 1000))
    except GeocodeError as exc:
        return {"error": str(exc), "retryable": False}
    except ProviderError as exc:
        ctx.errors.append(str(exc))
        return {"error": f"Restaurant data provider unavailable: {exc}", "retryable": True}
    ctx.search_point = point
    ctx.candidates = {r.id: r for r in items}
    ctx.kept = dict(ctx.candidates)
    ctx.search_meta = {"provider": "OpenStreetMap", "endpoint": meta.endpoint, "fetched_at": meta.fetched_at,
                       "from_cache": meta.from_cache, "fallback": meta.fallback, "warnings": meta.warnings or [],
                       "location": label, "radius_km": p.radius_km}
    ctx.cache_store(items)
    if p.kind:
        ctx.kept = {i: r for i, r in ctx.kept.items() if r.kind == p.kind}
    return {"found": len(ctx.candidates), "kept": len(ctx.kept), "location": label, "source": ctx.search_meta,
            "sample": [_compact(r) for r in list(ctx.kept.values())[:30]],
            "data_notes": "OpenStreetMap has no prices or ratings; many places lack cuisine/diet/amenity tags."}


def _search_demo(ctx: ToolContext, p: Prefs) -> dict[str, Any]:
    if ctx.demo is None:
        return {"error": "Demo data is disabled on this server."}
    area = ctx.demo.resolve_area(p.location_text or "")
    if not area:
        return {"error": "Demo data only covers these Pune areas: " + ", ".join(ctx.demo.graph.areas) + "."}
    items = ctx.demo.search(area)
    ctx.candidates = {r.id: r for r in items}
    ctx.kept = dict(ctx.candidates)
    ctx.search_point = None
    ctx.search_meta = {"provider": "DEMO DATA (fictional)", "endpoint": "local file", "fetched_at": None,
                       "from_cache": False, "fallback": False, "location": area, "radius_km": 6.0,
                       "warnings": ["These restaurants are fictional demo records, not real places."]}
    ctx.cache_store(items)
    return {"found": len(items), "kept": len(items), "location": area, "source": ctx.search_meta,
            "sample": [_compact(r) for r in items[:30]], "data_notes": "DEMO DATA: fictional restaurants."}


def _apply(ctx: ToolContext, keep: Callable[[Restaurant], bool]) -> tuple[int, int]:
    before = len(ctx.kept)
    ctx.kept = {i: r for i, r in ctx.kept.items() if keep(r)}
    return before, len(ctx.kept)


def filter_by_location(ctx: ToolContext, max_km: float) -> dict[str, Any]:
    if not isinstance(max_km, (int, float)) or not (0.2 <= max_km <= 8):
        return {"error": "max_km must be between 0.2 and 8."}
    ctx.prefs.radius_km = float(max_km)
    before, after = _apply(ctx, lambda r: r.distance_km is None or r.distance_km <= max_km)
    return {"before": before, "after": after, "removed_for": f"farther than {max_km} km"}


def filter_by_cuisine(ctx: ToolContext, cuisines: list[str]) -> dict[str, Any]:
    ctx.prefs.cuisines = [c.strip().lower().replace(" ", "_") for c in cuisines if c.strip()]
    wanted = ctx.prefs.cuisines
    before, after = _apply(ctx, lambda r: ranking.cuisine_match(r, wanted) != "none")
    unknown = sum(1 for r in ctx.kept.values() if ranking.cuisine_match(r, wanted) == "unknown")
    return {"before": before, "after": after, "unverified_cuisine": unknown,
            "note": "Places with no cuisine tag are kept but flagged unverified; places with a different cuisine are removed."}


def filter_by_dietary_preferences(ctx: ToolContext, diet: str) -> dict[str, Any]:
    if diet not in DIETS:
        return {"error": f"diet must be one of {', '.join(DIETS)}."}
    ctx.prefs.diet = diet
    before, after = _apply(ctx, lambda r: ranking.diet_status(r, diet) != "conflict")
    confirmed = sum(1 for r in ctx.kept.values() if ranking.diet_status(r, diet) == "confirmed")
    return {"before": before, "after": after, "confirmed": confirmed, "unverified": after - confirmed,
            "note": "Only explicit conflicts (e.g. diet:vegetarian=no) are removed; unlisted diet info stays flagged unverified."}


def filter_by_amenities(ctx: ToolContext, amenities: list[str]) -> dict[str, Any]:
    bad = [a for a in amenities if a not in AMENITIES]
    if bad:
        return {"error": f"Unknown amenities {bad}; allowed: {', '.join(AMENITIES)}."}
    ctx.prefs.amenities = list(dict.fromkeys(amenities))
    before, after = _apply(ctx, lambda r: all(ranking.amenity_status(r, a) != "conflict" for a in amenities))
    return {"before": before, "after": after,
            "confirmed_all": sum(1 for r in ctx.kept.values() if all(ranking.amenity_status(r, a) == "confirmed" for a in amenities))}


def filter_by_rating(ctx: ToolContext, min_rating: float) -> dict[str, Any]:
    if not isinstance(min_rating, (int, float)) or not (0 <= min_rating <= 5):
        return {"error": "min_rating must be between 0 and 5."}
    ctx.prefs.min_rating = float(min_rating)
    before, after = _apply(ctx, lambda r: r.rating is None or r.rating >= min_rating)
    return {"before": before, "after": after,
            "unrated_kept": sum(1 for r in ctx.kept.values() if r.rating is None),
            "note": "Places without a rating are kept but cannot be verified against the minimum."}


def calculate_group_budget_tool(ctx: ToolContext, budget_amount: float, budget_type: str = "total",
                                party_size: Optional[int] = None) -> dict[str, Any]:
    try:
        return calculate_group_budget(budget_amount, budget_type, party_size)
    except PrefError as exc:
        return {"error": str(exc)}


def filter_by_budget(ctx: ToolContext, budget_amount: float, budget_type: str = "total",
                     party_size: Optional[int] = None, require_known_price: bool = False) -> dict[str, Any]:
    try:
        ctx.prefs = prefs_from_args({"budget_amount": budget_amount, "budget_type": budget_type,
                                     "party_size": party_size}, ctx.prefs)
    except PrefError as exc:
        return {"error": str(exc)}
    summary = ranking.budget_summary(ctx.prefs)
    before = len(ctx.kept)
    over = sum(1 for r in ctx.kept.values() if ranking.budget_status(r, ctx.prefs) == "over")

    def keep(r: Restaurant) -> bool:
        st = ranking.budget_status(r, ctx.prefs)
        return st == "within" or (st == "unverified" and not require_known_price)
    _apply(ctx, keep)
    unverified = sum(1 for r in ctx.kept.values() if ranking.budget_status(r, ctx.prefs) == "unverified")
    if unverified:
        ctx.verification_notes.append(
            f"{unverified} of {len(ctx.kept)} shortlisted places have no listed price, so your budget could not be verified for them.")
    return {"budget": summary, "before": before, "after": len(ctx.kept), "removed_over_budget": over,
            "unverified_price": unverified,
            "note": "Estimates = listed average per person × party size; excludes taxes, drinks and service charge."}


def check_opening_hours(ctx: ToolContext, require_open: bool = False) -> dict[str, Any]:
    statuses = {i: open_now(r.opening_hours) for i, r in ctx.kept.items()}
    if require_open:
        ctx.prefs.open_now = True
        before, after = _apply(ctx, lambda r: statuses.get(r.id) is not False)
    else:
        before = after = len(ctx.kept)
    vals = list(statuses.values())
    return {"before": before, "after": after, "open_now": sum(v is True for v in vals),
            "closed_now": sum(v is False for v in vals), "unknown": sum(v is None for v in vals),
            "note": "Computed from OSM opening_hours in IST; unknown means hours missing or not interpretable."}


def rank_restaurants(ctx: ToolContext, top_n: int = 5) -> dict[str, Any]:
    top_n = max(1, min(int(top_n), 61))
    ctx.ranking = ranking.rank(list(ctx.kept.values()), ctx.prefs, top_n)
    ctx.cache_store([r for r in ctx.kept.values()])
    return {"ranked": [{"id": c["id"], "name": c["name"], "score": c["score"], "reasons": c["reasons"],
                        "trade_offs": c["trade_offs"], "estimated_total": c["estimated_total"],
                        "distance_km": c["distance_km"], "open_now": c["open_now"]} for c in ctx.ranking],
            "total_candidates": len(ctx.kept), "weights": ranking.WEIGHTS,
            "note": "Cards shown to the user are built from these records; do not invent other details."}


def _find(ctx: ToolContext, rid: str) -> Optional[Restaurant]:
    return ctx.candidates.get(rid) or ctx.kept.get(rid) or ctx.cache_lookup(rid)


def get_restaurant_details(ctx: ToolContext, restaurant_id: str) -> dict[str, Any]:
    r = _find(ctx, restaurant_id)
    if not r:
        return {"error": f"Unknown restaurant id '{restaurant_id}'. Use ids returned by search_restaurants."}
    return {"restaurant": r.to_dict()}


COMPARE_FIELDS = ("kind", "cuisines", "address", "cost_per_person", "rating", "review_count", "vegetarian",
                  "vegan", "opening_hours", "open_now", "distance_km", "website")


def compare_restaurants(ctx: ToolContext, restaurant_ids: list[str]) -> dict[str, Any]:
    if not (2 <= len(restaurant_ids) <= 5):
        return {"error": "Provide 2 to 5 restaurant ids."}
    found, missing = [], []
    for rid in dict.fromkeys(restaurant_ids):
        r = _find(ctx, rid)
        (found if r else missing).append(r or rid)
    if len(found) < 2:
        return {"error": "Could not find enough of those restaurants.", "missing": missing}
    rows = [r.to_dict() for r in found]  # type: ignore[union-attr]
    notes = []
    priced = [r for r in rows if r["cost_per_person"] is not None]
    rated = [r for r in rows if r["rating"] is not None]
    if priced:
        c = min(priced, key=lambda r: r["cost_per_person"])
        notes.append(f"Lowest listed cost: {c['name']} (₹{c['cost_per_person']}/person"
                     f"{', demo data' if c['is_demo'] else ''}).")
    if len(priced) < len(rows):
        notes.append(f"Price is unavailable for {len(rows) - len(priced)} of {len(rows)} places.")
    if rated:
        b = max(rated, key=lambda r: r["rating"])
        notes.append(f"Highest listed rating: {b['name']} ({b['rating']}/5).")
    if len(rated) < len(rows):
        notes.append(f"Rating is unavailable for {len(rows) - len(rated)} of {len(rows)} places.")
    near = [r for r in rows if r["distance_km"] is not None]
    if near:
        n = min(near, key=lambda r: r["distance_km"])
        notes.append(f"Nearest: {n['name']} ({n['distance_km']} km).")
    opens = [r["name"] for r in rows if r["open_now"] is True]
    if opens:
        notes.append("Open now (per listed hours): " + ", ".join(opens) + ".")
    ctx.comparison = {"restaurants": rows, "fields": list(COMPARE_FIELDS), "differences": notes, "missing_ids": missing}
    return {"differences": notes, "restaurants": [{k: r[k] for k in ("id", "name", *COMPARE_FIELDS)} for r in rows],
            "missing_ids": missing, "note": "Blank/None means the provider did not supply the value."}


# ----------------------------------------------------------- registry + specs
def _spec(name: str, description: str, props: dict[str, Any], required: Optional[list[str]] = None) -> dict[str, Any]:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props, "required": required or []}}


_PREF_PROPS = {
    "location_text": {"type": "string", "description": "Place to search, e.g. 'Baner, Pune'."},
    "near_me": {"type": "boolean", "description": "True if the user said 'near me' (uses their shared location)."},
    "radius_km": {"type": "number"},
    "cuisines": {"type": "array", "items": {"type": "string"}, "description": "e.g. north_indian, italian, cafe, chinese"},
    "kind": {"type": "string", "enum": list(KINDS)},
    "diet": {"type": "string", "enum": list(DIETS)},
    "budget_amount": {"type": "number", "description": "Rupees."},
    "budget_type": {"type": "string", "enum": ["total", "per_person"],
                    "description": "'total' = whole group's budget; 'per_person' = each person's budget."},
    "party_size": {"type": "integer"},
    "amenities": {"type": "array", "items": {"type": "string", "enum": list(AMENITIES)}},
    "occasion": {"type": "string", "enum": list(OCCASIONS)},
    "min_rating": {"type": "number"},
    "open_now": {"type": "boolean"},
}

TOOL_SPECS: list[dict[str, Any]] = [
    _spec("extract_restaurant_preferences",
          "Record the user's structured preferences (validated by the server). Call first for every restaurant request; "
          "only include values the user actually stated. Returns missing fields (ask the user for those).", _PREF_PROPS),
    _spec("retrieve_conversation_context", "Get earlier preferences/shortlist from this conversation and saved profile defaults.", {}),
    _spec("search_restaurants", "Fetch real restaurants around a place or coordinates from the data provider.",
          {"location_text": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"},
           "radius_km": {"type": "number"}, "use_demo": {"type": "boolean", "description": "Only if the user asked for demo data."}}),
    _spec("filter_by_location", "Drop restaurants farther than max_km from the search point.", {"max_km": {"type": "number"}}, ["max_km"]),
    _spec("filter_by_cuisine", "Keep restaurants matching cuisines (unknown cuisine is kept but flagged).",
          {"cuisines": {"type": "array", "items": {"type": "string"}}}, ["cuisines"]),
    _spec("filter_by_dietary_preferences", "Apply a dietary requirement.", {"diet": {"type": "string", "enum": list(DIETS)}}, ["diet"]),
    _spec("filter_by_amenities", "Require amenities such as wifi, outdoor_seating, parking.",
          {"amenities": {"type": "array", "items": {"type": "string", "enum": list(AMENITIES)}}}, ["amenities"]),
    _spec("filter_by_rating", "Apply a minimum rating (only places with a provider rating can be checked).",
          {"min_rating": {"type": "number"}}, ["min_rating"]),
    _spec("calculate_group_budget", "Convert between per-person and total group budgets.",
          {"budget_amount": {"type": "number"}, "budget_type": {"type": "string", "enum": ["total", "per_person"]},
           "party_size": {"type": "integer"}}, ["budget_amount"]),
    _spec("filter_by_budget", "Remove places whose listed price exceeds the budget; places without prices are kept and flagged.",
          {"budget_amount": {"type": "number"}, "budget_type": {"type": "string", "enum": ["total", "per_person"]},
           "party_size": {"type": "integer"}, "require_known_price": {"type": "boolean"}}, ["budget_amount"]),
    _spec("check_opening_hours", "Evaluate listed opening hours for right now (IST); optionally drop places known to be closed.",
          {"require_open": {"type": "boolean"}}),
    _spec("rank_restaurants", "Score and order the remaining places; produces the cards the user sees.",
          {"top_n": {"type": "integer"}}),
    _spec("get_restaurant_details", "Full normalized record for one restaurant id.", {"restaurant_id": {"type": "string"}}, ["restaurant_id"]),
    _spec("compare_restaurants", "Side-by-side comparison of 2-5 restaurant ids using only available attributes.",
          {"restaurant_ids": {"type": "array", "items": {"type": "string"}}}, ["restaurant_ids"]),
]

_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "extract_restaurant_preferences": extract_restaurant_preferences,
    "retrieve_conversation_context": retrieve_conversation_context,
    "search_restaurants": search_restaurants,
    "filter_by_location": filter_by_location,
    "filter_by_cuisine": filter_by_cuisine,
    "filter_by_dietary_preferences": filter_by_dietary_preferences,
    "filter_by_amenities": filter_by_amenities,
    "filter_by_rating": filter_by_rating,
    "calculate_group_budget": calculate_group_budget_tool,
    "filter_by_budget": filter_by_budget,
    "check_opening_hours": check_opening_hours,
    "rank_restaurants": rank_restaurants,
    "get_restaurant_details": get_restaurant_details,
    "compare_restaurants": compare_restaurants,
}


def execute_tool(ctx: ToolContext, name: str, args: Any) -> dict[str, Any]:
    """Run one tool; never raises. Records a trace entry."""
    started = time.monotonic()
    if name not in _REGISTRY:
        result: dict[str, Any] = {"error": f"Unknown tool '{name}'."}
    elif not isinstance(args, dict):
        result = {"error": "Tool arguments must be an object."}
    else:
        try:
            result = _REGISTRY[name](ctx, **args)
        except TypeError as exc:
            result = {"error": f"Invalid arguments for {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - tools must not crash the request
            result = {"error": f"{name} failed unexpectedly ({type(exc).__name__})."}
    ctx.trace.append({"tool": name, "args": _short(args), "ok": "error" not in result,
                      "summary": result.get("error") or _summary(name, result),
                      "ms": int((time.monotonic() - started) * 1000)})
    return result


def _short(args: Any) -> Any:
    return args if isinstance(args, dict) and len(str(args)) < 300 else str(args)[:300]


def _summary(name: str, r: dict[str, Any]) -> str:
    if "found" in r:
        return f"{r['found']} found, {r['kept']} kept"
    if "after" in r:
        return f"{r['before']} → {r['after']}"
    if "ranked" in r:
        return f"top {len(r['ranked'])} of {r['total_candidates']}"
    if "preferences" in r:
        return "missing: " + (", ".join(r["missing"]) or "nothing")
    return "ok"
