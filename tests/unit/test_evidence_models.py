from datetime import UTC, datetime

import pytest

from app.agent.orchestrator import Orchestrator
from app.evidence.models import EvidenceItem, EvidenceSource, ToolResult


def _source(name: str) -> EvidenceSource:
    return EvidenceSource(source_name=name, retrieved_at=datetime.now(UTC))


def test_legacy_tool_result_resolves_to_one_current_source_item():
    result = ToolResult(
        tool_name="WeatherTool",
        place="Lisbon",
        normalized_data={"avg_high_c": 24.0},
        source_name="Open-Meteo",
        retrieved_at=datetime.now(UTC),
    )
    result.stale = True

    items = result.resolved_evidence_items()

    assert len(items) == 1
    assert items[0].criterion == "WeatherTool"
    assert items[0].source.source_name == "Open-Meteo"
    assert items[0].source.stale is True


@pytest.mark.asyncio
async def test_each_explicit_source_item_is_persisted_independently():
    class RecordingMemory:
        def __init__(self):
            self.records = []

        async def store(self, record):
            self.records.append(record)

    memory = RecordingMemory()
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._evidence = memory
    result = ToolResult(
        tool_name="SafetyTool",
        place="Lisbon",
        source_name="PlaceMatch safety composite",
        retrieved_at=datetime.now(UTC),
        evidence_items=[
            EvidenceItem(
                criterion="safety",
                component="travel_advisory",
                value=1.0,
                normalized_data={"status": "no warning"},
                source=_source("FCDO"),
            ),
            EvidenceItem(
                criterion="safety",
                component="homicide_rate",
                value=0.85,
                normalized_data={"rate": 3.0},
                source=_source("World Bank"),
            ),
        ],
    )

    await orchestrator._persist_evidence("Lisbon", [result])

    assert [record.source_name for record in memory.records] == ["FCDO", "World Bank"]
    assert [record.raw_value["component"] for record in memory.records] == [
        "travel_advisory",
        "homicide_rate",
    ]


def test_source_collection_includes_every_explicit_source():
    result = ToolResult(
        tool_name="CompositeTool",
        place="Lisbon",
        source_name="Composite",
        retrieved_at=datetime.now(UTC),
        evidence_items=[
            EvidenceItem(criterion="test", source=_source("Source A")),
            EvidenceItem(criterion="test", source=_source("Source B")),
        ],
    )

    sources = Orchestrator._collect_sources({"Lisbon": [result]})

    assert {source["source_name"] for source in sources} == {"Source A", "Source B"}
