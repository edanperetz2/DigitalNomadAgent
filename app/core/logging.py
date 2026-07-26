"""Structured logging with secret redaction.

Any string that matches a configured secret value (e.g. the LLMod API key) is
replaced with a fixed redaction marker before it reaches a log record. This is
the single place responsible for keeping secrets out of logs.
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

_secret_values: list[str] = []


def register_secret(value: str | None) -> None:
    """Register a runtime secret value (e.g. an API key) for log redaction."""
    if value and value.strip():
        _secret_values.append(value.strip())


def redact(text: str) -> str:
    """Replace any registered secret value found in `text` with a marker."""
    redacted = text
    for secret in _secret_values:
        if secret:
            redacted = redacted.replace(secret, REDACTED)
    # Defensively redact common header patterns even if the secret wasn't registered.
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*)(bearer\s+)?[A-Za-z0-9._\-]{8,}",
        r"\1" + REDACTED,
        redacted,
    )
    return redacted


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("digitalnomadagent")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
    return logger


logger = configure_logging()
