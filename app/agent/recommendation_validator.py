"""Recommendation Validator: deterministic quality/coverage gate.

No LLM call. Decides whether the current evidence/ranking is good enough to
generate a final response, or whether exactly one additional gap-research
iteration is warranted (bounded by the orchestrator's iteration flag).
"""

from __future__ import annotations

from app.agent.dynamic_evaluation import canonical_criterion_name
from app.agent.models import (
    CandidateEvaluation,
    MissingResearchItem,
    PlaceRequestProfile,
    ValidationResult,
)

HIGH_WEIGHT_THRESHOLD = 0.7
RANKING_STABILITY_MARGIN = 0.05


def validate_recommendations(
    evaluations: list[CandidateEvaluation],
    profile: PlaceRequestProfile,
    gap_iteration_used: bool,
    max_final_recommendations: int = 3,
) -> ValidationResult:
    issues: list[str] = []
    missing_research: list[MissingResearchItem] = []

    viable = [e for e in evaluations if not e.eliminated]

    # The "shorter list than usual" caveat used to be raised here, as an issue.
    # It was wrong twice over. It went into `caveats_to_pass_on` for the
    # generator to paraphrase, and the model simply dropped it -- P06 proposed 30
    # places, researched 8, delivered a one-row table and never said so. And it
    # was computed here, before `_score_unresolved_criteria` can un-eliminate a
    # candidate by supplying the score it was missing, so it also fired when it
    # should not have: P02 carried it while delivering seven.
    #
    # It now lives in the orchestrator, after the rescue step, and is appended
    # deterministically after generation like every other disclosure the reader
    # must see (D56). See `collapse_disclosure` below.

    # All three vocabularies here are authored independently -- weight keys and
    # missing_evidence by the interpreter, unscored_evidence by the tool layer --
    # so they only line up once canonicalized.
    high_weight_criteria = {
        canonical_criterion_name(c)
        for c, w in profile.inferred_weights.items()
        if w >= HIGH_WEIGHT_THRESHOLD
    }

    top_candidates = viable[:max_final_recommendations]
    for evaluation in top_candidates:
        unscored = {canonical_criterion_name(c) for c in evaluation.unscored_evidence}
        missing_high = [
            criterion
            for criterion in evaluation.missing_evidence
            if canonical_criterion_name(criterion) in high_weight_criteria
            and canonical_criterion_name(criterion) not in unscored
        ]
        for criterion in missing_high:
            missing_research.append(MissingResearchItem(place=evaluation.place, criterion=criterion))

    should_research_again = bool(missing_research) and not gap_iteration_used
    if missing_research and gap_iteration_used:
        issues.append(
            "Some high-priority criteria remain unverified after the additional research "
            "iteration; the result is disclosed as limited rather than researched further."
        )

    for evaluation in viable:
        if not evaluation.drawbacks:
            issues.append(f"{evaluation.place} has no recorded drawbacks.")

    ranking_stability = "stable"
    if len(viable) >= 2:
        gap = viable[0].total_score - viable[1].total_score
        if gap < RANKING_STABILITY_MARGIN:
            ranking_stability = "uncertain"

    if viable:
        evidence_coverage = sum(e.confidence_score for e in top_candidates or viable) / len(
            top_candidates or viable
        )
    else:
        evidence_coverage = 0.0

    approved = not should_research_again

    return ValidationResult(
        approved=approved,
        issues=issues,
        missing_research=missing_research,
        ranking_stability=ranking_stability,
        evidence_coverage=round(evidence_coverage, 4),
        should_research_again=should_research_again,
    )
