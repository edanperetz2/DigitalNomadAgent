"""Shared data models for tool results and stored evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class EvidenceSource(BaseModel):
    """Identity and freshness metadata for one external evidence source."""

    source_name: str
    source_url: str | None = None
    retrieved_at: datetime
    data_date: str | None = None
    confidence: Confidence = "medium"
    stale: bool = False


class EvidenceItem(BaseModel):
    """One independently attributable component returned by a tool."""

    criterion: str
    component: str = "result"
    value: float | None = None
    normalized_data: dict[str, Any] = Field(default_factory=dict)
    source: EvidenceSource
    warnings: list[str] = Field(default_factory=list)


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
    evidence_items: list[EvidenceItem] = Field(default_factory=list)

    def resolved_evidence_items(self) -> list[EvidenceItem]:
        """Return explicit source items or adapt the legacy single-source envelope.

        Existing tools still populate the top-level source fields. Keeping this
        adapter dynamic means stale-cache mutations remain reflected in stored
        evidence while tools are migrated one at a time in later commits.
        """
        if self.evidence_items:
            return self.evidence_items
        return [
            EvidenceItem(
                criterion=self.tool_name,
                normalized_data=self.normalized_data,
                source=EvidenceSource(
                    source_name=self.source_name,
                    source_url=self.source_url,
                    retrieved_at=self.retrieved_at,
                    data_date=self.data_date,
                    confidence=self.confidence,
                    stale=self.stale,
                ),
                warnings=self.warnings,
            )
        ]


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
