"""Transparent ranking and fact-based explanations (plain Python, not AI).

Each component is scored 0..1 or ``None`` when it does not apply. Weights are
renormalised over the applicable components, so a restaurant is never rewarded
or punished for data a provider simply does not publish - except where the user
explicitly asked for something we cannot verify, which scores low ("unverified").
"""
from __future__ import annotations

from typing import Any, Optional

from .prefs import Prefs, budget_summary
from .providers.osm import BROAD_PARENTS
from .schema import Restaurant

WEIGHTS = {
    "cuisine": 0.22, "diet": 0.14, "amenities": 0.14, "distance": 0.16,
    "budget": 0.12, "rating": 0.10, "open_now": 0.07, "completeness": 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
UNVERIFIED = 0.3


def nice(token: str) -> str:
    return token.replace("_", " ")


def cuisine_match(r: Restaurant, wanted: list[str]) -> str:
    """exact | broad | none | unknown (no cuisine data)"""
    if not wanted:
        return "n/a"
    if not r.cuisines:
        return "unknown"
    if set(wanted) & set(r.cuisines):
        return "exact"
    if any(BROAD_PARENTS.get(w) in r.cuisines for w in wanted):
        return "broad"
    return "none"


def diet_status(r: Restaurant, diet: str) -> str:
    """confirmed | unknown | conflict | n/a"""
    if diet == "any":
        return "n/a"
    value = {"vegetarian": r.vegetarian, "vegan": r.vegan, "jain": r.jain}[diet]
    if diet == "vegetarian" and value is None and "vegetarian" in r.cuisines:
        return "confirmed"
    if value in ("only", "yes"):
        return "confirmed"
    if value == "no":
        return "conflict"
    return "unknown"


def amenity_status(r: Restaurant, key: str) -> str:
    v = r.amenities.get(key)
    return "confirmed" if v is True else "conflict" if v is False else "unknown"


def estimated_total(r: Restaurant, p: Prefs) -> Optional[int]:
    return None if r.cost_per_person is None else int(r.cost_per_person * p.party)


def budget_status(r: Restaurant, p: Prefs) -> str:
    """within | over | unverified | n/a"""
    b = budget_summary(p)
    if not b:
        return "n/a"
    est = estimated_total(r, p)
    if est is None:
        return "unverified"
    return "within" if est <= b["total"] else "over"


def completeness(r: Restaurant) -> float:
    have = [bool(r.cuisines), bool(r.opening_hours), bool(r.address), bool(r.website or r.phone),
            r.vegetarian is not None, bool(r.amenities)]
    return sum(have) / len(have)


def score(r: Restaurant, p: Prefs) -> tuple[float, dict[str, Optional[float]]]:
    parts: dict[str, Optional[float]] = {k: None for k in WEIGHTS}
    cm = cuisine_match(r, p.cuisines)
    if cm != "n/a":
        parts["cuisine"] = {"exact": 1.0, "broad": 0.7, "unknown": UNVERIFIED, "none": 0.0}[cm]
    ds = diet_status(r, p.diet)
    if ds != "n/a":
        parts["diet"] = {"confirmed": 1.0, "unknown": UNVERIFIED, "conflict": 0.0}[ds]
    if p.amenities:
        vals = {"confirmed": 1.0, "unknown": UNVERIFIED, "conflict": 0.0}
        parts["amenities"] = sum(vals[amenity_status(r, a)] for a in p.amenities) / len(p.amenities)
    if r.distance_km is not None:
        parts["distance"] = max(0.0, 1.0 - r.distance_km / max(p.radius_km, 0.5))
    bs = budget_status(r, p)
    if bs == "within":
        b = budget_summary(p)
        parts["budget"] = 0.5 + 0.5 * max(0.0, 1.0 - (estimated_total(r, p) or 0) / b["total"])  # type: ignore[index]
    elif bs == "unverified":
        parts["budget"] = UNVERIFIED
    elif bs == "over":
        parts["budget"] = 0.0
    if r.rating is not None:
        quality = max(0.0, min(1.0, (r.rating - 3.0) / 2.0))
        confidence = 0.7 if r.review_count is None else min(1.0, r.review_count / 50)
        parts["rating"] = 0.5 + (quality - 0.5) * confidence
    elif p.min_rating is not None:
        parts["rating"] = UNVERIFIED
    from .schema import open_now
    on = open_now(r.opening_hours)
    if on is not None:
        parts["open_now"] = 1.0 if on else 0.0
    elif p.open_now:
        parts["open_now"] = UNVERIFIED
    parts["completeness"] = completeness(r)
    applicable = {k: v for k, v in parts.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in applicable)
    total = sum(WEIGHTS[k] * v for k, v in applicable.items()) / total_w if total_w else 0.0
    return round(100 * total, 1), {k: (None if v is None else round(v, 2)) for k, v in parts.items()}


def explain(r: Restaurant, p: Prefs) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    trade: list[str] = []
    src = "the demo data" if r.is_demo else "OpenStreetMap"

    cm = cuisine_match(r, p.cuisines)
    if cm == "exact":
        reasons.append("Serves " + ", ".join(nice(c) for c in p.cuisines if c in r.cuisines) + " (listed cuisine).")
    elif cm == "broad":
        trade.append(f"Listed only as generic {', '.join(nice(c) for c in r.cuisines)} — may or may not offer "
                     f"{', '.join(nice(c) for c in p.cuisines)}.")
    elif cm == "unknown":
        trade.append("Cuisine not listed, so the match with your cuisine request is unverified.")

    ds = diet_status(r, p.diet)
    if ds == "confirmed":
        reasons.append(f"Listed as {'vegetarian-only' if (p.diet == 'vegetarian' and r.vegetarian == 'only') else p.diet + '-friendly'}.")
    elif ds == "unknown":
        trade.append(f"{p.diet.capitalize()} options are not listed in {src}; please confirm with the restaurant.")

    for a in p.amenities:
        st = amenity_status(r, a)
        if st == "confirmed":
            reasons.append(f"Has {nice(a)} (listed).")
        elif st == "unknown":
            trade.append(f"{nice(a).capitalize()} is not listed, so it is unverified.")

    b = budget_summary(p)
    if b:
        est = estimated_total(r, p)
        if est is None:
            who = f"{p.party} people{' (assumed)' if b['party_size_assumed'] else ''}"
            trade.append(f"No price information in {src}, so your budget (₹{b['per_person']:,.0f}/person × {who} = "
                         f"₹{b['total']:,.0f} total) can't be verified.")
        else:
            reasons.append(
                f"Estimated ₹{est:,} for {p.party} (listed average ₹{r.cost_per_person}/person × {p.party}); "
                f"your budget is ₹{b['total']:,.0f} total. This is an estimate, not a bill guarantee — "
                "drinks, taxes and service charge may be extra.")
            if est > 0.9 * b["total"]:
                trade.append("Uses over 90% of your budget, so extras may push you over.")

    if r.rating is not None:
        n = f" from {r.review_count} reviews" if r.review_count else ""
        reasons.append(f"Rated {r.rating}/5{n} ({'demo data' if r.is_demo else 'OpenStreetMap'}).")
    else:
        trade.append(f"No rating or review count available from {src}.")

    if r.distance_km is not None:
        reasons.append(f"About {r.distance_km:.1f} km from {'the search point' if not p.location_text else p.location_text}.")

    from .schema import open_now
    on = open_now(r.opening_hours)
    if on is True:
        reasons.append("Open right now according to the listed hours.")
    elif on is False:
        trade.append("Listed hours say it is closed right now.")
    elif r.opening_hours:
        trade.append("Opening hours are listed but could not be interpreted; check before going.")
    else:
        trade.append("Opening hours not listed — open status unverified.")
    if p.occasion and r.occasions:
        (reasons if p.occasion in r.occasions else trade).append(
            f"{'Tagged as suited to' if p.occasion in r.occasions else 'Not tagged for'} a {nice(p.occasion)} outing (demo tags).")
    return reasons, trade


def rank(items: list[Restaurant], p: Prefs, top_n: int) -> list[dict[str, Any]]:
    out = []
    for r in items:
        s, parts = score(r, p)
        reasons, trade = explain(r, p)
        card = r.to_dict()
        card.update(score=s, score_breakdown=parts, reasons=reasons, trade_offs=trade,
                    estimated_total=estimated_total(r, p), budget_status=budget_status(r, p))
        out.append(card)
    out.sort(key=lambda c: (-c["score"], c["distance_km"] if c["distance_km"] is not None else 99, c["name"]))
    return out[:top_n]
