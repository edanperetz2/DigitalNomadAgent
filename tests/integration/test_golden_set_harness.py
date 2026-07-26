"""Runs the golden-set harness itself under MockLLMClient as a pytest check,
so a regression in the pipeline that the harness is designed to catch also
fails the normal test suite -- not just a manually-run script.
"""

from app.llm.mock import MockLLMClient
from scripts.golden_set.runner import format_report, run_golden_set


def test_golden_set_passes_under_mock_llm():
    results = run_golden_set(MockLLMClient())
    failed = [r for r in results if not r.passed]
    assert not failed, format_report(results)
