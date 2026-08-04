from app.agent.request_interpreter import SYSTEM_PROMPT
from app.llm.mock import generate_candidates, interpret_prompt


def test_remote_work_prompt_detected():
    profile = interpret_prompt(
        "I want to spend three months somewhere in Europe where I can work remotely, "
        "live without a car, and stay within €1,800 per month."
    )
    assert profile["purpose"] == "remote_work"
    assert profile["budget"]["amount"] == 1800.0
    assert profile["budget"]["currency"] == "EUR"
    assert profile["budget"]["period"] == "monthly"
    assert "car-free" in profile["mobility_requirements"]
    assert profile["clarification_required"] is False


def test_ordinary_remote_phrasings_are_recognised():
    """Purpose detection used bare substrings, so "work fully remote" matched
    none of "remote work"/"work remotely"/"remote job" and the whole profile
    was discarded."""
    for phrasing in (
        "I've been cleared to work fully remote for three months.",
        "I want to work from home abroad for three months.",
        "Looking for a remote-first work base for a few months.",
        "I'm a digital nomad looking for a base.",
    ):
        assert interpret_prompt(phrasing)["purpose"] == "remote_work", phrasing


def test_an_unrecognised_purpose_still_extracts_every_stated_constraint():
    """The regression that made five of ten evaluation prompts return identical
    output: an unmatched purpose returned a stub, throwing away the budget,
    region and constraints the user had actually stated."""
    profile = interpret_prompt(
        "I'm looking for somewhere in Scandinavia for a month this winter. I don't want "
        "to spend more than $400 per month including accommodation, and I won't have a "
        "car, so everything needs to be walkable."
    )

    assert profile["purpose"] == "unknown"
    assert profile["clarification_required"] is True
    # ...but nothing the user said is lost.
    assert profile["budget"]["amount"] == 400.0
    assert profile["budget"]["period"] == "monthly"
    assert profile["preferred_regions"] == ["Scandinavia"]
    assert "car-free" in profile["mobility_requirements"]


def test_a_named_destination_is_captured_separately_from_regions():
    """"Is Lisbon a good fit?" previously put the city in preferred_regions,
    which is only matched against a candidate's country -- so it matched
    nothing, was dropped, and the answer never mentioned Lisbon."""
    profile = interpret_prompt(
        "I've more or less settled on Lisbon for six months of remote work. Is it a good fit?"
    )

    assert profile["named_destinations"] == ["Lisbon"]
    assert "Lisbon" not in profile["preferred_regions"]


def test_named_destination_phrasings():
    for prompt, expected in (
        ("Is Berlin actually a good fit for me?", ["Berlin"]),
        ("How about Valencia?", ["Valencia"]),
        ("Thinking about Porto for the winter.", ["Porto"]),
    ):
        assert interpret_prompt(prompt)["named_destinations"] == expected, prompt


def test_ordinary_prose_is_not_read_as_a_named_destination():
    assert interpret_prompt("I am thinking about the weather mostly.")["named_destinations"] == []
    assert interpret_prompt("I want a warm beach somewhere.")["named_destinations"] == []


def test_positive_region_preferences_are_extracted():
    """preferred_regions was hard-coded to [], so a stated region was ignored
    entirely and candidates could come from anywhere."""
    assert interpret_prompt("Three months somewhere in Europe.")["preferred_regions"] == ["Europe"]
    assert interpret_prompt("A month in Southeast Asia.")["preferred_regions"] == ["Southeast Asia"]


def test_a_negated_region_is_not_read_as_a_preference():
    profile = interpret_prompt("A beach holiday, but not in Southeast Asia.")
    assert profile["preferred_regions"] == []


def test_weights_reflect_each_criterion_separately():
    """Intensity was tested against the whole prompt, so one strong phrase set
    every criterion to the same weight."""
    profile = interpret_prompt(
        "Safety is my top priority. Mild weather would be nice but I'm not fussy."
    )

    assert profile["inferred_weights"]["safety"] == 1.0
    assert profile["inferred_weights"]["climate"] == 0.3


def test_an_explicitly_ranked_list_produces_descending_weights():
    profile = interpret_prompt(
        "What matters, roughly in order: public transport, safety, and affordable housing."
    )
    weights = profile["inferred_weights"]

    assert weights["transportation"] > weights["safety"] > weights["cost"]


def test_do_not_care_removes_the_criterion_entirely():
    """Documented in ARCHITECTURE.md but its only test was deleted in the
    scoring-funnel redesign and never replaced."""
    profile = interpret_prompt(
        "A city with great nightlife nearby. Actually, I do not care about nightlife."
    )

    assert "nightlife" not in profile["inferred_weights"]
    assert "nightlife" not in profile["relevant_criteria"]


def test_study_prompt_does_not_extract_an_academic_field():
    profile = interpret_prompt(
        "Recommend a city for a one-semester computer-science exchange. I care about "
        "student life, public transportation, safety, and affordable housing."
    )
    assert profile["purpose"] == "study"
    assert "study_field" not in profile
    assert profile["clarification_required"] is False
    assert "transportation" in profile["relevant_criteria"]
    assert "safety" in profile["relevant_criteria"]


def test_study_prompt_without_field_does_not_require_clarification():
    profile = interpret_prompt("I want to study abroad for a semester somewhere affordable.")
    assert profile["purpose"] == "study"
    assert "study_field" not in profile
    assert profile["clarification_required"] is False
    assert profile["clarification_question"] is None


def test_vacation_prompt_detected():
    profile = interpret_prompt(
        "Find a quiet beach destination for two weeks in October, with warm but not "
        "extremely hot weather and good hiking nearby."
    )
    assert profile["purpose"] == "vacation"
    assert "not extremely hot" in profile["climate_preferences"]
    assert profile["activity_preferences"] == ["beaches", "hiking"]


def test_mixed_purpose_detected():
    profile = interpret_prompt(
        "I want to work remotely for six weeks while staying close to a beach and "
        "overlapping with Israeli working hours."
    )
    assert profile["purpose"] == "mixed"
    assert "remote_work" in profile["secondary_purposes"]
    assert "vacation" in profile["secondary_purposes"]
    assert profile["origin"] == "Israel"


def test_unknown_purpose_requires_clarification():
    profile = interpret_prompt("Surprise me.")
    assert profile["purpose"] == "unknown"
    assert profile["clarification_required"] is True
    assert profile["clarification_question"]


def test_budget_period_assumption_recorded():
    profile = interpret_prompt("I need a European city to study data science on €1,500 per month.")
    assert profile["budget"]["period"] == "monthly"
    assert profile["budget"]["amount"] == 1500.0


def test_amenity_preferences_are_inferred_and_negated_categories_are_excluded():
    profile = interpret_prompt(
        "I want to work remotely near coworking spaces, quiet cafés, a park, a gym, and a hospital, "
        "but I do not need pharmacies."
    )

    assert profile["amenity_preferences"] == [
        "coworking",
        "cafe",
        "park",
        "fitness_centre",
        "hospital",
    ]


def test_real_interpreter_contract_requests_normalized_amenity_preferences():
    assert "amenity_preferences" in SYSTEM_PROMPT
    for category in ("coworking", "cafe", "university", "library", "park", "pharmacy", "supermarket"):
        assert f'"{category}"' in SYSTEM_PROMPT


def test_activity_preferences_are_inferred_and_negated_categories_are_excluded():
    profile = interpret_prompt(
        "I want a vacation with museums, nightlife, parks, hiking and surfing, but avoid beaches."
    )

    assert profile["activity_preferences"] == ["culture", "nightlife", "parks", "hiking", "surfing"]


def test_real_interpreter_contract_requests_normalized_activity_preferences():
    assert "activity_preferences" in SYSTEM_PROMPT
    for category in ("culture", "nightlife", "parks", "beaches", "hiking"):
        assert f'"{category}"' in SYSTEM_PROMPT


def test_real_interpreter_contract_does_not_request_study_field():
    assert "study_field" not in SYSTEM_PROMPT
    assert "discernible field" not in SYSTEM_PROMPT


def test_study_candidate_hypotheses_do_not_claim_program_availability():
    candidates = generate_candidates({"purpose": "study"})
    candidate_text = str(candidates).lower()

    assert "program" not in candidate_text
    assert "admission" not in candidate_text


def test_avoid_phrasing_populates_excluded_regions():
    profile = interpret_prompt("I want a vacation with hiking, but I want to avoid France.")
    assert profile["excluded_regions"] == ["France"]


def test_excluding_accommodation_does_not_leak_into_excluded_regions():
    profile = interpret_prompt("I want a vacation with a budget of 1000 EUR per month, excluding accommodation.")
    assert profile["excluded_regions"] == []
