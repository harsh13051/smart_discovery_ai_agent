"""Stage 3 - Rank the candidates and explain each choice.

Scoring is a transparent weighted sum (plain Python, not AI). Each component
is in 0..1, weights add up to 1, and the final score is shown out of 100.
The same components are turned into the "why" and "trade-off" sentences, so
explanations always match the numbers that produced the ranking.
"""
from __future__ import annotations

from typing import Optional

from .models import Preferences, Recommendation, Restaurant

WEIGHTS = {
    "rating": 0.30,     # how well-reviewed the place is
    "occasion": 0.20,   # suits the stated occasion
    "cuisine": 0.15,    # matches a requested cuisine
    "distance": 0.15,   # closeness to the requested area
    "value": 0.10,      # how much budget is left over
    "features": 0.10,   # requested features confirmed
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def _component_scores(r: Restaurant, prefs: Preferences, total: int, km: Optional[float]) -> dict[str, float]:
    budget = prefs.total_budget
    return {
        "rating": max(0.0, min(1.0, (r.rating - 3.0) / 2.0)),
        "value": 0.5 if budget is None else max(0.0, 1.0 - total / budget),
        "cuisine": 0.5 if not prefs.cuisines else float(bool(set(prefs.cuisines) & set(r.cuisines))),
        "occasion": 0.5 if not prefs.occasion else float(prefs.occasion in r.occasions),
        "distance": 0.5 if km is None else max(0.0, 1.0 - km / 10.0),
        "features": 0.5 if not prefs.features else sum(r.features.get(f) is True for f in prefs.features) / len(prefs.features),
    }


def _nice(token: str) -> str:
    return token.replace("_", " ")


def explain(r: Restaurant, prefs: Preferences, total: int, km: Optional[float]) -> tuple[list[str], list[str]]:
    reasons, trade_offs = [], []
    budget = prefs.total_budget

    if budget is not None:
        reasons.append(f"Estimated ₹{total:,} for {prefs.effective_party_size}, leaving ₹{budget - total:,.0f} of your ₹{budget:,.0f} budget.")
        if total / budget > 0.9:
            trade_offs.append("Uses over 90% of your budget, so extras (drinks, dessert) may push you over.")
    else:
        reasons.append(f"Estimated ₹{total:,} for {prefs.effective_party_size}.")

    reasons.append(f"Rated {r.rating}/5 in the sample data.")
    if prefs.min_rating is not None and r.rating < prefs.min_rating:
        trade_offs.append(f"Rating {r.rating} is below your requested minimum of {prefs.min_rating}.")

    matched = sorted(set(prefs.cuisines) & set(r.cuisines))
    if matched:
        reasons.append("Matches your cuisine choice: " + ", ".join(_nice(c) for c in matched) + ".")
    elif prefs.cuisines:
        trade_offs.append("Does not serve " + "/".join(_nice(c) for c in prefs.cuisines) + f"; offers {', '.join(_nice(c) for c in r.cuisines)} instead.")

    if prefs.diet == "vegetarian":
        reasons.append("Pure-vegetarian restaurant.")
    elif prefs.diet in ("vegan", "jain"):
        reasons.append(f"Confirmed {prefs.diet} options.")

    if prefs.occasion:
        if prefs.occasion in r.occasions:
            reasons.append(f"Suited to a {_nice(prefs.occasion)} outing.")
        else:
            trade_offs.append(f"Not specifically tagged as good for a {_nice(prefs.occasion)} outing.")

    if km is None:
        reasons.append(f"Located in {r.area}.")
    elif km == 0:
        reasons.append(f"Located in {r.area}, the area you asked for.")
    else:
        trade_offs.append(f"In {r.area}, about {km:.0f} km from {prefs.area}.")

    for f in prefs.features:
        value = r.features.get(f)
        if value is True:
            reasons.append(f"Has {_nice(f)}.")
        elif value is None:
            trade_offs.append(f"{_nice(f).capitalize()} is not confirmed (missing information).")
        else:
            trade_offs.append(f"No {_nice(f)}.")
    return reasons, trade_offs


def rank(
    candidates: list[Restaurant],
    prefs: Preferences,
    costs: dict[str, int],
    distances: dict[str, float],
    limit: int = 3,
) -> list[Recommendation]:
    recs = []
    for r in candidates:
        km = distances.get(r.area) if prefs.area else None
        parts = _component_scores(r, prefs, costs[r.id], km)
        score = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
        reasons, trade_offs = explain(r, prefs, costs[r.id], km)
        recs.append(
            Recommendation(
                restaurant=r,
                estimated_total=costs[r.id],
                distance_km=None if km is None else round(km, 1),
                score=score,
                score_breakdown={k: round(v, 2) for k, v in parts.items()},
                reasons=reasons,
                trade_offs=trade_offs,
            )
        )
    # Highest score first; ties broken by rating, then lower cost, then name for determinism.
    recs.sort(key=lambda x: (-x.score, -x.restaurant.rating, x.estimated_total, x.restaurant.name))
    return recs[:limit]
