"""Generic SQLite-backed cache for tool results, with per-tool TTLs.

Cache-first is used by every network-calling tool so repeated research for the
same place never re-issues the same external request within the TTL window.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from app.evidence.database import Database

# Bump this whenever a change alters what a cached tool result *contains* --
# the shape of `normalized_data`, a source name or URL, an evidence item. The
# key is derived from it, so bumping retires every existing row at once.
#
# It exists because a cached result outlives the code that produced it. D44
# replaced the Overpass source URL (the API's documentation page was being cited
# as the source of its counts) and this constant was not bumped, so 73 rows kept
# serving the old citation under a 14-day TTL. A run on 2026-08-07 reported it
# and it read exactly like a regression in a fix that was, in fact, correct.
#
# Two costs, both real: deployed readers keep pre-fix content until the TTL
# expires, and a validation run can show a fixed defect as still broken -- or
# hide a live one. `test_cache_contract_version.py` pins the source identities
# below so this cannot be forgotten silently again (D59).
# v4 (2026-08-10): LanguageTool's SOURCE_URL pointed at `shanigoren/
# DigitalNomadAgent`, which 404s -- the repository is owned by `edanperetz2`.
# Cached language rows carry the dead link, so they have to be retired.
CACHE_CONTRACT_VERSION = 4

# Per-tool TTL in hours. Distinguishes short-lived forecasts from long-lived
# climate normals / geocoding results per spec caching guidance (section 17).
TOOL_TTL_HOURS: dict[str, int] = {
    "GeocodingTool": 24 * 30,
    "WeatherTool:forecast": 24,
    "WeatherTool:climate": 24 * 365,
    "WikivoyageClimateTool": 24 * 14,
    "OriginResolver:open_meteo": 24 * 30,
    "AmenitiesTool": 24 * 14,
    "LocalMobilityTool": 24 * 14,
    "PlaceContextTool": 24 * 14,
    "BudgetFitTool:coverage": 24 * 30,
    "BudgetFitTool:city_prices": 24 * 30,
    "BudgetFitTool:country_costs": 24 * 30,
    "BudgetFitTool:exchange_rate": 24,
    "TransportAccessTool": 24 * 14,
    "ActivitiesTool": 24 * 14,
    "SafetyTool": 24,
}


def _cache_key(tool_name: str, place: str, params: dict) -> str:
    payload = json.dumps(
        {"version": CACHE_CONTRACT_VERSION, "tool": tool_name, "place": place, "params": params}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ToolCache:
    def __init__(self, db: Database, default_ttl_hours: int = 168):
        self._db = db
        self._default_ttl_hours = default_ttl_hours

    def _ttl_hours(self, ttl_key: str) -> int:
        return TOOL_TTL_HOURS.get(ttl_key, self._default_ttl_hours)

    async def get(
        self, tool_name: str, place: str, params: dict, ttl_key: str | None = None
    ) -> tuple[dict | None, bool]:
        """Return (cached_response, is_stale). (None, False) on a miss."""
        key = _cache_key(tool_name, place, params)
        cursor = await self._db.conn.execute(
            "SELECT response_json, expires_at FROM tool_cache WHERE cache_key = ?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None, False
        response_json, expires_at = row
        expired = datetime.fromisoformat(expires_at) < datetime.now(UTC)
        return json.loads(response_json), expired

    async def set(self, tool_name: str, place: str, params: dict, response: dict, ttl_key: str | None = None) -> None:
        key = _cache_key(tool_name, place, params)
        ttl = self._ttl_hours(ttl_key or tool_name)
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=ttl)
        await self._db.conn.execute(
            """
            INSERT INTO tool_cache (cache_key, tool_name, place, params_json, response_json, cached_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                response_json=excluded.response_json,
                cached_at=excluded.cached_at,
                expires_at=excluded.expires_at
            """,
            (
                key,
                tool_name,
                place,
                json.dumps(params, sort_keys=True),
                json.dumps(response),
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        await self._db.conn.commit()
