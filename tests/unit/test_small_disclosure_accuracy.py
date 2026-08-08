"""Two ways a disclosure told the reader something untrue.

P08 resolved "this winter" to [12, 1, 2] and scored every candidate against it,
then listed "this winter" as a requirement nothing could check -- trip timing
reported as a property of a place. P07 printed "ease for first-time travellers"
and "ease for first time travellers" as two separate gaps, one hyphen apart.
"""

from app.agent.dynamic_evaluation import _is_about_the_trip_not_the_place
from app.agent.recommendation_generator import _coverage_disclosure, _distinct


def test_a_season_is_when_the_trip_is_not_what_a_place_is():
    for phrase in ("this winter", "next spring", "late summer", "early autumn"):
        assert _is_about_the_trip_not_the_place(phrase), phrase


def test_trip_length_is_still_recognised():
    """The existing cases must keep working."""
    for phrase in ("one month", "ten days in October", "six months"):
        assert _is_about_the_trip_not_the_place(phrase), phrase


def test_a_real_requirement_is_not_swallowed_as_trip_shape():
    for phrase in ("walkable city", "quick access to a hospital", "reasonably flat terrain"):
        assert not _is_about_the_trip_not_the_place(phrase), phrase


def test_two_spellings_of_one_gap_are_reported_once():
    assert _distinct(
        ["ease for first-time travellers", "ease for first time travellers"]
    ) == ["ease for first-time travellers"]


def test_genuinely_different_gaps_are_both_kept():
    assert _distinct(["food scene", "street food", "market culture"]) == [
        "food scene",
        "street food",
        "market culture",
    ]


def test_the_disclosure_prints_the_gap_once():
    disclosure = _coverage_disclosure(
        ["ease for first-time travellers", "ease for first time travellers"]
    )

    assert disclosure.count("ease for first") == 1
