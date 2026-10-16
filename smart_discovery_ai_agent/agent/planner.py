"""Stage 2 - Decide what to do next.

The planner is deliberately rule-based (not an LLM) so its decisions are
predictable and testable. It answers three questions:

1. Can we proceed, or must we stop / ask the user something first?
2. Which tool calls make up the first search?
3. If too few restaurants matched, which ONE soft constraint should be
   relaxed next - based on what actually rejected the candidates?

Hard constraints (diet and budget) are never relaxed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from .models import Preferences

MAX_CLARIFICATIONS = 3
NEARBY_RADIUS_KM = 8.0

# Order in which soft constraints are relaxed, cheapest compromise first.
RELAXATION_ORDER = ("nearby_areas", "features", "rating", "cuisine")


@dataclass
class Decision:
    action: str  # "reject" | "unsupported" | "clarify" | "search"
    reason: str
    question: Optional[str] = None
    assumptions: list[str] = field(default_factory=list)


@dataclass
class PlanStep:
    tool: str
    reason: str


def decide(prefs: Preferences, problems: list[str], clarifications_so_far: int, areas: list[str]) -> Decision:
    if problems:
        return Decision("reject", "Input failed validation: " + " ".join(problems))
    if not prefs.is_restaurant_request:
        return Decision("unsupported", "Request is not about finding a place to eat.")

    can_ask = clarifications_so_far < MAX_CLARIFICATIONS
    assumptions: list[str] = []

    if prefs.raw_area and not prefs.area:
        if can_ask:
            return Decision(
                "clarify",
                f"'{prefs.raw_area}' is outside the covered areas.",
                question=(
                    f"I don't have data for '{prefs.raw_area}' yet. Which of these areas works for you? "
                    + ", ".join(areas)
                ),
            )
        assumptions.append(f"'{prefs.raw_area}' is not covered, so all covered areas were searched.")
    elif not prefs.area:
        if can_ask:
            return Decision(
                "clarify",
                "Location is essential for a useful recommendation and was not given.",
                question="Which area of Pune should I search? For example: " + ", ".join(areas[:6]) + ", ...",
            )
        assumptions.append("No area was given, so all covered areas were searched.")

    if prefs.budget_amount is None:
        if can_ask:
            return Decision(
                "clarify",
                "Budget is a hard constraint and was not given.",
                question="What's your budget? You can give a total (e.g. 'under ₹1500') or per person (e.g. '₹400 per person').",
            )
        assumptions.append("No budget was given, so cost was not used as a filter.")

    if prefs.party_size is None:
        assumptions.append("Party size not stated; cost estimates assume 2 people.")
    return Decision("search", "All essential information is available.", assumptions=assumptions)


def initial_plan(prefs: Preferences) -> list[PlanStep]:
    steps = [
        PlanStep("search_restaurants", f"Fetch restaurants in {prefs.area or 'all covered areas'}."),
        PlanStep("estimate_cost", f"Estimate each bill for {prefs.effective_party_size} people incl. 5% tax."),
        PlanStep("filter_candidates", "Apply diet and budget (hard) plus cuisine/rating/feature (soft) constraints."),
    ]
    if prefs.total_budget is not None:
        steps.append(PlanStep("check_budget", f"Confirm each shortlisted bill is within ₹{prefs.total_budget:,.0f}."))
    return steps


def next_relaxation(
    prefs: Preferences,
    relaxed: set[str],
    rejected: Counter,
    found: int,
    wanted: int,
) -> Optional[str]:
    """Pick the next soft constraint to relax, or None to stop.

    Policy:
    * Widening the area keeps every preference intact, so it is allowed
      whenever fewer than `wanted` restaurants match.
    * Changing a stated preference (features, rating, cuisine) is only done
      when NOTHING matches - one good exact match beats three compromises.
    * A preference is only relaxed if it actually rejected candidates in the
      last filtering pass, so compromises are kept to the minimum needed.
    """
    if found >= wanted:
        return None
    for step in RELAXATION_ORDER:
        if step in relaxed:
            continue
        if step != "nearby_areas" and found > 0:
            return None
        if step == "nearby_areas" and prefs.area:
            return step
        if step == "features" and prefs.features and rejected.get("features"):
            return step
        if step == "rating" and prefs.min_rating is not None and rejected.get("rating"):
            return step
        if step == "cuisine" and prefs.cuisines and rejected.get("cuisine"):
            return step
    return None


def describe_relaxation(step: str, prefs: Preferences) -> str:
    return {
        "nearby_areas": f"Expanded the search beyond {prefs.area} to areas within {NEARBY_RADIUS_KM:.0f} km.",
        "features": "Dropped the requirement for: " + ", ".join(f.replace("_", " ") for f in prefs.features) + ".",
        "rating": f"Accepted ratings below your minimum of {prefs.min_rating}.",
        "cuisine": "Included cuisines other than " + ", ".join(c.replace("_", " ") for c in prefs.cuisines) + ".",
    }[step]
