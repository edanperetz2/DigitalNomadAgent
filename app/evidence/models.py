"""Shared data models for tool results and stored evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class ToolResult(BaseModel):
    """Generic result envelope every tool in the Tool Registry returns."""

    tool_name: str
    place: str
    normalized_data: dict[str, Any] = Field(default_factory=dict)
    source_name: str
    source_url: str | None = None
    retrieved_at: datetime
    data_date: str | None = None
    confidence: Confidence = "medium"
    warnings: list[str] = Field(default_factory=list)
    stale: bool = False
    error: str | None = None


class EvidenceRecord(BaseModel):
    """A single (place, criterion) fact stored in Evidence Memory."""

    place: str
    criterion: str
    value: float | None = None
    raw_value: dict[str, Any] = Field(default_factory=dict)
    source_name: str
    source_url: str | None = None
    retrieved_at: datetime
    data_date: str | None = None
    confidence: Confidence = "medium"
    warnings: list[str] = Field(default_factory=list)
    stale: bool = False
