# Milestone 1: Alfred ownership and compatibility retirement

Completed against source revision `69f9d8cfc1f77417a57105223f4e0772ae62de5d`
on 11 September 2026. The checkout was clean before implementation. Changes
remain in the working tree; no commits, deployment, production restarts, live
hardware tests, or external notification sends were performed.

## Review units

The implementation is retained as three ordered patches, with per-stage source
snapshots and SHA-256 manifests, in:

`/Users/jas/Documents/IACS Regression Baselines/iacs-milestone1-ikl_01rk`

1. **Change A — trustworthy baseline:** `change-a.patch`. Add repeatable,
   validated `--include` source selection and five host-safe selection tests;
   capture the original 84-tool catalog without executable handler objects.
2. **Change B — explicit ownership:** `change-b.patch`. Make tool contracts and
   context independent of application imports; assemble the registry in one
   owner; replace implicit handler dependencies with explicit imports; move
   schemas to domain catalogs; retarget test doubles; enforce the boundaries.
3. **Change C — current provider contract:** `change-c.patch`. Remove signature
   fallback retries in both the common provider wrapper and V3 planner; update
   provider doubles; add 13 provider-contract tests; finish documentation.

The combined `milestone1.patch` is provided for complete review and a bounded
rollback. Patches do not require committing unrelated work. `patch-verification.json`
records sequential application, comparison with the final working-tree files,
and reverse-application checks. `handoff-manifest.json` inventories the final
changed files, hashes and evidence locations, including this report.

## Ownership and removed mechanisms

| Responsibility | Canonical owner |
| --- | --- |
| `AgentTool`, `ToolHandler`, safety/permission constants | `backend/app/ai/tools.py` |
| `CHAT_TOOL_CONTEXT`, get/set and token-based reset | `backend/app/ai/context.py` |
| `build_agent_tools()` and registry validation | `backend/app/ai/tool_groups/registry.py` |
| Names, descriptions, input definitions and domain schemas | Existing domain catalog modules under `backend/app/ai/tool_groups/` |
| Handler implementation and dependency use sites | The actual defining `*_handlers.py` module |
| Shared helper implementations | `backend/app/ai/tool_groups/_shared.py` |
| Current provider protocol and option normalisation | `backend/app/ai/providers.py` |
| Sessions, streaming, permissions, pending actions and orchestration | Existing chat and Alfred V3 services |

Removed: dynamic handler discovery, facade attribute forwarding, module-class
replacement, override propagation and supporting state, wildcard handler
imports, shared-module service/model re-exports, and old registry entry points.
Catalogs bind their defining handler module when constructed. Tests install
fakes at the dependency use site before constructing registries when needed.

Schedule, notification and automation input constants moved to their existing
tool catalog owners. The unused `AUTOMATION_RULE_PAYLOAD_SCHEMA` and unreferenced
provider helpers `_format_engine_capacity` / `_format_co2` were deleted.
No provider, business operation, stored record, schema or frontend feature was
removed. The ownership/retirement checklist is in [the architecture guide](../architecture.md).

## Behaviour and test changes

All 84 public tool definitions match the fixture captured from the untouched
baseline: names, descriptions, parameters, categories, permissions, confirmation
flags, examples, rate limits, limits and return metadata. `AgentTool` behaviour,
provider selection, response shapes and hardware owners are unchanged.

One intentional behaviour change: an incompatible provider signature or a
provider exception now fails the invocation without a reduced-argument retry.
Absent options (`None` and empty strings) are still omitted; supplied options,
including empty collections and zero, reach the provider unchanged. The V3
planner uses this same wrapper instead of maintaining another retry policy.
Existing caller failure handling remains responsible for the response/fallback.
This guarantee is per invocation, not a prohibition on separate planned calls
or existing answer-repair operations.

Two planned-read test assertions previously expected an empty tool catalog
because their outdated fake rejected efficiency options and the fallback
silently discarded every argument. Those assertions now require the actual
selected tools, model and request purpose. Their tool-execution and answer
assertions remain intact. The actionable-notification and iCloud doubles now
accept the current provider options while retaining their original assertions.
No tests were skipped or weakened to hide a failure.

Added coverage checks the complete catalog, direct handler ownership and
construction-time replacement, dependency boundaries, unset/nested/concurrent
actor contexts, restoration after exceptions/cancellation, and single-attempt
provider/planner failures. Existing confirmation, role, pending-action,
planning, streaming, memory, attachment and Admin-messaging tests still pass.

## Validation evidence

All results live under `/Users/jas/Documents/IACS Regression Baselines/`:

| Check | Before: `iacs-phase1-j96vg78u` | After: `iacs-phase1-uxmg1dzc` |
| --- | --- | --- |
| Full backend suite | 759 passed | 782 passed; no failures, errors or skips |
| PostgreSQL persistence suite | 12 passed | 12 passed |
| Frontend suite | 21 passed | 21 passed |
| Snapshot-selection tests | Not yet added | 5 passed |
| Frontend production build | Pass | Pass |
| Python compilation | Pass | Pass |
| Alembic upgrade/current/schema comparison | Pass | Pass |
| Targeted Ruff and mypy | Pass | Pass, including extracted contracts/context |
| Compose parsing | Pass | Pass |
| Production metadata comparison | Unchanged | Unchanged |
| Test-container cleanup | Complete | Complete |

Both migration checks reached `20260713_0002 (head)` and reported no new upgrade
operations. The older documented schema/index failures did not recur. No
migration changes were necessary or made. Locked Python/Node dependencies were
unchanged and installed into fresh isolated directories for each run.

The intermediate `iacs-phase1-xfriptkc` run passed all application tests but
identified import-order lint errors; these were corrected. The intermediate
`iacs-phase1-0cs7fvjc` run identified four outdated-provider-double assertions;
the cause and correction are described above. Their logs remain available.
There are no unexplained final failures or baseline exceptions.

Each run retains `manifest.json`, frozen source, dependency manifests/locks,
image identities, individual check logs, JUnit results, production-before/after
metadata and cleanup evidence. The catalog fixture was captured directly from
the untouched baseline source in a network-disabled disposable container; its
SHA-256 is `13881c2d58d3a57384bd47a9ca219271d18be10401aca3c73a9ff5e624d5ea11`.

The final isolated command was:

```sh
python3 scripts/phase1/validate.py \
  --include backend/app/ai/context.py \
  --include backend/tests/contracts/fixtures/alfred/tool_catalog.json \
  --include backend/tests/contracts/test_alfred_catalog_contract.py \
  --include backend/tests/test_alfred_architecture.py \
  --include backend/tests/test_alfred_provider_contract.py \
  --include docs/architecture.md
```

For an untracked re-run, also include this report with
`--include docs/validation/milestone1-alfred.md`. Once tracked, inclusions are
optional. After the successful run, only documentation was completed; application,
test, harness, CI and dependency files still match the tested source manifest.
The final handoff manifest covers those documentation updates as well.

Production container identities, images, mounts, start times and restart counts
match from the first baseline capture through final validation. All task-created
containers were removed. Retained source, dependencies, synthetic database files
and logs are deliberate evidence artifacts, not running test resources.

## Limitations and release boundary

Validation used copied source, synthetic configuration, fake providers and fresh
PostgreSQL/Redis storage. It did not exercise live provider accounts, hardware,
or production data, and does not establish which source revision the current
production images contain. Fresh migration checks do not prove upgrades from
every historical database state. Existing full access-transaction, concurrent
confirmation and interrupted-notification recovery gaps remain for their later
milestones. No performance improvement is claimed from this structural change.

Implementation acceptance does not authorise release. This milestone needs no
data migration. A separately authorised release must use its normal deployment
and verification procedure; do not run hardware or notification tests implicitly.

## Bounded rollback

Before a live release, preserve the current production image identities and
release configuration. Roll back a deployed milestone using those prior images
and the matching prior code, keeping existing configuration, data and audit
binds. Never reset the database or replay pending hardware/notification work.

For the current working-tree changes, first save any subsequent work separately.
From the repository root, check and reverse only the combined milestone patch:

```sh
git apply --reverse --check '/Users/jas/Documents/IACS Regression Baselines/iacs-milestone1-ikl_01rk/milestone1.patch'
git apply --reverse '/Users/jas/Documents/IACS Regression Baselines/iacs-milestone1-ikl_01rk/milestone1.patch'
```

Run the second command only if the first succeeds. If later edits overlap a
milestone hunk, stop and resolve those hunks while preserving the later work;
do not force, reset, clean or restore whole files indiscriminately. Review the
result and re-run isolated validation for the intended rollback source. The
reverse patch removes new milestone files and reverses only its recorded hunks;
unrelated files and non-overlapping changes remain. Nothing is rolled back live
by the implementation task.

## Next bounded milestone

Design shared schedule **create/update/delete** operations around the existing
API and Alfred callers. Inventory intentional differences, define equivalent
validation/persistence/audit outcomes, then make both callers thin adapters.
Assignments and overrides follow as separate changes. No milestone 2
implementation has started.

## Complete changed-file inventory

- `.github/workflows/backend-alfred.yml`
- `AGENTS.md`
- `README.md`
- `backend/README.md`
- `backend/app/ai/context.py`
- `backend/app/ai/providers.py`
- `backend/app/ai/tool_groups/_shared.py`
- `backend/app/ai/tool_groups/access_diagnostics.py`
- `backend/app/ai/tool_groups/access_diagnostics_handlers.py`
- `backend/app/ai/tool_groups/access_incident_handlers.py`
- `backend/app/ai/tool_groups/automations.py`
- `backend/app/ai/tool_groups/automations_handlers.py`
- `backend/app/ai/tool_groups/compliance_cameras_files.py`
- `backend/app/ai/tool_groups/compliance_cameras_files_handlers.py`
- `backend/app/ai/tool_groups/gate_maintenance.py`
- `backend/app/ai/tool_groups/gate_maintenance_handlers.py`
- `backend/app/ai/tool_groups/general.py`
- `backend/app/ai/tool_groups/general_handlers.py`
- `backend/app/ai/tool_groups/notifications.py`
- `backend/app/ai/tool_groups/notifications_handlers.py`
- `backend/app/ai/tool_groups/registry.py`
- `backend/app/ai/tool_groups/schedules.py`
- `backend/app/ai/tool_groups/schedules_handlers.py`
- `backend/app/ai/tool_groups/system_operations.py`
- `backend/app/ai/tool_groups/system_operations_handlers.py`
- `backend/app/ai/tool_groups/visitor_passes.py`
- `backend/app/ai/tool_groups/visitor_passes_handlers.py`
- `backend/app/ai/tools.py`
- `backend/app/services/alfred/planner.py`
- `backend/app/services/chat.py`
- `backend/tests/contracts/fixtures/alfred/tool_catalog.json`
- `backend/tests/contracts/test_alfred_catalog_contract.py`
- `backend/tests/contracts/test_alfred_v3_contracts.py`
- `backend/tests/test_actionable_notifications.py`
- `backend/tests/test_alfred_architecture.py`
- `backend/tests/test_alfred_provider_contract.py`
- `backend/tests/test_automations.py`
- `backend/tests/test_chat_agent.py`
- `backend/tests/test_chat_tool_context.py`
- `backend/tests/test_icloud_calendar.py`
- `backend/tests/test_notification_workflows.py`
- `backend/tests/test_telemetry.py`
- `backend/tests/test_visitor_passes.py`
- `docs/agent/backend.md`
- `docs/architecture.md`
- `docs/validation/milestone1-alfred.md`
- `docs/validation/phase1.md`
- `scripts/phase1/source_snapshot.py`
- `scripts/phase1/test_source_snapshot.py`
- `scripts/phase1/validate.py`
