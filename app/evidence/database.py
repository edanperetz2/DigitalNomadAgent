"""SQLite connection management and schema initialization.

Uses aiosqlite for non-blocking access from the async FastAPI app. Schema
creation is idempotent (`CREATE TABLE IF NOT EXISTS`) so it is safe to call on
every startup.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  place TEXT NOT NULL,
  criterion TEXT NOT NULL,
  value REAL,
  raw_value_json TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT,
  retrieved_at TEXT NOT NULL,
  data_date TEXT,
  confidence TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  stale INTEGER NOT NULL DEFAULT 0,
  UNIQUE(place, criterion, source_name)
);

CREATE TABLE IF NOT EXISTS tool_cache (
  cache_key TEXT PRIMARY KEY,
  tool_name TEXT NOT NULL,
  place TEXT,
  params_json TEXT NOT NULL,
  response_json TEXT NOT NULL,
  cached_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  module TEXT NOT NULL,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  provider_cost_usd REAL,
  estimated_cost_usd REAL,
  is_estimated INTEGER NOT NULL,
  running_total_usd REAL NOT NULL,
  remaining_budget_usd REAL NOT NULL,
  success INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_search_sessions (
  id TEXT PRIMARY KEY,
  request_hash TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  original_request TEXT NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saved_search_sessions_updated_at
  ON saved_search_sessions(updated_at DESC);
"""

LEGACY_HISTORY_SQL = """
DROP TABLE IF EXISTS recommendation_results;
DROP TABLE IF EXISTS conversation_messages;
DROP TABLE IF EXISTS conversations;
"""


class Database:
    """Thin wrapper providing a shared aiosqlite connection per app lifecycle."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.executescript(LEGACY_HISTORY_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._conn
