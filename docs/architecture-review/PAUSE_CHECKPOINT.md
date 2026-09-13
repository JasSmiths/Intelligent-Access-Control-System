# Paused recovery implementation — 13 September 2026

User explicitly requested pause. All agents stopped, all yielded processes were
polled to completion, and no task-owned containers or runner processes remain.
No implementation, test, deployment or scheduled continuation is running.

## Authoritative locations and scope

- Original daily-use checkout: `/Users/jas/Documents/Intelligent Access System`.
  Main remains at `69f9d8cfc1f77417a57105223f4e0772ae62de5d`. All488 captured
  source hashes and the complete initial working-tree status match the baseline
  (`evidence/source-preservation-pause.json`). Never apply work there casually.
- Work: `/private/tmp/iacs-recovery-7ggriel9/work`, branch
  `codex/architectural-recovery`. This includes the actual original dirty source.
  No commit/push/deployment/live integration action was performed.
- Evidence: `/private/tmp/iacs-recovery-7ggriel9/evidence`. Original evidence must
  remain immutable. A durable safe-source checkpoint is also recorded in
  `pause-snapshot.json` beside the evidence; it excludes credentials, runtime
  state, dependency trees and Git internals. It is a source backup, not a release.

## Last completed runtime validation

`notification-wa-wchwp8i6` used frozen source
`notification-correction-frozen-c2imivm1`, fingerprint
`7a4bfb41653f0307ca1201555161a8c50f2c23a868ca32aa1f2b16a68ca49187`.

- Confirmed notification PostgreSQL40/40 passed.
- Generic incoming store PostgreSQL22/22 passed.
- Concrete WhatsApp inbox PostgreSQL9/9 passed.
- Neutral visitor conversation/history PostgreSQL35/35 passed.
- Recovery-hold PostgreSQL7/7 passed.
- Shared messaging authority binding PostgreSQL7/7 passed.
- Unit selection325/326 passed. The remaining pending-timeframe test lacked model
  timestamps; its fake was corrected afterward but has not been rerun.
- Cleanup succeeded, production metadata was unchanged during the run and every
  captured source hash remained unchanged. Previous failures remain in their logs.
- Incoming frontend files are frozen with static checks only. Exact-lock frontend
  tests/build and final combined full validation remain unperformed on this state.
- Local/CI parity changes passed19 host-safe configuration tests and22 source
  snapshot tests. The expanded full harness has not yet been executed.

The prior backend test image disappeared locally. A repository-pinned Python base
was pulled; its interpreter3.12.14 and complete package inventory matched the
retained environment before the selected run. Original image receipts were not
rewritten. New dependencies were subsequently materialized from unchanged locks
under `locked-dependencies-g3g6x5w8`; all lock/package/tree checks and cleanup
passed. Use its recorded backend/node images and dependency receipt for the next
full harness, not the old unavailable image or a falsified receipt.

## Incomplete changes — do not release this working state

1. Discord is midway through cutover in `backend/app/services/discord_messaging.py`
   and new `backend/app/services/messaging/discord_incoming.py`. These edits have
   not even received a complete static/runtime review. Complete frozen channel
   preparation/config validation, actual connected-bot binding at every send, API
   confirmed test cutover, ephemeral interaction-map cleanup, reply claim-result
   checks, safe recovery projections, legacy helper deletion and real recovery
   tests. No schema change was added. Root owns final lifecycle/shared wiring.
2. Independent notification review NR-01 found accepted-plus-uncertain mobile
   fan-out was being represented as accepted, outside review. Partial root edits
   added delivery certainty to NotificationDeliveryError and HA translation, plus
   per-destination receipts in NotificationService. Dispatcher/store still need
   integration. The partial-failure receipt fields currently appear in logging
   but must also be carried in the returned outcome metadata. Required persisted
   shape: destination_outcomes [{target: apprise or HA service, delivery:
   accepted/not_sent/rejected/unknown}], known accepted evidence and truthful
   failed_count, with any uncertainty yielding review_required and no resend.
   Adapt the two old helper fakes to the new receipts keyword. No new NR-01
   persistence tests were written before pause.
3. NR-02 found an omitted announcement target could come from cached configuration
   while the journal bound newer settings. Root added configured_default and a
   fresh preparation-time mismatch rejection before confirmation consumption.
   Four actual PostgreSQL stale-cache/explicit-target/race cases remain unwritten.
   Existing40 confirmed tests have not run against these post-checkpoint edits.
4. NR-03 found ordinary prepared NotificationRule actions do not recheck current
   rule activation/definition. Add canonical rule identity/fingerprint only for
   ordinary rule-generated actions, recheck before unattempted send, and skip
   that rule's affected actions while allowing unrelated actions to continue.
   Explicitly confirmed inactive/unsaved rule tests retain their separate authority.
   No NR-03 implementation/tests were written before pause.
5. Actionable Home Assistant result notifications still need one durable
   transaction/audit/output owner; direct result sends in actionable_notifications
   remain. Do not broaden incoming/outgoing allowlist policies or transfer human
   approvals through shared conversation history.
6. Full-flow simulation still has one known failing pipeline case and an unresolved
   user decision: restrict simulation to isolated tests, or preserve production
   simulation and redesign its isolation. Do not quietly exclude its test or
   install an unapproved production-denying guard.
7. Final combined locks/build/type/lint/migrations/restore/lifecycle/architecture
   checks, cross-review, ownership/deletion documentation and task-only patch
   creation remain. Never generate the final patch against HEAD alone: original
   uncommitted changes are part of the preserved baseline.

## Resumption sequence

1. Recheck original and isolated fingerprints; inspect this checkpoint and
   IMPLEMENTATION_STATUS.md. Restore from the durable safe-source backup only
   if the temporary copy disappeared; do not overwrite the live checkout.
2. Complete current notification certainty/default/rule contracts and their
   tests, then Discord recovery. Reassign exclusive file allowlists explicitly.
   Root owns shared models, migration ancestry, main/lifecycle, common fixtures,
   notification owner and final integration.
3. Required harness manifest is scripts/phase1/recovery_checks.py. Add forthcoming
   test_notification_dispatch_truth.py and test_discord_inbox.py only when written.
   Their absence is not a passing test. Default full serializes every required
   PostgreSQL file into its own clean DB/Redis namespace and runs real restore.
4. Capture coherent source and run inspected harness with the new exact-lock
   dependency receipt and recorded image identities, explicit --include for new
   files, unique evidence directories and inert provider fixtures. Serialize runs.
5. Reconcile genuine failures rather than weaken assertions. Only a combined
   validated result can support completion. Deployment and live hardware remain
   separately authorized operations.
