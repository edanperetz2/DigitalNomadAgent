"""Central application configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM provider selection -------------------------------------------------
    llm_provider: str = "llmod"
    mock_llm: bool = True

    # --- LLMod.ai credentials / endpoint (never hard-coded, never logged) ------
    llmod_api_key: str | None = None
    llmod_base_url: str = "https://api.llmod.ai"
    llmod_chat_completions_path: str = "/v1/chat/completions"
    llmod_model: str | None = None
    llmod_auth_header: str = "Authorization"
    llmod_auth_scheme: str = "Bearer"

    # --- Budget controls ---------------------------------------------------------
    max_project_budget_usd: float = 13.0
    max_llm_calls_per_request: int = 4
    llm_max_input_tokens: int = 4000
    llm_max_output_tokens: int = 1500
    llm_input_cost_per_1m: float = 0.0
    llm_output_cost_per_1m: float = 0.0

    # --- Networking ---------------------------------------------------------------
    http_timeout_seconds: float = 10.0
    max_concurrent_tool_requests: int = 5
    cache_ttl_hours: int = 168

    # --- Storage / server -----------------------------------------------------------
    sqlite_path: str = "./data/placematch.db"
    app_port: int = 8000

    # --- Request limits ---------------------------------------------------------------
    max_prompt_length: int = 4000
    max_candidates: int = 5
    max_final_recommendations: int = 3
    max_research_iterations: int = 1
    max_json_repair_attempts: int = 1

    # --- Testing ------------------------------------------------------------------------
    run_live_tests: bool = False

    @property
    def sqlite_path_resolved(self) -> Path:
        path = Path(self.sqlite_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
