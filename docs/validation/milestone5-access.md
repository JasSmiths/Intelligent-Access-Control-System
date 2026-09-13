# Milestone 5 — access decisions, execution, and enrichment

## Starting evidence and release boundary

The main checkout matches all 451 milestone 4 source hashes; live backend is the
verified milestone 4 image. Its phone test completed with Admin confirmation,
audit, one accepted action, and user-confirmed receipt. Baseline isolated run
`iacs-phase1-hk3htm33` passes. Work is in a separate checkout because production
bind-mounts main's backend source. No main edits, deployment, restarts, live
hardware commands, provider sends, or production data mutations in this task.

## Design and sequence

1. Add PostgreSQL regressions against unchanged application code first. Exercise
   normalized ingest, real policy/FSM, event/saga/visitor/presence transactions,
   the actual gate coordinator with a fake provider, durable notification enqueue,
   suppression, replay, and reconciliation. Capture synthetic latency before
   changing the order. Extend interruption/rollback tests at the new boundaries.
2. Separate normalized read/window data from evidence gathering. Evidence owns
   identity, schedule, presence/history and conditional camera tie-breaker queries.
   The existing MovementDirectionFSM remains the direction policy owner. A small
   pure access plan combines identity/schedule allowance with the resolved direction
   and the existing closed-gate requirement. No new parallel direction policy.
3. Put core event/saga/pass/presence/session persistence and audited hardware
   execution in one explicit access execution owner. Preserve the commit before
   hardware and the verified-outcome requirement for presence. Replays of an
   already committed movement reuse its identity and do not repeat hardware.
4. Run optional DVLA, visual attributes and snapshots after the core decision and
   physical outcome are durable. Persist only fields owned by each enrichment,
   preserving concurrent changes to unrelated fields. Failure of one optional
   stage does not prevent the others, notification enqueue or completed access
   reporting. Cancellation propagates; it cannot undo committed decisions or cause
   a physical retry. Do not create an untracked background task per read.
5. Keep final event/notification payloads enriched when enrichment succeeds. Visitor
   arrival/departure claims remain in VisitorPassService; later vehicle information
   updates also use that owner with a separate audit. Keep SnapshotManager,
   MovementSessionService, the ledger, reconciliation, GateCommandCoordinator and
   AccessDeviceService. Move callers/tests directly; no facade/compatibility layer.

## Intended behavior changes

Optional external enrichment no longer delays the first gate attempt or the core
presence commit. Enrichment failures are recorded independently and do not restart
access execution. Evidence that can change a direction (including camera tie-breaks)
stays before the decision; no claim is made that all external reads are optional.
The existing accepted-but-unverified gate path still waits for reconciliation.
Stored access payloads retain existing fields. Optional-stage failures are logged
with their stage and exception class; optional enrichment is not a durable job.
Visitor vehicle enrichment becomes a distinct audited update after arrival commit.

The notification durable boundary still starts at NotificationRun insertion, as
in milestone 4. This change does not introduce a transactional inbox/outbox for all
access producers, resumable optional enrichment, or automatic replay of historical
access/notification work. Document any remaining interruption windows explicitly.
Automation recovery remains a separately assessed follow-up; this is the roadmap's
access milestone, not permission to retry automation hardware actions.

## Acceptance and rollback

Full isolated before/after validation, including new files and targeted lint/type
checks. PostgreSQL assertions cover arrivals, departures, schedule/unknown denial,
open-gate suppression of commands, visitor links/audits, duplicate/OCR/session
suppression, ingest/finalization replay, rollback before hardware, interrupted
physical outcomes, reconciliation and optional-stage failures/cancellation.
Boundary tests prevent service/provider dependencies in pure policy and prevent
reintroduction of optional enrichment before durable execution. Compare identical
synthetic-delay timing measurements; do not claim production performance from them.

No new schema migration or historical rewrite is planned. A separately authorized
release is a backend image/source switch. Rollback uses the preserved milestone 4
image plus matching source; retain the milestone 4 additive schema and all records.
Do not roll back to an unmodified pre-milestone-4 image. Preserve unrelated main
work, retain exact forward/reverse patch evidence and verify production/container
metadata unchanged during implementation.


## Ownership and review changes

- `access_events.py` retains normalized ingest, queue/debounce, durable suppression
  and worker recovery. Read helpers move to `access/reads.py`.
- `AccessEvidenceResolver` owns database/camera evidence; `decision.py` combines
  eligibility and the closed-gate rule. The direction FSM is unchanged.
- `AccessExecution` owns event/saga/pass/ingest commit, audited gate/garage execution,
  and verified outcome/presence/session commit. Sessions now join the appropriate
  core transaction. Concurrent finalizers lock/reload the unique saga, and completed
  reconciliation cannot be overwritten by an older finalizer outcome.
- `AccessEnrichment` owns independent DVLA, visual, snapshot, visitor enrichment,
  zone shadow, presence synchronization, realtime, leaderboard and notification
  stages. Snapshot/DVLA/visual writes update only owned columns or JSON keys.
- `VisitorPassService.enrich_arrival` adds `visitor_pass.arrival_enriched` only when
  linked-arrival vehicle information changes. It cannot repeat the arrival transition.
- Simulation subclasses/patches and tests now target the actual owners. No shim
  methods, dynamic discovery or old import aliases are added.

Hardware failure notifications are still attempted immediately; they can contain
cached vehicle information and lack a newly captured snapshot/DVLA result, because
optional enrichment now runs later. Authorized-entry and anomaly notifications
retain successful enrichment. A failed realtime publication or notification enqueue
in the hardware helper cannot discard a durable command outcome; required audit
persistence failures still propagate and leave the saga to reconciliation.
External admission explicitly suppresses IACS hardware even if its supplied gate
observation is closed, preserving the unknown-plate hardware prohibition.

## Tests and limitations

Initial application baseline: `iacs-phase1-hk3htm33` (816 backend, 147 persistence).
Expanded unchanged-code baseline: `iacs-phase1-mnh02hvz`; source checkpoint
`c48ae07` retains the tests before the extraction. Two earlier fixture runs failed
because the fake gate observation key/state and required DVLA fields did not match
the current contracts. Correcting the fakes produced the passing baseline; no
application assertions were removed to obtain a pass.

Final manifest, JUnit results, exact revision, timing comparison, production
metadata and rollback evidence are retained with the milestone handoff. The harness
now includes each extracted module in targeted Ruff and mypy checks. Boundary tests
are in `backend/tests/test_access_architecture.py`; the PostgreSQL suite is in
`scripts/phase1/test_access_pipeline.py`. Existing camera/FSM, confirmation,
provider, notification, Alfred and frontend regressions remain enabled.

The measured samples use a fake 40 ms DVLA lookup and 40 ms snapshot capture, three
fresh resident-arrival cases per checkout. They isolate ordering effects. The
worker still awaits optional work; no live latency or throughput claim is made.
Cancellation after access commit can leave enrichment, presence input-boolean sync
or notification enqueue unfinished. These stages are not automatically replayed.
If interruption happens before a gate attempt, the pending movement is reconciled
as missing/unknown command evidence; it is never blindly actuated. Audit and stored
history remain intact. Notification recovery starts at the durable run insertion;
closing the producer-to-run gap would need a separately designed transactional outbox.

No migration was added, and the movement FSM, ledger, reconciliation, historical
backfill, gate coordinator, access-device service and provider implementations are
unchanged. Full process/power-loss and physical relay behavior remain live-release
validation concerns; isolated tests inject exceptions/cancellation at the durable
boundaries and reopen database sessions.
