"""Core agent domain models: request profile, candidates, evaluation, validation."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

Confidence = Literal["high", "medium", "low"]
Month = Annotated[int, Field(ge=1, le=12)]
BudgetScope = Literal[
    "accommodation_only",
    "total_living_cost",
    "living_cost_excluding_accommodation",
    "unspecified",
]
HardConstraintStatus = Literal[
    "verified",
    "borderline",
    "no_evidence",
    "requirement_not_met",
]

HARD_CONSTRAINT_VERIFIED: HardConstraintStatus = "verified"
HARD_CONSTRAINT_BORDERLINE: HardConstraintStatus = "borderline"
HARD_CONSTRAINT_NO_EVIDENCE: HardConstraintStatus = "no_evidence"
HARD_CONSTRAINT_REQUIREMENT_NOT_MET: HardConstraintStatus = "requirement_not_met"
HARD_CONSTRAINT_STATUS_ORDER: tuple[HardConstraintStatus, ...] = (
    HARD_CONSTRAINT_VERIFIED,
    HARD_CONSTRAINT_BORDERLINE,
    HARD_CONSTRAINT_NO_EVIDENCE,
    HARD_CONSTRAINT_REQUIREMENT_NOT_MET,
)
HARD_CONSTRAINT_DISPLAY_LABELS: dict[HardConstraintStatus, str] = {
    HARD_CONSTRAINT_VERIFIED: "Verified",
    HARD_CONSTRAINT_BORDERLINE: "Borderline",
    HARD_CONSTRAINT_NO_EVIDENCE: "No Evidence",
    HARD_CONSTRAINT_REQUIREMENT_NOT_MET: "Requirement Not Met",
}


def normalize_hard_constraint_status(value: object) -> HardConstraintStatus:
    """Map explicit and legacy hard-constraint values to the current status."""
    if value is True:
        return HARD_CONSTRAINT_VERIFIED
    if value is False:
        return HARD_CONSTRAINT_REQUIREMENT_NOT_MET
    if value is None:
        return HARD_CONSTRAINT_NO_EVIDENCE
    if isinstance(value, str):
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        aliases = {
            "met": HARD_CONSTRAINT_VERIFIED,
            "passed": HARD_CONSTRAINT_VERIFIED,
            "confirmed": HARD_CONSTRAINT_VERIFIED,
            "verified": HARD_CONSTRAINT_VERIFIED,
            "borderline": HARD_CONSTRAINT_BORDERLINE,
            "partial": HARD_CONSTRAINT_BORDERLINE,
            "partially_verified": HARD_CONSTRAINT_BORDERLINE,
            "no_evidence": HARD_CONSTRAINT_NO_EVIDENCE,
            "unverified": HARD_CONSTRAINT_NO_EVIDENCE,
            "unconfirmed": HARD_CONSTRAINT_NO_EVIDENCE,
            "could_not_be_checked": HARD_CONSTRAINT_NO_EVIDENCE,
            "not_met": HARD_CONSTRAINT_REQUIREMENT_NOT_MET,
            "not_met_adequately": HARD_CONSTRAINT_REQUIREMENT_NOT_MET,
            "requirement_not_met": HARD_CONSTRAINT_REQUIREMENT_NOT_MET,
            "failed": HARD_CONSTRAINT_REQUIREMENT_NOT_MET,
        }
        if normalized in aliases:
            return aliases[normalized]
    raise ValueError(f"Unknown hard-constraint status: {value!r}")


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float | None = None
    currency: str | None = None
    period: Literal["total", "monthly", "weekly", "daily", "unknown"] = "unknown"
    budget_scope: BudgetScope = "unspecified"
    includes_accommodation: bool | None = None
    confidence: Confidence = "medium"


class PlaceRequestProfile(BaseModel):
    """Structured interpretation of a natural-language place request."""

    model_config = ConfigDict(extra="forbid")

    purpose: Literal["remote_work", "study", "vacation", "mixed", "unknown"]
    secondary_purposes: list[str] = Field(default_factory=list)
    duration: str | None = None
    dates_or_season: str | None = None
    target_months: list[Month] = Field(default_factory=list)
    origin: str | None = None
    nationality: str | None = None
    preferred_regions: list[str] = Field(default_factory=list)
    excluded_regions: list[str] = Field(default_factory=list)
    # Specific places the user named and wants judged ("is Lisbon a good fit?").
    # Separate from preferred_regions, which is only ever matched against a
    # candidate's country -- a city put there matches nothing, so the named
    # place was dropped and the question went unanswered.
    named_destinations: list[str] = Field(default_factory=list)
    preferred_languages: list[str] = Field(default_factory=list)
    mobility_requirements: list[str] = Field(default_factory=list)
    climate_preferences: list[str] = Field(default_factory=list)
    activity_preferences: list[str] = Field(default_factory=list)
    amenity_preferences: list[str] = Field(default_factory=list)
    # True only when the user's wording asks for student-specific accommodation
    # (student housing, student accommodation, residence, dorms, etc.). A study
    # trip with a normal apartment budget is not enough.
    student_housing_requested: bool = False
    budget: Budget = Field(default_factory=Budget)
    # Numeric limits, asked for as numbers -- the same treatment `budget` gets.
    #
    # These were previously left inside `hard_constraints` as free text and
    # re-parsed downstream by keyword and regex, which works only while the
    # interpreter happens to phrase them the way the parser expects. P14 stated
    # a ten-hour flight ceiling and the interpreter wrote it as
    # `travel_time_under_10_hours`; the parser looks for "flight"/"flying",
    # matched nothing, and applied no limit at all -- so Lisbon, roughly 24
    # hours from Melbourne, ranked first having paid nothing for it (D64).
    #
    # Free text is right for requirements that have no number ("step-free
    # access", "English widely spoken"). It is the wrong home for a figure the
    # system actually measures against.
    max_flight_hours: float | None = Field(default=None, gt=0)
    min_timezone_overlap_hours: float | None = Field(default=None, ge=0)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)
    relevant_criteria: list[str] = Field(default_factory=list)
    inferred_weights: dict[str, float] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    clarification_required: bool = False
    clarification_question: str | None = None


class CandidatePlaceSeed(BaseModel):
    """Lean Stage-1 bulk-recall shape: enough to identify a place, nothing else.

    Kept intentionally minimal so the bulk (~30 candidate) LLM call stays cheap;
    the richer CandidatePlace fields below are only ever populated for finalists.
    """

    model_config = ConfigDict(extra="forbid")

    place_name: str
    country: str
    reason_for_inclusion: str


class CandidatePlace(BaseModel):
    """A candidate destination proposed by Agentic Research, pre-verification."""

    model_config = ConfigDict(extra="forbid")

    place_name: str
    country: str
    reason_for_inclusion: str
    expected_strengths: list[str] = Field(default_factory=list)
    likely_weakness: str = ""
    criteria_to_verify: list[str] = Field(default_factory=list)

    # Populated after GeocodingTool verification.
    verified: bool = False
    lat: float | None = None
    lon: float | None = None
    canonical_name: str | None = None
    country_code: str | None = None
    geocoding_importance: float | None = None


class CandidateEvaluation(BaseModel):
    """Deterministic Dynamic Evaluation output for one candidate place."""

    model_config = ConfigDict(extra="forbid")

    place: str
    country: str = ""
    criterion_scores: dict[str, float] = Field(default_factory=dict)
    criterion_component_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    criterion_weights: dict[str, float] = Field(default_factory=dict)
    # Which source produced each scored criterion. The report carried one flat
    # bibliography of ~33 citations that no claim pointed into, so a reader could
    # not check any particular number against where it came from (E4).
    criterion_sources: dict[str, list[str]] = Field(default_factory=dict)
    total_score: float = 0.0
    confidence_score: float = 0.0
    # Evidence status for each stated hard requirement. Legacy traces used
    # True/False/None; the validator below keeps them readable while new runs
    # preserve the difference between borderline evidence and no evidence.
    hard_constraint_results: dict[str, HardConstraintStatus] = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)
    unscored_evidence: list[str] = Field(default_factory=list)
    advantages: list[str] = Field(default_factory=list)
    drawbacks: list[str] = Field(default_factory=list)
    eliminated: bool = False
    elimination_reason: str | None = None

    @field_validator("hard_constraint_results", mode="before")
    @classmethod
    def _normalize_hard_constraint_results(cls, value: object) -> object:
        if value is None:
            return {}
        if isinstance(value, dict):
            return {
                str(criterion): normalize_hard_constraint_status(status)
                for criterion, status in value.items()
            }
        return cast(object, value)


class MissingResearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place: str
    criterion: str


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    issues: list[str] = Field(default_factory=list)
    missing_research: list[MissingResearchItem] = Field(default_factory=list)
    ranking_stability: Literal["stable", "uncertain"] = "stable"
    evidence_coverage: float = 0.0
    should_research_again: bool = False
