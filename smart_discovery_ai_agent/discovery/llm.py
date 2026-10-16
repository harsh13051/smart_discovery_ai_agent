"""Anthropic Messages API client with tool use (httpx; the API key never leaves the server)."""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx


class LLMError(RuntimeError):
    """Any LLM failure; the caller falls back to the rule-based pipeline."""


class AnthropicClient:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, timeout_s: float = 45.0,
                 client: Optional[httpx.Client] = None):
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not configured")
        self.api_key, self.model = api_key, model
        self.client = client or httpx.Client(timeout=timeout_s)

    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
               max_tokens: int = 1500) -> dict[str, Any]:
        body = {"model": self.model, "max_tokens": max_tokens, "system": system,
                "messages": messages, "tools": tools}
        try:
            resp = self.client.post(
                self.URL, json=body,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMError(f"Could not reach the LLM provider ({type(exc).__name__})") from exc
        if resp.status_code == 429:
            raise LLMError("LLM provider rate limit reached (HTTP 429)")
        if resp.status_code >= 400:
            raise LLMError(f"LLM provider returned HTTP {resp.status_code}")  # body omitted: may echo input
        try:
            data = resp.json()
            if not isinstance(data.get("content"), list):
                raise ValueError("no content")
            return data
        except ValueError as exc:
            raise LLMError("LLM provider returned an unexpected response") from exc


class OpenAICompatClient:
    """Any OpenAI-style /chat/completions endpoint with function calling (Gemini, Groq, OpenRouter, Ollama...).

    Same ``create`` interface as AnthropicClient: Anthropic-shaped messages in, Anthropic-shaped
    response out, so the agent loop is provider-independent.
    """

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float = 45.0,
                 client: Optional[httpx.Client] = None):
        if not base_url:
            raise LLMError("LLM_BASE_URL is not configured")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model = api_key, model
        self.client = client or httpx.Client(timeout=timeout_s)

    @staticmethod
    def _convert(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            c = m["content"]
            if isinstance(c, str):
                out.append({"role": m["role"], "content": c})
            elif m["role"] == "assistant":
                text = "".join(b.get("text", "") for b in c if b.get("type") == "text")
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                         for b in c if b.get("type") == "tool_use"]
                msg: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                for b in c:
                    if b.get("type") == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
        return out

    def create(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
               max_tokens: int = 1500) -> dict[str, Any]:
        body = {"model": self.model, "max_tokens": max_tokens, "messages": self._convert(system, messages),
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["input_schema"]}} for t in tools]}
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        try:
            resp = self.client.post(self.url, json=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMError(f"Could not reach the LLM provider ({type(exc).__name__})") from exc
        if resp.status_code == 429:
            raise LLMError("LLM provider rate limit reached (HTTP 429)")
        if resp.status_code >= 400:
            raise LLMError(f"LLM provider returned HTTP {resp.status_code}")
        try:
            msg = resp.json()["choices"][0]["message"]
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for call in msg.get("tool_calls") or []:
                args = call["function"].get("arguments") or "{}"
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"],
                               "input": json.loads(args) if isinstance(args, str) else args})
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM provider returned an unexpected response") from exc
        has_tools = any(b["type"] == "tool_use" for b in blocks)
        return {"stop_reason": "tool_use" if has_tools else "end_turn", "content": blocks}
