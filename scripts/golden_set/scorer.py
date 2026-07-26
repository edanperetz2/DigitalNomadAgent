"""Structural (not exact-text) comparison scorer for golden-set responses.

LLM output is non-deterministic even for a fixed prompt, so this checks
structural/contractual properties -- the right modules ran, the response has
the required shape, no banned claims leaked in -- rather than comparing
against a fixed expected string.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scripts.golden_set.cases import GoldenCase

# Claims the Recommendation Generator's own system prompt already forbids
# (app/agent/recommendation_generator.py) -- checked here as an independent
# regression guard, not a duplicate of that prompt text. Deliberately avoids
# phrases like "guaranteed admission/visa" that the system's own legitimate
# limitations disclaimer uses in NEGATED form ("no ... guaranteed ... are
# claimed here") -- those would false-positive against compliant output.
_BANNED_CLAIM_PHRASES = (
    "guaranteed safety",
    "guaranteed entry",
    "live flight price",
    "live hotel price",
    "confirmed flight",
)

# Must never reappear now that Dynamic Evaluation actually resolves these
# criteria -- if this text is present, the batched scoring call silently
# failed and the response fell back to the pre-scoring placeholder.
_STALE_PLACEHOLDER = "awaits the LLM reasoning contract"


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_name: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def _count_table_rows(markdown: str) -> int:
    return sum(
        1
        for line in markdown.splitlines()
        if line.startswith("| ") and not line.startswith("|---") and "Rank" not in line
    )


def score_case(case: GoldenCase, response_data: dict, *, max_finalists: int) -> CaseResult:
    checks: list[CheckResult] = []
    status = response_data.get("status")
    checks.append(
        CheckResult(
            "status", status == case.expect_status, f"expected {case.expect_status!r}, got {status!r}"
        )
    )

    if case.expect_status != "ok":
        error_text = (response_data.get("error") or "").casefold()
        for phrase in case.required_phrases:
            checks.append(
                CheckResult(f"error_contains:{phrase}", phrase.casefold() in error_text, f"error was: {error_text!r}")
            )
        return CaseResult(case_name=case.name, checks=checks)

    response_text = response_data.get("response") or ""
    modules_called = {s.get("module") for s in response_data.get("steps", [])}

    checks.append(
        CheckResult(
            "expected_modules_ran",
            case.expected_modules <= modules_called,
            f"expected {sorted(case.expected_modules)}, got {sorted(modules_called)}",
        )
    )

    if case.expect_clarification:
        checks.append(CheckResult("is_clarification_question", "?" in response_text, response_text[:200]))
        return CaseResult(case_name=case.name, checks=checks)

    checks.append(CheckResult("has_best_matches_section", "Best matches" in response_text))
    checks.append(CheckResult("has_mode_disclosure", "Generated using:" in response_text))
    checks.append(CheckResult("no_stale_scoring_placeholder", _STALE_PLACEHOLDER not in response_text))

    row_count = _count_table_rows(response_text)
    checks.append(
        CheckResult(
            "finalist_count_in_bounds",
            0 < row_count <= max_finalists,
            f"row_count={row_count}, max_finalists={max_finalists}",
        )
    )

    lowered = response_text.casefold()
    for phrase in _BANNED_CLAIM_PHRASES:
        checks.append(CheckResult(f"no_banned_claim:{phrase}", phrase not in lowered))
    for phrase in case.forbidden_phrases:
        checks.append(CheckResult(f"forbidden_absent:{phrase}", phrase.casefold() not in lowered))
    for phrase in case.required_phrases:
        checks.append(CheckResult(f"required_present:{phrase}", phrase.casefold() in lowered))

    return CaseResult(case_name=case.name, checks=checks)
