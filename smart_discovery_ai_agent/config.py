"""Runtime configuration, read from environment variables (or a .env file).

No credentials are stored in code. If no LLM is configured the agent runs in
DEMO mode, which uses deterministic rule-based logic for every stage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

SUPPORTED_PROVIDERS = ("none", "openai", "anthropic")


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader so the project needs no extra package for it.

    Existing environment variables are never overwritten.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_timeout_s: float = 20.0
    restaurants_file: Path = DATA_DIR / "restaurants.json"
    areas_file: Path = DATA_DIR / "areas.json"
    max_results: int = 3
    tax_rate: float = 0.05  # assumed 5% GST on restaurant bills in the cost estimate

    @property
    def llm_enabled(self) -> bool:
        return (
            self.llm_provider in ("openai", "anthropic")
            and bool(self.llm_api_key)
            and bool(self.llm_model)
        )

    @property
    def mode_label(self) -> str:
        if self.llm_enabled:
            return f"LLM mode ({self.llm_provider}: {self.llm_model})"
        return "DEMO mode (rule-based, no API key needed)"


def get_settings(force_demo: bool = False) -> Settings:
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "none").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        provider = "none"
    if force_demo:
        provider = "none"
    return Settings(
        llm_provider=provider,
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "20")),
    )
