# PlaceMatch — Architecture

This document explains how PlaceMatch is built and, more importantly, *why* it is an autonomous
agent rather than a fixed pipeline. See `assets/model_architecture.png` (also served at
`GET /api/model_architecture`) for the visual diagram.

## 1. Canonical modules

`app/core/module_names.py` is the single source of truth for the seven canonical names used
throughout the code, the diagram, `/api/agent_info`, LLM-call tracing, and this document:

| Canonical name | File | Calls the LLM? |
|---|---|---|
| Request Interpreter | `app/agent/request_interpreter.py` | Yes — 1 call |
| Agentic Research | `app/agent/agentic_research.py` | Yes — 1 call (bulk candidate recall only) |
| Tool Registry | `app/tools/registry.py` | No |
| Evidence Memory | `app/evidence/memory.py` | No |
| Dynamic Evaluation | `app/agent/dynamic_evaluation.py` | Yes — 1 batched call (scores cost/transportation/accessibility/activities for all finalists at once) |
| Recommendation Validator | `app/agent/recommendation_validator.py` | No |
| Recommendation Generator | `app/agent/recommendation_generator.py` | Yes — 1 call |

Four modules ever produce a `steps` entry in `/api/execute`'s response, because four modules ever
call an LLM — always exactly once each per request, so the total stays at 4 calls even when a
gap-research round runs (Dynamic Evaluation's call fires only once, from the state machine's single
post-gap-resolution branch — see §2). Everything else — tool selection, the candidate-discovery
funnel, validation — is deterministic Python, which is both cheaper (course budget is $13 total)
and easier to test exhaustively.

## 2. State machine and conditional flow

`app/agent/state.py` defines the states exactly as specified: `received`, `interpreting`,
`clarification_required`, `planning_research`, `executing_tools`, `evaluating`, `validating`,
`researching_gap`, `generating_response`, `completed`, `failed`. `app/agent/orchestrator.py` drives
transitions with structured conditions rather than a fixed sequence:

- **`interpreting → clarification_required`** only when the Request Interpreter marks
  `clarification_required=True` (e.g. purpose is entirely unclear, or a study request has no
  discernible academic field). This short-circuits the rest of the pipeline — no candidates are
  generated, no tools run — and the response is simply the clarification question, still wrapped
  in the required four-field envelope.
- **`planning_research → executing_tools`**: Agentic Research proposes up to `MAX_BULK_CANDIDATES`
  (default 30) broad candidates in one LLM call, then `executing_tools` runs a cheap, zero-LLM
  funnel (`app/agent/candidate_funnel.py`) — serial geocoding verification, a region-only
  hard-constraint pre-check, and concurrent `BudgetFitTool` ranking — to narrow these down to
  `MAX_FINALISTS` (default 8) before deterministically deciding which of the tools are relevant to
  *this* request (`select_tools()` in `agentic_research.py`) — see §4 below for why tool selection
  is deterministic.
- **`executing_tools`**: `ToolRegistry.verify_candidates()` runs GeocodingTool first for every
  bulk candidate; unverifiable candidates are dropped. The funnel then narrows the geocoded
  survivors to the finalist count. Only then are the other selected tools run against the
  finalists, concurrently, bounded by `MAX_CONCURRENT_TOOL_REQUESTS`.
- **`validating → researching_gap`**: the Recommendation Validator (deterministic) can send control
  back to Agentic Research exactly once, only when a high-weight criterion is missing evidence for
  one of the top `MAX_FINAL_RECOMMENDATIONS` candidates. The orchestrator tracks a
  `gap_iteration_used` flag so this can never loop more than once, satisfying the "at most one
  additional research iteration" requirement. Notably,
  this gap round makes **zero** additional LLM calls — it re-runs only the specific missing
  `(place, criterion)` tool calls — which keeps typical execution at exactly 4 LLM calls total
  (Interpreter, Agentic Research, Dynamic Evaluation, Recommendation Generator), at but never over
  `MAX_LLM_CALLS_PER_REQUEST=4`, regardless of whether a gap round ran.
- **`generating_response`**: the Recommendation Generator makes one LLM call with a compact,
  pre-scored payload (never raw tool output). If that call fails for any reason (budget refusal,
  provider error, malformed output after the repair attempt), a deterministic Python template
  (`app/core/rendering.py::render_recommendation_markdown`) builds the same Markdown structure
  directly from the already-computed scores — so `/api/execute` never crashes and never fabricates
  facts, it just discloses that a limited automated summary was used.
- Any unhandled exception, from any state, is caught by the orchestrator and converted to
  `status="error"` while preserving every `steps` entry already recorded — never a raw traceback,
  never a lost trace.

A hard cap (`MAX_STATE_TRANSITIONS = 30`) prevents runaway loops regardless of any bug in the
transition logic above.

The complete state-machine task is also wrapped by a hard wall-clock deadline. The backend default
and allowed maximum are 285 seconds (`AGENT_EXECUTION_TIMEOUT_SECONDS`), reserving 15 seconds for
API and client overhead so the end-to-end interaction remains under 300 seconds. The normal
research cutoff is 225 seconds, leaving `RECOMMENDATION_RESERVE_SECONDS=60` for deterministic
evaluation and response generation. At that cutoff, pending calls are cancelled but completed
results are retained and scored. If the recommendation LLM exceeds its remaining allowance, the
deterministic renderer returns the same evidence-backed structure. The hard cutoff is an emergency
no-I/O fallback that builds a provisional recommendation from the latest checkpoint rather than
discarding completed work.

The two early LLM-dependent stages also degrade without losing the run: Request Interpreter falls
back to the deterministic parser, and Agentic Research falls back to the purpose-keyed curated
candidate seed set. Both substitutions are added to the response assumptions. This means provider
timeouts in interpretation, candidate generation, individual tools, or recommendation writing all
have bounded, disclosed paths to a usable response.

## 3. Why tool selection is deterministic, not LLM-driven

`Agentic Research` still needs to decide *which* of the 10 tools matter for a given request. The
course's optimization requirements (§6 of the spec) explicitly ask for deterministic Python logic
"whenever an LLM is unnecessary" and a hard cap on LLM calls. Tool relevance is a classification
problem cleanly solvable from the interpreted profile (`purpose`, `relevant_criteria`,
`mobility_requirements`, etc.) — see `select_tools()` in `agentic_research.py` — so it does not need
an LLM call at all. This is also what makes the system genuinely *not* a fixed pipeline: a
remote-work prompt and a vacation prompt produce different tool sets by construction (verified by
`tests/unit/test_tool_selection_rules.py` and `tests/integration/test_agent_autonomy.py`), even
though the *code path* through the state machine is identical.

The one place an LLM *is* used to shape the search space is candidate generation — deciding *which
places* to consider — because that benefits from broader world knowledge than a rule table can
encode. This happens in one bulk call (up to `MAX_BULK_CANDIDATES`, default 30) rather than asking
the LLM to also pick finalists: narrowing the bulk list down to `MAX_FINALISTS` is a separate,
deterministic step (`app/agent/candidate_funnel.py`) so the expensive per-candidate tool suite only
ever runs against a small, cheaply-vetted set. Even the bulk-recall step has a fallback:
MockLLMClient's curated, purpose-keyed seed list (~30 entries per purpose) so the system remains
fully testable offline.

## 4. Evidence Memory and Dynamic Evaluation

Every tool call returns a `ToolResult` (source name/URL, retrieval timestamp, confidence, staleness,
optional error). `Orchestrator._persist_evidence()` converts successful results into
`EvidenceRecord`s stored in the SQLite `evidence` table (deduplicated on
`place, criterion, source_name`), giving full traceability from a claim back to its source.

`Dynamic Evaluation` runs in two passes. `evaluate_candidates()` is a pure function (no LLM): it
reads the evidence collected for each candidate, computes per-criterion `[0,1]` ratings for the
five criteria deterministic normalization can score directly (climate, work_infrastructure,
timezone, student_life, safety), and combines them as `score = Σ(weight_i · rating_i) −
uncertainty_penalty`, where:

- Weights start from the Request Interpreter's `inferred_weights` (derived from language cues —
  "most important" → 0.9, "prefer" → 0.6, "would be nice" → 0.3, "do not care about X" → dropped)
  and fall back to a small per-criterion default otherwise.
- A criterion with **no evidence is excluded** from both the weighted sum and the weight
  normalization — it is never scored as 0 or 1, and it is recorded in `missing_evidence` so the
  Validator and the final response can disclose the gap honestly.

The remaining four criteria — cost, transportation, accessibility, activities — need reasoning over
unstructured evidence (Wikivoyage excerpts, cost baskets, mobility counts) that deterministic
normalization can't do justice to. These stay in `unscored_evidence` through the first pass and the
`EVALUATING ⇄ RESEARCHING_GAP` loop, then get resolved by `score_unresolved_criteria()` — the one
Dynamic Evaluation LLM call, fired once from `VALIDATING`'s approved branch (always after any
gap-research round), batching every viable finalist × all four criteria into a single request.
`apply_llm_scores()` folds the results back in and recomputes totals via the same weighting math.

`_check_hard_constraints()` runs in both passes: a region-only check (works from geocoded country
identity alone, so it's available even before any criterion is scored) plus keyword-triggered
score-threshold checks — a criterion is only judged if it's both actually scored and textually
referenced as a hard constraint or deal-breaker, so missing or incomparable evidence never produces
a false elimination.

This is deterministic and extensively unit-tested (`tests/unit/test_dynamic_evaluation.py`) — the
same inputs always produce the same score.

## 5. Recommendation Validator

`validate_recommendations()` is also a pure function. It checks: at least `max_final_recommendations`
viable candidates when reasonably possible, every viable candidate has recorded drawbacks, ranking stability (top two
scores within a small margin are flagged "uncertain"), and — most importantly — whether any
high-weight criterion is missing evidence for one of the top `max_final_recommendations` candidates. If so, and no gap iteration has
run yet, `should_research_again=True` triggers the `researching_gap` state described in §2. This
is the feedback loop shown in the architecture diagram.

## 6. Budget enforcement

`app/llm/budget.py::BudgetManager` is consulted by `TracedLLMClient` **before** every LLM call:

1. Refuse if the request has already made `MAX_LLM_CALLS_PER_REQUEST` calls.
2. Compute a conservative worst-case cost (`max_output_tokens` × configured per-token pricing) and
   refuse if `running_total + worst_case > MAX_PROJECT_BUDGET_USD`.

Every call (successful or not) is recorded in the `llm_usage` SQLite table with provider-reported
cost when available, otherwise a clearly-flagged (`is_estimated=True`) local estimate. This ledger
is an additional local safeguard, not a substitute for the actual LLMod.ai account limit — it never
claims to know the real provider-side balance (see README "Budget control").

## 7. Failure handling and graceful degradation

- The 285-second agent deadline bounds the entire state machine, including all LLM calls, provider
  retries, rate-limit waits, tool calls, persistence, gap research, and response generation. It is
  not reset between states or retries, and configuration validation prevents raising it above 285.
- Independent non-geocoding `(tool, candidate)` jobs are created together and run concurrently up
  to `MAX_CONCURRENT_TOOL_REQUESTS=10`. Provider-specific controls override that general cap:
  Nominatim candidate verification remains serial to honor its one-request-per-second policy, and
  the shared Overpass client permits at most two simultaneous requests.
- Each tool/candidate invocation also has a 50-second default wall-clock budget, including its
  internal retries and provider failover. This frees its concurrency slot before the shared
  research cutoff when one provider or tool is abnormally slow.
- Tool scheduling is deterministic: tools supporting hard constraints run first, followed by
  higher `inferred_weights`, with each priority applied across all candidates before lower-priority
  work begins. The cutoff therefore preserves the most decision-relevant evidence first.
- When the 225-second research cutoff is reached, the registry returns completed results plus
  explicit timeout results for cancelled jobs. Dynamic Evaluation treats those as missing evidence,
  reduces confidence, and continues to the recommendation instead of failing the entire request.
- Timing logs record queue/run duration and outcome for every tool, duration for every agent state,
  total request duration, timeout counts, and hard-fallback use without changing the strict API
  response schema.
- Individual tool failures (timeout, HTTP 429/5xx, malformed JSON, empty response) are caught
  inside each tool and converted to a `ToolResult(error=...)` — never raised — so one flaky source
  never aborts the whole request. `ToolRegistry.run_tools()` adds a second layer of the same
  protection around every tool call.
- Cached data is preferred; if a live call fails and only stale cache is available, the stale
  result is returned with `stale=True` explicitly set, never silently presented as current.
- LLM failures (malformed JSON, schema-invalid output) get one repair attempt
  (`MAX_JSON_REPAIR_ATTEMPTS=1`) inside `TracedLLMClient`; if that also fails, the orchestrator
  either continues with a deterministic fallback (Recommendation Generator) or surfaces a clean
  `status="error"` response while preserving every already-completed `steps` entry.

## 8. Security boundaries

- **Outbound allow-list** (`app/core/security.py`): only `https`, only exact/allow-listed hostnames
  (Nominatim, Overpass, Open-Meteo, GOV.UK, World Bank, WhereNext, Frankfurter,
  Wikivoyage/Wikipedia, plus the curated official-source domains),
  raw IP literals rejected, DNS-resolved private/loopback/reserved addresses rejected, redirects
  disabled. There is no generic URL-fetch tool anywhere in the codebase.
- **Prompt-injection resistance**: every system prompt sent to the LLM (Request Interpreter,
  Agentic Research, Recommendation Generator) explicitly instructs the model to treat its input as
  untrusted data and ignore any embedded instructions.
- **Secret handling**: the LLMod API key is read only from the environment, registered with the
  logging redaction filter (`app/core/logging.py`) so it can never appear in logs, and never
  included in exceptions or API responses.
- **Strict API contracts**: every response model uses `ConfigDict(extra="forbid")`; custom
  exception handlers guarantee `/api/execute` always returns the exact four-field envelope, never
  FastAPI's default `{"detail": [...]}`.

## 9. SQLite usage

One database (`SQLITE_PATH`, default `./data/placematch.db`) with three tables, created idempotently
at startup (`app/evidence/database.py`): `evidence` (Evidence Memory), `tool_cache` (generic
per-tool cache with per-source TTLs), and `llm_usage` (the budget ledger). WAL mode is enabled to
avoid "database is locked" errors under concurrent requests.

## 10. UI → API flow

The UI (`app/templates/index.html` + `app/static/app.js`) is a static page served by the same
FastAPI app. It only ever calls `POST /api/execute` — it holds no agent logic of its own. Markdown
from the response is rendered client-side through a small, whitelist-only transform
(`safeMarkdownToHtml` in `app.js`) that HTML-escapes all text *before* applying any structural
formatting, so raw LLM output is never inserted as live HTML.

## 11. Why this is autonomous, not a fixed pipeline

Two requests with the same *shape* of prompt but different purposes traverse the *same* state
machine code but produce materially different behavior: different candidate seeds, different tool
sets, different scoring weights, and a possible extra research round for one but not the other.
Nothing in the orchestrator hard-codes "always call these N tools" or "always ask these questions" —
every branch point reads from the interpreted profile. That is what makes this a conditional agent
rather than a linear script with optional steps.
