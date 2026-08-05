from app.agent.models import CandidateEvaluation, PlaceRequestProfile
from app.agent.recommendation_validator import validate_recommendations


def _evaluation(
    place: str,
    score: float,
    missing_evidence=None,
    drawbacks=None,
    eliminated=False,
    unscored_evidence=None,
) -> CandidateEvaluation:
    return CandidateEvaluation(
        place=place,
        total_score=score,
        confidence_score=0.8,
        missing_evidence=missing_evidence or [],
        unscored_evidence=unscored_evidence or [],
        drawbacks=drawbacks if drawbacks is not None else ["some drawback"],
        eliminated=eliminated,
    )


def test_approved_when_no_gaps():
    profile = PlaceRequestProfile(purpose="vacation", inferred_weights={"climate": 0.9})
    evaluations = [
        _evaluation("A", 0.9),
        _evaluation("B", 0.7),
        _evaluation("C", 0.5),
    ]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    assert result.approved is True
    assert result.should_research_again is False


def test_requests_gap_research_when_high_weight_criterion_missing():
    profile = PlaceRequestProfile(purpose="vacation", inferred_weights={"climate": 0.9})
    evaluations = [
        _evaluation("A", 0.9, missing_evidence=["climate"]),
        _evaluation("B", 0.7),
        _evaluation("C", 0.5),
    ]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    assert result.should_research_again is True
    assert any(m.place == "A" and m.criterion == "climate" for m in result.missing_research)


def test_gap_research_matches_free_form_weight_keys_against_missing_evidence():
    """P05's real weight keys never matched its missing_evidence entries.

    Both lists are interpreter prose, so whether a gap iteration fired depended
    on the LLM happening to spell one criterion the same way twice.
    """
    profile = PlaceRequestProfile(
        purpose="remote_work", inferred_weights={"time_zone_overlap": 1.0}
    )
    evaluations = [
        _evaluation("A", 0.9, missing_evidence=["timezone"]),
        _evaluation("B", 0.7),
        _evaluation("C", 0.5),
    ]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    assert result.should_research_again is True
    assert any(m.place == "A" and m.criterion == "timezone" for m in result.missing_research)


def test_no_gap_research_when_a_high_weight_criterion_is_already_evidenced():
    """The evidenced case: nothing is missing, so no iteration should be spent."""
    profile = PlaceRequestProfile(
        purpose="remote_work",
        inferred_weights={"time_zone_overlap": 1.0, "internet_quality": 0.9},
    )
    evaluations = [_evaluation(p, s) for p, s in (("A", 0.9), ("B", 0.7), ("C", 0.5))]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    assert result.should_research_again is False
    assert result.missing_research == []


def test_awaiting_llm_reasoning_is_recognized_across_vocabularies():
    """unscored_evidence is tool-layer wording; missing_evidence is the user's."""
    profile = PlaceRequestProfile(
        purpose="study", inferred_weights={"public_transport_quality": 0.9}
    )
    evaluations = [
        _evaluation(
            place,
            score,
            missing_evidence=["public transport"],
            unscored_evidence=["transportation"],
        )
        for place, score in (("A", 0.9), ("B", 0.7), ("C", 0.5))
    ]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    assert result.should_research_again is False


def test_does_not_repeat_research_for_criterion_intentionally_awaiting_llm_reasoning():
    profile = PlaceRequestProfile(purpose="study", inferred_weights={"transportation": 0.9})
    evaluations = [
        _evaluation(
            "A",
            0.9,
            missing_evidence=["transportation"],
            unscored_evidence=["transportation"],
        ),
        _evaluation(
            "B",
            0.7,
            missing_evidence=["transportation"],
            unscored_evidence=["transportation"],
        ),
        _evaluation(
            "C",
            0.5,
            missing_evidence=["transportation"],
            unscored_evidence=["transportation"],
        ),
    ]

    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)

    assert result.should_research_again is False
    assert result.missing_research == []


def test_retries_when_llm_reasoning_criterion_has_no_collected_evidence():
    profile = PlaceRequestProfile(purpose="study", inferred_weights={"transportation": 0.9})
    evaluations = [_evaluation("A", 0.9, missing_evidence=["transportation"])]

    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)

    assert result.should_research_again is True
    assert result.missing_research[0].criterion == "transportation"


def test_second_iteration_is_refused():
    profile = PlaceRequestProfile(purpose="vacation", inferred_weights={"climate": 0.9})
    evaluations = [_evaluation("A", 0.9, missing_evidence=["climate"])]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=True)
    assert result.should_research_again is False
    assert result.approved is True
    assert any("additional research iteration" in issue for issue in result.issues)


def test_fewer_than_three_viable_candidates_flagged():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [
        _evaluation("A", 0.9),
        _evaluation("B", 0.7, eliminated=True),
        _evaluation("C", 0.5, eliminated=True),
    ]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    # D42: reworded for the reader; the flag itself is what this guards.
    assert any("shorter list than usual" in issue for issue in result.issues)


def test_ranking_stability_flagged_when_close():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [_evaluation("A", 0.701), _evaluation("B", 0.700)]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    assert result.ranking_stability == "uncertain"


def test_missing_drawbacks_flagged():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [_evaluation("A", 0.9, drawbacks=[])]
    result = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    assert any("no recorded drawbacks" in issue for issue in result.issues)


def test_fewer_than_max_final_recommendations_flagged_with_custom_n():
    profile = PlaceRequestProfile(purpose="vacation")
    evaluations = [
        _evaluation(str(i), 0.9 - i * 0.01, eliminated=(i >= 5)) for i in range(8)
    ]
    result = validate_recommendations(
        evaluations, profile, gap_iteration_used=False, max_final_recommendations=8
    )
    assert any("shorter list than usual" in issue for issue in result.issues)


def test_top_candidates_slice_respects_custom_max_final_recommendations():
    profile = PlaceRequestProfile(purpose="vacation", inferred_weights={"climate": 0.9})
    evaluations = [
        _evaluation(str(i), 0.9 - i * 0.01, missing_evidence=["climate"] if i == 5 else None)
        for i in range(8)
    ]
    result_default = validate_recommendations(evaluations, profile, gap_iteration_used=False)
    result_wide = validate_recommendations(
        evaluations, profile, gap_iteration_used=False, max_final_recommendations=8
    )
    assert result_default.should_research_again is False
    assert result_wide.should_research_again is True
