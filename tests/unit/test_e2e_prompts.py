"""Sanity checks on the evaluation prompt set itself.

The prompt set is a deliverable the team agreed on, so it is worth guarding
against silent drift: IDs must stay stable (results are compared across runs by
ID), and the set must keep its intended shape.
"""

import pytest

from scripts.e2e.prompts import CONTRACT_CHECKS, E2E_PROMPTS, get_prompt


def test_prompt_ids_are_unique():
    ids = [p.id for p in E2E_PROMPTS]
    assert len(ids) == len(set(ids))


def test_prompt_ids_are_stable_and_sequential():
    """Results are keyed by ID across four configurations -- renumbering breaks comparison."""
    assert [p.id for p in E2E_PROMPTS] == [f"P{n:02d}" for n in range(1, 11)]


def test_set_keeps_its_agreed_composition():
    counts = {"mainstream": 0, "edge": 0}
    for prompt in E2E_PROMPTS:
        counts[prompt.category] += 1
    assert counts == {"mainstream": 6, "edge": 4}


@pytest.mark.parametrize("prompt", E2E_PROMPTS, ids=lambda p: p.id)
def test_every_prompt_is_realistically_detailed(prompt):
    """These are deliberately paragraph-length, not one-line specs."""
    assert len(prompt.prompt) > 200, f"{prompt.id} is too terse to be representative"
    assert prompt.focus, f"{prompt.id} must record why it is in the set"
    assert prompt.title


@pytest.mark.parametrize("prompt", E2E_PROMPTS, ids=lambda p: p.id)
def test_prompts_stay_within_the_api_length_limit(prompt):
    from app.core.config import get_settings

    assert len(prompt.prompt) <= get_settings().max_prompt_length


def test_contract_check_ids_are_unique():
    ids = [c[0] for c in CONTRACT_CHECKS]
    assert len(ids) == len(set(ids))


def test_contract_checks_cover_both_sides_of_the_length_limit():
    from app.core.config import get_settings

    limit = get_settings().max_prompt_length
    lengths = {len(v) for _, _, v in CONTRACT_CHECKS if isinstance(v, str)}
    assert limit in lengths, "missing a prompt exactly at the limit"
    assert limit + 1 in lengths, "missing a prompt one over the limit"


def test_get_prompt_returns_the_requested_prompt():
    assert get_prompt("P07").category == "edge"


def test_get_prompt_rejects_an_unknown_id():
    with pytest.raises(KeyError):
        get_prompt("P99")
