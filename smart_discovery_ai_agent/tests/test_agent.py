"""End-to-end tests of the full agent workflow (demo mode, no network)."""
from agent import ConversationState
from agent.tools import JsonRestaurantSource, diet_ok


def test_tc30_valid_request_gives_ranked_recommendations(agent):
    """TC-30: a complete request returns up to 3 ranked, explained, in-budget options."""
    r = agent.run("veg birthday dinner for 4 in Wakad under 3000")
    assert r.status == "ok" and r.mode == "demo"
    assert 1 <= len(r.recommendations) <= 3
    assert r.recommendations[0].restaurant.name == "Rooftop Rasoi"
    assert all(rec.estimated_total <= 3000 for rec in r.recommendations)
    assert all(rec.restaurant.veg_only for rec in r.recommendations)
    assert all(rec.reasons for rec in r.recommendations)
    scores = [rec.score for rec in r.recommendations]
    assert scores == sorted(scores, reverse=True)


def test_tc31_strict_budget_is_respected(agent):
    """TC-31: with ₹400 for two in Baner, only places costing ≤ ₹400 are returned."""
    r = agent.run("dinner for two in Baner under ₹400")
    assert r.status == "ok"
    assert all(rec.estimated_total <= 400 for rec in r.recommendations)
    assert [rec.restaurant.name for rec in r.recommendations] == ["Wakad Chaat Corner"]


def test_tc32_missing_area_triggers_clarification_then_completes(agent, state):
    """TC-32: missing location and budget are asked for, one question at a time."""
    r1 = agent.run("find me a good place to eat for my family", state)
    assert r1.status == "needs_clarification" and "area" in r1.question.lower()
    r2 = agent.run("Kothrud", state)
    assert r2.status == "needs_clarification" and "budget" in r2.question.lower()
    r3 = agent.run("1500 total, we are 4", state)
    assert r3.status == "ok"
    assert r3.preferences.area == "Kothrud" and r3.preferences.occasion == "family"
    assert r3.preferences.total_budget == 1500 and r3.preferences.party_size == 4


def test_tc33_bare_number_answers_budget_question(agent, state):
    """TC-33: after the agent asks for a budget, a lone '900' is understood as ₹900."""
    assert agent.run("lunch in Aundh", state).status == "needs_clarification"
    r = agent.run("900", state)
    assert r.status == "ok" and r.preferences.total_budget == 900
    assert any("Read the bare number" in t.detail for t in r.trace)


def test_tc34_no_results_is_explained(agent):
    """TC-34: impossible constraints produce 'no_results' with the reason and the closest option."""
    r = agent.run("jain japanese food in Koregaon Park for 6 under 1000")
    assert r.status == "no_results" and not r.recommendations
    assert "over your ₹1,000 budget" in r.message
    assert "jain" in r.message


def test_tc35_invalid_inputs_are_rejected_gracefully(agent):
    """TC-35: empty, negative-budget and oversized-party requests get clear messages, no crash."""
    assert agent.run("").status == "invalid_input"
    neg = agent.run("dinner for 2 in Aundh under -500")
    assert neg.status == "invalid_input" and "positive" in neg.message
    big = agent.run("dinner for 50 people in Baner under 20000")
    assert big.status == "invalid_input" and "Party size" in big.message


def test_tc36_off_topic_request_is_declined(agent):
    """TC-36: non-restaurant requests are declined with guidance."""
    r = agent.run("what's the weather tomorrow")
    assert r.status == "unsupported" and "restaurants" in r.message


def test_tc37_uncovered_city_gets_a_helpful_question(agent):
    """TC-37: an uncovered location is not guessed; the agent lists the covered areas."""
    r = agent.run("pizza in Mumbai under 800 for 2")
    assert r.status == "needs_clarification"
    assert "Mumbai" in r.question and "Baner" in r.question


def test_tc38_relaxation_widens_area_but_keeps_preferences(agent):
    """TC-38: Italian in Akurdi has no local match; the agent widens the area, keeps 'Italian'."""
    r = agent.run("italian date night in Akurdi under 1500")
    assert r.status == "ok"
    assert all("italian" in rec.restaurant.cuisines for rec in r.recommendations)
    assert any("Expanded the search" in x for x in r.relaxations)
    assert not any("Included cuisines" in x for x in r.relaxations)
    assert any(t.stage == "replan" for t in r.trace)


def test_tc39_preference_relaxed_only_when_nothing_matches(agent):
    """TC-39: if nothing meets the feature list, features are dropped and the user is told."""
    r = agent.run("rooftop place with live music and parking in Pimpri for 3, budget 3000, 4.5+ rated")
    assert r.status == "ok"
    assert any("Dropped the requirement" in x for x in r.relaxations)
    assert all(rec.restaurant.rating >= 4.5 for rec in r.recommendations)  # rating kept
    assert all(rec.trade_offs for rec in r.recommendations)


def test_tc40_data_source_failure_is_handled(settings, tmp_path):
    """TC-40: if the data service is down the agent says so instead of crashing or inventing results."""
    from agent import DiscoveryAgent

    broken = DiscoveryAgent(settings, source=JsonRestaurantSource(tmp_path / "offline.json"))
    r = broken.run("dinner for 2 in Baner under 1500")
    assert r.status == "tool_error" and not r.recommendations
    assert "unavailable" in r.message
    assert any(t.stage == "tool_error" for t in r.trace)


def test_tc41_trace_shows_every_workflow_stage(agent):
    """TC-41: the trace records extract, decide, plan, tool, observe, rank and respond."""
    r = agent.run("cafe in Camp, 400 per person for 3 people")
    stages = {t.stage for t in r.trace}
    assert {"extract", "decide", "plan", "tool", "observe", "rank", "respond"} <= stages


def test_tc42_clarification_is_capped(agent):
    """TC-42: after 3 unanswered questions the agent proceeds and states its assumptions."""
    state = ConversationState()
    for _ in range(3):
        assert agent.run("somewhere to eat", state).status == "needs_clarification"
    r = agent.run("anything is fine, just food", state)
    assert r.status == "ok"
    assert any("No area was given" in a for a in r.assumptions)
    assert any("No budget was given" in a for a in r.assumptions)


def test_tc43_diet_is_a_hard_constraint_end_to_end(agent):
    """TC-43: vegan requests never return places without confirmed vegan options."""
    r = agent.run("vegan lunch in Koregaon Park under 2000 for 2")
    assert r.status == "ok"
    assert all(diet_ok(rec.restaurant, "vegan") for rec in r.recommendations)
