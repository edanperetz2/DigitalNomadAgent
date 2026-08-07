"""The deployment's own environment must satisfy the code's own validators.

`vercel.json` carries the production environment, and `.env` is gitignored, so
nothing in a normal local run exercises those values. Lowering
MAX_AGENT_EXECUTION_TIMEOUT_SECONDS from 285 to 270 made `vercel.json`'s
`AGENT_EXECUTION_TIMEOUT_SECONDS: "285"` fail its `le=` bound -- Settings would
have raised at import and the deployment would have crashed on the next cold
start, with every local test still green.

So: load the deployed environment through the real Settings model.
"""

import json
from pathlib import Path

from app.core.config import MAX_AGENT_EXECUTION_TIMEOUT_SECONDS, Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
VERCEL_JSON = REPO_ROOT / "vercel.json"

# Set for the platform, not for Settings.
NON_SETTINGS_KEYS = {"SQLITE_PATH"}


def _deployment_env() -> dict:
    return json.loads(VERCEL_JSON.read_text(encoding="utf-8"))["env"]


def test_the_deployed_environment_loads_through_the_real_settings_model():
    env = _deployment_env()
    settings = Settings(
        _env_file=None,
        **{k.lower(): v for k, v in env.items() if k not in NON_SETTINGS_KEYS},
    )
    assert settings.agent_execution_timeout_seconds <= MAX_AGENT_EXECUTION_TIMEOUT_SECONDS


def test_the_deployed_deadline_matches_the_code_ceiling():
    """Drift either way is a bug: above the ceiling refuses to boot, well below
    silently throws away research time nobody decided to give up."""
    env = _deployment_env()
    assert float(env["AGENT_EXECUTION_TIMEOUT_SECONDS"]) == MAX_AGENT_EXECUTION_TIMEOUT_SECONDS


def test_the_function_limit_still_exceeds_the_backend_deadline():
    """maxDuration is Vercel's hard kill; the backend budget must finish first."""
    config = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))
    max_duration = config["functions"]["main.py"]["maxDuration"]
    assert max_duration - MAX_AGENT_EXECUTION_TIMEOUT_SECONDS >= 30.0
