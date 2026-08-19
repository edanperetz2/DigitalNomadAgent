# DigitalNomadAgent — Autonomous Evidence-Based Place Recommendation Agent

An autonomous AI agent that recommends cities, regions, or destinations from unrestricted
natural-language requests: remote work, studies, vacation, temporary relocation, family/cultural/
nature/business travel, or any mix of these.

---

## Course Submission Checklist

- [x] **Replace all placeholders** in `config/team_info.json` (student names, emails, batch/order
      number, team name) before submission.
- [x] **Re-register at LLMod.ai using the new email requested by the course** before requesting
      your project API key. Do this *before* filling in `LLMOD_API_KEY`/`LLMOD_MODEL` in `.env`.
- [x] Fill in the real `LLMOD_API_KEY` and `LLMOD_MODEL` in your local `.env` (never commit it).
- [ ] **Submission deadline: 23 August 2026.**
- [ ] **Total LLM budget: $13 per group.** `MAX_PROJECT_BUDGET_USD=13` in `.env.example` already
      reflects this; do not raise it without team agreement.
- [x] Run `pytest -q` and `ruff check .` one final time before submitting — both must pass with
      `MOCK_LLM=true` (the default), which costs $0.
- [x] Deploy on Vercel (required by the course spec — see Deployment section below) and replace
      the placeholders below with the real URLs before submitting.
- [x] **Vercel URL:** `https://digitalnomadagent.vercel.app/`
- [x] **GitHub Repo URL:** `https://github.com/edanperetz2/DigitalNomadAgent`

---

## What it does

Given a prompt like:

> "I want to spend three months somewhere in Europe where I can work remotely, live without a car,
> and stay within €1,800 per month."

DigitalNomadAgent extracts hard constraints (budget scoped to what it actually covers — e.g.
accommodation-only vs. total monthly living cost, never compared against the wrong kind of evidence
— plus things like car-free) and soft preferences, then:

1. **Recall** — one LLM call proposes up to 30 broad candidates.
2. **Filter** — a cheap deterministic pass (geocoding, region checks, budget ranking; no LLM, no
   expensive tools) narrows these to up to 8 finalists.
3. **Research** — the agent decides *which* tools are actually relevant to this request, not a
   fixed list (see `ARCHITECTURE.md` §3), and gathers evidence from open data sources.
4. **Score** — climate/work-infrastructure/timezone/student-life/safety are scored deterministically;
   cost/transportation/accessibility/activities via one batched LLM call.
5. **Validate and answer** — a ranked, explainable, source-cited Markdown recommendation.

### Supported request types

- Remote work / digital nomad stays
- Study / academic exchange
- Vacation / leisure travel
- Temporary relocation, family travel, cultural/nature/business travel
- Mixed-purpose requests (e.g. "work remotely while near a beach")

### Why this is autonomous, not a fixed pipeline

Which tools run, how criteria are weighted, and whether an extra research round happens are all
decided dynamically per request — an off-topic request is declined before any research starts,
rather than force-fit into an answer. Full explanation, including the conditional state machine:
`ARCHITECTURE.md`.

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

`prompt_examples` are **real answers**, captured from live runs against the deployment and stored in
`assets/prompt_examples.json` (exported by `scripts/export_prompt_examples.py`). The `full_response`
is the exact Markdown the agent returned and each step carries the actual `System_prompt` /
`User_prompt` it sent — not a summary. The endpoint itself makes no LLM and no network calls; it is a
file read, with generated examples as a fallback if the asset is ever unreadable.

### `GET /api/model_architecture`

Returns raw PNG bytes with `Content-Type: image/png`. Not JSON, not Base64, not a file path.

The image is generated by `scripts/render_architecture.py` from `app/core/module_names.py`, so the
module and tool names on it are the names in the code by construction;
`tests/unit/test_architecture_png_file.py` fails if they drift apart.

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

Hard backend deadline: **270s** total (research stops at 210s, keeping every completed tool result;
the remaining 60s score partial evidence and generate the response), inside the course's 300s Vercel
ceiling. Pending tool calls are cancelled and reported as missing evidence; a slow or unreachable LLM
falls back to deterministic parsing/rendering at every stage, always disclosed in the response —
never a raw error or a hang.

The request body is always exactly `{"prompt": "..."}` for every caller, including automated
grading — a bare call like that always resolves to one final, actionable recommendation and never
stops mid-way to ask a clarifying question, even for an ambiguous prompt (the would-be question is
instead disclosed as a stated assumption inside the response text). The deployed frontend is the one
exception: it sends an additional `X-Interactive-Mode: true` request header (never a body field, so
the documented request shape never changes), which restores the original behavior for a human at the
keyboard — a genuinely ambiguous prompt gets a real clarification question back instead of a guess.

### Additive, non-required endpoints

`DELETE /api/history` and `POST /api/history/delete` back the sidebar's clear-history controls
(clear all / clear selected conversations). Neither is one of the 4 required course-spec endpoints
above, and neither is used by the agent pipeline itself — purely UI convenience, safe to ignore for
grading purposes.

---

## UI

`GET /` serves a small responsive HTML/CSS/vanilla-JS page (no framework) with a prompt field,
purpose example buttons, loading/error states, rendered recommendations, and an expandable
"Execution steps" section showing every LLM call's module/prompt/response. It calls `POST
/api/execute` only — no agent logic is duplicated in the frontend. A live elapsed timer plus a
"still thinking" note after 15s cover the 40-110+ second typical response time; the browser aborts
after 295s as a final client-side guard, behind the backend's own 270s deadline (see above).

Each ranked place shows a **Fit Score** (reference only — the UI never re-sorts by it) and an
**Evidence Coverage** label; `[N]` citation markers link to their Sources entry (a plain chip if no
URL exists). The interactive path (see "Required API endpoints" above) shows an inline reply box
instead of a dead end when a clarification question comes back — capped at 2 questions per thread,
after which the reply is sent non-interactively so the conversation always resolves to a real
answer. History can be multi-selected and cleared via `DELETE /api/history` /
`POST /api/history/delete` (additive, not required endpoints). The sidebar collapses to an icon rail
or resizes (260-480px); both persist via `localStorage`.

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
| `LLM_MAX_OUTPUT_TOKENS` | Default `12000` — too low truncates real recommendation output mid-JSON |
| `LLM_HTTP_TIMEOUT_SECONDS` | Default `60` — separate from `HTTP_TIMEOUT_SECONDS`, since real generation takes longer than a tool call |
| `MAX_JSON_REPAIR_ATTEMPTS` | Default `2` — retries for a malformed-but-parseable LLM response before falling back |
| `MAX_BULK_CANDIDATES` / `MAX_FINALISTS` / `MAX_FINAL_RECOMMENDATIONS` | Funnel sizes: `30` bulk recall → `8` finalists get full research → `8` presented |
| `AGENT_EXECUTION_TIMEOUT_SECONDS` | Backend deadline; default and maximum `270` seconds |
| `RECOMMENDATION_RESERVE_SECONDS` | Reserved after research for scoring/rendering; default `60` seconds |
| `TOOL_EXECUTION_TIMEOUT_SECONDS` | Budget for one tool/candidate invocation; default `50` seconds |
| `MAX_CONCURRENT_TOOL_REQUESTS` | Independent tool/candidate jobs allowed at once; default `10` |
| `UPSTREAM_REQUEST_TIMEOUT_SECONDS` | Optional declaration of the real proxy/platform timeout; must exceed `270` |
| `SQLITE_PATH`, `APP_PORT`, `HTTP_TIMEOUT_SECONDS`, `CACHE_TTL_HOURS` | Infra configuration |

**Never commit the real `.env` file** — it is already listed in `.gitignore`.

### LLMod.ai configuration

`LLModClient` (`app/llm/llmod.py`) assumes an OpenAI-compatible chat-completions shape, the course's
documented default; if that differs, only this one file needs to change — the app is
provider-agnostic via `BaseLLMClient`. **Re-register at LLMod.ai with the new course-required email
before** requesting the project API key. **Never commit the key** — it's read only from the
environment, and the logging redaction filter keeps it out of logs and error responses.

### Optional connection check

`scripts/check_llmod_connection.py` is a manual, opt-in script that sends one small real request to
verify your LLMod.ai configuration. **It consumes real course credit and is never run
automatically** by tests, installation, or app startup.

```powershell
python scripts/check_llmod_connection.py
```

### Golden-set evaluation harness

`scripts/golden_set/` is a fixed, representative prompt set (one per purpose plus edge cases —
ambiguous, unaffordable budget, excluded region, car-free) scored on structural properties (right
modules ran, no stale placeholders, finalist count in bounds) rather than exact text, since LLM
output is non-deterministic. The ambiguous case is called the same way an automated grader would (no
`X-Interactive-Mode`), asserting the disclosed-assumption behavior, not a bare question. Also runs as
a normal pytest case (`tests/integration/test_golden_set_harness.py`), so a regression fails CI too.

```powershell
# Default: MockLLMClient, zero cost, safe anytime.
python scripts/run_golden_set.py

# Real LLMod.ai provider -- sends one real request per case, consumes course credit. Opt-in only.
python scripts/run_golden_set.py --real
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
  MediaWiki API (revision-pinned context), GOV.UK and World Bank (safety), Wikipedia (Ookla Speedtest
  Global Index, internet connectivity), WhereNext (typed city-price and country cost context), and
  Frankfurter (budget currency conversion).
- **Local/curated (no network)**: a static language-coverage reference (`app/languages.py`) for the
  Language tool, plus deterministic offline fakes for tests.

All outbound requests are restricted to an explicit domain allow-list with SSRF protections
(`app/core/security.py`) — there is no generic URL-fetch tool anywhere in the codebase.

## Cache behavior

SQLite-backed (`tool_cache` table), cache-first, with per-source TTLs (`app/evidence/cache.py`):
geocoding and climate-normal data cached long (30 days / 1 year), amenities/place-context/mobility
medium (2 weeks), and safety, exchange rates, and weather forecasts short (1 day). If a live call
fails and only expired cache is available, the stale value is returned but explicitly marked
`stale=True` — never silently presented as current.

---

## Testing

```powershell
pytest -q
ruff check .
```

The entire offline test suite runs with `MockLLMClient` (zero LLM cost) plus deterministic fake
tool implementations (zero network calls), with an autouse fixture
that raises if any test ever attempts a real outbound HTTP request. Optional live tests (which
would spend real LLMod.ai credit) live in `tests/live/` and are **skipped** unless
`RUN_LIVE_TESTS=1` and `MOCK_LLM=false` — do not enable those flags casually.

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
docker build -t digitalnomadagent .
docker run --rm -p 8000:8000 --env-file .env digitalnomadagent
```

The image defaults to `MOCK_LLM=true` so a container never spends real money unless you explicitly
override it. `SQLITE_PATH` is configurable and can be mounted as a volume for persistence:

```powershell
docker run --rm -p 8000:8000 --env-file .env -v ${PWD}/data:/app/data digitalnomadagent
```

## Deployment

The course spec requires deploying on **Vercel** specifically (not a general host choice) — see
`docs/Project instructions.pdf`. `vercel.json` at the repo root configures this:

- **Entrypoint**: the existing `main.py` (`from app.main import app`) is used directly — Vercel's
  Python runtime auto-detects a file named `main.py` at the project root exporting an `app`
  variable, so no separate `api/` wrapper file is needed.
- **`routes`** sends every path (`/`, `/static/*`, `/api/*`) to that one function, so FastAPI's
  own router still handles everything internally exactly as it does under Uvicorn.
- **`functions.main.py.maxDuration = 300`** matches the spec's stated Vercel ceiling exactly — our
  own `AGENT_EXECUTION_TIMEOUT_SECONDS=270` already fits under this with margin.
- **`env`** sets the deployed defaults, including `MOCK_LLM=false` (the live grading environment
  uses the real LLMod.ai provider — the Dockerfile's own default is `true` instead, since that
  image is a local/manual-use path, not what's graded) and matching timeout values.
  `SQLITE_PATH=/tmp/digitalnomadagent.db` is set here because **Vercel's filesystem is read-only
  except `/tmp`, and `/tmp` resets on every cold start** — see "Storage" below for what that means.

**Budget ceiling**: the number that actually binds is the **account**-level cap, not the key —
`GET /user/info` reports `max_budget: 13.0` while `GET /key/info` reports `null`. Verify with
`python scripts/probe_llmod_account.py` (read-only, $0). `MAX_PROJECT_BUDGET_USD` is only a local,
per-deployment guard on top of that, and its ledger resets every cold start (see "Storage").

**To deploy:** connect this GitHub repo to a Vercel project (vercel.com → Add New Project → import
the repo) and set `LLMOD_API_KEY`/`LLMOD_MODEL` in the Vercel dashboard (secrets — never commit
them). Everything else is already in `vercel.json`. Keep the Vercel account active until graded
(per the spec).

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

## Storage: a stated deviation from the brief

The course brief names **Supabase** as the primary database and **Pinecone** for embeddings/vectors.
DigitalNomadAgent uses neither. It runs on SQLite (`app/evidence/database.py`), and it does not
embed anything — there is no retrieval-over-vectors step in the architecture to serve.

This is a deliberate choice, recorded here rather than glossed over:

- **Nothing in the four required endpoints depends on persistence.** `/api/team_info`,
  `/api/agent_info` and `/api/model_architecture` are file reads. `/api/execute` is stateless and
  single-shot: it interprets, researches, scores and answers within the one request. The database
  holds two things that are *optimisations*, not correctness requirements — the tool-evidence cache
  and the local LLM budget ledger.
- **Under Vercel, that database lives in `/tmp` and resets on every cold start.** So in production
  the cache is best-effort and the local ledger does not accumulate. The budget that actually binds
  is the provider-side account cap ($13, enforced by LLMod.ai and readable via
  `scripts/probe_llmod_account.py`), not the local ledger — see "Budget control".
- **Retrieval is live, not vectorised.** Evidence comes from named APIs (Overpass, Open-Meteo,
  Wikivoyage, GOV.UK, World Bank) at request time, with the source URL and retrieval date attached
  to every claim. A vector store would add an indexing layer between the answer and its source
  without making the answer more checkable.

Swapping SQLite for Supabase would make the cache and ledger genuinely persistent in production.
It is the largest remaining gap against the brief's letter, and it was not attempted this close to
the deadline in preference to leaving the four required endpoints untouched.

## Known limitations

- Typed city prices cover 57 cities (third-party dataset); other cities get low-confidence country-
  level context only, never a city estimate, and fixed-cost items (rent/utilities/internet/transit)
  are not a complete personal budget.
- `TimezoneFitTool` covers only a curated set of common origins — an unmapped one honestly reports
  "overlap unknown" rather than guessing.
- `MockLLMClient`'s keyword-based interpreter and its curated per-purpose candidate seed pool
  (~30 entries) are simplified offline stand-ins; real LLM output extracts richer nuance and isn't
  limited to a fixed candidate set.
- Safety evidence (FCDO advisory + World Bank/UNODC homicide rate + Wikivoyage) requires at least
  two sources and is comparative, never a universal city-safety rating.

## Legal and ethical limitations

- Never claims live flight/hotel prices, guaranteed visa/admission/safety, or exact current
  costs/travel times — visa and entry wording always directs the user to verify officially.
- External retrieved content is always treated as untrusted evidence, never instructions — every
  LLM system prompt says so explicitly.
- No user data is persisted beyond evidence caching and the local budget ledger; no personal
  information is collected.

---

## Team information

`config/team_info.json` carries the real team name, batch/order number (`2_4`), and each student's
name and email. It is served verbatim by `GET /api/team_info`;
`tests/unit/test_config_team_info.py` pins the `{batch#}_{order#}` format.
