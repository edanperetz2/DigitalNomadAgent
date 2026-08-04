"""Golden-set harness CLI.

Default (no flags): runs entirely against MockLLMClient. Zero cost, safe to
run anytime, and this is what CI/regression checks should use.

    python scripts/run_golden_set.py

WARNING: --real sends one real request per golden-set case to LLMod.ai and
WILL consume course credit. Tool calls stay fake either way -- only the
LLM-calling modules' actual output is being evaluated. Never run --real
automatically; it is opt-in only.

    python scripts/run_golden_set.py --real
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import ConfigurationError  # noqa: E402
from app.llm.llmod import LLModClient  # noqa: E402
from app.llm.mock import MockLLMClient  # noqa: E402
from scripts.golden_set.runner import format_report, run_golden_set  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use the real configured LLMod.ai client instead of MockLLMClient. Spends real course credit.",
    )
    args = parser.parse_args()

    if args.real:
        print("=" * 70)
        print("Running the golden set against the REAL LLMod.ai provider.")
        print("This WILL send one real request per case and consume course credit.")
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
                timeout_seconds=settings.llm_http_timeout_seconds,
                temperature=settings.llm_temperature,
            )
        except ConfigurationError as exc:
            print(f"Configuration error: {exc}")
            return 1
    else:
        print("Running the golden set against MockLLMClient (zero cost).")
        client = MockLLMClient()

    results = run_golden_set(client)
    print()
    print(format_report(results))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
