"""LLM integration tests using a scripted fake provider (no network, no real key)."""
import pytest

from agent.llm import LLMClient, LLMError, parse_json_object
from config import Settings, get_settings

GOOD_EXTRACTION = {
    "is_restaurant_request": True, "area": "baner", "budget_amount": 1800, "budget_type": "total",
    "party_size": 2, "cuisines": ["italian", "klingon"], "diet": "any", "occasion": "date",
    "min_rating": None, "features": ["teleporter"],
}


def test_tc50_llm_output_is_validated_before_use(make_llm_agent):
    """TC-50: fenced JSON is parsed; area is normalised; invented cuisine/feature values are dropped."""
    agent = make_llm_agent(extract_json="```json\n" + __import__("json").dumps(GOOD_EXTRACTION) + "\n```")
    p = agent.extractor.extract("somewhere romantic and italian in baner, about 1800 for two")
    assert agent.extractor.last_source == "llm"
    assert p.area == "Baner" and p.cuisines == ["italian"] and p.features == []


def test_tc51_llm_failure_falls_back_to_rules(make_llm_agent):
    """TC-51: if the LLM API fails, the rule-based extractor is used and the run still succeeds."""
    agent = make_llm_agent(fail="HTTP 503 from LLM provider")
    r = agent.run("veg birthday dinner for 4 in Wakad under 3000")
    assert r.status == "ok" and r.mode == "llm"
    assert any("fell back to rule-based" in t.detail for t in r.trace)
    assert any("LLM summary rejected" in t.detail for t in r.trace)  # summary also falls back


def test_tc52_malformed_llm_json_falls_back(make_llm_agent):
    """TC-52: non-JSON model output is detected and the rule-based extractor takes over."""
    agent = make_llm_agent(extract_json="Sure! The user wants food.")
    r = agent.run("dinner for two in Baner under ₹400")
    assert r.status == "ok"
    assert any("fell back" in t.detail for t in r.trace)


def test_tc53_hallucinated_restaurant_is_blocked(make_llm_agent):
    """TC-53: an LLM summary naming a non-shortlisted restaurant is rejected; template used."""
    agent = make_llm_agent(extract_json=GOOD_EXTRACTION, summary_text="Try Sushi Lane, it's lovely!")
    r = agent.run("italian date in Baner under 1800 for 2")
    assert "Sushi Lane" not in r.message
    assert any("non-shortlisted" in t.detail for t in r.trace)


def test_tc54_invented_price_is_blocked(make_llm_agent):
    """TC-54: an LLM summary quoting a price we never computed is rejected."""
    agent = make_llm_agent(extract_json=GOOD_EXTRACTION, summary_text="Little Napoli costs just ₹999 for two.")
    r = agent.run("italian date in Baner under 1800 for 2")
    assert "₹999" not in r.message
    assert any("unverified amount" in t.detail for t in r.trace)


def test_tc55_faithful_llm_summary_is_used(make_llm_agent):
    """TC-55: a summary that only uses verified facts passes validation and is shown."""
    text = "For your date, Little Napoli in Baner is the pick at about ₹1,470."
    agent = make_llm_agent(extract_json=GOOD_EXTRACTION, summary_text=text)
    r = agent.run("italian date in Baner under 1800 for 2")
    assert r.status == "ok" and r.message == text
    assert r.recommendations[0].restaurant.name == "Little Napoli"


def test_tc56_anthropic_response_shape_is_supported():
    """TC-56: the Anthropic Messages API response format is parsed correctly."""
    s = Settings(llm_provider="anthropic", llm_api_key="k", llm_model="m")
    seen = {}

    def transport(url, headers, body):
        seen.update(url=url, headers=headers)
        return {"content": [{"type": "text", "text": '{"ok": true}'}]}

    assert LLMClient(s, transport=transport).complete_json("sys", "user") == {"ok": True}
    assert seen["url"].endswith("/v1/messages") and seen["headers"]["x-api-key"] == "k"


def test_tc57_demo_mode_needs_no_credentials(monkeypatch):
    """TC-57: with no key (or an unknown provider) the agent runs in demo mode."""
    for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("config.load_dotenv", lambda *a, **k: None)
    assert get_settings().llm_enabled is False
    monkeypatch.setenv("LLM_PROVIDER", "made-up")
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("LLM_MODEL", "y")
    assert get_settings().llm_provider == "none"
    with pytest.raises(LLMError):
        LLMClient(Settings())


def test_tc58_json_parser_rejects_garbage():
    """TC-58: parse_json_object refuses output without a JSON object."""
    with pytest.raises(LLMError):
        parse_json_object("no json here")
    assert parse_json_object('text {"a": 1} text') == {"a": 1}
