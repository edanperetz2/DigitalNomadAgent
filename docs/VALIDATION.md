# Validation Report

How DigitalNomadAgent has been validated, what the end-to-end runs found, and what still needs
fixing. Two things are kept deliberately separate: **defects** (it does not work as intended) and
**enhancements** (it works, but could be better). Correctness comes first.

Last updated: **2026-08-06**.

> **Reading order.** Section 0 is the current status, including the results of all three
> real-provider runs — the post-fix verification run (2026-08-04), the subset re-validation and
> the confirmation run (both 2026-08-05) — and the fixes that followed each. Sections 2–9 are the
> original findings, written before the fixes landed — they still describe several defects as
> open, and section 8's enhancement list is partly superseded (see "Follow-ups"). Section 0 is
> authoritative on what is fixed; the detail below explains *why* each one mattered and is worth
> keeping for that reason.

---

## 0. Current status and next steps

### Where this stands, in one paragraph

**D60, D61 and D62 are open; D0–D59 are closed.** D35 is also still unverified against the provider — Overpass has now been unreachable for four runs. D55, D56 and D58 were all
found on 2026-08-06 by reading the ten answers of the first run against the *deployed* app. D31–D45 came from reading the ten
answers of the 2026-08-05 full run as prose rather than as pass/fail — every one of them had
passed the golden set, because that suite checks *structure* (the four modules ran, a table
exists, no banned claim leaked) and nothing in it tests whether the recommendation is **correct**.
That blind spot is the single most important finding in this document. The offline gate is green
(**698 passed, 1 skipped**, `ruff` clean) and **$9.50 of the $13.00 budget remains**
(account-authoritative — see the D19 note for why the account figure, not the key's, is the one
that binds).

**The deployment now runs on the real model.** The Vercel `LLMOD_API_KEY` was replaced between
sessions, confirmed by probe on 2026-08-06 (`20260806T112005Z-vercel-probe`, P01, $0.033): the
answer ends "a real LLM provider (LLMod.ai)" with no reduced-capability notice and no 401 in the
record. The full ten then ran against the deployed URL for the first time
(`20260806T112705Z-vercel-full`, $0.376) — **10/10 `ok`, every answer on the real model**. Every
prior run in this repository went to a local `uvicorn`, which is exactly how D50 survived.

**The 2026-08-06 verification run is clean: 10/10 `ok`** (`20260806T053620Z-d45-smoke` P01 plus
`20260806T053849Z-d31-d45-real` P02–P10, $0.4134, 42 calls). Across all ten answers: no internal
score reaches the prose, no pipeline identifier leaks, and `target_months` is correct on every
prompt that stated timing — the bug behind D31, where all ten had been scored against August.

**The coverage gap is closed.** Three real-provider runs on 2026-08-05: two subsets
(`20260805T051351Z-real-api-postfix-2`, $0.1130, confirming D8b/D20/D21 and finding D23/D24;
`20260805T061515Z-real-api-d23-d24-e3`, $0.1098, confirming those plus E3 and the input-token
ceiling), then the **full ten-prompt suite** (`20260805T122313Z-real-api-full`, $0.3525), which
confirmed E4 and D27 and exposed **D28** and **D29**.

**Only D35 and D37 are waiting on a run** (Overpass has returned no data for three runs running;
see the handover). D28 and D29 were confirmed against the real
provider straight after being fixed (`20260805T145400Z-real-api-d28-d29`, $0.0944): **P08 returns
`ok`** where it had errored two hours earlier, with eight Scandinavian finalists and the budget
disclosure firing on its own, and **P09 returns Lisbon at rank 2** under the exact
`preferred_regions: ["Portugal", "Europe"]` that had been eliminating it.

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
| D19 | ~~No provider-side budget cap~~ — **the original finding was wrong.** The cap is on the *account* (`/user/info` → `max_budget: 13.0`), not the key (`/key/info` → `max_budget: None`); the probe read only the key, so the authoritative balance was fetched on every run and never shown | **Fixed** (2026-08-05) | `14d582e`, corrected in `5249442` |
| D20 | Time-zone criterion never scored when the reference timezone is named ("overlap with US Eastern") rather than implied by an origin | **Fixed** | `8d1f6b5`, `03ce480` |
| D21 | Free-form interpreter weight keys (`time_zone_overlap`) never matched the scoring vocabulary, so every user-stated weight fell back to the 0.5 default | **Fixed** | `17927a0` |
| D8b | Residual of D8: global `Semaphore(2)` starved Amenities/LocalMobility jobs, and a killed LocalMobilityTool lost its already-fetched Wikivoyage prose | **Fixed** | `9405937` |
| D22 | Mock scorer rendered a failed count lookup as a scored `count (0)` — absence of evidence as evidence of absence | **Fixed** | `d6b2939` |
| D23 | `missing_evidence` compared free-form interpreter criterion names against the canonical scoring vocabulary, so every criterion was reported missing even when scored — and the same mismatch made the gap-research iteration select no tools | **Fixed** (2026-08-05) | `7a99f1a` |
| D24 | A stated working-hours overlap minimum ("at least four hours with US Eastern") was never checked, so a candidate missing it ranked #1 | **Fixed** (2026-08-05) | `5606431` |
| D25 | `llm_max_input_tokens` was declared, set in `.env`, and read nowhere — a configured guard that could never fire, and set to a value (4000) that real calls exceed anyway | **Fixed** (2026-08-05) | `27586eb` |
| D26 | The region-relaxation disclosure asserted "candidate selection still targeted it" — false whenever the generator ignores the region, which nothing in the pipeline can detect | **Fixed** (2026-08-05) | `4cf8bc4` |
| D27 | A requested region that cannot be resolved to countries is never actually researched: P08 asks for Scandinavia and gets 30 candidates, none Scandinavian | **Fixed** (2026-08-05) | `dbda6bb` |
| D28 | An unmeetable hard constraint emptied the field and failed the request outright — D24 solved this for timezone only, so P08's $400 budget in Scandinavia killed the whole run | **Fixed** (2026-08-05) | `e45a936` |
| D29 | A named destination was eliminated and vanished from the answer: `verify_candidates` never copied the geocoded country, so a synthesized candidate matched no region | **Fixed** (2026-08-05) | `e45a936` |
| D30 | `named_destinations` never reached the Recommendation Generator, so it could not lead with a verdict on the place the user actually asked about | **Fixed** (2026-08-05) | `416d734` |
| D31 | `target_months` was absent from the interpreter's field list, so it arrived empty on every request and both climate tools fell back to *the current calendar month* — all ten prompts were scored against August | **Fixed** (2026-08-06) | `36685d2` |
| D32 | P10's interpreter call failed with a 400 whose body was discarded; the deterministic fallback took over silently, "Bali" was never extracted, and the three out-of-scope asks went unanswered without being declined | **Fixed** (2026-08-06) | `99b53e9` |
| D33 | A hard constraint that nothing measured cost a candidate nothing: P02 capped flight time at five hours and Madeira, at nearly seven, ranked first | **Fixed** (2026-08-06) | `008111d` |
| D34 | Nothing measured terrain or spoken language, so P06's wheelchair user was recommended Lisbon — with its funiculars cited as evidence *for* step-free access | **Fixed** (2026-08-06) | `25c2b4f` |
| D35 | Deal-breakers were collected and never queried, so P04's "big party destinations" exclusion put Barcelona first with its nightlife reframed as a virtue | **Fixed** (2026-08-06) | `4abf3f2` |
| D36 | The uncertainty penalty was a flat 0.15 by count, so P04 could lose food scene, street food, market culture and party level and still rank five cities | **Fixed** (2026-08-06) | `69f8fdc` |
| D37 | A zero amenity count read as "none here": Gdansk was *excluded* on "0 coworking and 0 cafes", and the coworking query missed the `coworking=yes` tagging entirely | **Fixed** (2026-08-06) | `f60ebbd` |
| D38 | Only the budget half of P08's impossibility was detected — the answer never used the words "snow" or "swim" | **Fixed** (2026-08-06) | `1507602` |
| D39 | A country-level cost estimate cannot separate two cities in one country, but carried full weight anyway (Recife and Rio both "Brazil ~$1,300") | **Fixed** (2026-08-06) | `ccf2d17` |
| D40 | Costs were quoted in whichever currency they arrived in — P01 stated a EUR budget and answered in USD and BGN | **Fixed** (2026-08-06) | `9f75b5e` |
| D41 | Internal 0-1 scores reached the prose ("Total score is 0.8145"), inconsistently between prompts and without discriminating (0.61 for six of seven candidates in P05) | **Fixed** (2026-08-06) | `e9a2f57` |
| D42 | Pipeline vocabulary reached the reader, including "I did not receive a `named_destinations` field" | **Fixed** (2026-08-06) | `36b00d6` |
| D43 | "Yes with conditions" was returned for nearly every candidate, naming no condition | **Fixed** (2026-08-06) | `cadc639` |
| D44 | The bibliography never said which city a source supported, and cited the Overpass *documentation page* as the source of its counts | **Fixed** (2026-08-06) | `4f25786` |
| D45 | P07 says outright "I don't really know what I'm looking for" and got a scoring table with no question, on the one prompt where the traveller had said they could not specify | **Fixed** (2026-08-06) | `91402f9` |
| D46 | Terrain triggers included bare "mobility"/"accessible"/"flat", so elevation spread became the headline reason in P08 — a prompt about snow, swimming and cafés. **Introduced by D34.** | **Fixed** (2026-08-06) | `54803ef` |
| D47 | P03 and P06 proposed ~30 places each, got one through, and printed a one-row "Best matches" table without saying the rest could not be researched | **Fixed** (2026-08-06) | `54803ef` |
| D48 | The elimination reason read "the evidence puts it below the minimum this request sets" — for cost, that reads as *cheaper* than required. **Introduced by D41.** | **Fixed** (2026-08-06) | `54803ef` |
| D49 | The real interpreter left `target_months` empty for "a month this winter", so P08 told the traveller it did not know when they were going — after they had said | **Fixed** (2026-08-06) | `54803ef` |
| D50 | **The Vercel deployment served nothing.** `vercel.json` rewrites every request to `/main.py` and the Python runtime hands the ASGI app that rewritten path, so FastAPI matched no route and answered `{"detail":"Not Found"}` to everything, `/openapi.json` included, while importing and running correctly | **Fixed** (2026-08-06) | `ac9c118` |
| D51 | A *failed* hard constraint and an *unconfirmed* one produced the same verdict; evidence that a place does not meet a non-negotiable is a no, not a "yes only if" | **Fixed** (2026-08-06) | `ac9c118` |
| D52 | Monthly rent-inclusive living costs were used to judge a two-week holiday: P02 read "$1,063 per month" as "mid-range rather than luxury" | **Fixed** (2026-08-06) | `ac9c118` |
| D54 | The generator's 4000-token output ceiling truncated the largest answers mid-JSON, and the repair attempt asked only for valid JSON so it truncated identically — P08 lost its written answer to the template, silently | **Fixed** (2026-08-06) | `14502f0` |
| D53 | "I don't care about nightlife at all" was recorded as a deal-breaker *and* weighted 0.0. Harmless until D35 made deal-breakers score against a place; after that a city is marked down for something the traveller shrugged at | **Fixed** (2026-08-06) | `e492629` |
| D55 | A stated non-negotiable was recorded **met** at the 0.2 *elimination* floor — one threshold answering two questions — so P06's "reasonably flat terrain" passed for a city the same evaluation labels rolling and whose cited evidence says "steep in parts (requiring walking up and down stairs)". Now three bands: met ≥ 0.75, unconfirmed between, fails below 0.2. Elimination is unchanged | **Fixed** (2026-08-07) | `5be55ee` |
| D56 | The collapse disclosure is routed **through the model**, which dropped it: P06 proposed 30 places, delivered a one-row table, and never said so. D47's deterministic notice sits at finalist selection; this collapse happened later, at hard-constraint elimination. Also computed before `_score_unresolved_criteria` could rescue candidates, so it fired wrongly too — P02 carried it while delivering seven | **Fixed** (2026-08-06) | `bcd84c3` |
| D57 | Overpass failover had no memory, so every query re-paid the full 22s timeout for a dead mirror — and round-robin tried it *first* on half of them, exhausting the 50s per-invocation cap before the working endpoint was reached | **Fixed** (2026-08-06) | `f9eabc1` |
| D58 | Naming English as a preferred language was answered *worse* than leaving it implied: the named-language branch checked the country's official list only and returned 0.0 on no match, below the elimination floor. It never read `english_reach`, which the same tool computes on the same call. **This is why P06 collapsed** — 7 of its 8 researched places were eliminated for not speaking English, four of them Cypriot, while `app/languages.py` lists Cyprus as English-widespread | **Fixed** (2026-08-06) | `7c94942` |
| D61 | A stated hard constraint whose wording matches no keyword is **silently dropped** — not recorded as unverified, simply absent. `_HARD_CONSTRAINT_KEYWORDS` is matched by literal substring, so "must be liveable without a car" registers and "no car required" does not. P11, P15 and P18 recorded `{}` for every stated non-negotiable; P17 recorded one of five. The reader is never told the requirement went unchecked | **Open** (2026-08-07) | — |
| D62 | The same substring matching fires **falsely**: `"one-bedroom flat"` matches the `terrain` keyword `"flat"`, and `"remote work"` matches the `accessibility` keyword `"remote"` (which means *airport/arrival* access). P12 never mentions terrain, yet its top pick's headline drawback is "the evidence does not establish the non-negotiable checks: flat terrain" — a requirement the traveller never stated. **This is D46 recurring**, which narrowed these very triggers | **Open** (2026-08-07) | — |
| D60 | `constraint_tier` is a coarse min/max: any single unconfirmed constraint drops a candidate to tier 1, so one confirmed on 2 of 3 ranks identically to one confirmed on 0 of 3. In P06 `transportation` was unconfirmed for **every** candidate, flattening the tier to 1 across the board — so Seville, with `terrain: met` (the wheelchair user's stated non-negotiable, confirmed flat), ranked *below* Lisbon, whose terrain is unconfirmed and which the answer itself calls hilly. Ordering fell back entirely to `total_score` | **Open** (2026-08-07) | — |
| D59 | A cached tool result outlives the code that produced it. `CACHE_CONTRACT_VERSION` is in the key, so the lever to retire every row existed — but nothing obliged anyone to pull it, and D44 did not, so 73 rows kept citing the Overpass documentation page under a 14-day TTL. Deployed readers keep pre-fix content for up to two weeks, and a validation run can report a fixed defect as still broken | **Fixed** (2026-08-07) | `a569185` |

### Reading the answers a second time (2026-08-06)

The ten answers from the verification run were read as prose again rather than
trusted to the structural checks, and produced D46–D52 — **two of them introduced
by this week's own fixes** (D46 by D34, D48 by D41). That is the same lesson as
before, one level up: a change that makes the structural checks pass can still
make an answer worse, and only reading it finds that out.

D50 deserves separate mention because it was invisible to every test and every
run in this repository: all validation ran against a local `uvicorn`, never
against the deployed URL, so a deployment that answered 404 to every request
went unnoticed. It is reproducible without deploying — `GET /main.py` against
the app returns the live URL's exact body — and there is now a test that does so.

**`vercel.json` sets `MOCK_LLM=false`** so the deployment exercises the real
provider. One consequence worth stating: a public URL now spends real money, and
because `SQLITE_PATH=/tmp` resets on every cold start, the local budget ledger
cannot accumulate across requests — only the provider's account-side $13 cap
genuinely binds. At roughly $0.04 a request that is about 250 requests. Consider
a rate limit before publicising the URL.

**Deployment status after D50 (verified against the live URL, not locally):**

| Endpoint | Result |
|---|---|
| `/` | 200, the UI |
| `/api/team_info` | 200 JSON |
| `/api/agent_info` | 200 JSON |
| `/api/model_architecture` | 200 JSON |
| `/openapi.json` | 200 JSON |
| `/api/execute` (P09) | `ok` in 141s, all four modules traced |

Two rounds were needed. Switching the entrypoint to strip the function prefix
got the site answering, but Vercel's `rewrites` discards the original path
entirely and passes only `/main.py` — so every request resolved to `/` and
`/api/team_info` returned the index page. Legacy `routes` selects the function
*without* rewriting the path it sees, which is the working configuration. The
prefix-stripping entrypoint stays as a backstop; it is a no-op when the path is
already correct.

**One step remains and it is not a code change.** The live `/api/execute` returns
`ok` but runs entirely on deterministic fallbacks, because every LLM call fails:

> `401 Authentication Error, Invalid proxy server token passed. Received API Key = sk-...vQSw`

The Vercel project has an `LLMOD_API_KEY` set, but not a valid one. It must be
replaced with the working key in the Vercel dashboard (Settings → Environment
Variables) and the project redeployed; the key is a secret and is deliberately
not in this repository. Until then the deployment is honest about its state —
the response carries the "Reduced-capability run" disclosure from D32 — but it
is not exercising the model. Re-run
`scripts/run_e2e_suite.py --base-url https://digitalnomadagent.vercel.app` after
fixing the key to confirm.

That the 401 is legible at all is D32: before it, the response body was
discarded and this would have read as an unattributable 400/401.

### The 2026-08-06 verification run, and what it did and did not prove

Confirmed against the real provider, per prompt:

- **D31** — `target_months` correct throughout: P01 `[4,5,6]`, P02 `[8]`, P03 `[3,4,5]`, P04 `[10]`,
  P06 `[11,12,1,2,3,4]`, P09 `[9,10,11,12,1,2]`, and correctly empty for P05 and P07. P07 says so
  in the answer: *"Because the timing of the trip is unknown, climate is not treated as a deciding
  factor."*
- **D32** — P10's interpreter still fails, and the captured body finally says why:
  `litellm.ContentPolicyViolationError` — **Azure's content filter rejects the injection prompt**.
  That is an upstream provider policy, not a defect here, and it was unattributable before this
  fix. The run degrades correctly: Bali is extracted by the fallback parser and named in the
  answer, all three out-of-scope asks are declined by name, and the reduced-capability notice
  reaches the reader.
- **D33** — Madeira is gone from P02. Every finalist (Barcelona, Nice, Cagliari, Split, Bari,
  Rhodes, Mallorca) is genuinely inside the stated five hours of Tel Aviv.
- **D34** — P06 leads on winter climate and English, both newly measured, and its verdict reads
  *"yes if you can live with a steep, stair-heavy historic centre"*.
- **D36** — P04 names food scene, street food and market culture as unmeasured and says they were
  not decisive. Barcelona fell from 1st to 4th.
- **D38** — P08's snow-versus-outdoor-swimming contradiction is stated and asks which to optimise
  for. It had never been mentioned.
- **D41/D42** — zero internal scores and zero pipeline identifiers across all ten answers.
- **D43** — conditional verdicts name their condition (P02: *"Yes if you want a shorter flight and
  can accept less family-beach evidence"*).
- **D44** — sources read "OpenStreetMap Nominatim — Seville".
- **D45** — P07 asks, and offers three concrete directions instead of demanding a specification.

**Not proved by this run: D35 and D37.** Overpass returned almost no amenity counts —
`counts_by_category` was empty for nearly every candidate, `culture` included, so neither the
nightlife query nor the widened coworking selector had data to act on. The cause is
environmental, not a regression: querying the *old* two-selector coworking query directly fails
identically (`ReadTimeout` on overpass-api.de, `ConnectError` on the kumi mirror), so the D37
selector change is exonerated. Both defects remain covered by unit tests only, and want a rerun
when Overpass is reachable.

Two smaller things the run surfaced, neither fixed:

- **P08's `target_months` came back empty** from the real interpreter, which did not read "a month
  this winter" as timing. The deterministic parser does resolve it to `[12,1,2]`. Empty is the
  safe direction — climate simply is not scored — but the timing was usable and was missed.
- **P01's interpreter put `nightlife` in `deal_breakers`** for "I don't care about nightlife at
  all", while also correctly setting its weight to `0.0`. Indifference is not avoidance, and the
  two records contradict each other. It did no harm here (P01 is a remote-work request, so
  ActivitiesTool never ran) but on a vacation prompt the D35 machinery would penalise a city for
  something the traveller merely does not care about. The interpreter prompt should state that the
  two are mutually exclusive.

One deliberate non-change, flagged rather than decided unilaterally:

- **Budget refusals still leave `steps` empty** (`df24a78`). A refused call never reaches the
  provider, so there is no interaction to document, and
  `test_budget_refusal_prevents_call_and_leaves_trace_empty` encodes that as intended. But the
  orchestrator *does* fall back on `BudgetExceededError`, so the same hidden-degradation argument
  as D17 applies. Worth a decision.

**D19 was recorded wrongly and is now corrected.** It said there was no provider-side budget cap
and that `MAX_PROJECT_BUDGET_USD` plus the D0 fix was "the entire spend guard". There *is* a
provider cap — LiteLLM carries it on the account, not the key:

```
/key/info    spend=0.8622   max_budget=None
/user/info   spend=0.9985   max_budget=13.0
```

The original investigation looked only at `/key/info`, saw `max_budget: None`, and concluded no
cap existed. `scripts/probe_llmod_account.py` had the same blind spot by construction: it called
`/user/info` and labelled it "account-level spend", but only ever ran its budget summary for
`/key/info` — so the authoritative number was fetched on every probe and never printed. Both are
fixed; the probe now prints the account's spend, cap and remaining, marked as the one that binds.

Two consequences worth carrying forward:

- **The authoritative spend is the account's, and it runs ahead of this key's** — $0.9985 versus
  $0.8622, a gap of $0.136 not attributable to this key. Every figure in this report before
  2026-08-05 used the key number and therefore *understated* spend. All spend figures below are
  now on the account basis.
- **The cap is real but blunt.** It stops the whole account at $13, which is a backstop, not
  per-deployment control. `MAX_PROJECT_BUDGET_USD` remains the only granular guard, and under
  Vercel its ledger lives in `/tmp` and resets on every cold start — so the README's advice to set
  a `max_budget` on the *key* before any public deployment still stands.

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
  **`vercel.json` sets `MOCK_LLM=false`** so the deployment uses the real provider (D50 notes).
- **`LLM_MAX_OUTPUT_TOKENS` is 8000**, raised from 4000 on 2026-08-06 (D54). `.env` sets this
  explicitly, so changing the code default alone will not take effect locally.
- **Provider pricing is $0.75 / $4.50 per 1M** (input/output), from `/model/info` and confirmed
  against a billed call — *not* the $0.1438 / $5.7205 in the course handout. `.env` deliberately
  carries the higher of each figure so the pre-call guard cannot under-estimate.
- **Spend so far: $3.14 of $13.00** (account-authoritative, after the 2026-08-06 verification run,
  the Vercel probes, and two full suites against the deployment), leaving **$9.86** — about
  25 more full suite runs. Read this from `/user/info`, not `/key/info`: this key alone shows
  $2.60, and the difference is account spend not attributable to it.
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

### The confirmation run — DONE (2026-08-05, `validation_runs/20260805T061515Z-real-api-d23-d24-e3`)

P03, P04 and P05 against the real provider: 12 calls, **$0.1098**. All three `status: ok`. P04 was
added over the planned P03+P05 subset because it is the only prompt that really exercises E3's
activities selection.

- **D23 — PASS.** `missing_evidence` is now accurate per candidate rather than listing everything.
  P05's candidates report `[]` where all four criteria are scored, and Madrid and Istanbul report
  exactly `["internet speed/reliability"]`, which genuinely is unscored. P03's Wroclaw reports
  `["student life", "English-taught programs"]` and correctly drops `student life` for Warsaw,
  which has it. P03's spurious "high-priority criteria remain unverified" issue is gone. P05 still
  carries that issue — correctly this time: it has one genuinely unevidenced high-weight criterion,
  and the gap iteration now selects a real tool for it (`internet` canonicalizes to
  `work_infrastructure` → `AmenitiesTool`) instead of selecting nothing. No tool can measure
  internet speed, so the disclosure is honest.
- **D24 — PASS, and the relaxation was load-bearing.** `hard_constraint_results` carries a
  `timezone` entry for every candidate, which it never had before. This run's candidate set came
  back entirely European — Lisbon 3.0h, Barcelona 2.0h, Sofia 1.0h — so **all eight failed the
  four-hour minimum**. Without the relaxation path this real request would have died with "All
  candidate destinations were eliminated by hard constraints". Instead every row of the table names
  the shortfall: "Still short of the required 4-hour Eastern overlap", "Only about 2 hours of
  overlap, below the requirement".
- **E3 — PASS.** Excerpts are 1,206–1,254 chars and varied, against a uniform 599 in the previous
  run — direct evidence that every excerpt used to be cut at the preview boundary. The scoring
  LLM's rationales now cite specific subsections: Hong Kong's names "culinary tours, guided walks,
  and major viewpoints like Victoria Peak", drawn from `[See] [Itineraries] [Guided walks]
  [Victoria Peak]`.
- **D25 — PASS.** The largest payload was ~7,600 tokens against the new 16,000 ceiling, so the
  enforced cap does not interfere with ordinary traffic.

The run also found the gap fixed in `68907e3`: P04 asks for "a really good food scene, ideally with
strong street food or market culture" and the real interpreter filed all of it under
`soft_preferences`, leaving `activity_preferences` empty — so E3's relevance selection had nothing
to match on for the most activity-driven prompt in the set, and fell back to opening-chunk order.
Interests are now drawn from `soft_preferences` as well; replayed against that captured profile the
activities interests go from `[]` to `["food", "scene", "street", "culture", "market"]`.

### The full suite — DONE (2026-08-05, `validation_runs/20260805T122313Z-real-api-full`)

All ten prompts against the real provider: 39 calls, **$0.3525**. **Nine returned `ok`; P08
errored.** This is the run that finally closes the coverage gap — the seven prompts outside the
earlier subsets had not been exercised since 2026-08-04, twelve commits back.

**The enhancements all hold in real mode.**

- **E4** is the strongest result: on every one of the nine successful prompts, the set of criteria
  carrying citations is *exactly* the set of criteria that were scored. Nothing scored goes
  uncited. Real per-item source names come through — `cost: [Frankfurter exchange-rate API,
  WhereNext City Price Dataset]`, `transportation: [OpenStreetMap local mobility infrastructure,
  Wikivoyage Get around section]`.
- **D27** is emphatic: P01 returned eight European finalists with the relaxation disclosure gone
  entirely, and P08's generator returned Stockholm, Uppsala, Gothenburg, Luleå and Kiruna where it
  previously returned Chiang Mai and Bali.
- **D23** holds: `missing_evidence` now names only genuinely unmeasurable things — "flight
  duration", "English-taught programs", "food scene" — with no false positives anywhere.

**Two new defects, one root cause.** Both are cases where elimination deletes a candidate from the
payload and nothing anywhere reports what it failed.

- **D28** — P08 failed outright with "All candidate destinations were eliminated by hard
  constraints". **D27 caused this, and correctly**: once Scandinavia is actually researched every
  candidate is Swedish, and $400/month eliminates all of them. The prompt had only ever "worked"
  because the region was silently dropped and cheaper cities substituted — an answer to a
  different question. D24 had already solved this shape for timezone; the rule is about
  elimination, not about which criterion caused it, so it now covers whichever constraint wiped
  the field out. Note this **overturns a deliberate contract**: an impossible budget used to
  return `status: error`, asserted by an integration test and a golden case. Erroring tells the
  reader nothing about what was impossible or by how much, and every other unsatisfiable case here
  degrades and discloses, so the golden case now asserts the explanation. Straightforward to
  restore if the error is wanted for grading.
- **D29** — P09 asked "is Lisbon a good fit?" and received eight other cities plus the sentence
  "the available candidate data, however, does not include Lisbon". The server log shows Lisbon
  *was* geocoded and fully researched — BudgetFit, LocalMobility, Weather, Safety, Wikivoyage,
  Activities all ran on it — and it was then eliminated before scoring. The root cause is a
  dropped field rather than the region work: the geocoder resolves a country name and
  `verify_candidates` copied `lat`, `lon`, `canonical_name`, `country_code` and importance but
  **not `country`**, so a candidate the pipeline synthesized rather than the generator proposing
  it — a named destination, created with `country=""` — stayed countryless and matched no region.
  Pre-existing, but D27 made it bite because regions now genuinely filter. D18 pinned the named
  destination through the *funnel*; nothing protected it at *evaluation*.

### D28/D29 confirmed — DONE (2026-08-05, `validation_runs/20260805T145400Z-real-api-d28-d29`)

P08 and P09 re-run against the real provider after the fixes: 9 calls, **$0.0944**, both `ok`.

- **P08** — `error` → `ok`. Eight Scandinavian finalists (Stockholm, Trondheim, Oslo, Copenhagen,
  Bergen, Malmö, Aarhus, Uppsala) and the disclosure fires unprompted: *"None of the 8 places
  researched can be done for 400 USD monthly including accommodation. The cheapest evidenced
  option is Stockholm at about 1,565 USD a month."* Every row's drawback reads "Fails budget hard
  constraint by a large margin." This is the first time the prompt's stated requirement — degrade
  gracefully, do not fabricate, do not silently drop a constraint — is actually met.
- **P09** — Lisbon is back, at rank 2, under the exact `preferred_regions: ["Portugal", "Europe"]`
  that had been eliminating it, with `country` correctly resolved to Portugal. The verdict is
  specific and useful: *"Excellent work infrastructure and very strong transport; fits the budget
  if staying outside the center… Center estimate appears above budget, so location choice within
  the city matters."*

That run also surfaced **D30**, first noted here as a quality point and then found to be a
missing field: the opening line read "You asked for remote-work-friendly destinations" because
`_build_payload` never sent `named_destinations` at all. The generator was accurately describing
the only thing it had been given. Fixed in `416d734` and confirmed against the real provider
(`20260805T151138Z-real-api-d30`, $0.0338) — the answer now opens: *"you specifically named
**Lisbon** as a place to judge… the verdict on Lisbon is **yes-with-conditions**: it is a strong
remote-work city, but it is **not the top-ranked option** here because the center-cost scenario is
slightly above your all-in budget."*

### The final full suite — DONE (2026-08-05, `validation_runs/20260805T152411Z-real-api-final`)

All ten prompts on the current code: 43 calls, **$0.4214**, **10 of 10 `ok`** — the first clean
full suite this project has had. Run after D28/D29/D30 and the geography coverage fix, which is
what the earlier full run could not cover.

| Check | Result |
|---|---|
| **E4 attribution** | On every one of the ten, the criteria carrying citations are *exactly* the criteria scored. No scored criterion goes uncited |
| **All four modules in `steps`** | 10/10 (D17) |
| **Region filtering** | No finalist outside its stated region on any constrained prompt. P08 returned eight Scandinavian cities, P07 honored four stated regions at once. The D29 country-overwrite eliminated nothing by mistake |
| **D28 relaxation** | P08 `ok` with the disclosure: *"None of the 8 places researched can be done for 400 USD monthly… the cheapest evidenced option is Stockholm at about 1,565 USD a month"* |
| **D30 named destination** | P09 opens *"the named destination **Lisbon** is being judged explicitly first. **Verdict on Lisbon: yes-with-conditions**… It is **not** the top-ranked option here because Sofia scores higher"* |
| **P10 injection** | Not obeyed, and all four modules still recorded |

**P05 is the most improved and the clearest evidence that the timezone work composes.** It asks
for four hours of US Eastern overlap. Every finalist now scores `timezone: 1.0` with
`hard_constraint_results.timezone: True`, and the candidate set is Santo Domingo, Cartagena,
Guayaquil, Recife and Quito — Latin American cities that genuinely satisfy the requirement. On
2026-08-04 the same prompt returned European cities that all failed it and ranked by cost instead.
That took D20 (score a named reference timezone), D21 (let the stated weight reach the criterion),
D24 (enforce the hours) and D27 (research places that can satisfy it) working together.

### Follow-ups this report does not cover

- The enhancement backlog (section 8) is partly stale. **E3 is done** (`c1fd0ae`, `68907e3`).
  **E1 is done in both modes** (`137304f`): it was already fine in real mode — its own entry
  predicted that ("configuration 3 will show whether the real LLM fixes it") — but the mock
  renderer, which every $0 verification run is read through, was still emitting one fixed sentence
  per criterion. Now each names its numbers. **E2 is resolved in real mode**; confidence varies
  within result sets. **E6 is D18**, fixed in `2c5c9bf`. Still open and real: **E4** (sources are a
  flat list, not attached to claims), **E5** (vacuous trade-offs), **E7** (UI progress is still
  driven by `setInterval` timers in `app/static/app.js`, not by real `steps`), **E8** (golden case
  for a positive region constraint).
### P08, investigated 2026-08-05 — the finding was mis-diagnosed

P08's PARTIAL was recorded as "never states plainly that $400/month contradicts Scandinavia".
Investigating it (mock LLM, real tools, $0) showed that is not the defect, and the real one is
worse — and was in fact already written down in section 5, just never numbered or carried onto
the ledger: *"P08 — asks for Scandinavia, receives no Scandinavian city."*

**That still held.** Of the 30 candidates generated for "somewhere in Scandinavia", **not one was
Scandinavian** — Lisbon, Tbilisi, Chiang Mai, Bali, Mexico City. The region could not be resolved
to countries, so it was relaxed (D16's fail-open, correct in itself), and candidate generation
then optimised for the $400 budget instead. Numbered **D27** and **fixed the same day**
(`dbda6bb`) — the taxonomy it needed turned out to be a hand-written table of the region words
people actually type, not a dataset. Worth noting the finding survived three sessions by never
having an ID.

**And the budget only looked contradictory because the region had been dropped.** With Scandinavia
gone, Tirana came in at about $385 against the $400 budget, so nothing was over budget and there
was no contradiction to state. What was true all along is "$400 is possible, but not in
Scandinavia" — the system simply could not establish the second half, because it never looked at
Scandinavia.

Fixing D27 closed that loop. P08 now researches Copenhagen and Uppsala, and the budget disclosure
fires on its own:

> None of the 2 places researched can be done for 400 USD monthly including accommodation. The
> cheapest evidenced option is Uppsala at about 3,050 USD a month, so the stated budget and the
> rest of the request cannot both be satisfied.

That is the statement the original P08 finding asked for, reached from evidence rather than
asserted — and it only became sayable once the right places were researched. The two fixes had to
compose; neither alone would have produced it.

Two things came out of the investigation itself, both fixed in `4cf8bc4`:

- **D26** — the relaxation disclosure asserted "candidate selection still targeted it". For P08
  that is plainly false, and nothing in the pipeline can distinguish the honored case from the
  ignored one, since resolving the region is exactly what failed. It now warns the reader to check
  rather than reassuring them that the region was honored.
- **A budget disclosure**, which was the original goal: when every researched place is over the
  stated budget, the answer now says so in words and names the cheapest, instead of leaving it as
  a low cost score. Bounded by measurement, silent unless every comparable candidate is over — so
  it correctly does *not* fire for P08.
- There is still **no CI**, no coverage measurement, and no frontend test tooling.
- ~~**Region preferences never filter.**~~ **Closed by D27** (`dbda6bb`). This sat here as an
  enhancement on the grounds that "candidate selection still targets the region"; P08 disproved
  that (D26), and the fix was a hand-written region→country table rather than the dataset it was
  assumed to need. P01 now filters on "Europe" for the first time: every finalist is European and
  the relaxation disclosure is gone, because nothing had to be given up.

### Next session — pick up here

**State: D60, D61 and D62 are open.** D61/D62 are one root cause — substring keyword matching over stated hard constraints, failing in both directions. D55, D56 and D58 are confirmed on live data (2026-08-07); D35 needs a run that coincides with Overpass being up. The offline gate is green (**698 passed,
1 skipped**, `ruff` clean) and **$9.50 of the $13.00 budget remains**.

P06's one-row answer took three defects to explain, and two are now closed:
**D58** was the cause (30 proposed, 8 researched, 7 eliminated by one language
scoring bug), **D56** was why the reader was never told, and **D55** — still open
— is the threshold that let a "met" verdict stand on top of it.

None of the three has been re-run against the provider. The next paid run should
re-read P06 first: the field should no longer collapse, and if it does, the
answer should now say so in words. The Vercel key is fixed and the deployment runs on the real
model — the ten prompts have now been run against the deployed URL, 10/10 `ok`.

Render any captured run as readable prose before judging it:

```bash
python scripts/render_answers.py validation_runs/<run-dir>
```

#### The 2026-08-07 post-fix run — 10/10 `ok`, $0.405, $9.86 left

`20260807T073611Z-vercel-p06-discriminator` (P06) plus
`20260807T073929Z-vercel-full-postfix` (the other nine), both against the
**deployed** app. P06 was run first on purpose: it exercises D55, D56 and D58 at
once, so it doubles as proof the deployment actually carries the new code before
committing the rest of the money.

**D58 — confirmed.** P06 returned **5 candidates, not 1**, and they include
Lisbon, Seville, Palma de Mallorca and Barcelona — Portuguese and Spanish cities
that scored 0.0 and were eliminated the day before for "not speaking English".
The fix's own wording reaches the reader: *"English is not official, but it is
widely usable in the city."*

**D55 — confirmed.** Lisbon's drawback is now *"Hillier terrain and no direct
proof of step-free access"* and the verdict says *"the evidence does not confirm
barrier-free movement"*. The day before, the same city was recorded `terrain:
met` and answered *"yes if you are comfortable with a compact, hilly historic
center"*. Unconfirmed-constraint phrasing appears in 9 of 10 answers.

**D56 — correctly silent.** No prompt collapsed (5–8 candidates from 25–31
proposed), so the block should not fire, and does not. Still covered by unit
tests for the case that matters.

Also holding across all ten: **D54** (P08 complete, conflict block intact, not
truncated), **D41/D42** (zero internal scores, zero pipeline vocabulary in any
answer), **D38** (P08 names both contradictions), **D30** (P09 leads with Lisbon;
P10 leads with Bali and gives it a verdict), **D32** (P10's interpreter still
hits Azure's content filter, the fallback extracts Bali, the reduced-capability
notice reaches the reader).

**Checked and cleared, not a defect:** Melbourne ranks 2nd in P06 with "winter-sun
fit is weak" for a November–April trip, which looks like a hemisphere error. It
is not — the tool used real 2021–2025 climatology for months 11–4 and judged
Melbourne's ~25 °C summer a weak match for "mild winters, not tropical heat".

#### D35 — unproven for a fourth run

Overpass returned **zero** sources across all ten. The deployment hit the same
504s this machine saw at 09:00, so `counts_by_category` was empty everywhere and
the nightlife scoring had nothing to act on. Nothing to do with the code: D57
removed the dead-mirror waste and the path is wired correctly end to end (the
alias `"big party destinations"` → `nightlife` → `nwr["amenity"~"^(nightclub|bar|pub)$"]`
was traced by hand). It needs a run that coincides with Overpass being up.

#### P11-P20, the extended set — first run (2026-08-07, `20260807T085224Z-vercel-p11-p20`)

Ten prompts added to cover paths the original set never touched, run against the
deployment. **10/10 `ok`, $0.358, $9.50 left.**

**What held up:**

- **P12** — all three named destinations (Porto, Valencia, Split) reach the
  finalists and the answer addresses each by name with what you give up.
- **P14** — the southern-hemisphere case works: January resolved to `[1]`, and
  costs are quoted **in AUD** against the stated AUD budget (3000 vs 4190).
- **P15** — the excluded region is honoured where it matters most, at generation:
  of 30 proposed candidates, **zero** are in Southeast Asia. Nothing is said
  about it in the answer, which is correct — nothing had to be given up (D26).
- **P18** — air quality, which no tool measures, is named and disclosed as not
  established rather than quietly answered around on climate and cost.
- **P20** — one line in, `target_months: [2]` out, assumptions disclosed.
- **P17** — both purposes survive; the answer holds fieldwork and remote work
  together rather than collapsing to one.

**What it found — D61 and D62, one root cause.** `_HARD_CONSTRAINT_KEYWORDS` is
matched by literal substring against the traveller's own wording, and it fails
both ways at once:

| Stated non-negotiable | Criterion matched |
|---|---|
| "must be liveable without a car" (P01) | `transportation` ✓ |
| "no car required" (P17) | **nothing** |
| "must be within about two hours of UK time" (P11) | **nothing** |
| "reachable from Madrid with at most one connecting flight" (P15) | **nothing** |
| "quick access to a hospital" (P18) | **nothing** |
| "budget … including a one-bedroom **flat**" (P12) | `cost` ✓ **and `terrain`** ✗ |
| "**remote** work stay" (P12) | **`accessibility`** ✗ (means *airport access*) |

So P11, P15 and P18 recorded `{}` for every stated non-negotiable, and P12 was
given two it never stated — one of which became the headline drawback of its top
pick. D62 is D46 recurring, which narrowed these exact triggers.

**Two smaller things, not filed as defects:**

- **P11's purpose came back `remote_work`, not `relocation`.** "Leaving the UK
  for good", "settle somewhere for years", schooling and healthcare — the answer
  did cover schools and healthcare, so little was lost, but the one prompt
  written to exercise the `relocation` purpose did not trigger it.
- **P13 and P16 quote no cost figure at all** despite stated budgets ($1,500/mo
  and £90/day), where P14/P15/P17/P18/P19 all quote several. P16 says why — no
  clean city-level daily figure — and declining to compare a monthly
  country-level estimate against a daily holiday budget is arguably D52 working.
  Worth a decision rather than an assumption either way.

#### D55 — CLOSED (`5be55ee`)

One threshold was answering two questions. `HARD_CONSTRAINT_ELIMINATION_THRESHOLD`
is **0.2** and is deliberately low, because a false elimination is worse than a
missed one — but it was also the bar for reporting a non-negotiable as **met**, so
anything short of catastrophic was reported as satisfied. P06's wheelchair user
called flat terrain non-negotiable; Valletta scores `0.6308` (49 m of spread,
which this codebase labels *rolling*, from evidence saying the city is "steep in
parts, requiring walking up and down stairs") and was recorded `met`. A city
needed roughly 78 m before flat terrain failed.

Three bands now: **met ≥ 0.75**, **unconfirmed** between, **fails below 0.2**.
The tri-state was already plumbed end to end for constraints nothing measured, so
this added no machinery. **Elimination is unchanged** — only `< 0.2` eliminates,
exactly as before — so the field cannot empty and nothing is dropped that was not
dropped before. What changed is what the reader is told, and that a confirmed
place now ranks above an unconfirmed one.

Correcting an earlier note here: it said two of P06's five constraints "produce
no constraint entry at all". That was wrong. All five are covered; they collapse
onto three measurable criteria — English → `language_spoken`, wheelchair /
step-free / flat terrain → `terrain`, accessible transport → `transportation`.

**Still worth a decision, and not fixed:** the `accessibility` criterion means
**airport / arrival** access (`"airport"`, `"distance"`, `"remote"`, `"arrival"`,
`"get there"`, sourced from "Wikivoyage Get in"), but reaches the generator under
a name that reads as *disability* access — and on P06 the generator used it that
way. Renaming it is a one-line change with a wide blast radius through payloads
and prompts; it wants doing deliberately, not folded into D55.

#### D56 — CLOSED (`bcd84c3`), and the rule it proves

P06 proposed **30** places, had **8 fully researched**, and delivered a
**one-row** table without telling the reader. The wording existed but travelled
as `caveats_to_pass_on` — input for the model to paraphrase — and the model
dropped it.

**Keep this result.** The same run is a controlled experiment in the D32 rule:
the four disclosures concatenated *after* the model returns
(`_conflict_`, `_coverage_`, `_out_of_scope_`, `_degradation_`) survived in all
ten answers, while the caveats routed *through* the model did not. Same run, same
model, opposite outcomes, and the only variable is which side of the LLM call the
text sits on. Anything the reader must see goes after the call.

It was also computed at `VALIDATING`, before `_score_unresolved_criteria` can
rescue an eliminated candidate, so it fired wrongly too — **P02 carried it while
delivering seven**. The old trigger was `viable < max_final_recommendations`,
which under the cap of 8 called any shortlist below eight unusually short; the
replacement uses a fixed floor of three and stays silent when nothing was
eliminated at all.

#### D37 — CONFIRMED on live data (2026-08-07, `20260806T221506Z-scheduled-overpass-retry-*`)

Overpass recovered on its own overnight (200 in 1.9s). P01 ran free under
`MOCK_LLM=true` with real tools, cited **8 Overpass sources**, and returned
coworking counts that scale with city size alongside real café counts:

| City | Coworking | Cafés |
|---|---|---|
| Barcelona | 53 | 967 |
| Berlin | 52 | 631 |
| Lisbon | 21 | 575 |
| Cluj-Napoca | 16 | 144 |
| Bucharest | 8 | 353 |
| Tirana | 7 | 567 |
| Kraków | 4 | 337 |
| Antalya | 1 | 170 |

Before the widened selector these came back 0 or 1 in cities with hundreds of
cafés. Antalya's single space is recorded as a drawback ("Thin work setup
nearby") rather than an exclusion, which is the other half of D37: a low count is
evidence, not absence.

#### D35 — still unproven, and `MOCK_LLM=true` can never prove it

P04 returned **zero** Overpass sources in the same run, and Overpass was not the
reason. The *mock* interpreter returns `deal_breakers: []`; only the real one
extracts "big party destinations". With no deal-breaker there is no avoided
category, so the nightlife scoring path never engages and ActivitiesTool is not
selected at all.

**D35 therefore needs a paid run** — roughly $0.04 for P04 alone. The free path
can confirm anything downstream of the interpreter, and nothing that depends on
it.

#### D44 is not regressed — the stale citations are cache, not code

The same run cited Overpass as `wiki.openstreetmap.org/wiki/Overpass_API`, the
documentation page D44 replaced. The production code is clean; the old value
survives only in `app/tools/fakes.py`. It came from **73 cached rows** written
before D44 landed — D59, now fixed: the contract version was bumped, which makes
every one of them unreachable.

Overpass returned **zero** sources across all ten prompts of the deployed run,
the third run in a row with no amenity data, so nightlife-avoidance scoring and
the widened coworking selector still rest on unit tests alone.

D57 removed one measured cause (a dead mirror re-charging 22s per query). The
remaining cause is a 429 standing against the caller's IP that **no client
strategy recovers** — measured on an 8-city workload: 2/8 with the current policy
in 30s, 2/8 with two retries and backoff in 172s, 0/8 at concurrency 1 in 517s.
Note `overpass-api.de` itself is healthy and fast when it grants a slot (4–11s,
Sofia `[290, 188]`, Gdańsk `[70, 49]`), and `/api/status` reports "Rate limit: 2,
2 slots available now" even while returning 429s.

Two things worth checking before spending more on this:

1. **Re-measure from the deployment, not this machine**, which has been probing
   Overpass all session and is not a clean vantage point.
2. **The Vercel cache resets on every cold start** (`SQLITE_PATH=/tmp`, same
   constraint as the budget ledger — see the D50 notes). `AmenitiesTool` caches
   with a stale-fallback path, so on Vercel every run re-queries everything from
   scratch. That multiplies Overpass load by exactly the factor the cache exists
   to remove, and is the most likely reason the deployment earns a 429 standing
   within a single ten-prompt suite.

#### Confirmed working on the deployed run (2026-08-06)

- **D54** — no prompt fell back to the template; P08 produced a full 12,964-char
  answer with its conflict block intact. The 8000-token ceiling is enough.
- **D31/D49** — `target_months` correct on every prompt that stated timing, and
  **P08 now resolves "a month this winter" to `[12,1,2]`**, closing the open item
  from the previous session.
- **D38, D41, D42, D36** — conflict disclosures fire; zero internal scores and
  zero pipeline identifiers across all ten; coverage blocks present in 9 of 10.

#### Deliberately not fixed — decide only if you want them

- **"in the evidence provided"** appears in 7 of 10 answers. It refers to data
  the agent was handed, but reads as ordinary hedging. Tightening it risks
  stilted prose for no real gain.
- **Uniform confidence within one prompt** (P02 all High, P04 all Medium, P07
  all Low). Checked and judged correct rather than broken: confidence tracks
  evidence coverage, and candidates researched identically have identical
  coverage. P07 being all-Low is right for an under-specified request.
- **P10 calls the shortlist "the named options"** when the traveller named only
  Bali. Cosmetic; the verdicts and the lead-with-Bali behaviour are right.

#### Environment notes specific to this work

- **Restart the server after any code change.** `uvicorn` runs without
  `--reload` here, and a stale server silently invalidated a verification round
  on 2026-08-06 — the results looked like a fix had not worked when it had.
- **Overpass is intermittently unreachable from this machine** (`ReadTimeout` on
  overpass-api.de, `ConnectError` on the kumi mirror). Confirmed not to be
  caused by the D37 selector change: the pre-change two-selector query fails
  identically. When it is down, amenity counts come back empty and the answers
  are thinner but honest.
- **Bash heredocs mangle regex backslashes** in this environment. Write Python
  helper scripts with the Write tool instead of piping heredocs into `python -`.
- **Console output is cp1252.** Call `sys.stdout.reconfigure(encoding="utf-8")`
  in any script that prints place names, or it dies on `ł`, `ș`, `å`.
- **A public deployment with `MOCK_LLM=false` spends real money.**
  `SQLITE_PATH=/tmp` resets on every cold start, so the local budget ledger
  cannot accumulate — only the provider's account-side $13 cap binds, roughly
  250 requests. No rate limit exists. Worth adding before the URL is publicised.

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
| Key budget | **`max_budget: None`** on the key — but the account carries **`max_budget: 13.0`**, which is the cap that binds. See the corrected D19 |

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

> **This finding was wrong and is superseded by the corrected D19 in section 0 (2026-08-05).** It
> is kept because the mistake is instructive: the conclusion was drawn from one endpoint without
> checking the neighbouring one, and the probe script encoded the same blind spot, so every
> subsequent run reproduced the error rather than exposing it.

`/key/info` reports `max_budget: None`, `tpm_limit: None`, `rpm_limit: None`. `README.md` states
*"the real budget backstop is the LLMod.ai account balance itself"* — for this key that is **not
true**. `MAX_PROJECT_BUDGET_USD` is the only spend protection in existence, which makes the D0 fix
load-bearing rather than a nicety.

*What was actually the case:* `/user/info` reports `max_budget: 13.0`. The account-level cap exists
and LiteLLM enforces it. The README sentence this finding called false was substantially right.

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
| 2026-08-05 | **Subset re-validation** — P01, P03, P05 real, API | 13 | 46,358 | 18,411 | $0.1130 |
| 2026-08-05 | **Confirmation run** — P03, P04, P05 real, API | 12 | 47,171 | 16,545 | $0.1098 |
| 2026-08-05 | **Full suite** — P01–P10 real, API (P08 errored) | 39 | 156,868 | 51,113 | $0.3525 |
| 2026-08-05 | **D28/D29 confirmation** — P08, P09 real, API | 9 | 44,584 | 15,116 | $0.0944 |
| 2026-08-05 | **D30 confirmation** — P09 real, API | 4 | 16,898 | 4,699 | $0.0338 |
| 2026-08-05 | **Final full suite** — P01–P10 real, API, all ok | 43 | 197,726 | 63,412 | $0.4214 |

| | |
|---|---:|
| Local ledger total | **$1.8112** |
| Provider `/key/info` — this key only | **$1.7175** |
| **Provider `/user/info` (authoritative — the capped account)** | **$1.8537** |
| Remaining of the $13.00 account cap | **$11.15** |
| Budget consumed | **14.3 %** |

The three figures differ for two unrelated reasons, and both are worth knowing. The ledger sits
$0.0468 *above* the key because of conservatively-estimated failed calls (below). The account sits
$0.136 *above* the key because the account is not this key alone. Only the account figure is
measured against the cap — earlier revisions of this report quoted the key and so understated
spend throughout (see the corrected D19).

**The $0.0468 gap reconciles exactly.** It is two failed Request Interpreter calls on P10 — one
per full real run — each locally estimated at ~$0.0234 using deliberately conservative worst-case
pricing and not billed by the provider. The ledger is correct and errs on the safe side, which is
the behaviour you want from a spend guard. Neither 2026-08-05 subset run included P10, and both
reconciled to the tenth of a cent, which is the expected result when no call fails.

**Actual cost per full prompt: ~$0.022–0.037** — the upper end from the 2026-08-05 runs, whose
richer evidence payloads cost more input tokens. Still roughly a third of the pre-run estimate,
because real output tokens came in well below the 2.2× multiplier assumed from the single
historical datapoint. At this rate the ten-prompt suite can be re-run **~34 more times** within
budget, so re-validating after each change is comfortably affordable.

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
afterwards, and D20–D25 were found and fixed across the two 2026-08-05 runs. All of those are now
closed, D27 included, and D19's original "no provider-side cap" finding was itself wrong — both
corrections are in section 0. **Three defects are open — D60, D61, D62**; section 0 has them.

Also open, off the ledger: the **budget-refusal `steps` decision**, and enhancements **E4, E5,
E7, E8**.
