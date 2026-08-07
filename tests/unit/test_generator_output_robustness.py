"""A usable answer must not be thrown away over the wrapper it arrived in.

P13 and P14 fell back to the deterministic template on 2026-08-07. The cause was
assumed to be the 8,000-token output ceiling; measured against the real provider
the same two prompts produced 3,542 and 3,234 output tokens and succeeded first
time. Length was never the problem.

What the evidence did show was a response that parsed as valid JSON and was
rejected anyway, because the schema forbade extra keys and the model had
volunteered one. Three ways an answer was being discarded, none of them about
its content: an extra field, a code fence, and a repair that assumed length.
"""

import json

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.recommendation_generator import _RecommendationOutput
from app.llm.base import LLMRawResponse
from app.llm.traced_client import _looks_truncated, _malformed_json_repair, traced_llm_call


class _Budget:
    async def check_before_call(self, *a, **k):
        return None

    async def record_call(self, *a, **k):
        return None


class _Schema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    markdown: str


class _ScriptedClient:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.repair_instructions: list[str] = []
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        if self.calls:
            self.repair_instructions.append(messages[-1]["content"])
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return LLMRawResponse(text=text, input_tokens=5, output_tokens=3000)


async def _run(client) -> dict:
    return await traced_llm_call(
        module_name="Recommendation Generator",
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        execution_trace=[],
        client=client,
        budget=_Budget(),
        request_id="r1",
        max_output_tokens=8000,
        response_model=_Schema,
    )


def test_an_extra_field_does_not_discard_the_answer():
    """P13 exactly: valid JSON, complete answer, one key nothing reads."""
    parsed = _RecommendationOutput.model_validate(
        {"markdown": "## Best matches\n...", "notes": "ignore me", "confidence": 0.8}
    )
    assert parsed.markdown.startswith("## Best matches")


@pytest.mark.asyncio
async def test_an_extra_field_survives_the_whole_call():
    client = _ScriptedClient(json.dumps({"markdown": "the answer", "sources_used": [1, 2]}))
    assert await _run(client) == {"markdown": "the answer"}
    assert client.calls == 1, "a usable answer must not cost a repair attempt"


@pytest.mark.asyncio
async def test_a_fenced_answer_is_read_rather_than_rejected():
    """```json fences are the commonest thing a model wraps an object in."""
    body = json.dumps({"markdown": "fenced answer"})
    client = _ScriptedClient(f"```json\n{body}\n```")
    assert await _run(client) == {"markdown": "fenced answer"}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_a_sentence_before_the_object_is_tolerated():
    body = json.dumps({"markdown": "prefixed answer"})
    client = _ScriptedClient(f"Here is the recommendation you asked for:\n{body}")
    assert await _run(client) == {"markdown": "prefixed answer"}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_genuinely_broken_json_still_fails_and_still_gets_a_repair():
    """The salvage must not become a licence to guess at content."""
    client = _ScriptedClient('{"markdown": "unterminated', json.dumps({"markdown": "repaired"}))
    assert await _run(client) == {"markdown": "repaired"}
    assert client.calls == 2


def test_a_short_response_that_will_not_parse_is_not_called_truncated():
    """P13/P14: 3,234-3,542 tokens against a ceiling of 8,000.

    Telling the model its answer was too long, when it was a third of the
    ceiling, spends the one repair attempt on the wrong problem.
    """
    raw = LLMRawResponse(text='{"markdown": "done"}', input_tokens=5, output_tokens=3234)
    assert not _looks_truncated(raw, 8000)

    instruction = _malformed_json_repair(raw, 8000, ValueError("Expecting ',' delimiter"))
    assert "do not shorten" in instruction
    assert "escaped" in instruction
    assert "shorter" not in instruction.replace("do not shorten", "")


def test_a_response_at_the_ceiling_is_called_truncated():
    raw = LLMRawResponse(text='{"markdown": "cut', input_tokens=5, output_tokens=8000)
    assert _looks_truncated(raw, 8000)
    assert "shorter" in _malformed_json_repair(raw, 8000, ValueError("boom"))


def test_an_unclosed_object_is_truncated_whatever_the_token_count():
    """A response can stop early for reasons other than the ceiling."""
    raw = LLMRawResponse(text='{"markdown": "stopped mid', input_tokens=5, output_tokens=120)
    assert _looks_truncated(raw, 8000)
