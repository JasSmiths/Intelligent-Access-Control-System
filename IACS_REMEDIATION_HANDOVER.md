Two remaining integration-edit defects are fixed in the local IACS files. Concurrent ESPHome, Apprise and Home Assistant discovery edits now apply to fresh configuration inside the existing settings transaction, so two successful additions preserve both. Apprise deletion also binds approval to the exact destination and list revision: if another screen removes an earlier entry, the stale request reports a conflict instead of removing the wrong destination.

All earlier uncommitted remediation is preserved. The real API/PostgreSQL regression inventory went from **16 failed / 7 passed** on the captured starting application to **23 passed**. The final isolated harness, affected browser checks and runtime checks pass. Changes remain local and uncommitted; production was not migrated or deployed, live containers were not restarted, and no real providers or hardware were contacted. Deploy the matching backend/frontend together later under separate authorization; refresh old Apprise editors and confirm stale deletions again.

## Focused follow-up technical appendix (V01/V02)

### Identity, scope and inspectable artifacts

- Workspace: `/Users/jas/Documents/Intelligent Access System`. Starting and final branch `main`, HEAD `abec96d37b2d60a2d5f403f441cef21619432b1a`. No branch/worktree switch, staging, commit or remote operation.
- Follow-up starting **actual dirty tree**: `a773364a0933db096464143bb8941f87e3dc5a3874f73e03ea68a18b47ed3d1f` (812 safe source files). Final tested source: **`1bc3838659160f422bad2e5800473da993188f4746f0c1e338c11beaa396a557`** (815 files). HEAD alone does not identify either tree.
- Fingerprint algorithm: SHA-256 of sorted relative path, NUL, per-file SHA-256, newline. Safe manifests exclude secrets, production state, dependencies and caches. The root delivery documents were written after code validation and have separate bundle hashes.
- Local evidence folder: `/private/tmp/iacs-integration-mutations-20261014`. Portable paths in this document refer to members inside [the review bundle](</Users/jas/Documents/Intelligent Access System/IACS_REMEDIATION_REVIEW_BUNDLE.zip>). The earlier bundle's contents are under `prior-remediation/`; its source patch is historical. The top `task-only.patch` contains **only this follow-up**, compared with captured starting bytes, including the two root delivery documents. The earlier Git-dirty work is not attributed to this delta.
- [Machine-readable evidence](</Users/jas/Documents/Intelligent Access System/IACS_REMEDIATION_EVIDENCE.json>); `followup-change-manifest.json` lists every task modification/new file; `evidence/preexisting-context.json` records the pre-existing dirty status and prior artifact hashes. No follow-up source was deleted. Three selected source/test files are new; the remaining 17 are modifications. Generated workflow contracts, migrations and dependency manifests have no follow-up delta.

| Category | Files | Added / deleted lines | New / deleted files |
| --- | --- | --- | --- |
| production_code | 10 | +302 / −283 | 2 / 0 |
| documentation | 2 | +20 / −0 | 0 / 0 |
| tests | 6 | +436 / −15 | 1 / 0 |
| validation_configuration | 2 | +3 / −0 | 0 / 0 |

These statistics cover selected source/docs/test/harness files only. The two modified delivery documents and regenerated archive are counted separately in JSON; old remediation line totals below are historical.

### Findings and corrections

| ID | Status | Root cause and correction | Before / after |
| --- | --- | --- | --- |
| V01 | Fixed, tested, scoped independent review completed | Cached read→replacement-list assembly occurred before save admission. Explicit typed ESPHome/Apprise/cover-discovery operations now run after the existing current-actor, target/configuration and settings locks, against fresh decrypted values. Canonical validation, required atomic audit, encryption and commit remain owned by settings. The coordinator owns the admitted persistence→activation lifetime. Accepted sanitized representations are constructed before commit and returned in the saved receipt. | Concurrent add/add (including two coordinators), add/remove, independent field edits, Apprise edits and post-discovery merge failures reproduced on starting code; final API/PostgreSQL suite 23 passes. |
| V02 | Fixed, tested, scoped independent review completed | An index-only approval could remove a different entry after a shift. Required keyed opaque destination and ordered-list revision fingerprints now bind approval payload and DELETE body. The same locked operation validates both, then removes exactly one occurrence. Any change to order/multiplicity makes old approval conflict 409; obsolete index-only request 422. Public previews use service/ordinal, without credential prefixes. Frontend confirmation, typed API, panel and fixtures migrated together; UI consumes accepted URLs. | Real preceding deletion/reorder/duplicate/stale/concurrent tests reproduce the old defect or absent new binding; all 23 API/storage cases and 58 affected browser cases pass. |

Current owners and exact symbols:

- [backend/app/services/settings.py:673](</Users/jas/Documents/Intelligent Access System/backend/app/services/settings.py:673>) (`update_settings`)
- [backend/app/services/settings_activation.py:39](</Users/jas/Documents/Intelligent Access System/backend/app/services/settings_activation.py:39>) (`SettingsActivationCoordinator`)
- [backend/app/services/integration_settings.py:80](</Users/jas/Documents/Intelligent Access System/backend/app/services/integration_settings.py:80>) (`apply_integration_edit`)
- [backend/app/api/v1/integrations.py:89](</Users/jas/Documents/Intelligent Access System/backend/app/api/v1/integrations.py:89>) (`update_integration_settings`)
- [backend/app/services/integration_settings.py:72](</Users/jas/Documents/Intelligent Access System/backend/app/services/integration_settings.py:72>) (`apprise_destinations`)
- [backend/app/api/v1/integrations.py:460](</Users/jas/Documents/Intelligent Access System/backend/app/api/v1/integrations.py:460>) (`remove_apprise_url`)
- [frontend/src/api/integrations.ts:198](</Users/jas/Documents/Intelligent Access System/frontend/src/api/integrations.ts:198>) (`removeAppriseUrl`)

`backend/app/modules/notifications/apprise_urls.py` owns pure URL normalization/safe summaries; vendor parsing/sending remains in `apprise_client.py`. Moving these helpers prevents a settings/vendor dependency cycle and preserves notification recipient ID vocabulary. No compatibility alias, index-only bypass, versioning platform or generic mutation framework was introduced. `update_integration_settings` no longer accepts unused before/after arguments; callers no longer construct discarded summaries.

HA discovery performs provider I/O before settings locks; only collected entity facts enter the locked merge. Existing manual entity fields and the current default open service are preserved. No allow decision or provider observation is cached as authority. Existing settings activation/no-op/partial failure/cancellation/queued cancellation/shutdown tests remain intact. Explicit null device host still rejects 400; omitted encryption key is preserved and an explicit empty key clears, matching the starting route behavior.

Ownership before: route cached read + whole list + unused summaries → coordinator serializes save/activation. Ownership after: route intent/discovery facts → explicit integration operation applied under fresh settings transaction → validation/audit/commit/accepted representation → existing cancellation-aware activation → UI accepted receipt. To extend an existing compound integration edit, add a concrete typed operation to this owner, apply it against locked current values, return its safe accepted representation and add route/storage concurrency tests. A provider remains responsible only for discovery/I/O.

### Exact regression inventory and retained failures

The same final 23-case API test file was overlaid on the immutable starting source for the paired baseline. Application code remained byte-identical to the starting snapshot. Tests use real FastAPI routes, bearer authentication, confirmation API, PostgreSQL, audit and persistence owners; provider discovery/restarts alone are inert. The isolation guard rejects other sockets/HTTP, application workers/lifespan are not started. Route-entry barriers expose the earlier read-before-save race, rather than merely enqueueing prebuilt coordinator payloads.

| Test (parameter cases are separate) | Starting application | Final application |
| --- | --- | --- |
| `test_two_real_add_routes_preserve_both_additions[False]` | failed | passed |
| `test_two_real_add_routes_preserve_both_additions[True]` | failed | passed |
| `test_add_after_concurrent_remove_cannot_resurrect_endpoint` | failed | passed |
| `test_independent_device_patches_preserve_both_accepted_fields` | failed | passed |
| `test_concurrent_apprise_additions_preserve_both` | failed | passed |
| `test_preceding_item_deletion_never_substitutes_another_target` | failed | passed |
| `test_reordering_or_duplicate_change_conflicts_with_stale_approval[replacement0]` | failed | passed |
| `test_reordering_or_duplicate_change_conflicts_with_stale_approval[replacement1]` | failed | passed |
| `test_fresh_duplicate_selection_removes_one_and_old_approval_conflicts` | failed | passed |
| `test_index_only_delete_is_not_an_approval_bypass` | failed | passed |
| `test_discovery_merges_fresh_configuration_after_external_work[gates]` | passed | passed |
| `test_discovery_merges_fresh_configuration_after_external_work[garage-doors]` | passed | passed |
| `test_required_settings_audit_failure_rolls_back_compound_edit` | passed | passed |
| `test_public_apprise_metadata_and_audit_do_not_include_credentials` | failed | passed |
| `test_discovery_merge_uses_current_settings_at_save_boundary[gates]` | failed | passed |
| `test_discovery_merge_uses_current_settings_at_save_boundary[garage-doors]` | failed | passed |
| `test_concurrent_apprise_addition_conflicts_with_approved_delete` | failed | passed |
| `test_apprise_add_after_delete_does_not_restore_removed_destination` | failed | passed |
| `test_confirmation_is_bound_to_exact_opaque_target_and_cannot_be_replayed` | passed | passed |
| `test_current_actor_is_revalidated_after_route_confirmation` | failed | passed |
| `test_compound_edit_cancellation_drains_activation_before_shutdown` | passed | passed |
| `test_duplicate_add_is_authoritative_noop_with_confirmation_and_no_restart` | passed | passed |
| `test_explicit_null_host_remains_invalid_and_secret_omission_and_clear_are_distinct` | passed | passed |

`evidence/paired-regression-results.json` includes the exact baseline assertion messages. The actor-revalidation case was denied by the old service with 400 and now returns 403; it was not an authorization bypass. Some V02 cases fail on the baseline because opaque binding fields did not exist; the preceding-deletion and concurrent mutation cases demonstrate the underlying behavior. Setup failures were not counted as these sixteen behavioral/contract failures.

Earlier attempts are retained in `evidence/retained-runs.json` with command/result/log evidence: an unavailable old image, correctly rejected mismatched dependency cache, initial fixture teardown guard failures, and a test expecting 403 for an already-consumed confirmation instead of the existing 409. The assertion was corrected; confirmation/audit guards were preserved. Three earlier full attempts were interrupted for relevant source/test corrections or whitespace cleanup; none is claimed as a complete pass. The final run below is the completion evidence. No previous Python test functions or test files were removed; the new suite adds 23 cases, one stale-delete browser scenario was added, and existing response fixtures were migrated.

### Final validation

| Check | Exit | Actual result | Portable evidence |
| --- | --- | --- | --- |
| Paired starting-code API/PostgreSQL regression | 1 | 16 failed / 7 passed; expected behavioral baseline, zero setup errors | `evidence/iacs-validation-focus-_16peat7/suite-001.log` |
| Focused after-fix real API/storage and activation | 0 | 23 API/PostgreSQL + 20 settings PostgreSQL + 14 activation tests passed | `evidence/focused-compatibility-console.log` |
| Full final isolated harness | 0 | 1565 backend passed / 35 persistence-only skips; frontend 61 files / 367 tests; 60 suites / 1134 cases; all checks passed | `evidence/iacs-validation-c1ylh8yt/results.json` |
| Affected Chromium and WebKit browser checks | 0 | 58 passed; no backend proxy, strict inert API mocks, unchanged 30-second test timeout | `evidence/browser/console.log` |
| Final runtime build and PDF | 0 | Passed; non-root, network none, no installed-source or dependency overlays | `evidence/runtime-accepted/iacs-runtime-pdf-1r5mfym4/result.json` |
| Final installed runtime imports/hash parity | 0 | Changed owners import; installed hashes match final source; no lifespan/provider startup | `evidence/runtime-imports.json` |
| Host snapshot tests | 0 | 22 passed | `evidence/host-source-snapshot.log` |
| Host harness configuration tests | 0 | 30 passed | `evidence/host-harness-configuration.log` |

The final full run is `iacs-validation-c1ylh8yt`. Its `checks.json` holds exact commands, synthetic environment and isolation mounts; `results.json` records every exit code. All 60 persistence/diagnostic suites, migration upgrades/checks, schema drift, database restore rehearsal, architecture boundaries, strict types (132 source files), lint, compile, generated contracts, lock/tree checks and frontend build pass. Cleanup/source integrity exit0 and production-unchanged=true. The 35 backend unit skips are journal tests deliberately executed against PostgreSQL in the full persistence inventory; all have passing exact-name counterparts in `evidence/skip-counterparts.json`. No additional exclusions were added.

The browser test entrypoint/options are recorded; its original Docker wrapper argv was not saved, so the evidence does not invent wrapper flags. Image identity, isolation configuration, frontend hashes and the actual console result are retained. 58 browser tests passed on Chromium/WebKit with one worker, unchanged 30-second test limits, CI=1, owned port5174, strict inert API fixtures, no backend proxy and network disabled. Every frontend input hash equals the final harness source; later changes were backend-only compatibility normalization/test and AST-identical EOF cleanup. Broader unrelated layout/browser suites were not rerun in this follow-up; the historical 106-case run remains historical evidence below.

Final runtime image `sha256:20ede5a8c60e5523535e90ae2d086d7ea359880136b3d62f4446c75302888987` is non-root. The build used final local source, and PDF/import smoke checks had network disabled and no source/dependency overlays; installed changed-owner hashes equal the final manifest. Dependency audit reports from the previous unchanged locks remain historical; final harness rechecked exact locks/dependency identity, with no follow-up dependency changes. No performance claim or new benchmark is made for this mutation correctness fix; earlier R06 measurements below remain historical.

### Independent review and limits

One existing reviewer was reused for the actual immutable follow-up delta, originally requested explicitly as **gpt-6-astra / medium**. Review target `8ab88de886ba15d56de4cb44b17862f774c703b19e1a701637fc4a90e225f7db` covered actual new/uncommitted source, callers, storage tests and strict targets, via `review.patch` and `review-manifest.json`. It found no material issue with locked edits, actor/audit ownership, authoritative receipts, approval binding, duplicates or client migration. A targeted follow-up reviewed only the two explicit null normalization lines/new compatibility test and EOF cleanup at final fingerprint `1bc3838659160f422bad2e5800473da993188f4746f0c1e338c11beaa396a557` and found no material issue. Reviewed files were stable snapshots; no reviewer edited application files or operated integrations. Final check results were provided separately after completion.

Effective primary model/effort and reviewer model/effort are **unverified because session metadata is not exposed**, including custom overrides. A role name is not model proof; no simulated Astra approval is claimed. Requested primary remains Sol Extra High. Token counts are unavailable. The last observed usage window had86% remaining, above the user's pause threshold of20% remaining. This was a scoped independent delta review, not whole-repository certification; previously underexamined unrelated files were not reopened.

### Verification and owner actions

1. Extract the bundle; inspect top `followup-change-manifest.json`, `task-only.patch`, `starting/` and `final/`. Check its hashes/CRC and the final source fingerprint. `prior-remediation/` preserves earlier remediation context separately; exact original delivery documents are under `evidence/prior-context/`.
2. Read settings locks/commit and coordinator admission, then `integration_settings.py`, routes and frontend deletion caller/panel. Inspect 23 real API cases and `suite-004` logs before relying on source-extracted reproductions. Read exact baseline failures and final JUnit counterparts.
3. Verify no index-only caller/confirmation bypass exists; inspect opaque IDs/privacy, stale409, duplicateone-occurrence semantics, authoritative saved responses and all 14 activation +20 settings real-storage regressions. Inspect browser stale-delete assertions and final runtime installed hashes.
4. Later deploy backend/frontend together under separate authorization; stale screens/approvals must refresh and get a new confirmation. This follow-up has no schema migration. Earlier calendar retirement upgrade/rollback instructions below still apply to the undelivered combined local remediation; production was not migrated or deployed. No rollback restores deleted credentials automatically.

The previous handover's stale `full-final-6-console.log` reference is repaired to the actual historical `prior-remediation/evidence/full-final-8-console.log`. The prior archive's original bytes/hashes and exact delivery documents are preserved as provenance; its extracted readable handover has that one link correction. Bundle inventory excludes secrets, .env credentials, .git, production data, dependencies and caches; the follow-up patch is checked/applied only to an inert copy of included starting files and all final bytes compared. External inspection result: `/private/tmp/iacs-integration-mutations-20261014/bundle-inspection.json`.

Historical C01 attachment optionality remains the existing deferred product choice; this follow-up does not change it. Real providers/hardware, live application lifespans, production migrations and deployment were intentionally not executed. Synthetic validation does not prove audible playback, exactly-once delivery or production speedups. No unresolved V01/V02 material finding remains.

## Earlier remediation context — historical, completed before this follow-up

The following preserved narrative describes the previous R01–R09/R10/R11/C01 work and its earlier fingerprint, test runs and review checkpoints. Those changes were already present at the follow-up start; they are **not** this follow-up's implementation or current final fingerprint. Its artifact paths live under `prior-remediation/evidence/` inside the portable bundle, or the original local evidence folder identified below. The original source/diffs/manifests are retained there. The full-final8 log typo is corrected in this readable copy.

IACS now validates settings before saving them, preserves partial voice-delivery receipts, finds the previous live event through intervening backfills, and handles routines around midnight correctly. Recovery policy has clearer ownership, authentication and camera probing load less data, and unused internal handoffs are removed. iCloud Calendar has been retired from the application, UI, workflow catalog and dependencies, with a forward migration that removes credentials, cancels eligible future passes and holds affected work without replaying hardware.

For example, a voice announcement accepted by one speaker remains recorded as accepted if a second speaker times out. A saved configuration also stays visibly saved when a provider restart fails, with a separate activation warning.

These are local, uncommitted changes. Production has not been migrated, deployed, restarted or tested against providers. The owner must separately perform the coordinated backup/stop/migrate/start procedure below. Requested mobile snapshots still fail delivery when capture fails: the existing contract does not say they are optional, so text-only fallback remains a product choice. This is a scoped remediation with inspectable evidence, not an every-file certification.

Validation status: **final full harness passed**: 1,565 backend tests, 367 frontend tests and 1,111 cases across 59 persistence/diagnostic suites, including schema/migration/restore checks. The exact final-source Chromium/WebKit rerun passed all 106 checks. The scoped independent evidence review is complete, with no outstanding material finding.

## Technical appendix

### Local identity and baseline

- Project: `/Users/jas/Documents/Intelligent Access System`.
- Starting branch/HEAD: `main` / `abec96d37b2d60a2d5f403f441cef21619432b1a`. This is also the original review SHA; it was already local. No remote access was used.
- Starting tree: clean, with no pre-existing staged, unstaged or relevant untracked source changes. The actual bytes of 831 safe source files were captured; committed HEAD was not used as a substitute for them.
- Starting source fingerprint: `4d3d3fe526f87f222526c6dadb95ac35402ade2a0a65311ec67610b18b4fa8e2`.
- Final code/test/docs snapshot fingerprint: `a773364a0933db096464143bb8941f87e3dc5a3874f73e03ea68a18b47ed3d1f`.
- Evidence base: `../IACS Regression Baselines/remediation-20261014/`. Initial snapshot: `iacs-validation-t9b5rhyd/source`; final snapshot: `iacs-validation-p0mpv1kl/source`. Both manifests retain per-file SHA-256 values. Root delivery documents and the zip are outside the harness source selection and are documented separately.

No branch/worktree creation, staging, commit, reset, stash, fetch, pull, merge, push or deployment occurred. Existing running containers were only inspected for isolation; their environment values/data were not loaded. Validation copied current tracked bytes and safe untracked source into isolated snapshots, used disposable PostgreSQL/Redis and inert providers, and checked production state before/after. The final runtime checks used newly built images, not a stale installed application.

Coherent batches were settings validation/activation; live history and circular timing; pure recovery policy; voice delivery truth; compact reads and first-run concurrency; internal UUID handoffs; complete calendar retirement; review fixes and integrated validation. They remain visible as working-tree changes rather than commits. Detailed task-only statistics, new/deleted paths and original/final files are included in the review bundle. Against captured starting bytes, 162 source paths changed: production 84 files (+1,136/−5,552 lines); migrations 2 (+227/−0); tests 66 (+2,165/−4,265); generated contract 1 (+1/−12); dependency manifests 2 (+0/−162); repository docs 4 (+164/−147); validation configuration 3 (+18/−29). Root handover/evidence documents are new, written after code validation and counted separately in the bundle manifest.

### Requirements and findings

All R01–R11 implementation, local validation and scoped independent review is complete. C01 preserves existing behavior pending a product decision. Exact current symbols, regression names, results and artifact paths are also in `IACS_REMEDIATION_EVIDENCE.json`.

| ID | Status | Finding and correction | Current owners / regression evidence |
| --- | --- | --- | --- |
| R01 | Fixed; locally verified | Reproduced. Invalid recognized settings were saved before conversion; malformed policy could become allow. A canonical pure validator checks the complete prospective configuration before writes, normalizes legitimate conversions and preserves omitted/blank encrypted secrets. Invalid legacy policy fails closed; Admin diagnostics identify keys without revealing values. Legacy type repairs use explicit JSON dirty marking. | `services/settings.py`, `settings_validation.py`, `api/v1/settings.py`; `test_settings_validation.py`, `test_settings_configuration.py`. Original real-storage run: 10 failed/1 passed; current focused storage: 20 passed. |
| R02 | Fixed; locally verified | Reproduced. Limiting before historical filtering missed a preceding live event. SQL now filters canonical source/JSON historical semantics before deterministic time/UUID ordering and LIMIT 1. Each identity uses an independently ordered live-filtered LIMIT 1 index seek; at most two candidates feed same-statement winning-ID hydration. Fixed literals permit generic-plan implication. | `access/evidence.py::_latest_live_person_or_vehicle_event`, `models/core.py`, migration 0015; two reported-behavior failures in the original combined suite, then 23 passing evidence cases. |
| R03 | Fixed; locally verified | Reproduced. A later voice failure erased earlier acceptance. The existing delivery vocabulary and explicit progress accumulator preserve per-destination truth through exceptions, timeout and cancellation, stored checkpoints, APIs and visible recent history. Provider acceptance is not audible-playback proof. Uncertain runs remain held. | `notifications.py::_send_voice`, `notification_dispatch.py`, `notification_runs.py`, HA TTS adapter, `NotificationRunHistory.tsx`; 6 original voice failures; 6 focused voice cases and 17 persistence truth cases pass. |
| R04 | Fixed; locally verified | Reproduced. Linear minute averaging put midnight routines near 08:00. Circular unwrapping cuts the largest empty arc; fewer than three samples, a spread of six hours or more, or an exact half-day comparison returns UNKNOWN. Site timezone and existing ±45-minute boundaries remain explicit. | `access/timing.py`, evidence timing projection; four original storage behavior failures. Pure and real-storage tests cover midnight/daytime, sparse/separated samples, DST/timezones and thresholds. |
| R05 | Fixed; locally verified | Fixed through cohesive extraction. Identity eligibility, movement direction, conflict relevance, arrival conflict and recovery phone/camera/safety precedence are pure policy. Evidence gathers current facts; execution and authorization retain committed effects and fresh revalidation. | `access/decision.py`, `movement_fsm.py`, evidence, resident recovery, execution/authorization; table-driven recovery tests plus actual camera, visitor, recovery and admission suites. |
| R06 | Fixed; locally verified | Demonstrated low-risk reductions implemented. Photos/passwords/raw event payloads are deferred or projected; explicit photo reads hydrate only requested media. Camera preflight uses a narrow projection and avoids unnecessary full resolution. Successful auth skips setup counting. Complete fuzzy matching is retained. | Evidence/authorization/auth/middleware/profile-photo owners; `test_compact_auth_access.py` (8 passed), efficiency suite and before/after artifacts. No stale authorization cache, directory cap or ordered-processing parallelism was added. |
| R07 | Fixed; locally verified | Reproduced. Requested keys restarted services on no-op saves; activation/read failures could misstate accepted persistence. Precise changed keys feed one serialized admitted persistence→activation lifetime. No-op/blank-secret saves do not restart. Partial/failed/pending activation remains a saved receipt in all clients. Acknowledged commit survives ordinary teardown error; unconfirmed commit is never reported saved. | Settings owner/coordinator, thin routes, adapter-owned connection tests, every settings-save consumer; 14 coordinator and 20 storage tests pass, including mixed cancellation, queued cancellation, shutdown, secrets, postcommit read/close and commit failure. |
| R08 | Fixed; locally verified | Verified unused plumbing removed. Execution and external-admission persistence return event UUID/None; durable publication remains reserved in the transaction. Discarded realtime payload/result snapshots and dead plural snapshot helper are gone. Tests inspect committed behavior instead. | `access/execution.py`, `access_events.py`, `notifications.py`; camera/resident recovery, pipeline, publication and notification handoff suites. Persisted enrichment, snapshots, vehicle fields and public events remain. |
| R09 | Fixed; locally verified | Complete active feature retirement implemented. Runtime account/session/SDK/sync/action/API/UI/model/dependency paths are removed, not replaced with stubs. Migration 0016 handles existing data and captured work conservatively. | Migration 0016, `test_calendar_retirement.py` (17 passed), runtime retirement contracts, current route/catalog/import checks and browser absence assertion. Historical migrations/fixtures remain intact. |
| R10 | Fixed; locally verified | Additional reproduced defect: Infinity/extreme percentages/booleans could become clear camera confidence; huge integer conversion could overflow. Invalid/nonfinite/out-of-range confidence is now rejected conservatively, including ±10**400. | Evidence confidence parser and `test_camera_confidence.py`; original 4 failed/6 passed, corrected targeted tests pass. |
| R11 | Fixed; locally verified | Additional reproduced defect: concurrent public setup could create two first Admins. An advisory transaction fence serializes authoritative counting, creation and required audit. Audit failure cannot persist the Admin. | `auth.py::create_initial_admin`, auth API and `test_initial_setup_concurrency.py`; original concurrency test failed with two Admins, both corrected concurrency/audit cases pass. |
| C01 | Deferred | Deferred product choice, behavior preserved. Requested mobile attachments have a boolean request but no explicit optionality. Capture failure still prevents that delivery; no new settings matrix or evidence bypass was added. | Workflow media contract and `_snapshot_attachment`; existing optional public-URL omission retains its truthful warning. |

Invalid legacy settings can be diagnosed through Admin `GET /api/v1/settings/validation`. Cookie authentication reads only fresh authentication fields, so unrelated corrupt policy/integration settings do not prevent authenticated repair. A malformed authentication configuration still fails closed; use an already valid Admin bearer session for repair, or a separately authorized maintenance/verified matching-backup recovery if none exists. Do not relax policy validation or silently rewrite legacy access policy.

### Ownership and extension guidance

| Area | Before | After / extension point |
| --- | --- | --- |
| Access | Evidence mixed SQL, direction/recovery branches and policy; full evidence could run twice before optional camera work. | Evidence gathers facts and narrow camera need; pure `MovementDirectionFSM`/`access/decision.py` decide; `access/timing.py` handles time policy; execution commits journals/reservations; authorization freshly rechecks before effects. Add a condition using existing facts to the typed policy and table tests. New facts may extend the evidence contract. Providers/delivery/unrelated SQL need not change. |
| Settings | Persistence, requested-key restart decisions and provider test I/O were coupled in routes. | Canonical validation/persistence owns values/audit; coordinator owns serialized activation; adapters own vendor tests; UI owns truthful saved/activation receipts and drafts. Add a setting to the canonical defaults/validation contract and affected-service mapping only when activation is needed. |
| Notifications | Voice fanout replaced destination details on later error; no native recent receipt view. | Existing vocabulary, explicit delivery progress, dispatcher checkpoint ownership and safe run serialization retain truth; bounded Admin history reads show it. Add a supported destination through its adapter/contract, with definite/uncertain failure tests; do not add a second delivery framework or replay mechanism. |

Intentionally retained complexity includes fresh authorization, configuration/root locks, atomic audit, durable reservations, leases, idempotency, command reconciliation and cancellation draining. These protect real effects. Logical publication reservations do not guarantee exactly-once network delivery. A hard process loss before a receipt is durably checkpointed can remain unknown; recovery must not manufacture acceptance or blindly replay it.

### Calendar inventory, data and operations

Removed owners include `services/calendar_*`, `services/icloud_calendar*`, `modules/icloud_calendar`, the calendar route, composition/startup registration, dedicated ORM models, account authentication/2FA/session handling, reconciliation/queues, workflow integration dispatch plumbing, frontend account/sync/recovery editor/client/catalog, calendar-only styles/tests and dependency `pyicloud`. Normal locked uv resolution removed 13 packages total (pyicloud plus 12 exclusive dependencies), with no added packages or version changes to survivors. Shared lifecycle, HTTP, visitor, scheduling and UI helpers remain supported.

Migration `20261014_0015` adds live-history indexes. `20261014_0016` retires calendar state after that revision:

- Drops `icloud_calendar_accounts` and `icloud_calendar_sync_runs`, including encrypted session bundles and account leases. Sanitized sync statuses/counts/checkpoints are preserved as retirement audits; raw Apple IDs/names/errors and executable run contexts are not archived.
- Proven calendar SCHEDULED/ACTIVE passes are cancelled without deleting visits/events/reservations/linkage. Departed duration passes lose future access. USED one-time open visits remain departure-matchable.
- Ambiguous provenance, reserved/held work, active open visits, reversed visit chronology and inconsistent current-arrival consumption block the whole migration. UUID-specific errors require deliberate offline review; missing deleted account rows and valid truncated source references alone are not blockers. Older duration reservations need not equal the latest arrival.
- Disables affected saved workflows and clears schedules. Queued/retrying/claimed/orphan captured work is held as terminal `review_required` with `icloud_calendar_retired`; whole mixed plans remain, leases/claims are cleared, accepted/unknown receipts are retained and sanitized. It never deletes a calendar step and executes the hardware remainder. Unaffected work remains active.
- Cancels pending calendar confirmations and removes calendar-only settings. No migration imports removed runtime modules or contacts providers.

Every retained current-tree iCloud/pyicloud reference is classified in `calendar-leftovers-classified.json`: immutable schema/migration fixtures, retirement migration/tests/docs, or safe historical pass/audit labels. No active provider capability remains. The type coverage baseline removes only the deleted integration-dispatch module; surviving checks/flags are not relaxed.

**Production was not migrated.** For a separate upgrade, stop/drain all old workers and back up/verify the matching database, configured data/auth-root files and old images. Mixed old/new workers are unsupported. Inspect unresolved visits/commands before upgrading. Run the normal Alembic head upgrade using the new image while workers remain stopped; blockers roll back all retirement SQL. Resolve only the identified record through its established owner and deliberate authorization. Start the new deployment after successful migration/schema verification. Review disabled workflows and explicitly create supported replacements; do not replay their hardware remainder.

After successful cutover and a verified backup, an operator may inspect only the deployment's configured `IACS_DATA_DIR/icloud-calendar-sessions` child. Confirm the exact directory, reject symlinks and ensure no old process uses it; remove that retired directory offline if desired. Never delete the data parent, auth-root, other provider files or backups. This task/migration did not remove file state. Old UUID-only browser references can be cleared by exact `iacs-calendar-run:` storage keys.

Downgrade is explicitly refused and cannot recreate deleted credentials/data. Rollback requires stopping new workers and restoring the matching pre-upgrade database/files and image pair; otherwise fix forward.

### Tests, measurements and retained failures

The starting full isolated harness passed: backend 1,529 passed/35 skipped, with 62 persistence/diagnostic suites and schema/restore checks. It is baseline evidence, not evidence for the implementation.

The final harness uses Python 3.12.14, disposable PostgreSQL/Redis, locked dependencies and source-bound containers. Host `/usr/bin/python3` is 3.9 and was not used for supported application checks. The backend tooling image is `iacs-backend:uiprotect-23-dev-20261013`; current source and a separate exact-lock venv override its old packaged application/dependencies. Final images are built from the final safe snapshot and checked separately.

| Check | Actual result / location |
| --- | --- |
| Full final harness | Passed (exit 0): `iacs-validation-p0mpv1kl`; command in JSON and `full-final-8-console.log`. Backend 1,565 passed/35 skipped; frontend 61 files/367 tests passed; lint/type/architecture/contracts, all 59 suites/1,111 cases, migrations/schema/restore, cleanup/source integrity and unchanged-production checks pass. |
| Host source-selection and harness configuration | 22 and 30 tests pass; `host-source-snapshot.log`, `host-harness-configuration.log`. |
| Calendar populated/fresh/blocked upgrade | 17 focused cases pass; `iacs-validation-focus-qrhei2ut`. Full harness repeats them against the final snapshot. |
| Settings accepted/uncertain/cancellation | 20 real-storage and 14 coordinator cases pass; `iacs-validation-focus-pn06pbl6`. |
| Browser | 106 passed (11.4 minutes): `browser-checks-5`, one worker, exact final-source hashes. Earlier corrected application run passed 104/106; two stale response fixtures were fixed. The subsequent concurrent final-source run (`browser-checks-4`) had 103 passes/three WebKit timeouts; the one-worker rerun retains unchanged assertions and 30-second test limits. Chromium/WebKit, CI=1, owned 5174, network disabled, strict API fixtures, no backend proxy. |
| Final runtime PDF | Pass: `runtime-pdf-seek/iacs-runtime-pdf-60oo4kk2`, non-root image `0b1f611b…60308`, no source/dependency overlays and no network. |
| Runtime imports/retirement | Pass: `final-seek-runtime-imports.json`; no pyicloud package, routes, models or action registrations; installed settings/activation/evidence hashes match the final snapshot. |
| Development/backend and frontend runtime builds | Pass; exact image IDs in `final-images.json`. nginx config/assets pass with an isolated synthetic `backend` hostname; the initial hostname-free check failed DNS and is retained. |
| Dependency audits | Python strict exported-lock audit: no known vulnerabilities. npm lock audit: zero vulnerabilities. `dependency-audits/`. |
| Diff/docs | `git diff --check`, 268 local links, archive CRC/member hashes/exclusions and task-only patch application to inert starting context pass; see `bundle-inspection.json`. |

All 35 db-free skips are `tests/test_access_device_command_journal.py`; their exact passing persistence counterpart is separately registered in the full harness, not omitted. Calendar-only tests were removed with their feature. Backend inventory is 1,564→1,600 cases: 62 removed and 98 added, with no unexplained removal. Frontend inventory is 377→367: eight calendar recovery cases, two calendar provider parameter cases and one calendar pending-operation case were removed; one voice receipt history case was added. Shared auth-root tests were retained under `test_auth_root_persistence.py` (12 cases), and all three historical calendar ownership downgrade states now run in the retirement suite. Test inventory/rename accounting is retained separately.

Original failures are retained rather than overwritten: settings 10/1 storage reproduction and 15 unit failures; live/timing 6 failed/10 passed (two history/query and four timing cases); voice six failures; confidence four failures; concurrent setup two-Admin failure. Fixture/setup failures are separately labelled: missing synthetic fields, mock sessions missing the new narrow query, old response fixtures, browser Vite cache permissions, Python 3.9 syntax mismatch and full-run lock refusal. An earlier populated migration exposed an actual asyncpg bind-type conflict; explicit SQL casts corrected it. Interrupted harnesses retain incomplete/failing status and do not count as final passes. In particular, `hlac8mcs` and `vc9gcxf_` were initially summarized too broadly: both retain five failed suites (stale auth mocks and deleted calendar-only dispatcher imports). Those fixtures were corrected without removing behavior assertions; all five passed in `iacs-validation-focus-iddw489u` (9/12/26/7/6 cases). The following `8a9f7in_` run was deliberately interrupted for final documentation/unused-icon cleanup and is not a complete pass.

Final plan inspection found the interim OR query filtered 1,998 unrelated rows.
The bounded seek correction passed actual and forced-generic plan assertions for
identity/time index conditions, at most one emitted row per branch and zero
unrelated-row filtering; selection/hydration remains one statement. Its extra
query structure makes the interim synthetic median 1.79→2.13 ms, so reduced
scanned work is not presented as a universal latency improvement.

Performance uses 16/2,000 registrations, two warm-ups, seven samples, 64 KiB unused photos/payloads and four concurrent UI readers. Public outputs match in all measured workloads. At 2,000: no-camera closed/absent probes fell from five queries to one (median about 4.50→1.04 ms); open probes five→zero (4.69→0.01 ms); Bearer middleware three→two (2.48→2.32 ms). Photo-heavy identity Python peak fell about 547→314 KiB while median latency slightly increased (2.69→3.03 ms). Fuzzy/OCR matching remained approximately 75/114 ms median with complete candidates and unchanged algorithm. These are synthetic substep measurements with tracemalloc; seven-sample p95 is coarse, scalar/JSON bytes are proxies rather than wire bytes/RSS, and larger directories retain linear fuzzy cost. Full artifacts include SQL, actual/generic plans, materialized rows/scalars, p50/p95 and output parity. No production speedup or universal capacity claim is made.

At 2,000 registrations, the paired measurements are below (milliseconds; before→after). Query counts are per measured operation, or per four-reader batch for UI reads.

| Workload | p50 | p95 | Queries |
| --- | --- | --- | --- |
| Exact known plate | 1.155→1.029 | 1.609→1.171 | 1→1 |
| Unknown fuzzy plate | 79.512→75.397 | 81.986→109.557 | 2→2 |
| OCR variant | 116.807→114.391 | 130.836→119.155 | 2→2 |
| Photo-heavy identity | 2.686→3.027 | 2.795→3.204 | 2→2 |
| Live predecessor | 2.356→2.127 | 2.570→2.217 | 1→1 |
| No-camera closed/absent probe | 4.497→1.043 | 5.070→1.091 | 5→1 |
| No-camera open probe | 4.693→0.007 | 5.510→0.010 | 5→0 |
| Token authentication substep | 1.385→2.131 | 1.445→2.332 | 2→2 |
| Bearer middleware request | 2.476→2.323 | 5.383→2.571 | 3→2 |
| Four concurrent directory readers | 62.415→62.604 | 105.410→63.333 | 20→20 |

Authentication substep latency increased while unused photo materialization fell; the measured middleware request avoids setup counting. Cookie requests still read fresh authentication configuration. The small 16-registration workload and exact plan/row/scalar/peak details are in `performance-comparison-final.json`; medians/p95 are measurements, not guarantees.

### Independent review and limits

The primary requested model was GPT-6.1 Sol/xhigh; session tools did not expose effective model/effort. One read-only reviewer was explicitly delegated with `model=gpt-6-astra`, `reasoning_effort=medium`, `fork_turns=none` and reused throughout. Effective reviewer model/effort metadata was also not exposed; a role name is not verification. The independent reading is real, but confirming that the effective model was Astra remains an explicit verification gap. A separate explicitly selected Astra app conversation can use the compact continuation brief in `review-checkpoints.json` and the same local bundle if that verification is required. No CLI/cloud task, global configuration or IACS AI settings were changed. Token counts are unavailable; last observed account usage was 11% used, and work must pause at 20% remaining.

Three substantive checkpoints covered the starting ownership/migration plan, correctness/settings implementation, and integrated retirement delta. Targeted follow-ups resolved nested ESPHome normalization, legacy scalar repairs, huge confidence conversion, deferred self-photo access, whole activation-chain cancellation, inactive-present camera equivalence, migration chronology/current consumption and admitted save/postcommit cleanup. Reviewer reports contain no outstanding material finding in those reviewed deltas. The final reviewer independently checked the completed full-harness/JUnit/browser/source-parity evidence and found no outstanding material discrepancy. Sol's own review is not presented as independent approval.

Stable review fingerprints include `b4dc69ee…ab91` (checkpoint 2), `7f127b2f…5a35` (targeted compact/read/internal review), `c1b008cb…5c5a` (checkpoint 3), `f5847dc1…64ab` (retirement fix) and `ead61335…7cc8` (final settings lifecycle). Exact diffs/manifests are retained. The migration strategy was refined with evidence: cancelling an open ACTIVE duration visit would break departure matching, so that state blocks upgrade; departed ACTIVE passes are cancelled. No unresolved disagreement remains on that point.

Fully reviewed/authored compact owners include new validation/activation/test orchestration, circular timing, pure access decision, retirement migration and notification receipt UI/tests. Larger settings/auth/evidence/notification/visitor/recovery/lifecycle/API/frontend files were read in relevant bounded paths and reviewed with callers/tests; this is partial file coverage, not whole-file certification. Locks/generated contracts, immutable historical schema fixtures, dependency manifests, removed vendor modules and CSS were statically traced/checked, with affected UI screenshots separately inspected. Unrelated integrations and hardware behavior were not live-tested; physical Safari/device behavior and production cutover remain unverified.

A fresh reviewer should inspect migration 0016 and its synthetic fixtures first, then settings save/activation and API/UI receipts, voice dispatcher/checkpoint history, compact authentication/media hydration and final access authorization. Compare the task-only patch against the captured starting versions, run the documented isolated harness, examine preserved failures and skipped-test counterparts, and verify runtime/source hashes before any separate deployment. The bundle provides current changed text, needed starting context and new/deleted manifests without secrets, data, caches, dependencies or Git internals.

The final archive contains the handover, compact evidence, a task-only patch, final changed text, necessary starting text and new/deleted manifests. Its paths, file hashes and exclusions were inspected, and its patch was applied to an inert copy of the included starting context: every resulting changed file matches the final bytes and all intended deletions are absent. No Git repository was initialized. Delivery documents were written after code validation and received separate link/whitespace/archive checks.
