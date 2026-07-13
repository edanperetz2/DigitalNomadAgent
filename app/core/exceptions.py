"""Typed application exceptions. Messages must always be human-readable and
must never include secrets (API keys, auth headers) or raw chain-of-thought."""


class PlaceMatchError(Exception):
    """Base class for all application-level errors surfaced to /api/execute."""


class ConfigurationError(PlaceMatchError):
    """Raised at startup when required configuration is missing or invalid."""


class BudgetExceededError(PlaceMatchError):
    """Raised when a call would exceed the local project budget or call cap."""


class LLMOutputError(PlaceMatchError):
    """Raised when an LLM response could not be parsed/validated, even after repair."""


class NoViableCandidatesError(PlaceMatchError):
    """Raised when no candidate places survive verification or hard constraints."""


class ToolExecutionError(PlaceMatchError):
    """Raised only for catastrophic tool-layer failures; individual tool failures
    are captured inside ToolResult.error instead of raising."""
