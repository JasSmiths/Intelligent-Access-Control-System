# Resident missed-exit recovery

Recovery defaults to disabled. An Admin can configure the designated top
admission gate and its coordinates, enable recovery in Settings → Missed Exit
Recovery, then opt in each resident with one unique iPhone GPS `device_tracker`
and a unique `notify.mobile_app_*` destination. Configuration changes use the
existing confirmation and durable audit. iPhone location sharing must use exact
location. Home Assistant background updates are irregular: a missing/late update
refuses automatic recovery; it does not wait for an on-demand phone fix.

The LPR plate must be an exact trusted match. Fuzzy normalization never creates
new phone, camera recovery or resident-approval authority. Active vehicle/owner,
schedule, historical/replay exclusions, maintenance, gate malfunction, unresolved
movement/commands and the command coordinator's current gate policy remain
binding. Ordinary outgoing reads retain existing departure classification.

For a conflicting prior entry, the locally collected owner journey requires GPS
accuracy `(0,50]m`, a continuous observed away episode with at least two changed
coordinates spanning five minutes at least 500m away after uncertainty, then
three approaching updates within five minutes spanning 30 seconds and progressing
200m beyond uncertainty. No outward significant leg is allowed. The terminal
arrival update must be within 150m including uncertainty and at most 60 seconds
old at both capture and decision. That first arrival update is latched; subsequent
near-home updates cannot extend its life. A return requires a prior verified (or
legacy granted) entry for that exact vehicle before the away observations. One
journey is spent across all the owner's cars atomically with the new decision.

`last_updated` is the HA changed-coordinate update time, not guaranteed GPS
acquisition time. GPS and the phone's journey corroborate a return but do not
prove phone/vehicle co-travel. Bootstrap, unrelated attribute changes, malformed,
future, out-of-order and implausible updates cannot create fresh authority. Raw
coordinates are not retained: the bounded owner row keeps distances, accuracy,
timestamps and a coordinate hash. Connection epochs reset on authenticated
connect/disconnect, and the actual socket configuration fingerprint must match
current integration configuration before evidence is collected.

A ready journey skips camera and HA network requests during the access decision.
Otherwise recovery camera work has a five-second wall-clock budget. Late and
cancellation-resistant results are discarded. A clear entry resolves the conflict;
a clear departure follows exit handling; ambiguity creates an actual denied
access decision. Existing camera behavior for residents outside the feature is
unchanged.

An eligible unresolved denial reserves one owner-scoped **Allow entry** action.
The iPhone must unlock (`authenticationRequired`). The capability expires 120
seconds after the original plate capture, cannot be renewed by repeat reads and
cannot authorize another car. Duplicate same-car observations are correlated to
the canonical request; another owned car receives an explicit pending-request
refusal. The token is derived only for delivery; only its keyed hash is stored.
Owner, vehicle, schedule, destination, feature configuration, original provenance,
committed intervening movements and the exact frozen gate plan are revalidated
before effect. Resident confirmation creates a linked granted event and saga
while preserving the original denied observation and original capture timestamp.
Ordinary recognition still expires after 60 seconds; approval alone has separate
120-second authority. No missing exit or departure duration is fabricated.

Provider acceptance is not gate verification. Accepted/unverified commands remain
reconcilable and are never retried by recovery. An apology is reserved once only
after verified admission, including later reconciliation, and expires unsent after
five minutes. Notifications use the existing durable dispatcher and do not delay
gate opening. Restart and historical recovery never replay the command.

The Settings page exposes retained conflict evaluations, method, reason,
policy/check snapshot and safe timeline. Filters and bounded paging expose owner,
plate, outcome, method and dates. Detail links distinguish original/recovery
observations, saga and command. Notification queue/send/skip/review/action times
and gate delivery/verification are separate evidence. Missing timing values are
shown as not recorded. Coordinates, capability tokens and provider payloads are
excluded. Coalesced rows retain their own outcome and show canonical outcome
separately, keeping filter counts consistent. The page is read-only apart from
its confirmed feature configuration forms; audit records have no automatic purge.

## Implementation and validation

- `backend/app/services/resident_recovery.py`: eligibility, one-use resident
  actions, configuration checks, and durable recovery records.
- `backend/app/services/resident_recovery_evidence.py`: owner journeys, GPS
  thresholds, expiry, and connection provenance.
- `backend/app/services/recovery_tracker_discovery.py`: read-only Home Assistant
  tracker discovery for configuration.
- `backend/app/services/access/evidence.py` and `access/authorization.py`: camera
  budget and recognition/approval authority; `movement/admission.py`: verified
  admission and reconciliation follow-up.
- `backend/app/api/v1/missed_exit_recovery.py`,
  `frontend/src/api/missedExitRecovery.ts`, and
  `frontend/src/features/missedExitRecovery/`: API, typed client, and Settings UI.
- `backend/alembic/versions/20261002_0009_missed_exit_recovery.py`: durable schema.

Use the [network-none full harness](phase1.md). DB-free evidence and guard tests
accompany `scripts/phase1/test_resident_recovery.py` and
`scripts/phase1/test_resident_recovery_automatic.py` in its mandatory persistence
inventory. The harness also checks source selection, frontend tests/build, and
schema upgrade/comparison. These fixtures do not prove real iPhone delivery,
location acquisition, or physical gate behavior. Deployment, production
migrations, notification sends, and [supervised gate tests](../agent/hardware-safety.md)
require separate authorization; this guide makes no claim about deployed state.
