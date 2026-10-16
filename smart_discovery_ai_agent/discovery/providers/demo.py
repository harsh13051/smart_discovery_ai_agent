"""Demo provider: the fictional sample restaurants shipped with the original project.

Every record is flagged ``is_demo=True`` and labelled "DEMO DATA" everywhere it is
shown. It exists so budget/rating/diet logic can be exercised when live data has
no prices, and as an offline fallback the *user must opt into*.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from agent.extractor import AreaVocabulary
from agent.tools import AreaGraph
from config import DATA_DIR

from ..db import utcnow
from ..schema import Restaurant

FEATURE_MAP = {
    "parking": "parking", "outdoor_seating": "outdoor_seating", "wifi": "wifi",
    "live_music": "live_music", "wheelchair_access": "wheelchair", "serves_alcohol": "serves_alcohol",
}
SOURCE_NAME = "DEMO DATA (fictional sample restaurants)"


class DemoProvider:
    name = "demo"

    def __init__(self, restaurants_file: Path = DATA_DIR / "restaurants.json", areas_file: Path = DATA_DIR / "areas.json"):
        self.graph = AreaGraph(areas_file)
        self.vocab = AreaVocabulary(self.graph.areas, self.graph.aliases)
        raw = json.loads(Path(restaurants_file).read_text(encoding="utf-8"))["restaurants"]
        self._items = [self._normalize(r) for r in raw]

    @staticmethod
    def _normalize(r: dict) -> Restaurant:
        fetched = utcnow().isoformat()
        features = {FEATURE_MAP[k]: v for k, v in (r.get("features") or {}).items() if k in FEATURE_MAP and v is not None}
        return Restaurant(
            id=f"demo:{r['id']}", provider="demo", provider_id=r["id"], name=r["name"], locality=r["area"],
            address=f"{r['area']}, Pune (fictional)", kind="restaurant", cuisines=list(r["cuisines"]),
            cost_per_person=int(r["avg_cost_per_person"]), price_label="Sample average per person",
            rating=float(r["rating"]), review_count=None,
            vegetarian="only" if r.get("veg_only") else None,
            vegan="yes" if r.get("vegan_options") else ("no" if r.get("vegan_options") is False else None),
            jain="yes" if r.get("jain_options") else ("no" if r.get("jain_options") is False else None),
            amenities=features, occasions=list(r.get("occasions", [])),
            source_name=SOURCE_NAME, source_url=None, fetched_at=fetched, is_demo=True,
        )

    def resolve_area(self, text: str) -> Optional[str]:
        return self.vocab.normalise(text) or self.vocab.find_in_text(text.lower())

    def search(self, area: str, include_nearby_km: float = 6.0) -> list[Restaurant]:
        dist = self.graph.distances_from(area)
        out = []
        for r in self._items:
            km = dist.get(r.locality or "")
            if km is not None and km <= include_nearby_km:
                copy = Restaurant.from_dict(r.to_dict())
                copy.distance_km = round(km, 1)
                out.append(copy)
        out.sort(key=lambda r: r.distance_km or 0)
        return out

    def get(self, provider_id: str) -> Optional[Restaurant]:
        return next((r for r in self._items if r.provider_id == provider_id), None)
