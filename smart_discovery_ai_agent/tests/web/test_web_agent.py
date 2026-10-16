import httpx
import pytest

from conftest import new_client, register
from discovery.agent import run_agent, run_llm, run_rule_based, unsupported_numbers
from discovery.llm import AnthropicClient, LLMError
from discovery.prefs import Prefs, PrefError, calculate_group_budget, prefs_from_args
from discovery.providers.demo import DemoProvider
from discovery.tools import TOOL_SPECS, ToolContext, execute_tool
from helpers import FakeAnthropic, text_reply, tool_use


@pytest.fixture
def ctx(osm_provider):
    return ToolContext(osm=osm_provider, demo=DemoProvider())


def llm(script):
    fake = FakeAnthropic(script)
    return AnthropicClient("sk-test", "claude-test", client=httpx.Client(transport=httpx.MockTransport(fake.handler))), fake


# ---- preference extraction ---------------------------------------------------
@pytest.mark.parametrize("text,expect", [
    ("Find a good restaurant in Baner, Pune for dinner under ₹1,000 for two",
     {"location_text": "Baner, Pune", "budget_amount": 1000, "budget_type": "total", "party_size": 2}),
    ("Find a restaurant for four people with a maximum budget of ₹2,000 in Kothrud, Pune",
     {"budget_amount": 2000, "party_size": 4}),
    ("vegetarian restaurants with outdoor seating in Koregaon Park",
     {"diet": "vegetarian", "amenities": ["outdoor_seating"], "location_text": "Koregaon Park, Pune"}),
    ("Show affordable cafés with Wi-Fi where I can work in Aundh, Pune",
     {"kind": "cafe", "amenities": ["wifi"], "occasion": "work"}),
    ("I want a quiet café with good coffee and a budget of ₹500 in Baner, Pune", {"kind": "cafe", "budget_amount": 500}),
    ("north indian near me", {"near_me": True, "cuisines": ["north_indian"]}),
    ("budget 600 per person for 3 in Baner, Pune", {"budget_amount": 600, "budget_type": "per_person", "party_size": 3}),
])
def test_rule_based_preference_extraction(ctx, text, expect):
    ctx.user_location = (18.56, 73.78)
    run_rule_based(ctx, text)
    got = ctx.prefs.to_dict()
    for k, v in expect.items():
        assert got[k] == v, (k, got)


def test_per_person_vs_total_budget_arithmetic():
    assert calculate_group_budget(500, "per_person", 4) == {"type": "per_person", "party_size": 4, "party_size_assumed": False, "per_person": 500, "total": 2000}
    assert calculate_group_budget(2000, "total", 4)["per_person"] == 500
    assert calculate_group_budget(900, "total", None)["party_size_assumed"] is True


@pytest.mark.parametrize("args", [{"budget_amount": -1}, {"budget_amount": "abc"}, {"party_size": 0}, {"party_size": 500},
                                  {"diet": "keto"}, {"budget_type": "weekly"}, {"min_rating": 9}, {"radius_km": 99},
                                  {"amenities": ["jacuzzi"]}, {"lat": 10}, {"cuisines": 5}])
def test_invalid_preferences_rejected(args):
    with pytest.raises(PrefError):
        prefs_from_args(args)


def test_missing_preferences_trigger_clarification(ctx):
    res = run_rule_based(ctx, "find me a good place to eat")
    assert res.status == "needs_clarification" and "Which area" in res.message and not res.cards


def test_non_restaurant_and_invalid_text(ctx):
    assert run_rule_based(ctx, "what is the capital of France").status == "not_restaurant"
    assert run_rule_based(ctx, "   ").status == "invalid_input"
    assert run_rule_based(ctx, "x" * 600).status == "invalid_input"


# ---- tools execute for real ----------------------------------------------------
def test_tool_pipeline_executes_and_traces(ctx):
    execute_tool(ctx, "extract_restaurant_preferences", {"location_text": "Baner, Pune", "cuisines": ["north_indian"], "diet": "vegetarian"})
    assert execute_tool(ctx, "search_restaurants", {})["found"] == 6
    assert execute_tool(ctx, "filter_by_cuisine", {"cuisines": ["north_indian"]})["after"] < 6
    assert execute_tool(ctx, "filter_by_dietary_preferences", {"diet": "vegetarian"})["after"] >= 1
    ranked = execute_tool(ctx, "rank_restaurants", {"top_n": 3})
    assert ranked["ranked"][0]["name"] == "Spice Route" and ranked["ranked"][0]["reasons"]
    assert [t["tool"] for t in ctx.trace] == ["extract_restaurant_preferences", "search_restaurants", "filter_by_cuisine",
                                              "filter_by_dietary_preferences", "rank_restaurants"]
    assert execute_tool(ctx, "compare_restaurants", {"restaurant_ids": [c["id"] for c in ctx.ranking[:2]]})["differences"]
    assert execute_tool(ctx, "get_restaurant_details", {"restaurant_id": ctx.ranking[0]["id"]})["restaurant"]["name"] == "Spice Route"
    assert execute_tool(ctx, "check_opening_hours", {})["open_now"] >= 1


def test_tools_never_raise_on_bad_input(ctx):
    assert "error" in execute_tool(ctx, "no_such_tool", {})
    assert "error" in execute_tool(ctx, "filter_by_budget", {"budget_amount": -3})
    assert "error" in execute_tool(ctx, "filter_by_budget", {"bogus": 1})
    assert "error" in execute_tool(ctx, "compare_restaurants", {"restaurant_ids": ["only-one"]})
    assert "error" in execute_tool(ctx, "get_restaurant_details", {"restaurant_id": "osm:node/404"})
    assert "error" in execute_tool(ctx, "search_restaurants", {})  # no location yet
    assert "error" in execute_tool(ctx, "filter_by_location", {"max_km": 99})
    assert "error" in execute_tool(ctx, "search_restaurants", "not-a-dict")


def test_tool_specs_cover_required_tools():
    names = {t["name"] for t in TOOL_SPECS}
    assert {"extract_restaurant_preferences", "search_restaurants", "get_restaurant_details", "filter_by_budget", "filter_by_cuisine",
            "filter_by_location", "filter_by_dietary_preferences", "rank_restaurants", "compare_restaurants", "check_opening_hours",
            "calculate_group_budget", "retrieve_conversation_context"} <= names
    assert all(t["input_schema"]["type"] == "object" for t in TOOL_SPECS)


def test_explanations_reflect_real_attributes_only(ctx):
    run_rule_based(ctx, "vegetarian restaurants with outdoor seating in Baner, Pune")
    top = ctx.ranking[0]
    assert top["name"] == "Green Leaf"
    assert any("outdoor seating" in r for r in top["reasons"]) and any("vegetarian" in r.lower() for r in top["reasons"])
    assert any("No rating" in t for t in top["trade_offs"])  # missing data stated, not hidden


# ---- LLM tool-use loop ------------------------------------------------------------
def test_llm_loop_calls_real_tools_and_returns_cards(ctx):
    client, fake = llm([
        tool_use("extract_restaurant_preferences", {"location_text": "Baner, Pune", "cuisines": ["north_indian"]}, "t1"),
        tool_use("search_restaurants", {}, "t2"),
        tool_use("filter_by_cuisine", {"cuisines": ["north_indian"]}, "t3"),
        tool_use("rank_restaurants", {"top_n": 3}, "t4"),
        text_reply("Spice Route is the best match: it lists north indian cuisine."),
    ])
    res = run_llm(ctx, client, [], "north indian in Baner, Pune")
    assert res.mode == "llm" and res.status == "ok" and res.cards[0]["name"] == "Spice Route"
    assert res.message.startswith("Spice Route is the best match")
    assert [t["tool"] for t in res.trace] == ["extract_restaurant_preferences", "search_restaurants", "filter_by_cuisine", "rank_restaurants"]
    # tool results were fed back to the model
    last = fake.requests[-1]["messages"][-1]["content"][0]
    assert last["type"] == "tool_result" and "Spice Route" in last["content"]
    assert fake.requests[0]["tools"] == TOOL_SPECS and "claude-test" == fake.requests[0]["model"]


def test_llm_tool_errors_are_returned_to_the_model_not_raised(ctx):
    client, fake = llm([tool_use("filter_by_budget", {"budget_amount": -5}), text_reply("Sorry, that budget is invalid.")])
    res = run_llm(ctx, client, [], "cheap place in Baner")
    assert res.message == "Sorry, that budget is invalid."
    assert fake.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_llm_asking_a_question_creates_no_cards(ctx):
    client, _ = llm([text_reply("Which area should I search in?")])
    res = run_llm(ctx, client, [], "find me food")
    assert res.cards == [] and res.status == "needs_clarification" and res.message == "Which area should I search in?"


def test_llm_fabricated_numbers_are_replaced_by_template(ctx):
    client, _ = llm([
        tool_use("extract_restaurant_preferences", {"location_text": "Baner, Pune"}, "t1"), tool_use("search_restaurants", {}, "t2"),
        tool_use("rank_restaurants", {"top_n": 2}, "t3"),
        text_reply("Green Leaf is rated 4.8/5 and costs ₹350 per person."),  # none of this is in any tool result
    ])
    res = run_llm(ctx, client, [], "restaurants in Baner, Pune")
    assert "4.8" not in res.message and "₹350" not in res.message and res.message.startswith("I found")
    assert any(t["tool"] == "fact_check" for t in res.trace)


def test_unsupported_numbers_helper():
    assert unsupported_numbers("Costs ₹1,200 total, 4.5/5, 2.5 km away", "1200 4.5 2.5") == []
    assert unsupported_numbers("Costs ₹900", "1200") == ["900"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_llm_http_failure_falls_back_with_reason_and_no_secret_leak(osm_provider, status):
    client, fake = llm([])
    fake.status = status
    ctx = ToolContext(osm=osm_provider)
    res = run_agent(ctx, "cafe in Baner, Pune", [], client)
    assert res.mode == "rule-based" and res.status == "ok" and res.cards
    assert "LLM unavailable" in res.fallback_reason and "sk-test" not in res.fallback_reason
    assert any(t["tool"] == "llm" and not t["ok"] for t in res.trace)


def test_llm_timeout_falls_back(osm_provider):
    def boom(request):
        raise httpx.ReadTimeout("slow")
    client = AnthropicClient("sk-test", "m", client=httpx.Client(transport=httpx.MockTransport(boom)))
    res = run_agent(ToolContext(osm=osm_provider), "cafe in Baner, Pune", [], client)
    assert res.mode == "rule-based" and res.cards and "LLM unavailable" in res.fallback_reason


def test_missing_api_key_reported_honestly(osm_provider):
    res = run_agent(ToolContext(osm=osm_provider), "cafe in Baner, Pune", [], None)
    assert res.mode == "rule-based" and "No LLM API key" in res.fallback_reason
    with pytest.raises(LLMError):
        AnthropicClient("", "m")


def test_llm_garbage_response_falls_back(osm_provider):
    client = AnthropicClient("k", "m", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json"))))
    res = run_agent(ToolContext(osm=osm_provider), "cafe in Baner, Pune", [], client)
    assert res.mode == "rule-based" and res.cards


def test_llm_agent_through_api_persists_ai_response(app_with_llm):
    app, fake = app_with_llm([
        tool_use("extract_restaurant_preferences", {"location_text": "Baner, Pune"}, "t1"), tool_use("search_restaurants", {}, "t2"),
        tool_use("rank_restaurants", {"top_n": 2}, "t3"), text_reply("Here are two options near Baner."),
    ])
    c = new_client(app)
    register(c)
    cid = c.post("/api/conversations").json()["id"]
    r = c.post(f"/api/conversations/{cid}/messages", json={"content": "restaurants in Baner, Pune"}).json()["assistant_message"]
    assert r["content"] == "Here are two options near Baner." and r["payload"]["mode"] == "llm" and r["payload"]["cards"]
    assert c.get("/api/status").json()["llm"]["configured"] is True
    stored = c.get(f"/api/conversations/{cid}/messages").json()["items"][1]
    assert stored["content"] == "Here are two options near Baner." and stored["payload"]["cards"]


def test_conversation_history_is_sent_to_the_model(app_with_llm):
    app, fake = app_with_llm([text_reply("Which area?"), text_reply("Noted, Baner it is.")])
    c = new_client(app)
    register(c)
    cid = c.post("/api/conversations").json()["id"]
    c.post(f"/api/conversations/{cid}/messages", json={"content": "veg food"})
    c.post(f"/api/conversations/{cid}/messages", json={"content": "Baner"})
    second = fake.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"] and second[0]["content"] == "veg food"


def test_status_endpoint_never_leaks_keys(app_with_llm):
    app, _ = app_with_llm([])
    c = new_client(app)
    text = c.get("/api/status").text
    assert "sk-test" not in text and "claude-test" in text


def test_openai_compatible_client_translates_tool_calls(ctx):
    import json as _j
    from discovery.llm import OpenAICompatClient
    seen = []

    def call(cid, name, args):
        return {"choices": [{"message": {"content": None, "tool_calls": [{"id": cid, "type": "function",
                                                                          "function": {"name": name, "arguments": args}}]}}]}
    replies = [call("c1", "extract_restaurant_preferences", _j.dumps({"location_text": "Baner, Pune"})),
               call("c2", "search_restaurants", "{}"), call("c3", "rank_restaurants", '{"top_n": 2}'),
               {"choices": [{"message": {"content": "Two options near Baner."}}]}]

    def handler(request):
        seen.append(_j.loads(request.content))
        assert request.headers["authorization"] == "Bearer free-key"
        return httpx.Response(200, json=replies.pop(0))
    client = OpenAICompatClient("https://llm.test/v1", "free-key", "gemma", client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = run_llm(ctx, client, [], "restaurants in Baner, Pune")
    assert res.mode == "llm" and res.cards and res.message == "Two options near Baner."
    roles = [m["role"] for m in seen[-1]["messages"]]
    assert roles[0] == "system" and "tool" in roles and seen[0]["tools"][0]["type"] == "function"
