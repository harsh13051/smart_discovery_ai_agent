"""Stage 1 tests: understanding the request (rule-based extractor)."""
import pytest

from agent.extractor import (
    AreaVocabulary,
    InputValidationError,
    RuleBasedExtractor,
    bare_amount,
    check_constraints,
)


@pytest.fixture
def rules(agent):
    return RuleBasedExtractor(agent.vocab)


def test_tc01_full_request_is_structured(rules):
    """TC-01: every stated preference is extracted from one sentence."""
    p = rules.extract("Veg birthday dinner for 4 in Wakad under ₹3,000, rooftop if possible")
    assert p.area == "Wakad"
    assert p.budget_amount == 3000 and p.budget_type == "total"
    assert p.party_size == 4
    assert p.diet == "vegetarian"
    assert p.occasion == "birthday"
    assert "outdoor_seating" in p.features


def test_tc02_per_person_budget_becomes_total(rules):
    """TC-02: '600 per person' for 3 people means a ₹1,800 total budget."""
    p = rules.extract("600 per person in Hinjewadi for 3")
    assert p.budget_type == "per_person"
    assert p.total_budget == 1800


def test_tc03_non_veg_is_not_vegetarian(rules):
    """TC-03: 'non-veg' must not be misread as a vegetarian constraint."""
    assert rules.extract("non-veg biryani for 4 in Chinchwad under 2000").diet == "any"
    assert rules.extract("pure veg thali in Baner under 600").diet == "vegetarian"


def test_tc04_area_aliases_are_normalised(rules):
    """TC-04: spelling variants map to the canonical area name."""
    assert rules.extract("lunch in Hinjawadi under 500").area == "Hinjewadi"
    assert rules.extract("dinner at FC Road under 800").area == "Shivajinagar"


@pytest.mark.parametrize("bad", ["", "   ", None, "x" * 501])
def test_tc05_unusable_input_is_rejected(rules, bad):
    """TC-05: empty, blank, missing or over-long input raises a clear error."""
    with pytest.raises(InputValidationError):
        rules.extract(bad)


def test_tc06_budget_numbers_are_not_confused(rules):
    """TC-06: 'for 1500' is not a party of 1500, and '2k budget, 4 of us' is ₹2,000 for 4."""
    p = rules.extract("dinner in Camp for 1500 rupees")
    assert p.party_size is None and p.budget_amount == 1500
    p = rules.extract("2k budget, 4 of us, kothrud")
    assert p.budget_amount == 2000 and p.party_size == 4


def test_tc07_invalid_values_are_reported(rules):
    """TC-07: negative/zero budgets and huge parties fail constraint checks."""
    assert check_constraints(rules.extract("dinner for 2 in Aundh under -500"))
    assert check_constraints(rules.extract("dinner for 2 in Aundh under 0"))
    assert check_constraints(rules.extract("dinner for 50 people in Baner under 20000"))
    assert check_constraints(rules.extract("dinner for 2 in Aundh under 900")) == []


def test_tc08_bare_amount_only_matches_lone_numbers():
    """TC-08: a bare follow-up number such as '1,500' or '2k' is read as an amount."""
    assert bare_amount("1,500") == 1500
    assert bare_amount("₹2k") == 2000
    assert bare_amount("Kothrud") is None


def test_tc09_off_topic_request_is_flagged(rules):
    """TC-09: a non-food request is marked as not a restaurant request."""
    assert rules.extract("what's the weather tomorrow").is_restaurant_request is False
