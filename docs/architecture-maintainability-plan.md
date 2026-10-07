# IACS maintainability remediation programme

## 1. Outcome, decisions, and working model

Resolve the verified findings in the [architecture review](architecture-maintainability-review.md) through separately reviewable implementation stages. Preserve durable decisions, current authorization, command uncertainty, recovery behavior, and frontend request/draft lifetimes.

Astra Ultra approved the proposed plan. That approval covers the plan only. Every implementation stage and the final integrated result require separate Astra approval. The [closure matrix](architecture-maintainability-closure.md) records implementation and review status; a prepared patch is not a completed stage.

### Model responsibilities

- Implementation: GPT-6.1 Sol, reasoning effort Ultra, performs all code, test, migration, and documentation changes.
- Advisor and reviewer: GPT-6 Astra, reasoning effort Ultra, reviews consequential designs before implementation and independently reviews completed changes.
- Use at most two Sol implementation agents concurrently, with disjoint ownership and isolated checkouts. Integrate their work sequentially.
- Do not substitute another model silently. If either required model is unavailable, retain the current checkpoint and report the blocker.
- Astra identifies problems and advises; Sol implements the corrections. Sol cannot approve its own work.

### Agreed behavior

| Area | Decision |
| --- | --- |
| Top Charts | Notify the final leader after a burst; intermediate overtakes may be coalesced. |
| Optional access work | Durable processing with fixed deadlines, visible expiry, and conservative recovery. |
| PDF exports | Keep synchronous exports; admit one complete export pipeline per backend process. |
| PDF limits | Maximum 10,000 output activity rows, 25 MiB embedded image data, and a 120-second pipeline deadline. Reject oversized work clearly; never truncate silently. |
| Calendar sync | Commit successful accounts independently and report partial or interrupted completion. |
| External API consumers | Treat them as unknown. Preserve existing public behavior where migration would require consumer evidence. |
| New product features | The five audit exercises become conformance tests and implementation walkthroughs; they do not add a production provider, action, or feature. |
| Deployment | Implementation and isolated validation only. Deployment, production migrations, and live provider/hardware tests remain separately authorized operations. |

The public population-sized presence list is an explicit compatibility exception: internal consumers will become bounded, but the report must not claim that every external presence read is bounded.

## 2. Ordered implementation stages

### P0 — Establish the implementation baseline and lasting safeguards

Addresses AM-10 and programme traceability.

- Start from the actual reviewed source: `main`, HEAD `65e1b6d070e6327314ca9bd72c01264496b821fb`. Recheck local changes before starting.
- Use an isolated checkout on `codex/iacs-maintainability-programme`; preserve the existing uncommitted audit document.
- Save this plan and a closure matrix mapping AM-01–AM-13 and every simplification candidate to its implementation, evidence, Astra approval, or justified retention.
- Consolidate checked-body and strict type targets into one manifest. Derive their respective commands from it; preserve existing checking flags.
- Cover new cohesive owners by package. Add negative tests that fail when protected modules or required validation suites escape classification.
- Retain performance results as JSON artifacts, including complete query plans and source/dependency identities, rather than relying on captured test output.

Acceptance: adding a module inside a covered owner requires no second filename registration; omitted coverage fails automatically; benchmark artifacts survive successful validation.

### P1 — Repair the reproduced defects

Addresses immediate AM-02 and AM-09 remediation. Deliver as two independent patches.

Frontend invalidation:

- Correct People, Vehicles, and Events/history invalidation.
- Ensure an unrelated shell fetch failure cannot suppress the feature's refresh signal.
- Add composed tests using the actual shell, coordinator, and feature hooks.

Runtime PDF packaging:

- Install locked Playwright's Chromium and required native libraries in shared runtime ancestry.
- Keep browser files readable by the runtime user.
- Add a final-image smoke using the image's own installed code, environment, and actual report template.
- Run it as the runtime user, with networking disabled and without source or development-environment overlays.

Acceptance: committed changes become visible in the active feature; a synthetic PDF renders successfully from the final runtime image. Retain the image identity, PDF artifact, build duration, and image size.

### P2 — Consolidate workflow and notification contracts

Addresses AM-01 and relevant AM-10 safeguards.

- Introduce pure, closed action definitions owning configuration models, defaults, execution mode, effect classification, maintenance behavior, uncertainty continuation policy, and editor identity.
- Keep handler composition static and exhaustive.
- Transactional handlers use the caller's transaction and do not commit or perform external I/O. External handlers execute through existing durable attempt and effect owners.
- Generate static TypeScript configuration types and defaults from backend definitions using a narrow deterministic exporter. Unsupported schema constructs fail generation.
- Keep explicit typed editor controls; do not introduce a generic form engine.
- Include notification-channel configuration, defaults, rendering, and dispatch parity. Retain separate automation and notification run stores.
- Migrate one action family at a time, deleting its previous normalization/classification branches in the same patch.

Preserve valid action strings, stored configurations, frozen targets, and attempted identities. Reject invalid new writes; keep historical records readable and hold unsupported unfinished plans for review. Do not reinterpret attempted work.

Acceptance: the representative new-action fixture needs its definition, handler, editor control, domain reservation, and tests, without action-specific branches in dispatchers, stores, or facades. Missing handler/configuration/editor coverage fails checks.

### P3 — Complete frontend feature ownership

Addresses AM-02 and report subject-search simplification.

- Move directory queries first, then Events/history, report dependencies, and dashboard inputs.
- Each feature owns its query results, errors, cursor/filter state, selected-ID hydration, and invalidation rules.
- Retain the existing refresh coordinator, five-second realtime coalescing, and 150 ms search debounce.
- Remove each migrated shell collection, setter, fetch, navigation requirement, and seed prop together.
- Replace Reports' indirect dependencies on shell arrays with an explicit preview revision.
- Replace the first-500 visitor-pass preload with bounded server search and selected-detail hydration. Remove secondary client filtering that hides valid server matches.
- Preserve account changes, cancellation, stale-result protection, dirty drafts, and fresh selected metadata.

Acceptance:

- One authoritative request per displayed page, plus justified selected-ID reads.
- Relevant events received during one blocked refresh produce the current request and at most one trailing refresh.
- Page-two history, filters, off-page selections, and dirty editors remain correct.
- Adding a feature read does not require adding feature policy to shell data management.

### P4 — Finish provider and integration-editor boundaries

Addresses AM-03 and AM-04. Backend and frontend portions may be reviewed separately.

Provider configuration:

- Give each provider a pure binding policy owning validation, configured predicates, physical identity, fingerprint inputs, and affected setting keys.
- Keep locking, frozen targets, authorization, provider ordering, and command dispatch in their existing domain owners.
- Preserve HA→ESPHome ordering, public setting keys, secret blank/clear behavior, and exact existing fingerprint inputs.
- Use a closed frontend provider descriptor for discovery, selection, binding conversion, rendering, and saving.

Editor lifetimes:

- Make the shared modal resolve a keyed provider editor.
- Each editor owns its complete draft, baseline, validation, reads, mutations, pending state, and feedback.
- Editors render a shared dialog frame with explicit dirty/busy/save state.
- Keep the generic scalar-settings editor.
- Migrate Apprise first, followed by the other managed providers, removing corresponding parent state and effects during each migration.
- Remove artificial progress delays and the nonfunctional OAuth placeholder.
- Preserve accepted-operation receipts; closing an editor must not imply that an accepted external action was cancelled.

Acceptance: an injected third-provider fixture works through discovery, configuration, and persistence without provider branches in the command owner or shared modal. Every dismissal path protects child drafts and pending operations.

### P5 — Remove proven obsolete code and repair ownership leaks

Addresses AM-11, AM-13, and the simplification inventory.

- Remove the unused pure-plan gate-command field while retaining authoritative execution, journal, direction, and recovery evidence.
- Remove the no-op automation subscription and obsolete notification fallback.
- Remove empty retired catalog debris.
- Replace internal compatibility wrappers only after tests use the actual required ports.
- Move vehicle-information invalidation into its feature store as an in-session operation. Directory mutations retain actor checks, audit, and commit ownership.
- Retain public cover aliases, uncertain external configuration options/scripts, vendor fallback branches lacking sufficient evidence, historical migrations, upgrade fixtures, audit interpretation, and required dependencies.
- Record a reason for every retained candidate.

Acceptance: extending vehicle information requires no parallel reset list in directory mutations; removed paths have no supported consumer; historical records remain readable.

### P6 — Replace the inline optional access tail and bound durable intake

Addresses AM-05 and the access-path portion of AM-06. This is the highest-risk stage. Astra must approve the concrete schema and state-transition design before implementation.

Durable intake and debounce:

- Persist the original gate observation and timestamps before making an ingest record runnable.
- Duplicate intake and failed retries must not overwrite that original evidence or refresh recognition authority.
- Use durable debounce-window records and indexed ingest membership.
- Process reads and open-window summaries in pages of 50. Preserve original ordering, fuzzy matching, ranking, tie behavior, merge order, and finalization conditions.
- Store compact first/best/anchor evidence, counts, timestamps, lifecycle identity, and outcome links.
- Replace complete read lists across access/admission/movement handoffs with typed compact window evidence and durable references.
- Perform full-evidence decisions through indexed reads or bounded streaming; never base authority or recovery on a truncated preview.
- Store one bounded new evidence format: the first 50 candidates, exact total count, evidence version, durable window reference, and pagination metadata.
- Add an authenticated, sanitized cursor read for complete candidate evidence. Preserve historical decoding of existing records.
- Commit event/saga changes, required intents, member terminalization, and outcome links together through existing owners.

No early finalization, acknowledged-read loss, or fresh timestamp substitution is permitted to satisfy a memory bound.

Optional completion:

- Reserve work atomically with its originating decision; release readiness only after the appropriate execution/admission checkpoint.
- Store immutable origins and deadlines. Repeated wakes and lease recovery cannot extend them.
- Preserve the separate vehicle worker: two active jobs, claim page of 32, 120-second lease, and 90-second processing timeout clipped to remaining deadline.
- Use a five-minute origin-based deadline for optional completion. This is intentionally stricter than extending time after delayed enqueue.
- Handle late visitor-admission linkage idempotently without repeating provider work or hardware.
- Live-camera capture must complete within five seconds of original receipt. Afterwards, use only an exact event-bound thumbnail within the completion deadline.
- Preserve the visual recorder's 45-second event-matching tolerance.
- Reconcile HA presence against the latest committed desired state and current binding, ordered by physical target as well as person. Preserve uncertain outcomes; do not replay old events blindly.
- Publish initial committed state independently of enrichment, followed by typed updates as enrichment completes.
- Apply the same completion ownership to the optional tail following externally observed gate admission.

Coalesced Top Charts:

- Use a two-second quiet window and ten-second maximum batching wait.
- Maintain one durable dirty generation, one evaluator, and at most one trailing generation.
- Release claim/source locks before aggregation.
- Commit changed leader state and a deterministic notification intent together.
- Acknowledge only the claimed generation.
- Historical refreshes are silent. Live notification eligibility expires against its original deadline; already attempted effects remain reconcilable.

Acceptance: blocked enrichment for event A does not delay event B's core decision; retained memory is bounded; duplicate claims, restart, and late linkage preserve decisions and required outcomes; no historical repair replays hardware.

### P7 — Apply compact-query discipline to remaining reads

Addresses remaining AM-06 and AM-12.

- Project only required columns for charts, presence, and forecasting.
- Fetch latest snapshots only for selected ranked identities.
- Batch attribution and preserve recorded identity before current single-assignee fallback.
- Give internal presence consumers bounded summary and selected-ID reads.
- Preserve the existing public list under the agreed compatibility exception, while removing unnecessary blob/entity materialization.
- Keep forecast semantics and its existing history horizon; do not claim constant memory when required event volume grows.
- Do not add a count projection, authorization cache, or prediction cache in this programme. Coalescing and compact reads address the demonstrated problems without another persisted representation.

Acceptance: equivalent rankings and forecasts; no unused photo/raw-payload loading; bounded internal responses; retained measurements clearly disclose remaining full-history aggregation and population-sized compatibility reads.

### P8 — Bound background resources and shorten slow-operation transactions

Addresses AM-07 and AM-08. Deliver as separate owner-focused patches.

Event delivery and diagnostics:

- Replace per-event listener task creation with filtered inline listeners, invoked outside bus and domain locks.
- Remove no-op subscriptions; keep notification events as durable-dispatcher wakeups.
- Await required local gate-admission handling; move optional work to P6.
- Replace malfunction evaluation tasks with a coalesced wake on its existing scheduler, retaining the 15-second fallback. Clear the wake before evaluation.
- Preserve listener exception isolation and local/all-worker scope.
- Persist provider outcomes before observer dispatch; observer failures or delays must not reclassify an accepted command.
- Preserve durable pending/outcome evidence when required handling fails.
- Retain existing WebSocket and Redis bounds.
- Migrate all synchronous audit scheduling to explicitly awaited persistence, using the owning transaction where atomicity is required.
- Diagnostics use one writer, batches of at most 100, and bounded admission of 1,024 records/16 MiB. Drop new diagnostics on overflow with accurate counters. Mandatory audit never uses this lossy path.
- Stop producers before draining dispatch and diagnostics. Allow ten seconds for diagnostic drain, then count diagnostic-only abandonment. Dispose the database last.

UniFi callbacks and probes:

- Convert synchronous callbacks into compact typed work, without retaining arbitrary coroutines or SDK objects.
- Use a 256-record/16 MiB callback inbox with one consumer; four fixed probe workers and 64 pending probe IDs.
- Bound finished-ID and update-key retention to 4,096 entries with a ten-minute TTL.
- Coalesce status updates; expose capacity, oldest age, drops, and stream gaps.
- Overflow invalidates affected cached evidence immediately. Missing observations must not become evidence of absence or current authorization.
- Preserve durable webhook intake.
- Use immutable 90-second probe deadlines and original observation timestamps; expired or obsolete-generation results cannot revive evidence.
- Stop admission and clean up actual workers/client operations during shutdown.

These capacities are engineering defaults, validated with fixtures rather than presented as production sizing measurements.

Reports:

- Acquire export capacity before materializing the snapshot.
- Check all output activity rows—including requested denied rows—and image budgets before unbounded materialization.
- End the source-read transaction before rendering.
- Enforce the agreed limits and whole-pipeline deadline, returning clear retryable busy, size-limit, or timeout errors.
- Use privately owned UUID artifact paths independent of the public report number.
- Recheck current actor authorization before committing the export and audit.
- Handle number collisions and failures without overwriting or deleting another export.
- Release capacity only after underlying work and owned-resource cleanup finish.

Calendar:

- Keep the existing synchronization horizon and result structure.
- Fetch outside database transactions.
- Commit each account's pass changes, required intents, audit, and checkpoint atomically.
- Fence application against account removal, reauthentication, configuration changes, and overlapping syncs.
- Retain completed accounts when a run is interrupted; retries must not repeat completed checkpoints.
- Never cancel missing passes from incomplete fetch results.
- Give SDK calls finite transport timeouts and retain capacity until the underlying thread actually finishes. Cancellation must reject late results.

### P9 — Complete integrated verification and handoff

- Reconcile every AM finding and simplification candidate with its final disposition.
- Run the five extension exercises against the final structure, using fixtures and temporary implementations rather than shipping additional features.
- Update architecture, development, integration, and validation guidance.
- Remove temporary migration paths, duplicate writers, obsolete registrations, and transitional shell ownership.
- Produce the final implementation report with exact source identity, checks, benchmarks, migration compatibility, limitations, and deployment status.
- Obtain a fresh Astra Ultra review of the complete integrated candidate.

Acceptance: all in-scope changes pass their gates; every retained exception is explicit; no unresolved actionable review finding or unreviewed final edit remains.

## 3. Dependencies, contracts, and migration policy

The default sequence is P0 → P1 → P2/P3 → P4 → P5 → P6 → P7 → P8 → P9.

P2 and P3 may run concurrently. Independent P4 work and P8 report/calendar work may start earlier after their prerequisites, provided writers do not overlap. Event-delivery changes depend on the P5 cleanup and P6 optional-work separation.

A single Sol owner coordinates schema migrations to avoid competing migration heads.

Important interface changes are:

- Typed workflow definitions and generated frontend configuration contracts.
- Pure provider binding policies and closed frontend descriptors.
- Feature-owned query revisions and invalidation contracts.
- Durable window evidence, membership, readiness, leases, and completion outcomes.
- Bounded internal presence reads and sanitized candidate-evidence pagination.
- Explicit report busy/limit/timeout responses.
- Per-account calendar checkpoints and interrupted/partial completion handling.

Preserve existing valid workflow payloads, provider fingerprints, historical records, and public behavior covered by the compatibility exception.

Schema-changing rollback must name a supported source/image/schema combination. “Additive migration” does not imply that the previous image is compatible. Refuse incompatible downgrade when durable pending or attempted records exist; preserve them under recovery hold and use a compatible rollback or fix-forward.

## 4. Verification and evidence

Every stage uses focused tests during development, followed by its required completion gate.

- Frontend: production build and unit tests, plus affected Chromium/WebKit fixture suites for interactions, navigation, roles, focus, responsiveness, and draft lifetimes.
- Backend/schema/cross-system: full isolated validation harness with explicit dependency reuse or download mode.
- Harness changes: source-snapshot and harness-configuration tests.
- Packaging: actual final-runtime-image PDF smoke.
- Documentation: links, source references, commands, and whitespace checks.

Required scenarios include:

- Missing workflow registrations, malformed configuration, historical frozen plans, and authorization changes.
- Realtime bursts, failed unrelated reads, stale responses, account changes, off-page selections, and dirty drafts.
- Provider fingerprint parity, configuration races, editor dismissal, and late accepted-operation results.
- Crash points around reservation, readiness, claim, commit, admission linkage, and notification reservation.
- Duplicate intake, equal timestamps, merged windows, restart with changed gate state, and backlog beyond memory capacity.
- Unknown/accepted effects, expiry of unattempted work, and recovery without replay.
- Nested event publication, listener database failure, blocked listeners, and shutdown ordering.
- UniFi overflow, cache expiry, stream-gap invalidation, stale probe results, and cancellation.
- Export limits at the boundary and one unit beyond it, collision-safe cleanup, concurrent exports, and actor revocation.
- Calendar partial failure, account mutation during fetch, interruption, retry, and late SDK completion.

Retain equivalent-output comparisons and small/target-scale evidence: 20/2,000-input background probes; 20/2,000-person forecasting fixtures at varied event densities; 10,000/1,000,000-event chart workloads; and maximum-size report fixtures.

Record query plans, scanned/returned rows, query counts, payloads, allocations, repeated timings, workload definitions, and environment identities. Do not infer production speedups or database memory from narrower synthetic measurements.

## 5. Mandatory Astra sign-off protocol

For every stage:

1. Sol prepares the implementation, tests, documentation, and evidence.
2. Record checkout, branch, commit, patch identity, dependency identities, and a source manifest including relevant new files.
3. Astra reviews the actual changes and evidence against the stage's acceptance criteria.
4. Sol fixes every actionable finding and reruns affected checks.
5. Astra reviews the revised candidate and explicitly approves it.

A stage cannot complete with failed or unattempted required checks, unresolved actionable findings, or changes made after its reviewed snapshot. Justified retention requires an explicit rationale and Astra approval; it is not silently relabeled as a fix.

After integration, run the complete release-candidate validation and obtain fresh Astra Ultra sign-off on the exact final candidate. Only then is implementation complete. Deployment, production migration, and live-effect approval remain separate.
