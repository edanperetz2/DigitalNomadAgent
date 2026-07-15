"""Core agent domain models: request profile, candidates, evaluation, validation."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
Month = Annotated[int, Field(ge=1, le=12)]


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float | None = None
    currency: str | None = None
    period: Literal["total", "monthly", "weekly", "daily", "unknown"] = "unknown"
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
    preferred_languages: list[str] = Field(default_factory=list)
    mobility_requirements: list[str] = Field(default_factory=list)
    climate_preferences: list[str] = Field(default_factory=list)
    activity_preferences: list[str] = Field(default_factory=list)
    amenity_preferences: list[str] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)
    relevant_criteria: list[str] = Field(default_factory=list)
    inferred_weights: dict[str, float] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    clarification_required: bool = False
    clarification_question: str | None = None
    study_field: str | None = None


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


class CandidateEvaluation(BaseModel):
    """Deterministic Dynamic Evaluation output for one candidate place."""

    model_config = ConfigDict(extra="forbid")

    place: str
    country: str = ""
    criterion_scores: dict[str, float] = Field(default_factory=dict)
    criterion_component_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    criterion_weights: dict[str, float] = Field(default_factory=dict)
    total_score: float = 0.0
    confidence_score: float = 0.0
    hard_constraint_results: dict[str, bool] = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)
    advantages: list[str] = Field(default_factory=list)
    drawbacks: list[str] = Field(default_factory=list)
    eliminated: bool = False
    elimination_reason: str | None = None


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
