"""D54: a truncated answer wasted both attempts and fell back to the template.

The Recommendation Generator's JSON ran past the output ceiling and stopped
mid-string. The repair attempt asked only for "valid JSON", so the model
produced the same over-long answer, hit the same ceiling, and failed
identically -- P08 lost its written answer that way, and P01/P03/P05 needed a
retry for the same reason the day before.
"""

import json

import pytest
from pydantic import BaseModel

from app.llm.base import BaseLLMClient, LLMRawResponse
from app.llm.traced_client import traced_llm_call


class _Budget:
    async def check_before_call(self, request_id, module, est_input, est_output):
        return None

    async def record_call(self, request_id, module, model, input_tokens, output_tokens, cost, success):
        return None


class _Schema(BaseModel):
    markdown: str


class _TruncatingClient(BaseLLMClient):
    """Returns a cut-off answer first, then a valid one. Records what it was told."""

    def __init__(self):
        self.repair_instructions: list[str] = []
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens, metadata=None):
        self.calls += 1
        if self.calls > 1:
            self.repair_instructions.append(messages[-1]["content"])
            return LLMRawResponse(
                text=json.dumps({"markdown": "short"}), input_tokens=5, output_tokens=5
            )
        return LLMRawResponse(
            text='{"markdown":"## Best matches\\nRank 1 is Copenhagen and the evidence sh',
            input_tokens=5,
            output_tokens=5,
        )


@pytest.mark.asyncio
async def test_the_repair_asks_for_a_shorter_answer_not_just_valid_json():
    client = _TruncatingClient()
    trace: list[dict] = []

    result = await traced_llm_call(
        module_name="Recommendation Generator",
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        execution_trace=trace,
        client=client,
        budget=_Budget(),
        request_id="r1",
        max_output_tokens=8000,
        response_model=_Schema,
    )

    assert result == {"markdown": "short"}
    instruction = client.repair_instructions[0]
    # The wording may change; asking for a shorter answer when the response was
    # cut off is the behaviour D54 exists to protect.
    assert "shorter" in instruction
    assert "stopped before it finished" in instruction or "cut off" in instruction


@pytest.mark.asyncio
async def test_the_truncated_attempt_stays_visible_in_the_trace():
    """The degradation has to stay recordable, not become silent."""
    client = _TruncatingClient()
    trace: list[dict] = []

    await traced_llm_call(
        module_name="Recommendation Generator",
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        execution_trace=trace,
        client=client,
        budget=_Budget(),
        request_id="r1",
        max_output_tokens=8000,
        response_model=_Schema,
    )

    assert any(step["response"].get("error") == "malformed_json" for step in trace)


def test_the_output_ceiling_leaves_room_for_the_largest_answers():
    """4000 was below what an eight-candidate answer with a full bibliography
    needs, and the shortfall showed up as a silent fallback rather than as
    anything the reader could see."""
    from app.core.config import Settings

    assert Settings().llm_max_output_tokens >= 8000
