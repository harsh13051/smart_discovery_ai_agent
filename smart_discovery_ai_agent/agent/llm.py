"""Thin LLM client used for two jobs only:

1. Turning a free-text request into structured preferences (JSON).
2. Rewriting the final, already-computed recommendation as friendly prose.

Everything else (filtering, budget maths, ranking) is ordinary Python, so the
agent's decisions stay checkable. The client uses only the standard library,
so demo mode needs no packages at all.

Supported providers:
* "openai"    - any OpenAI-compatible /chat/completions endpoint (OpenAI,
                Groq, Google Gemini's OpenAI-compatible endpoint, ...).
                Set LLM_BASE_URL to change the endpoint.
* "anthropic" - the Anthropic Messages API.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from config import Settings


class LLMError(RuntimeError):
    """Raised for any LLM failure; callers fall back to rule-based logic."""


# A transport takes (url, headers, body) and returns the decoded JSON response.
Transport = Callable[[str, dict, dict], dict]


def _http_post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - needs network
        raise LLMError(f"HTTP {exc.code} from LLM provider") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:  # pragma: no cover
        raise LLMError(f"Could not reach LLM provider: {exc}") from exc
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise LLMError("LLM provider returned invalid JSON") from exc


class LLMClient:
    def __init__(self, settings: Settings, transport: Optional[Transport] = None):
        if not settings.llm_enabled:
            raise LLMError("LLM is not configured")
        self.settings = settings
        self._transport = transport or (
            lambda url, headers, body: _http_post(url, headers, body, settings.llm_timeout_s)
        )

    def complete(self, system: str, user: str, max_tokens: int = 500) -> str:
        s = self.settings
        if s.llm_provider == "anthropic":
            url = (s.llm_base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
            headers = {"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01"}
            body = {
                "model": s.llm_model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "temperature": 0,
            }
            data = self._transport(url, headers, body)
            try:
                return "".join(
                    block.get("text", "") for block in data["content"] if block.get("type") == "text"
                ).strip()
            except (KeyError, TypeError) as exc:
                raise LLMError("Unexpected Anthropic response shape") from exc

        url = (s.llm_base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {s.llm_api_key}"}
        body = {
            "model": s.llm_model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        data = self._transport(url, headers, body)
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected OpenAI-compatible response shape") from exc

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        text = self.complete(system, user)
        return parse_json_object(text)


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from model output (tolerates ``` fences)."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise LLMError("No JSON object found in LLM output")
    try:
        value = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError("LLM output was not valid JSON") from exc
    if not isinstance(value, dict):
        raise LLMError("LLM output JSON was not an object")
    return value
