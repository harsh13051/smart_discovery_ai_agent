"""Stage 4 - Write the final answer, then validate it.

The template writer is deterministic and always available. When an LLM is
configured, it may rewrite the summary in friendlier language, but its text
is checked before use: it must not mention restaurants outside the shortlist,
and every rupee amount it quotes must come from our own numbers. If the check
fails, the agent silently uses the template instead.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from .llm import LLMClient, LLMError
from .models import Preferences, Recommendation

log = logging.getLogger("agent.response")


def template_summary(prefs: Preferences, recs: list[Recommendation], relaxations: list[str]) -> str:
    if not recs:
        return "I couldn't find a restaurant that meets your requirements."
    top = recs[0]
    where = prefs.area or "the covered areas"
    budget = f" within ₹{prefs.total_budget:,.0f}" if prefs.total_budget is not None else ""
    lines = [
        f"I found {len(recs)} option{'s' if len(recs) > 1 else ''} near {where}{budget} "
        f"for {prefs.effective_party_size} people. My top pick is {top.restaurant.name} "
        f"({top.restaurant.area}, rated {top.restaurant.rating}, about ₹{top.estimated_total:,})."
    ]
    if relaxations:
        lines.append("To find enough options I had to compromise: " + " ".join(relaxations))
    return " ".join(lines)


def format_recommendations(recs: list[Recommendation]) -> str:
    out = []
    for i, rec in enumerate(recs, 1):
        r = rec.restaurant
        out.append(
            f"{i}. {r.name} - {r.area} | {', '.join(c.replace('_', ' ') for c in r.cuisines)} | "
            f"rating {r.rating} | est. ₹{rec.estimated_total:,} | score {rec.score}/100"
        )
        out.extend(f"     + {reason}" for reason in rec.reasons)
        out.extend(f"     - {t}" for t in rec.trade_offs)
    return "\n".join(out)


def validate_summary(text: str, recs: list[Recommendation], all_names: Iterable[str], budget: Optional[float]) -> list[str]:
    """Return a list of problems found in an LLM-written summary."""
    problems = []
    if not text or len(text) > 1200:
        problems.append("empty or overly long summary")
    shortlisted = {rec.restaurant.name for rec in recs}
    for name in all_names:
        if name not in shortlisted and name.lower() in text.lower():
            problems.append(f"mentions non-shortlisted restaurant '{name}'")
    allowed = {rec.estimated_total for rec in recs}
    allowed |= {rec.restaurant.avg_cost_per_person for rec in recs}
    if budget is not None:
        allowed.add(int(round(budget)))
    for match in re.findall(r"(?:₹|rs\.?\s?|inr\s?)(\d[\d,]*)", text, flags=re.I):
        if int(match.replace(",", "")) not in allowed:
            problems.append(f"quotes an unverified amount ₹{match}")
    return problems


SUMMARY_SYSTEM = (
    "You write a short, friendly summary (max 90 words) of restaurant recommendations. "
    "Use ONLY the facts in the JSON you are given. Do not invent restaurants, prices, "
    "ratings or features. Mention every compromise listed under 'relaxations'."
)


def llm_summary(
    client: LLMClient,
    prefs: Preferences,
    recs: list[Recommendation],
    relaxations: list[str],
    all_names: Iterable[str],
) -> tuple[Optional[str], list[str]]:
    import json

    payload = {
        "request": prefs.to_dict(),
        "relaxations": relaxations,
        "recommendations": [r.to_dict() for r in recs],
    }
    try:
        text = client.complete(SUMMARY_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=300)
    except LLMError as exc:
        return None, [f"LLM call failed: {exc}"]
    problems = validate_summary(text, recs, all_names, prefs.total_budget)
    return (None, problems) if problems else (text, [])
