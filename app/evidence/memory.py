"""Evidence Memory: deduplicated, source-traceable storage of evidence facts.

Backed by the `evidence` SQLite table. Never turns missing evidence into a
positive score -- callers simply get an empty list back when nothing is known.
"""

from __future__ import annotations

import json
from datetime import datetime

from app.evidence.database import Database
from app.evidence.models import EvidenceRecord


class EvidenceMemory:
    def __init__(self, db: Database):
        self._db = db

    async def store(self, record: EvidenceRecord) -> None:
        """Insert or update a (place, criterion, source_name) evidence fact."""
        await self._db.conn.execute(
            """
            INSERT INTO evidence
                (place, criterion, value, raw_value_json, source_name, source_url,
                 retrieved_at, data_date, confidence, warnings_json, stale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(place, criterion, source_name) DO UPDATE SET
                value=excluded.value,
                raw_value_json=excluded.raw_value_json,
                source_url=excluded.source_url,
                retrieved_at=excluded.retrieved_at,
                data_date=excluded.data_date,
                confidence=excluded.confidence,
                warnings_json=excluded.warnings_json,
                stale=excluded.stale
            """,
            (
                record.place,
                record.criterion,
                record.value,
                json.dumps(record.raw_value),
                record.source_name,
                record.source_url,
                record.retrieved_at.isoformat(),
                record.data_date,
                record.confidence,
                json.dumps(record.warnings),
                int(record.stale),
            ),
        )
        await self._db.conn.commit()

    async def get(self, place: str, criterion: str) -> list[EvidenceRecord]:
        """Return all evidence records for a (place, criterion) pair, any source."""
        cursor = await self._db.conn.execute(
            "SELECT place, criterion, value, raw_value_json, source_name, source_url, "
            "retrieved_at, data_date, confidence, warnings_json, stale "
            "FROM evidence WHERE place = ? AND criterion = ?",
            (place, criterion),
        )
        rows = await cursor.fetchall()
        return [_row_to_record(row) for row in rows]

    async def get_all_for_place(self, place: str) -> list[EvidenceRecord]:
        cursor = await self._db.conn.execute(
            "SELECT place, criterion, value, raw_value_json, source_name, source_url, "
            "retrieved_at, data_date, confidence, warnings_json, stale "
            "FROM evidence WHERE place = ?",
            (place,),
        )
        rows = await cursor.fetchall()
        return [_row_to_record(row) for row in rows]


def _row_to_record(row: tuple) -> EvidenceRecord:
    return EvidenceRecord(
        place=row[0],
        criterion=row[1],
        value=row[2],
        raw_value=json.loads(row[3]),
        source_name=row[4],
        source_url=row[5],
        retrieved_at=datetime.fromisoformat(row[6]),
        data_date=row[7],
        confidence=row[8],
        warnings=json.loads(row[9]),
        stale=bool(row[10]),
    )
