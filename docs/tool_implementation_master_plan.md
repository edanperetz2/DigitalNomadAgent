# DigitalNomadAgent Tool Completion Master Plan

This document is the canonical scope and status tracker for completing the DigitalNomadAgent tools without invoking a real LLM. Any scope or ordering change requires user approval before implementation.

## Non-negotiable end-to-end runtime requirement

- Every current and future unit in this plan must preserve an externally observed end-to-end runtime of **under 300 seconds** for one `/api/execute` request, including interpretation, candidate generation, verification, all selected tools and retries, evidence persistence, optional gap research, recommendation generation, API serialization, transport, and UI handling.
- The backend agent has one non-renewable wall-clock budget of 285 seconds (`AGENT_EXECUTION_TIMEOUT_SECONDS`, also the allowed maximum). With the normal configuration, unfinished research is stopped after 225 seconds so 60 seconds remain for scoring and response generation (`RECOMMENDATION_RESERVE_SECONDS=60`). The browser stops waiting after 295 seconds.
- The deadline covers the whole state machine and is never reset by a new state, retry, fallback endpoint, candidate, tool, or gap-research round. At the research cutoff, completed results are retained and pending calls are cancelled. A recommendation is then generated from partial evidence with explicit low-confidence/timing disclosures; if its LLM is slow, the deterministic renderer is used. The 285-second hard cutoff repeats this no-I/O best-effort fallback rather than discarding the recommendation.
- LLM timeout/failure before research must not consume the run: interpretation falls back to the deterministic parser and candidate generation falls back to the curated purpose-specific seed set, with both substitutions disclosed in the response assumptions.
- Every unit proposal and review must account for worst-case external-call count, per-call timeout, retry/failover behavior, rate-limit waits, candidate count, concurrency, caching, and gap-research duplication. A change that could violate the 285-second backend budget must be redesigned, bounded more tightly, or rejected.
- Shared defaults remain the first line of control: at most 30 bulk candidates narrowed to at most 8 finalists via the zero-LLM candidate funnel (`app/agent/candidate_funnel.py`), bounded provider retries, 10-second HTTP timeouts unless a smaller tool-specific value is justified, deterministic tool selection, cache-first access, and at most one gap-research round. All independent tool/candidate jobs are scheduled together with an application-wide concurrency cap of 10; provider-specific rules remain stricter where needed (Nominatim serial, Overpass at most two). The hard deadline remains the final safety net.
- Every tool/candidate invocation has a 50-second default execution budget covering its internal retries and failover. Work is scheduled deterministically by hard-constraint relevance, then inferred criterion weight, and applied across candidates before lower-priority tools so timeout degradation preserves the most important evidence.

## Workflow and commit discipline

Every foundation/tool unit follows this workflow:

1. Start from a clean worktree.
2. Explain the current implementation, limitations, proposed files, behavior, interfaces, tests, and worst-case contribution to the 285-second backend budget.
3. Wait for approval before implementation.
4. If scope changes, revise this plan proposal, show the revision, and obtain approval again.
5. Implement only the approved unit and directly related tests, fakes, selection/scoring integration, and documentation.
6. Run focused tests (including deadline/cancellation coverage where timing behavior changes), the full offline suite, and Ruff. Run a timed free live API smoke test where applicable; never invoke an LLM.
7. Before committing, show changed files and diff summary, behavior changes, verification results, and remaining limitations.
8. Address review comments and repeat verification.
9. Wait for explicit approval to commit unless the user has already granted advance approval for the finished unit.
10. Create exactly one detailed commit for that unit, push it to the configured remote, confirm its hash and a clean synchronized worktree, then present the next kickoff explanation.

Unrelated user changes are never staged. If they overlap the active unit, stop and resolve scope with the user.

## Status legend

- `IN PROGRESS`: approved and currently being implemented or reviewed.
- `APPROVED`: scope is approved, but implementation is waiting for an earlier plan revision or unit to finish.
- `PLANNED`: scoped but implementation has not been approved for that unit yet.
- `DEFERRED`: deliberately removed from the active sequence and retained only for possible later reconsideration.
- `DROPPED`: intentionally removed from the product and active tool registry because its responsibility is already covered elsewhere.
- `BLOCKED`: intentionally waiting for required external data.
- `COMPLETE`: implemented, reviewed, explicitly approved, and committed.

## Commit sequence

### 0. Shared foundation — COMPLETE

Planned commit: `chore(tools): add shared tool infrastructure and master plan`

- Add this tracked master plan and change the root-only data ignore rule so future `app/data` resources can be committed.
- Add structured `target_months` and `activity_preferences` profile fields. `target_year` is intentionally omitted: historical climate evidence needs requested months, while representative dates for DST can be selected by the relevant tool without making a year part of the user profile.
- Add `EvidenceSource` and `EvidenceItem` support. A `ToolResult` may contain independently attributable evidence items, and every evidence item/source used by scoring is persisted as its own `EvidenceRecord`. A single source containing several related normalized values may remain one evidence item.
- Keep a compatibility adapter for existing single-source tools so their behavior can be migrated one tool at a time.
- Version cache keys so incompatible cached response contracts are not reused.
- Add reusable JSON HTTP, MediaWiki, Overpass, rate-limit, response-parsing, and test-injection helpers.
- Serialize Nominatim candidate requests at one request per second in accordance with its usage policy.
- Configure the shared Overpass client for at most two application-side concurrent requests and endpoint failover. This is a conservative DigitalNomadAgent default, not a provider-mandated limit.
- Keep all shared clients, retry waits, semaphores, and persistence operations cancellable under the single 285-second agent deadline; no helper may create a detached request that outlives cancellation.
- Mark the two production-data tests skipped only when their files are absent. The separate live SLA test is also skipped unless explicitly enabled, yielding three expected skips in a normal offline run until those datasets arrive.

### 1. GeocodingTool — COMPLETE

Planned commit: `feat(tools): harden destination geocoding`

- Request multiple Nominatim matches, require country agreement, reject ambiguity, and return canonical name, coordinates, country code, OSM identity, and confidence.
- Cache for 30 days, process candidates serially, and persist geocoding evidence rather than discarding it.
- Keep verification bounded by `MAX_BULK_CANDIDATES`; serial Nominatim rate-limit waits and bounded retries count against the same 285-second request deadline.
- Test ambiguity, country mismatch, low importance, rate limiting, cache fallback, and malformed responses.

### 2. PlaceContextTool — DEFERRED

Commit: `refactor(tools): defer destination context enrichment`

- Remove PlaceContextTool from active study, remote-work, vacation, and mixed-purpose tool selection.
- Stop treating a generic destination introduction as a recommendation advantage or scoring signal.
- Retain the current implementation and shared MediaWiki client only for possible future post-ranking enrichment of final recommendations.
- Any future reactivation must run only for finalists, remain non-scoring, and expose a bounded introduction, resolved title, revision identity, and exact source.
- Any future reactivation must also use cache-first, bounded MediaWiki calls for finalists only and demonstrate that it preserves the under-300-second end-to-end requirement.
- SafetyTool remains independent and may still use the shared MediaWiki client for its specific Stay safe evidence component.

### 3. WeatherTool — COMPLETE

Planned commit: `feat(tools): add requested-season climate evidence`

- Resolve requested months from structured profile fields, with a documented current-month fallback.
- Query the five previous calendar years from the Open-Meteo archive and label the result climatology rather than forecast.
- Return a multidimensional climate profile covering actual and apparent temperature, humidity, rain, snow, sunshine/daylight, cloud cover, wind/gusts, heavy-precipitation and high-wind proxies, heat/freezing frequencies, interannual variability, and per-variable coverage. Do not claim a thunderstorm frequency because the archive variables do not establish one reliably.
- Score every explicitly requested climate preference as a separate component; average only available requested components, name unavailable requested components, reduce confidence for them, and produce no universal climate score when the user states no climate preference.
- Treat missing dimensions as missing rather than zero, never use a climate result for hard elimination in this unit, and retain every raw metric and threshold used by scoring.
- Fetch the bounded five-year/month payload per candidate without unbounded pagination or per-day requests; all archive retries and parsing remain inside the shared request deadline.
- Test seasons spanning calendar years, leap years, current-month fallback, missing dates/values, misaligned arrays, insufficient coverage, stale cache, and preference-specific scoring.

### 4. WikivoyageClimateTool — COMPLETE

Planned commit: `feat(tools): add Wikivoyage climate corroboration`

- Extract only the resolved Wikivoyage Climate section and climate chart, with revision identity, exact source URL, bounded excerpts, and independently persisted evidence.
- Deterministically score only chart values and observable, preference-relevant climate statements; ignore generic claims such as pleasant or perfect weather.
- Combine each available preference component at 80% WeatherTool and 20% WikivoyageClimateTool, renormalizing to WeatherTool alone when Wikivoyage has no relevant evidence.
- Select the tool only for explicit climate preferences. Treat Wikivoyage-only components as low-confidence secondary soft evidence, ignore stale Wikivoyage evidence for scoring, and never let it alone satisfy or violate a hard constraint.
- Cap Wikivoyage confidence at medium, expose contradictions and reduce combined confidence, and carry revision dates, confidence, and staleness into stored evidence and final source rendering.
- Keep resolution, section, and chart retrieval bounded and cache-first; redirect handling may not introduce unbounded request chains or extend the 285-second deadline.
- Test missing sections, redirects, chart parsing, negation, irrelevant prose, source weighting, contradictions, and missing-source renormalization.

### 5. TimezoneFitTool — COMPLETE

Planned commit: `feat(tools): make timezone overlap date-aware`

- Use local aliases and direct IANA timezone input as the fast path. Preserve the existing reasonable country/timezone guesses and add common city aliases and comma-qualified forms.
- When the fast path has no match, make one free Open-Meteo geocoding request and accept its top usable city or country result containing an IANA timezone. Do not add a separate ambiguity engine; expose the selected canonical place so the guess remains visible.
- Cache the provider resolution for 30 days through the existing ToolCache so the same origin is resolved once and reused across destination candidates.
- Compute offsets on the 15th day of the first requested month in its next occurrence so seasonal DST is respected; use the current date with an explicit warning when no target month is available.
- Retain the simple standard 09:00–17:00 overlap model, with a small circular-offset correction for international-date-line cases, and keep the existing four-hour full-score threshold.
- Return the selected origin name and country, both IANA timezones, representative date, UTC offsets, offset difference, overlap, resolution method, and confidence. Unknown origins remain missing evidence rather than receiving a positive score.
- Resolve an uncached origin at most once per agent request and reuse it for every candidate; the local fast path and shared cache avoid multiplying network latency.
- Test local fast-path aliases, provider-resolved cities and countries, top-result visibility, cached reuse, provider failure, requested-season DST, current-date fallback, half-hour zones, date-line correction, direct IANA input, unknown origins, and missing destination coordinates.

### 6. AmenitiesTool — COMPLETE

Planned commit: `Amenities tool: return category-level evidence`

- Treat this tool as prompt-driven nearby everyday infrastructure; hospitals are explicitly outside its scope.
- Add structured `amenity_preferences` to PlaceRequestProfile and update both the real request-interpreter contract and deterministic mock interpreter to extract normalized supported categories from the user's prompt.
- Select coworking/cafes by default for remote-work requests and universities/libraries by default for study requests, then merge explicit prompt preferences from a bounded OSM category allow-list. Initial supported explicit categories are coworking, cafes, universities, libraries, parks, pharmacies, supermarkets, and fitness centres; unsupported requests remain visible as unresolved rather than being silently substituted.
- Query the selected categories in one bounded 3 km Overpass request across OSM nodes, ways, and relations. Use the existing shared client, which permits at most two simultaneous Overpass requests across candidates and fails over between public instances.
- Return independent `counts_by_category` values and deduplicate each category by OSM element type and ID. Remove the aggregate count.
- Score work infrastructure as 60% coworking (saturating at five results) and 40% cafes (saturating at 25); score student life equally from universities (saturating at three) and libraries (saturating at eight). Stop using AmenitiesTool as transportation or general-activity evidence; those criteria remain unresolved until their dedicated planned tools are implemented.
- Preserve valid category counts from partial responses with an explicit warning and reduced confidence; use stale cache or missing evidence after a complete provider failure.
- Keep the one-request-per-candidate design, shared two-request Overpass concurrency cap, bounded endpoint failover, and cache-first behavior; no category may add a separate request that risks the 285-second backend budget.
- Test prompt-to-category inference in both interpreters, purpose defaults, supported and unsupported explicit categories, one-request query generation, independent counts, node/way/relation handling, deduplication, cache behavior, fallback/rate errors, partial results, and criterion-specific scoring.

### 7. LocalMobilityTool — COMPLETE

Planned commit: `Local mobility tool: add car-free mobility evidence`

- Add a tool for bus stops, metro/tram/rail stations, pedestrian ways, and cycleways within 3 km. Query nodes, ways, and relations, deduplicate by OSM element type and ID, and return the independent raw counts without converting them into a tool-level mobility score.
- Extract the canonical destination's revision-pinned Wikivoyage `Get around` section through the shared MediaWiki client and return a bounded section excerpt, exact section identity, revision identity, and source URL. Do not apply a phrase lexicon or convert the prose into a numeric value.
- Persist the OSM counts and Wikivoyage section as separately attributable evidence so the future reasoning agent can compare the quantitative infrastructure with the qualitative local context and produce a justified aggregate score.
- Route car-free, walkability, and public-transport requirements to this tool and stop using arrival infrastructure from TransportAccessTool as evidence of local car-free living. Until the reasoning backbone is connected, transportation evidence is available but its final aggregate score is explicitly unresolved.
- Do not hard-eliminate a candidate from raw counts or Wikivoyage prose in this unit. Hard car-free feasibility remains unresolved until the reasoning agent can assess both sources under a separately reviewed scoring contract.
- Combine all required OSM mobility categories into one bounded Overpass request per candidate and use at most three cache-first MediaWiki requests to resolve and retrieve the revision-pinned `Get around` section. Reuse the shared provider limits, cache, and failover paths, keep all work inside the per-tool/request deadlines, and include measured deadline impact in review.
- Test dense, sparse, missing, stale, and partial OSM evidence; Wikivoyage redirects, missing `Get around` sections, excerpt bounds, revision attribution, separate evidence persistence, tool selection, unresolved scoring, and the absence of hard elimination.

### 7a. Shared Wikivoyage context coverage — COMPLETE

Planned commit: `Wikivoyage context: preserve section coverage for agent reasoning`

- Keep a short preview excerpt separate from the source material intended for future LLM reasoning.
- Parse revision-pinned Wikivoyage sections into heading-aware paragraph chunks and preserve all cleaned section text up to a 20,000-character safety limit.
- When a section exceeds the safety limit, distribute the available context across its subsections instead of retaining only the beginning. Expose the complete and included character counts, included, truncated, and omitted subsection names, and an explicit truncation flag.
- Store the bounded reasoning chunks and coverage metadata in Evidence Memory with the exact section, revision, and source URL. Do not score or summarize the text inside the evidence tool.
- Upgrade LocalMobilityTool and WikivoyageClimateTool to expose this context contract without changing their provider request counts or existing climate-scoring behavior. The deferred PlaceContextTool remains a short non-scoring synopsis and is not part of this evidence contract.
- Require TransportAccessTool, ActivitiesTool, SafetyTool, and any later criterion-specific Wikivoyage consumer to reuse the same contract.
- Test full-section preservation, heading coverage, fair allocation under truncation, preview separation, chunk bounds, coverage metadata, LocalMobility persistence, climate compatibility, and unchanged provider-call topology.

### 8. TransportAccessTool — COMPLETE

Planned commit: `Transport access tool: separate destination arrival evidence`

- Rename the arrival-related AccessibilityTool to TransportAccessTool and define its scope as evidence about reaching the destination. It does not measure local mobility or disability accessibility.
- Return independent raw OSM counts for airports within 50 km and potential mainline rail stations, bus terminals, and ferry terminals within 10 km. Query nodes, ways, and relations and deduplicate by OSM element type and ID.
- Extract a shared coordinate-aware Open-Meteo origin resolver from TimezoneFitTool. Preserve its local timezone fast path, top-match visibility, 30-day cache, stale fallback, and existing timezone behavior; require provider coordinates for distance calculations and serialize duplicate uncached lookups so one origin is resolved once and reused across candidates.
- When both origin and destination coordinates are available, return the resolved origin identity and straight-line Haversine distance. Keep the calculation visible and never describe it as route distance or travel time.
- Return the canonical destination's bounded, revision-pinned Wikivoyage `Get in` section using the shared 20,000-character, heading-aware context contract. Persist OSM infrastructure, origin/distance, and Wikivoyage context as separately attributable evidence.
- Route origin, distance, remoteness, and arrival-access concerns here; stop using arrival infrastructure as proof of car-free living. Remove the obsolete `likely_car_dependent` output.
- Do not produce a fixed accessibility score. Mark the criterion explicitly unresolved until the future reasoning agent can assess the counts, distance, and contextual evidence together; none of these raw signals may hard-eliminate a candidate in this unit.
- Avoid claims about live routes, services, schedules, frequencies, prices, or travel times.
- Use one bounded Overpass request per candidate, one shared cached origin resolution, and bounded cache-first MediaWiki retrieval. Run the independent lookups concurrently under existing provider and application limits, without detached requests or unbounded routing/schedule fan-out.
- Test nodes/ways/relations and deduplication, rail exclusions, partial and malformed Overpass responses, origin top-match identity, distance calculation, shared origin reuse and stale fallback, Wikivoyage attribution/coverage, cache fallback, tool selection, unresolved evaluation, registry wiring, and preservation of TimezoneFitTool behavior.

### 9. ActivitiesTool — COMPLETE

Planned commit: `Activities tool: add category-specific activity evidence`

- Populate structured `activity_preferences` in both the real interpreter contract and deterministic mock. Normalize prompt aliases to culture, nightlife, parks, beaches, and hiking, exclude negated requests, and preserve unsupported requested activities as unresolved.
- Select ActivitiesTool whenever structured activity preferences exist. For vacation requests without explicit activities, use the bounded generic fallback of culture and parks rather than assuming beach access.
- Query nodes, ways, and relations with category-specific radii: 5 km for culture, nightlife, and parks; 10 km for beaches; and 20 km for hiking. Include hiking-route relations and merge all requested categories into one bounded Overpass request per candidate.
- Return independent, deduplicated counts and status for every supported requested category. A complete zero-count response remains an observed mapped count; failed, malformed, unsupported, or absent evidence remains missing rather than being converted to zero.
- Resolve the canonical Wikivoyage article and revision once, then retrieve bounded revision-pinned `See` and `Do` sections through the shared 20,000-character context contract. Keep each section and OSM counts separately attributable.
- Remove the aggregate activity count and fixed deterministic score. Mark activity scoring unresolved until the future reasoning agent can assess requested-category counts and contextual evidence together; raw signals cannot hard-eliminate a candidate in this unit.
- Run OSM and Wikivoyage retrieval concurrently with bounded sub-lookups, retain completed partial evidence, use cache-first behavior and stale fallback, and make no claims about live events, opening status, trail conditions, or activity quality.
- Test prompt inference, aliases and negation, explicit non-vacation selection, generic vacation fallback, unsupported activities, multi-radius query generation, nodes/ways/relations, hiking routes, deduplication, complete zero counts, partial/malformed responses, Wikivoyage multi-section revision reuse and attribution, caching, timeouts, unresolved evaluation, fakes, and registry wiring.

### 10. EducationOptionsTool — DROPPED

Planned commit: `Education options tool: remove redundant study matching`

- Remove EducationOptionsTool from the production registry, deterministic fake registry, study-purpose selection, criterion routing, evaluation, tests, and source tree. Do not retain a dormant or deferred implementation.
- Treat nearby study infrastructure as AmenitiesTool's responsibility: study requests already select independent university and library counts and score student life from those supported categories.
- Route both `education` and `student_life` research concerns to AmenitiesTool. Do not introduce Wikidata, university-site, or Wikivoyage `Learn` calls for a second overlapping education tool.
- Remove `study_field` from PlaceRequestProfile, the real interpreter contract, the deterministic mock, agent-info examples, and tests. Do not extract, store, or use an academic field for candidate generation or tool selection.
- Study-purpose requests must not ask a blocking clarification about an academic field. DigitalNomadAgent will recommend destinations from general study infrastructure and the user's ordinary constraints and preferences, not specialize recommendations by discipline.
- Make no claims about current programs, admissions, academic eligibility, or field availability from university/library proximity counts.
- Remove the obsolete five-city curated university directory and its deterministic `0.8`/`0.4` match heuristic.
- Test study selection without EducationOptionsTool, AmenitiesTool routing for education and student-life concerns, study requests without field clarification, profile-schema removal, registry/fake removal, unchanged public endpoint shapes, and the absence of education-program claims.

### 11. SafetyTool — COMPLETE

Planned commit: `Safety tool: add composite destination evidence`

- Add three visible components: 40% current FCDO advisory severity through the GOV.UK Content API; 35% latest available World Bank/UNODC intentional-homicide rate through the World Bank API; and 25% deterministic analysis of Wikivoyage's city-level Stay safe section through MediaWiki.
- Map the most severe active FCDO status transparently: no warning `1.0`, avoid all but essential
  travel to parts `0.75`, avoid all travel to parts `0.50`, avoid all but essential travel to the
  whole country `0.25`, and avoid all travel to the whole country `0.0`.
- Map homicide rates through documented piecewise thresholds: `<=1 -> 1.0`, `3 -> 0.85`, `6 -> 0.70`, `10 -> 0.55`, `20 -> 0.35`, `>20 -> 0.15`, with linear interpolation.
- Use a versioned, negation-aware phrase lexicon for Wikivoyage: limited reassurance credit, moderate petty-crime/scam/harassment penalties, and stronger violent-crime/kidnapping/avoid-area penalties.
- Renormalize when one component is missing, require at least two components for a score, cap confidence at medium with all three and low with two, and expose every component/date/excerpt.
- Route the `safety` criterion to this tool and never present the result as an objective universal city-safety rating.
- Fetch the three independent components concurrently within shared limits, with bounded retries and cache-first reuse; partial completion degrades confidence instead of delaying for unbounded recovery.

### 12. BudgetFitTool — COMPLETE

Planned commit: `Budget fit tool: replace local estimates with live cost context`

- Delete the dependency on `app/data/cost_estimates.csv`, the curated lower/upper monthly estimates, and the corresponding missing-production-data skip. Do not replace them with another hard-coded cost table.
- Use WhereNext's free, no-authentication, CC BY 4.0 city-price API as the primary source. Resolve candidates by normalized `(country_code, city_name)` against its cached coverage index, then retrieve typed local-currency and USD prices for available categories such as one-bedroom rent, groceries, utilities, internet, monthly transport, restaurants, and leisure. Preserve the dataset version, update date, declared upstream source, methodology URL, licence, and exact API source.
- When city-level data is unavailable, fall back to WhereNext's cached 95-country cost dataset and expose its monthly USD estimate and category indices strictly as low-confidence country context. Never present a country fallback as city-specific evidence, and never turn absent coverage into a positive affordability result.
- Convert the user's stated budget to USD through the keyless Frankfurter API when necessary, retaining the original value, converted value, rate, and rate date. Coalesce concurrent identical FX requests across candidates, use one-day FX caching, and treat unavailable conversion as unresolved rather than comparing currencies.
- Return the complete typed city-price basket rather than hiding it behind one aggregate. When the necessary monthly items exist, also expose transparent centre and outside-centre fixed-cost scenarios consisting only of one-bedroom rent, utilities, internet, and monthly transit, plus the budget remaining after those named items. Do not estimate food consumption, deposits, healthcare, discretionary spending, shared housing, or total monthly cost.
- Remove the current deterministic `cost` score and hard-budget pass/fail based on local estimates. The future reasoning agent will assess the visible basket, fixed-cost scenarios, user duration/purpose, and country fallback. Incomplete or stale evidence cannot satisfy a hard budget constraint or positively eliminate uncertainty.
- Keep provider usage fast and bounded: one cached city-coverage lookup, at most one detailed city request per covered candidate, one shared country-dataset request for all uncovered candidates, and at most one coalesced FX pair request per user budget currency. Cache versioned cost data for 30 days, retain stale fallback, and use no retry chain beyond the shared HTTP policy.
- Do not use Wikivoyage in the initial BudgetFitTool implementation. Its `Eat`, `Sleep`, and `Get around` sections are travel-oriented, inconsistently dated, often delegate to district articles, and do not cover a stable monthly basket. Reconsider it later only if real evaluation shows that its context materially improves uncovered destinations.
- Cap WhereNext city evidence at medium confidence because it is a third-party researched/modelled dataset with detailed coverage for only 57 cities; cap the country fallback at low. Do not use Numbeo scraping or its paid/keyed API, and do not substitute a broad purchasing-power score for direct city prices.
- Test city identity and aliases, coverage-index reuse, every returned category, missing items, fixed-cost scenario composition, country fallback labelling, dataset metadata and attribution, FX coalescing/conversion/failure, cache/stale fallback, partial failures, timeouts, source persistence, unresolved scoring, hard-budget behavior, fakes, registry wiring, and removal of the local-data skip.

### 13. OfficialSourceTool — DROPPED (removal landed)

Planned commit: `Official source tool: remove unused curated link lookup`

- Delete OfficialSourceTool and remove it from the production registry, deterministic fake registry, cache configuration, nationality/visa-based selection, tests, README, and source tree. Remove the corresponding missing-production-data skip; do not retain a dormant implementation or placeholder dataset.
- Keep `nationality` in PlaceRequestProfile as user context, but do not use it to infer or claim visa eligibility, entry rights, immigration requirements, or legal conclusions.
- Remove deterministic candidate claims such as "visa-friendly" that cannot be verified after the tool is removed. Preserve the general final-response warning that users must verify entry and legal requirements with the relevant authorities.
- Do not replace the tool with automatic government-site discovery or generic web search. Such links do not determine personal eligibility and would add latency without supporting destination scoring.
- Test registry/fake removal, selection without the tool, unchanged profile and public endpoint shapes, removal of the production-data skip, preservation of the verification warning, and absence of unverified visa or entry claims.

## Per-commit verification

- Focused unit/contract tests use recorded or injected responses and never access the network.
- Full `pytest -q` remains green. The OfficialSourceTool removal has landed; the final steady
  state has only the live SLA check skipped by default.
- `ruff check .` passes.
- Applicable live smoke checks run serially against known destinations and use only free APIs.
- Every selected criterion maps to a real tool or is explicitly unresolved.
- Missing or failed evidence cannot produce a positive hard-constraint result.
- Tool errors, source dates, staleness, and confidence reach Evidence Memory and final rendering.
- Deadline tests use intentionally tiny configured timeouts to prove active work is cancelled quickly, completed evidence and trace entries are retained, and a disclosed best-effort recommendation still reaches the user.
- Timed representative smoke runs must finish below 300 seconds; any unit that changes external-call topology documents its measured time and conservative worst-case bound.
- Runtime telemetry must record tool queue/run time and outcome, agent-phase time, total time, timeout counts, and fallback use. Offline representative requests must retain a large margin; the opt-in real-provider SLA check targets p95 below 240 seconds and is never run without `RUN_LIVE_TESTS=1`.

## Defaults and boundaries

- Do not invoke a real LLM or LLMod during this sequence.
- Use no paid or API-keyed sources.
- Keep public FastAPI endpoint shapes unchanged.
- Never raise `AGENT_EXECUTION_TIMEOUT_SECONDS` above 285 or any client wait above 295 seconds; lower deployment-specific limits are allowed.
- No tool, retry helper, fallback, or future plan may reset, bypass, or extend the single end-to-end deadline.
- Timeouts are graceful-degradation events after candidate generation: they must preserve completed evidence and return a provisional recommendation, not discard the run solely because one provider is slow.
- Any deployed proxy/load balancer request or idle timeout must exceed the 285-second backend deadline; declare the real value with `UPSTREAM_REQUEST_TIMEOUT_SECONDS` for startup validation and configure the external platform itself.
- Disability accessibility is outside these transport tools; they cover reaching a destination and moving locally.
- For a tool whose criterion has a directly relevant Wikivoyage section, return its structured evidence plus a revision-pinned short preview, heading-aware reasoning chunks, and explicit coverage/truncation metadata as separately attributable evidence. Preserve up to 20,000 cleaned characters per section and distribute a truncated budget across subsections instead of keeping only the beginning. Evidence tools do not translate community-written prose into scores. The future reasoning agent will compare the sources and explain its aggregate judgment; until that backbone exists, the contextual evidence remains visible but numerically unresolved and cannot cause hard elimination.
- Change the order only through an approved master-plan revision.
- Each tool is implemented in one commit; the shared foundation and the explicitly approved shared Wikivoyage context-coverage follow-up are the only approved non-tool commits.

## Status: tool-implementation phase complete

Every unit above has landed (including OfficialSourceTool's removal). This document's own scope
boundary above ("Do not invoke a real LLM or LLMod during this sequence") and line 258's "until
that backbone exists, the contextual evidence remains visible but numerically unresolved" are now
intentionally superseded, not violated: the tool-implementation phase this document governs is
finished, and a new phase has begun implementing exactly the "future reasoning agent" this document
anticipated -- a single batched Dynamic Evaluation LLM call that scores cost/transportation/
accessibility/activities for every finalist and can now produce real hard-constraint eliminations.
See `ARCHITECTURE.md` §1/§4 and `app/agent/dynamic_evaluation.py` for the current, load-bearing
description of that phase; this document remains the historical record of how the tool suite itself
was built and verified, not a live description of current scoring behavior.
