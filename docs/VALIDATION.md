# Validation Report

How DigitalNomadAgent has been validated, what the end-to-end runs found, and what still needs
fixing. Two things are kept deliberately separate: **defects** (it does not work as intended) and
**enhancements** (it works, but could be better). Correctness comes first.

Last updated: **2026-08-04**.

---

## 1. Method

Automated tests are a regression net, not evidence that the product is good. So the core of this
report is **manual analysis**: the ten evaluation prompts in `scripts/e2e/prompts.py` were run
against a live server, and each response was read start to finish the way a user would read it,
then cross-checked against its `steps` trace to understand *why* it came out that way. There is no
score and no pass/fail computed from a checklist — the written judgement is the finding.

Four configurations, two of which are still pending an API key:

| # | Configuration | LLM | Tools | Status |
|---|---|---|---|---|
| 1 | API mode | mock | **real** | Done — 10 prompts + 7 contract checks |
| 2 | UI mode | mock | **real** | Done — interactive divergence + browser walkthrough |
| 3 | API mode | **real** | **real** | **Blocked on `LLMOD_API_KEY`** |
| 4 | UI mode | **real** | **real** | **Blocked on `LLMOD_API_KEY`** |

Reproduce with:

```bash
python scripts/run_e2e_suite.py --label mock-api
```

Raw artifacts (full JSON, `steps` traces, timings, ledger deltas) land in `validation_runs/`,
which is gitignored. The harness **captures and does not judge**.

---

## 2. Automated test state

`pytest -q` → **435 passed, 1 skipped** (the opt-in live SLA check). `ruff check .` → clean.
Baseline before this work was 367 passed; 68 tests were added.

| Category | Count | Where |
|---|---:|---|
| Unit | ~380 | `tests/unit/` |
| Integration / API contract | 44 | `tests/integration/` |
| Component / browser UI | **0** | — no JS test tooling exists |
| Live (real provider) | 1 | `tests/live/`, skipped by default |
| Golden-set structural harness | 8 cases | `scripts/golden_set/` |

**Gaps:** no CI enforcing the suite, no coverage measurement, no frontend tests despite
`app/static/app.js` being ~1,400 lines, and no stored results to diff between runs.

### Was validation recorded anywhere before this?

Only as prose claims, and one was wrong. `README.md` states live tests are *"not included in this
repository by default"* — `tests/live/test_runtime_sla_live.py` **is** committed; it is skipped,
not absent (**D2**). The ticked checklist item asserting `pytest` and `ruff` both pass was
unverified because nothing enforced it; it is now confirmed true. The only recorded real-LLM
datapoint in the whole repo was a comment in `.env.example` noting a run that used 2,773 of 4,000
output tokens.

---

## 3. Configuration under test

| Setting | Value |
|---|---|
| `MOCK_LLM` | `true` (configurations 1–2) |
| Tools | **real** — Nominatim, Overpass, Open-Meteo, Wikivoyage, GOV.UK, World Bank, WhereNext, Frankfurter |
| `LLM_TEMPERATURE` | `0` (newly pinned — see D-fixes) |
| `LLMOD_MODEL` | **not yet known** — absent from the repo, lives only in an untracked `.env` |

**Runtime:** all 10 prompts completed, none near the 285 s deadline. Min 51.9 s (P09), max 136.9 s
(P07), median ~95 s. Second runs of the same prompt were markedly faster (P01: 128.7 s cold →
95.6 s warm), so the cache works.

**Cost:** $0.00. Configurations 1–2 make no paid calls. Ledger baseline is 62 mock rows at $0.00.

---

## 4. The evaluation prompt set

Ten prompts, six mainstream and four edge, written at realistic paragraph length rather than as
tidy one-line specs — prompt shape is itself part of what is under test. Full text and the
rationale for each is in `scripts/e2e/prompts.py`; IDs are stable so results stay comparable
across runs and configurations.

| ID | What it is | What it targets |
|---|---|---|
| P01 | Remote work, €1,800 cap, car-free, "don't care about nightlife" | Hard constraints; the `do not care about X` path |
| P02 | Family of 4, Tel Aviv origin, August, ≤5 h flight | Origin resolution, flight-time cap, family activities |
| P03 | CS exchange semester, explicitly **ranked** priorities | Does weighting respect stated order? |
| P04 | Solo traveller, safety-dominant, negative preference | Safety+mobility co-activation, one dominant criterion |
| P05 | Business base, ≥4 h overlap with US Eastern | Timezone as the **primary** criterion |
| P06 | Retired couple, 6 months, **wheelchair**, English | Qualitative hard constraint, accessibility |
| P07 | Burnt out, "I don't know what I'm looking for" | Ambiguity; API↔UI divergence |
| P08 | Scandinavia + snow + outdoor swimming + $400/mo | Self-contradictory, over-constrained |
| P09 | "I've settled on Lisbon — is it a good fit?" | Evaluate a named place, not discover one |
| P10 | Injection + persona swap + live prices/visa demands | Robustness, staying in scope |

Plus seven protocol-level contract checks (C01–C07).

---

## 5. What works well

Worth stating plainly, because the defect list below is long.

- **The API contract layer is flawless.** All seven checks return the exact four-field envelope
  with no leaked FastAPI `detail`, clear human-readable errors, and correct boundary behaviour
  (4,000 chars → 200; 4,001 → 400). Nothing to fix here.
- **Evidence provenance is genuinely good.** Every source is cited with a URL, retrieval date,
  data date, and a confidence level — including Wikivoyage revision IDs. This is better than most
  projects of this kind.
- **Graceful degradation is real.** Every one of the 10 prompts returned `status: "ok"`; no
  traceback ever leaked; tool timeouts degraded to partial evidence rather than failing the run.
- **The clarification round-trip works end to end.** In the UI, answering the question appends the
  detail to the original prompt, re-runs, and produces sensible output.
- **Candidate selection is good once the profile is populated.** P02 returned Malaga, Nice,
  Cagliari, Split, Valletta, Dubrovnik, Corfu, Tenerife for an August family beach trip — a list a
  knowledgeable person would endorse.
- **Runtime is comfortable.** Median ~95 s against a 285 s budget, and caching visibly helps.

---

## 6. Defects

Severity reflects user impact. **Mock-only** matters more than it sounds: `MOCK_LLM=true` is the
default in `.env.example`, `Dockerfile`, and **`vercel.json`** — so mock mode is what the publicly
deployed, graded app actually runs.

### Fixed during this work

| ID | Severity | Defect |
|---|---|---|
| **D0** | critical | **The $13 budget cap could never fire.** `LLModClient` read cost only from the JSON body, ignoring the `x-litellm-response-cost` header that LiteLLM-family proxies use, and `LLM_*_COST_PER_1M` default to `0`. Every ledger row wrote `$0.00`, so `check_before_call` evaluated `0 + 0 > 13` — permanently false. Also fixed a truthiness bug treating a genuine `$0.00` as "unknown". Now reads the header first; both the reactive and pre-emptive properties are pinned by tests. |
| **D3** | high | **Both paid scripts used the 10 s research timeout for LLM calls.** `run_golden_set.py --real` and `check_llmod_connection.py` passed `http_timeout_seconds` instead of `llm_http_timeout_seconds` (60 s). Since the generator emits up to 4,000 tokens, `--real` would time out, retry once, and fail — burning tokens on two doomed attempts per case. Suggests `--real` never successfully ran. |
| **D5** | high | **The test suite wrote to the production database.** `conftest.py`'s isolation fixture was not autouse and the golden-set harness bypassed it, so every `pytest` run appended 31 rows to `data/digitalnomadagent.db` — mock ledger rows, fake evidence, and **phantom entries in the user's UI search history**. Now an autouse fixture, with `tests/unit/test_database_isolation.py` pinning it. |
| **D4** | medium | **`app/llm/llmod.py` had no tests at all** — the one module that spends money. Now 19 tests. |
| **D1** | medium | **`do not care about X` has zero coverage.** Implemented, instructed to the real LLM, and documented in `ARCHITECTURE.md:132`, but its only test was deleted in the funnel redesign and never replaced. |
| **D2** | low | README falsely claims live tests are not in the repo. |

Also pinned `LLM_TEMPERATURE=0`, so a repeated prompt returns the same answer and you never
re-spend budget to find out whether a difference was real or sampling noise.

### Open — recommended before submission

#### D6 — One missed keyword silently discards every stated constraint · **critical** · mock-only

P01 states purpose, duration, region, budget and two hard constraints. The interpreter returned
`purpose: "unknown"` with **every field empty** — no budget, no regions, no constraints, no
criteria, no weights.

`_detect_purposes` (`app/llm/mock.py:130`) is plain substring matching. P01 says *"cleared to
**work fully remote**"*, which matches none of `["remote work", "work remotely", "remote job",
"digital nomad", …]`. On no match, `interpret_prompt` returns early (`mock.py:257`) with a stub
that **never attempts** budget, region, duration or constraint extraction.

| Prompt fragment | purpose | budget |
|---|---|---|
| "work **fully remote** … 1800 EUR a month" | `unknown` | `None` |
| "work **remotely** … 1800 EUR a month" | `remote_work` | `1800.0` |
| "work **from home abroad** … 1800 EUR a month" | `unknown` | `None` |

The failure is silent: `_resolve_ambiguous_profile` proceeds with a "broad default", so the user
gets eight confident results at **"High" confidence** that ignore everything they said.

*Fix, cheapest first:* (1) let the no-purpose branch fall through to full extraction so a detected
budget/region survives an unclear purpose; (2) broaden the keyword list and use word-boundary
regex; (3) stop reporting "High" confidence on an empty profile.

#### D9 — Five of ten prompts return effectively identical output · **critical** · mock-only

Consequence of D6. SHA-256 of the response text:

| Digest | Prompts |
|---|---|
| `e05c0bea09f3` | **P01, P06, P07, P10** — byte-identical, 9,454 chars each |
| `97b33e1b5a8f` | P08 — same eight cities, minor reordering |

A remote-work request, a **wheelchair user's six-month winter escape**, a burnout escape, and a
**prompt-injection attempt** all return Berlin, Prague, Buenos Aires, Lisbon, Riga, Nice, Mexico
City, Budapest — at "High" confidence.

- **P06** — a wheelchair user wanting mild winters and English gets Berlin, Prague and Riga *for
  November–April*, plus Buenos Aires and Mexico City. Accessibility is never mentioned.
- **P08** — asks for Scandinavia, receives **no Scandinavian city**; the contradiction it was
  designed to expose is never detected or disclosed.
- **P10** — the injection is not obeyed (good), but neither is it refused or acknowledged; someone
  asking for Bali flight prices simply receives eight city recommendations.
- **P01** — "somewhere in Europe" returns **Buenos Aires and Mexico City**.

#### D8 — Every Overpass-backed tool times out · **critical** · affects real mode too

Measured from server logs across the run:

| Tool | ok | error | timeout | success |
|---|---:|---:|---:|---|
| `LocalMobilityTool` | 0 | 0 | 8 | **0%** |
| `AmenitiesTool` | 3 | 3 | 18 | **12.5%** |
| `SafetyTool` / `BudgetFitTool` / `WeatherTool` | all | 0 | 0 | 100% |

Not just contention: `run_seconds=50.016` with `queue_seconds=0.000` shows a single Overpass query
exceeding `TOOL_EXECUTION_TIMEOUT_SECONDS=50` unaided. Contention compounds it — `OverpassClient`
holds a global `Semaphore(2)` (`overpass_client.py:30`), so later candidates wait the full 50 s and
then time out anyway.

These tools feed the **primary** criterion for four of the ten prompts (P01 car-free, P03 public
transport, P04 walkability, P06 accessible transport). The evidence table holds just **2**
`AmenitiesTool` rows from 24 invocations.

Worst part is the presentation: P01 renders *"Coworking and cafe evidence suggests limited work
infrastructure nearby"* for Budapest. That reads as a finding **about Budapest** but is an artifact
of a timed-out query. **Missing data is being reported as a negative property of the place.**

*Directions:* a longer per-invocation cap for Overpass specifically; a lighter query (smaller bbox,
fewer tag groups); higher concurrency against a mirror that allows it; cross-candidate caching; or
fall back to the Wikivoyage "Get around" prose already being fetched. Separately, and regardless of
the timeouts, **distinguish "no data" from "little of the thing"** in the drawback wording.

#### D11 — P05: decisive criterion unevidenced, ranking effectively inverted · **high**

P05 asks for "at least four hours of overlap with US Eastern". Every finalist reports *"Evidence
limitations: no verified data for work_infrastructure, timezone"* — both decisive criteria
unevidenced. Scoring fell back to cost, which the user gave as a **ceiling**, not a goal:

| Rank | City | UTC | Overlap with a 9–5 US Eastern day |
|---:|---|---|---|
| 1 | Bucharest | +3 | ~1 h |
| 4 | Antalya | +3 | ~1 h |
| 6 | **Lisbon** | +1 | **good** |
| 8 | Taipei | +8 | ~0 h |

The best answer sits at rank 6. Every row simultaneously reads **"No major drawback identified"**
while declaring no verified data — presenting an unmet hard requirement as no drawback at all.

#### D10 — Criterion weights are uniformly 0.5 · **high** · mock-only

Every prompt that produced weights produced **exactly 0.5 for every criterion**.
`ARCHITECTURE.md:132` documents "most important" → 0.9, "prefer" → 0.6, "would be nice" → 0.3, but
that needs the literal phrases. P04's *"Safety is genuinely my top priority"* and P03's explicitly
ranked *"what matters, roughly in order: …"* both fall through to the default. **Ranking cannot
reflect user priorities** — which is the core value proposition.

#### D7 — Positive region constraints are structurally unsupported · **high** · mock-only

`interpret_prompt` hard-codes `"preferred_regions": []` (`mock.py:350`). "Somewhere in Europe", "in
Southeast Asia" are **always ignored**. `excluded_regions` *is* extracted, which is why the golden
set's "avoid France" case passes — there is no golden case for a positive region. Independent of
D6: fixing the keywords alone still leaves this broken.

#### D13 — Landing page claims capabilities the system explicitly disclaims · **high**

> **Trusted & Up-to-Date** — "Real-time visa, cost, and safety information you can rely on."
> Hero: "Get personalized recommendations, costs, **visas** and more."

**There is no visa tool** among the 11 registered tools. Nothing is real-time — cost data is a
static dataset stamped `2026-01-15`, climate is 2021–2025 normals. And every generated response
says the opposite: *"No live prices … or guaranteed admission/visa eligibility are claimed here."*
The marketing copy contradicts the product's own disclaimer and promises a capability that does not
exist. Cheap to fix; it undercuts the project's central claim of evidence-based honesty.

#### D12 — Assorted extraction defects · **medium**

- **Budget period is unreliable.** P09's "€1,200 **a month** all-in" → `period: "daily"` (a 30×
  error). In the UI clarification reply, "€1,200 a month" → *"Assumed … is a **total** amount"*.
  Two different wrong answers for the same phrasing.
- **P09 ignores the named destination.** The user names Lisbon and asks "is it a good fit?";
  Lisbon ranks **6th of 8**. The question asked is never answered.
- **P03 garbage hard constraint:** `['must — my language skills are nonexistent']`, a regex
  grabbing text around "must".
- **P03 language requirement dropped:** "English-taught courses are a must" yields Seoul,
  Singapore and Warsaw; `preferred_languages` is never populated.
- **P02 origin dropped:** "flying out of **Tel Aviv**" → `origin: None`, so the 5-hour flight cap
  is never applied — Tenerife (~7 h from Tel Aviv) is recommended.
- **P02 month dropped:** "in August" → `target_months: []`, though the weather tool did use month
  8, so the profile misrepresents what actually ran.

#### D14 — UI progress labels are simulated and name a removed module · **low**

`app/static/app.js:63` states the stages are simulated. Observed live: "Generating candidate
destinations…" → "Writing your recommendation…" → **"Gathering official sources…"** → "Checking
weather…" → back to "Gathering official sources…". The order is not the real pipeline order, it
loops, and **`OfficialSourceTool` was removed** from the system. The real `steps` data needed to
drive this honestly is already returned by `/api/execute`.

---

## 7. API vs UI divergence

The single most interesting behavioural difference, and it is by design
(`Orchestrator._resolve_ambiguous_profile` vs the `X-Interactive-Mode: true` header the UI sends).

| | Bare API | UI (interactive) |
|---|---|---|
| P01, P06, P07, P08, P10 | Full pipeline, 95–137 s, generic eight-city answer at "High" confidence | Returns in **0.02 s** with a clarification question |
| LLM calls | 4 | 1 |

**The UI behaves better** — asking beats guessing. But because of D6 it asks the wrong prompts: all
five receive the *identical* canned question —

> "Could you clarify the main purpose of this trip (remote work, study, vacation, or something
> else), your approximate budget, and how long you plan to stay?"

— and P01 already stated all three (work fully remote, €1,800/month, three months). A user who
wrote a detailed paragraph is asked to supply what they just supplied.

An automated grader calling the bare API and a human clicking the UI will therefore see
**completely different behaviour** for the same prompt. Worth deciding deliberately which is
intended.

---

## 8. Enhancements

Separate from defects: these are things that work but could be materially better. Ranked by
user-visible impact per unit of effort. Cost impact noted because the $13 cap is a real constraint.

| # | Enhancement | Effort | Extra LLM cost |
|---|---|---|---|
| **E1** | **Justifications are boilerplate.** "Cost evidence compared against the stated budget informs this score" appears as the *why it fits* for 7 of 8 candidates in P01 and as a **drawback** in the UI run — the same sentence used in whichever slot needs filling. One UI result ranked **#1** with *"Why it fits: No specific strengths recorded."* A reader cannot tell why rank 1 beat rank 4. Largely a mock-renderer limitation; configuration 3 will show whether the real LLM fixes it. | med | none |
| **E2** | **No discrimination in confidence.** Every prompt returns all-High, all-Medium or all-Low. Confidence that never varies within a result set carries no information. | low | none |
| **E3** | **Collected evidence is discarded.** All 32 `activities` rows are rich Wikivoyage See/Do prose, yet output reports *"Activity counts (0)"* because scoring counts only OSM POIs — which time out (D8). Good evidence is fetched and thrown away. | med | none |
| **E4** | **Sources are a flat list.** 33 undifferentiated citations, not attached to the claims they support. Attaching each source to its claim would make the evidence chain checkable. | med | none |
| **E5** | **Trade-offs section is vacuous.** "Berlin is the strongest overall match, but Prague may be preferable if its advantages matter more to you" — true of any ranked list, says nothing. | low | none |
| **E6** | **Answer the question that was asked.** P09 asks "is Lisbon a good fit?" and gets a ranked list of eight other cities. Detecting a named-destination request and leading with a verdict on it would be a visible improvement. | med | none |
| **E7** | **Drive UI progress from real `steps`** instead of a simulated timer (see D14). | low | none |
| **E8** | **Add a golden case for a positive region constraint** — the gap that let D7 survive. | low | none |

---

## 9. Cost log

| Date | Activity | Calls | Input | Output | Cost | Ledger total |
|---|---|---:|---:|---:|---:|---:|
| 2026-08-04 | Phase 1 — 10 prompts, mock | 40 | 62,428 | 42,544 | **$0.00** | $0.00 |
| 2026-08-04 | Contract checks, mock | 4 | 7,530 | 4,223 | **$0.00** | $0.00 |
| 2026-08-04 | Interactive variant, mock | 5 | — | — | **$0.00** | $0.00 |

**Total spent to date: $0.00 of $13.00.**

The mock ledger records *real* prompt sizes (the prompts sent are genuine; only the completion is
synthetic), so it is a sound basis for estimating configurations 3–4. Real completions run longer
than mock ones — the one recorded real datapoint used 2,773 output tokens where mock uses ~1,255,
roughly 2.2×.

**Estimate for configurations 3 + 4** (10 API prompts + 2 UI prompts = 48 calls): ~75 k input,
~90 k output. Dollar cost cannot be quoted until the model and its pricing are known —
`scripts/probe_llmod_account.py` (read-only, $0) will establish both, and no paid run will start
without that figure being agreed first.

---

## 10. Reproducing this

```bash
# 1. Offline suite + lint
pytest -q && ruff check .

# 2. Start a server (mock, zero cost, real tools)
uvicorn app.main:app --port 8000

# 3. Capture the ten prompts
python scripts/run_e2e_suite.py --label mock-api
python scripts/run_e2e_suite.py --label mock-interactive --interactive
python scripts/run_e2e_suite.py --label contract --contract-checks

# 4. Read the ledger at any time (read-only, $0)
python scripts/show_llm_usage.py --calls

# 5. Once a key exists — read-only account probe, still $0
python scripts/probe_llmod_account.py
```

---

## 11. Open items

1. **Blocked on `LLMOD_API_KEY`:** the provider probe, real per-token pricing, and configurations
   3–4. Everything needed is written and tested; the paid runs are one command plus an agreed cost.
2. **Record `LLMOD_MODEL` here** once known — nothing in the repo currently states which model the
   project uses, which makes the results unreproducible by anyone else.
3. **Decide on D6/D7/D10** before submission. They are the difference between a grader seeing a
   constraint-respecting agent and seeing the same eight cities for five different prompts.
4. **Existing DB pollution** from the pre-D5-fix runs (62 mock ledger rows, 214 fake evidence rows,
   7 phantom history entries) is still in `data/digitalnomadagent.db`. Nothing needs deleting for
   correctness — real costs can be reported as a delta from the $0.00 baseline — but the phantom
   history entries are user-visible and can be cleared via `DELETE /api/history`.
