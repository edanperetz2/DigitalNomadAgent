import pytest

from app.evidence.cache import TOOL_TTL_HOURS, ToolCache
from app.evidence.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "cache_test.db")
    await database.connect()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_cache_miss_returns_none(db):
    cache = ToolCache(db)
    value, stale = await cache.get("GeocodingTool", "Lisbon", {})
    assert value is None
    assert stale is False


@pytest.mark.asyncio
async def test_cache_hit_within_ttl(db):
    cache = ToolCache(db)
    await cache.set("GeocodingTool", "Lisbon", {}, {"lat": 38.7})
    value, stale = await cache.get("GeocodingTool", "Lisbon", {})
    assert value == {"lat": 38.7}
    assert stale is False


def test_wikivoyage_climate_cache_ttl_is_two_weeks():
    assert TOOL_TTL_HOURS["WikivoyageClimateTool"] == 24 * 14


@pytest.mark.asyncio
async def test_cache_expired_marked_stale(db, monkeypatch):
    cache = ToolCache(db)
    # A tool with a 0-hour TTL should be immediately considered expired.
    from app.evidence import cache as cache_module

    monkeypatch.setitem(cache_module.TOOL_TTL_HOURS, "WeatherTool:forecast", 0)
    await cache.set("WeatherTool:forecast", "Lisbon", {}, {"temp": 20}, ttl_key="WeatherTool:forecast")
    import asyncio

    await asyncio.sleep(0.01)
    value, stale = await cache.get("WeatherTool:forecast", "Lisbon", {}, ttl_key="WeatherTool:forecast")
    assert value == {"temp": 20}
    assert stale is True


@pytest.mark.asyncio
async def test_different_params_are_different_cache_entries(db):
    cache = ToolCache(db)
    await cache.set("AmenitiesTool", "Lisbon", {"categories": ["cafe"]}, {"count": 5})
    await cache.set("AmenitiesTool", "Lisbon", {"categories": ["museum"]}, {"count": 9})
    v1, _ = await cache.get("AmenitiesTool", "Lisbon", {"categories": ["cafe"]})
    v2, _ = await cache.get("AmenitiesTool", "Lisbon", {"categories": ["museum"]})
    assert v1 == {"count": 5}
    assert v2 == {"count": 9}


def test_cache_key_includes_contract_version(monkeypatch):
    from app.evidence import cache as cache_module

    first = cache_module._cache_key("WeatherTool", "Lisbon", {})
    monkeypatch.setattr(cache_module, "CACHE_CONTRACT_VERSION", cache_module.CACHE_CONTRACT_VERSION + 1)
    second = cache_module._cache_key("WeatherTool", "Lisbon", {})

    assert first != second
