from datetime import UTC, datetime

import pytest

from app.evidence.database import Database
from app.evidence.memory import EvidenceMemory
from app.evidence.models import EvidenceRecord


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "evidence_test.db")
    await database.connect()
    yield database
    await database.close()


def _record(**overrides) -> EvidenceRecord:
    defaults = dict(
        place="Lisbon",
        criterion="WeatherTool",
        value=24.0,
        raw_value={"avg_high_c": 24.0},
        source_name="Open-Meteo",
        source_url="https://open-meteo.com/",
        retrieved_at=datetime.now(UTC),
        confidence="medium",
    )
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


@pytest.mark.asyncio
async def test_store_and_get_roundtrip(db):
    memory = EvidenceMemory(db)
    await memory.store(_record())
    results = await memory.get("Lisbon", "WeatherTool")
    assert len(results) == 1
    assert results[0].value == 24.0
    assert results[0].source_url == "https://open-meteo.com/"


@pytest.mark.asyncio
async def test_dedup_on_same_source_updates_in_place(db):
    memory = EvidenceMemory(db)
    await memory.store(_record(value=24.0))
    await memory.store(_record(value=26.0))
    results = await memory.get("Lisbon", "WeatherTool")
    assert len(results) == 1
    assert results[0].value == 26.0


@pytest.mark.asyncio
async def test_different_sources_are_preserved_separately(db):
    memory = EvidenceMemory(db)
    await memory.store(_record(source_name="Open-Meteo"))
    await memory.store(_record(source_name="Wikivoyage", criterion="WeatherTool", value=None))
    results = await memory.get("Lisbon", "WeatherTool")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_missing_place_returns_empty_list_not_fabricated(db):
    memory = EvidenceMemory(db)
    results = await memory.get("Nowhere", "WeatherTool")
    assert results == []


@pytest.mark.asyncio
async def test_get_all_for_place(db):
    memory = EvidenceMemory(db)
    await memory.store(_record(criterion="WeatherTool"))
    await memory.store(_record(criterion="AmenitiesTool", value=5.0))
    results = await memory.get_all_for_place("Lisbon")
    assert {r.criterion for r in results} == {"WeatherTool", "AmenitiesTool"}
