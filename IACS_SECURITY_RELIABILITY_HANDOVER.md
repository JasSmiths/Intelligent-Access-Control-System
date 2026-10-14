IACS had several timing and ownership problems in its account, live dashboard, notification selection and missed-event recovery paths. A login could verify an old password and then acquire a newer session version after a reset. Concurrent resets could miss an invalidation, and concurrent administrator edits could release their protective locks too early. Account changes now recheck current authority after expensive password work and keep confirmation, the last-administrator rule, the change and its required audit in one transaction.

Live dashboard connections now enforce expiry and recheck current account authority before sending and while idle. Standard users receive limited refresh hints instead of administrative audit details. Connection cancellation, blocked sends, Redis shutdown and repeated cleanup have one owner, with explicit failure when cleanup cannot finish. The browser refreshes current data after reconnecting while preserving open drafts and history filters.

A saved Apprise selection used to mean a numeric position that could later belong to a different recipient. Deliberate selection now records an opaque catalog revision. An unrelated edit preserves the old binding; a stale save or dispatch requires review rather than silently delivering to another destination. For example, removing A from [A,B,C] cannot reinterpret an old selection of B as C. Legacy ambiguous workflows and captured pending work are held by forward migration, while accepted/uncertain receipts and completed history survive. Empty selected voice delivery cannot fall back to a default speaker, and duplicate Apprise occurrences retain their own receipts.

Recovery previously reported success after taking the newest 250 observations, losing 51 in a reproduced 301-event window. It now retains bounded discovery progress in PostgreSQL, advances only with durable acceptance or deduplication, and resumes after interruption. A dense timestamp the provider cannot enumerate is explicitly held. Independent review also found and corrected repeated overlap subtraction that could invent progressively older recovery work.

Our validation artifacts accumulated duplicate dependencies and raw test databases and exhausted disk space. Cleanup preserved source, evidence, production data and rollback checkpoints. Validation now reuses one read-only dependency preparation, checks a 30GiB reserve plus admission headroom, limits task allocation, serializes bulk runs and removes only proved-owned disposable state. These guards are conservative checks, not a filesystem quota or protection against unrelated writers.

Current status: **complete; estimated overall completion 100%**. Detailed results below distinguish focused, baseline, interrupted and final runs. The final isolated run passed 2,814 backend/database tests, all frontend checks, 30 affected browser cases, migration/schema and restore checks, and installed runtime/PDF verification. All 35 backend skips have exact passing persistence counterparts. Independent code and evidence review is closed with no unresolved material finding. The owner subsequently authorized committing and pushing the remediation (07bcd6b), then held deployment when GitHub exposed a Linux test-data ownership failure. The focused correction (41fa684) runs PostgreSQL as the runner without added capabilities, preserving private permissions and fail-closed disk accounting. Complete local validation and actual Linux CI evidence are recorded below. Source changes are committed and pushed; delivery artifacts were regenerated afterward. These migrations and application changes have not been deployed to production. Deployment requires separate authorization, a tested matching backup, review of held recipient workflows, and normal supervised rollout checks. Calendar retirement was already present in the starting source and remains intact. Effective model metadata and actual token counts are unavailable; genuine requested-model subagent reviews are recorded without claiming verified model identity.

## Technical appendix

Workspace: `/Users/jas/Documents/Intelligent Access System`. Branch `main`, starting HEAD `53db38ffad2517e2b3076f08086be6422a287287`; committed source now41fa684d7e84d5f94bd9d91c88de2df41b1a8cba. Starting tree was clean; no pre-existing source edits were replaced. Safe starting source: 815 files, fingerprint `1bc3838659160f422bad2e5800473da993188f4746f0c1e338c11beaa396a557`. Final candidate source fingerprint: `f7d66850e18e8e05f600be877a21e2b4bcb4b6e83e3502f1daae6d0a8066ac04` (832 files). HEAD alone is not the tested identity. Fingerprints use SHA-256 over sorted relative path, NUL, file SHA-256 hex and newline for each selected exact-byte source file (`scripts/validation/source_snapshot.py`). Selection includes relevant untracked first-party source. It excludes delivery artifacts, environment/credential files, production data/logs, dependencies, build/test/browser caches, Git internals and private runtime state; full exclusion rules are retained in that module. `task-only.patch` compares captured starting bytes with final working-tree bytes, including new files. Initial implementation remained local until separate user authorization. Remediation07bcd6b and focused CI correction41fa684 were then committed and pushed to existingmain; no branch switch occurred. Delivery documents describe this validation point; Git history records any subsequent documentation commit. Remote operations during the CI follow-up read Actions metadata/logs and pushed the fix; no remote source was fetched or substituted.

Evidence root: `/private/tmp/iacs-security-reliability-20261014`. Paths below are relative to it unless they start `backend/`, `frontend/`, `docs/` or `scripts/`. The portable bundle maps retained evidence under `evidence/`, final changed files under `source-final/`, and starting/context sources under `source-start/`. It contains no installed dependencies or production state. Delivery documents are excluded from executable-source fingerprints; this handover/JSON and the bundle are generated after the final candidate snapshot. Architecture/integration/validation documentation is included in that snapshot.

Coherent batches: A account authority/confirmation, B realtime lifecycle/audience and frontend invalidation, C recipient binding/legacy holds, D durable historical discovery, and N09 isolated validation storage safeguards. No wholesale refactor or renewed calendar retirement was performed.

| Task-only category | Files | Lines added/deleted |
| --- | ---: | ---: |
| docs | 5 | +241 / -5 |
| generated | 1 | +3 / -3 |
| migrations | 2 | +141 / -0 |
| production | 34 | +1300 / -592 |
| tests | 26 | +2433 / -213 |
| validation_tools | 6 | +397 / -56 |

## Findings and verification

Statuses require the final full harness, runtime proof and review closure; focused passes alone are not complete verification. Exact collected case identities and commands are retained in JUnit/checks artifacts.

| ID | Status | Root cause and correction | Current owner/symbol | Regression tests | Actual paired/focused evidence |
| --- | --- | --- | --- | --- | --- |
| N01-A | fixed_and_verified | Login could adopt a session version whose password it never verified. Issue only after exact verified password/version and current active authority are rechecked. | backend/app/services/auth.py:238 `complete_verified_login` | test_account_authority.py: login/reset overlap, old password, disabled account | Baseline account behavior failures; 30 account API/PG cases pass at reviewed correction b_pa3c4x. |
| N01-B | fixed_and_verified | Concurrent resets could collapse two version increments. Fresh target under the root-first transaction increments once per successful reset. | backend/app/services/user_accounts.py:180 `reset_account_password` | test_account_authority.py: concurrent/sequential reset and token invalidation | Real validator rejects pre-reset/intermediate tokens; failure/cancellation rollback covered. |
| N02 | fixed_and_verified | Standalone confirmation consumption released last-Admin locks. Participant confirmation, invariant, mutation and required audit share one commit. | backend/app/services/user_accounts.py:75 `_mutation` | test_account_authority.py: delete/demote/disable, deadlocks, audit/cancel | Paired account baseline 18 failures/7 controls; reviewed correction 30 cases passed. |
| N03-A | fixed_and_verified | Connected sockets outlived expired/revoked/inactive authority. Independent expiry timer plus current authorization before sends and on idle. | backend/app/services/realtime_connections.py:78 `RealtimeConnection` | test_realtime_authority.py: expiry/logout/reset/disable/delete/role | Paired socket baseline 16 failures; reviewed correction 24 passed; final includes 1/10-connection load. |
| N03-B | fixed_and_verified | Standard users received administrative audit payloads. Explicit common hints preserve internal/full Admin delivery contracts. | backend/app/services/realtime_connections.py:31 `common_payload` | test_realtime_authority.py: audience, internal listeners, queued role loss | Real ASGI/PostgreSQL authority and Redis coverage; no handshake-only authority. |
| N04 | fixed_and_verified | Blocked send/idle receiver and shutdown races could retain connection tasks. One supervised lifetime, bounded waits, repeated-cancel draining and truthful incomplete cleanup. | backend/app/services/event_bus.py:50 `EventBus` | test_realtime_authority.py; realtime-invalidation.spec.ts | Checkpoint2 faults reproduced then corrected; browser reconnect/draft/authority cases passed. |
| V03 | fixed_and_verified | Saved selected Apprise indices could silently point to another destination. Opaque catalog revision binds deliberate selection; save and pre-attempt dispatch revalidate. | backend/app/services/notification_recipient_binding.py:27 `recipient_binding_error` | test_notification_recipient_binding.py; test_notification_binding_upgrade.py; notification-recipients.spec.ts | Before 5 failures/1 control; after 20 API/PG +1 populated upgrade +71 affected units passed stw3rvc2. |
| N05 | fixed_and_verified | Newest-250 discovery discarded 51 of 301 events while claiming completed coverage. Raw saturation-aware pages, bounded fixed intervals, durable frontier/page and atomic acceptance; explicit held gaps. | backend/app/services/restart_backfill.py:319 `_discover`; backend/app/services/historical_discovery.py:59 `HistoricalDiscoveryStore` | test_historical_discovery_persistence.py; test_historical_discovery_upgrade.py | Before 301 failed/3-event control passed nnbz43cm; first fix 2 passed; expanded 26 discovery/12 historical/1 upgrade/15 pipeline passed ppr2ae8n; final has 29 discovery cases. |
| N06 | fixed_and_verified | Account creation could retain actor authority across expensive hashing. Revalidate exact actor/session under the mutation fence after hashing. | backend/app/services/user_accounts.py:122 `create_account` | test_account_authority.py: stale actor during creation | Additional material finding reproduced in paired baseline and actual API tests. |
| N07 | fixed_and_verified | Apprise occurrence receipts were dropped by the safe outcome whitelist. Retain bounded opaque apprise:index receipts, including duplicate URL occurrences. | backend/app/services/notification_runs.py:56 `safe_destination_outcomes` | test_notification_recipient_binding.py: duplicate receipts/checkpoints | Occurrence receipt and partial/unknown delivery evidence pass; no URL credentials in receipts. |
| N08 | fixed_and_verified | An empty selected voice list could fall back to the default speaker. Reject invalid selections and definitely-not-sent empty destinations; explicit all remains explicit. | backend/app/services/notifications.py:1181 `_send_voice` | test_notification_recipient_binding.py::test_empty_selected_voice_does_not_fall_back_to_default_speaker | Baseline wfkderem failed through actual API/dispatch; corrected stw3rvc2 passed. |
| N09 | fixed_and_verified | Retained duplicate dependencies/raw test DBs and caches exhausted host storage. Shared read-only exact-lock reuse, pre-allocation/continuous guards, shared bulk lock and owned cleanup. | scripts/validation/storage.py; scripts/validation/validate.py; scripts/validation/runtime_pdf_smoke.py | test_storage.py; test_harness_configuration.py; test_source_snapshot.py | 70 host tests + guarded actual Linux frontend/build proof passed; focused independent corrections closed. |
| N10 | fixed_and_verified | The Linux runner could not traverse private PostgreSQL data owned by image UID999, so accounting and cleanup failed. Run synthetic PostgreSQL as the runner UID/GID, drop every capability and preserve0700 data permissions. | scripts/validation/validate.py:276 `container` | test_harness_configuration.py: test_container_user_matches_bind_owner_without_restoring_capabilities; native Linux/PostgreSQL proof; complete GitHub harness | Before:1host failure/30controls and real PG runner traversal rejected. After:31host passes, SQL/private permissions/traversal/cleanup pass. Actual Linux CI closure: ci-followup.json. |

## Ownership, behavior and extension

Before: routes retained stale account objects and could consume confirmation separately; sockets split responsibility between the route and bus; selected recipient positions could be rebound; discovery used a bounded newest-list as if it proved coverage. After: account mutation owns the complete authoritative commit; realtime connection owns child lifetime and send authorization; recipient binding owns current selection validity; discovery progress owns coverage and atomic candidate acceptance. Existing settings activation, notification delivery vocabulary, candidate/media leases, access execution and provider operation owners remain in charge of their existing effects.

Extend an account mutation through `user_accounts.py` after external hashing/photo work, preserving root-first lock order and exact session proof. Extend a dashboard hint by adding an explicit safe projection and matching typed invalidation consumer; do not expose internal receipts to standard users. Change recipient selection through the binding owner and deliberate editor selection, preserving revision on unrelated edits. New historical facts may extend the explicit safe captured schema; coverage advancement remains transactional and hardware suppression remains unconditional. Intentionally retained complexity protects cancellation, uncertainty, fresh authority and historical chronology.

## Migration and operator actions

`20261014_0017_notification_recipient_binding.py` disables ambiguous legacy selected rules and holds affected unfinished/captured work. It preserves action definitions, accepted/unknown receipts, counts and completed history. Manual recipient review/reselection is required before deliberately re-enabling affected rules. Explicit safe all-recipient rules remain supported. New/edited stale selected actions fail truthfully instead of being rebound.

`20261014_0018_historical_discovery.py` adds a bounded leased progress table without rewriting historical candidate receipts. Four raw pages, 32 records, 36 admitted list/detail/track reads, ten seconds of discovery and a seven-day queued-horizon hold bound work. Detail+track counts as two reads. The separate candidate drain retains its existing 32-item/media deadlines. Dense or malformed coverage is held; provider failure remains resumable. Detailed health exposes pending/held counts and sanitized reasons. Raw HTTP allocation, silently truncated responses, retention deletion and arbitrarily late insertion remain provider limitations; overlap is mitigation, not proof against them.

Both new downgrades refuse unsafe loss of review/coverage state. Restore the corresponding pre-upgrade database/files and matching application image when rollback requires going behind them. Existing calendar retirement guard is independently retained/tested at0016. Historical migrations/checksum fixtures were not rewritten. Production was not migrated or deployed for this task. An earlier separately approved isolation-only backend restart removed source exposure before the starting snapshot; no additional agent production restart followed. After eventual deployment, expired/revoked sessions must sign in again and live sockets reauthenticate; do not bypass those closes or blindly resend uncertain notifications.

## Commands, results and retained failures

Main final command: bundled Python3.12 `scripts/validation/validate.py --reuse-dependencies /private/tmp/iacs-integration-mutations-20261014/iacs-validation-focus-rstp6hsg --evidence-root /private/tmp/iacs-security-reliability-20261014`, with `IACS_VALIDATION_BACKEND_IMAGE=iacs-backend:uiprotect-23-backfill-verified-20261013`. Exact container arguments, environment and exit codes: `iacs-validation-1ts31y6o/checks.json`. Console: `full-ci-fix-console.log`. The log name is generated from one evidence key; it is included under the same name in the bundle.

| Check | Result / exit | Retained log |
| --- | --- | --- |
| python-lock-check | passed / 0 | `iacs-validation-1ts31y6o/python-lock-check.log` |
| python-identity | passed / 0 | `iacs-validation-1ts31y6o/python-identity.log` |
| uv-identity | passed / 0 | `iacs-validation-1ts31y6o/uv-identity.log` |
| frontend-lock-check | passed / 0 | `iacs-validation-1ts31y6o/frontend-lock-check.log` |
| frontend-tree-check | passed / 0 | `iacs-validation-1ts31y6o/frontend-tree-check.log` |
| npm-identity | passed / 0 | `iacs-validation-1ts31y6o/npm-identity.log` |
| isolation-db-free | passed / 0 | `iacs-validation-1ts31y6o/isolation-db-free.log` |
| snapshot-tests | passed / 0 | `iacs-validation-1ts31y6o/snapshot-tests.log` |
| harness-configuration-tests | passed / 0 | `iacs-validation-1ts31y6o/harness-configuration-tests.log` |
| storage-tests | passed / 0 | `iacs-validation-1ts31y6o/storage-tests.log` |
| architecture-boundaries | passed / 0 | `iacs-validation-1ts31y6o/architecture-boundaries.log` |
| compile | passed / 0 | `iacs-validation-1ts31y6o/compile.log` |
| workflow-contracts | passed / 0 | `iacs-validation-1ts31y6o/workflow-contracts.log` |
| backend-tests | passed / 0 | `iacs-validation-1ts31y6o/backend-tests.log` |
| ruff | passed / 0 | `iacs-validation-1ts31y6o/ruff.log` |
| test-names | passed / 0 | `iacs-validation-1ts31y6o/test-names.log` |
| mypy | passed / 0 | `iacs-validation-1ts31y6o/mypy.log` |
| typed-contracts | passed / 0 | `iacs-validation-1ts31y6o/typed-contracts.log` |
| frontend-tests | passed / 0 | `iacs-validation-1ts31y6o/frontend-tests.log` |
| frontend-build | passed / 0 | `iacs-validation-1ts31y6o/frontend-build.log` |
| compose-config | passed / 0 | `iacs-validation-1ts31y6o/compose-config.log` |
| postgres-start | passed / 0 | `iacs-validation-1ts31y6o/postgres-start.log` |
| redis-start | passed / 0 | `iacs-validation-1ts31y6o/redis-start.log` |
| isolation-persistence | passed / 0 | `iacs-validation-1ts31y6o/isolation-persistence.log` |
| isolation-inspect | passed / 0 | `iacs-validation-1ts31y6o/isolation-inspect.log` |
| migration-up | passed / 0 | `iacs-validation-1ts31y6o/migration-up.log` |
| migration-current | passed / 0 | `iacs-validation-1ts31y6o/migration-current.log` |
| migration-check | passed / 0 | `iacs-validation-1ts31y6o/migration-check.log` |
| database-restore-client-version | passed / 0 | `iacs-validation-1ts31y6o/database-restore-client-version.log` |
| database-restore-source-create | passed / 0 | `iacs-validation-1ts31y6o/database-restore-source-create.log` |
| database-restore-prepare | passed / 0 | `iacs-validation-1ts31y6o/database-restore-prepare.log` |
| database-restore-dump | passed / 0 | `iacs-validation-1ts31y6o/database-restore-dump.log` |
| database-restore-target-create | passed / 0 | `iacs-validation-1ts31y6o/database-restore-target-create.log` |
| database-restore-load | passed / 0 | `iacs-validation-1ts31y6o/database-restore-load.log` |
| database-restore-verify | passed / 0 | `iacs-validation-1ts31y6o/database-restore-verify.log` |
| database-restore-drop-0 | passed / 0 | `iacs-validation-1ts31y6o/database-restore-drop-0.log` |
| database-restore-drop-1 | passed / 0 | `iacs-validation-1ts31y6o/database-restore-drop-1.log` |
| schema-check-1 | passed / 0 | `iacs-validation-1ts31y6o/schema-check-1.log` |
| suite-001-create | passed / 0 | `iacs-validation-1ts31y6o/suite-001-create.log` |
| suite-001 | passed / 0 | `iacs-validation-1ts31y6o/suite-001.log` |
| suite-001-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-001-drop.log` |
| suite-002-create | passed / 0 | `iacs-validation-1ts31y6o/suite-002-create.log` |
| suite-002 | passed / 0 | `iacs-validation-1ts31y6o/suite-002.log` |
| suite-002-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-002-drop.log` |
| suite-003-create | passed / 0 | `iacs-validation-1ts31y6o/suite-003-create.log` |
| suite-003 | passed / 0 | `iacs-validation-1ts31y6o/suite-003.log` |
| suite-003-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-003-drop.log` |
| suite-004-create | passed / 0 | `iacs-validation-1ts31y6o/suite-004-create.log` |
| suite-004 | passed / 0 | `iacs-validation-1ts31y6o/suite-004.log` |
| suite-004-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-004-drop.log` |
| suite-005-create | passed / 0 | `iacs-validation-1ts31y6o/suite-005-create.log` |
| suite-005 | passed / 0 | `iacs-validation-1ts31y6o/suite-005.log` |
| suite-005-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-005-drop.log` |
| suite-006-create | passed / 0 | `iacs-validation-1ts31y6o/suite-006-create.log` |
| suite-006 | passed / 0 | `iacs-validation-1ts31y6o/suite-006.log` |
| suite-006-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-006-drop.log` |
| suite-007-create | passed / 0 | `iacs-validation-1ts31y6o/suite-007-create.log` |
| suite-007 | passed / 0 | `iacs-validation-1ts31y6o/suite-007.log` |
| suite-007-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-007-drop.log` |
| suite-008-create | passed / 0 | `iacs-validation-1ts31y6o/suite-008-create.log` |
| suite-008 | passed / 0 | `iacs-validation-1ts31y6o/suite-008.log` |
| suite-008-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-008-drop.log` |
| suite-009-create | passed / 0 | `iacs-validation-1ts31y6o/suite-009-create.log` |
| suite-009 | passed / 0 | `iacs-validation-1ts31y6o/suite-009.log` |
| suite-009-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-009-drop.log` |
| suite-010-create | passed / 0 | `iacs-validation-1ts31y6o/suite-010-create.log` |
| suite-010 | passed / 0 | `iacs-validation-1ts31y6o/suite-010.log` |
| suite-010-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-010-drop.log` |
| suite-011-create | passed / 0 | `iacs-validation-1ts31y6o/suite-011-create.log` |
| suite-011 | passed / 0 | `iacs-validation-1ts31y6o/suite-011.log` |
| suite-011-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-011-drop.log` |
| suite-012-create | passed / 0 | `iacs-validation-1ts31y6o/suite-012-create.log` |
| suite-012 | passed / 0 | `iacs-validation-1ts31y6o/suite-012.log` |
| suite-012-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-012-drop.log` |
| suite-013-create | passed / 0 | `iacs-validation-1ts31y6o/suite-013-create.log` |
| suite-013 | passed / 0 | `iacs-validation-1ts31y6o/suite-013.log` |
| suite-013-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-013-drop.log` |
| suite-014-create | passed / 0 | `iacs-validation-1ts31y6o/suite-014-create.log` |
| suite-014 | passed / 0 | `iacs-validation-1ts31y6o/suite-014.log` |
| suite-014-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-014-drop.log` |
| suite-015-create | passed / 0 | `iacs-validation-1ts31y6o/suite-015-create.log` |
| suite-015 | passed / 0 | `iacs-validation-1ts31y6o/suite-015.log` |
| suite-015-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-015-drop.log` |
| suite-016-create | passed / 0 | `iacs-validation-1ts31y6o/suite-016-create.log` |
| suite-016 | passed / 0 | `iacs-validation-1ts31y6o/suite-016.log` |
| suite-016-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-016-drop.log` |
| suite-017-create | passed / 0 | `iacs-validation-1ts31y6o/suite-017-create.log` |
| suite-017 | passed / 0 | `iacs-validation-1ts31y6o/suite-017.log` |
| suite-017-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-017-drop.log` |
| suite-018-create | passed / 0 | `iacs-validation-1ts31y6o/suite-018-create.log` |
| suite-018 | passed / 0 | `iacs-validation-1ts31y6o/suite-018.log` |
| suite-018-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-018-drop.log` |
| suite-019-create | passed / 0 | `iacs-validation-1ts31y6o/suite-019-create.log` |
| suite-019 | passed / 0 | `iacs-validation-1ts31y6o/suite-019.log` |
| suite-019-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-019-drop.log` |
| suite-020-create | passed / 0 | `iacs-validation-1ts31y6o/suite-020-create.log` |
| suite-020 | passed / 0 | `iacs-validation-1ts31y6o/suite-020.log` |
| suite-020-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-020-drop.log` |
| suite-021-create | passed / 0 | `iacs-validation-1ts31y6o/suite-021-create.log` |
| suite-021 | passed / 0 | `iacs-validation-1ts31y6o/suite-021.log` |
| suite-021-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-021-drop.log` |
| suite-022-create | passed / 0 | `iacs-validation-1ts31y6o/suite-022-create.log` |
| suite-022 | passed / 0 | `iacs-validation-1ts31y6o/suite-022.log` |
| suite-022-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-022-drop.log` |
| suite-023-create | passed / 0 | `iacs-validation-1ts31y6o/suite-023-create.log` |
| suite-023 | passed / 0 | `iacs-validation-1ts31y6o/suite-023.log` |
| suite-023-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-023-drop.log` |
| suite-024-create | passed / 0 | `iacs-validation-1ts31y6o/suite-024-create.log` |
| suite-024 | passed / 0 | `iacs-validation-1ts31y6o/suite-024.log` |
| suite-024-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-024-drop.log` |
| suite-025-create | passed / 0 | `iacs-validation-1ts31y6o/suite-025-create.log` |
| suite-025 | passed / 0 | `iacs-validation-1ts31y6o/suite-025.log` |
| suite-025-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-025-drop.log` |
| suite-026-create | passed / 0 | `iacs-validation-1ts31y6o/suite-026-create.log` |
| suite-026 | passed / 0 | `iacs-validation-1ts31y6o/suite-026.log` |
| suite-026-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-026-drop.log` |
| suite-027-create | passed / 0 | `iacs-validation-1ts31y6o/suite-027-create.log` |
| suite-027 | passed / 0 | `iacs-validation-1ts31y6o/suite-027.log` |
| suite-027-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-027-drop.log` |
| suite-028-create | passed / 0 | `iacs-validation-1ts31y6o/suite-028-create.log` |
| suite-028 | passed / 0 | `iacs-validation-1ts31y6o/suite-028.log` |
| suite-028-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-028-drop.log` |
| suite-029-create | passed / 0 | `iacs-validation-1ts31y6o/suite-029-create.log` |
| suite-029 | passed / 0 | `iacs-validation-1ts31y6o/suite-029.log` |
| suite-029-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-029-drop.log` |
| suite-030-create | passed / 0 | `iacs-validation-1ts31y6o/suite-030-create.log` |
| suite-030 | passed / 0 | `iacs-validation-1ts31y6o/suite-030.log` |
| suite-030-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-030-drop.log` |
| suite-031-create | passed / 0 | `iacs-validation-1ts31y6o/suite-031-create.log` |
| suite-031 | passed / 0 | `iacs-validation-1ts31y6o/suite-031.log` |
| suite-031-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-031-drop.log` |
| suite-032-create | passed / 0 | `iacs-validation-1ts31y6o/suite-032-create.log` |
| suite-032 | passed / 0 | `iacs-validation-1ts31y6o/suite-032.log` |
| suite-032-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-032-drop.log` |
| suite-033-create | passed / 0 | `iacs-validation-1ts31y6o/suite-033-create.log` |
| suite-033 | passed / 0 | `iacs-validation-1ts31y6o/suite-033.log` |
| suite-033-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-033-drop.log` |
| suite-034-create | passed / 0 | `iacs-validation-1ts31y6o/suite-034-create.log` |
| suite-034 | passed / 0 | `iacs-validation-1ts31y6o/suite-034.log` |
| suite-034-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-034-drop.log` |
| suite-035-create | passed / 0 | `iacs-validation-1ts31y6o/suite-035-create.log` |
| suite-035 | passed / 0 | `iacs-validation-1ts31y6o/suite-035.log` |
| suite-035-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-035-drop.log` |
| suite-036-create | passed / 0 | `iacs-validation-1ts31y6o/suite-036-create.log` |
| suite-036 | passed / 0 | `iacs-validation-1ts31y6o/suite-036.log` |
| suite-036-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-036-drop.log` |
| suite-037-create | passed / 0 | `iacs-validation-1ts31y6o/suite-037-create.log` |
| suite-037 | passed / 0 | `iacs-validation-1ts31y6o/suite-037.log` |
| suite-037-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-037-drop.log` |
| suite-038-create | passed / 0 | `iacs-validation-1ts31y6o/suite-038-create.log` |
| suite-038 | passed / 0 | `iacs-validation-1ts31y6o/suite-038.log` |
| suite-038-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-038-drop.log` |
| suite-039-create | passed / 0 | `iacs-validation-1ts31y6o/suite-039-create.log` |
| suite-039 | passed / 0 | `iacs-validation-1ts31y6o/suite-039.log` |
| suite-039-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-039-drop.log` |
| suite-040-create | passed / 0 | `iacs-validation-1ts31y6o/suite-040-create.log` |
| suite-040 | passed / 0 | `iacs-validation-1ts31y6o/suite-040.log` |
| suite-040-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-040-drop.log` |
| suite-041-create | passed / 0 | `iacs-validation-1ts31y6o/suite-041-create.log` |
| suite-041 | passed / 0 | `iacs-validation-1ts31y6o/suite-041.log` |
| suite-041-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-041-drop.log` |
| suite-042-create | passed / 0 | `iacs-validation-1ts31y6o/suite-042-create.log` |
| suite-042 | passed / 0 | `iacs-validation-1ts31y6o/suite-042.log` |
| suite-042-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-042-drop.log` |
| suite-043-create | passed / 0 | `iacs-validation-1ts31y6o/suite-043-create.log` |
| suite-043 | passed / 0 | `iacs-validation-1ts31y6o/suite-043.log` |
| suite-043-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-043-drop.log` |
| suite-044-create | passed / 0 | `iacs-validation-1ts31y6o/suite-044-create.log` |
| suite-044 | passed / 0 | `iacs-validation-1ts31y6o/suite-044.log` |
| suite-044-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-044-drop.log` |
| suite-045-create | passed / 0 | `iacs-validation-1ts31y6o/suite-045-create.log` |
| suite-045 | passed / 0 | `iacs-validation-1ts31y6o/suite-045.log` |
| suite-045-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-045-drop.log` |
| suite-046-create | passed / 0 | `iacs-validation-1ts31y6o/suite-046-create.log` |
| suite-046 | passed / 0 | `iacs-validation-1ts31y6o/suite-046.log` |
| suite-046-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-046-drop.log` |
| suite-047-create | passed / 0 | `iacs-validation-1ts31y6o/suite-047-create.log` |
| suite-047 | passed / 0 | `iacs-validation-1ts31y6o/suite-047.log` |
| suite-047-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-047-drop.log` |
| suite-048-create | passed / 0 | `iacs-validation-1ts31y6o/suite-048-create.log` |
| suite-048 | passed / 0 | `iacs-validation-1ts31y6o/suite-048.log` |
| suite-048-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-048-drop.log` |
| suite-049-create | passed / 0 | `iacs-validation-1ts31y6o/suite-049-create.log` |
| suite-049 | passed / 0 | `iacs-validation-1ts31y6o/suite-049.log` |
| suite-049-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-049-drop.log` |
| suite-050-create | passed / 0 | `iacs-validation-1ts31y6o/suite-050-create.log` |
| suite-050 | passed / 0 | `iacs-validation-1ts31y6o/suite-050.log` |
| suite-050-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-050-drop.log` |
| suite-051-create | passed / 0 | `iacs-validation-1ts31y6o/suite-051-create.log` |
| suite-051 | passed / 0 | `iacs-validation-1ts31y6o/suite-051.log` |
| suite-051-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-051-drop.log` |
| suite-052-create | passed / 0 | `iacs-validation-1ts31y6o/suite-052-create.log` |
| suite-052 | passed / 0 | `iacs-validation-1ts31y6o/suite-052.log` |
| suite-052-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-052-drop.log` |
| suite-053-create | passed / 0 | `iacs-validation-1ts31y6o/suite-053-create.log` |
| suite-053 | passed / 0 | `iacs-validation-1ts31y6o/suite-053.log` |
| suite-053-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-053-drop.log` |
| suite-054-create | passed / 0 | `iacs-validation-1ts31y6o/suite-054-create.log` |
| suite-054 | passed / 0 | `iacs-validation-1ts31y6o/suite-054.log` |
| suite-054-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-054-drop.log` |
| suite-055-create | passed / 0 | `iacs-validation-1ts31y6o/suite-055-create.log` |
| suite-055 | passed / 0 | `iacs-validation-1ts31y6o/suite-055.log` |
| suite-055-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-055-drop.log` |
| suite-056-create | passed / 0 | `iacs-validation-1ts31y6o/suite-056-create.log` |
| suite-056 | passed / 0 | `iacs-validation-1ts31y6o/suite-056.log` |
| suite-056-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-056-drop.log` |
| suite-057-create | passed / 0 | `iacs-validation-1ts31y6o/suite-057-create.log` |
| suite-057 | passed / 0 | `iacs-validation-1ts31y6o/suite-057.log` |
| suite-057-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-057-drop.log` |
| suite-058-create | passed / 0 | `iacs-validation-1ts31y6o/suite-058-create.log` |
| suite-058 | passed / 0 | `iacs-validation-1ts31y6o/suite-058.log` |
| suite-058-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-058-drop.log` |
| suite-059-create | passed / 0 | `iacs-validation-1ts31y6o/suite-059-create.log` |
| suite-059 | passed / 0 | `iacs-validation-1ts31y6o/suite-059.log` |
| suite-059-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-059-drop.log` |
| suite-060-create | passed / 0 | `iacs-validation-1ts31y6o/suite-060-create.log` |
| suite-060 | passed / 0 | `iacs-validation-1ts31y6o/suite-060.log` |
| suite-060-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-060-drop.log` |
| suite-061-create | passed / 0 | `iacs-validation-1ts31y6o/suite-061-create.log` |
| suite-061 | passed / 0 | `iacs-validation-1ts31y6o/suite-061.log` |
| suite-061-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-061-drop.log` |
| suite-062-create | passed / 0 | `iacs-validation-1ts31y6o/suite-062-create.log` |
| suite-062 | passed / 0 | `iacs-validation-1ts31y6o/suite-062.log` |
| suite-062-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-062-drop.log` |
| suite-063-create | passed / 0 | `iacs-validation-1ts31y6o/suite-063-create.log` |
| suite-063 | passed / 0 | `iacs-validation-1ts31y6o/suite-063.log` |
| suite-063-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-063-drop.log` |
| suite-064-create | passed / 0 | `iacs-validation-1ts31y6o/suite-064-create.log` |
| suite-064 | passed / 0 | `iacs-validation-1ts31y6o/suite-064.log` |
| suite-064-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-064-drop.log` |
| suite-065-create | passed / 0 | `iacs-validation-1ts31y6o/suite-065-create.log` |
| suite-065 | passed / 0 | `iacs-validation-1ts31y6o/suite-065.log` |
| suite-065-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-065-drop.log` |
| suite-066-create | passed / 0 | `iacs-validation-1ts31y6o/suite-066-create.log` |
| suite-066 | passed / 0 | `iacs-validation-1ts31y6o/suite-066.log` |
| suite-066-drop | passed / 0 | `iacs-validation-1ts31y6o/suite-066-drop.log` |

Final pytest suite summaries: `IACS_SECURITY_RELIABILITY_EVIDENCE.json`. Backend skips are accounted individually in `skip-accounting.json`; their exact passing persistence counterparts must exist before closure. No calendar-only test was removed in this task. The old SDK test was renamed to describe the preserved public browsing contract; actual recovery continuation now has real persistence tests. Existing historical concurrency mocks were migrated to the exclusive discovery lease while retaining one-event/media/no-hardware assertions.

Baseline/fault evidence: account initial5fail/3controls `iacs-validation-focus-wya4izf2`; expanded18fail/7controls `iacs-validation-focus-ylhm8rre`; socket baseline16 behavior failures `iacs-validation-focus-j3lerdiv`; recipient baseline5fail/1control `iacs-validation-focus-71z4stm1`; empty voice1fail `iacs-validation-focus-wfkderem`;301-event1fail/1control `iacs-validation-focus-nnbz43cm`. Earlier setup failures and interrupted runs remain retained and are not counted as behavioral proof. Expanded recovery run32kyc8dl had23passes/3fixture setup errors and11passes/1obsolete mock failure; corrected ppr2ae8n passed. First full6hizzaf3 exposed lint/type and an outdated schema-guard assertion, then was intentionally stopped and drained: cleanup0/source-integrity0/production unchanged. It is not a completed final run. Intermediate full34xxl1cr completed with only two window-query classifier assertion failures: the new bounded diagnostics LIMIT was incorrectly counted as a window page. The corrected test retains the page/materialization assertions and adds one-query/no-candidate-hydration checks. The final recovery mixed-filtering fixture also preserves the established negative-score exclusion after counting raw saturation; its pre-correction failure and final passing counterpart are retained.

Independent periodic-loop finding: reviewed8d2857 snapshot failed the real99-record regression (`iacs-validation-focus-lzgeg069`:1fail/1held control pass). Corrected c5bo524a:2pass. Final held control was strengthened to the first logical timestamp boundary and runs in the final harness. No source-extracted function is used for these claims.

Browser: `iacs-browser-8o965v08`,30Chromium/WebKit cases passed using locked cached image869763af..., network none and inert API/WebSocket fixtures. Screenshots/report retained under its artifacts/browser. Actual backend/API contracts are separately covered by PostgreSQL tests. Runtime status: `passed`; installed-image proof and source hash record: `runtime-final-reference.json`. No source/dependency overlays or application lifespan/provider startup are permitted for that proof.

## Measured authority overhead

Three synthetic accounts, five warmups,50samples, isolated PostgreSQL/ASGI,1CPU harness limit. Before and after use equivalent payloads and1/10connections. Fresh authorization intentionally adds queries; do not present this as a speedup. Timings are local proxies, not network/browser/render latency or database wire bytes. Artifacts: `performance-comparison.json`.

| Phase | Connections | Statements /50samples | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: |
| before | 10 | 0 | 0.090 | 0.096 |
| before | 1 | 0 | 0.021 | 0.028 |
| after | 10 | 1000 | 16.724 | 54.772 |
| after | 1 | 100 | 0.637 | 0.903 |

## Independent review and coverage limits

Requested primary gpt-6.1-sol/xhigh and reviewer gpt-6-astra/medium. Supported subagent delegation was used; effective model/effort metadata is unavailable, so neither identity is represented as verified. Actual token usage is unavailable. Checkpoint1 assessed ownership/design. Checkpoint2 found exact-session proof, repeated-cancellation and lifecycle gaps; all were reproduced, fixed, tested and reviewed at00447666.... N09 guard consultations corrected admission/probe ordering, accounting/traversal failures and cleanup truth; final targeted review2847e60... closed those fixes. Checkpoint3 inspected the exact task patch and local immutable source8d2857..., found the periodic overlap defect, then reviewed the overlap correction at3239f2905f0de5d97789642e02a99798a4c8a36754891c94502adb4da76f5ee7. The final three-file score-eligibility/window-query-test delta was reviewed at `f7d66850e18e8e05f600be877a21e2b4bcb4b6e83e3502f1daae6d0a8066ac04`; completed full/browser/runtime evidence was subsequently inspected for closure. No automatic deference: changes follow reproduced behavior and source evidence. Evidence closure: `True`; reports and snapshot/diff manifests are under `checkpoint3/`.

Source-reading coverage (separate from automated test coverage):

| Reading level | Paths/scope |
| --- | --- |
| Complete new application owners | user_accounts.py, realtime_connections.py, notification_recipient_binding.py, historical_discovery.py; both new migration bodies. |
| Complete affected existing owner | historical_candidates.py, including the extracted in-session reservation. |
| Partial existing files, complete affected paths | auth.py login/token paths; event_bus.py connection/Redis/lifecycle paths; notification_rules.py mutation/activation; notification_runs.py authorization/receipt checkpoints; notifications.py mobile/voice; Protect client/service raw collection, serialization, current-operation and lifecycle boundaries. |
| Partial historical recovery file | restart_backfill.py discovery worker, candidate extraction, drain, event transaction, duplicate matching and direction paths; unrelated later helpers were not all reread. |
| Frontend affected flows | Typed hint/invalidation, recipient model/selection/card and new browser/unit fixtures; unrelated views received static/regression coverage. |
| Validation | New storage owner and affected snapshot/runtime/shared-lock/cleanup paths reviewed; unchanged harness and remaining source are not an every-line certification. |

The changed authority, connection lifecycle, recipient-binding/dispatch and discovery/transaction/provider boundaries were reviewed. Supporting callers, migrations and frontend flows were reviewed where these changes depend on them. The entire repository is not certified or claimed fully read; unrelated providers, UI sections and business policies received regression/static coverage rather than an every-line audit. Live provider/hardware tests were deliberately not executed. Remaining risks are the provider completeness assumptions, bounded eventual revocation detection (idle2s; current check before send; authority timeout1s), possible conservative storage aborts, and operator review of held legacy recipients/coverage. Authorization checks do not make network byte delivery atomic with revocation.

Documentation checks: `final-documentation-checks.json` records291local links/command paths and `git diff --check` passing. Portable bundle verification: `bundle-inspection.json` and `patch-apply-verification.json` are retained at the evidence root; the archive contains its own member manifest and patch-apply receipt. Only delivery documents/archive assembly follow final code validation. Percentage re-estimates and the disk incident/pause/resume are retained in `progress.json`; they describe completed evidence rather than elapsed time.

## Linux CI follow-up N10

The failed Project Checks run38646126165 passed application/static/frontend checks before aborting with StorageViolation/PermissionError immediately after synthetic PostgreSQL startup; cleanup also failed. Old root startup changed data ownership to image UID999, preventing runner UID1001 from traversing0700 directories. The3-file fix uses the runner UID/GID and removes all PostgreSQL capability additions. No disk threshold or permission guard was weakened. Native Linux tmpfs proof retains real PostgreSQL SQL before/after, unchanged0700 permissions and expected runner traversal failure/pass; this is a targeted permission proof, not the full Linux bind-mount test. Full local harness plus actual GitHub run38648177324 provide that closure.

`ci-followup.json` records the exact commit, results and focused review. Focused review found no material issue atf7d66850..., while explicitly requiring the full Linux bind-mount/accounting/cleanup run. The original30browser cases remain applicable because all269frontend source hashes are unchanged; final full harness repeats frontend unit/build checks. Earlier deployment preparation built only a frontend image and performed isolated checks; production containers stayed unchanged and no migration ran. Its isolated Nginx configuration check lacked a backend DNS alias; that is preparation context, not an application defect or deployed change.

## Fresh reviewer checklist

1. Check baseline/final manifest and task-only patch, including new/untracked files; verify no pre-existing work is misattributed.
2. Read account mutation/auth owners with30real API cases; inspect last-Admin/confirmation/audit/cancellation boundaries.
3. Read realtime owner and event bus with actual ASGI/Redis faults and30browser cases; inspect payload projections and incomplete shutdown truth.
4. Trace selected Apprise revisions from editor/catalog through save/planning/pre-attempt dispatch; inspect populated0017 holds and accepted/unknown receipts.
5. Inspect raw recovery cardinality, interval/tie handling, atomic reservation/index and monotonic periodic cursor; read29discovery cases, historical suite and populated0018 preservation fixture.
6. Verify all35backend skip counterparts, full schema/restore checks, installed runtime proof, source parity, review closure and bundle patch-apply/hash receipts.
7. Before separate deployment, confirm disk headroom, tested matching backup and held-work review; never replay uncertain physical commands or notifications.
