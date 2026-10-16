"""Common restaurant schema every provider normalizes into.

Fields a provider does not supply stay ``None`` (or empty) and are shown as
"unavailable" in the UI. Nothing here is ever invented.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

IST = timezone(timedelta(hours=5, minutes=30))  # opening-hours checks assume India

AMENITY_KEYS = (
    "wifi", "outdoor_seating", "parking", "wheelchair", "takeaway", "delivery",
    "dine_in", "air_conditioning", "serves_alcohol", "live_music",
)


@dataclass
class Restaurant:
    id: str                                  # "<provider>:<provider_id>", e.g. "osm:node/123"
    provider: str                            # "osm" | "demo"
    provider_id: str
    name: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    address: Optional[str] = None
    locality: Optional[str] = None
    kind: Optional[str] = None               # restaurant | cafe | fast_food | food_court
    cuisines: list[str] = field(default_factory=list)
    cost_per_person: Optional[int] = None    # INR; only when the provider states it
    price_label: Optional[str] = None
    rating: Optional[float] = None           # only when the provider states it
    review_count: Optional[int] = None
    vegetarian: Optional[str] = None         # "only" | "yes" | "no" | None (unknown)
    vegan: Optional[str] = None
    jain: Optional[str] = None
    amenities: dict[str, Optional[bool]] = field(default_factory=dict)
    occasions: list[str] = field(default_factory=list)  # demo data only
    opening_hours: Optional[str] = None      # raw provider string
    phone: Optional[str] = None
    website: Optional[str] = None
    menu_url: Optional[str] = None
    source_name: str = ""
    source_url: Optional[str] = None
    fetched_at: Optional[str] = None         # ISO timestamp of retrieval
    is_demo: bool = False
    distance_km: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["open_now"] = open_now(self.opening_hours)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Restaurant":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


# ----------------------------------------------------------- opening hours
_DAYS = ["mo", "tu", "we", "th", "fr", "sa", "su"]
_TIME = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def _days(spec: str) -> Optional[set[int]]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip().lower()
        if "-" in part:
            a, _, b = part.partition("-")
            if a not in _DAYS or b not in _DAYS:
                return None
            i, j = _DAYS.index(a), _DAYS.index(b)
            out |= set(range(i, j + 1)) if i <= j else set(range(i, 7)) | set(range(0, j + 1))
        elif part in _DAYS:
            out.add(_DAYS.index(part))
        else:
            return None
    return out


def open_now(hours: Optional[str], now: Optional[datetime] = None) -> Optional[bool]:
    """Evaluate an OSM ``opening_hours`` string for the current moment (IST).

    Supports the common subset (day ranges, one or more time ranges, ``off``,
    ``24/7``, overnight spans). Anything else returns None ("cannot verify").
    """
    if not hours:
        return None
    text = hours.strip()
    if text == "24/7":
        return True
    now = (now or datetime.now(IST)).astimezone(IST)
    minutes, today, yesterday = now.hour * 60 + now.minute, now.weekday(), (now.weekday() - 1) % 7
    day_rules: dict[int, list[tuple[int, int]]] = {}
    for rule in (r.strip() for r in text.split(";") if r.strip()):
        m = re.match(r"^([A-Za-z,\-]+)\s+(.+)$", rule)
        if m:
            days = _days(m.group(1))
            body = m.group(2).strip()
        elif re.match(r"^\d", rule):
            days, body = set(range(7)), rule
        else:
            return None
        if days is None:
            return None
        spans: list[tuple[int, int]] = []
        if body.lower() != "off":
            for piece in body.split(","):
                t = _TIME.match(piece.strip())
                if not t:
                    return None
                spans.append((int(t.group(1)) * 60 + int(t.group(2)), int(t.group(3)) * 60 + int(t.group(4))))
        for d in days:  # later rules override earlier ones (OSM semantics)
            day_rules[d] = spans
    if not day_rules:
        return None
    for start, end in day_rules.get(today, []):
        if (start <= minutes < end) or (end <= start and minutes >= start):
            return True
    for start, end in day_rules.get(yesterday, []):
        if end <= start and minutes < end:  # overnight span from yesterday
            return True
    return False
