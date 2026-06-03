# Hardware Safety Agent Notes

Use this before touching gate, garage, cover, Home Assistant, ESPHome,
access-device, LPR hardware side effects, gate reconciliation, Alfred hardware
tools, or live deployment validation.

## Absolute Rules

- Do not issue live gate or garage commands unless the user explicitly requests a supervised hardware test.
- Before live actuation, require explicit local confirmation in the current session.
- Do not call Home Assistant, ESPHome, UniFi, or vendor APIs directly to actuate hardware.
- Do not bypass IACS.
- Do not issue repeated open/close commands if the first command is accepted, pending, or ambiguous.
- Do not retry ambiguous hardware commands.
- Do not delete command/audit rows to hide state.
- Do not mark commands successful unless valid current evidence verifies success.
- Do not use raw SQL mutation for command repair unless no current owner path exists; stop and report the proposed mutation first.
- Do not print secrets, tokens, cookies, API keys, passwords, or provider credentials.

## Audited Command Owners

Gate opens:

- Owner: `backend/app/services/gate_commands.py`
- API path: `POST /api/v1/integrations/gate/open`
- Coordinator: `GateCommandCoordinator`
- Controller adapter: `backend/app/modules/gate/access_devices.py`
- Records: `gate_command_records`

Garage/access-device commands:

- Owner: `backend/app/services/access_devices.py`
- API path: `POST /api/v1/integrations/cover/command`
- Service: `AccessDeviceService`
- Providers: `backend/app/modules/access_devices/home_assistant.py`, `backend/app/modules/access_devices/esphome.py`
- Records/audit: durable audit logs and provider/access-device status/events

Alfred hardware tools:

- Gate opens must call `GateCommandCoordinator`.
- Garage/access-device commands must call `AccessDeviceService`.
- State-changing tools must return `requires_confirmation` before mutation.

## LPR Hardware Behavior

- LPR webhook input must pass `X-IACS-LPR-Token` and source IP/CIDR allowlist before durable side effects.
- Maintenance mode accepts/ignores webhook and clears queues; it must not create access events, presence updates, gate commands, or garage commands.
- Unknown plates never trigger hardware.
- Entry gate opens go through `GateCommandCoordinator`.
- Assigned garage doors open only after accepted gate open and schedule allowance.
- Suppressed reads remain durable/explainable.
- Restart backfill and historical repair suppress hardware side effects.

## Reconciliation

Current reconciliation must handle:

- Stale gate command leases.
- Pending movement sagas.
- Accepted-but-unverified commands.
- Provider rejection as failure.
- Presence commit when safe.
- Realtime/audit evidence for reconciliation outcomes.

Allowed current safety fallbacks:

- Gate command reconciliation.
- LPR security fail-closed.
- Durable suppressed movement/session reads.
- Snapshot recovery when access evidence depends on it.
- Provider rejection audit/critical notification paths.
- Notification delivery partial success.

Do not use "safety fallback" as a loophole to keep old architecture.

## Live Hardware Preflight

Before any live command:

1. Confirm backend and frontend are healthy.
2. Confirm current Admin auth/confirmation path.
3. Confirm no pending, leased, reconciliation-required, or unverified gate command is already active.
4. Confirm access-device/gate status endpoints are readable.
5. Confirm provider/integration status is healthy enough to issue commands.
6. Confirm the code path routes through `GateCommandCoordinator` or `AccessDeviceService`.
7. Ask for exact operator confirmation:

```text
Type LIVE_HARDWARE_TEST_CONFIRMED to open the gate and garage door through IACS audited command paths.
```

Do not proceed unless that exact confirmation is provided in the local session.

## Safe Read-Only Checks

```bash
docker compose ps
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/auth/status
docker compose logs --tail=200 backend
```

Gate command state check:

```bash
docker compose exec -T postgres psql -U iacs -d iacs -At -c "select state || '|' || requires_reconciliation || '|' || count(*) from gate_command_records group by state, requires_reconciliation order by 1;"
```

## Post-Command Verification

After a supervised live command:

- Check backend/worker/provider logs.
- Check command/audit records.
- Check realtime/status events.
- Confirm no duplicate command was issued.
- Confirm no stuck pending/reconciling command remains.
- Confirm no direct provider bypass occurred.
- Do not close hardware automatically unless the operator separately asks for that.
