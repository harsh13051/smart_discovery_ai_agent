from conftest import new_client, register


def search(c, **params):
    return c.get("/api/restaurants/search", params=params)


def test_search_returns_ranked_cards_with_source_and_freshness(user_client):
    r = search(user_client, location="Baner, Pune")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["cards"]
    c = body["cards"][0]
    assert c["source_url"].startswith("https://www.openstreetmap.org/") and c["fetched_at"] and c["is_demo"] is False
    assert c["rating"] is None and c["cost_per_person"] is None  # never fabricated
    assert body["source"]["provider"] == "OpenStreetMap" and body["source"]["fetched_at"]


def test_cuisine_filter_and_ranking_prefer_relevance_over_distance(user_client):
    cards = search(user_client, location="Baner, Pune", cuisines="north_indian").json()["cards"]
    names = [c["name"] for c in cards]
    assert names[0] == "Spice Route"  # exact cuisine match first
    assert "Wok Express" not in names and "Green Leaf" not in names  # known different cuisine removed
    assert "Mystery Bistro" in names  # unknown cuisine kept but flagged
    mystery = next(c for c in cards if c["name"] == "Mystery Bistro")
    assert any("unverified" in t for t in mystery["trade_offs"])
    assert cards[0]["score"] > mystery["score"]


def test_dietary_filter_removes_explicit_conflicts_and_flags_unknown(user_client):
    cards = search(user_client, location="Baner, Pune", diet="vegetarian").json()["cards"]
    names = [c["name"] for c in cards]
    assert "Meat Lovers Grill" not in names
    assert names.index("Green Leaf") < names.index("Mystery Bistro")  # confirmed outranks unverified
    vegan = search(user_client, location="Baner, Pune", diet="vegan").json()["cards"]
    assert vegan[0]["name"] == "Wok Express"


def test_amenity_filter_wifi(user_client):
    cards = search(user_client, location="Baner, Pune", amenities="wifi").json()["cards"]
    names = [c["name"] for c in cards]
    assert "Green Leaf" not in names  # internet_access=no is an explicit conflict
    assert names[0] in {"Spice Route", "Cafe Beans"}


def test_budget_without_prices_is_flagged_unverified_not_faked(user_client):
    body = search(user_client, location="Baner, Pune", budget=1000, budget_type="per_person", party=4).json()
    assert body["cards"] and all(c["budget_status"] == "unverified" and c["estimated_total"] is None for c in body["cards"])
    assert any("no listed price" in n for n in body["notes"])
    assert "₹1,000/person × 4 people = ₹4,000 total" in " ".join(body["cards"][0]["trade_offs"])


def test_budget_filter_against_demo_data_distinguishes_per_person_and_total(user_client):
    total = search(user_client, location="Wakad", demo_data="true", budget=1200, budget_type="total", party=4).json()
    per_person = search(user_client, location="Wakad", demo_data="true", budget=1200, budget_type="per_person", party=4).json()
    assert all(c["is_demo"] and c["estimated_total"] <= 1200 for c in total["cards"])
    assert all(c["estimated_total"] <= 4800 for c in per_person["cards"])
    assert len(per_person["cards"]) > len(total["cards"])  # 1200 each for 4 people is a much bigger budget
    assert all(c["cost_per_person"] * 4 == c["estimated_total"] for c in total["cards"])


def test_no_match_explains_constraints_and_offers_labelled_alternatives(user_client):
    body = search(user_client, location="Wakad", demo_data="true", budget=100, budget_type="total", party=4).json()
    assert body["status"] == "no_results" and body["alternatives"] is True
    assert "closest alternatives" in body["message"] and "budget" in body["message"]


def test_no_restaurants_found(user_client, fake_osm):
    fake_osm.overpass = "empty"
    body = search(user_client, location="Baner, Pune").json()
    assert body["status"] == "no_results" and body["cards"] == []


def test_invalid_inputs(user_client):
    assert search(user_client).status_code == 422                                    # no location
    assert search(user_client, location="x", budget=-5).status_code == 422
    assert search(user_client, location="x", party=0).status_code == 422
    assert search(user_client, location="x", diet="carnivore").status_code == 422
    assert search(user_client, location="x", amenities="jacuzzi").status_code == 422
    assert search(user_client, lat=500, lon=0).status_code == 422
    assert search(user_client, location="Atlantis_nowhere", **{}).status_code in (200, 422)


def test_unknown_place_is_a_clean_422(user_client, fake_osm):
    fake_osm.geocode_empty = True
    r = search(user_client, location="Atlantis")
    assert r.status_code == 422 and "couldn't find" in r.json()["detail"]


def test_provider_outage_gives_503_not_a_crash(user_client, fake_osm):
    fake_osm.overpass = "timeout"
    fake_osm.nominatim = "500"
    r = search(user_client, location="Baner, Pune")
    assert r.status_code == 503 and "unavailable" in r.json()["detail"].lower()


def test_demo_data_never_mixed_into_live_results(user_client):
    live = search(user_client, location="Baner, Pune").json()["cards"]
    assert all(c["provider"] == "osm" and not c["is_demo"] for c in live)
    demo = search(user_client, location="Baner", demo_data="true").json()
    assert demo["cards"] and all(c["is_demo"] for c in demo["cards"]) and "DEMO" in demo["source"]["provider"]


def test_pagination(user_client):
    p1 = search(user_client, location="Baner, Pune", page_size=2, page=1).json()
    p2 = search(user_client, location="Baner, Pune", page_size=2, page=2).json()
    assert len(p1["cards"]) == 2 and p1["has_more"] is True
    assert {c["id"] for c in p1["cards"]}.isdisjoint({c["id"] for c in p2["cards"]})


def test_detail_endpoint_and_unknown_id(user_client):
    cid = search(user_client, location="Baner, Pune").json()["cards"][0]["id"]
    d = user_client.get("/api/restaurant", params={"id": cid})
    assert d.status_code == 200 and d.json()["restaurant"]["id"] == cid and d.json()["restaurant"]["is_favourite"] is False
    assert user_client.get("/api/restaurant", params={"id": "osm:node/999999"}).status_code in (404, 200)
    assert user_client.get("/api/restaurant", params={"id": "demo:NOPE"}).status_code == 404


def test_favourites_lifecycle_and_persistence(user_client, app):
    rid = search(user_client, location="Baner, Pune").json()["cards"][0]["id"]
    r = user_client.post("/api/favourites", json={"restaurant_id": rid})
    assert r.status_code == 201 and r.json()["restaurant"]["name"] and r.json()["stale"] is False
    assert user_client.post("/api/favourites", json={"restaurant_id": rid}).status_code in (200, 201)  # idempotent
    assert len(user_client.get("/api/favourites").json()["items"]) == 1
    assert user_client.get("/api/restaurant", params={"id": rid}).json()["restaurant"]["is_favourite"] is True
    user_client.post("/api/auth/logout")
    again = new_client(app)
    again.post("/api/auth/login", json={"email": "asha@example.com", "password": "dinner2026x"})
    items = again.get("/api/favourites").json()["items"]
    assert len(items) == 1 and items[0]["restaurant"]["source_url"]
    assert again.delete("/api/favourites", params={"id": rid}).status_code == 200
    assert again.get("/api/favourites").json()["items"] == []
    assert again.delete("/api/favourites", params={"id": rid}).status_code == 404


def test_favourites_are_isolated_between_users(user_client, second_client):
    rid = search(user_client, location="Baner, Pune").json()["cards"][0]["id"]
    user_client.post("/api/favourites", json={"restaurant_id": rid})
    assert second_client.get("/api/favourites").json()["items"] == []
    assert second_client.delete("/api/favourites", params={"id": rid}).status_code == 404
    assert len(user_client.get("/api/favourites").json()["items"]) == 1


def test_stale_favourites_are_marked(user_client, app):
    from datetime import timedelta
    from sqlalchemy import select
    from discovery.db import Favourite, utcnow
    rid = search(user_client, location="Baner, Pune").json()["cards"][0]["id"]
    user_client.post("/api/favourites", json={"restaurant_id": rid})
    with app.state.sf() as db:
        f = db.scalar(select(Favourite))
        f.created_at = utcnow() - timedelta(days=45)
        db.commit()
    assert user_client.get("/api/favourites").json()["items"][0]["stale"] is True


def test_favourite_unknown_restaurant_rejected(user_client):
    assert user_client.post("/api/favourites", json={"restaurant_id": "demo:NOPE"}).status_code == 404
    assert user_client.post("/api/favourites", json={"restaurant_id": "x"}).status_code == 422


def test_compare_uses_only_available_attributes(user_client):
    cards = search(user_client, location="Baner, Pune").json()["cards"]
    ids = [c["id"] for c in cards[:3]]
    r = user_client.post("/api/compare", json={"ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert len(body["restaurants"]) == 3
    assert any("Price is unavailable" in d for d in body["differences"]) and any("Rating is unavailable" in d for d in body["differences"])
    assert not any("Lowest listed cost" in d for d in body["differences"])  # nothing invented to fill the gap


def test_compare_with_prices_from_demo_data(user_client):
    cards = search(user_client, location="Wakad", demo_data="true").json()["cards"][:3]
    body = user_client.post("/api/compare", json={"ids": [c["id"] for c in cards]}).json()
    assert any("Lowest listed cost" in d and "demo data" in d for d in body["differences"])


def test_compare_validation(user_client):
    assert user_client.post("/api/compare", json={"ids": ["osm:node/1"]}).status_code == 422
    assert user_client.post("/api/compare", json={"ids": ["osm:node/1"] * 6}).status_code == 422
    assert user_client.post("/api/compare", json={"ids": ["demo:A", "demo:B"]}).status_code == 404
