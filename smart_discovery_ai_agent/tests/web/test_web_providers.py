from datetime import datetime

import httpx
import pytest

from discovery.providers.cache import MemoryCache
from discovery.providers.demo import DemoProvider
from discovery.providers.osm import (
    GeocodeError, OSMProvider, ProviderUnavailable, normalize_cuisines, normalize_osm,
)
from discovery.schema import IST, open_now
from helpers import ELEMENTS


def provider(web_settings, fake_osm, attempts=2):
    client = httpx.Client(transport=httpx.MockTransport(fake_osm.handler))
    return OSMProvider(web_settings, MemoryCache(), client=client, sleep=lambda s: None, attempts=attempts)


# ---- normalization ----------------------------------------------------------
def test_normalization_maps_tags_and_never_invents_fields():
    r = normalize_osm(ELEMENTS[0]["tags"], "node", 1, 18.56, 73.78, "2026-01-01T00:00:00+00:00")
    assert r.id == "osm:node/1" and r.cuisines == ["south_indian"] and r.vegetarian == "only"
    assert r.amenities == {"outdoor_seating": True, "wifi": False}
    assert r.rating is None and r.review_count is None and r.cost_per_person is None  # OSM has none of these
    assert r.source_url == "https://www.openstreetmap.org/node/1" and r.address == "Baner Road, Baner"


def test_missing_attributes_stay_empty():
    r = normalize_osm(ELEMENTS[3]["tags"], "node", 4, 18.5, 73.7, "t")
    assert r.cuisines == [] and r.amenities == {} and r.opening_hours is None and r.vegetarian is None and r.address is None


def test_unnamed_places_are_dropped_and_cuisine_aliases_normalized():
    assert normalize_osm({"amenity": "restaurant"}, "node", 9, 1, 1, "t") is None
    assert normalize_cuisines("Coffee_Shop; Punjabi", "cafe") == ["cafe", "north_indian"]


def test_open_now_parser():
    mon_noon = datetime(2026, 10, 12, 12, 0, tzinfo=IST)   # Monday
    sun_late = datetime(2026, 10, 18, 23, 0, tzinfo=IST)  # Sunday
    assert open_now("Mo-Fr 09:00-18:00", mon_noon) is True
    assert open_now("Mo-Fr 09:00-11:00", mon_noon) is False
    assert open_now("Mo-Su 11:00-15:00,19:00-23:30", sun_late) is True
    assert open_now("Mo-Sa 10:00-22:00; Su off", sun_late) is False
    assert open_now("Fr-Sa 18:00-02:00", datetime(2026, 10, 10, 1, 0, tzinfo=IST)) is True  # Saturday 01:00: overnight from Friday
    assert open_now("24/7", mon_noon) is True
    assert open_now("sunrise-sunset", mon_noon) is None and open_now(None, mon_noon) is None and open_now("PH off", mon_noon) is None


# ---- geocoding + search -----------------------------------------------------
def test_geocode_success_and_not_found(web_settings, fake_osm):
    p = provider(web_settings, fake_osm)
    g = p.geocode("Baner, Pune")
    assert round(g.lat, 3) == 18.559 and "Baner" in g.label
    fake_osm.geocode_empty = True
    with pytest.raises(GeocodeError):
        provider(web_settings, fake_osm).geocode("Atlantis")


def test_geocode_invalid_input(web_settings, fake_osm):
    with pytest.raises(GeocodeError):
        provider(web_settings, fake_osm).geocode("   ")
    with pytest.raises(GeocodeError):
        provider(web_settings, fake_osm).geocode("x" * 300)


def test_search_success_returns_normalized_sorted_by_distance(web_settings, fake_osm):
    items, meta = provider(web_settings, fake_osm).search(18.559, 73.7868)
    assert [r.name for r in items][0] in {"Green Leaf", "Mystery Bistro", "Cafe Beans"}
    assert len(items) == 6 and all(r.distance_km is not None for r in items)
    assert [r.distance_km for r in items] == sorted(r.distance_km for r in items)
    assert meta.provider == "osm" and meta.endpoint.startswith("overpass:") and not meta.fallback


def test_search_empty_results(web_settings, fake_osm):
    fake_osm.overpass = "empty"
    items, meta = provider(web_settings, fake_osm).search(18.559, 73.7868)
    assert items == [] and not meta.fallback


def test_search_caches_results(web_settings, fake_osm):
    p = provider(web_settings, fake_osm)
    p.search(18.559, 73.7868)
    n = len(fake_osm.calls)
    _, meta = p.search(18.559, 73.7868)
    assert meta.from_cache and len(fake_osm.calls) == n


def test_failover_to_second_overpass_mirror(web_settings, fake_osm):
    fake_osm.overpass = "first_down"
    items, meta = provider(web_settings, fake_osm).search(18.559, 73.7868)
    assert items and meta.endpoint == "overpass:overpass2.test"
    assert fake_osm.overpass_hosts_tried == ["overpass.test", "overpass2.test"]


@pytest.mark.parametrize("mode", ["timeout", "500", "429"])
def test_overpass_failures_fall_back_to_nominatim_with_warning(web_settings, fake_osm, mode):
    fake_osm.overpass = mode
    items, meta = provider(web_settings, fake_osm).search(18.559, 73.7868)
    assert meta.fallback and meta.endpoint == "nominatim-fallback" and meta.warnings
    assert items and all(r.provider == "osm" for r in items)


def test_circuit_breaker_skips_overpass_after_total_failure(web_settings, fake_osm):
    fake_osm.overpass = "500"
    p = provider(web_settings, fake_osm)
    p.search(18.559, 73.7868)
    tried = len(fake_osm.overpass_hosts_tried)
    p.search(18.60, 73.80)  # different cache key, Overpass still considered down
    assert len(fake_osm.overpass_hosts_tried) == tried


def test_all_providers_down_raises_unavailable(web_settings, fake_osm):
    fake_osm.overpass = "timeout"
    fake_osm.nominatim = "timeout"
    with pytest.raises(ProviderUnavailable):
        provider(web_settings, fake_osm).search(18.559, 73.7868)


def test_rate_limit_is_retried_then_succeeds(web_settings):
    state = {"n": 0}

    def handler(request):
        if request.url.host.startswith("overpass"):
            state["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "0"}) if state["n"] == 1 else httpx.Response(200, json={"elements": ELEMENTS})
        return httpx.Response(404)
    p = OSMProvider(web_settings, MemoryCache(), client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    # one attempt per mirror: the first mirror is rate limited, the second answers
    items, meta = p.search(18.559, 73.7868)
    assert items and state["n"] == 2 and not meta.fallback


def test_requests_carry_identifying_user_agent(web_settings, fake_osm):
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: (seen.append(r.headers["user-agent"]), fake_osm.handler(r))[1]),
                          headers={"User-Agent": web_settings.user_agent})
    OSMProvider(web_settings, MemoryCache(), client=client, sleep=lambda s: None).search(18.559, 73.7868)
    assert seen and "tests@example.com" in seen[0]


def test_demo_provider_records_are_always_flagged():
    d = DemoProvider()
    items = d.search("Wakad")
    assert items and all(r.is_demo and "DEMO" in r.source_name and r.source_url is None for r in items)
    assert d.resolve_area("hinjawadi") == "Hinjewadi" and d.resolve_area("Tokyo") is None
