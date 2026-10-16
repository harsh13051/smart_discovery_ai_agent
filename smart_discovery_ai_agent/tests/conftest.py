import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from agent import ConversationState, DiscoveryAgent  # noqa: E402
from agent.llm import LLMClient  # noqa: E402


@pytest.fixture
def settings():
    return Settings()  # demo mode: no provider, no key


@pytest.fixture
def agent(settings):
    return DiscoveryAgent(settings)


@pytest.fixture
def state():
    return ConversationState()


@pytest.fixture
def llm_settings():
    return Settings(llm_provider="openai", llm_api_key="test-key-not-real", llm_model="test-model")


def openai_reply(text):
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture
def make_llm_agent(llm_settings):
    """Build an agent whose LLM is a scripted fake (no network)."""

    def _make(extract_json=None, summary_text="", fail=None):
        def transport(url, headers, body):
            if fail:
                from agent.llm import LLMError
                raise LLMError(fail)
            system = body["messages"][0]["content"]
            if "extract" in system:
                return openai_reply(extract_json if isinstance(extract_json, str) else json.dumps(extract_json))
            return openai_reply(summary_text)

        return DiscoveryAgent(llm_settings, llm_client=LLMClient(llm_settings, transport=transport))

    return _make
