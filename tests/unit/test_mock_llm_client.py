import json

import pytest

from app.core.module_names import (
    AGENTIC_RESEARCH,
    DYNAMIC_EVALUATION,
    RECOMMENDATION_GENERATOR,
    REQUEST_INTERPRETER,
)
from app.llm.mock import MockLLMClient


@pytest.mark.asyncio
async def test_request_interpreter_module_returns_valid_json():
    client = MockLLMClient()
    response = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "I want to work remotely in Lisbon."}],
        max_output_tokens=500,
        metadata={"module": REQUEST_INTERPRETER},
    )
    parsed = json.loads(response.text)
    assert parsed["purpose"] == "remote_work"


@pytest.mark.asyncio
async def test_agentic_research_module_returns_lean_bulk_candidates():
    client = MockLLMClient()
    payload = json.dumps({"profile": {"purpose": "study"}})
    response = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": payload}],
        max_output_tokens=500,
        metadata={"module": AGENTIC_RESEARCH},
    )
    parsed = json.loads(response.text)
    assert len(parsed["candidates"]) >= 25
    assert all(
        set(c) == {"place_name", "country", "reason_for_inclusion"} for c in parsed["candidates"]
    )


@pytest.mark.asyncio
async def test_dynamic_evaluation_module_scores_every_unresolved_pair():
    client = MockLLMClient()
    payload = json.dumps(
        {
            "candidates": [
                {
                    "place": "Valencia",
                    "country": "Spain",
                    "criteria": {
                        "cost": {"budget_context": {"status": "not_provided"}},
                        "activities": {"counts_by_category": {"beaches": 10, "hiking": 5}},
                    },
                    "preferences": {"activity_preferences": ["beaches"]},
                }
            ]
        }
    )
    response = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": payload}],
        max_output_tokens=500,
        metadata={"module": DYNAMIC_EVALUATION},
    )
    parsed = json.loads(response.text)
    criteria_scored = {s["criterion"] for s in parsed["scores"]}
    assert criteria_scored == {"cost", "activities"}
    assert all(0.0 <= s["score"] <= 1.0 for s in parsed["scores"])


@pytest.mark.asyncio
async def test_recommendation_generator_module_returns_markdown():
    client = MockLLMClient()
    candidate = {
        "place": "Valencia",
        "country": "Spain",
        "total_score": 0.8,
        "confidence_score": 0.7,
        "advantages": ["nice"],
        "drawbacks": ["crowded"],
    }
    payload = json.dumps(
        {
            "purpose_summary": "a vacation request",
            "assumptions": [],
            "validation_issues": [],
            "candidates": [candidate],
            "sources": [],
        }
    )
    response = await client.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": payload}],
        max_output_tokens=800,
        metadata={"module": RECOMMENDATION_GENERATOR},
    )
    parsed = json.loads(response.text)
    assert "Best matches" in parsed["markdown"]
    assert "Valencia" in parsed["markdown"]


@pytest.mark.asyncio
async def test_mock_client_is_deterministic():
    client = MockLLMClient()
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "vacation to a beach"}]
    r1 = await client.complete(messages, max_output_tokens=500, metadata={"module": REQUEST_INTERPRETER})
    r2 = await client.complete(messages, max_output_tokens=500, metadata={"module": REQUEST_INTERPRETER})
    assert r1.text == r2.text


@pytest.mark.asyncio
async def test_mock_client_never_reports_nonzero_cost():
    client = MockLLMClient()
    response = await client.complete(
        [{"role": "user", "content": "test"}], max_output_tokens=100, metadata={"module": REQUEST_INTERPRETER}
    )
    assert response.provider_cost_usd == 0.0


def test_missing_count_evidence_is_never_scored_as_a_zero_count():
    """A failed count lookup must not surface as a scored 'count (0)' (D22).

    With nothing else to go on the criterion stays unassessed. Where the tool
    also collected descriptive prose, that prose is scored on its own terms
    (E3) -- which is the opposite of inventing a zero, so the D22 guarantee is
    asserted directly here rather than via an empty result.
    """
    from app.llm.mock import score_unresolved_mock

    scores = score_unresolved_mock(
        {
            "candidates": [
                {
                    "place": "Lisbon",
                    "criteria": {
                        "transportation": {
                            "counts_by_component": {},
                            "wikivoyage_excerpt": "The metro is extensive.",
                        },
                        "accessibility": {"counts_by_component": None},
                        "activities": {"counts_by_category": {}},
                    },
                    "preferences": {},
                }
            ]
        }
    )

    by_criterion = {s["criterion"]: s for s in scores}
    # No count was obtained, so no score may be justified by a count.
    assert all(s["score"] != 0.0 for s in scores)
    assert not any("count (0)" in s["rationale"] for s in scores)
    # Prose is evidence and is read as such, clearly labelled.
    assert by_criterion["transportation"]["score"] == 0.4
    assert "descriptive rather than counted" in by_criterion["transportation"]["rationale"]
    # Nothing at all was collected for these two, so they stay unassessed.
    assert "accessibility" not in by_criterion
    assert "activities" not in by_criterion


def test_genuine_zero_counts_still_score_as_evidence_of_absence():
    from app.llm.mock import score_unresolved_mock

    scores = score_unresolved_mock(
        {
            "candidates": [
                {
                    "place": "Remoteville",
                    "criteria": {
                        "transportation": {
                            "counts_by_component": {
                                "bus_stops": 0,
                                "rail_metro_tram_stations": 0,
                                "pedestrian_ways": 0,
                                "cycleways": 0,
                            }
                        }
                    },
                    "preferences": {},
                }
            ]
        }
    )

    assert len(scores) == 1
    assert scores[0]["criterion"] == "transportation"
    assert scores[0]["score"] == 0.0
    assert "count (0)" in scores[0]["rationale"]
