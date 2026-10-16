"""Validated search preferences and the budget arithmetic (plain application logic, not AI)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

DIETS = ("any", "vegetarian", "vegan", "jain")
AMENITIES = ("wifi", "outdoor_seating", "parking", "wheelchair", "takeaway", "delivery",
             "dine_in", "air_conditioning", "serves_alcohol", "live_music")
KINDS = ("restaurant", "cafe", "fast_food")
OCCASIONS = ("birthday", "date", "business", "family", "friends", "quick_bite", "work")
MAX_BUDGET = 1_000_000
MAX_PARTY = 50
MAX_RADIUS_KM = 8.0


class PrefError(ValueError):
    """Invalid preference values (reported back to the user / model)."""


@dataclass
class Prefs:
    location_text: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    near_me: bool = False
    radius_km: float = 2.5
    cuisines: list[str] = field(default_factory=list)
    kind: Optional[str] = None
    diet: str = "any"
    budget_amount: Optional[float] = None
    budget_type: str = "total"          # "total" for the group, or "per_person"
    party_size: Optional[int] = None
    amenities: list[str] = field(default_factory=list)
    occasion: Optional[str] = None
    min_rating: Optional[float] = None
    open_now: bool = False
    use_demo: bool = False

    @property
    def party(self) -> int:
        return self.party_size or 2

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["budget"] = budget_summary(self)
        return d


def budget_summary(p: Prefs) -> Optional[dict[str, Any]]:
    """Distinguish per-person from group budgets explicitly."""
    if p.budget_amount is None:
        return None
    if p.budget_type == "per_person":
        per, total = p.budget_amount, p.budget_amount * p.party
    else:
        total, per = p.budget_amount, p.budget_amount / p.party
    return {
        "type": p.budget_type, "party_size": p.party, "party_size_assumed": p.party_size is None,
        "per_person": round(per, 2), "total": round(total, 2),
    }


def calculate_group_budget(budget_amount: float, budget_type: str, party_size: Optional[int]) -> dict[str, Any]:
    p = validate(Prefs(budget_amount=budget_amount, budget_type=budget_type, party_size=party_size))
    return budget_summary(p) or {}


def validate(p: Prefs) -> Prefs:
    """Raise PrefError on invalid values; normalise the rest in place."""
    if p.location_text is not None:
        p.location_text = p.location_text.strip()[:200] or None
    for name, lo, hi in (("lat", -90, 90), ("lon", -180, 180)):
        v = getattr(p, name)
        if v is not None and not (isinstance(v, (int, float)) and lo <= v <= hi):
            raise PrefError(f"{name} must be a number between {lo} and {hi}.")
    if (p.lat is None) != (p.lon is None):
        raise PrefError("Provide both lat and lon, or neither.")
    if not isinstance(p.radius_km, (int, float)) or not (0.2 <= p.radius_km <= MAX_RADIUS_KM):
        raise PrefError(f"radius_km must be between 0.2 and {MAX_RADIUS_KM}.")
    if p.diet not in DIETS:
        raise PrefError(f"diet must be one of {', '.join(DIETS)}.")
    if p.budget_type not in ("total", "per_person"):
        raise PrefError("budget_type must be 'total' (whole group) or 'per_person'.")
    if p.budget_amount is not None:
        if not isinstance(p.budget_amount, (int, float)) or p.budget_amount <= 0:
            raise PrefError("The budget must be a positive amount in rupees.")
        if p.budget_amount > MAX_BUDGET:
            raise PrefError(f"A budget above ₹{MAX_BUDGET:,} is outside this app's range.")
    if p.party_size is not None and (not isinstance(p.party_size, int) or not (1 <= p.party_size <= MAX_PARTY)):
        raise PrefError(f"party_size must be a whole number between 1 and {MAX_PARTY}.")
    if p.min_rating is not None and not (isinstance(p.min_rating, (int, float)) and 0 <= p.min_rating <= 5):
        raise PrefError("min_rating must be between 0 and 5.")
    if p.kind is not None and p.kind not in KINDS:
        raise PrefError(f"kind must be one of {', '.join(KINDS)}.")
    bad = [a for a in p.amenities if a not in AMENITIES]
    if bad:
        raise PrefError(f"Unknown amenities {bad}; allowed: {', '.join(AMENITIES)}.")
    p.cuisines = list(dict.fromkeys(c.strip().lower().replace(" ", "_") for c in p.cuisines if c and c.strip()))[:6]
    p.amenities = list(dict.fromkeys(p.amenities))
    if p.occasion is not None and p.occasion not in OCCASIONS:
        p.occasion = None
    return p


def prefs_from_args(args: dict[str, Any], base: Optional[Prefs] = None) -> Prefs:
    """Build validated Prefs from untrusted (LLM or HTTP) arguments, layered over ``base``."""
    p = Prefs(**asdict(base)) if base else Prefs()
    allowed = set(Prefs.__dataclass_fields__)  # type: ignore[attr-defined]
    for k, v in args.items():
        if k in allowed and v is not None:
            setattr(p, k, v)
    for k in ("cuisines", "amenities"):
        v = getattr(p, k)
        if isinstance(v, str):
            setattr(p, k, [x for x in v.split(",") if x.strip()])
        elif not isinstance(v, list):
            raise PrefError(f"{k} must be a list.")
    for k in ("budget_amount", "min_rating", "radius_km", "lat", "lon"):
        v = getattr(p, k)
        if isinstance(v, str):
            try:
                setattr(p, k, float(v))
            except ValueError as exc:
                raise PrefError(f"{k} must be a number.") from exc
    if isinstance(p.party_size, (str, float)):
        try:
            p.party_size = int(p.party_size)
        except ValueError as exc:
            raise PrefError("party_size must be a whole number.") from exc
    return validate(p)
