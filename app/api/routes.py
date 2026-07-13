"""The 4 required PlaceMatch API endpoints. Paths and shapes are exact and
must never be renamed or restructured."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.api.agent_info_content import DESCRIPTION, PROMPT_EXAMPLES, PROMPT_TEMPLATE, PURPOSE
from app.api.schemas import (
    AgentInfoResponse,
    ExecuteRequest,
    ExecuteResponse,
    LLMStep,
    PromptExample,
    PromptTemplate,
    TeamInfoResponse,
)
from app.core.config import REPO_ROOT
from app.core.exceptions import PlaceMatchError

router = APIRouter()


@router.get("/api/team_info", response_model=TeamInfoResponse)
async def get_team_info(request: Request) -> TeamInfoResponse:
    return request.app.state.team_info


@router.get("/api/agent_info", response_model=AgentInfoResponse)
async def get_agent_info() -> AgentInfoResponse:
    return AgentInfoResponse(
        description=DESCRIPTION,
        purpose=PURPOSE,
        prompt_template=PromptTemplate(template=PROMPT_TEMPLATE),
        prompt_examples=[PromptExample.model_validate(e) for e in PROMPT_EXAMPLES],
    )


@router.get("/api/model_architecture")
async def get_model_architecture() -> FileResponse:
    path = REPO_ROOT / "assets" / "model_architecture.png"
    return FileResponse(path, media_type="image/png")


@router.post("/api/execute", response_model=ExecuteResponse)
async def execute(payload: ExecuteRequest, request: Request) -> ExecuteResponse:
    prompt = payload.prompt.strip()
    settings = request.app.state.settings

    if not prompt:
        raise PlaceMatchError("The prompt cannot be empty.")
    if len(prompt) > settings.max_prompt_length:
        raise PlaceMatchError("The prompt exceeds the maximum allowed length.")

    orchestrator = request.app.state.orchestrator
    result = await orchestrator.run(prompt)

    return ExecuteResponse(
        status=result.status,
        error=result.error,
        response=result.response,
        steps=[LLMStep.model_validate(s) for s in result.steps],
    )
