# Recovery boundary diagnostics

`scripts/phase1/test_recovery_boundaries.py` exercises six recovery boundaries
with synthetic records, real disposable PostgreSQL transactions and inert
provider transports. It is a required full-mode suite in
`scripts/phase1/recovery_checks.py`. Its assertions are current regression
contracts; a dated failure or pass does not describe the current checkout.

## Run through the isolated harness

Follow [the validation guide](phase1.md) for dependency preparation, execution and
evidence. A full run includes these diagnostics automatically; `--diagnostic`
adds other reviewed suites rather than replacing the mandatory checks.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py \
  --mode full --reuse-dependencies /absolute/path/to/trusted-prior-evidence-run \
  --evidence-root /private/tmp/iacs-validation-evidence
```

The harness runs PostgreSQL suites serially with a separate migrated database
clone and Redis database per file. This diagnostic also truncates its explicit
table set with `CASCADE` before each test. Never run it against a shared test
database or production, or run its tests concurrently against one database.

Collection fails before IACS imports unless the Linux namespace has only loopback,
no Docker socket or copied runtime files, synthetic test credentials, disabled
schema bootstrap/demo seeding and the isolated PostgreSQL database identity. The
fixture rejects persisted runtime settings, unmocked HTTP transports and sockets
other than its PostgreSQL connection. It stubs event publication, optional
telemetry/memory and command/message transports. It does not start the application
lifespan or workers, invoke production endpoints or send hardware commands.

## What the probes establish

| Probe | Boundary and assertion |
| --- | --- |
| `probe_01` | Adapter, coordinator and command ledger preserve each selected target receipt. Aggregate verification is independent of target order; partial/uncertain delivery stays truthful and replay does not issue another command. |
| `probe_02` | Home Assistant response loss or failed post-command state reads cannot trigger fallback after possible transmission. A definitely unsent connection failure retains configured fallback. |
| `probe_03` | An expired command lease cannot execute the same intent again, regardless of whether retry or reconciliation happens first. Completed duplicates also remain inert. |
| `probe_04` | Persisted recognition-triggered rules retain the originating authorization. Unknown denied input cannot actuate gates or garages in either action order, while safe messaging remains possible. Authorized, historical and dry-run controls constrain the result. |
| `probe_05` | Concurrent calls through the real Alfred V3 confirmation route claim one persisted approval and execute it once. Preview and sequential replay are inert. |
| `probe_06` | Historical admission does not promote an incomplete/failed gate grant or overwrite newer committed presence. Alfred historical repair preserves ordering and suppresses hardware, automation and notification actions. |

The fixture scenarios live in
`scripts/phase1/fixtures/recovery_boundaries/scenarios.json`. Owner implementations
are mapped in [the backend guide](../agent/backend.md); hardware policy is in
[the hardware guide](../agent/hardware-safety.md).

## Interpret results

Retain the run manifest, exact commands, source/lock identities, JUnit XML and
logs. A failure is a failed contract; do not hide it with `xfail`, expected-failure
inversion or baseline acceptance. Distinguish a failed safety assertion from
collection, fixture, migration or dependency failure before diagnosing a hazard.
Use the probe names to locate bounded failures in the retained suite output.

These synthetic checks establish software behavior under injected outcomes.
They do not establish production deployment status, provider reliability, physical
vehicle passage, live backup/restore readiness or permission for live testing.
