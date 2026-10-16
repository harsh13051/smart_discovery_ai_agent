"""Tool tests: data retrieval, distances, cost estimates, filtering, failures."""
import pytest

from agent.models import Preferences
from agent.tools import (
    JsonRestaurantSource,
    ToolBox,
    ToolError,
    check_budget,
    diet_ok,
    estimate_cost,
    filter_candidates,
    get_nearby_areas,
)


def test_tc10_cost_estimate_includes_tax(agent):
    """TC-10: avg ₹650/person x 4 people + 5% tax = ₹2,730."""
    rooftop = next(r for r in agent.source.load() if r.name == "Rooftop Rasoi")
    assert estimate_cost(rooftop, 4, 0.05) == 2730
    with pytest.raises(ToolError):
        estimate_cost(rooftop, 0)


def test_tc11_nearby_areas_use_shortest_path(agent):
    """TC-11: Akurdi->Pimpri is 5 km direct; Wakad is 9 km via Chinchwad, so excluded at 8 km."""
    near = dict(get_nearby_areas(agent.graph, "Akurdi", max_km=8))
    assert near == {"Chinchwad": 3.0, "Pimpri": 5.0}
    assert dict(get_nearby_areas(agent.graph, "Akurdi", max_km=10))["Wakad"] == 9.0


def test_tc12_unknown_area_is_a_tool_error(agent):
    """TC-12: asking for neighbours of an unknown area fails loudly, not silently."""
    with pytest.raises(ToolError):
        get_nearby_areas(agent.graph, "Mumbai")


@pytest.mark.parametrize("diet", ["any", "vegetarian", "vegan", "jain"])
@pytest.mark.parametrize("budget", [300, 800, 1500, 4000])
def test_tc13_hard_constraints_are_never_violated(agent, diet, budget):
    """TC-13: across the whole dataset, filtering never keeps an over-budget or wrong-diet restaurant."""
    everything = agent.source.load()
    prefs = Preferences(budget_amount=budget, party_size=2, diet=diet)
    costs = {r.id: estimate_cost(r, 2) for r in everything}
    kept, _ = filter_candidates(everything, prefs, costs, relaxed={"cuisine", "rating", "features"})
    assert all(costs[r.id] <= budget for r in kept)
    assert all(diet_ok(r, diet) for r in kept)


def test_tc14_check_budget_reports_headroom():
    """TC-14: budget check returns within/over and the remaining amount."""
    assert check_budget(900, 1000) == {"within_budget": True, "headroom": 100, "share_of_budget": 0.9}
    assert check_budget(1100, 1000)["within_budget"] is False


def test_tc15_missing_data_file_retries_then_fails(agent, tmp_path):
    """TC-15: an unavailable data source is retried once, then a ToolError is raised."""
    events = []
    box = ToolBox(JsonRestaurantSource(tmp_path / "missing.json"), agent.graph, on_call=lambda n, d: events.append(d))
    with pytest.raises(ToolError, match="not found"):
        box.call("search_restaurants", areas=["Baner"])
    assert len(events) == 1 and "retrying" in events[0]


def test_tc16_corrupt_data_file_is_detected(tmp_path):
    """TC-16: a corrupt JSON file produces a ToolError, not a crash."""
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ToolError, match="corrupt"):
        JsonRestaurantSource(bad).load()


def test_tc17_unknown_tool_is_rejected(agent):
    """TC-17: the agent can only call registered tools."""
    with pytest.raises(ToolError):
        ToolBox(agent.source, agent.graph).call("delete_everything")
