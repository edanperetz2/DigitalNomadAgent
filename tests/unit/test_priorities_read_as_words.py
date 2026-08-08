"""The traveller's priorities are reported in words, never as identifiers.

The coverage block echoes `relevant_criteria` as the interpreter wrote it, and
it does not reliably write prose: one run produced "food scene", the next
`food_scene`. Harmless while the block sat under the bibliography; D68 moved it
to the top, so P04 opened with `food_scene`, `street_food`, `market_culture`,
`party_scene` and P01 with `city_size` (D42).
"""

from app.agent.dynamic_evaluation import universally_unmeasured_priorities
from app.agent.models import CandidateEvaluation, PlaceRequestProfile
from app.agent.recommendation_generator import _coverage_disclosure

DELIVERED = [CandidateEvaluation(place="Seoul", country="South Korea", criterion_scores={"safety": 0.9})]


def _unmeasured(relevant, weights):
    profile = PlaceRequestProfile(
        purpose="vacation", relevant_criteria=relevant, inferred_weights=weights
    )
    return universally_unmeasured_priorities(profile, DELIVERED)


def test_underscored_criteria_are_reported_as_words():
    words = _unmeasured(
        ["food_scene", "street_food", "market_culture", "party_scene"],
        {"food_scene": 0.8, "street_food": 0.7, "market_culture": 0.7, "party_scene": 0.0},
    )

    assert words == ["food scene", "street food", "market culture", "party scene"]


def test_no_identifier_survives_into_the_answer():
    disclosure = _coverage_disclosure(_unmeasured(["city_size"], {"city_size": 0.5}))

    assert "city_size" not in disclosure
    assert "city size" in disclosure


def test_prose_the_traveller_gave_is_left_alone():
    """Where the interpreter does write words, they are the traveller's and are
    not reworded."""
    assert _unmeasured(["a genuinely active student scene"], {"student_life": 0.9}) == [
        "a genuinely active student scene"
    ]


def test_two_spellings_of_one_priority_print_once():
    """Humanising can make near-duplicates identical; the disclosure collapses
    them on what the reader sees."""
    disclosure = _coverage_disclosure(
        ["ease for first-time traveller", "ease for first time traveller", "social scene"]
    )

    assert disclosure.count("ease for first") == 1
    assert disclosure.count("social scene") == 1
