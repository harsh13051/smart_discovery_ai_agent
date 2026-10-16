"""The agent's tools.

Each tool is a plain Python function with one responsibility. The agent never
touches the data directly; it calls tools through ToolBox.call(), which adds
logging, a retry for data-access failures, and a trace entry, so every action
the agent takes is visible.

| Tool               | Input                          | Output                          |
|--------------------|--------------------------------|---------------------------------|
| search_restaurants | list of areas                  | restaurants in those areas      |
| get_nearby_areas   | area, max_km                   | [(area, km)] sorted by distance |
| estimate_cost      | restaurant, party size         | estimated bill incl. tax (INR)  |
| check_budget       | estimated total, budget        | within budget? + headroom       |
| filter_candidates  | restaurants, prefs, relaxed    | kept list + rejection counts    |
"""
from __future__ import annotations

import heapq
import json
import logging
import math
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .models import Preferences, Restaurant

log = logging.getLogger("agent.tools")


class ToolError(RuntimeError):
    """A tool could not complete (e.g. the data source is unavailable)."""


# ---------------------------------------------------------------- data access
class JsonRestaurantSource:
    """Reads the local sample dataset. Swappable for a live API later."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._cache: Optional[list[Restaurant]] = None

    def load(self) -> list[Restaurant]:
        if self._cache is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._cache = [Restaurant.from_dict(r) for r in raw["restaurants"]]
            except FileNotFoundError as exc:
                raise ToolError(f"Restaurant data file not found: {self.path.name}") from exc
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ToolError(f"Restaurant data file is corrupt: {exc}") from exc
        return self._cache


class AreaGraph:
    def __init__(self, path: Path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        self.areas: list[str] = raw["areas"]
        self.aliases: dict[str, str] = raw.get("aliases", {})
        self.edges: dict[str, list[tuple[str, float]]] = {a: [] for a in self.areas}
        for a, b, km in raw["edges"]:
            self.edges[a].append((b, float(km)))
            self.edges[b].append((a, float(km)))

    def distances_from(self, start: str) -> dict[str, float]:
        """Dijkstra shortest-path distance (km) from start to every area."""
        dist = {start: 0.0}
        queue = [(0.0, start)]
        while queue:
            d, node = heapq.heappop(queue)
            if d > dist.get(node, math.inf):
                continue
            for nxt, km in self.edges.get(node, []):
                nd = d + km
                if nd < dist.get(nxt, math.inf):
                    dist[nxt] = nd
                    heapq.heappush(queue, (nd, nxt))
        return dist


# ---------------------------------------------------------------- the tools
def search_restaurants(source: JsonRestaurantSource, areas: Iterable[str]) -> list[Restaurant]:
    wanted = set(areas)
    return [r for r in source.load() if r.area in wanted]


def get_nearby_areas(graph: AreaGraph, area: str, max_km: float = 8.0) -> list[tuple[str, float]]:
    if area not in graph.edges:
        raise ToolError(f"Unknown area: {area}")
    dist = graph.distances_from(area)
    return sorted(((a, d) for a, d in dist.items() if a != area and d <= max_km), key=lambda x: x[1])


def estimate_cost(restaurant: Restaurant, party_size: int, tax_rate: float = 0.05) -> int:
    """Average cost per person x party size, plus tax, rounded to the nearest 10."""
    if party_size < 1:
        raise ToolError("party_size must be at least 1")
    total = restaurant.avg_cost_per_person * party_size * (1 + tax_rate)
    return int(round(total / 10.0) * 10)


def check_budget(estimated_total: float, budget: Optional[float]) -> dict[str, Any]:
    if budget is None:
        return {"within_budget": True, "headroom": None, "share_of_budget": None}
    return {
        "within_budget": estimated_total <= budget,
        "headroom": round(budget - estimated_total, 2),
        "share_of_budget": round(estimated_total / budget, 3) if budget else None,
    }


def diet_ok(r: Restaurant, diet: str) -> bool:
    """Diet is a hard constraint. "vegetarian" is read strictly as a pure-veg
    restaurant (the common meaning of "veg" in India); unknown (None) values
    for vegan/Jain options count as "not confirmed" and are rejected."""
    if diet == "vegetarian":
        return r.veg_only
    if diet == "vegan":
        return r.vegan_options is True
    if diet == "jain":
        return r.jain_options is True
    return True


def filter_candidates(
    candidates: list[Restaurant],
    prefs: Preferences,
    costs: dict[str, int],
    relaxed: set[str],
) -> tuple[list[Restaurant], Counter]:
    """Apply hard constraints (diet, budget) and un-relaxed soft constraints.

    Returns the kept restaurants and a count of why others were rejected,
    which the planner uses to decide what to relax next.
    """
    kept, rejected = [], Counter()
    budget = prefs.total_budget
    for r in candidates:
        if not diet_ok(r, prefs.diet):
            rejected["diet"] += 1
            continue
        if budget is not None and costs[r.id] > budget:
            rejected["budget"] += 1
            continue
        if prefs.cuisines and "cuisine" not in relaxed and not set(prefs.cuisines) & set(r.cuisines):
            rejected["cuisine"] += 1
            continue
        if prefs.min_rating is not None and "rating" not in relaxed and r.rating < prefs.min_rating:
            rejected["rating"] += 1
            continue
        if prefs.features and "features" not in relaxed and not all(
            r.features.get(f) is True for f in prefs.features
        ):
            rejected["features"] += 1
            continue
        kept.append(r)
    return kept, rejected


# ---------------------------------------------------------------- registry
TOOL_DESCRIPTIONS = {
    "search_restaurants": "Retrieve restaurants located in the given areas from the data source.",
    "get_nearby_areas": "List areas within max_km of an area (shortest road distance).",
    "estimate_cost": "Estimate the bill for the party, including tax.",
    "check_budget": "Check an estimated bill against the user's total budget.",
    "filter_candidates": "Remove restaurants that break hard or active soft constraints.",
}


class ToolBox:
    """Executes tools with logging, one retry on ToolError, and a trace hook."""

    def __init__(
        self,
        source: JsonRestaurantSource,
        graph: AreaGraph,
        tax_rate: float = 0.05,
        on_call: Optional[Callable[[str, str], None]] = None,
        retries: int = 1,
    ):
        self.source = source
        self.graph = graph
        self.tax_rate = tax_rate
        self.on_call = on_call or (lambda name, detail: None)
        self.retries = retries
        self.calls: list[str] = []

    def call(self, name: str, **kwargs: Any) -> Any:
        funcs: dict[str, Callable[..., Any]] = {
            "search_restaurants": lambda areas: search_restaurants(self.source, areas),
            "get_nearby_areas": lambda area, max_km=8.0: get_nearby_areas(self.graph, area, max_km),
            "estimate_cost": lambda restaurant, party_size: estimate_cost(restaurant, party_size, self.tax_rate),
            "check_budget": check_budget,
            "filter_candidates": filter_candidates,
        }
        if name not in funcs:
            raise ToolError(f"Unknown tool: {name}")
        attempt = 0
        while True:
            try:
                result = funcs[name](**kwargs)
                self.calls.append(name)
                log.info("tool %s ok", name)
                return result
            except ToolError as exc:
                attempt += 1
                log.warning("tool %s failed (attempt %d): %s", name, attempt, exc)
                if attempt > self.retries:
                    raise
                self.on_call(name, f"failed ({exc}); retrying once")
