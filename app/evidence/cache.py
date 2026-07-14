"""Generic SQLite-backed cache for tool results, with per-tool TTLs.

Cache-first is used by every network-calling tool so repeated research for the
same place never re-issues the same external request within the TTL window.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from app.evidence.database import Database

CACHE_CONTRACT_VERSION = 2

# Per-tool TTL in hours. Distinguishes short-lived forecasts from long-lived
# climate normals / geocoding results per spec caching guidance (section 17).
TOOL_TTL_HOURS: dict[str, int] = {
    "GeocodingTool": 24 * 30,
    "WeatherTool:forecast": 24,
    "WeatherTool:climate": 24 * 365,
    "WikivoyageClimateTool": 24 * 14,
    "TimezoneFitTool:origin": 24 * 30,
    "AmenitiesTool": 24 * 14,
    "PlaceContextTool": 24 * 14,
    "BudgetFitTool": 24 * 30,
    "EducationOptionsTool": 24 * 30,
    "AccessibilityTool": 24 * 14,
    "ActivitiesTool": 24 * 14,
    "OfficialSourceTool": 24 * 7,
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
