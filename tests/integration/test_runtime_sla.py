import logging
import time

REPRESENTATIVE_PROMPTS = (
    "Find a quiet beach destination for two weeks in October with good hiking nearby.",
    "Recommend a city for a computer-science exchange with affordable housing and student life.",
    "Find a European city for three months of remote work with cafes and a moderate budget.",
)


def test_offline_representative_requests_finish_with_large_sla_margin(client):
    durations = []
    for prompt in REPRESENTATIVE_PROMPTS:
        started = time.monotonic()
        response = client.post("/api/execute", json={"prompt": prompt})
        durations.append(time.monotonic() - started)
        assert response.json()["status"] == "ok"

    assert max(durations) < 5.0


def test_runtime_telemetry_records_tool_phase_and_total_durations(client, caplog):
    caplog.set_level(logging.INFO, logger="placematch")

    response = client.post("/api/execute", json={"prompt": REPRESENTATIVE_PROMPTS[0]})

    assert response.json()["status"] == "ok"
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("tool_timing ") for message in messages)
    assert any(message.startswith("agent_phase ") for message in messages)
    assert any(message.startswith("agent_timing ") for message in messages)
