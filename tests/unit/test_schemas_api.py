import pytest
from pydantic import ValidationError

from app.api.schemas import (
    AgentInfoResponse,
    ExecuteRequest,
    ExecuteResponse,
    LLMStep,
    PromptExample,
    PromptTemplate,
    StudentInfo,
    TeamInfoResponse,
)


def test_team_info_response_exact_fields():
    resp = TeamInfoResponse(
        group_batch_order_number="1_2",
        team_name="Team",
        students=[StudentInfo(name="A", email="a@example.com")],
    )
    assert set(resp.model_dump().keys()) == {"group_batch_order_number", "team_name", "students"}


def test_team_info_response_rejects_extra_fields():
    with pytest.raises(ValidationError):
        TeamInfoResponse(
            group_batch_order_number="1_2",
            team_name="Team",
            students=[],
            status="ok",
        )


def test_student_info_exact_fields():
    with pytest.raises(ValidationError):
        StudentInfo(name="A", email="a@example.com", extra="x")


def test_agent_info_response_exact_fields():
    resp = AgentInfoResponse(
        description="d",
        purpose="p",
        prompt_template=PromptTemplate(template="t"),
        prompt_examples=[
            PromptExample(
                prompt="p1",
                full_response="r1",
                steps=[LLMStep(module="Request Interpreter", prompt={}, response={})],
            )
        ],
    )
    assert set(resp.model_dump().keys()) == {"description", "purpose", "prompt_template", "prompt_examples"}
    assert set(resp.prompt_template.model_dump().keys()) == {"template"}
    example = resp.prompt_examples[0]
    assert set(example.model_dump().keys()) == {"prompt", "full_response", "steps"}
    step = example.steps[0]
    assert set(step.model_dump().keys()) == {"module", "prompt", "response"}


def test_execute_request_only_accepts_prompt():
    req = ExecuteRequest(prompt="hello")
    assert req.prompt == "hello"
    with pytest.raises(ValidationError):
        ExecuteRequest(prompt="hello", extra_field="oops")
    with pytest.raises(ValidationError):
        ExecuteRequest()


def test_execute_response_exact_fields():
    resp = ExecuteResponse(status="ok", error=None, response="hi", steps=[])
    assert set(resp.model_dump().keys()) == {"status", "error", "response", "steps"}
    with pytest.raises(ValidationError):
        ExecuteResponse(status="ok", error=None, response="hi", steps=[], extra="x")
    with pytest.raises(ValidationError):
        ExecuteResponse(status="weird", error=None, response=None, steps=[])
