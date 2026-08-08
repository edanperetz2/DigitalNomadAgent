"""The generator has to know what was asked before it can answer it.

Its payload carried `purpose_summary` -- built as "a {purpose} request", so
literally "a vacation request" -- plus assumptions, sources and scored
candidates. Nothing about the person asking. Its own system prompt tells it to
open with an interpretation of the request and to acknowledge "burnout, a first
trip, a disability, small children", which it could not do. P02's answer never
mentioned the 6- and 9-year-olds or the five-hour flight cap; P04's never
mentioned safety, the stated top priority; P06's never used the word wheelchair;
and P01's opened by asserting the traveller wanted "English-only day-to-day",
reasoning backwards from drawbacks it had been handed.
"""

import json

from app.agent.models import CandidateEvaluation, PlaceRequestProfile, ValidationResult
from app.agent.recommendation_generator import _build_payload, _llm_payload, _priorities_in_order

P02_TEXT = (
    "We're a family of four flying out of Tel Aviv for two weeks in August. Our kids are "
    "6 and 9, so we need somewhere with a real beach they can actually swim at. Flight "
    "time is the big constraint: anything over five hours and the younger one falls apart."
)

P02_PROFILE = PlaceRequestProfile(
    purpose="vacation",
    target_months=[8],
    origin="Tel Aviv",
    max_flight_hours=5.0,
    hard_constraints=[
        "must be within 5 flight hours from Tel Aviv",
        "must have a real swimmable beach",
    ],
    soft_preferences=["aquariums", "science museums"],
    deal_breakers=["flight time over 5 hours"],
    relevant_criteria=["flight_duration", "beach_quality", "family_friendliness"],
    inferred_weights={"flight_duration": 1.0, "beach_quality": 0.95, "family_friendliness": 0.9},
)


def _payload(profile=P02_PROFILE, text=P02_TEXT):
    return _build_payload(
        profile,
        [CandidateEvaluation(place="Rhodes", country="Greece", criterion_scores={"cost": 0.7})],
        ValidationResult(approved=True),
        [],
        3,
        text,
    )


def test_the_request_reaches_the_generator_verbatim():
    """No structured field holds "our kids are 6 and 9", and that is exactly
    what the answer has to be written for."""
    assert "6 and 9" in _payload()["stated_request"]["in_their_own_words"]


def test_the_stated_non_negotiables_reach_the_generator():
    stated = _payload()["stated_request"]["stated_as_non_negotiable"]

    assert "must be within 5 flight hours from Tel Aviv" in stated


def test_what_the_traveller_asked_to_avoid_reaches_the_generator():
    assert _payload()["stated_request"]["asked_to_avoid"] == ["flight time over 5 hours"]


def test_priorities_arrive_in_order_highest_first():
    assert _priorities_in_order(P02_PROFILE) == [
        "flight duration",
        "beach quality",
        "family friendliness",
    ]


def test_priorities_carry_no_identifiers():
    """`nighttime_safety` is an identifier, not something anyone says (D42)."""
    for words in _priorities_in_order(P02_PROFILE):
        assert "_" not in words


def test_distinct_priorities_are_not_collapsed_onto_one_criterion():
    """P01 weighted internet above coworking and both canonicalize to
    `work_infrastructure`, so mapping first would have shown the reader the
    higher priority under the lower one's name."""
    profile = P02_PROFILE.model_copy(
        update={"inferred_weights": {"internet_quality": 0.9, "coworking_availability": 0.7}}
    )

    assert _priorities_in_order(profile) == ["internet quality", "coworking availability"]


def test_something_the_traveller_does_not_care_about_is_not_a_priority():
    """"I don't care about nightlife at all" arrives as weight 0.0 (D53)."""
    profile = P02_PROFILE.model_copy(
        update={"inferred_weights": {"safety": 1.0, "nightlife": 0.0}}
    )

    assert _priorities_in_order(profile) == ["safety"]


def test_no_weight_value_reaches_the_payload():
    """Handed a decimal, the generator prints it (D41). The order is the
    meaning; the numbers behind it are working notes."""
    serialized = json.dumps(_payload()["stated_request"])

    for weight in ("1.0", "0.95", "0.9"):
        assert weight not in serialized


def test_the_key_the_model_sees_reads_as_language():
    """The model echoes the keys it is given (D42)."""
    presented = _llm_payload(_payload())

    assert "what_the_traveller_asked_for" in presented
    assert "stated_request" not in presented


def test_a_profile_with_nothing_stated_adds_nothing():
    """The block is absent rather than empty, so a bare request does not gain a
    section promising detail it has not got."""
    bare = PlaceRequestProfile(purpose="vacation")

    assert "stated_request" not in _build_payload(bare, [], ValidationResult(approved=True), [], 3, "")


def test_sources_reach_the_model_already_numbered():
    """Unnumbered, the model assigns its own numbers to as many as 70 entries
    and drifts: P03 cited Sofia's transport to a travel advisory and its
    language note to another city's geocoder record."""
    payload = _build_payload(
        P02_PROFILE,
        [],
        ValidationResult(approved=True),
        [{"source_name": "Wikivoyage Get around"}, {"source_name": "UK FCDO"}],
        3,
        "",
    )

    assert [s["number"] for s in _llm_payload(payload)["sources"]] == [1, 2]


def test_the_numbering_matches_the_deterministic_renderer():
    """Both paths number the same list, so a citation means the same thing
    whichever wrote the answer."""
    sources = [{"source_name": f"source {i}"} for i in range(5)]
    payload = _build_payload(P02_PROFILE, [], ValidationResult(approved=True), sources, 3, "")

    presented = _llm_payload(payload)["sources"]

    assert [s["number"] for s in presented] == list(range(1, len(sources) + 1))
    assert [s["source_name"] for s in presented] == [s["source_name"] for s in payload["sources"]]
