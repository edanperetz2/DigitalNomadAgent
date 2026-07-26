"""Shared pytest fixtures.

Normal tests run fully offline: MockLLMClient (zero cost) + FakeToolRegistry
(no network) + a temporary SQLite database per test. An autouse fixture blocks
real outbound HTTP unless a test is explicitly marked `live` and the operator
sets RUN_LIVE_TESTS=1.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm.mock import MockLLMClient
from app.tools.fakes import build_fake_tool_registry_dict
from app.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _block_real_network(request, monkeypatch):
    if request.node.get_closest_marker("live") and get_settings().run_live_tests:
        return

    import httpx

    async def _blocked_send(*args, **kwargs):
        raise RuntimeError("Real network access is blocked during tests. Use fake tools/MockLLMClient.")

    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_send)


@pytest.fixture
def app_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test_digitalnomadagent.db"))
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("MAX_PROJECT_BUDGET_USD", "13")
    monkeypatch.setenv("MAX_LLM_CALLS_PER_REQUEST", "4")
    get_settings.cache_clear()

    from app.main import create_app

    fake_registry = ToolRegistry(build_fake_tool_registry_dict(), max_concurrent_requests=5)
    app = create_app(tool_registry_override=fake_registry, llm_client_override=MockLLMClient())
    yield app
    get_settings.cache_clear()


@pytest.fixture
def client(app_instance):
    with TestClient(app_instance) as test_client:
        yield test_client
