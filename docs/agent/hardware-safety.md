# Hardware safety

Read this before gate, garage, cover, HA/ESPHome provider, LPR effects,
reconciliation or live validation work.

## Authorization boundary

Source inspection and isolated fake-provider tests are the default. Do not issue
live commands without a user-requested supervised test and explicit local
confirmation in the current session. Configuration changes, integration tests,
maintenance, announcements and notification sends also require their Admin
confirmation and durable audit. A request to edit or test code does not authorize
these effects or production deployment/migrations.

Never bypass IACS to actuate through Home Assistant, ESPHome, UniFi or another
vendor API. An accepted, pending or ambiguous command must not be repeated.
Do not retry uncertain effects, delete command/audit rows, or mark success without
valid current verification evidence. If repair has no current owner path, stop
and report the proposed mutation before considering raw SQL.

Never expose secrets, cookies, credentials, capability tokens or raw provider
payloads. Keep diagnostics bounded and redacted.

## Command owners and evidence

| Operation | Owner | IACS API | Durable evidence |
| --- | --- | --- | --- |
| Gate open | `GateCommandCoordinator`, `backend/app/services/gate_commands.py` | `POST /api/v1/integrations/gate/open` | `gate_command_records` and audit |
| Garage/cover | `AccessDeviceService`, `backend/app/services/access_devices.py` | `POST /api/v1/integrations/cover/command` | `access_device_command_records` and audit |
| Target configuration/preview | `backend/app/services/access_device_configuration.py` | `/api/v1/access-devices` | Confirmed configuration and frozen target plans |

The gate adapter is `backend/app/modules/gate/access_devices.py`; HA and ESPHome
providers are under `backend/app/modules/access_devices/`. All API and workflow callers use these same
owners and returns `requires_confirmation` before state-changing execution.

Inspect authenticated Admin receipts through `GET` routes under
`/api/v1/integrations/gate/commands` and `/api/v1/integrations/cover/commands`,
including `/{command_id}`. Delivery, acceptance, verification and reconciliation
are separate facts. Realtime status is useful context, not durable audit history.

## LPR and recovery invariants

- Validate `X-IACS-LPR-Token` and the configured source IP/CIDR allowlist before
  durable effects. Unknown plates never actuate hardware.
- Maintenance accepts/ignores LPR webhook input and clears queues; it must not
  produce access decisions, presence or hardware commands from those reads.
- Commit the access/movement decision before dispatch. Recheck current identity,
  schedule, target plan and recognition expiry at the command attempt.
- Automatic garage fanout requires verified gate admission, the existing `fanout`
  policy and applicable target/schedule authorization; acceptance alone is
  insufficient (`backend/app/services/access/hardware.py`).
- Suppressed reads remain durable and explainable. Restart/backfill and historical
  repair never replay hardware.
- Missed-exit recovery preserves exact-plate, current authority and gate-policy
  checks. Phone/camera evidence or scoped resident confirmation cannot bypass
  command owners. It never invents an exit. See the
  [recovery contract](../validation/missed-exit-recovery.md).

Reconciliation must preserve stale lease handling, pending sagas,
accepted-but-unverified commands and safe presence updates. Provider rejection is
failure. Unknown outcomes remain reconcilable; recovery is not permission to
send again. Use `services/movement_reconciliation.py`, `services/movement/admission.py`
and the command owners, with `backend/app/` as the path prefix.

## Supervised live test

Before an authorized live command:

1. Read current health, Admin auth, command receipts, device status and provider
   status. Check for pending/leased commands, reconciliation requirements and
   unresolved admission; do not stack another command on an uncertain outcome.
2. Confirm the exact target and effect with the operator and verify the path uses
   the audited owner. Include every gate/garage target a configured plan can reach.
3. Require the operator to type `LIVE_HARDWARE_TEST_CONFIRMED` in the local session
   after the exact test scope is stated. This does not replace the application's
   Admin confirmation.
4. Issue the authorized command once through IACS. Verify command/audit records
   and current device evidence, including delayed reconciliation if applicable.
5. Report delivery and verification separately. Check for duplicates or stuck
   reconciliation. Do not close hardware unless separately requested.

Basic read-only health checks, when a running local stack is already available:

```bash
docker compose ps
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/auth/status
```

Use the authenticated UI/receipt APIs for command inspection. Do not treat a live
integration test, simulator injection, webhook POST or notification test as a
read-only health check. Full-flow simulation belongs in the
[isolated harness](../validation/phase1.md).
