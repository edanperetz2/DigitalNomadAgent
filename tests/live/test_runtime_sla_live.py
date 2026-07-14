"""Paid/network SLA check. Runs only with RUN_LIVE_TESTS=1 and MOCK_LLM=false."""

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.integration.test_runtime_sla import REPRESENTATIVE_PROMPTS

pytestmark = pytest.mark.live


def test_live_representative_requests_target_p95_below_240_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "live_runtime_sla.db"))
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.run_live_tests:
        pytest.skip("Set RUN_LIVE_TESTS=1 to run paid/network SLA checks.")
    if settings.mock_llm:
        pytest.skip("Set MOCK_LLM=false to exercise the real provider in the live SLA check.")

    durations = []
    with TestClient(create_app()) as client:
        for prompt in REPRESENTATIVE_PROMPTS:
            started = time.monotonic()
            response = client.post("/api/execute", json={"prompt": prompt})
            durations.append(time.monotonic() - started)
            assert response.json()["status"] == "ok"

    observed_p95 = max(durations)
    assert observed_p95 < 240.0, f"Observed representative p95 was {observed_p95:.1f}s"
    get_settings.cache_clear()
