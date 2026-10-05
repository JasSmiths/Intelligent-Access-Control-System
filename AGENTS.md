# IACS agent guide

IACS turns LPR observations into access decisions, movement, presence, alerts,
and audited device operations. Current source and tests define behavior.
Read only the guides relevant to the task:

- [Development](docs/development.md): setup, configuration, dependencies, and storage.
- [Architecture](docs/architecture.md): backend/frontend owners and data flow.
- [Integrations and safety](docs/integrations.md): providers, physical effects, and recovery.
- [Validation](docs/validation.md): isolated checks, browser checks, and retained evidence.

## Safety

- Gate commands use `GateCommandCoordinator` in `backend/app/services/gate_commands.py`.
  Garage and access-device commands use `AccessDeviceService` in
  `backend/app/services/access_devices.py`. Never bypass those owners.
- Validate untrusted LPR input before durable effects. Unknown plates never
  actuate hardware; suppressions remain durable and explainable.
- Commit access and movement decisions before hardware. Presence follows eligible
  committed admission. Realtime logs are not audit history.
- Provider rejection is failure. Accepted-but-unverified or uncertain commands
  remain reconcilable; restart/backfill and historical repair never replay hardware.
- Preserve Admin confirmation and durable audit for hardware, announcements,
  device configuration, maintenance, schedule overrides, notification sends/tests,
  workflow edits, integration tests, and telemetry purge.
- Live hardware commands require a user-requested supervised test and explicit
  local confirmation as described in the integrations guide. Deployment,
  production migrations, and live provider sends/tests require separate authorization.
  Simulation endpoints can actuate hardware. Never expose secrets or raw provider payloads.

## Working rules

- Complete the requested change, update affected docs, and run relevant checks.
  Resolve routine choices from existing owners; ask when missing intent changes
  scope or requires live access.
- Local edits, read-only inspection, and isolated validation are authorized
  development work. Application confirmation requirements do not block edits or
  inert fixture tests. Use exact locked dependencies and explicit harness
  download/reuse modes.
- Preserve unrelated changes. If backend source is mounted into a running app,
  use an isolated checkout for code changes and validation.
- Locate owners with `rg -l`, then use bounded reads. Read applicable guides once
  unless they change. Default combined tool output to about 2,000 tokens; retain
  verbose validation output in a local log and report status and relevant failures.
- Use `/api/v1`, existing typed clients/contracts, and bind mounts only.
  Default Compose ports: frontend 8089, backend 8088, Postgres 5432, Redis 6379.
- Keep policy and transactions in services and vendor I/O in modules. Do not add
  compatibility shims, runtime schema bootstrap, setting aliases, or provider bypasses.
  Retire callers, registrations, tests, UI, and docs together.

## Required checks

- Docs only: check links, source paths/commands, and `git diff --check`.
- Frontend only: `cd frontend && npm run build && npm test`; follow validation's
  browser checks when interaction or layout changes.
- Backend, schema, or cross-system: run the full isolated harness,
  `python3 scripts/validation/validate.py`, with `--reuse-dependencies` or
  `--allow-downloads` and the prerequisites in the validation guide. It includes
  source checks, backend/frontend checks, migrations, persistence, diagnostics,
  and a database restore rehearsal.
- Harness/source selection: also run
  `python3 scripts/validation/test_source_snapshot.py` and
  `python3 scripts/validation/test_harness_configuration.py`.

Do not use `scripts/backend-pytest` for isolated regression; it can select the
running Compose backend. Report failed or unattempted checks and their limits.
Repeat checks only after relevant changes or to resolve a failure.
