# PlaceMatch — Autonomous Evidence-Based Place Recommendation Agent

An autonomous AI agent that recommends cities, regions, or destinations from unrestricted
natural-language requests: remote work, studies, vacation, temporary relocation, family/cultural/
nature/business travel, or any mix of these.

---

## Course Submission Checklist

- [ ] **Replace all placeholders** in `config/team_info.json` (student names, emails, batch/order
      number, team name) before submission.
- [ ] **Re-register at LLMod.ai using the new email requested by the course** before requesting
      your project API key. Do this *before* filling in `LLMOD_API_KEY`/`LLMOD_MODEL` in `.env`.
- [ ] Fill in the real `LLMOD_API_KEY` and `LLMOD_MODEL` in your local `.env` (never commit it).
- [ ] **Submission deadline: 23 August 2026.**
- [ ] **Total LLM budget: $13 per group.** `MAX_PROJECT_BUDGET_USD=13` in `.env.example` already
      reflects this; do not raise it without team agreement.
- [ ] Run `pytest -q` and `ruff check .` one final time before submitting — both must pass with
      `MOCK_LLM=true` (the default), which costs $0.
- [ ] If deploying, replace the "deployment URL" placeholder below with the real one, or leave it
      documented as not yet deployed.
- [ ] **Deployment URL:** `REPLACE_WITH_DEPLOYMENT_URL_OR_"not deployed"`.

---

## What it does

Given a prompt like:

> "I want to spend three months somewhere in Europe where I can work remotely, live without a car,
> and stay within €1,800 per month."

PlaceMatch interprets the request, extracts hard constraints (budget cap, car-free) and soft
preferences, generates 4–5 diverse candidate destinations, decides *which* research tools are
actually relevant to this request (not a fixed list — see `ARCHITECTURE.md` §3), gathers evidence
from open data sources, verifies destination identity, deterministically scores and validates
candidates, and returns a ranked, explainable, source-cited Markdown recommendation.

### Supported request types

- Remote work / digital nomad stays
- Study / academic exchange
- Vacation / leisure travel
- Temporary relocation, family travel, cultural/nature/business travel
- Mixed-purpose requests (e.g. "work remotely while near a beach")

### Why this is autonomous, not a fixed pipeline

The agent does not run the same tools or ask the same questions for every prompt. Which research
tools run, what criteria are scored, how heavily each is weighted, and whether an extra research
round is needed are all decided dynamically from the interpreted request. See `ARCHITECTURE.md`
for the full explanation, including the conditional state machine and the "Agentic Research"
component (the module explicitly required to have this exact name).

---

## Architecture overview

```
Natural-Language Request → Request Interpreter → Agentic Research ⇄ Tool Registry → Evidence Memory
    → Dynamic Evaluation → Recommendation Validator ⇄ (back to Agentic Research, max 1x)
    → Recommendation Generator → Ranked Recommendations
```

Full diagram: `assets/model_architecture.png` (also served at `GET /api/model_architecture`).
Full explanation: `ARCHITECTURE.md`.

Canonical module names (used consistently in code, the diagram, `/api/agent_info`, and LLM-call
tracing — single source of truth in `app/core/module_names.py`):

`Request Interpreter`, `Agentic Research`, `Tool Registry`, `Evidence Memory`,
`Dynamic Evaluation`, `Recommendation Validator`, `Recommendation Generator`.

---

## Required API endpoints (exact contracts)

### `GET /api/team_info`

```json
{
  "group_batch_order_number": "1_2",
  "team_name": "Your Team Name",
  "students": [{"name": "Student A", "email": "a@example.com"}]
}
```

### `GET /api/agent_info`

```json
{
  "description": "...",
  "purpose": "...",
  "prompt_template": {"template": "..."},
  "prompt_examples": [
    {"prompt": "...", "full_response": "...", "steps": [{"module": "...", "prompt": {}, "response": {}}]}
  ]
}
```

### `GET /api/model_architecture`

Returns raw PNG bytes with `Content-Type: image/png`. Not JSON, not Base64, not a file path.

### `POST /api/execute`

Request:

```json
{"prompt": "I want to spend three months somewhere in Europe where I can work remotely..."}
```

Success response (always exactly these four top-level fields):

```json
{"status": "ok", "error": null, "response": "## Best matches\n...", "steps": [{"module": "Request Interpreter", "prompt": {}, "response": {}}]}
```

Error response (same four fields, never FastAPI's default `{"detail": [...]}`):

```json
{"status": "error", "error": "The prompt cannot be empty.", "response": null, "steps": []}
```

Every `/api/execute` request has a hard 285-second backend execution deadline. Under the default
configuration, research stops at 225 seconds and retains every completed tool result, leaving 60
seconds to score the partial evidence and generate the response. Pending calls are cancelled and
reported as missing evidence. If the recommendation-writing LLM is slow, a deterministic renderer
sends the recommendation instead. The 285-second emergency cutoff also returns a disclosed,
best-effort recommendation whenever the request is sufficiently clear, leaving 15 seconds for API,
transport, and UI overhead before the 300-second user-visible limit. If interpretation or candidate
generation fails early because the LLM provider times out, deterministic parsing and curated
candidate seeds keep the pipeline moving and are disclosed in the response.

---

## UI

`GET /` serves a small responsive HTML/CSS/vanilla-JS page (no framework, no separate Streamlit
service) with a prompt field, example buttons for remote work/study/vacation, loading and error
states, rendered recommendations, and an expandable "Execution steps" section showing every LLM
call's module/prompt/response. It calls `POST /api/execute` only — no agent logic is duplicated in
the frontend. The browser also aborts after 295 seconds as a final client-side guard; the backend's
285-second deadline should normally return a structured error first.

---

## Example prompts

- "I want to spend three months somewhere in Europe where I can work remotely, live without a car,
  and stay within €1,800 per month."
- "Recommend a city for a one-semester computer-science exchange. I care about student life,
  public transportation, safety, and affordable housing."
- "Find a quiet beach destination for two weeks in October, with warm but not extremely hot weather
  and good hiking nearby."

See `GET /api/agent_info` for full representative responses including the LLM-call trace for each.

---

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
pytest -q
ruff check .
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/` in a browser, or:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/team_info
Invoke-RestMethod http://127.0.0.1:8000/api/agent_info
Invoke-WebRequest http://127.0.0.1:8000/api/model_architecture -OutFile architecture_check.png
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/execute `
    -ContentType "application/json" `
    -Body (@{prompt = "Find a quiet beach destination for two weeks in October."} | ConvertTo-Json)
```

### PyCharm

Open the repository folder as a PyCharm project, set the interpreter to `.venv\Scripts\python.exe`
(File → Settings → Project → Python Interpreter → Add → Existing environment), then run/debug
`app/main.py` or use a PyCharm "FastAPI"/"Python" run configuration with
`uvicorn app.main:app --reload` as the run command. Mark `tests/` as a Test Sources root if you
want in-IDE test discovery.

---

## Environment variables

All variables are documented with safe defaults or empty placeholders in `.env.example`. Key ones:

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `llmod` (the course's primary live provider) |
| `MOCK_LLM` | `true` by default — zero-cost, deterministic `MockLLMClient` |
| `LLMOD_API_KEY`, `LLMOD_MODEL` | Real credentials, required only when `MOCK_LLM=false` |
| `LLMOD_BASE_URL`, `LLMOD_CHAT_COMPLETIONS_PATH`, `LLMOD_AUTH_HEADER`, `LLMOD_AUTH_SCHEME` | Provider request shape, isolated to `app/llm/llmod.py` |
| `MAX_PROJECT_BUDGET_USD` | Local hard cap, default `13` (the course budget) |
| `MAX_LLM_CALLS_PER_REQUEST` | Default `4` |
| `LLM_INPUT_COST_PER_1M`, `LLM_OUTPUT_COST_PER_1M` | Used only for conservative local cost *estimation* |
| `AGENT_EXECUTION_TIMEOUT_SECONDS` | Complete backend agent deadline; default and maximum `285` seconds |
| `RECOMMENDATION_RESERVE_SECONDS` | Time reserved after research for scoring/rendering; default `60` seconds |
| `TOOL_EXECUTION_TIMEOUT_SECONDS` | Complete budget for one tool/candidate invocation; default `50` seconds |
| `MAX_CONCURRENT_TOOL_REQUESTS` | Independent tool/candidate jobs allowed at once; default `10` |
| `UPSTREAM_REQUEST_TIMEOUT_SECONDS` | Optional declaration of the real proxy/platform timeout; must exceed `285` |
| `SQLITE_PATH`, `APP_PORT`, `HTTP_TIMEOUT_SECONDS`, `CACHE_TTL_HOURS` | Infra configuration |

**Never commit the real `.env` file** — it is already listed in `.gitignore`.

### LLMod.ai configuration

`LLModClient` (`app/llm/llmod.py`) assumes an OpenAI-compatible chat-completions request/response
shape by default, since that is the documented default for the course's LLMod.ai integration. If
the official configuration differs, only this one file needs to change — the rest of the
application is provider-agnostic via `BaseLLMClient`.

**Re-registration reminder:** the team must re-register for LLMod.ai using the new email requested
by the course *before* obtaining the project API key. Do this before filling in `.env`.

**Never commit the API key.** It is read only from the environment, registered with the logging
redaction filter so it can never leak into logs, and never included in exceptions or API responses.

### Optional connection check

`scripts/check_llmod_connection.py` is a manual, opt-in script that sends one small real request to
verify your LLMod.ai configuration. **It consumes real course credit and is never run
automatically** by tests, installation, or app startup.

```powershell
python scripts/check_llmod_connection.py
```

---

## Budget control

- **Local ledger (SQLite `llm_usage` table)**: every LLM call is recorded with token counts and
  either the provider-reported cost (when the provider returns usage/cost metadata) or a clearly
  flagged (`is_estimated=True`) conservative local estimate. `BudgetManager` refuses a call *before*
  it is made if the worst-case estimated cost would push the running total over
  `MAX_PROJECT_BUDGET_USD`, or if the per-request call cap (`MAX_LLM_CALLS_PER_REQUEST`) is reached.
- **Provider-side limit is authoritative.** The local ledger is an *additional safeguard*, not a
  substitute for LLMod.ai's own account-level limit. This project does **not** claim to know or
  display the official LLMod.ai remaining balance — only a locally estimated one, and it is always
  labeled as such.

---

## Data sources

- **Live/network**: OpenStreetMap Nominatim (geocoding/verification), Open-Meteo historical archive
  (climate evidence), OpenStreetMap Overpass (amenities/activities/accessibility density), Wikivoyage
  MediaWiki API (short place-context excerpts).
- **Local/curated (no network)**: `app/data/cost_estimates.csv` (sample/test-only cost ranges,
  clearly dated and labeled — never presented as live pricing), `app/data/official_sources.json`
  (curated official tourism/immigration links), a small curated university directory
  (`app/tools/education_options.py`), and a small curated origin→timezone map
  (`app/tools/timezone_fit.py`).

All outbound requests are restricted to an explicit domain allow-list with SSRF protections
(`app/core/security.py`) — there is no generic URL-fetch tool anywhere in the codebase.

## Cache behavior

SQLite-backed (`tool_cache` table), cache-first, with per-source TTLs (`app/evidence/cache.py`):
geocoding and climate-normal data cached long (30 days / 1 year), amenities/place-context medium
(2 weeks), official sources medium (1 week), weather forecasts short (1 day, and never reused as
long-term climate evidence — the two are stored under different cache keys). If a live call fails
and only expired cache is available, the stale value is returned but explicitly marked `stale=True`
— never silently presented as current.

---

## Testing

```powershell
pytest -q
ruff check .
```

The entire test suite (124 tests at last count) runs fully offline: `MockLLMClient` (zero LLM
cost) plus deterministic fake tool implementations (zero network calls), with an autouse fixture
that raises if any test ever attempts a real outbound HTTP request. Optional live tests (which
would spend real LLMod.ai credit) are gated behind `RUN_LIVE_TESTS=1` and are **not** included in
this repository by default — do not enable this flag casually.

Test layout: `tests/unit/` (schemas, state machine, tool selection, scoring, validator, budget,
security, caching, evidence memory, tools with only local/no-network data) and
`tests/integration/` (all 4 endpoints, contract/error-envelope enforcement, agent autonomy across
purposes, UI markup).

---

## FastAPI startup

```powershell
uvicorn app.main:app --reload
```

Startup validates `config/team_info.json` (fails fast if missing/malformed) and, if
`MOCK_LLM=false`, validates that `LLMOD_API_KEY`/`LLMOD_MODEL` are present and `LLMOD_BASE_URL` is
a syntactically valid URL — **without making any paid request at startup.**

---

## Docker

```powershell
docker build -t placematch .
docker run --rm -p 8000:8000 --env-file .env placematch
```

The image defaults to `MOCK_LLM=true` so a container never spends real money unless you explicitly
override it. `SQLITE_PATH` is configurable and can be mounted as a volume for persistence:

```powershell
docker run --rm -p 8000:8000 --env-file .env -v ${PWD}/data:/app/data placematch
```

## Deployment

No public deployment has been performed as part of this build. The Dockerfile above is sufficient
for any standard Docker-compatible host (a VM, a container platform, etc.). If/when deployed,
replace the placeholder deployment URL in the Course Submission Checklist above.

To preserve the under-300-second contract, configure every reverse proxy, load balancer, ingress,
and hosting platform in front of Uvicorn with a request/read/idle timeout of **at least 290 seconds**.
The backend returns by 285 seconds and the bundled UI stops waiting at 295 seconds. A common
platform default of 60 or 100 seconds will otherwise disconnect the user before the graceful
recommendation fallback arrives. `UPSTREAM_REQUEST_TIMEOUT_SECONDS=290` can declare the actual
infrastructure value for startup validation, but it does not configure the external platform by
itself. Direct API clients should wait at least 290 seconds as well.

Before deployment, run the offline suite normally. An explicitly opt-in live SLA check exercises
three representative prompts and targets an observed p95 below 240 seconds:

```powershell
$env:RUN_LIVE_TESTS="1"
$env:MOCK_LLM="false"
pytest -q -m live
```

This check uses real providers and consumes LLMod.ai project credit; it is always skipped during
ordinary `pytest` runs.

---

## Known limitations

- `cost_estimates.csv` contains hand-curated, dated, sample/test-only estimates, not live pricing —
  clearly labeled as such in every response.
- The origin→timezone mapping (`TimezoneFitTool`) covers a small curated set of common origins; an
  unmapped origin results in an honest "timezone overlap unknown" rather than a guess.
- The deterministic keyword-based Request Interpreter used by `MockLLMClient` is a simplified stand-in
  for the real LLMod.ai model; real LLM output will generally extract richer nuance.
- Candidate discovery draws from a modest curated seed pool per purpose (both in `MockLLMClient` and
  as a grounding aid for the real model); it is not an exhaustive global city database.
- No dedicated safety-rating data source is integrated; the system never fabricates a safety score
  and instead discloses it as missing evidence rather than guessing.

## Legal and ethical limitations

- PlaceMatch never claims live flight/hotel prices, guaranteed visa eligibility, guaranteed
  university admission, guaranteed safety, or exact current housing costs/travel times. Visa,
  entry, and immigration wording is always cautious and directs the user to official sources
  (`app/data/official_sources.json`, `OfficialSourceTool`).
- External retrieved content (Wikivoyage excerpts, etc.) is always treated as untrusted evidence,
  never as instructions — every LLM system prompt explicitly says so.
- No user data is persisted beyond what is necessary for evidence caching and the local budget
  ledger; no personal user information is collected by the API.

---

## Team information

Replace all placeholders in `config/team_info.json` (`REPLACE_WITH_...`) with the real team name,
batch/order number, and each student's name and email before submission.
