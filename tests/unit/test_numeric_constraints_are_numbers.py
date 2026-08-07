"""D64: a stated number is carried as a number, not re-read out of a sentence.

P14 capped flying at ten hours. The interpreter recorded that as
`travel_time_under_10_hours`; `_stated_flight_hours` gates on the words "flight"
or "flying", matched neither, and returned None -- so no ceiling was applied and
Lisbon, roughly 24 hours from Melbourne, ranked first having paid nothing for it.

Written as "no more than ten hours of flying" the identical requirement parses
fine, which is the whole problem: the same request succeeded or failed depending
on how the model happened to phrase it that run. `budget` never had this
weakness because it is asked for as a number. These two now are as well.
"""

import pytest

from app.agent.dynamic_evaluation import _stated_flight_hours, _stated_overlap_hours
from app.agent.models import PlaceRequestProfile
from app.llm.mock import interpret_prompt
from scripts.e2e.prompts import get_prompt


def _profile(**kwargs) -> PlaceRequestProfile:
    return PlaceRequestProfile(purpose=kwargs.pop("purpose", "remote_work"), **kwargs)


def test_the_number_survives_a_phrasing_the_parser_cannot_read():
    """P14 exactly: the field carries it even though the sentence is unreadable."""
    profile = _profile(
        max_flight_hours=10.0,
        hard_constraints=["travel_time_under_10_hours", "must_have_reliable_internet"],
    )
    assert _stated_flight_hours(profile) == 10.0


def test_without_the_field_that_phrasing_still_yields_nothing():
    """Documents the bug the field exists to route around."""
    profile = _profile(hard_constraints=["travel_time_under_10_hours"])
    assert _stated_flight_hours(profile) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "no more than five hours of flying",
        "flight time under 5 hours",
        "anything over five hours in the air is too much",
    ],
)
def test_prose_still_works_when_no_number_was_supplied(phrase):
    """The parser stays as a fallback -- nothing that worked before stops working."""
    assert _stated_flight_hours(_profile(hard_constraints=[phrase])) == 5.0


def test_the_field_wins_over_the_prose():
    """If both are present the number is authoritative; it is what was asked for."""
    profile = _profile(
        max_flight_hours=8.0,
        hard_constraints=["no more than five hours of flying"],
    )
    assert _stated_flight_hours(profile) == 8.0


def test_timezone_overlap_gets_the_same_treatment():
    assert _stated_overlap_hours(_profile(min_timezone_overlap_hours=4.0)) == 4.0
    assert (
        _stated_overlap_hours(
            _profile(hard_constraints=["at least four hours of overlap with US Eastern"])
        )
        == 4.0
    )


def test_no_number_stated_means_no_ceiling_invented():
    profile = _profile(hard_constraints=["must be liveable without a car"])
    assert _stated_flight_hours(profile) is None
    assert _stated_overlap_hours(profile) is None


@pytest.mark.parametrize(
    ("prompt_id", "flight", "overlap"),
    [
        ("P02", 5.0, None),   # "anything over five hours and the younger one falls apart"
        ("P05", None, 4.0),   # "at least four hours of overlap with US Eastern"
        ("P14", 10.0, None),  # "I'd rather not fly more than about ten hours"
        ("P01", None, None),  # states no numeric limit of either kind
        ("P11", None, None),  # "two hours of UK time" is an offset, not an overlap
    ],
)
def test_the_offline_parser_fills_the_same_fields(prompt_id, flight, overlap):
    """The mock path has to produce the same shape of profile as the real one."""
    parsed = interpret_prompt(get_prompt(prompt_id).prompt)
    assert parsed["max_flight_hours"] == flight
    assert parsed["min_timezone_overlap_hours"] == overlap


def test_the_fields_reject_nonsense():
    with pytest.raises(ValueError):
        _profile(max_flight_hours=0)
    with pytest.raises(ValueError):
        _profile(min_timezone_overlap_hours=-1)
