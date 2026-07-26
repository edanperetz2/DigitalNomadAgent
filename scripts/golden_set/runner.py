"""Runs the golden-set cases against an app instance and scores each result.

Reuses app.main.create_app's existing tool_registry_override/llm_client_override
DI seam -- the same one tests use -- rather than a separate app-construction path.
Defaults to the fake tool registry (offline, free) so this is safe to run with
either MockLLMClient (zero cost) or a real LLM client (tool calls stay fake and
free; only the LLM-calling modules' behavior is actually being evaluated).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm.base import BaseLLMClient
from app.main import create_app
from app.tools.fakes import build_fake_tool_registry_dict
from app.tools.registry import ToolRegistry
from scripts.golden_set.cases import GOLDEN_CASES, GoldenCase
from scripts.golden_set.scorer import CaseResult, score_case


def run_golden_set(
    llm_client: BaseLLMClient,
    *,
    cases: list[GoldenCase] | None = None,
    tool_registry: ToolRegistry | None = None,
) -> list[CaseResult]:
    cases = cases if cases is not None else GOLDEN_CASES
    registry = tool_registry or ToolRegistry(build_fake_tool_registry_dict(), max_concurrent_requests=5)
    max_finalists = get_settings().max_finalists

    app = create_app(tool_registry_override=registry, llm_client_override=llm_client)
    results: list[CaseResult] = []
    with TestClient(app) as client:
        for case in cases:
            response = client.post("/api/execute", json={"prompt": case.prompt})
            results.append(score_case(case, response.json(), max_finalists=max_finalists))
    return results


def format_report(results: list[CaseResult]) -> str:
    lines: list[str] = []
    total_passed = sum(1 for r in results if r.passed)
    lines.append(f"Golden set: {total_passed}/{len(results)} cases passed")
    lines.append("")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"[{status}] {result.case_name}")
        for failure in result.failures:
            lines.append(f"    - {failure.check}: {failure.detail}")
    return "\n".join(lines)
