import json
from datetime import UTC, datetime

import pytest

from app.agent.dynamic_evaluation import build_unresolved_scoring_payload, score_unresolved_criteria
from app.agent.models import Budget, CandidateEvaluation, PlaceRequestProfile
from app.evidence.models import ToolResult
from app.llm.base import BaseLLMClient, LLMRawResponse


def _evaluation(place, *, eliminated=False, unscored_evidence=None) -> CandidateEvaluation:
    return CandidateEvaluation(
        place=place,
        country="Testland",
        total_score=0.5,
        confidence_score=0.5,
        eliminated=eliminated,
        unscored_evidence=unscored_evidence or [],
    )


class _FakeBudget:
    async def check_before_call(self, request_id, module, est_input, est_output):
        return None

    async def record_call(self, request_id, module, model, input_tokens, output_tokens, cost, success):
        return None


class _EchoClient(BaseLLMClient):
    def __init__(self, text: str):
        self._text = text

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        return LLMRawResponse(text=self._text, input_tokens=10, output_tokens=10, provider_cost_usd=0.0)


def test_build_unresolved_scoring_payload_skips_eliminated_and_fully_resolved():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [
        _evaluation("Eliminated", eliminated=True, unscored_evidence=["cost"]),
        _evaluation("FullyResolved", unscored_evidence=[]),
        _evaluation("Pending", unscored_evidence=["cost"]),
    ]
    evidence_by_place = {
        "Pending": [
            ToolResult(
                tool_name="BudgetFitTool",
                place="Pending",
                normalized_data={"budget_context": {"status": "not_provided"}},
                source_name="t",
                retrieved_at=datetime.now(UTC),
                confidence="medium",
            )
        ]
    }
    payload = build_unresolved_scoring_payload(evaluations, profile, evidence_by_place)
    assert [item["place"] for item in payload] == ["Pending"]
    assert "cost" in payload[0]["criteria"]


@pytest.mark.asyncio
async def test_score_unresolved_criteria_returns_empty_without_pending_work():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [_evaluation("Resolved", unscored_evidence=[])]
    result = await score_unresolved_criteria(
        evaluations,
        profile,
        {},
        client=_EchoClient(""),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_score_unresolved_criteria_parses_llm_response():
    profile = PlaceRequestProfile(purpose="vacation", budget=Budget())
    evaluations = [_evaluation("Nice", unscored_evidence=["cost"])]
    evidence_by_place = {
        "Nice": [
            ToolResult(
                tool_name="BudgetFitTool",
                place="Nice",
                normalized_data={"budget_context": {"status": "not_provided"}},
                source_name="t",
                retrieved_at=datetime.now(UTC),
                confidence="medium",
            )
        ]
    }
    response_text = json.dumps(
        {"scores": [{"place": "Nice", "criterion": "cost", "score": 0.75, "rationale": "Affordable."}]}
    )
    result = await score_unresolved_criteria(
        evaluations,
        profile,
        evidence_by_place,
        client=_EchoClient(response_text),
        budget=_FakeBudget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
    )
    assert result == {"Nice": {"cost": (0.75, "Affordable.")}}
