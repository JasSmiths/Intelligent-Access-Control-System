# Milestone 3: feature mutations and Alfred contracts

## Boundary

Starting source is an isolated copy of all 437 verified milestone 2 files,
from base 69f9d8cfc1f77417a57105223f4e0772ae62de5d plus milestones 1 and 2.
The main checkout is mounted into production and remains untouched. Fresh
baseline iacs-phase1-zbwixact passed all checks (790 backend, 80 persistence,
21 frontend and 5 snapshot tests). Production metadata was unchanged.

Implement four sequential review units:

1. Visitor passes: retain VisitorPassService as the canonical create/update/cancel
   transaction participant, including calendar and visitor-sandbox callers.
   API and Alfred retain confirmation, input interpretation and presentation.
   Attribute Alfred mutations to the actual active Admin. Make explicitly empty
   phone updates consistent, and make realtime failures after commit independent
   from the successful mutation and existing optional outreach. Preserve calendar,
   lifecycle, privacy and abuse rules. Prove persisted fields/audit parity and
   rollback; do not invent another CRUD owner where one already exists.
2. Notification rules: move duplicated API/Alfred CRUD into notification_rules.py.
   The owner validates the complete merged rule, requires an active Admin and
   commits mutation plus audit atomically. Both channels use the existing full
   normalizer (including trigger/action coupling); reject empty/overlength names
   and missing actions consistently. Lock updates/deletes. Preview and send/test
   execution remain with NotificationService, outside this CRUD change.
3. Automation rules: retain AutomationService CRUD transaction participants.
   Remove pre-normalization/validation duplication from Alfred. Validate names
   once without truncating them, require real Admin actors, preserve webhook
   hardening and scheduler computation. Existing adapters commit audit and rule
   together; dispatch/recovery is outside this milestone.
4. Alfred contracts: introduce one execution outcome contract, shared input
   validation and catalog-owned presentation metadata. Keep domain output data
   and current chat/confirmation wire shapes readable; generic orchestration
   consumes metadata instead of adding feature-name branches. Explicit failure
   results must never emit a succeeded status or successful confirmation text.
   Test ordinary, failed, clarification and confirmation outcomes, and adding
   a synthetic operation without editing chat orchestration.

No schema migration, historical rewrite, notification replay, live hardware test,
provider removal, UI redesign or automatic deployment. Test notifications use
fakes only. Domain errors retain channel-specific HTTP/friendly presentation.
Each code change has isolated validation and a retained patch before proceeding.

## Completed handoff

Milestone 3 is implemented in an isolated checkout, with four sequential review
commits after the captured milestone 2 source. Production and the main checkout
remain unchanged. The durable checkout, Git bundle, individual change patches,
combined patch and SHA-256 handoff manifest are retained at:

`/Users/jas/Documents/IACS Regression Baselines/iacs-milestone3-isy14wkw`

Assessed original revision: `69f9d8cfc1f77417a57105223f4e0772ae62de5d`, plus
milestones 1/2. The source snapshot commit in this isolated repository is
`22a3c96`; it is a checkpoint, not an assertion that production ran that Git commit.

### Validation

Final isolated run: `/Users/jas/Documents/IACS Regression Baselines/iacs-phase1-0z7po4y2`.

- 813 backend tests; 122 PostgreSQL persistence tests; 21 frontend tests;
  5 source snapshot checks. No failed, errored or skipped tests.
- Frontend build, Python compilation, fresh migrations/current revision/schema
  comparison, Compose parsing and targeted Ruff/mypy all pass. New contract and
  operation modules are included in targeted checks.
- All 84 original tool definitions preserve names, descriptions, schemas,
  permissions and confirmation requirements. An additional explicit fixture
  covers the new execution/presentation metadata and callback owners.
- All 437 main-checkout baseline file hashes match. Production container IDs,
  image IDs, mounts, start times and restart counts match across the entire task.
- Every harness run cleaned up its disposable containers, including failed runs.
  Focused unit/lint checks used network-disabled disposable containers.
- Only documentation was completed after the final run; code/tests match its
  manifest. No live provider, hardware or notification test was run.

Acceptance evidence for review units: visitor-pass success `xxbmo0jm`, notification
success `0hviul3y` plus the subsequent focused lint pass, automation success
`lnqbj38j`, and combined contract/final success `_88fxos9`, `ewxkdov9`, `0z7po4y2`.
Retained intermediate failures: `fcwolsy5` caught an unintended sync actor edit,
a null-omission test assumption and lint; `ghtkrogb` exposed an unloaded database
update timestamp plus typing/lint; `0hviul3y` had only the subsequently fixed
preview catch lint annotation; `1qc8vy60` used an unsupported action in a new
fixture; `g8ovkhvq` caught a generated syntax error. All were resolved. Existing
assertions were preserved; fixtures/doubles were updated to the actual owners.

### Resulting ownership and intentional changes

- VisitorPassService remains the single pass mutation/audit participant. Alfred
  uses a real active Admin identity rather than the fixed Alfred_AI actor label.
  An explicitly empty phone clears it, matching API behaviour. A failed post-commit
  realtime publication cannot invalidate a saved pass or prevent optional outreach.
- notification_rules.py owns full-rule normalization, active-Admin checks,
  locked partial updates/deletes and atomic mutation/audit commits. Alfred CRUD
  now has the same durable audit as API, with explicit source provenance. Names
  must contain 1–160 characters; missing actions/trigger fail consistently.
- AutomationService remains the mutation/audit participant. Alfred passes raw
  fields to it. Blank/overlength names fail rather than defaulting/truncating.
  Real Admins are required. Edits/deletes lock/reload the record. Updating actions
  alone persists webhook hardening computed by the existing policy helper.
- Optional notification previews and automation dry-runs cannot turn an already
  saved rule into a failed mutation outcome.
- `ai/tool_inputs.py` validates the catalog's explicit schema subset before each
  tool invocation. Unsupported schema constraints fail catalog assembly. Inputs
  are not coerced; invalid boolean confirmations and nested values fail before
  handler invocation, with field-only error messages.
- `ai/tools.py` owns ToolOutcome/ToolError: `succeeded`, `failed`,
  `requires_confirmation`, `requires_details`, with nullable error code/message.
  Executions expose outcome alongside existing domain output. Exceptions,
  timeouts and unknown tools have explicit error codes; no retries are added.
  Failure flags inform streaming, audit and confirmation summaries consistently.
- Feature catalogs own progress labels, success flags, confirmation summaries,
  button labels and completion policy. Four chat presentation methods now consume
  metadata. Adding a synthetic operation is tested without editing those methods.
  Existing specialised wording stays in its catalog; automation confirmation
  summaries now identify the operation and automation instead of generic completion.

### Limits

No migrations, historical data rewrites or output-data deletions. Visitor calendar,
visitor-sandbox, lifecycle and privacy rules stay under their existing owners.
Alfred still interprets local dates and rejects elapsed creation windows; API
input schemas and existing confirmation payload/null rules remain unchanged.
Visitor and automation services remain transaction participants: callers must
commit or roll back their whole transaction. Existing background dispatch paths
are outside this milestone. Read-only lookup with no matches is a successful
lookup, not a mutation failure.

The existing `return_schema` is descriptive answer metadata, not JSON Schema
validation of all domain output values. The execution envelope is typed and
checked; this work does not claim every old payload is fully schema-validated.
Confirmation-token consumption remains separate from the mutation transaction.
No background delivery recovery, automatic replay, external agent server or
performance improvement is claimed. Live acceptance is pending a separate release.

### Release and rollback

The main checkout is unchanged. Before releasing, confirm its current source
still matches, preserve the **currently running milestone 2** backend image and
baked source for rollback, then review/apply the combined patch and build the
backend. No frontend implementation changed. Because production has a live source
bind mount, applying the patch is part of release, not a background staging step.
Never assume an older release's milestone 1 rollback image is the milestone 3
rollback target.

Preflight (read-only; also verified during handoff):

```sh
git apply --check '/Users/jas/Documents/IACS Regression Baselines/iacs-milestone3-isy14wkw/milestone3.patch'
```

After release, source rollback reverses only milestone 3:

```sh
git apply --reverse --check '/Users/jas/Documents/IACS Regression Baselines/iacs-milestone3-isy14wkw/milestone3.patch'
git apply --reverse '/Users/jas/Documents/IACS Regression Baselines/iacs-milestone3-isy14wkw/milestone3.patch'
```

Use the reverse command only if its check succeeds; handle any later overlapping
work explicitly. Restore the preserved milestone 2 image **and source mount**,
then recreate the backend and verify health/read-only UI/Alfred paths. Keep data,
configuration and audit mounts. No database downgrade, deletion or replay.
Patch application and reversal are verified in disposable source copies, with
an unrelated sentinel retained. No reset, clean or whole-file restoration is needed.

### Next bounded milestone

Design recoverable notification runs: durable claims/completion, interruption and
partial delivery handling, concurrency and duplicate protection. Surface historical
unfinished runs for review; distinguish never attempted from provider outcome unknown.
Define migration/recovery/rollback before changing persistence. Then assess
background automation dispatch against the same guarantees.

### Complete milestone 3 changed-file inventory

- `.github/workflows/backend-alfred.yml`
- `AGENTS.md`
- `README.md`
- `backend/README.md`
- `backend/app/ai/tool_groups/access_diagnostics.py`
- `backend/app/ai/tool_groups/automations.py`
- `backend/app/ai/tool_groups/automations_handlers.py`
- `backend/app/ai/tool_groups/compliance_cameras_files.py`
- `backend/app/ai/tool_groups/gate_maintenance.py`
- `backend/app/ai/tool_groups/general.py`
- `backend/app/ai/tool_groups/metadata.py`
- `backend/app/ai/tool_groups/notifications.py`
- `backend/app/ai/tool_groups/notifications_handlers.py`
- `backend/app/ai/tool_groups/registry.py`
- `backend/app/ai/tool_groups/schedules.py`
- `backend/app/ai/tool_groups/system_operations.py`
- `backend/app/ai/tool_groups/visitor_passes.py`
- `backend/app/ai/tool_groups/visitor_passes_handlers.py`
- `backend/app/ai/tool_inputs.py`
- `backend/app/ai/tools.py`
- `backend/app/api/v1/automations.py`
- `backend/app/api/v1/notifications.py`
- `backend/app/api/v1/visitor_passes.py`
- `backend/app/services/alfred/executor.py`
- `backend/app/services/automations.py`
- `backend/app/services/chat.py`
- `backend/app/services/mutation_context.py`
- `backend/app/services/notification_rules.py`
- `backend/app/services/visitor_passes.py`
- `backend/tests/contracts/fixtures/alfred/tool_execution_contract.json`
- `backend/tests/contracts/test_alfred_catalog_contract.py`
- `backend/tests/test_alfred_architecture.py`
- `backend/tests/test_alfred_execution_contract.py`
- `backend/tests/test_feature_operation_boundaries.py`
- `backend/tests/test_notification_workflows.py`
- `docs/agent/backend.md`
- `docs/architecture.md`
- `docs/validation/milestone3-operations.md`
- `scripts/phase1/test_feature_operations.py`
- `scripts/phase1/validate.py`
