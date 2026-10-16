"""The discovery agent.

LLM mode:   Claude decides which tools to call (tool-use loop); tools do the real work.
Fallback:   if no key is configured or the LLM fails, a rule-based extractor feeds the
            *same* tools in a fixed order. Responses state which mode produced them.

What is model behaviour vs application logic:
* model:   understanding the request, choosing tools/arguments, asking questions, wording the summary
* code:    validation, provider calls, filtering, budget maths, ranking, cards, fact-checking numbers
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.extractor import (
    AreaVocabulary, InputValidationError, RuleBasedExtractor, bare_amount, validate_text,
)
from agent.tools import AreaGraph
from config import DATA_DIR

from .llm import AnthropicClient, LLMError
from .prefs import Prefs, PrefError
from .tools import TOOL_SPECS, ToolContext, execute_tool

log = logging.getLogger("discovery.agent")
MAX_TOOL_ROUNDS = 10

SYSTEM_PROMPT = """You are the AI dining assistant inside a restaurant discovery app. Help users find restaurants \
that fit their request using the provided tools.

Rules:
- Facts about restaurants (names, ratings, prices, hours, amenities) must come from tool results. Never invent them. \
If a tool says a field is unavailable, say it is unavailable.
- For any restaurant request: call extract_restaurant_preferences first (only values the user stated), then \
search_restaurants, then the filters that match the stated preferences, check_opening_hours, and rank_restaurants. \
For comparisons use compare_restaurants.
- If no location is given and the user has not shared theirs, ask one short clarifying question instead of guessing. \
Use retrieve_conversation_context for follow-ups ("cheaper", "what about vegan?").
- Budgets: keep per-person and total (group) budgets distinct; ask or state your assumption if party size is unknown. \
Costs are estimates from a listed average, not the final bill.
- OpenStreetMap data has no prices or ratings; say so plainly when the user's budget or rating request cannot be verified.
- Data marked DEMO is fictional; always say so when presenting it.
- The app renders restaurant cards from rank_restaurants automatically, so in your reply give a brief summary \
(2-5 sentences): what matched, notable trade-offs, and what could not be verified. Do not paste long lists. \
Do not claim reservations or availability. Do not reveal internal reasoning."""


@dataclass
class AgentResult:
    status: str                      # ok | needs_clarification | no_results | provider_error | invalid_input | not_restaurant
    message: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    comparison: Optional[dict[str, Any]] = None
    preferences: Optional[dict[str, Any]] = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    mode: str = "rule-based"         # llm | rule-based
    fallback_reason: Optional[str] = None
    alternatives: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# ------------------------------------------------------------ shared pipeline
def run_pipeline(ctx: ToolContext, top_n: int = 5) -> AgentResult:
    """Deterministic search → filter → rank over the preferences in ``ctx.prefs``."""
    p = ctx.prefs
    res = execute_tool(ctx, "search_restaurants", {})
    if "error" in res:
        status = "provider_error" if res.get("retryable") else "invalid_input"
        msg = res["error"]
        if res.get("retryable"):
            msg += (" You can retry in a moment" + (", or switch on demo data to explore the app with clearly "
                                                    "labelled fictional restaurants." if ctx.demo else "."))
        return AgentResult(status, msg, preferences=p.to_dict(), trace=ctx.trace)
    if p.cuisines:
        execute_tool(ctx, "filter_by_cuisine", {"cuisines": p.cuisines})
    if p.diet != "any":
        execute_tool(ctx, "filter_by_dietary_preferences", {"diet": p.diet})
    if p.amenities:
        execute_tool(ctx, "filter_by_amenities", {"amenities": p.amenities})
    if p.min_rating is not None:
        execute_tool(ctx, "filter_by_rating", {"min_rating": p.min_rating})
    if p.budget_amount is not None:
        execute_tool(ctx, "filter_by_budget", {"budget_amount": p.budget_amount, "budget_type": p.budget_type,
                                               "party_size": p.party_size})
    execute_tool(ctx, "check_opening_hours", {"require_open": p.open_now})
    return finish_ranking(ctx, top_n)


def finish_ranking(ctx: ToolContext, top_n: int = 5) -> AgentResult:
    p = ctx.prefs
    source = ctx.search_meta
    if not ctx.kept:
        return no_match_result(ctx, top_n)
    execute_tool(ctx, "rank_restaurants", {"top_n": top_n})
    return AgentResult("ok", template_summary(ctx), cards=ctx.ranking, preferences=p.to_dict(), trace=ctx.trace,
                       source=source, notes=ctx.verification_notes + list(source.get("warnings", [])))


def no_match_result(ctx: ToolContext, top_n: int) -> AgentResult:
    from . import ranking
    p = ctx.prefs
    removed = []
    for t in ctx.trace:
        if t["tool"].startswith("filter_by_") and t["ok"] and "→" in t["summary"]:
            a, b = (int(x) for x in t["summary"].split(" → "))
            if a > b:
                removed.append(f"{t['tool'].replace('filter_by_', '').replace('_', ' ')} removed {a - b}")
    if not ctx.candidates:
        msg = f"No restaurants were found around {ctx.search_meta.get('location', 'that location')}. " \
              "Try a larger radius or a nearby area."
        return AgentResult("no_results", msg, preferences=p.to_dict(), trace=ctx.trace, source=ctx.search_meta,
                           notes=list(ctx.search_meta.get("warnings", [])))
    msg = ("No restaurant meets every requirement" + (f" ({'; '.join(removed)})" if removed else "") +
           ". Here are the closest alternatives from the same area — they do NOT meet all your constraints, "
           "see the trade-offs on each card.")
    alts = ranking.rank(list(ctx.candidates.values()), p, 3)
    return AgentResult("no_results", msg, cards=alts, alternatives=True, preferences=p.to_dict(), trace=ctx.trace,
                       source=ctx.search_meta, notes=list(ctx.search_meta.get("warnings", [])))


def template_summary(ctx: ToolContext) -> str:
    p, top = ctx.prefs, ctx.ranking
    where = ctx.search_meta.get("location", "your area").split(",")[0]
    demo = " (DEMO data — fictional restaurants)" if ctx.search_meta.get("provider", "").startswith("DEMO") else ""
    parts = [f"I found {len(ctx.kept)} places around {where}{demo} and ranked the best {len(top)} for your request."]
    if top:
        parts.append(f"Top match: {top[0]['name']} (score {top[0]['score']}/100).")
    from .prefs import budget_summary
    b = budget_summary(p)
    if b:
        known = sum(1 for c in top if c["estimated_total"] is not None)
        if known == 0:
            parts.append(f"None of these list prices, so I couldn't verify your ₹{b['total']:,.0f} total budget "
                         f"({b['party_size']} people{' — party size assumed' if b['party_size_assumed'] else ''}).")
        else:
            parts.append(f"Cost figures are estimates (listed average × {b['party_size']} people), not the final bill.")
    parts.append("Details marked unverified were not listed by the data source.")
    return " ".join(parts)


# ------------------------------------------------------------ rule-based path
class RuleBasedPlanner:
    STOP = r"(?:under|below|within|for|with|that|which|where|and|budget|upto|up to|max|maximum|please|good|best|nearby|dinner|lunch|breakfast|brunch|tonight|today|around)"
    LOC_RE = re.compile(r"\b(?:in|near|at|around|close to)\s+([A-Za-z][A-Za-z0-9 .'\-]*?(?:,\s*[A-Za-z][A-Za-z .]*?)?)(?=\s+" + STOP + r"\b|[.!?]|$)", re.I)
    NEAR_ME = re.compile(r"\b(near me|nearby|around me|close to me|near here)\b", re.I)
    COMPARE = re.compile(r"\bcompare\b", re.I)

    def __init__(self) -> None:
        graph = AreaGraph(DATA_DIR / "areas.json")
        self.rules = RuleBasedExtractor(AreaVocabulary(graph.areas, graph.aliases))

    def parse(self, text: str, awaiting_location: bool, ctx: ToolContext) -> dict[str, Any]:
        text = validate_text(text)
        legacy = self.rules.extract(text)
        args: dict[str, Any] = {}
        loc_text = None
        m = self.LOC_RE.search(text)
        if m and m.group(1).strip().lower() not in ("me", "my area", "here", "town", "the city"):
            loc_text = m.group(1).strip(" ,")
        known = self.rules.vocab.normalise(loc_text) if loc_text else None
        if known:  # a locality we know is in Pune: disambiguate for the geocoder
            loc_text = f"{known}, Pune"
        if not loc_text and legacy.area:
            loc_text = f"{legacy.area}, Pune"
        if self.NEAR_ME.search(text) and not loc_text:
            args["near_me"] = True
        if not loc_text and not args.get("near_me") and awaiting_location and len(text) <= 80 and not re.search(r"\d", text):
            loc_text = text.strip(" .!?")
        if loc_text:
            args["location_text"] = loc_text
        if legacy.budget_amount is not None:
            args["budget_amount"], args["budget_type"] = legacy.budget_amount, legacy.budget_type
        elif ctx.conversation_context.get("awaiting") == "budget":
            amount = bare_amount(text)
            if amount:
                args["budget_amount"] = amount
        if legacy.party_size:
            args["party_size"] = legacy.party_size
        if legacy.diet != "any":
            args["diet"] = legacy.diet
        if legacy.cuisines:
            args["cuisines"] = legacy.cuisines
        if legacy.features:
            args["amenities"] = ["wheelchair" if f == "wheelchair_access" else f for f in legacy.features]
        if legacy.occasion:
            args["occasion"] = legacy.occasion
        if legacy.min_rating is not None:
            args["min_rating"] = legacy.min_rating
        low = text.lower()
        if "work" in low and ("wifi" in low or "laptop" in low or "cafe" in low or "café" in low):
            args["occasion"] = "work"
        if re.search(r"\b(caf[eé]s?|coffee)\b", low):
            args["kind"] = "cafe"
        if re.search(r"\bopen now|open right now|currently open\b", low):
            args["open_now"] = True
        args["_is_request"] = legacy.is_restaurant_request or bool(loc_text) or awaiting_location
        return args


_planner: Optional[RuleBasedPlanner] = None


def _get_planner() -> RuleBasedPlanner:
    global _planner
    if _planner is None:
        _planner = RuleBasedPlanner()
    return _planner


def run_rule_based(ctx: ToolContext, text: str, fallback_reason: Optional[str] = None) -> AgentResult:
    awaiting_loc = ctx.conversation_context.get("awaiting") == "location"
    try:
        args = _get_planner().parse(text, awaiting_loc, ctx)
    except InputValidationError as exc:
        return AgentResult("invalid_input", str(exc), mode="rule-based", fallback_reason=fallback_reason)
    is_request = args.pop("_is_request")
    if not is_request:
        return AgentResult("not_restaurant", "I can help you find restaurants — tell me a place, and optionally "
                           "cuisine, budget, party size or diet (e.g. 'vegetarian dinner for 2 in Baner under ₹1,000').",
                           mode="rule-based", fallback_reason=fallback_reason)
    prior = ctx.conversation_context.get("prefs")
    if prior:  # follow-up: layer new values over what we already know
        try:
            from .prefs import prefs_from_args
            ctx.prefs = prefs_from_args(prior)
        except PrefError:
            ctx.prefs = Prefs()
    if not prior and ctx.saved_prefs.get("diet") in ("vegetarian", "vegan", "jain") and "diet" not in args:
        args["diet"] = ctx.saved_prefs["diet"]  # profile default, only when the message doesn't say otherwise
    res = execute_tool(ctx, "extract_restaurant_preferences", args)
    if "error" in res:
        return AgentResult("invalid_input", res["error"], trace=ctx.trace, mode="rule-based", fallback_reason=fallback_reason)
    if "location" in res["missing"] and not ctx.user_location:
        if ctx.saved_prefs.get("default_location") and not prior:
            ctx.prefs.location_text = ctx.saved_prefs["default_location"]
        else:
            return AgentResult("needs_clarification", "Which area or neighbourhood should I search in? "
                               "(For example 'Baner, Pune' — or allow location access and say 'near me'.)",
                               preferences=ctx.prefs.to_dict(), trace=ctx.trace, mode="rule-based",
                               fallback_reason=fallback_reason)
    if ctx.user_location and ctx.prefs.lat is None and not ctx.prefs.location_text:
        ctx.prefs.lat, ctx.prefs.lon = ctx.user_location
    result = run_pipeline(ctx)
    result.mode, result.fallback_reason = "rule-based", fallback_reason
    return result


# ------------------------------------------------------------------ LLM path
def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*\.?\d*", s)}


def unsupported_numbers(text: str, corpus: str) -> list[str]:
    """Numbers (prices, ratings, distances) in the model's prose that appear in no tool result."""
    allowed = _numbers(corpus)
    bad = []
    for m in re.finditer(r"(?:₹\s*|Rs\.?\s*)(\d[\d,]*)|(\d(?:\.\d)?)\s*/\s*5|(\d+(?:\.\d+)?)\s*km", text):
        val = next(g for g in m.groups() if g).replace(",", "")
        if val not in allowed and val.rstrip("0").rstrip(".") not in {a.rstrip("0").rstrip(".") for a in allowed}:
            bad.append(val)
    return bad


def run_llm(ctx: ToolContext, client: AnthropicClient, history: list[dict[str, str]], text: str) -> AgentResult:
    text = validate_text(text)
    system = SYSTEM_PROMPT
    if ctx.saved_prefs:
        system += "\n\nUser's saved profile defaults (use only when the request does not say otherwise): " + json.dumps(ctx.saved_prefs)
    system += "\n\nUser shared their location: " + ("yes" if ctx.user_location else "no")
    messages = [{"role": m["role"], "content": m["content"]} for m in history[-12:] if m["content"].strip()]
    messages.append({"role": "user", "content": text})
    corpus_parts: list[str] = [text]
    final_text = ""
    for _ in range(MAX_TOOL_ROUNDS):
        data = client.create(system, messages, TOOL_SPECS)
        blocks = data["content"]
        messages.append({"role": "assistant", "content": blocks})
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        final_text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        if data.get("stop_reason") != "tool_use" or not uses:
            break
        results = []
        for b in uses:
            out = execute_tool(ctx, b["name"], b.get("input") or {})
            payload = json.dumps(out, default=str)
            corpus_parts.append(payload)
            results.append({"type": "tool_result", "tool_use_id": b["id"], "content": payload[:12000],
                            "is_error": "error" in out})
        messages.append({"role": "user", "content": results})
    else:
        final_text = final_text or ""
    corpus_parts.append(json.dumps(ctx.prefs.to_dict(), default=str))
    corpus_parts.append(json.dumps(ctx.ranking, default=str))

    p = ctx.prefs
    cards, alternatives = ctx.ranking, False
    status = "ok" if cards else "needs_clarification" if not ctx.candidates else "no_results"
    if not ctx.candidates and any(e for e in ctx.errors):
        status = "provider_error"
    notes = ctx.verification_notes + list(ctx.search_meta.get("warnings", []))
    guard = unsupported_numbers(final_text, "\n".join(corpus_parts)) if final_text else []
    if guard:
        ctx.trace.append({"tool": "fact_check", "args": {}, "ok": False, "ms": 0,
                          "summary": f"model text had numbers not in tool results {guard[:3]}; replaced with template"})
        final_text = ""
    if not final_text:
        if cards:
            final_text = template_summary(ctx)
        else:
            final_text = "I couldn't put together a recommendation from that. Could you tell me a place to search?"
    return AgentResult(status, final_text, cards=cards, comparison=ctx.comparison, preferences=p.to_dict(),
                       trace=ctx.trace, source=ctx.search_meta, notes=notes, mode="llm", alternatives=alternatives)


def run_agent(ctx: ToolContext, text: str, history: list[dict[str, str]],
              client: Optional[AnthropicClient]) -> AgentResult:
    """Entry point used by the API: LLM first (if configured), rule-based fallback on any failure."""
    reason: Optional[str] = None
    if client is None:
        reason = "No LLM API key configured; using the rule-based fallback."
    else:
        try:
            return run_llm(ctx, client, history, text)
        except InputValidationError as exc:
            return AgentResult("invalid_input", str(exc), mode="llm")
        except LLMError as exc:
            reason = f"LLM unavailable ({exc}); used the rule-based fallback."
            log.warning("LLM failed: %s", exc)
        ctx.trace.append({"tool": "llm", "args": {}, "ok": False, "summary": reason, "ms": 0})
        # reset working state touched by a partially completed LLM run
        ctx.prefs, ctx.candidates, ctx.kept, ctx.ranking = Prefs(), {}, {}, []
        ctx.verification_notes.clear()
    return run_rule_based(ctx, text, reason)
