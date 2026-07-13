"""Shared pytest fixtures.

Every test runs fully offline: MockLLMClient (zero cost) + FakeToolRegistry
(no network) + a temporary SQLite database per test. An autouse fixture also
blocks any real outbound HTTP call so a bug can never silently make a real
network request during the test suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.llm.mock import MockLLMClient
from app.tools.fakes import build_fake_tool_registry_dict
from app.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch):
    import httpx

    async def _blocked_send(*args, **kwargs):
        raise RuntimeError("Real network access is blocked during tests. Use fake tools/MockLLMClient.")

    monkeypatch.setattr(httpx.AsyncClient, "send", _blocked_send)


@pytest.fixture
def app_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test_placematch.db"))
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
