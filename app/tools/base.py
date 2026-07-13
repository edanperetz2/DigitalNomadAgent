"""Shared tool interface. All tools (real or fake) implement this Protocol so
they can be swapped via dependency injection in tests."""

from __future__ import annotations

from typing import Protocol

from app.agent.models import CandidatePlace, PlaceRequestProfile
from app.evidence.models import ToolResult


class BaseTool(Protocol):
    name: str

    async def run(self, candidate: CandidatePlace, profile: PlaceRequestProfile) -> ToolResult: ...
