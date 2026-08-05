"""Fixed, representative prompt set for the golden-set harness.

Each case is deliberately worded to exercise a specific path through the
pipeline (a purpose, an edge case, a hard constraint) rather than being a
"nice" example -- the point is coverage of behavior, not variety of prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.module_names import AGENTIC_RESEARCH, DYNAMIC_EVALUATION, RECOMMENDATION_GENERATOR, REQUEST_INTERPRETER

_NORMAL_FLOW_MODULES = frozenset(
    {REQUEST_INTERPRETER, AGENTIC_RESEARCH, DYNAMIC_EVALUATION, RECOMMENDATION_GENERATOR}
)


@dataclass(frozen=True)
class GoldenCase:
    name: str
    prompt: str
    expect_status: str = "ok"
    expect_clarification: bool = False
    expected_modules: frozenset[str] = field(default_factory=frozenset)
    forbidden_phrases: tuple[str, ...] = ()
    required_phrases: tuple[str, ...] = ()
    # Every recommended place must be inside this region. A phrase check cannot
    # express that -- it can only forbid cities someone thought to name -- which
    # is why the positive case had no coverage and D7 (preferred_regions
    # hard-coded empty) survived to a real run.
    required_region: str | None = None


GOLDEN_CASES: list[GoldenCase] = [
    GoldenCase(
        name="remote_work_budget_carfree",
        prompt=(
            "I want to spend three months somewhere in Europe where I can work remotely, "
            "live without a car, and stay within €1,800 per month."
        ),
        expected_modules=_NORMAL_FLOW_MODULES,
    ),
    GoldenCase(
        name="study_with_field",
        prompt=(
            "Recommend a city for a one-semester computer-science exchange. I care about "
            "student life, public transportation, safety, and affordable housing."
        ),
        expected_modules=_NORMAL_FLOW_MODULES,
    ),
    GoldenCase(
        name="vacation_climate_activities",
        prompt=(
            "Find a quiet beach destination for two weeks in October, with warm but not "
            "extremely hot weather and good hiking nearby."
        ),
        expected_modules=_NORMAL_FLOW_MODULES,
    ),
    GoldenCase(
        name="mixed_purpose",
        prompt=(
            "I want to work remotely for a month somewhere warm with a beach nearby, then "
            "take a two-week vacation afterward. Budget-friendly, please."
        ),
        expected_modules=_NORMAL_FLOW_MODULES,
    ),
    GoldenCase(
        # /api/execute is called through the golden-set runner exactly the way an
        # automated grader would (bare {"prompt": ...}, no X-Interactive-Mode
        # header) -- so this must NOT dead-end on a clarification question. The
        # interpreter still flags clarification_required internally, but the
        # orchestrator resolves it to a broad-default assumption and runs the
        # full pipeline, per app/agent/orchestrator.py's _resolve_ambiguous_profile.
        name="ambiguous_prompt_requests_clarification",
        prompt="Surprise me.",
        expect_clarification=True,
        expected_modules=_NORMAL_FLOW_MODULES,
    ),
    GoldenCase(
        name="hard_budget_eliminates_all_candidates",
        prompt=(
            "I want to work remotely somewhere in Europe. It is required that the budget "
            "stay under 1 USD per month including accommodation."
        ),
        expect_status="error",
        required_phrases=("eliminated",),
    ),
    GoldenCase(
        name="excluded_region_is_respected",
        prompt=(
            "I want a vacation with lots of hiking and beaches, but I want to avoid France."
        ),
        expected_modules=_NORMAL_FLOW_MODULES,
        forbidden_phrases=("France",),
    ),
    GoldenCase(
        # The positive counterpart of excluded_region_is_respected. Only became
        # assertable once a region resolved to its member countries (D27); before
        # that a stated region was relaxed and quietly ignored, and a Scandinavia
        # request came back with Chiang Mai.
        name="preferred_region_is_respected",
        prompt=(
            "I want to spend a month somewhere in Scandinavia, with good public transport "
            "and a walkable city centre."
        ),
        expected_modules=_NORMAL_FLOW_MODULES,
        required_region="Scandinavia",
    ),
    GoldenCase(
        name="car_free_hard_requirement",
        prompt=(
            "I need a remote work destination in Europe where I can live entirely without "
            "a car, with great public transportation."
        ),
        expected_modules=_NORMAL_FLOW_MODULES,
    ),
]
