import json

import pytest

from app.agent.models import CandidateEvaluation, PlaceRequestProfile, ValidationResult
from app.agent.recommendation_generator import (
    _candidate_detail_sections_complete,
    _complete_candidate_detail_sections,
    generate_recommendation,
)
from app.core.rendering import render_recommendation_markdown
from app.llm.base import LLMRawResponse


class _Budget:
    async def check_before_call(self, *args, **kwargs):
        return None

    async def record_call(self, *args, **kwargs):
        return None


class _ScriptedClient:
    def __init__(self, markdown: str):
        self.markdown = markdown
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        self.calls += 1
        return LLMRawResponse(
            text=json.dumps({"markdown": self.markdown}),
            input_tokens=10,
            output_tokens=100,
        )


def _candidate(rank: int, place: str, country: str = "Testland") -> dict:
    return {
        "place": place,
        "country": country,
        "total_score": 1.0 - rank * 0.01,
        "confidence_score": 0.82 if rank <= 3 else 0.58,
        "criterion_scores": {"cost": 0.8, "transportation": 0.7},
        "hard_constraint_results": {"cost": "verified"},
        "advantages": [f"Deterministic advantage for {place}."],
        "drawbacks": [f"Deterministic drawback for {place}."],
        "missing_evidence": [],
    }


def _payload() -> dict:
    places = [
        ("Warsaw", "Poland"),
        ("Valencia", "Spain"),
        ("Berlin", "Germany"),
        ("Vienna", "Austria"),
        ("Hamburg", "Germany"),
        ("Copenhagen", "Denmark"),
        ("Helsinki", "Finland"),
        ("Barcelona", "Spain"),
    ]
    return {
        "purpose_summary": "a remote work request",
        "assumptions": [],
        "validation_issues": [],
        "sources": [],
        "candidates": [
            _candidate(rank, place, country)
            for rank, (place, country) in enumerate(places, start=1)
        ],
    }


def _table(payload: dict) -> str:
    lines = [
        "## Best matches",
        "",
        "| Rank | Place | Why it fits | Main drawback | Confidence |",
        "|---:|---|---|---|---|",
    ]
    for rank, candidate in enumerate(payload["candidates"], start=1):
        lines.append(f"| {rank} | {candidate['place']} | LLM why | LLM drawback | Medium |")
    return "\n".join(lines)


def _llm_section(rank: int, place: str, country: str) -> str:
    return (
        f"### {rank}. {place}, {country}\n\n"
        f"- Why it fits: Original LLM section for {place}.\n"
        f"- Relevant evidence: Original cited prose for {place}.\n"
        f"- Budget fit: Original budget wording for {place}.\n"
        f"- Main trade-off: Original trade-off for {place}.\n"
        "- Confidence: Medium, because the LLM said so."
    )


def _llm_markdown(payload: dict, ranks: list[int], *, rank_4_title: str | None = None) -> str:
    sections = []
    for rank in ranks:
        candidate = payload["candidates"][rank - 1]
        place = candidate["place"]
        country = candidate["country"]
        if rank == 4 and rank_4_title is not None:
            sections.append(_llm_section(rank, rank_4_title, "Germany"))
        else:
            sections.append(_llm_section(rank, place, country))
    return (
        _table(payload)
        + "\n\n"
        + "\n\n".join(sections)
        + "\n\n## Trade-offs discussion\n\nOriginal trade-off text stays here."
        + "\n\n## Sources\n\nOriginal source section stays here."
    )


def test_complete_response_remains_unchanged():
    payload = _payload()
    markdown = render_recommendation_markdown(payload)

    repaired = _complete_candidate_detail_sections(markdown, payload)

    assert repaired == markdown
    assert _candidate_detail_sections_complete(repaired, payload)


def test_one_missing_section_is_generated_without_touching_valid_sections():
    payload = _payload()
    markdown = _llm_markdown(payload, list(range(1, 8)))

    repaired = _complete_candidate_detail_sections(markdown, payload)

    assert "Original LLM section for Warsaw." in repaired
    assert "Original LLM section for Helsinki." in repaired
    assert "Original LLM section for Barcelona." not in repaired
    assert "### 8. Barcelona, Spain" in repaired
    assert "Deterministic advantage for Barcelona." in repaired
    assert _candidate_detail_sections_complete(repaired, payload)


def test_several_missing_sections_are_generated_in_rank_order():
    payload = _payload()
    markdown = _llm_markdown(payload, [1, 2, 3])

    repaired = _complete_candidate_detail_sections(markdown, payload)

    assert "Original LLM section for Warsaw." in repaired
    assert "Original LLM section for Valencia." in repaired
    assert "Original LLM section for Berlin." in repaired
    assert "Deterministic advantage for Vienna." in repaired
    assert "Deterministic advantage for Barcelona." in repaired
    assert repaired.index("### 3. Berlin, Germany") < repaired.index("### 4. Vienna, Austria")
    assert repaired.index("### 8. Barcelona, Spain") < repaired.index("## Trade-offs discussion")
    assert _candidate_detail_sections_complete(repaired, payload)


def test_wrong_city_at_rank_is_repaired_with_authoritative_candidate():
    payload = _payload()
    markdown = _llm_markdown(payload, list(range(1, 9)), rank_4_title="Hamburg")

    repaired = _complete_candidate_detail_sections(markdown, payload)

    assert "### 4. Hamburg, Germany" not in repaired
    assert "### 4. Vienna, Austria" in repaired
    assert "Deterministic advantage for Vienna." in repaired
    assert "Original LLM section for Hamburg." in repaired
    assert _candidate_detail_sections_complete(repaired, payload)


def test_later_response_content_is_preserved_after_insertion():
    payload = _payload()
    markdown = _llm_markdown(payload, [1, 2, 3])
    later_content = (
        "## Trade-offs discussion\n\nOriginal trade-off text stays here.\n\n"
        "## Sources\n\nOriginal source section stays here."
    )

    repaired = _complete_candidate_detail_sections(markdown, payload)

    assert later_content in repaired


@pytest.mark.asyncio
async def test_generator_repairs_missing_sections_without_another_llm_call():
    payload = _payload()
    client = _ScriptedClient(_llm_markdown(payload, [1, 2, 3]))
    evaluations = [CandidateEvaluation.model_validate(candidate) for candidate in payload["candidates"]]

    markdown = await generate_recommendation(
        PlaceRequestProfile(purpose="remote_work"),
        evaluations,
        ValidationResult(approved=True),
        [],
        client=client,
        budget=_Budget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
        max_final_recommendations=8,
    )

    assert client.calls == 1
    assert "Original LLM section for Warsaw." in markdown
    assert "Original LLM section for Berlin." in markdown
    assert "Deterministic advantage for Vienna." in markdown
    assert "Deterministic advantage for Barcelona." in markdown
    assert _candidate_detail_sections_complete(markdown, payload)


@pytest.mark.asyncio
async def test_repair_failure_uses_full_deterministic_fallback(monkeypatch):
    from app.agent import recommendation_generator as generator_module

    payload = _payload()
    client = _ScriptedClient(_llm_markdown(payload, [1, 2, 3]))
    evaluations = [CandidateEvaluation.model_validate(candidate) for candidate in payload["candidates"]]

    monkeypatch.setattr(generator_module, "_deterministic_candidate_detail_sections", lambda payload: {})

    markdown = await generate_recommendation(
        PlaceRequestProfile(purpose="remote_work"),
        evaluations,
        ValidationResult(approved=True),
        [],
        client=client,
        budget=_Budget(),
        request_id="r1",
        execution_trace=[],
        max_output_tokens=500,
        max_final_recommendations=8,
    )

    assert client.calls == 1
    assert "Generated using:** a deterministic fallback template" in markdown
    assert "limited automated summary" in markdown
    assert "### 8. Barcelona, Spain" in markdown
