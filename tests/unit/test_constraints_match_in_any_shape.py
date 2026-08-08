"""The interpreter does not reliably send prose, and matching must not care.

One run stated P01's limits as "budget no more than EUR 1,800 per month all-in"
and "must be genuinely livable without a car". The next sent
`budget_max_1800_eur_month_all_in` and `must_be_livable_without_a_car`.

Every keyword is matched on word boundaries and `\w` includes the underscore,
so `\bbudget\b` cannot match inside `budget_max_...`. That failed every lookup
at once: both hard limits were recorded as unchecked and, since D70 discloses
those, the answer opened by telling the traveller neither could be checked --
then ranked the eight places by budget fit and car-free living.
"""

import pytest

from app.agent.dynamic_evaluation import _check_hard_constraints, criteria_for_constraint
from app.agent.models import CandidatePlace, PlaceRequestProfile

CANDIDATE = CandidatePlace(place_name="Seville", country="Spain", reason_for_inclusion="x")


@pytest.mark.parametrize(
    ("underscored", "prose", "expected"),
    [
        ("budget_max_1800_eur_month_all_in", "budget no more than 1800 per month", ["cost"]),
        ("must_be_livable_without_a_car", "must be livable without a car", ["transportation"]),
        ("car_independence", "car independence", ["transportation"]),
        ("english_taught_courses", "English-taught courses", ["language_spoken"]),
        ("reasonably_flat_terrain", "reasonably flat terrain", ["terrain"]),
        ("flight_time_under_5_hours", "flight time under 5 hours", ["flight_duration"]),
    ],
)
def test_the_two_shapes_reach_the_same_criterion(underscored, prose, expected):
    assert criteria_for_constraint(underscored) == expected
    assert criteria_for_constraint(prose) == expected


def test_measured_limits_are_not_reported_as_uncheckable():
    """The whole failure in one assertion: P01's profile as the interpreter
    actually sent it, against criteria that were measured."""
    profile = PlaceRequestProfile(
        purpose="remote_work",
        hard_constraints=["budget_max_1800_eur_month_all_in", "must_be_livable_without_a_car"],
    )

    _, _, results = _check_hard_constraints(
        profile, {"cost": 0.9, "transportation": 0.85}, CANDIDATE, []
    )

    assert results == {"cost": True, "transportation": True}


def test_an_underscored_requirement_nothing_measures_is_still_recorded():
    """Normalising must not make unmatched requirements vanish again (D61)."""
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=["quick_access_to_a_hospital"])

    _, _, results = _check_hard_constraints(profile, {"cost": 0.9}, CANDIDATE, [])

    assert results.get("quick access to a hospital") is None


def test_the_recorded_wording_carries_no_identifiers():
    """It is shown to the reader, so it reads as words either way (D42)."""
    profile = PlaceRequestProfile(purpose="vacation", hard_constraints=["quick_access_to_a_hospital"])

    _, _, results = _check_hard_constraints(profile, {}, CANDIDATE, [])

    assert not any("_" in key for key in results)
