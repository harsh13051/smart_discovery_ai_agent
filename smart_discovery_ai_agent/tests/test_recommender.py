"""Ranking and explanation tests."""
from agent.models import Preferences
from agent.recommender import WEIGHTS, rank
from agent.tools import estimate_cost


def _rank(agent, prefs, names, distances):
    pool = [r for r in agent.source.load() if r.name in names]
    costs = {r.id: estimate_cost(r, prefs.effective_party_size) for r in pool}
    return rank(pool, prefs, costs, distances, limit=5)


def test_tc20_weights_and_scores_are_well_formed(agent):
    """TC-20: weights sum to 1 and every score is within 0-100."""
    assert abs(sum(WEIGHTS.values()) - 1) < 1e-9
    prefs = Preferences(area="Baner", budget_amount=2000, party_size=2)
    recs = _rank(agent, prefs, {"Little Napoli", "Taco Theory", "Baner Brew Garden"}, {"Baner": 0.0})
    assert all(0 <= r.score <= 100 for r in recs)


def test_tc21_occasion_and_rating_drive_ranking(agent):
    """TC-21: for a birthday, a 4.3-rated birthday venue beats a cheaper 3.9-rated chaat stall."""
    prefs = Preferences(area="Wakad", budget_amount=3000, party_size=4, diet="vegetarian", occasion="birthday")
    recs = _rank(agent, prefs, {"Rooftop Rasoi", "Wakad Chaat Corner"}, {"Wakad": 0.0})
    assert [r.restaurant.name for r in recs] == ["Rooftop Rasoi", "Wakad Chaat Corner"]


def test_tc22_explanations_match_the_data(agent):
    """TC-22: reasons cite budget headroom; trade-offs cite distance, budget pressure and unknown info."""
    prefs = Preferences(area="Pimpri", budget_amount=1100, party_size=2, features=["parking"])
    recs = _rank(agent, prefs, {"The Biryani Ledger", "Kolhapuri Kitchen Co."}, {"Pimpri": 0.0, "Chinchwad": 3.0})
    by_name = {r.restaurant.name: r for r in recs}
    biryani = by_name["The Biryani Ledger"]  # Chinchwad, ₹800 for 2
    assert any("leaving ₹300" in x for x in biryani.reasons)
    assert any("3 km from Pimpri" in x for x in biryani.trade_offs)
    kolhapuri = by_name["Kolhapuri Kitchen Co."]
    assert any("Has parking" in x for x in kolhapuri.reasons)


def test_tc23_unknown_feature_is_reported_as_missing_information(agent):
    """TC-23: a null feature value is shown as 'not confirmed', never as a yes."""
    prefs = Preferences(area="Pimpri", budget_amount=2000, party_size=2, features=["wifi"])
    rec = _rank(agent, prefs, {"Kolhapuri Kitchen Co."}, {"Pimpri": 0.0})[0]
    assert any("not confirmed" in t for t in rec.trade_offs)


def test_tc24_ranking_is_deterministic(agent):
    """TC-24: the same input always produces the same order."""
    prefs = Preferences(area="Camp", budget_amount=3000, party_size=2)
    names = {"Camp Irani Café", "The Colonial Table", "Mughal Lanterns"}
    first = [r.restaurant.id for r in _rank(agent, prefs, names, {"Camp": 0.0})]
    for _ in range(5):
        assert [r.restaurant.id for r in _rank(agent, prefs, names, {"Camp": 0.0})] == first
