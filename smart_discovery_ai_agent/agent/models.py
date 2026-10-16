"""Data structures shared by every stage of the agent.

Keeping these as plain dataclasses makes each stage's input and output
explicit, which is what lets us test the stages independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# Hard constraints (diet, budget) are never relaxed by the agent. Soft
# constraints may be relaxed one at a time, and every relaxation is reported.
DIET_VALUES = ("any", "vegetarian", "vegan", "jain")
OCCASION_VALUES = ("birthday", "date", "business", "family", "friends", "quick_bite")
FEATURE_VALUES = (
    "parking",
    "outdoor_seating",
    "wifi",
    "live_music",
    "wheelchair_access",
    "serves_alcohol",
)


@dataclass
class Preferences:
    """Structured version of what the user asked for."""

    area: Optional[str] = None            # canonical area name, e.g. "Wakad"
    raw_area: Optional[str] = None        # place the user named that we do not cover
    budget_amount: Optional[float] = None
    budget_type: str = "total"            # "total" or "per_person"
    party_size: Optional[int] = None
    cuisines: list[str] = field(default_factory=list)
    diet: str = "any"
    occasion: Optional[str] = None
    min_rating: Optional[float] = None
    features: list[str] = field(default_factory=list)
    is_restaurant_request: bool = True

    @property
    def effective_party_size(self) -> int:
        """Party size used for cost estimates (assumed 2 if not given)."""
        return self.party_size or 2

    @property
    def total_budget(self) -> Optional[float]:
        if self.budget_amount is None:
            return None
        if self.budget_type == "per_person":
            return self.budget_amount * self.effective_party_size
        return self.budget_amount

    def merge(self, newer: "Preferences") -> "Preferences":
        """Combine an earlier turn with a follow-up answer.

        Anything the user states in the newer turn wins; anything they do
        not mention is kept from the earlier turn.
        """
        merged = Preferences(**asdict(self))
        for name, value in asdict(newer).items():
            if name == "is_restaurant_request":
                continue
            if name in ("cuisines", "features"):
                if value:
                    setattr(merged, name, list(value))
            elif name == "diet":
                if value != "any":
                    merged.diet = value
            elif name == "budget_type":
                if newer.budget_amount is not None:
                    merged.budget_type = value
            elif value is not None:
                setattr(merged, name, value)
        if newer.area:
            merged.raw_area = None
        return merged

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total_budget"] = self.total_budget
        data["effective_party_size"] = self.effective_party_size
        return data


@dataclass
class Restaurant:
    id: str
    name: str
    area: str
    cuisines: list[str]
    avg_cost_per_person: int
    rating: float
    veg_only: bool
    vegan_options: Optional[bool]
    jain_options: Optional[bool]
    occasions: list[str]
    features: dict[str, Optional[bool]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Restaurant":
        return cls(
            id=data["id"],
            name=data["name"],
            area=data["area"],
            cuisines=list(data["cuisines"]),
            avg_cost_per_person=int(data["avg_cost_per_person"]),
            rating=float(data["rating"]),
            veg_only=bool(data["veg_only"]),
            vegan_options=data.get("vegan_options"),
            jain_options=data.get("jain_options"),
            occasions=list(data.get("occasions", [])),
            features=dict(data.get("features", {})),
        )


@dataclass
class Recommendation:
    restaurant: Restaurant
    estimated_total: int
    distance_km: Optional[float]
    score: float
    score_breakdown: dict[str, float]
    reasons: list[str]
    trade_offs: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.restaurant.id,
            "name": self.restaurant.name,
            "area": self.restaurant.area,
            "cuisines": self.restaurant.cuisines,
            "rating": self.restaurant.rating,
            "estimated_total": self.estimated_total,
            "distance_km": self.distance_km,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "reasons": self.reasons,
            "trade_offs": self.trade_offs,
        }


@dataclass
class TraceStep:
    """One line of the agent's visible decision trail."""

    stage: str
    detail: str


@dataclass
class AgentResponse:
    # ok | needs_clarification | no_results | invalid_input | unsupported | tool_error
    status: str
    message: str
    preferences: Optional[Preferences] = None
    recommendations: list[Recommendation] = field(default_factory=list)
    relaxations: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    mode: str = "demo"
    question: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "message": self.message,
            "question": self.question,
            "preferences": self.preferences.to_dict() if self.preferences else None,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "relaxations": self.relaxations,
            "assumptions": self.assumptions,
            "trace": [asdict(t) for t in self.trace],
        }
