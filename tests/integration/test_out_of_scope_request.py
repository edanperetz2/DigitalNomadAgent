"""Full /api/execute path for a prompt that is not a travel/relocation request
at all -- mirrors test_agent_autonomy.py's client.post()/_stored_tool_names
style, but controls the Request Interpreter's decision directly since
MockLLMClient's rule-based parser has no domain-relevance logic of its own."""

import pytest

from app.agent import orchestrator as orchestrator_module
from app.agent.models import PlaceRequestProfile


async def _stored_tool_names(app_instance) -> set[str]:
    cursor = await app_instance.state.db.conn.execute("SELECT DISTINCT criterion FROM evidence")
    rows = await cursor.fetchall()
    return {row[0] for row in rows}


@pytest.mark.asyncio
async def test_off_topic_prompt_declines_and_stores_no_evidence(client, app_instance, monkeypatch):
    async def interpreted(*args, **kwargs):
        return PlaceRequestProfile(purpose="unknown", in_scope=False)

    monkeypatch.setattr(orchestrator_module, "interpret_request", interpreted)

    response = client.post(
        "/api/execute",
        json={"prompt": "How do I make a sourdough starter?"},
    )

    body = response.json()
    assert body["status"] == "ok"
    assert body["error"] is None
    assert "travel" in body["response"].lower() or "relocation" in body["response"].lower()

    tool_names = await _stored_tool_names(app_instance)
    assert tool_names == set()
