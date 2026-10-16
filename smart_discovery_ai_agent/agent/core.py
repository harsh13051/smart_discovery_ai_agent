"""The DiscoveryAgent: orchestrates every stage and records a visible trace.

    request -> validate -> extract preferences -> check constraints
            -> decide (reject / unsupported / clarify / search)
            -> plan -> tool calls -> filter -> [observe & relax]* -> rank
            -> explain -> write summary -> validate summary -> response
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from config import Settings
from .extractor import (
    AreaVocabulary,
    HybridExtractor,
    InputValidationError,
    LLMExtractor,
    RuleBasedExtractor,
    bare_amount,
    check_constraints,
)
from .llm import LLMClient
from .models import AgentResponse, Preferences, TraceStep
from .planner import NEARBY_RADIUS_KM, decide, describe_relaxation, initial_plan, next_relaxation
from .recommender import rank
from .response_generator import llm_summary, template_summary
from .tools import AreaGraph, JsonRestaurantSource, ToolBox, ToolError, diet_ok

log = logging.getLogger("agent.core")


@dataclass
class ConversationState:
    """Carries partial preferences across clarification turns."""

    prefs: Optional[Preferences] = None
    clarifications: int = 0
    awaiting_answer: bool = False

    def reset(self) -> None:
        self.prefs, self.clarifications, self.awaiting_answer = None, 0, False


@dataclass
class _Run:
    trace: list[TraceStep] = field(default_factory=list)

    def log(self, stage: str, detail: str) -> None:
        self.trace.append(TraceStep(stage, detail))
        log.info("[%s] %s", stage, detail)


class DiscoveryAgent:
    def __init__(
        self,
        settings: Settings,
        llm_client: Optional[LLMClient] = None,
        source: Optional[JsonRestaurantSource] = None,
    ):
        self.settings = settings
        self.graph = AreaGraph(settings.areas_file)
        self.source = source or JsonRestaurantSource(settings.restaurants_file)
        self.vocab = AreaVocabulary(self.graph.areas, self.graph.aliases)
        if llm_client is None and settings.llm_enabled:
            llm_client = LLMClient(settings)
        self.llm = llm_client
        self.extractor = HybridExtractor(
            RuleBasedExtractor(self.vocab),
            LLMExtractor(llm_client, self.vocab) if llm_client else None,
        )

    @property
    def mode(self) -> str:
        return "llm" if self.llm else "demo"

    # ------------------------------------------------------------------ main
    def run(self, text: str, state: Optional[ConversationState] = None) -> AgentResponse:
        state = state or ConversationState()
        run = _Run()
        respond = lambda status, message, **kw: AgentResponse(  # noqa: E731
            status=status, message=message, trace=run.trace, mode=self.mode, **kw
        )

        # 1. Understand ----------------------------------------------------
        try:
            new_prefs = self.extractor.extract(text)
        except InputValidationError as exc:
            run.log("validate_input", f"Rejected: {exc}")
            return respond("invalid_input", str(exc))
        source = self.extractor.last_source
        if self.extractor.last_fallback_reason:
            run.log("extract", f"LLM extraction failed ({self.extractor.last_fallback_reason}); fell back to rule-based extractor.")
        run.log("extract", f"Preferences extracted by {source} extractor: {self._brief(new_prefs)}")

        if state.awaiting_answer and state.prefs is not None:
            if state.prefs.budget_amount is None and new_prefs.budget_amount is None:
                amount = bare_amount(text)
                if amount is not None:
                    new_prefs.budget_amount = amount
                    run.log("extract", f"Read the bare number as the budget the agent asked for: {amount:g}")
            prefs = state.prefs.merge(new_prefs)
            prefs.is_restaurant_request = True
            run.log("merge", f"Merged answer with earlier request: {self._brief(prefs)}")
        else:
            prefs = new_prefs

        # 2. Decide --------------------------------------------------------
        decision = decide(prefs, check_constraints(prefs), state.clarifications, self.graph.areas)
        run.log("decide", f"{decision.action}: {decision.reason}")
        if decision.action == "reject":
            state.reset()
            return respond("invalid_input", decision.reason.replace("Input failed validation: ", ""), preferences=prefs)
        if decision.action == "unsupported":
            state.reset()
            return respond(
                "unsupported",
                "I can only help find restaurants in Pune (e.g. 'veg dinner for 2 in Baner under ₹1200').",
                preferences=prefs,
            )
        if decision.action == "clarify":
            state.prefs, state.awaiting_answer = prefs, True
            state.clarifications += 1
            return respond("needs_clarification", decision.question, question=decision.question, preferences=prefs)

        state.reset()
        try:
            return self._search_and_recommend(prefs, decision.assumptions, run, respond)
        except ToolError as exc:
            run.log("tool_error", str(exc))
            return respond(
                "tool_error",
                "Sorry, the restaurant data service is unavailable right now, so I can't make a "
                "reliable recommendation. Please try again shortly.",
                preferences=prefs,
            )

    # ------------------------------------------------------------ search loop
    def _search_and_recommend(self, prefs, assumptions, run, respond) -> AgentResponse:
        tools = ToolBox(self.source, self.graph, self.settings.tax_rate, on_call=lambda n, d: run.log("tool", f"{n}: {d}"))
        for step in initial_plan(prefs):
            run.log("plan", f"{step.tool} - {step.reason}")

        areas = [prefs.area] if prefs.area else list(self.graph.areas)
        distances = {prefs.area: 0.0} if prefs.area else {}
        candidates = tools.call("search_restaurants", areas=areas)
        run.log("tool", f"search_restaurants({', '.join(areas) if prefs.area else 'all areas'}) -> {len(candidates)} restaurants")
        costs = {r.id: tools.call("estimate_cost", restaurant=r, party_size=prefs.effective_party_size) for r in candidates}
        run.log("tool", f"estimate_cost x{len(candidates)} for party of {prefs.effective_party_size}")

        relaxed: set[str] = set()
        relaxations: list[str] = []
        wanted = self.settings.max_results
        while True:
            kept, rejected = tools.call("filter_candidates", candidates=candidates, prefs=prefs, costs=costs, relaxed=relaxed)
            run.log("observe", f"{len(kept)} match; rejected by " + (", ".join(f"{k}={v}" for k, v in rejected.items()) or "nothing"))
            step = next_relaxation(prefs, relaxed, rejected, len(kept), wanted)
            if step is None:
                break
            relaxed.add(step)
            note = describe_relaxation(step, prefs)
            run.log("replan", f"Too few matches ({len(kept)}/{wanted}). {note}")
            if step == "nearby_areas":
                nearby = tools.call("get_nearby_areas", area=prefs.area, max_km=NEARBY_RADIUS_KM)
                run.log("tool", "get_nearby_areas -> " + (", ".join(f"{a} ({d:.0f} km)" for a, d in nearby) or "none"))
                distances.update(dict(nearby))
                extra = tools.call("search_restaurants", areas=[a for a, _ in nearby])
                run.log("tool", f"search_restaurants(nearby) -> {len(extra)} more restaurants")
                for r in extra:
                    costs[r.id] = tools.call("estimate_cost", restaurant=r, party_size=prefs.effective_party_size)
                candidates = candidates + extra
            relaxations.append(note)

        # Relaxing areas is only reported if it actually contributed results
        if "nearby_areas" in relaxed and not any(r.area != prefs.area for r in kept):
            relaxations = [n for n in relaxations if not n.startswith("Expanded")]

        if not kept:
            return self._no_results(prefs, candidates, costs, rejected, assumptions, run, respond)

        if prefs.total_budget is not None:
            for r in kept:
                check = tools.call("check_budget", estimated_total=costs[r.id], budget=prefs.total_budget)
                if not check["within_budget"]:  # safety net: should never happen
                    raise ToolError(f"Budget check failed for {r.name}")
            run.log("tool", f"check_budget confirmed all {len(kept)} candidates are within ₹{prefs.total_budget:,.0f}")

        recs = rank(kept, prefs, costs, distances, limit=wanted)
        run.log("rank", "; ".join(f"{r.restaurant.name}={r.score}" for r in recs))

        message = template_summary(prefs, recs, relaxations)
        if self.llm:
            text, problems = llm_summary(self.llm, prefs, recs, relaxations, [r.name for r in self.source.load()])
            if text:
                message = text
                run.log("validate_output", "LLM summary passed fact checks; using it.")
            else:
                run.log("validate_output", "LLM summary rejected (" + "; ".join(problems) + "); using template summary.")
        else:
            run.log("respond", "Template summary (demo mode).")
        return respond("ok", message, preferences=prefs, recommendations=recs, relaxations=relaxations, assumptions=assumptions)

    def _no_results(self, prefs, candidates, costs, rejected, assumptions, run, respond) -> AgentResponse:
        blockers = [k for k in ("diet", "budget") if rejected.get(k)]
        parts = ["No restaurant meets all your requirements, even after relaxing the flexible ones."]
        if "budget" in blockers and prefs.total_budget is not None:
            suitable = [r for r in candidates if diet_ok(r, prefs.diet)]
            if suitable:
                cheapest = min(suitable, key=lambda r: costs[r.id])
                parts.append(
                    f"The closest option is {cheapest.name} ({cheapest.area}) at about ₹{costs[cheapest.id]:,}, "
                    f"which is ₹{costs[cheapest.id] - prefs.total_budget:,.0f} over your ₹{prefs.total_budget:,.0f} budget."
                )
        if "diet" in blockers:
            parts.append(f"Few places nearby have confirmed {prefs.diet} options in the data.")
        parts.append("Try a higher budget, a different area, or fewer requirements.")
        run.log("respond", "No results; explained blocking hard constraints: " + (", ".join(blockers) or "none"))
        return respond("no_results", " ".join(parts), preferences=prefs, assumptions=assumptions)

    @staticmethod
    def _brief(p: Preferences) -> str:
        bits = [
            f"area={p.area or p.raw_area}",
            f"budget={p.budget_amount}{'/person' if p.budget_type == 'per_person' else ''}",
            f"party={p.party_size}",
            f"diet={p.diet}",
        ]
        if p.cuisines:
            bits.append("cuisines=" + "/".join(p.cuisines))
        if p.occasion:
            bits.append(f"occasion={p.occasion}")
        if p.min_rating is not None:
            bits.append(f"min_rating={p.min_rating}")
        if p.features:
            bits.append("features=" + "/".join(p.features))
        return ", ".join(bits)
