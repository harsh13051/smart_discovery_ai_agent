"""OpenStreetMap provider: Nominatim (geocoding + fallback POI search) and Overpass (POI search).

What OSM supplies: name, coordinates, address parts, cuisine, opening_hours,
diet tags, a few amenities, website/phone/menu links - when volunteers mapped
them. What it does NOT supply: ratings, review counts, prices, photos. Those
fields therefore stay empty; the app never fills them in.

Usage-policy notes: every request carries an identifying User-Agent, results
are cached, Nominatim calls are spaced >= 1.1 s apart and used sparingly.
Data (c) OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

import httpx

from ..db import utcnow
from ..schema import Restaurant
from .cache import cache_key

log = logging.getLogger("discovery.osm")

POI_KINDS = ("restaurant", "cafe", "fast_food", "food_court")
SOURCE_NAME = "OpenStreetMap contributors"


class ProviderError(RuntimeError):
    """Base error for provider problems that callers should report honestly."""


class ProviderUnavailable(ProviderError):
    """All endpoints failed (timeout, 5xx, rate limiting, network)."""


class ProviderRateLimited(ProviderUnavailable):
    pass


class GeocodeError(ProviderError):
    """The place name could not be resolved."""


@dataclass
class GeoPoint:
    lat: float
    lon: float
    label: str
    bbox: Optional[tuple[float, float, float, float]] = None  # south, north, west, east


@dataclass
class SearchMeta:
    provider: str
    endpoint: str
    fetched_at: str
    fallback: bool = False
    from_cache: bool = False
    warnings: Optional[list[str]] = None


# ------------------------------------------------------------- normalization
CUISINE_ALIASES = {
    "indian": "indian", "north_indian": "north_indian", "punjabi": "north_indian", "mughlai": "mughlai",
    "south_indian": "south_indian", "udupi": "south_indian", "regional": "regional",
    "coffee_shop": "cafe", "coffee": "cafe", "cafe": "cafe", "tea": "cafe", "bakery": "desserts",
    "ice_cream": "desserts", "dessert": "desserts", "chinese": "chinese", "asian": "pan_asian",
    "thai": "pan_asian", "japanese": "japanese", "sushi": "japanese", "pizza": "italian",
    "pasta": "italian", "italian": "italian", "burger": "burgers", "american": "american",
    "mexican": "mexican", "seafood": "seafood", "fish": "seafood", "biryani": "biryani",
    "kebab": "mughlai", "maharashtrian": "maharashtrian", "gujarati": "gujarati", "continental": "continental",
    "european": "continental", "street_food": "street_food", "sandwich": "sandwiches", "vegetarian": "vegetarian",
    "arab": "arabic", "lebanese": "arabic", "chicken": "chicken", "barbecue": "barbecue",
}
# A generic "indian" tag is a broad (not exact) match for these requested cuisines.
BROAD_PARENTS = {
    "north_indian": "indian", "south_indian": "indian", "mughlai": "indian", "maharashtrian": "indian",
    "gujarati": "indian", "biryani": "indian", "street_food": "indian",
}


def normalize_cuisines(tag: Optional[str], kind: Optional[str]) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"[;,]", tag or ""):
        key = raw.strip().lower().replace(" ", "_").replace("-", "_")
        if key:
            out.append(CUISINE_ALIASES.get(key, key))
    if kind == "cafe" and "cafe" not in out:
        out.append("cafe")
    return list(dict.fromkeys(out))


def _yn(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("yes", "only", "true", "wlan", "wifi", "customers", "limited"):
        return True
    if v in ("no", "false"):
        return False
    return None


def _diet(tags: dict[str, str], key: str) -> Optional[str]:
    v = (tags.get(f"diet:{key}") or "").strip().lower()
    return v if v in ("only", "yes", "no") else None


def _address(tags: dict[str, str]) -> tuple[Optional[str], Optional[str]]:
    parts = [tags.get("addr:housenumber"), tags.get("addr:street")]
    line1 = " ".join(p for p in parts if p) or None
    locality = tags.get("addr:suburb") or tags.get("addr:neighbourhood") or tags.get("addr:city")
    pieces = [p for p in (line1, tags.get("addr:suburb"), tags.get("addr:city"), tags.get("addr:postcode")) if p]
    return (", ".join(pieces) or None), locality


def normalize_osm(tags: dict[str, str], osm_type: str, osm_id: int, lat: Optional[float], lon: Optional[float],
                  fetched_at: str) -> Optional[Restaurant]:
    name = (tags.get("name") or tags.get("name:en") or "").strip()
    if not name:
        return None  # unnamed POIs are not useful to show
    kind = tags.get("amenity")
    address, locality = _address(tags)
    amenities = {
        "wifi": _yn(tags.get("internet_access") or tags.get("wifi")),
        "outdoor_seating": _yn(tags.get("outdoor_seating")),
        "parking": _yn(tags.get("parking")) if tags.get("parking") in ("yes", "no") else None,
        "wheelchair": _yn(tags.get("wheelchair")),
        "takeaway": _yn(tags.get("takeaway")),
        "delivery": _yn(tags.get("delivery")),
        "dine_in": _yn(tags.get("indoor_seating")),
        "air_conditioning": _yn(tags.get("air_conditioning")),
        "serves_alcohol": _yn(tags.get("bar")) if tags.get("bar") else None,
        "live_music": _yn(tags.get("live_music")),
    }
    pid = f"{osm_type}/{osm_id}"
    return Restaurant(
        id=f"osm:{pid}", provider="osm", provider_id=pid, name=name, lat=lat, lon=lon,
        address=address, locality=locality, kind=kind,
        cuisines=normalize_cuisines(tags.get("cuisine"), kind),
        vegetarian=_diet(tags, "vegetarian"), vegan=_diet(tags, "vegan"), jain=_diet(tags, "jain"),
        amenities={k: v for k, v in amenities.items() if v is not None},
        opening_hours=tags.get("opening_hours"),
        phone=tags.get("phone") or tags.get("contact:phone") or tags.get("mobile"),
        website=tags.get("website") or tags.get("contact:website"),
        menu_url=tags.get("website:menu") or tags.get("menu:url"),
        source_name=SOURCE_NAME, source_url=f"https://www.openstreetmap.org/{pid}",
        fetched_at=fetched_at,
    )


# ------------------------------------------------------------------ provider
class OSMProvider:
    name = "osm"

    def __init__(self, settings, cache, client: Optional[httpx.Client] = None,  # noqa: ANN001
                 sleep: Callable[[float], None] = time.sleep, attempts: int = 2):
        self.s = settings
        self.cache = cache
        self.client = client or httpx.Client(
            timeout=settings.provider_timeout_s, headers={"User-Agent": settings.user_agent},
        )
        self._sleep = sleep
        self.attempts = attempts
        self._last_nominatim = 0.0
        self._overpass_down_until = 0.0   # circuit breaker: skip Overpass briefly after total failure

    # ---- http with retries -------------------------------------------------
    def _request(self, method: str, url: str, attempts: Optional[int] = None, **kw: Any) -> httpx.Response:
        last: Exception = ProviderUnavailable("no attempt made")
        attempts = attempts or self.attempts
        for attempt in range(attempts):
            try:
                resp = self.client.request(method, url, **kw)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = ProviderUnavailable(f"{type(exc).__name__} contacting {httpx.URL(url).host}")
            else:
                if resp.status_code == 429:
                    wait = min(float(resp.headers.get("Retry-After", "1") or 1), 5.0)
                    last = ProviderRateLimited(f"rate limited by {httpx.URL(url).host}")
                    if attempt + 1 < attempts:
                        self._sleep(wait)
                    continue
                if resp.status_code >= 500:
                    last = ProviderUnavailable(f"HTTP {resp.status_code} from {httpx.URL(url).host}")
                elif resp.status_code >= 400:
                    raise ProviderError(f"HTTP {resp.status_code} from {httpx.URL(url).host}")
                else:
                    return resp
            if attempt + 1 < attempts:
                self._sleep(0.5 * (2 ** attempt))
        raise last

    def _nominatim_get(self, path: str, params: dict[str, Any]) -> Any:
        gap = 1.1 - (time.monotonic() - self._last_nominatim)
        if gap > 0:
            self._sleep(gap)
        self._last_nominatim = time.monotonic()
        resp = self._request("GET", self.s.nominatim_url.rstrip("/") + path, params=params)
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderUnavailable("Nominatim returned invalid JSON") from exc

    # ---- geocoding ---------------------------------------------------------
    def geocode(self, query: str) -> GeoPoint:
        query = query.strip()
        if not query or len(query) > 200:
            raise GeocodeError("Please give a place name (up to 200 characters).")
        key = cache_key("geocode", q=query.lower())
        hit = self.cache.get(key, 30 * 86400)
        if hit is None:
            data = self._nominatim_get(
                "/search", {"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "in"})
            hit = data[0] if data else {}
            if hit:
                self.cache.set(key, hit)
        if not hit:
            raise GeocodeError(f"I couldn't find a place called '{query}' in OpenStreetMap.")
        bb = hit.get("boundingbox")
        return GeoPoint(float(hit["lat"]), float(hit["lon"]), hit.get("display_name", query),
                        tuple(float(x) for x in bb) if bb else None)  # type: ignore[arg-type]

    # ---- POI search --------------------------------------------------------
    def search(self, lat: float, lon: float, radius_m: int = 2500, limit: int = 150
               ) -> tuple[list[Restaurant], SearchMeta]:
        radius_m = max(200, min(int(radius_m), 8000))
        key = cache_key("search", lat=round(lat, 3), lon=round(lon, 3), r=radius_m)
        cached = self.cache.get(key, self.s.cache_ttl_s)
        if cached:
            items = [Restaurant.from_dict(d) for d in cached["items"]]
            return items[:limit], SearchMeta(**{**cached["meta"], "from_cache": True})

        query = (
            f'[out:json][timeout:25];nwr["amenity"~"^({"|".join(POI_KINDS)})$"]'
            f'["name"](around:{radius_m},{lat},{lon});out center tags {limit * 2};'
        )
        errors: list[str] = []
        urls = () if time.monotonic() < self._overpass_down_until else self.s.overpass_urls
        if not urls:
            errors.append("Overpass skipped (recent failures)")
        for url in urls:
            try:
                # one attempt per mirror (the next mirror is the retry) with a short timeout
                resp = self._request("POST", url, attempts=1, data={"data": query},
                                     timeout=min(self.s.provider_timeout_s, 8.0))
                payload = resp.json()
                fetched = utcnow().isoformat()
                items = self._from_overpass(payload.get("elements", []), fetched)
                meta = SearchMeta("osm", f"overpass:{httpx.URL(url).host}", fetched, warnings=[])
                break
            except ProviderUnavailable as exc:
                errors.append(str(exc))
            except (ValueError, KeyError) as exc:
                errors.append(f"bad response from {httpx.URL(url).host}: {exc}")
            except ProviderError as exc:
                errors.append(str(exc))
        else:
            if urls:
                self._overpass_down_until = time.monotonic() + 120
            items, meta = self._nominatim_fallback(lat, lon, radius_m, errors)

        items = self._attach_distance(items, lat, lon, radius_m)[:limit]
        self.cache.set(key, {"items": [_plain(r) for r in items],
                             "meta": _meta_dict(meta)})
        return items, meta

    @staticmethod
    def _from_overpass(elements: list[dict], fetched: str) -> list[Restaurant]:
        out = []
        for el in elements:
            lat = el.get("lat") or (el.get("center") or {}).get("lat")
            lon = el.get("lon") or (el.get("center") or {}).get("lon")
            r = normalize_osm(el.get("tags") or {}, el.get("type", "node"), int(el["id"]), lat, lon, fetched)
            if r:
                out.append(r)
        return out

    def _nominatim_fallback(self, lat: float, lon: float, radius_m: int, errors: list[str]
                            ) -> tuple[list[Restaurant], SearchMeta]:
        d = radius_m / 111_000.0
        viewbox = f"{lon - d * 1.1},{lat + d},{lon + d * 1.1},{lat - d}"  # left,top,right,bottom
        fetched = utcnow().isoformat()
        items: dict[str, Restaurant] = {}
        failures = 0
        for term in ("restaurant", "cafe"):
            try:
                data = self._nominatim_get("/search", {
                    "q": term, "format": "jsonv2", "limit": 40, "extratags": 1, "addressdetails": 1,
                    "viewbox": viewbox, "bounded": 1, "countrycodes": "in"})
            except ProviderError as exc:
                failures += 1
                errors.append(str(exc))
                continue
            for h in data:
                if h.get("category") != "amenity" or h.get("type") not in POI_KINDS:
                    continue
                tags = dict(h.get("extratags") or {})
                tags["name"] = h.get("name") or ""
                tags["amenity"] = h.get("type")
                addr = h.get("address") or {}
                for src, dst in (("road", "addr:street"), ("suburb", "addr:suburb"), ("city", "addr:city"),
                                 ("postcode", "addr:postcode"), ("house_number", "addr:housenumber")):
                    if addr.get(src):
                        tags.setdefault(dst, addr[src])
                r = normalize_osm(tags, h.get("osm_type", "node"), int(h["osm_id"]),
                                  float(h["lat"]), float(h["lon"]), fetched)
                if r:
                    items[r.id] = r
        if failures == 2:
            raise ProviderUnavailable("OpenStreetMap services are unavailable (" + "; ".join(errors[-3:]) + ")")
        meta = SearchMeta("osm", "nominatim-fallback", fetched, fallback=True, warnings=[
            "Overpass was unavailable, so this shortlist came from the smaller Nominatim search "
            "(max 40 results per category); some places may be missing."])
        return list(items.values()), meta

    @staticmethod
    def _attach_distance(items: list[Restaurant], lat: float, lon: float, radius_m: int) -> list[Restaurant]:
        from ..schema import haversine_km
        for r in items:
            if r.lat is not None and r.lon is not None:
                r.distance_km = round(haversine_km(lat, lon, r.lat, r.lon), 2)
        items.sort(key=lambda r: (r.distance_km is None, r.distance_km or 0))
        return items

    # ---- lookup of a single object (detail refresh) -------------------------
    def lookup(self, provider_id: str) -> Optional[Restaurant]:
        m = re.fullmatch(r"(node|way|relation)/(\d+)", provider_id)
        if not m:
            raise ProviderError("Invalid OpenStreetMap id.")
        prefix = {"node": "N", "way": "W", "relation": "R"}[m.group(1)]
        data = self._nominatim_get("/lookup", {
            "osm_ids": f"{prefix}{m.group(2)}", "format": "jsonv2", "extratags": 1, "addressdetails": 1})
        if not data:
            return None
        h = data[0]
        tags = dict(h.get("extratags") or {})
        tags["name"], tags["amenity"] = h.get("name") or "", h.get("type")
        addr = h.get("address") or {}
        for src, dst in (("road", "addr:street"), ("suburb", "addr:suburb"), ("city", "addr:city"),
                         ("postcode", "addr:postcode"), ("house_number", "addr:housenumber")):
            if addr.get(src):
                tags.setdefault(dst, addr[src])
        return normalize_osm(tags, h.get("osm_type", m.group(1)), int(h["osm_id"]),
                             float(h["lat"]), float(h["lon"]), utcnow().isoformat())


def _plain(r: Restaurant) -> dict[str, Any]:
    from dataclasses import asdict
    return asdict(r)


def _meta_dict(m: SearchMeta) -> dict[str, Any]:
    return {"provider": m.provider, "endpoint": m.endpoint, "fetched_at": m.fetched_at,
            "fallback": m.fallback, "from_cache": False, "warnings": m.warnings or []}
