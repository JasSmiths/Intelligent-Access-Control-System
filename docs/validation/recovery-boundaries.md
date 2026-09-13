# Inert recovery boundary diagnostics

This is future-recovery characterization, separate from the passing regression
suite. `scripts/phase1/test_recovery_boundaries.py` contains real failing safety
assertions: no xfail, expected-failure inversion, skips, or application fixes.
The fixture data in `scripts/phase1/fixtures/recovery_boundaries/scenarios.json`
is synthetic. Failing diagnostics block the affected recovery release until its
implementation makes the invariant pass. A diagnostic failure does not mean its
entire existing subsystem is broken.

## Safe execution

Use the existing phase1 backend environment, **serially**, after the lead has
prepared a disposable PostgreSQL schema. This script does not migrate, bootstrap,
start workers, install dependencies, contact live IACS, or invoke Docker. It
truncates its explicit synthetic table set with cascades before each test; it is
not suitable for a shared test database or production data.

Collection refuses before IACS imports unless all of these hold:

- Linux has only the loopback network interface and no Docker socket.
- Source snapshot contains no root `.env`, `data`, or `logs`; cwd has no `.env`.
- `IACS_RECOVERY_PROBES=synthetic-only` and `IACS_ENVIRONMENT=testing`.
- `IACS_AUTO_CREATE_SCHEMA=false`, `IACS_SEED_DEMO_DATA=false`.
- DB URL is the existing phase1 synthetic principal/password at
  `127.0.0.1:5432`, with database name beginning `iacs_p1_` or `iacs_recovery_`.
- Auth root is exactly the phase1 synthetic root. Integration credential
  environment variables are absent/empty. The disposable DB has no persisted
  runtime settings.

The test fixture then rejects every socket except that PostgreSQL endpoint and
rejects real HTTP transports. HTTP tests use only `MockTransport` or
`ASGITransport`. Event publication, optional traces/memory, and command/message
provider sinks are inert. The database name is verified before truncation. No
production credentials, payloads, identities, settings or logs are fixture input.

Inside that prepared environment, from the snapshot repository root:

```bash
IACS_RECOVERY_PROBES=synthetic-only PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend \
  python -m pytest -q -p no:cacheprovider scripts/phase1/test_recovery_boundaries.py
```

Use `-k probe_01` through `-k probe_06` for bounded diagnosis. Do not use xdist or
parallel execution against one DB. Keep original test output, exit status,
runtime/lock identity and source hashes. Add both new source and JSON fixture to
the existing harness snapshot manifest while untracked. Do not run
`scripts/backend-pytest` or the full Docker harness merely to execute this file.

## What each probe establishes

| Probe | Real boundary | Inert substitutions | Intended assertion / supporting cases |
|---|---|---|---|
| 01 | Gate adapter → coordinator → PostgreSQL command ledger | Device-owner results | Target order cannot manufacture aggregate physical verification; partial acceptance remains reconcilable. All-verified/all-rejected controls. No selected-target product policy is assumed. |
| 02 | HA HTTP request/error translation → HA provider → device failover | HTTP MockTransport; fallback command sink; immediate synthetic verification result | A POST accepted before response loss cannot be sent again through fallback. Successful POST plus failed state read remains accepted/unverified. Definitely-unsent connection failure retains configured fallback. This injects delivery ambiguity; it does not measure network performance. |
| 03 | Coordinator → real PostgreSQL lease → fresh coordinator/reconciler | Gate sink held after simulated acceptance by asyncio events | Expiry must not repeat one intent. Retry-before-reconciler and reconciler-before-retry expose ordering dependence. Completed duplicate identity is a positive control. |
| 04 | Persisted rule → real recognition event bridge → rule selection/action loop/registered WhatsApp action | Audited command-owner/provider sinks and WhatsApp delivery sink | Unknown denied input cannot actuate either gate or garage, regardless of action order; safe messaging still executes. Known authorized, historical skip flags and dry-run controls. Seeded existing rules ensure configuration rejection cannot conceal an execution bypass. |
| 05 | Actual HTTP confirm route → real session/pending JSON reads/writes → catalogue/schema/definitive tool execution | Current synthetic actor dependency, registered tool's inert handler, optional memory and provider | Concurrent confirmation invokes the stored action once; preview and sequential replay controls. A barrier schedules both real reads before clear; no fake pending store or same-widget guard supplies atomicity. |
| 06 | Restart presence wrapper → latest-event query; registered Alfred historical handler → real event/presence/audit writes | Synthetic historical candidate instead of Protect acquisition | Failed/pending grants cannot become committed presence; stale Alfred history cannot rewind newer presence. Newer historical repair remains a control and requests no actuation/replayed notification. |

All tests use bounded watchdogs; race ordering uses events/barriers, not sleeps.
The original task is always released and joined even when a lease assertion
fails. Fresh session factories remain the application's real factories.

Probe 05 characterizes current API actor binding and the approval race, not a
complete authentication or WebSocket security audit. Its dependency override
loads an actual synthetic current User per request but deliberately avoids token
issuance. Optional chat memory is excluded to avoid unrelated background tasks.
After replacing pending storage, port its scheduling seam to the new claim
boundary while retaining the two concurrent real requests and one-effect
assertion; do not preserve the old private helper just for this test.

Probe 06 isolates historical write policy from recognition evidence quality.
It does not choose visitor consumption, equal-time ordering, maximum live-event
age, or physical-passage policy. The multi-target probe does not choose which
subset of gates admission should require: it only rejects a misleading global
physical-success assertion and loss of accepted partial work.

## Result recording

The lead executed the captured diagnostic source in the isolated full run
`/private/tmp/iacs-recovery-7ggriel9/evidence/iacs-phase1-j23gc90q` on 12 September
2026. Its `diagnostic-1.log` and `diagnostic-1.xml` record **14 failed, 18 passed in
9.16 seconds**. Every failure reached the intended invariant assertion; there
were no collection or fixture failures. This is a red diagnostic baseline, not
a passing release gate.

| Probe | Failing invariants | Supporting passes |
|---|---:|---:|
| 01 multi-target result | 3 | 3 |
| 02 HA response loss | 1 | 2 |
| 03 expired lease/reconciler order | 1 | 2 |
| 04 recognition-to-automation authority | 4 | 9 |
| 05 concurrent V3 approval | 1 | 1 |
| 06 historical presence | 4 | 1 |

Lease expiry used actual persisted timestamps and `clock_timestamp()` in a
fresh transaction. Approval expiry came from the actual pending-action store.
The fixed fixture date was used only for synthetic observation/history ordering,
not lease or approval timing. A separate host-side syntax/JSON/guard check also
confirmed that collection without the explicit opt-in refuses before any IACS
module import. No dependencies were installed by the diagnostic author.

Infrastructure/fixture failures in future runs must be corrected or reported as
blocked before claiming an observed architectural hazard. Supporting controls
must pass before interpreting each hazard failure. No application repair,
schema change, live test, deployment, commit or push is part of these files.
