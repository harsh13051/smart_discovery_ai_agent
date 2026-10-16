"""Shared test doubles: a scriptable fake of the OpenStreetMap services and of the Anthropic API."""
from __future__ import annotations

import json
from typing import Any, Callable

import httpx

OVERPASS_URL = "https://overpass.test/api/interpreter"
OVERPASS_URL_2 = "https://overpass2.test/api/interpreter"
NOMINATIM = "https://nominatim.test"

ELEMENTS = [
    {"type": "node", "id": 1, "lat": 18.5600, "lon": 73.7800, "tags": {
        "name": "Green Leaf", "amenity": "restaurant", "cuisine": "south_indian", "diet:vegetarian": "only",
        "outdoor_seating": "yes", "opening_hours": "24/7", "addr:street": "Baner Road", "addr:suburb": "Baner",
        "website": "https://greenleaf.example", "internet_access": "no"}},
    {"type": "node", "id": 2, "lat": 18.5620, "lon": 73.7810, "tags": {
        "name": "Spice Route", "amenity": "restaurant", "cuisine": "north_indian;mughlai", "diet:vegetarian": "yes",
        "internet_access": "wlan", "opening_hours": "Mo-Su 00:00-23:59"}},
    {"type": "way", "id": 3, "center": {"lat": 18.5700, "lon": 73.7900}, "tags": {
        "name": "Meat Lovers Grill", "amenity": "restaurant", "cuisine": "steak_house", "diet:vegetarian": "no"}},
    {"type": "node", "id": 4, "lat": 18.5605, "lon": 73.7805, "tags": {"name": "Mystery Bistro", "amenity": "restaurant"}},
    {"type": "node", "id": 5, "lat": 18.5610, "lon": 73.7790, "tags": {
        "name": "Cafe Beans", "amenity": "cafe", "cuisine": "coffee_shop", "internet_access": "wlan", "wheelchair": "yes"}},
    {"type": "node", "id": 6, "lat": 18.5601, "lon": 73.7801, "tags": {"amenity": "restaurant"}},  # unnamed: dropped
    {"type": "node", "id": 7, "lat": 18.5640, "lon": 73.7830, "tags": {
        "name": "Wok Express", "amenity": "restaurant", "cuisine": "chinese", "diet:vegan": "yes", "opening_hours": "Mo-Su 00:00-23:59"}},
]
GEOCODE_HIT = [{"lat": "18.5590", "lon": "73.7868", "display_name": "Baner, Pune, Maharashtra, India",
                "boundingbox": ["18.54", "18.58", "73.77", "73.80"]}]


class FakeOSM:
    """Scriptable transport. ``overpass`` / ``nominatim`` modes: ok | timeout | 500 | 429 | empty."""

    def __init__(self) -> None:
        self.overpass = "ok"
        self.nominatim = "ok"
        self.geocode_empty = False
        self.calls: list[str] = []
        self.overpass_hosts_tried: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url.split("?")[0])
        if request.url.host.startswith("overpass"):
            self.overpass_hosts_tried.append(request.url.host)
            mode = self.overpass
            if mode == "first_down":
                mode = "500" if request.url.host == "overpass.test" else "ok"
            return self._respond(mode, {"elements": ELEMENTS})
        if request.url.path.endswith("/search"):
            q = request.url.params.get("q", "")
            if q in ("restaurant", "cafe"):
                if self.nominatim != "ok":
                    return self._respond(self.nominatim, [])
                items = [{"category": "amenity", "type": e["tags"]["amenity"], "name": e["tags"]["name"],
                          "osm_type": e["type"], "osm_id": e["id"], "lat": str(e.get("lat", 18.57)), "lon": str(e.get("lon", 73.79)),
                          "extratags": {k: v for k, v in e["tags"].items() if k not in ("name", "amenity")}, "address": {"suburb": "Baner"}}
                         for e in ELEMENTS if "name" in e["tags"]]
                return httpx.Response(200, json=items)
            return httpx.Response(200, json=[] if self.geocode_empty else GEOCODE_HIT)
        if request.url.path.endswith("/lookup"):
            e = ELEMENTS[0]
            return httpx.Response(200, json=[{"category": "amenity", "type": "restaurant", "name": e["tags"]["name"],
                                              "osm_type": "node", "osm_id": 1, "lat": "18.56", "lon": "73.78",
                                              "extratags": {"cuisine": "south_indian"}, "address": {}}])
        return httpx.Response(404)

    @staticmethod
    def _respond(mode: str, body: Any) -> httpx.Response:
        if mode == "timeout":
            raise httpx.ReadTimeout("timed out")
        if mode == "500":
            return httpx.Response(503, text="busy")
        if mode == "429":
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        if mode == "empty":
            return httpx.Response(200, json={"elements": []})
        return httpx.Response(200, json=body)


class FakeAnthropic:
    """Scripted Anthropic Messages API. ``script`` is a list of responses (dict content blocks) or callables."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "secret echo sk-test"}})
        step = self.script.pop(0)
        step = step(body) if callable(step) else step
        return httpx.Response(200, json=step)


def tool_use(name: str, args: dict, uid: str = "tu_1") -> dict:
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": uid, "name": name, "input": args}]}


def text_reply(text: str) -> dict:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}
