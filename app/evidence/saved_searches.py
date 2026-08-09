"""Persistence for complete saved search sessions."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.evidence.database import Database


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _request_hash(prompt: str) -> str:
    normalized = " ".join(prompt.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _title(prompt: str, max_length: int = 76) -> str:
    normalized = " ".join(prompt.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3].rstrip()}..."


class SavedSearchStore:
    """Store and load complete recommendation responses by stable session id."""

    def __init__(self, db: Database):
        self._db = db

    async def list_sessions(self) -> list[dict[str, Any]]:
        cursor = await self._db.conn.execute(
            """
            SELECT id, title, original_request, response_json, created_at, updated_at
            FROM saved_search_sessions
            ORDER BY updated_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [self._row_to_session(row) for row in rows]

    async def seed_examples(self, examples: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> int:
        """Write the shipped examples into a history that has none.

        Only into an empty one. A visitor's own conversations are the history;
        examples are what stands in for it before they have any, so anything
        already saved means these are not wanted.

        `created_at` is the real time the answer was produced, so the sidebar
        dates it honestly rather than implying it was generated on boot.
        `updated_at` is the sidebar's sort key and carries the curation order
        instead: the list runs newest first, so Example 1 needs the latest one
        for the numbering to read downwards. Both sit inside the window of the
        run they came from.

        Each row carries the true hash of its prompt, so a visitor who submits
        that same prompt updates the example rather than ending up with two
        copies of it.
        """
        cursor = await self._db.conn.execute("SELECT COUNT(*) FROM saved_search_sessions")
        row = await cursor.fetchone()
        if row[0]:
            return 0

        seeded = 0
        for example in examples:
            prompt = " ".join(str(example["prompt"]).split())
            generated_at = example.get("generated_at") or _now_iso()
            listed_at = example.get("listed_at") or generated_at
            await self._db.conn.execute(
                """
                INSERT OR IGNORE INTO saved_search_sessions
                  (id, request_hash, title, original_request, response_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    example["id"],
                    _request_hash(prompt),
                    example.get("title") or _title(prompt),
                    prompt,
                    json.dumps(example["result"], ensure_ascii=False, separators=(",", ":")),
                    generated_at,
                    listed_at,
                ),
            )
            seeded += 1

        await self._db.conn.commit()
        return seeded

    async def save_session(self, *, prompt: str, result_data: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        normalized_prompt = " ".join(prompt.split())
        request_hash = _request_hash(normalized_prompt)
        response_json = json.dumps(result_data, ensure_ascii=False, separators=(",", ":"))

        cursor = await self._db.conn.execute(
            "SELECT id, created_at FROM saved_search_sessions WHERE request_hash = ?",
            (request_hash,),
        )
        existing = await cursor.fetchone()

        if existing:
            session_id = existing[0]
            await self._db.conn.execute(
                """
                UPDATE saved_search_sessions
                SET title = ?, original_request = ?, response_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (_title(normalized_prompt), normalized_prompt, response_json, now, session_id),
            )
        else:
            session_id = str(uuid4())
            await self._db.conn.execute(
                """
                INSERT INTO saved_search_sessions
                  (id, request_hash, title, original_request, response_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, request_hash, _title(normalized_prompt), normalized_prompt, response_json, now, now),
            )

        await self._db.conn.commit()
        session = await self.get_session(session_id)
        if session is None:
            raise RuntimeError("Saved search session was not persisted.")
        return session

    async def clear_all(self) -> None:
        await self._db.conn.execute("DELETE FROM saved_search_sessions")
        await self._db.conn.commit()

    async def delete_sessions(self, session_ids: list[str]) -> None:
        if not session_ids:
            return
        placeholders = ",".join("?" for _ in session_ids)
        await self._db.conn.execute(
            f"DELETE FROM saved_search_sessions WHERE id IN ({placeholders})",
            session_ids,
        )
        await self._db.conn.commit()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        cursor = await self._db.conn.execute(
            """
            SELECT id, title, original_request, response_json, created_at, updated_at
            FROM saved_search_sessions
            WHERE id = ?
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_session(row) if row else None

    def _row_to_session(self, row) -> dict[str, Any]:
        result_data = json.loads(row[3])
        return {
            "id": row[0],
            "title": row[1],
            "original_request": row[2],
            "prompt": row[2],
            "result": result_data,
            "response": result_data.get("response"),
            "steps": result_data.get("steps", []),
            "created_at": row[4],
            "updated_at": row[5],
            "createdAt": row[4],
            "updatedAt": row[5],
        }
