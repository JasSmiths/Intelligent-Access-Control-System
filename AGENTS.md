# IACS Agent Guide

Intelligent Access Control + Presence System turns LPR reads into durable movement
and access decisions, presence, alerts, audited gate/garage orchestration, and
a realtime console with camera image analysis.

## Find the relevant context

Read the guides needed for the task; a small edit does not require a full system
review. Current source and tests define behavior. The [documentation index](docs/README.md)
routes operational and validation questions.

- Backend, APIs, migrations, integrations: [backend guide](docs/agent/backend.md)
- React, typed API clients, routes, styles: [frontend guide](docs/agent/frontend.md)
- Gate, garage, LPR, providers, or live checks: [hardware safety](docs/agent/hardware-safety.md)
- Ownership changes or retirement: [architecture guide](docs/architecture.md)
- Isolated validation and retained evidence: [validation guide](docs/validation/phase1.md)

## Non-negotiable safety

- Gate commands use `GateCommandCoordinator` in `backend/app/services/gate_commands.py`.
  Garage and access-device commands use `AccessDeviceService` in
  `backend/app/services/access_devices.py`.
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

## Work through to completion

- Complete the requested change, update affected documentation, and run the
  relevant checks. Resolve routine implementation choices from existing owners
  and contracts. Ask when a missing decision changes scope or requires live access.
- Local edits, read-only inspection, and the isolated validation harness are
  authorized development work. Run them and fix task-related failures without
  requesting approval for each step. Use exact locked dependencies; follow the
  harness's explicit download/reuse modes.
- Admin confirmation above is an application requirement for real actions, not
  a reason to stop before editing their implementation or testing inert fixtures.
  Deployment, production migrations, live provider sends/tests, and hardware
  operation require separate authorization. A simulation endpoint can actuate
  hardware; synthetic input alone does not make it safe.
- Preserve unrelated working-tree changes. If backend source is mounted into a
  running application, use an isolated checkout for code changes and validation.

## Repository constraints

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

## Validation by change

- Documentation only: check links, referenced paths/commands, and `git diff --check`.
- Frontend only: `cd frontend && npm run build && npm test`; use the frontend
  guide for browser checks when interaction or layout changes.
- Backend, schema, or cross-system changes: run the full
  [isolated harness](docs/validation/phase1.md). The entrypoint is
  `python3 scripts/phase1/validate.py`; execution needs `--reuse-dependencies`
  or `--allow-downloads`. It includes source-selection tests, backend and frontend
  checks, migrations, persistence, diagnostics, and a database restore rehearsal.
- Harness/source-selection changes also need
  `python3 scripts/phase1/test_source_snapshot.py` and
  `python3 scripts/phase1/test_harness_configuration.py`.

Do not use `scripts/backend-pytest` for isolated regression: it can select the
running Compose backend. Report failed or unattempted checks and their limits;
repeat checks only after relevant changes or to resolve a failure.
