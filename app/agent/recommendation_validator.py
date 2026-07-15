"""Recommendation Validator: deterministic quality/coverage gate.

No LLM call. Decides whether the current evidence/ranking is good enough to
generate a final response, or whether exactly one additional gap-research
iteration is warranted (bounded by the orchestrator's iteration flag).
"""

from __future__ import annotations

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
) -> ValidationResult:
    issues: list[str] = []
    missing_research: list[MissingResearchItem] = []

    viable = [e for e in evaluations if not e.eliminated]

    if len(evaluations) >= 3 and len(viable) < 3:
        issues.append("Fewer than 3 viable candidates remain after hard-constraint checks.")

    high_weight_criteria = {c for c, w in profile.inferred_weights.items() if w >= HIGH_WEIGHT_THRESHOLD}

    top_candidates = viable[:3]
    for evaluation in top_candidates:
        missing_high = [
            criterion
            for criterion in evaluation.missing_evidence
            if criterion in high_weight_criteria and criterion not in evaluation.unscored_evidence
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
