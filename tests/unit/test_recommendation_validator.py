from app.agent.models import CandidateEvaluation, PlaceRequestProfile
from app.agent.recommendation_validator import validate_recommendations


def _evaluation(
    place: str, score: float, missing_evidence=None, drawbacks=None, eliminated=False
) -> CandidateEvaluation:
    return CandidateEvaluation(
        place=place,
        total_score=score,
        confidence_score=0.8,
        missing_evidence=missing_evidence or [],
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
    assert any("Fewer than 3 viable" in issue for issue in result.issues)


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
