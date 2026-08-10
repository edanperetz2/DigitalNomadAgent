"""`/api/agent_info` serves real captured answers, and survives losing them.

The brief asks `prompt_examples[].full_response` to be "the full response your
agent returns". It is: `assets/prompt_examples.json` holds answers captured from
a live run against the deployment. Because a required endpoint must not depend
on an asset staying well-formed, the loader falls back to generated examples
rather than raising -- these tests cover both directions.
"""

import json

import pytest

from app.api import agent_info_content
from app.api.schemas import PromptExample
from app.core.module_names import LLM_CALLING_MODULES


def test_the_committed_asset_exists_and_is_what_ships():
    assert agent_info_content.CAPTURED_EXAMPLES_PATH.exists()
    assert agent_info_content._load_captured_examples() is not None


def test_every_committed_example_validates_against_the_response_model():
    for example in agent_info_content._load_captured_examples():
        PromptExample.model_validate(example)


def test_committed_examples_are_real_answers_not_stubs():
    for example in agent_info_content._load_captured_examples():
        assert len(example["full_response"]) > 500
        assert example["steps"], "an example must show the trace that produced it"
        for step in example["steps"]:
            assert step["module"] in LLM_CALLING_MODULES
            assert set(step["prompt"]) == {"System_prompt", "User_prompt"}


def test_committed_examples_come_from_the_real_provider():
    """A mock-generated answer would defeat the point of capturing them."""
    for example in agent_info_content._load_captured_examples():
        assert "a real LLM provider" in example["full_response"]


@pytest.mark.parametrize(
    "payload",
    [
        None,  # file absent
        "{ not json",
        json.dumps({}),  # no prompt_examples key
        json.dumps({"prompt_examples": []}),  # empty
        json.dumps({"prompt_examples": [{"prompt": "x"}]}),  # wrong shape
        json.dumps({"prompt_examples": [{"prompt": "x", "full_response": "y", "steps": [{}]}]}),
    ],
)
def test_a_broken_asset_falls_back_instead_of_breaking_the_endpoint(tmp_path, monkeypatch, payload):
    path = tmp_path / "prompt_examples.json"
    if payload is not None:
        path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(agent_info_content, "CAPTURED_EXAMPLES_PATH", path)
    assert agent_info_content._load_captured_examples() is None


def test_the_fallback_builder_still_produces_a_usable_example():
    """The safety net has to actually work, not merely exist."""
    example = agent_info_content._build_example("I want to work remotely from Europe for a month.")
    PromptExample.model_validate(example)
    assert example["full_response"]
