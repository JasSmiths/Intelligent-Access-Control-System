# Milestone 2: shared schedule operations

## Boundary and starting evidence

Milestone 1's 50 changed files match its final SHA-256 manifest. HEAD remains
`69f9d8cfc1f77417a57105223f4e0772ae62de5d`; those uncommitted changes must be
preserved. A fresh isolated baseline at
`/Users/jas/Documents/IACS Regression Baselines/iacs-phase1-q96i0hwm` passes all
checks, including 782 backend, 12 persistence and 21 frontend tests.

Implement schedule create/update/delete first. Assignments and overrides are
separate review units with their own boundary design after this one. No changes
to schedule evaluation, live deployment, hardware calls, UI or data migrations.

## Create/update/delete design

Use `backend/app/services/schedule_operations.py` for canonical schedule values,
operation errors, persistence and durable CRUD audit. Keep evaluation and
existing dependency discovery in `services/schedules.py`. API and Alfred retain
transport-specific confirmation, name resolution, natural-language parsing and
presentation; both delegate their mutations to the shared operations.

- A nonempty trimmed name is required and must fit the existing 120-character
  column. Descriptions trim to text or null. Time blocks use the existing
  normalization/evaluation semantics and half-hour boundaries.
- An operation owns its schedule transaction: change plus successful CRUD audit
  commit together. Failure rolls back both. Audit includes the real requesting
  user ID and label, common action/target/diff, and explicit `api`/`alfred`
  provenance. Only active Admin users may mutate. Alfred resolves the actor from
  trusted request context; tool arguments cannot choose the audit actor.
- Update/delete reload and lock the schedule in the operation. Update merges
  explicit changes against the current persisted record before validation and
  audit. Duplicate names are conflicts; missing IDs are not-found; deletion of
  an assigned schedule is a conflict. Do not label unrelated database failures
  as duplicate names.
- Keep API server-confirmation payloads/tokens and response shapes intact.
  Existing PATCH behaves as replacement of name/description/blocks; preserve
  that API contract. Alfred update is partial and retains omitted fields.
- Preserve Alfred's create-time clarification when no allowed interval was
  supplied; the API permits a deliberately empty (deny-all) schedule. This is
  an explicit interaction difference, not duplicated persistence policy.
- Intentional corrections: blank/overlength names fail consistently, whitespace
  descriptions become null consistently, and Alfred gets the same transactional
  CRUD audit as the API. An Alfred description-only edit that contains no
  parseable schedule times retains existing blocks instead of silently clearing
  them. Confirmation consumption is still owned by its current
  channel and is not made atomic with mutation in this milestone.

## Acceptance for this review unit

Add adapter and PostgreSQL tests proving equivalent canonical fields/audit,
partial-update semantics, confirmation and role denial before mutation,
duplicate/not-found/in-use failures, canonical normalization, and rollback of
schedule plus audit on injected failure. Preserve the 84-tool catalog. Run the
full isolated harness with all new files included and compare production
metadata/cleanup. No provider accounts or live data are used.

No schema migration or historical record rewrite is required. Code rollback
must reverse only changes since this milestone's starting snapshot, preserving
milestone 1. Any future deployment requires separate authorisation.

## Assignment boundary design

CRUD validation passed in `iacs-phase1-owiuxin8` (782 backend, 48 persistence,
21 frontend tests; all project checks and production isolation passed). The CRUD
patch is retained separately before assignment implementation.

People and vehicle assignment are fields of broader directory transactions.
Use one `schedule_assignments.py` operation to validate/lock the referenced
schedule, set the target field, and stage a `schedule.assign` audit in the same
transaction. Directory callers retain their aggregate transaction and existing
person/vehicle audit. Alfred uses that operation and commits its standalone
assignment. Clearing a vehicle assignment continues to mean owner inheritance.

Device assignment must stay under `AccessDeviceService`. Its create/update and
standalone assignment paths will use the same assignment operation. Alfred
resolves current access-device records and calls that owner instead of writing
old Home Assistant settings. Schedule-target listings and door schedule checks
will read those same current devices. No device commands or provider I/O occur.

Intentional changes: nonexistent/malformed schedule IDs fail rather than being
silently cleared; the device UI sends an explicit empty string to clear an assignment (null
is omitted by the existing API confirmation protocol); successful
assignments receive a common durable audit with real actor and source. Empty or
unchanged assignments produce no extra assignment audit. Existing aggregate API
audit remains. Preview/confirmation flows and vehicle inheritance stay intact.

## Override boundary design

Assignment validation passed in `iacs-phase1-9xw6p5jo`: 790 backend and 70
persistence tests, 21 frontend tests and all other checks passed. Its patch is
retained separately. Additional inheritance/malformed-ID regressions will run
with final milestone validation.

There is one current override caller: Alfred. Extract a
`services/schedule_overrides.py` operation; do not invent another API or an
external agent server. The adapter keeps datetime parsing, current 60-minute
default/bounded duration, confirmation preview and display formatting. The
operation verifies an active Admin, an existing person, aware start time and a
1–1440-minute duration, then commits the override and `schedule.override.create`
audit together. It attributes the request to the trusted current user.

Keep the existing post-commit `schedule.override_created` realtime event.
Publication failure must not turn an already committed override into a failed
mutation; the durable row/audit are authoritative. This does not provide durable
realtime delivery or introduce background-work recovery. Failure before commit
rolls back both rows. No historical overrides are rewritten or automatically
replayed, and there are no migrations or hardware calls.

## Completed implementation and acceptance

Milestone 2 is complete in the working tree, as three independently reviewable
patches. No commits, production deployment, restarts, live hardware tests,
notification sends, migrations or historical data changes were performed.
Milestone 1's uncommitted changes are preserved.

| Check | Before | Final: `iacs-phase1-pefaobyc` |
| --- | --- | --- |
| Full backend suite | 782 passed | 790 passed |
| PostgreSQL persistence | 12 passed | 80 passed |
| Frontend suite | 21 passed | 21 passed |
| Snapshot selection | 5 passed | 5 passed |
| Frontend build, compilation, Compose parsing | Pass | Pass |
| Migration upgrade/current/schema comparison | Pass | Pass |
| Targeted Ruff/mypy, including new operation modules | Pass | Pass |
| Production container metadata | Captured | Unchanged throughout milestone |
| Task-created container cleanup | Complete | Complete |

All final tests passed without failures, errors or skips. All 84 tool definitions
still match the milestone 1 catalog fixture. The first CRUD implementation run
(`iacs-phase1-ga3uebi7`) caught an async-fixture setup error in the new standalone
persistence suite and a typed-dictionary inference error. Both were corrected;
existing application tests passed throughout. No assertions were removed to
obtain a pass. Subsequent CRUD, assignment and final runs passed.

The final validation directory contains copied source and SHA-256 manifest,
locked dependency installations, immutable image IDs, per-check logs, JUnit
results, before/after production metadata and cleanup proof. Only documentation
was completed afterward; code, tests, harness and CI still match the tested
snapshot. `git diff --check` also passes.

Final invocation (until new files are tracked):

```sh
python3 scripts/phase1/validate.py \
  --include backend/app/ai/context.py \
  --include backend/tests/contracts/fixtures/alfred/tool_catalog.json \
  --include backend/tests/contracts/test_alfred_catalog_contract.py \
  --include backend/tests/test_alfred_architecture.py \
  --include backend/tests/test_alfred_provider_contract.py \
  --include docs/architecture.md \
  --include docs/validation/milestone1-alfred.md \
  --include backend/app/services/schedule_operations.py \
  --include backend/app/services/schedule_assignments.py \
  --include backend/app/services/schedule_overrides.py \
  --include backend/tests/test_schedule_operations.py \
  --include docs/validation/milestone2-schedules.md
```

The harness automatically includes its new persistence test file under
`scripts/phase1/`. It never uses the production-selecting test wrapper.

## Behaviour changes and retained differences

- Schedule validation and CRUD audits now have one owner for API and Alfred.
  Blank/overlength canonical names fail; descriptions trim to null or text.
  API request-schema validation remains in front of the shared operation.
- Alfred description-only updates preserve times unless a schedule time was
  explicitly supplied or parsed from the description.
- Person, vehicle, gate and garage assignments use one validation/audit rule.
  Alfred's door targets, assignment and schedule checks use current access-device
  records, not the old settings-backed assignment path.
- The device UI can clear an assignment with a signed empty-string field.
  Invalid device schedule IDs are rejected instead of silently becoming null.
- Overrides now commit a durable audit with the row. A failed realtime publish
  logs the delivery failure but leaves the successful mutation outcome intact.
- API schedule PATCH remains replacement-style and Alfred remains partial.
  Alfred still asks for allowed times before creation; the API can deliberately
  create an empty deny-all schedule. Existing confirmation/presentation protocols
  remain separate. No new tool-result format or API endpoint was introduced.

## Limits and release boundary

Tests used fresh PostgreSQL, synthetic users/data and isolated networking; no
live provider accounts or production data were exercised. Provider construction
is forbidden in assignment tests. No performance improvement is claimed.

Confirmation consumption remains separate from the mutation transaction. The
existing directory confirmation protocol omits nulls; that protocol has not been
redesigned here. Device UI clearing uses an explicit non-null wire value.
Required background-work recovery, full access-transaction coverage and
historical database upgrade testing remain outside this milestone. Realtime
publication is best effort, and failed events are not automatically replayed.
The existing aggregate device-config audit outside the service transaction was
not redesigned; the new canonical schedule-assignment audit commits with the
assignment itself.

No migration/recovery job is needed for the new implementation. Existing
records, configuration and audit history remain in place. Live release requires
separate authorisation and its own deployment verification.

## Review artifacts and rollback

Artifacts are retained at:

`/Users/jas/Documents/IACS Regression Baselines/iacs-milestone2-eoc36s5f`

- `change-a.patch`: shared CRUD and its validation baseline.
- `change-b.patch`: shared assignments and device adapter correction.
- `change-c.patch`: override ownership, final regressions and documentation.
- `milestone2.patch`: combined milestone-only patch against the starting snapshot.
- Per-stage source/hashes, `handoff-manifest.json` and `patch-verification.json`.

Sequential patch application and reverse application are checked in a temporary
copy. Reversing the combined patch restores the milestone 2 starting source,
including milestone 1's changes, and preserves an unrelated sentinel file.

To reverse only milestone 2 in the current checkout, first preserve any later
work and review whether it overlaps. From the repository root:

```sh
git apply --reverse --check '/Users/jas/Documents/IACS Regression Baselines/iacs-milestone2-eoc36s5f/milestone2.patch'
git apply --reverse '/Users/jas/Documents/IACS Regression Baselines/iacs-milestone2-eoc36s5f/milestone2.patch'
```

Run the second command only if the first succeeds. Resolve overlapping later
edits manually; do not reset, clean or restore whole files indiscriminately.
Re-run isolated validation for the intended rollback source. A later deployed
release rolls back by restoring its prior code/images while retaining data and
configuration binds; never purge the new audit records or replay overrides.

## Next bounded milestone

Milestone 3 starts with shared visitor-pass create/update/cancel operations.
Confirm current API/Alfred behaviour and actor/audit contracts at that boundary,
then tackle notification and automation rules separately. Tool outcome/error
and presentation contracts receive their own design. No milestone 3
implementation has started.

## Complete milestone 2 changed-file inventory

- `.github/workflows/backend-alfred.yml`
- `AGENTS.md`
- `backend/README.md`
- `backend/app/ai/tool_groups/schedules_handlers.py`
- `backend/app/api/v1/access_devices.py`
- `backend/app/api/v1/directory.py`
- `backend/app/api/v1/schedules.py`
- `backend/app/services/access_devices.py`
- `backend/app/services/schedule_assignments.py`
- `backend/app/services/schedule_operations.py`
- `backend/app/services/schedule_overrides.py`
- `backend/tests/test_schedule_operations.py`
- `docs/agent/backend.md`
- `docs/architecture.md`
- `docs/validation/milestone2-schedules.md`
- `docs/validation/phase1.md`
- `frontend/src/views/SettingsViews.tsx`
- `scripts/phase1/test_schedule_operations.py`
- `scripts/phase1/validate.py`
