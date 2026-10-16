"""Runtime configuration for the web app, read from environment variables / .env.

Only variables the implementation actually uses are read here. Nothing secret is
stored in code; a missing key simply disables the matching integration and the
app reports that honestly through /api/status.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from config import PROJECT_ROOT, load_dotenv

DEFAULT_OVERPASS = (
    "https://overpass-api.de/api/interpreter,"
    "https://overpass.kumi.systems/api/interpreter,"
    "https://overpass.private.coffee/api/interpreter"
)


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AppSettings:
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'discovery.db').as_posix()}"
    anthropic_api_key: str = ""
    llm_provider: str = "anthropic"            # anthropic | openai_compatible
    llm_api_key: str = ""                      # for openai_compatible (Gemini, Groq, OpenRouter, Ollama...)
    llm_base_url: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_s: float = 45.0
    osm_contact: str = ""                      # required by OSM usage policies (User-Agent)
    overpass_urls: tuple[str, ...] = tuple(DEFAULT_OVERPASS.split(","))
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    provider_timeout_s: float = 25.0
    cache_ttl_s: int = 6 * 3600
    cookie_secure: bool = False
    session_days: int = 14
    allow_demo_data: bool = True
    static_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "web")

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "openai_compatible":
            local = any(h in self.llm_base_url for h in ("localhost", "127.0.0.1"))
            return bool(self.llm_base_url and self.llm_model and (self.llm_api_key or local))
        return bool(self.anthropic_api_key and self.llm_model)

    @property
    def user_agent(self) -> str:
        contact = self.osm_contact or "unconfigured"
        return f"smart-discovery-ai-agent/1.0 ({contact})"


def load_settings() -> AppSettings:
    load_dotenv()
    urls = tuple(u.strip() for u in os.getenv("OVERPASS_URLS", DEFAULT_OVERPASS).split(",") if u.strip())
    return AppSettings(
        database_url=os.getenv("DATABASE_URL") or AppSettings.database_url,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        llm_provider="openai_compatible" if os.getenv("LLM_PROVIDER", "").strip().lower() in ("openai_compatible", "openai") else "anthropic",
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "claude-sonnet-5-5").strip(),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "45")),
        osm_contact=os.getenv("OSM_CONTACT", "").strip(),
        overpass_urls=urls,
        cookie_secure=_bool("COOKIE_SECURE", False),
        allow_demo_data=_bool("ALLOW_DEMO_DATA", True),
    )
