"""Manual, opt-in LLMod.ai connectivity check.

Run explicitly: python scripts/check_llmod_connection.py

WARNING: this sends one small real request to LLMod.ai and WILL consume
course credit. It is never run automatically by installation, tests, or
application startup.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import ConfigurationError, LLMOutputError  # noqa: E402
from app.llm.llmod import LLModClient  # noqa: E402


async def main() -> int:
    print("=" * 70)
    print("LLMod.ai connection check")
    print("This will send ONE small real request and MAY consume course credit.")
    print("=" * 70)

    settings = get_settings()
    try:
        client = LLModClient(
            api_key=settings.llmod_api_key,
            base_url=settings.llmod_base_url,
            chat_completions_path=settings.llmod_chat_completions_path,
            model=settings.llmod_model,
            auth_header=settings.llmod_auth_header,
            auth_scheme=settings.llmod_auth_scheme,
            timeout_seconds=settings.http_timeout_seconds,
        )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 1

    try:
        response = await client.complete(
            [
                {"role": "system", "content": "Reply with exactly one word."},
                {"role": "user", "content": "Say OK."},
            ],
            max_output_tokens=5,
        )
    except LLMOutputError as exc:
        print(f"Connection check FAILED: {exc}")
        return 1

    print("Connection check SUCCEEDED.")
    print(f"Model responded: {response.text!r}")
    if response.input_tokens is not None or response.output_tokens is not None:
        print(f"Reported usage: input_tokens={response.input_tokens}, output_tokens={response.output_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
