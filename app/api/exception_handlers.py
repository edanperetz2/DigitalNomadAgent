"""Custom exception handlers enforcing the strict /api/execute response contract.

FastAPI's default validation error body ({"detail": [...]}) must never reach the
client for /api/execute. Every error path (validation, budget refusal, internal
exception) is converted to the exact four-field envelope:
{"status": "error", "error": <human-readable>, "response": None, "steps": []}
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import BudgetExceededError, PlaceMatchError
from app.core.logging import logger, redact

_EXECUTE_PATH = "/api/execute"


def _error_envelope(message: str) -> dict:
    return {"status": "error", "error": redact(message), "response": None, "steps": []}


def _first_readable_error(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "The request payload is invalid."
    first = errors[0]
    err_type = first.get("type", "")
    loc = first.get("loc", ())
    msg = first.get("msg", "The request payload is invalid.")

    if err_type == "json_invalid":
        return "The request body is not valid JSON."
    if "prompt" in loc:
        if err_type == "missing":
            return "The prompt is required."
        if "at least" in msg.lower():
            return "The prompt cannot be empty."
        return f"Invalid prompt: {msg}"
    return "The request payload is invalid."


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path != _EXECUTE_PATH:
        # Only /api/execute has a strict contract to preserve; other endpoints
        # take no client-supplied body, so this path is effectively unreachable
        # for them, but we still avoid leaking FastAPI's default shape.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request."},
        )
    message = _first_readable_error(exc)
    logger.warning("Validation error on %s: %s", request.url.path, redact(message))
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=_error_envelope(message))


async def budget_exceeded_handler(request: Request, exc: BudgetExceededError) -> JSONResponse:
    logger.warning("Budget refusal: %s", redact(str(exc)))
    return JSONResponse(status_code=status.HTTP_402_PAYMENT_REQUIRED, content=_error_envelope(str(exc)))


async def place_match_error_handler(request: Request, exc: PlaceMatchError) -> JSONResponse:
    logger.warning("Application error: %s", redact(str(exc)))
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=_error_envelope(str(exc)))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s", request.url.path)
    if request.url.path != _EXECUTE_PATH:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error."},
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_envelope("An unexpected internal error occurred."),
    )
