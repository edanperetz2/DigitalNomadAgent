from app.agent.request_interpreter import SYSTEM_PROMPT, out_of_scope_requests
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


def test_a_month_is_recognised_as_a_monthly_budget():
    """The most natural phrasing of all was missing: the period patterns knew
    "per month" and "monthly" but not "a month"."""
    for phrasing in ("€1,200 a month", "1200 EUR each month", "€1,200 per month"):
        budget = interpret_prompt(f"Six months of remote work on {phrasing} all-in.")["budget"]
        assert budget["amount"] == 1200.0, phrasing
        assert budget["period"] == "monthly", phrasing


def test_an_unrelated_period_word_cannot_hijack_the_budget():
    """"reliable internet for daily video calls" set the budget period to
    daily -- a 30x error, because the period was searched across the whole
    prompt rather than the sentence stating the amount."""
    budget = interpret_prompt(
        "My budget is €1,200 a month all-in. I need reliable internet for daily video calls."
    )["budget"]

    assert budget["period"] == "monthly"


def test_budget_scope_accommodation_only_phrasings():
    for prompt in (
        "student housing I can afford on about €700 a month",
        "€900 a month for accommodation",
    ):
        budget = interpret_prompt(prompt)["budget"]
        assert budget["budget_scope"] == "accommodation_only", prompt


def test_budget_scope_total_living_cost_phrasing():
    budget = interpret_prompt("no more than €1,800 a month all-in including rent")["budget"]

    assert budget["amount"] == 1800.0
    assert budget["currency"] == "EUR"
    assert budget["period"] == "monthly"
    assert budget["budget_scope"] == "total_living_cost"


def test_budget_scope_living_cost_excluding_accommodation_phrasing():
    budget = interpret_prompt("€800/month excluding rent")["budget"]

    assert budget["amount"] == 800.0
    assert budget["currency"] == "EUR"
    assert budget["period"] == "monthly"
    assert budget["budget_scope"] == "living_cost_excluding_accommodation"


def test_ambiguous_budget_scope_stays_unspecified():
    budget = interpret_prompt("my budget is €1,000/month")["budget"]

    assert budget["amount"] == 1000.0
    assert budget["currency"] == "EUR"
    assert budget["period"] == "monthly"
    assert budget["budget_scope"] == "unspecified"
    assert budget["includes_accommodation"] is None


def test_exchange_student_housing_prompt_is_accommodation_only():
    prompt = (
        "I'm a third-year computer science undergrad and I've been accepted for a one-semester "
        "exchange next spring, but I get to pick from a fairly open list of partner universities "
        "— so really I'm choosing a city. What matters, roughly in order: a genuinely active "
        "student scene so I'm not isolated, public transport good enough that I don't have to live "
        "right next to campus, feeling safe walking home late, and student housing I can afford on "
        "about €700 a month. English-taught courses are a must — my language skills are nonexistent."
    )

    budget = interpret_prompt(prompt)["budget"]

    assert budget["amount"] == 700.0
    assert budget["currency"] == "EUR"
    assert budget["period"] == "monthly"
    assert budget["budget_scope"] == "accommodation_only"
    assert interpret_prompt(prompt)["student_housing_requested"] is True


def test_student_housing_requested_requires_student_specific_wording():
    explicit = interpret_prompt("I need student housing under €700 a month.")
    generic = interpret_prompt("I'm a student and want accommodation for €700 a month.")

    assert explicit["student_housing_requested"] is True
    assert generic["student_housing_requested"] is False


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


def test_timezone_overlap_requirement_becomes_a_hard_constraint():
    profile = interpret_prompt(
        "I work remotely and need a time zone giving me at least four hours of "
        "overlap with US Eastern. Budget about $2,000 a month."
    )
    assert any("overlap with us eastern" in c for c in profile["hard_constraints"])


def test_target_months_are_derived_from_the_stated_dates():
    """D31: the field existed on the profile but nothing ever populated it, so
    WeatherTool fell back to the current calendar month on every request."""
    assert interpret_prompt("Ten days in October, somewhere warm.")["target_months"] == [10]
    assert interpret_prompt("Two weeks in August with the kids.")["target_months"] == [8]


def test_a_multi_month_stay_spans_forward_from_its_start_month():
    profile = interpret_prompt(
        "I've been cleared to work fully remote for three months starting in April."
    )
    assert profile["target_months"] == [4, 5, 6]


def test_a_month_range_expands_inclusively_across_the_year_boundary():
    profile = interpret_prompt("We want to escape the winter, roughly November through April.")
    assert profile["target_months"] == [11, 12, 1, 2, 3, 4]


def test_a_bare_season_is_read_as_northern_hemisphere_and_disclosed():
    profile = interpret_prompt("I'm looking for somewhere in Scandinavia for a month this winter.")
    assert profile["target_months"] == [12, 1, 2]
    assert any("northern-hemisphere" in a for a in profile["assumptions"])


def test_no_stated_timing_leaves_target_months_empty():
    """An empty list is the correct answer -- climate is not scored without one,
    and a guessed month is worse than no month."""
    profile = interpret_prompt(
        "I've been burnt out for a year and finally saved enough to get away for a while."
    )
    assert profile["target_months"] == []


def test_may_and_march_only_count_as_months_when_capitalised():
    assert interpret_prompt("Somewhere I may be able to relax for ten days.")["target_months"] == []
    assert interpret_prompt("Two weeks in May.")["target_months"] == [5]


def test_interpreter_prompt_asks_for_target_months():
    assert "target_months" in SYSTEM_PROMPT


def test_a_bare_named_place_is_captured_without_a_deliberation_phrase():
    """D32: every trigger needed the user to deliberate out loud ("settled on
    X"). P10 simply named Bali and was answered with Israeli cities."""
    profile = interpret_prompt(
        "I need the cheapest confirmed flight and hotel prices for Bali for the week of the 14th."
    )
    assert profile["named_destinations"] == ["Bali"]


def test_regions_months_and_origins_are_not_mistaken_for_named_places():
    assert interpret_prompt("I want to spend three months somewhere in Europe.")["named_destinations"] == []
    assert interpret_prompt("Ten days in October, somewhere warm.")["named_destinations"] == []
    assert (
        interpret_prompt("We're flying out of Tel Aviv for two weeks in August.")["named_destinations"] == []
    )


def test_out_of_scope_asks_are_detected_from_the_raw_prompt():
    """Detected off the prompt, not the profile, so the refusal survives an
    interpreter call that fails outright -- which is what happened on P10."""
    asks = out_of_scope_requests(
        "I need the cheapest confirmed flight and hotel prices for Bali with the actual current "
        "nightly rates, and tell me exactly what the visa fee is for an Israeli passport holder."
    )
    assert "live or confirmed flight prices" in asks
    assert "current hotel or nightly accommodation rates" in asks
    assert "visa fees or entry eligibility" in asks


def test_an_ordinary_request_asks_for_nothing_out_of_scope():
    assert out_of_scope_requests(
        "I want three months in Europe, car-free, under EUR 1800 a month including rent."
    ) == []


def test_saying_you_do_not_know_what_you_want_earns_a_question():
    """D45: P07 -- "I've been burnt out for the better part of a year... I don't
    really know what I'm looking for" -- got a ranked scoring table with
    four-decimal totals and no question at all, on the one prompt where the
    traveller had said outright they could not specify the request."""
    profile = interpret_prompt(
        "I've been burnt out for the better part of a year and I've finally saved enough to get "
        "away for a while. I don't really know what I'm looking for. I've never really travelled "
        "properly before. Where should I go?"
    )

    assert profile["clarification_required"] is True
    assert "narrow this down" in profile["clarification_question"]


def test_the_question_offers_directions_rather_than_asking_for_a_spec():
    """Asking someone who just said they cannot specify to specify is no help."""
    profile = interpret_prompt("I have no idea where to go. Somewhere nice?")
    question = profile["clarification_question"]

    assert "rest" in question and "meet people" in question


def test_a_specific_request_is_still_answered_without_asking():
    profile = interpret_prompt(
        "I want three months in Europe working remotely, car-free, under EUR 1800 a month."
    )
    assert profile["clarification_required"] is False


def test_interpreter_prompt_covers_self_declared_uncertainty():
    assert "do not know what they want" in SYSTEM_PROMPT


def test_a_bare_season_counts_as_timing():
    """D49: the real interpreter left target_months empty for "a month this
    winter", so P08's answer told the traveller it did not know when they were
    going -- after they had said."""
    assert interpret_prompt("Somewhere in Scandinavia for a month this winter.")["target_months"] == [12, 1, 2]
    assert interpret_prompt("We want six months escaping the winter.")["target_months"] == [12, 1, 2]


def test_interpreter_prompt_says_a_bare_season_is_usable_timing():
    assert "A bare season is usable timing" in SYSTEM_PROMPT


def test_interpreter_prompt_asks_for_in_scope():
    assert "in_scope" in SYSTEM_PROMPT
    assert "Surprise me." in SYSTEM_PROMPT
    assert "is NOT out of" in SYSTEM_PROMPT
