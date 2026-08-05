# Validation Report

How DigitalNomadAgent has been validated, what the end-to-end runs found, and what still needs
fixing. Two things are kept deliberately separate: **defects** (it does not work as intended) and
**enhancements** (it works, but could be better). Correctness comes first.

Last updated: **2026-08-05**.

> **Reading order.** Section 0 is the current status, including the results of both real-provider
> runs — the post-fix verification run (2026-08-04) and the subset re-validation (2026-08-05) —
> and the fixes that followed each. Sections 2–9 are the original findings, written before the
> fixes landed — they still describe several defects as open. Section 0 is authoritative on what
> is fixed; the detail below explains *why* each one mattered and is worth keeping for that
> reason.

---

## 0. Current status and next steps

### Where this stands, in one paragraph

Every defect on the ledger is closed except **D19**, which cannot be fixed from this repository —
the API key's budget cap is administered by the course provider. The offline gate is green (**493
passed, 1 skipped**, `ruff` clean) and $12.25 of the $13.00 budget remains.

The outstanding risk named here previously — that the post-`6da2464` fix commits had never met the
real LLM — **is closed.** The 2026-08-05 subset run
(`validation_runs/20260805T051351Z-real-api-postfix-2`, $0.1130) confirmed **D8b, D20 and D21 all
hold in real mode**, and turned up two new defects, **D23 and D24**, both since fixed. Those two
fixes have themselves been verified offline and in mock mode but *not* against the real provider;
that is now the outstanding gap, and it is a much smaller one. See "Suggested next session".

### Defect status

Every defect found is listed here with its state. Commits are on `main`.

| ID | Defect | State | Commit |
|---|---|---|---|
| D0 | Per-request cost never read, so the $13 cap could never fire | **Fixed** | `0fcd28f` |
| D1 | `do not care about X` had zero test coverage | **Fixed** | `b100775` |
| D2 | README claimed live tests are not in the repo | **Fixed** | `14d582e` |
| D3 | Paid scripts used the 10s research timeout for LLM calls | **Fixed** | `0fcd28f` |
| D4 | `app/llm/llmod.py` had no tests | **Fixed** | `0fcd28f` |
| D5 | Test suite wrote to the production database | **Fixed** | `0fcd28f` |
| D6 | One missed keyword discarded every stated constraint | **Fixed** | `b100775` |
| D7 | `preferred_regions` hard-coded empty | **Fixed** | `b100775` |
| D8 | Every Overpass-backed tool timed out | **Fixed** in two rounds — `054fc41` (server-side counting), then `9405937` (per-endpoint concurrency + in-tool OSM budget); see D8b below | `054fc41`, `9405937` |
| D9 | Five of ten prompts returned identical output | **Fixed** (via D6) | `b100775` |
| D10 | All criterion weights were exactly 0.5 | **Fixed** | `b100775` |
| D11 | "No major drawback identified" printed when evidence was absent | **Fixed** | `0c88ab9` |
| D12 | Budget period misparsed ("a month" → daily) | **Fixed** | `0dcbfad` |
| D13 | Landing page claims visa/real-time data the system has not got | **Fixed** (2026-08-05) | `bdd9f60` |
| D14 | UI progress labels looped and named a removed module | **Fixed** | `14d582e` |
| D15 | Pinning `temperature` broke every gpt-5 call | **Fixed** | `5e8623b` |
| D16 | Continental region preference eliminated every candidate | **Fixed** | `5e8623b` |
| D17 | A failed LLM module vanished from `steps` | **Fixed** | `df24a78` |
| D18 | A named destination ("is Lisbon a good fit?") was dropped | **Fixed** | `2c5c9bf` |
| D19 | No provider-side budget cap; README claimed otherwise | **Documented** | `14d582e` |
| D20 | Time-zone criterion never scored when the reference timezone is named ("overlap with US Eastern") rather than implied by an origin | **Fixed** | `8d1f6b5`, `03ce480` |
| D21 | Free-form interpreter weight keys (`time_zone_overlap`) never matched the scoring vocabulary, so every user-stated weight fell back to the 0.5 default | **Fixed** | `17927a0` |
| D8b | Residual of D8: global `Semaphore(2)` starved Amenities/LocalMobility jobs, and a killed LocalMobilityTool lost its already-fetched Wikivoyage prose | **Fixed** | `9405937` |
| D22 | Mock scorer rendered a failed count lookup as a scored `count (0)` — absence of evidence as evidence of absence | **Fixed** | `d6b2939` |
| D23 | `missing_evidence` compared free-form interpreter criterion names against the canonical scoring vocabulary, so every criterion was reported missing even when scored — and the same mismatch made the gap-research iteration select no tools | **Fixed** (2026-08-05) | `7a99f1a` |
| D24 | A stated working-hours overlap minimum ("at least four hours with US Eastern") was never checked, so a candidate missing it ranked #1 | **Fixed** (2026-08-05) | `5606431` |

One deliberate non-change, flagged rather than decided unilaterally:

- **Budget refusals still leave `steps` empty** (`df24a78`). A refused call never reaches the
  provider, so there is no interaction to document, and
  `test_budget_refusal_prevents_call_and_leaves_trace_empty` encodes that as intended. But the
  orchestrator *does* fall back on `BudgetExceededError`, so the same hidden-degradation argument
  as D17 applies. Worth a decision.

**D19 is not fixable from this repository.** The key's `max_budget` is administered by the course
provider; `MAX_PROJECT_BUDGET_USD` plus the D0 fix is the entire spend guard, and both README and
this report now say so.

### The verification run — DONE (2026-08-04, `validation_runs/20260804T175107Z-real-api-postfix`)

The fixes were re-validated end to end against the real provider on 2026-08-04. Offline gate
first: `pytest -q` → **465 passed, 1 skipped**; `ruff check .` clean. All ten prompts returned
`status: ok`; run cost **$0.3252** (ledger) / $0.3018 (provider-billed). Per-prompt outcomes
against the planned checklist:

| Prompt | What was verified | Outcome |
|---|---|---|
| **P01** | Budget, car-free and region constraints in the profile; European finalists; no elimination error (D6, D7, D16) | **PASS** — full profile (€1,800/monthly, both hard constraints, `["Europe"]`); finalists Sofia, Timișoara, Plovdiv, Gdańsk, Seville; no elimination error |
| **P02** | `origin: "Tel Aviv"` extracted, 5-hour flight cap applied | **PASS** — origin extracted, `flight_time_under_5_hours` a hard constraint; finalists Barcelona, Sicily, Mallorca, Marseille, Nice — all ≲5 h from Tel Aviv |
| **P03** | Ranked priorities → descending weights (D10) | **PASS** — 0.35 / 0.25 / 0.20 / 0.15 / 0.05, matching the stated order |
| **P05** | Timezone evidenced; ranking not inverted (D8, D11) | **PARTIAL** — D11 holds: every finalist *names* the unverified time-zone evidence as its key uncertainty; no "No major drawback identified" anywhere. But the evaluation still scored only `cost` and `accessibility` — the time-zone criterion (weighted 1.0) remains unevidenced, and ranking (Madrid, Barcelona, Málaga, Lisbon) still leans on cost. Better than Bucharest/Taipei, still not evidence-driven |
| **P06** | Accessibility criteria evidenced (D8) | **PASS** — differentiated accessibility scores (0.4–0.7) with concrete transit/airport evidence per city. Note: the first Recommendation Generator call failed (`malformed_json`), the retry succeeded, **and both steps are recorded** — 261.8 s of the 285 s deadline |
| **P08** | Relaxation explained, not a bare elimination error | **PARTIAL** — no error; it recommends Tallinn and *discloses* that Scandinavia was not hard-filtered and the candidate set was thin, but never states plainly that $400/month contradicts Scandinavia |
| **P09** | Lisbon in the finalists with a direct verdict (D18) | **PASS** — interpreter emits `named_destinations: ["Lisbon"]`; **Lisbon ranks #1** of five Portuguese candidates with a direct verdict |
| **P10** | All four modules in `steps` even on interpreter failure (D17) | **PASS** — the real interpreter call failed again (provider 400) and the step **stays visible**: `{"error": "provider_call_failed", "note": "…a deterministic fallback was used."}`. Injection still not obeyed |
| **All** | Honest evidence wording (D11) | **PASS** — missing data is reported as "missing verification", never as a negative property of the place; confidence varies within result sets (e.g. "Medium-low") |

**The one substantive negative finding is that D8 is only partially fixed.** The server-side
counting fix (`054fc41`) brought `TransportAccessTool` (~41 s/query) and `ActivitiesTool` into
reliable success, which is what evidenced P06's accessibility criteria. But `AmenitiesTool` and
`LocalMobilityTool` still exceed the 50 s cap and timed out on essentially every invocation in
this run — coworking/amenity and car-free-mobility criteria remain unevidenced in real runs.
Thanks to D11 the output now *says so honestly*, so the failure mode is disclosure rather than
misinformation, but the evidence gap itself stands. Second observation, related: the time-zone
criterion is never scored even though `timezonefinder` data appears in the source list — an
evidence-mapping gap rather than an Overpass one.

*(Both findings were fixed the same evening — see "Verification-run findings, fixed on
2026-08-04" below: D8b, D20, D21.)*

### Environment notes that will bite otherwise

- **`LLMOD_MODEL` must be `MB5R2CF-azure/gpt-5.4-mini`.** The short form `azure/gpt-5.4-mini` does
  not exist and every call 400s.
- **Do not set `LLM_TEMPERATURE`.** gpt-5 deployments reject every value except 1.0 with a 400
  `UnsupportedParamsError`. It is deliberately commented out in `.env.example`. Determinism is not
  achievable with this model, so identical prompts will not reproduce.
- **`.env` currently has `MOCK_LLM=true`** as a safety default; real runs override it at launch.
- **Provider pricing is $0.75 / $4.50 per 1M** (input/output), from `/model/info` and confirmed
  against a billed call — *not* the $0.1438 / $5.7205 in the course handout. `.env` deliberately
  carries the higher of each figure so the pre-call guard cannot under-estimate.
- **Spend so far: $0.7524 of $13.00** (provider-authoritative, after the 2026-08-05 subset run).
  About 37 more full suite runs fit in the remaining $12.25.
- **The ledger has a pre-existing baseline** of 62 mock rows at $0.00, plus 214 fake evidence rows
  and some phantom search-history entries, all written by the test suite before D5 was fixed.
  Harmless for cost reporting; the history entries are user-visible and can be cleared with
  `DELETE /api/history`.

### Verification-run findings, fixed on 2026-08-04 (evening)

Both residual findings from the verification run were investigated and fixed the same day; the
P05 investigation split into two distinct defects (D20, D21).

- **D20 — named reference timezone** (`8d1f6b5`, mock half `03ce480`): P05 names "US Eastern" as
  the coordination target and states no origin, so `TimezoneFitTool` could never compute an
  overlap. The tool now resolves a timezone named in the request text (word-bounded phrase table,
  no network) and measures against it; an explicit reference wins over the origin. The mock
  interpreter also now captures "N hours of overlap with X" as the hard constraint it is.
  *Verified live (mock LLM + real tools, $0):* P05 now ranks Lisbon #1 with "Good working-hours
  overlap (~3.0h)" and demotes Taipei to 7th at ~0.0h — the original run had ranked Bucharest #1.
  The chain was also verified offline against the captured real-LLM P05 profile.
- **D21 — weight canonicalization** (`17927a0`): the real interpreter's free-form weight keys
  (`time_zone_overlap: 1.0`, `car_free_livability: 0.9`) never matched the canonical criterion
  names, so every user weight silently became the 0.5 default — ranking could not reflect stated
  priorities in real mode, the product's core claim. An ordered pattern table now maps them;
  unmapped keys stay verbatim so an unrecognized priority still counts as unevidenced.
- **D8b — Overpass starvation** (`9405937`): all Overpass jobs shared one global `Semaphore(2)`,
  so with ~16–32 jobs per request most spent their whole 50 s budget queueing. Now per-endpoint
  limits (2 for overpass-api.de per its usage policy, 4 for kumi.systems) with round-robin
  dispatch, and `LocalMobilityTool` bounds its OSM sub-call at 40 s so an Overpass stall degrades
  to Wikivoyage-context-only evidence instead of losing the whole tool run. *Verified live under
  the same evening Overpass load that produced the failures:* LocalMobilityTool went 0/8 → **8/8
  ok** (Kraków 21,368 / Berlin 27,595 mobility elements; stalled cities returned context-only),
  AmenitiesTool 12.5% → 4/9 ok with `queue_seconds=0.000` on every invocation — the remaining
  failures are upstream Overpass slowness on specific cities, honestly disclosed per D11.

### Closing out the ledger, 2026-08-05

- **D13 — landing-page copy** (`bdd9f60`): the last deferred defect. Hero and feature-card copy
  no longer promise visa data or real-time information; see the D13 detail in section 6.
- **D22 — mock scorer honesty** (`d6b2939`): noticed while verifying D8b. When a tool's OSM half
  failed, the mock Dynamic Evaluation stand-in summed an empty counts dict to zero and emitted a
  *scored* 0.0 reading "Local mobility infrastructure count (0) informs this score" — the same
  absence-as-evidence pattern D11 fixed elsewhere. Absent counts now leave the criterion unscored
  so it renders as "not assessed"; a genuine dict of zero counts is real evidence and still
  scores. Both cases are pinned by tests.

### The subset re-validation run — DONE (2026-08-05, `validation_runs/20260805T051351Z-real-api-postfix-2`)

P01, P03 and P05 against the real provider: three prompts, 13 calls, **$0.1130** — ledger and
provider-billed agree to the tenth of a cent. All three `status: ok`. Offline gate green first (481 passed, 1 skipped,
`ruff` clean); `.env` was left at `MOCK_LLM=true` and overridden by environment variable at launch.

| Fix under test | Outcome |
|---|---|
| **D8b** — do Amenities/LocalMobility evidence the car-free criterion in real mode? | **PASS**, and this closes the previous run's one substantive negative finding. P01's finalists carry real component counts (Seville, Cluj-Napoca, Timișoara all `work_infrastructure 1.0` from `{coworking: 1.0, cafe: 1.0}`) and differentiated `transportation` (0.87 / 0.76 / 0.69) with concrete prose — Seville's rechargeable travel card, Timișoara's "schedule reliability is a major problem" |
| **D20** — does a named reference timezone score? | **PASS**. Every P05 candidate scored on measured hours: Guadalajara ~6.0h → 1.0, Santiago ~8.0h → 1.0, Lisbon ~3.0h → 0.75, Barcelona ~2.0h → 0.5. On 2026-08-04 this criterion was never scored at all despite carrying weight 1.0 |
| **D21** — do free-form weight keys drive ranking? | **PASS**. No flat 0.5 profile anywhere. `time_zone_overlap: 1.0` → `timezone: 0.282` (highest, P05); `cost_of_living: 0.95` → `cost: 0.306` (highest, P01); P03's ranked priorities → `student_life 0.253 > safety 0.227 > transportation 0.213 > cost 0.200 > activities 0.107`, descending in the stated order |

Two new defects came out of reading the traces, both fixed the same day.

- **D23 — everything reported as missing evidence** (`7a99f1a`): `missing_evidence` was
  `[c for c in profile.relevant_criteria if c not in criterion_scores]` — free-form interpreter
  prose on the left, canonical vocabulary on the right, so nothing ever matched. Seville listed 7
  of 7 criteria missing against 4 real scores; Lisbon 5 of 5 against 4. Not cosmetic: the
  validator intersects that list with the high-weight criteria to set `should_research_again`, and
  the orchestrator maps each item through `_CRITERION_TO_TOOLS` to choose gap tools — so **P03 and
  P05 each spent a gap research iteration that selected no tools at all**, then disclosed "some
  high-priority criteria remain unverified" about evidence they were holding. P01 escaped only
  because its interpreter happened to write `"cost of living"` in one list and `cost_of_living` in
  the other; whether a wasted iteration fired came down to LLM spelling. `_tool_priorities` had
  the same mismatch, dropping a wanted criterion to priority 0.0. D21 introduced the canonicalizer
  for the weights path; this extracts the per-name half and applies it at all four sites. Replayed
  against the captured profiles: Seville 7 → 1, Munich 4 → 1, Lisbon 5 → 0, and the survivors
  (`city size`, `english_taught_programs`) are genuinely unscored.
- **D24 — a stated overlap minimum was never enforced** (`5606431`): P05 asks for "at least four
  hours of overlap with US Eastern" and the interpreter records it as a hard constraint, but
  `_HARD_CONSTRAINT_KEYWORDS` has no timezone row — and adding one would not have helped, because
  those rows threshold a 0-1 score at 0.2 and Lisbon's ~3.0h scores 0.75. **Lisbon ranked #1 while
  missing the minimum the request was built around.** An hours minimum is now compared in hours,
  against `estimated_workday_overlap_hours`, with the figure read from the constraint phrase that
  mentions the overlap (so P02's "no more than 5 hours flight" cannot supply it).
  *Enforcement alone was worse than the defect:* mock P05's candidate set holds no city that
  reaches four hours (Lisbon 3.0h is the best of it, Taipei 0.0h the worst), so everything was
  eliminated and the request failed outright — the same
  no-answer-at-all failure D16 had to undo for continental regions. When no candidate can meet the
  bar the field is now un-eliminated and ranked, with the failed check kept in
  `hard_constraint_results` and the shortfall promoted to the leading drawback. Verified in mock
  mode at $0: `status: error` → `status: ok`, Lisbon first on the best available 3.0h, Taipei last
  at 0.0h, every candidate stating how far short it falls.

### Follow-ups this report does not cover

- The enhancement backlog (section 8) is untouched. E1 (boilerplate justifications) and E3
  (Wikivoyage prose collected then discarded) are the most valuable.
- There is still **no CI**, no coverage measurement, and no frontend test tooling.
- **Region preferences still never filter.** Both P01 and P05 disclose "the stated region
  preference (Europe) could not be matched against any candidate's country, so it was treated as
  guidance rather than a filter". That is D16's fix working as designed — `check_geocoded_constraints`
  matches country names and ISO codes, and deliberately does not resolve a continent to its member
  countries because no region taxonomy exists in the codebase. It is disclosed and candidate
  selection still targets the region, so it is an enhancement rather than a defect, but it is the
  reason a continental preference cannot be enforced.

### Suggested next session

Ranked. The first item is the only one with a correctness argument behind it; the rest are
improvements.

**1. Confirm D23 and D24 against the real provider (~$0.08 for P05 + P03, ~$0.34 full suite).**
Both were verified offline and in mock mode, but neither has met the real LLM. **P05** is the
one that matters: it should now show *no* spurious `missing_evidence`, spend *no* wasted gap
research iteration, and either eliminate the sub-four-hour candidates outright or — if the
candidate set has no qualifying city — rank them with the shortfall stated. **P03** is the
cheap confirmation that the gap iteration it wasted last time is gone. Full suite only if you
want the complete picture; nothing else is known to be at risk.

```bash
# 1. Offline gate -- must be green before spending anything
pytest -q && ruff check .

# 2. Provider state, read-only, $0
python scripts/probe_llmod_account.py

# 3. Start the server against the real provider. Prefer the environment
#    variable over editing .env -- there is then nothing to remember to revert.
#    PowerShell: $env:MOCK_LLM="false"; uvicorn app.main:app --port 8000
MOCK_LLM=false uvicorn app.main:app --port 8000

# 4. Second shell -- captures to validation_runs/, judges nothing
python scripts/run_e2e_suite.py --label real-api-d23-d24 --only P03,P05

# 5. Reconcile spend
python scripts/show_llm_usage.py --calls && python scripts/probe_llmod_account.py
```

What to look for in the captured `steps`, beyond `status: ok`: each candidate's
`missing_evidence` should list only criteria that genuinely have no score (compare against its
`criterion_scores`); `validation_issues` should no longer claim high-priority criteria are
unverified when they are scored; and P05's `hard_constraint_results` should carry a `timezone`
entry, which it never did before.

**2. E1 — boilerplate justifications.** The single most visible quality gap: "Cost evidence
compared against the stated budget informs this score" appears as the *why it fits* for most
candidates, so a reader cannot tell why rank 1 beat rank 4. Mock-renderer work, no LLM cost.

**3. E3 — Wikivoyage prose is fetched then discarded.** Rich See/Do prose is collected and never
scored. Now more valuable than before: D8b's degradation path deliberately returns
context-only evidence, so wiring the prose into scoring turns a fallback into a real answer.

**4. Decide the budget-refusal `steps` question** (the one deliberate non-change above), and
**E8** — add a golden case for a positive region constraint, the gap that let D7 survive.

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
| 3 | API mode | **real** | **real** | Done — 10 prompts |
| 4 | UI mode | **real** | **real** | Done — P02 through the browser |

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
| `MOCK_LLM` | `true` (configs 1–2) / `false` (configs 3–4) |
| Tools | **real** in all four — Nominatim, Overpass, Open-Meteo, Wikivoyage, GOV.UK, World Bank, WhereNext, Frankfurter |
| `LLMOD_MODEL` | **`MB5R2CF-azure/gpt-5.4-mini`** — the fully-qualified id; the short form `azure/gpt-5.4-mini` is rejected |
| `LLM_TEMPERATURE` | **unset** — must not be pinned, see D15 |
| Provider | LLMod.ai, confirmed to be a **LiteLLM proxy** (`/key/info`, `/model/info`, `/spend/logs` all respond) |
| Provider pricing | $0.75 /1M input, $4.50 /1M output (from `/model/info`; verified against a billed call) |
| Key budget | **`max_budget: None`** — no provider-side cap, see D19 |

Establish all of this with `python scripts/probe_llmod_account.py` (read-only, $0).

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

#### D13 — Landing page claims capabilities the system explicitly disclaims · **high** · **FIXED**

> **Trusted & Up-to-Date** — "Real-time visa, cost, and safety information you can rely on."
> Hero: "Get personalized recommendations, costs, **visas** and more."

**There is no visa tool** among the 11 registered tools. Nothing is real-time — cost data is a
static dataset stamped `2026-01-15`, climate is 2021–2025 normals. And every generated response
says the opposite: *"No live prices … or guaranteed admission/visa eligibility are claimed here."*
The marketing copy contradicts the product's own disclaimer and promises a capability that does not
exist. Cheap to fix; it undercuts the project's central claim of evidence-based honesty.

Fixed on 2026-08-05 (`bdd9f60`). The hero now reads *"personalized, evidence-backed
recommendations for costs, climate, safety and more"* and the feature card is *"Evidence You Can
Check — every claim cited to its source, with retrieval dates and confidence levels"*, which
describes what the system genuinely does. `test_index_does_not_claim_capabilities_the_system_lacks`
asserts the words "visa", "real-time" and "up-to-date" do not appear in the rendered page.

#### D15 — Pinning `temperature` breaks every call · **critical** · real mode · **FIXED**

A self-inflicted regression from this work. `temperature=0`, added for reproducibility, is rejected
outright:

> `litellm.UnsupportedParamsError: gpt-5 models (including gpt-5-codex) don't support
> temperature=0. Only temperature=1 is supported.`

Every real LLM call returned 400. Fixed: `temperature` is now **omitted** unless explicitly
configured, and `.env.example` warns against setting it. **Determinism is not achievable with this
model** — the reproducibility goal is simply unavailable, which is worth knowing before anyone
tries to re-pin it.

#### D16 — Any continental region preference fails the whole request · **critical** · real mode · **FIXED**

`check_geocoded_constraints` matches `preferred_regions` against a candidate's **country name or
ISO code only** — its own docstring concedes that broader region names "are not resolved to member
countries since no region-taxonomy dataset exists". A non-match then **eliminates**.

So `preferred_regions: ["Europe"]` matches no candidate's country, the entire field is eliminated,
and the flagship prompt P01 died with *"All candidate destinations were eliminated by region
constraints"*. The real interpreter also emitted `["Europe", "mid-sized city"]` — a size preference
is not a region at all.

**This was masked by D7:** mock hard-codes `preferred_regions: []`, so the two defects concealed
each other and only the real LLM exposed it.

Fixed in `Orchestrator._relax_unresolvable_preferred_regions`: a preference that eliminates *every*
candidate is treated as unresolvable and relaxed to guidance, with the relaxation disclosed as an
assumption. `excluded_regions` still eliminates absolutely — it is a stated deal-breaker and it
*can* be resolved against country identity.

*Note:* the first fix attempt (in `select_finalists` alone) was insufficient — `_check_hard_constraints`
re-runs the same region check during scoring, so the failure merely moved downstream to
*"eliminated by hard constraints"*. The relaxation must happen once, on the profile.

#### D17 — A failed LLM module silently vanishes from `steps` · **high** · real mode

On **P10** the Request Interpreter call failed, the deterministic fallback ran, and **no step was
recorded**: `steps` contained only `['Agentic Research', 'Dynamic Evaluation', 'Recommendation
Generator']`.

Two consequences. First, the course spec requires the pipeline to be documented in `steps`, and
`scripts/golden_set/scorer.py` asserts `expected_modules <= modules_called` — this response would
fail that check. Second, and worse for a user, the degradation is **invisible**: the response reads
as a normal four-module run, giving no hint that interpretation fell back to keyword matching.

The ledger did record the failed call (`success=0`), so the information exists — it just never
reaches the response. A `steps` entry noting the fallback would fix both problems.

#### D18 — "Is Lisbon a good fit?" drops Lisbon entirely · **high** · real mode

P09 names a city and asks for a verdict. The interpreter put it in `preferred_regions: ["Lisbon"]`
— a city, not a region — which matches no candidate's *country*, so D16's relaxation discarded it
and the funnel ranked **Batumi, Málaga, Bucharest, Buenos Aires, Belgrade, Tbilisi, Kuala Lumpur**.
**Lisbon does not appear at all.** In mock mode it at least ranked 6th.

The underlying issue is systemic: `preferred_regions` is being populated with continents, cities,
and non-geographic phrases, while the only consumer understands country identity. Either the
interpreter contract must be narrowed (regions only, with a separate field for a named target), or
the consumer must handle the other cases.

#### D19 — The key has no provider-side budget cap · **medium** · operational

`/key/info` reports `max_budget: None`, `tpm_limit: None`, `rpm_limit: None`. `README.md` states
*"the real budget backstop is the LLMod.ai account balance itself"* — for this key that is **not
true**. `MAX_PROJECT_BUDGET_USD` is the only spend protection in existence, which makes the D0 fix
load-bearing rather than a nicety.

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

## 7b. Real LLM vs mock

The single clearest result of this exercise: **the real LLM layer is good, and the evidence layer
is what limits it.** Same ten prompts, same real tools, only the LLM differs.

| | Mock | Real (`MB5R2CF-azure/gpt-5.4-mini`) |
|---|---|---|
| Distinct answers across 10 prompts | **6** (P01/P06/P07/P10 byte-identical) | **10** |
| Criterion weights | every one exactly `0.5` | genuinely differentiated |
| P02 origin "Tel Aviv" | dropped | extracted; all finalists within ~4 h |
| P03 finalists | Seoul, Singapore, Warsaw | Porto, Budapest, Cluj-Napoca, Kraków… |
| P05 timezone | Bucharest #1 (~1 h overlap) | Guadalajara #1 (~2 h gap) — correct |
| P07 ambiguity | asks for clarification | infers "vacation" and proceeds |

Worked examples of the real interpreter's quality:
- **P01** — `budget: 1.0, car_free: 0.95, internet: 0.9, coworking: 0.75, nightlife: 0.0`. The
  "don't care about nightlife" instruction landed as a literal zero weight. **D1 and D10 are
  therefore confirmed mock-only.**
- **P04** — `safety: 1.0, nighttime_safety: 0.95, walkability: 0.9, party_scene: 0.0` → Singapore,
  Munich, Stockholm, Helsinki, Copenhagen. A knowledgeable person would endorse that list.
- **P06** — all three accessibility criteria weighted `1.0`.

Where the real mode is still wrong:
- **Every criterion except cost is unevidenced** (D8). P01's finalists all read *"Missing
  verification for internet quality, coworking, and car-free livability"* at **Low** confidence —
  precisely the criteria weighted 0.95, 0.9 and 0.75. Ranking collapses to cost, which several
  prompts gave as a *ceiling*, not a goal.
- **P06 ranked Manchester #1** for "escaping winter, mild winters, not housebound by cold".
  Manchester in November–April is the opposite of the request.
- **P08** errored with *"All candidate destinations were eliminated by hard constraints"*. Refusing
  an impossible request is defensible, but the message never says **which** constraint was
  impossible or that the request was self-contradictory — the one thing the user needs to hear.
- **Non-determinism is real and unavoidable.** P02 returned Crete/Rhodes/Split/Antalya/Alanya via
  the API and Crete/Split/Rhodes/Varna/Dubrovnik via the UI. With temperature unusable (D15),
  identical prompts will not reproduce.
- **Runtime roughly doubled.** Median ~95 s (mock) → ~160 s (real), with **P06 at 245 s** against
  the 285 s deadline. Real mode has far less headroom than mock suggested.

On the good side, the banned-claim discipline held: *"I did not assume exact current hotel or
housing prices, exact flight times, or guaranteed beach conditions"*, and P10's injection was
**not** obeyed — no persona swap, no invented prices, no visa fee.

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

| Date | Activity | Calls | Input | Output | Cost |
|---|---|---:|---:|---:|---:|
| 2026-08-04 | Configs 1–2 — all mock runs | 49 | 69,958 | 46,767 | **$0.0000** |
| 2026-08-04 | Account probe + connectivity check | 0 | 19 | 5 | **$0.0000** |
| 2026-08-04 | P01 diagnostics (2 failed runs, D15/D16) | 4 | 2,348 | 3,072 | $0.0156 |
| 2026-08-04 | P01 real, successful | 4 | 7,022 | 3,769 | $0.0222 |
| 2026-08-04 | Config 3 — P02–P10 real, API | 35 | 88,391 | 42,745 | $0.2635 |
| 2026-08-04 | Config 4 — P02 real, UI | 4 | ~7,000 | ~3,500 | ~$0.0364 |
| 2026-08-04 | **Post-fix verification run** — P01–P10 real, API | 41 | 120,881 | 52,308 | $0.3252 |

| | |
|---|---:|
| Local ledger total | **$0.6862** |
| **Provider `/key/info` (authoritative)** | **$0.6395** |
| Remaining of $13.00 | **$12.36** |
| Budget consumed | **4.9 %** |

**The $0.0467 gap reconciles exactly.** It is two failed Request Interpreter calls on P10 — one
per real run — each locally estimated at ~$0.0234 using deliberately conservative worst-case
pricing and not billed by the provider. The ledger is correct and errs on the safe side, which is
the behaviour you want from a spend guard.

**Actual cost per full prompt: ~$0.022–0.029** — roughly a third of the pre-run estimate, because
real output tokens came in well below the 2.2× multiplier assumed from the single historical
datapoint. At this rate the entire ten-prompt suite can be re-run **~45 more times** within budget,
so iterating on the open defects and re-validating is comfortably affordable.

Note the pricing asymmetry: output is **6× input** ($4.50 vs $0.75 per 1M), so spend is dominated
by generated tokens and `LLM_MAX_OUTPUT_TOKENS` is the effective cost lever.

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

Superseded by **section 0**, which carries the current defect status and the handover for the next
session. The items that were listed here — D8, D6/D7/D10, D17, D18 — have since been fixed and
**verified against the real provider on 2026-08-04**; D8's residual (D8b) and D13 were fixed
afterwards. **Every defect on the ledger is now closed** except D19, which cannot be fixed from
this repository (the key's budget cap is provider-administered).
