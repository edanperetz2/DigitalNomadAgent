"""Central application configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_AGENT_EXECUTION_TIMEOUT_SECONDS = 285.0
DEFAULT_RECOMMENDATION_RESERVE_SECONDS = 60.0


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
    llm_max_output_tokens: int = 4000
    llm_input_cost_per_1m: float = 0.0
    llm_output_cost_per_1m: float = 0.0

    # Pinned so paid runs are reproducible: an identical prompt should not need
    # re-spending to chase output variance. Raise it only for a deliberate
    # experiment, never as an incidental change.
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    # --- Networking ---------------------------------------------------------------
    # Research-tool HTTP calls (Nominatim/Overpass/etc.) are fast REST lookups and
    # should fail fast; real LLM completions take proportionally longer to generate
    # as llm_max_output_tokens grows, so they get their own, longer timeout instead
    # of sharing this one.
    http_timeout_seconds: float = 10.0
    llm_http_timeout_seconds: float = 60.0
    agent_execution_timeout_seconds: float = Field(
        default=MAX_AGENT_EXECUTION_TIMEOUT_SECONDS,
        gt=0,
        le=MAX_AGENT_EXECUTION_TIMEOUT_SECONDS,
        allow_inf_nan=False,
    )
    recommendation_reserve_seconds: float = Field(
        default=DEFAULT_RECOMMENDATION_RESERVE_SECONDS,
        gt=0,
        le=120,
        allow_inf_nan=False,
    )
    tool_execution_timeout_seconds: float = Field(default=50.0, gt=0, le=120, allow_inf_nan=False)
    max_concurrent_tool_requests: int = 10
    cache_ttl_hours: int = 168
    upstream_request_timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    # --- Storage / server -----------------------------------------------------------
    sqlite_path: str = "./data/digitalnomadagent.db"
    app_port: int = 8000

    # --- Request limits ---------------------------------------------------------------
    max_prompt_length: int = 4000
    max_bulk_candidates: int = 30
    max_finalists: int = 8
    max_final_recommendations: int = 8
    max_research_iterations: int = 1
    max_json_repair_attempts: int = 1

    # --- Testing ------------------------------------------------------------------------
    run_live_tests: bool = False

    @model_validator(mode="after")
    def validate_timeout_alignment(self) -> Settings:
        if (
            self.upstream_request_timeout_seconds is not None
            and self.upstream_request_timeout_seconds <= self.agent_execution_timeout_seconds
        ):
            raise ValueError(
                "UPSTREAM_REQUEST_TIMEOUT_SECONDS must exceed AGENT_EXECUTION_TIMEOUT_SECONDS "
                "so a proxy cannot drop the best-effort response."
            )
        return self

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
