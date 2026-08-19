"""The 4 required DigitalNomadAgent API endpoints. Paths and shapes are exact and
must never be renamed or restructured."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

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
from app.core.logging import logger
from app.evidence.saved_searches import SavedSearchStore

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
async def execute(payload: ExecuteRequest, request: Request, response: Response) -> ExecuteResponse:
    prompt = payload.prompt.strip()
    settings = request.app.state.settings

    if not prompt:
        raise PlaceMatchError("The prompt cannot be empty.")
    if len(prompt) > settings.max_prompt_length:
        raise PlaceMatchError("The prompt exceeds the maximum allowed length.")

    interactive = request.headers.get("x-interactive-mode", "").strip().lower() == "true"

    orchestrator = request.app.state.orchestrator
    result = await orchestrator.run(prompt, interactive=interactive)
    steps = [LLMStep.model_validate(s) for s in result.steps]

    if result.status == "ok" and result.response:
        # History is a purely additive convenience feature -- a failure here (DB
        # lock contention, /tmp pressure on a Vercel cold start) must never turn
        # an already-computed valid answer into a client-visible error.
        try:
            session = await SavedSearchStore(request.app.state.db).save_session(
                prompt=prompt,
                result_data={
                    "status": result.status,
                    "error": result.error,
                    "response": result.response,
                    "steps": [step.model_dump(mode="json") for step in steps],
                },
            )
            response.headers["X-DigitalNomadAgent-Session-Id"] = session["id"]
        except Exception as exc:  # noqa: BLE001 - history persistence must never fail the request
            logger.warning("save_session failed, continuing without history: %s", exc)

    return ExecuteResponse(
        status=result.status,
        error=result.error,
        response=result.response,
        steps=steps,
    )


# Not one of the 4 required course-spec endpoints above -- purely additive, for the
# "Clear history" sidebar button. Safe to add: doesn't touch the pinned paths/shapes.
@router.delete("/api/history")
async def clear_history(request: Request) -> dict:
    await SavedSearchStore(request.app.state.db).clear_all()
    return {"status": "ok"}


class DeleteHistorySessionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str]


# Also additive, for the "clear selected" sidebar action -- deletes only the
# checked conversations rather than all of history.
@router.post("/api/history/delete")
async def delete_history_sessions(payload: DeleteHistorySessionsRequest, request: Request) -> dict:
    await SavedSearchStore(request.app.state.db).delete_sessions(payload.ids)
    return {"status": "ok"}
