# Architecture recovery and extension guide

IACS remains one application with explicit feature ownership. Alfred V3 is the
only supported Alfred runtime. Preserve durable operational history when
retiring implementation code; use Git history instead of compatibility shims.

## Current Alfred owners

| Responsibility | Owner |
| --- | --- |
| Tool contracts and safety constants | `backend/app/ai/tools.py` (standard library only) |
| Input contracts | `backend/app/ai/tool_inputs.py` (standard library only) |
| Request-scoped actor context | `backend/app/ai/context.py` (standard library only) |
| Catalog assembly and metadata validation | `backend/app/ai/tool_groups/registry.py::build_agent_tools` |
| Tool descriptions, schemas and direct handler bindings | Domain catalog modules in `backend/app/ai/tool_groups/` |
| Tool execution and domain delegation | Explicitly imported `*_handlers.py` modules |
| Actual shared helper functions | `backend/app/ai/tool_groups/_shared.py` |
| Provider protocol and one-call option normalisation | `backend/app/ai/providers.py` |
| Chat/session orchestration | `backend/app/services/chat.py` and `services/alfred/` |

Contract/context modules must not import registries, handlers, providers, or
application services. The shared helper module is not a service/model import
facade. Patch a handler's dependency use site in tests or use an existing
injection point. Build the catalog after installing doubles if it captures them.
Do not adapt production signatures to historical test doubles. The V3 planner
and chat both use `complete_with_provider_options`; signature errors propagate
through existing failure handling without dropping request options or retrying.

## Ownership and retirement checklist

1. Identify the feature and operation owner before editing. Reuse existing
   domain rules and audited hardware services.
2. Keep protocol/UI/Alfred presentation outside shared business operations.
   Make dependencies explicit; add abstractions only to remove real duplication.
3. Add catalog metadata and tests at the feature owner. Document intentional
   contract/behaviour changes separately from structural moves.
4. For deletion, check production imports, registered callbacks/catalogs,
   tests, configuration consumers, and documentation. Unreferenced code is a
   candidate, not permission to remove a rarely used feature or recovery path.
5. Move current callers and tests directly to the owner; delete the obsolete
   implementation and aliases together. Keep operational records and supported
   provider protocols intact.
6. Run the [isolated harness](validation/phase1.md), including untracked files.
   Record source hashes, results, limitations and rollback. Deployment is separate.

## Programme boundaries

Milestone 1 established explicit Alfred ownership. Milestone 2 shares schedule
CRUD, assignment and override operations. Their implementation steps were separate from deployment and did not migrate
historical data. A separately authorised release on 12 September 2026 deployed
the combined milestone 1/2 source and passed live read-only checks. See the [schedule handoff](validation/milestone2-schedules.md).

Schedule operations own canonical validation and audit. API/Alfred adapters keep
confirmation and presentation. Assignments participate in directory/device
aggregate transactions; device assignment remains under `AccessDeviceService`.
Do not create a parallel schedule implementation in an adapter.

Milestone 3 shares visitor-pass, notification-rule and automation-rule mutation
contracts, with tool input/outcome/presentation contracts reviewed separately. Later checkpoints cover recoverable notifications, access
decision/execution boundaries, and frontend ownership/retirement. Each requires
its own bounded design and acceptance criteria.

The current tool-catalog fixture is a preservation baseline, not proof that
all existing result metadata is a validated output schema. Execution outcome and input validation now have explicit contracts; descriptive
answer metadata is still not a domain output validator. Historical unfinished notifications remain review-only under the milestone 4 recovery owner.

## Milestone 3 ownership

VisitorPassService and AutomationService retain shared policy/audit ownership.
Notification rule mutations now use services/notification_rules.py. Realtime and
optional previews happen independently of committed mutation success.

Every Alfred result has an execution outcome alongside its domain output. Tool
catalogs own progress labels, successful-result flags, confirmation button/summary
callbacks and completion policy. Adding an operation requires a handler, catalog
entry and tests, without presentation-name branches in chat. Unsupported input
schema constraints fail registry construction instead of being silently ignored.

Milestones 3 and 4 were separately deployed and verified on 12 September 2026.
The milestone 4 phone notification test was received by the user. Milestone 5 was
then separately deployed and verified. Milestone 6 is implemented in isolation
because production uses a source bind mount; its release remains separate.

## Notification recovery ownership

`notification_runs.py` owns durable plans and fenced claims. `notification_dispatch.py`
owns polling, synchronous reservations and recovery. `notifications.py` owns
rendering and provider delivery. Realtime is optional wakeup/enrichment. The former
uncheckpointed process/execute loops are removed, and Home Assistant degradation,
visitor event adapters and gate notification outbox delivery use the durable owner.

Only a pending action can start. An attempting action with an expired claim becomes
unknown and requires review. Accepted/skipped actions never replay. Historical
unfinished rows remain intact and review-only. Admin read-only endpoints surface
that queue. Provider internals can fan out: checkpointing is per configured action,
not a claim of endpoint-level exactly-once delivery.

See [milestone 4](validation/milestone4-recovery.md) for intentional behaviour changes,
migration and the special code rollback requirements. Automation dispatch is assessed
there separately; never generalize safe notification preparation recovery into
hardware-action retries.


## Access ownership after milestone 5

| Stage | Owner | Extension boundary |
| --- | --- | --- |
| Durable ingest, debounce, suppression and worker recovery | `services/access_events.py` | Keep read lifecycle here; delegate finalization. |
| Normalized windows and read metadata | `services/access/reads.py` | One parser for shared read metadata. |
| Identity, schedule and direction evidence | `services/access/evidence.py` | Add evidence here; MovementDirectionFSM remains the policy for direction. |
| Access plan | `services/access/decision.py` | Pure eligibility/decision/hardware requirement, without service or database I/O. |
| Core transactions and audited execution | `services/access/execution.py` | Existing ledger/session/pass/presence and hardware owners; commit before command. |
| Optional enrichment and reporting | `services/access/enrichment.py` | Fresh short transactions for owned fields; failures isolated, cancellation propagated. |
| Gate/garage dispatch | `services/access/hardware.py` | GateCommandCoordinator and AccessDeviceService remain mandatory. |
| Payloads and snapshots | `services/access/payloads.py`, `services/access/snapshots.py` | Reuse formats and SnapshotManager. |

The former finalizer combined evidence, external enrichment, persistence and
hardware. Optional enrichment now follows durable execution; no facade aliases
were retained. Visitor state and vehicle-information updates share VisitorPassService,
with arrival linking and later enrichment separately audited. A committed access
identity cannot be finalized into a second event, and a fresh saga is loaded for
outcome persistence so an intervening reconciliation is retained.

The worker still awaits enrichment/reporting for each read. This is stage ownership
and fault isolation, not a new durable enrichment queue or a guarantee of improved
queue throughput. Notifications are recoverable only once their run is inserted.
See [milestone 5 evidence and limits](validation/milestone5-access.md).

## Frontend ownership after milestone 6

Schedule CRUD/confirmation HTTP contracts live in `frontend/src/api/schedules.ts`;
`features/schedules/` owns its list, form, grid and interval model. Workflows have
separate automation/notification route entries and editors, with one shared list,
selection/template UI and request lifecycle. No aggregate export facade remains.

`app/realtimeRefresh.ts` maps events to resources and route consumers.
`useShellRefresh.ts` owns typed reads and cancellation at account/route boundaries;
`refreshCoordinator.ts` serializes batches and retains trailing invalidations.
Manual/reconnect refresh covers the full active route. Camera choices are loaded
only by an open notification editor that can use media.

See [milestone 6 ownership, retirement and acceptance](validation/milestone6-frontend.md).
This completes implementation of the planned six-stage roadmap; release verification
and a programme review remain separate. Future work should begin with a specific
remaining ownership/recovery gap, not another broad mechanical split.
