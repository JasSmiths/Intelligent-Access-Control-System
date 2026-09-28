# IACS Agent Guide

Intelligent Access Control + Presence System turns LPR reads into durable movement
and access decisions, presence, alerts, audited gate/garage orchestration, and
a realtime console with Alfred operations.

## Read first

- Backend, APIs, Alfred, migrations, integrations: [backend guide](docs/agent/backend.md)
- React, typed API clients, routes, styles: [frontend guide](docs/agent/frontend.md)
- Gate, garage, LPR, providers, or live checks: [hardware safety](docs/agent/hardware-safety.md)
- Ownership changes or retirement: [architecture guide](docs/architecture.md)
- Isolated validation and retained evidence: [validation guide](docs/validation/phase1.md)

## Non-negotiable safety

- Gate commands use `GateCommandCoordinator` in `backend/app/services/gate_commands.py`.
  Garage and access-device commands use `AccessDeviceService` in
  `backend/app/services/access_devices.py`; Alfred uses those same owners.
- Never actuate unknown plates. Validate untrusted LPR input before durable
  side effects. Suppressions are durable and explainable.
- Treat provider rejection as failure. Keep accepted-but-unverified gate commands
  reconcilable. Restart/backfill and historical repair never replay hardware.
- Commit access and movement decisions before hardware; presence follows committed
  decisions. Realtime logs are not audit history.
- Admin confirmation and durable audit are required for hardware, announcements,
  access-device configuration, maintenance, schedule overrides, notification
  sends/tests, workflow edits, integration tests, and telemetry purge.
- Do not run live hardware commands without a user-requested supervised test and
  explicit local confirmation. Never expose secrets or provider payloads.

## Working rules

- Keep context focused: use `rg -l` to locate owners, then bounded searches and
  relevant line ranges. Do not dump whole large files, catalogs, logs, or diffs.
- Read applicable guides once per task; reuse findings unless files change.
- Default tool output to about 2,000 tokens; expand only to resolve missing
  evidence. In `functions.exec`, bound the combined output, not just each call.
- Save verbose validation output to a local log; report exit status, summary,
  and relevant failures. Run required checks, then repeat only for new changes
  or unresolved failures. Do not reload successful logs.
- Inspect current owners and runtime state; change only task-required files.
- Use `/api/v1` only, bind mounts only, and existing typed/API contracts.
- Do not add compatibility shims, runtime schema bootstrap, setting aliases, or
  provider bypasses. Delete retired callers, tests, UI, and documentation together.
- Compose ports: frontend 8089, backend 8088, Postgres 5432, Redis 6379.

## Main validation

```bash
python3 scripts/phase1/validate.py
python3 scripts/phase1/test_source_snapshot.py
git diff --check
```

For frontend-only work, run `cd frontend && npm run build && npm test`.
Do not use `scripts/backend-pytest` as an isolated regression run. Deployment,
production migrations, and hardware tests require separate authorization.
