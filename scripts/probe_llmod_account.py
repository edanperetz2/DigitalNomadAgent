"""Read-only probe of the configured LLMod.ai account. Costs $0.

Sends only GET requests to metadata endpoints. It never calls
/v1/chat/completions, so it consumes no tokens and no course credit. Run it
before any paid work to learn the available models, the real per-token
pricing, how much has been spent, and how much budget remains.

The balance that matters is the **account's** (`/user/info`), not the key's:
the key reports `max_budget: None` while the account carries the $13 cap, and
account spend runs ahead of key spend because the account is not this key
alone. Reading only /key/info is what produced D19's "no provider-side cap".

    python scripts/probe_llmod_account.py
    python scripts/probe_llmod_account.py --json

LLMod.ai presents an OpenAI-compatible surface and describes itself as a
per-learner budget proxy, which is the LiteLLM proxy shape -- so the LiteLLM
admin endpoints are probed alongside the standard ones. Endpoints that do not
exist simply report as unavailable; that is information, not an error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import redact, register_secret  # noqa: E402

# (path, why it is worth asking). All GET, all metadata-only.
PROBE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("/v1/models", "models this key may use"),
    ("/model/info", "per-model metadata, often including input/output pricing"),
    ("/key/info", "this key's own spend -- note the cap is NOT here, see /user/info"),
    ("/user/info", "account spend and max_budget -- the authoritative balance"),
    ("/spend/logs", "per-request spend history"),
    ("/global/spend", "aggregate spend"),
)


async def probe_endpoint(client: httpx.AsyncClient, base_url: str, path: str, headers: dict) -> dict:
    url = f"{base_url}{path}"
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return {"path": path, "ok": False, "error": f"{type(exc).__name__}: {redact(str(exc))}"}

    result: dict = {"path": path, "status_code": response.status_code, "ok": response.is_success}
    if not response.is_success:
        result["error"] = response.text[:300]
        return result
    try:
        result["body"] = response.json()
    except ValueError:
        result["body_text"] = response.text[:2000]
    return result


async def probe(settings) -> dict:
    if not settings.llmod_api_key:
        raise SystemExit(
            "LLMOD_API_KEY is not set. Copy .env.example to .env and fill it in.\n"
            "This probe makes no paid calls, but it does need a key to authenticate."
        )
    if not settings.llmod_api_key.isascii():
        raise SystemExit(
            "LLMOD_API_KEY contains non-ASCII characters and cannot be sent as an HTTP "
            "header.\nThis usually means a masked value was copied from a UI (e.g. "
            "'sk-abc' followed by bullet characters) rather than the real key.\n"
            "Re-copy the unmasked key into .env."
        )
    register_secret(settings.llmod_api_key)
    base_url = settings.llmod_base_url.rstrip("/")
    headers = {
        settings.llmod_auth_header: f"{settings.llmod_auth_scheme} {settings.llmod_api_key}".strip()
    }

    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=False) as client:
        results = []
        for path, purpose in PROBE_ENDPOINTS:
            outcome = await probe_endpoint(client, base_url, path, headers)
            outcome["purpose"] = purpose
            results.append(outcome)
    return {"base_url": base_url, "configured_model": settings.llmod_model, "endpoints": results}


def _budget_node(body: dict, *scopes: str) -> dict | None:
    """LiteLLM nests the useful object differently per endpoint."""
    for scope in scopes:
        node = body.get(scope)
        if isinstance(node, dict):
            return node
    return body if isinstance(body, dict) and "spend" in body else None


def _summarize_budget(body: dict, *, scopes: tuple[str, ...], spend_label: str) -> list[str]:
    """Spend, cap and remaining, for whichever object actually carries them.

    The cap is on the *account*, not the key: /key/info reports
    max_budget: None while /user/info reports max_budget: 13.0. This summary
    used to run for /key/info only, so the authoritative number was fetched on
    every probe and never shown, and the $13 cap was recorded as not existing
    (D19). Print both, and say which one binds.
    """
    node = _budget_node(body, *scopes)
    if node is None:
        return []
    lines = []
    if node.get("spend") is not None:
        lines.append(f"    {spend_label:<18}: {node['spend']}")
    for label, key in (("Account budget", "max_budget"), ("Budget resets", "budget_reset_at")):
        if node.get(key) is not None:
            lines.append(f"    {label:<18}: {node[key]}")
    spend, budget = node.get("spend"), node.get("max_budget")
    if isinstance(spend, int | float) and isinstance(budget, int | float):
        lines.append(f"    {'Remaining':<18}: {budget - spend:.4f}   <-- the cap that binds")
    return lines


def format_report(report: dict) -> str:
    lines = [
        "=" * 70,
        "LLMod.ai account probe (read-only -- no completions, $0)",
        "=" * 70,
        f"  Base URL         : {report['base_url']}",
        f"  Configured model : {report['configured_model'] or '(LLMOD_MODEL is not set)'}",
        "",
    ]
    for entry in report["endpoints"]:
        status = entry.get("status_code", "---")
        mark = "OK  " if entry.get("ok") else "n/a "
        lines.append(f"  [{mark}] {entry['path']:<16} {status}   ({entry['purpose']})")
        if not entry.get("ok"):
            continue
        body = entry.get("body")
        if entry["path"] == "/key/info" and isinstance(body, dict):
            lines.extend(_summarize_budget(body, scopes=("info",), spend_label="Spend (this key)"))
        elif entry["path"] == "/user/info" and isinstance(body, dict):
            lines.extend(
                _summarize_budget(body, scopes=("user_info",), spend_label="Spend (account)")
            )
        elif entry["path"] == "/v1/models" and isinstance(body, dict):
            models = [m.get("id") for m in body.get("data", []) if isinstance(m, dict)]
            for model_id in models[:40]:
                lines.append(f"    - {model_id}")
            if len(models) > 40:
                lines.append(f"    ... and {len(models) - 40} more")
    lines.append("")
    lines.append("Full JSON: re-run with --json")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Emit the raw probe results as JSON.")
    args = parser.parse_args()

    report = asyncio.run(probe(get_settings()))
    print(json.dumps(report, indent=2, default=str) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
