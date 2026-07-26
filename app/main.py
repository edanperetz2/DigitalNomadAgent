"""FastAPI application factory: DI wiring, startup validation, route mounting."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.agent.orchestrator import Orchestrator
from app.api.exception_handlers import (
    budget_exceeded_handler,
    place_match_error_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.api.routes import router
from app.api.schemas import TeamInfoResponse
from app.core.config import REPO_ROOT, get_settings
from app.core.exceptions import BudgetExceededError, ConfigurationError, PlaceMatchError
from app.core.logging import logger
from app.evidence.cache import ToolCache
from app.evidence.database import Database
from app.evidence.memory import EvidenceMemory
from app.llm.base import BaseLLMClient
from app.llm.budget import BudgetManager
from app.llm.llmod import LLModClient
from app.llm.mock import MockLLMClient
from app.tools.activities import ActivitiesTool
from app.tools.amenities import AmenitiesTool
from app.tools.budget_fit import BudgetFitTool
from app.tools.geocoding import GeocodingTool
from app.tools.http_client import JsonHttpClient
from app.tools.local_mobility import LocalMobilityTool
from app.tools.mediawiki_client import WIKIVOYAGE_API, MediaWikiClient
from app.tools.origin_resolution import OriginResolver
from app.tools.overpass_client import OverpassClient
from app.tools.place_context import PlaceContextTool
from app.tools.registry import ToolRegistry
from app.tools.safety import SafetyTool
from app.tools.timezone_fit import TimezoneFitTool
from app.tools.transport_access import TransportAccessTool
from app.tools.weather import WeatherTool
from app.tools.wikivoyage_climate import WikivoyageClimateTool
from app.tools.wikivoyage_sections import WikivoyageSectionClient

TEAM_INFO_PATH = REPO_ROOT / "config" / "team_info.json"
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"
STATIC_DIR = REPO_ROOT / "app" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _load_team_info() -> TeamInfoResponse:
    if not TEAM_INFO_PATH.exists():
        raise ConfigurationError(f"config/team_info.json is missing at {TEAM_INFO_PATH}.")
    try:
        data = json.loads(TEAM_INFO_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"config/team_info.json is not valid JSON: {exc}") from exc
    return TeamInfoResponse.model_validate(data)


def _build_llm_client(settings) -> BaseLLMClient:
    if settings.mock_llm:
        return MockLLMClient()
    return LLModClient(
        api_key=settings.llmod_api_key,
        base_url=settings.llmod_base_url,
        chat_completions_path=settings.llmod_chat_completions_path,
        model=settings.llmod_model,
        auth_header=settings.llmod_auth_header,
        auth_scheme=settings.llmod_auth_scheme,
        timeout_seconds=settings.http_timeout_seconds,
    )


def _build_tool_registry(
    cache: ToolCache,
    timeout: float,
    max_concurrent: int,
    tool_execution_timeout: float = 50.0,
) -> ToolRegistry:
    http = JsonHttpClient(timeout=timeout)
    overpass = OverpassClient(http=http)
    mediawiki = MediaWikiClient(WIKIVOYAGE_API, http=http)
    wikivoyage_sections = WikivoyageSectionClient(mediawiki)
    origin_resolver = OriginResolver(cache, http=http)
    tools = {
        "GeocodingTool": GeocodingTool(cache, timeout, http=http),
        "WeatherTool": WeatherTool(cache, timeout, http=http),
        "WikivoyageClimateTool": WikivoyageClimateTool(cache, timeout, mediawiki=mediawiki),
        "AmenitiesTool": AmenitiesTool(cache, timeout, overpass=overpass),
        "LocalMobilityTool": LocalMobilityTool(
            cache,
            timeout,
            overpass=overpass,
            wikivoyage=wikivoyage_sections,
        ),
        "PlaceContextTool": PlaceContextTool(cache, timeout, mediawiki=mediawiki),
        "TimezoneFitTool": TimezoneFitTool(
            cache,
            timeout,
            http=http,
            origin_resolver=origin_resolver,
        ),
        "BudgetFitTool": BudgetFitTool(cache, timeout, http=http),
        "TransportAccessTool": TransportAccessTool(
            cache,
            timeout,
            overpass=overpass,
            wikivoyage=wikivoyage_sections,
            origin_resolver=origin_resolver,
        ),
        "ActivitiesTool": ActivitiesTool(
            cache,
            timeout,
            overpass=overpass,
            wikivoyage=wikivoyage_sections,
        ),
        "SafetyTool": SafetyTool(
            cache,
            timeout,
            http=http,
            wikivoyage=wikivoyage_sections,
        ),
    }
    return ToolRegistry(
        tools,
        max_concurrent_requests=max_concurrent,
        tool_execution_timeout_seconds=tool_execution_timeout,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.team_info = _load_team_info()

    db = Database(settings.sqlite_path_resolved)
    await db.connect()
    app.state.db = db

    cache = ToolCache(db, default_ttl_hours=settings.cache_ttl_hours)
    evidence_memory = EvidenceMemory(db)
    budget_manager = BudgetManager(
        db,
        max_project_budget_usd=settings.max_project_budget_usd,
        max_llm_calls_per_request=settings.max_llm_calls_per_request,
        input_cost_per_1m=settings.llm_input_cost_per_1m,
        output_cost_per_1m=settings.llm_output_cost_per_1m,
    )
    llm_client = getattr(app.state, "llm_client_override", None) or _build_llm_client(settings)
    tool_registry = getattr(app.state, "tool_registry_override", None) or _build_tool_registry(
        cache,
        settings.http_timeout_seconds,
        settings.max_concurrent_tool_requests,
        settings.tool_execution_timeout_seconds,
    )

    app.state.orchestrator = Orchestrator(
        tool_registry,
        evidence_memory,
        llm_client,
        budget_manager,
        max_output_tokens=settings.llm_max_output_tokens,
        max_bulk_candidates=settings.max_bulk_candidates,
        max_finalists=settings.max_finalists,
        max_final_recommendations=settings.max_final_recommendations,
        max_prompt_length=settings.max_prompt_length,
        execution_timeout_seconds=settings.agent_execution_timeout_seconds,
        recommendation_reserve_seconds=settings.recommendation_reserve_seconds,
    )

    logger.info("DigitalNomadAgent started (mock_llm=%s)", settings.mock_llm)
    yield
    await db.close()


def create_app(*, tool_registry_override=None, llm_client_override=None) -> FastAPI:
    """Build the FastAPI app. Test code may inject a fake tool registry and/or
    LLM client so `pytest` never makes real network or paid LLM calls."""
    app = FastAPI(title="DigitalNomadAgent", lifespan=lifespan)
    app.state.tool_registry_override = tool_registry_override
    app.state.llm_client_override = llm_client_override

    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(BudgetExceededError, budget_exceeded_handler)
    app.add_exception_handler(PlaceMatchError, place_match_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(router)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    else:
        # StaticFiles(directory=...) validates existence immediately and raises at import
        # time (this function runs at module load via `app = create_app()` below) --
        # some serverless bundlers don't reliably include non-Python-imported asset
        # directories, so degrade to a missing /static mount rather than crash the
        # whole app over CSS/JS.
        logger.warning("Static directory %s not found; /static will 404.", STATIC_DIR)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {})

    return app


app = create_app()
