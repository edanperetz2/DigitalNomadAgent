"""Central application configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
# Vercel kills any function at 300 seconds, and it kills it *hard* -- a platform
# error page, not this agent's graceful degraded answer. The backend budget has
# to leave room for the whole tail after the deadline fires: rendering the
# best-effort result, serializing a ~15 KB response with its full step trace, and
# the network hop.
#
# 285 left roughly 15 seconds for all of that. The slowest run measured against
# the deployment was P09 at 247.7s client-side (2026-08-07), so nothing has hit
# the ceiling yet -- but a request that does would finish perilously close to the
# platform kill. 270 buys 30 seconds of margin and costs 15 seconds of research
# time (the shared research cutoff moves 225 -> 210), which is the cheap side of
# that trade: a slightly thinner answer beats no answer at all.
MAX_AGENT_EXECUTION_TIMEOUT_SECONDS = 270.0
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
    # Ceiling on one prompt, enforced by BudgetManager.check_before_call. It was
    # 4000 while nothing read it; the real 2026-08-05 run showed ordinary calls
    # at 4,194-8,471 input tokens, so enforcing 4000 would have refused most of
    # them. 16000 is roughly double the largest observed call, which leaves room
    # for a full finalist set with rich evidence while still bounding a runaway
    # prompt at about $0.012.
    llm_max_input_tokens: int = 16000
    # 4000 was not enough for the Recommendation Generator's largest answers and
    # the failure was silent: the model's JSON was cut off mid-string, the
    # repair attempt hit the identical ceiling, and the response degraded to the
    # deterministic template. P08 -- eight candidates, four evidence bullets
    # each, 59 sources -- lost its written answer that way in the 2026-08-06
    # run, and P01/P03/P05 needed a retry for the same reason the day before.
    # Only the answers that need the room pay for it (D54). Raised again from
    # 8000 on 2026-08-17: a full live 22-prompt production run still showed one
    # first-attempt truncation (P15) that only survived because the repair
    # attempt happened to fit -- the same failure D54 describes was one repair
    # away from recurring. 12000 leaves 50% more headroom before the same
    # ceiling problem hits both the original call and its repair.
    llm_max_output_tokens: int = 12000
    llm_input_cost_per_1m: float = 0.0
    llm_output_cost_per_1m: float = 0.0

    # Left unset by default, which omits `temperature` from the request so the
    # model's own default applies. Do NOT pin this blindly: gpt-5 family models
    # reject every value except 1.0 with an UnsupportedParamsError, so a pin
    # intended to make runs reproducible breaks them outright. Set it only for a
    # provider known to accept the value.
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)

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
    # Write the shipped example conversations into an empty history at startup.
    # On Vercel the database lives in /tmp, which is wiped on every cold start,
    # so a deployment otherwise shows an empty sidebar to its first visitor.
    # Off by default: a developer's own history is theirs, and every test gets a
    # fresh database that should stay empty unless the test fills it.
    seed_example_sessions: bool = False

    # --- Request limits ---------------------------------------------------------------
    max_prompt_length: int = 4000
    max_bulk_candidates: int = 30
    max_finalists: int = 8
    max_final_recommendations: int = 8
    # Was set but never actually wired to the 4 LLM-calling modules until
    # 2026-08-17 -- every traced_llm_call() site relied on its own hardcoded
    # default of 1 regardless of this value. Now threaded through
    # Orchestrator -> {interpret_request, generate_candidates,
    # score_unresolved_criteria, generate_recommendation}. Raised to 2: a
    # second repair attempt costs nothing unless the first one is also
    # malformed, and gives one more chance before a request falls back to the
    # deterministic template (see the llm_max_output_tokens note above, D54).
    max_json_repair_attempts: int = 2

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
