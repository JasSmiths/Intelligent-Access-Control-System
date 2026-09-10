# Phase 1 result — 6 September 2026

The isolated working-tree baseline passes all existing backend/frontend tests
and six new PostgreSQL regressions. One pre-existing schema-comparison failure
remains. No application code, dependency, production Compose file or migration
was changed by this task. No production endpoint, database, Redis, log or media
was used for testing. Nothing was deployed.

## Exact assessed source

- HEAD: `a84ea4a3a046b70cb055d7d7fdf0b4925f567c95`.
- Included existing unstaged changes: ESPHome device logging in
  `backend/app/modules/access_devices/esphome.py` (+268/-7), timestamp
  canonicalisation in `backend/app/services/action_confirmations.py` (+20/-1),
  and their existing test edits in `test_access_devices.py` (+128) and
  `test_action_confirmations.py` (+18).
- Deliberately included existing untracked
  `.github/workflows/v2-p06-privacy-review.yml` and the Phase 1 harness/docs.
- Complete source snapshot and SHA-256 inventory:
  [manifest.json](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t/manifest.json>).
  Manifest SHA-256: `5955af778695c79da271162afa5b5bd8e929d09248fab679a812701b92b5532c`.
- All pre-existing source files were hash-compared against the first snapshot:
  unchanged by this task. All inventoried final-run snapshot files remained
  unchanged during checks. This report was written after the final run; it is
  not part of that manifest.

The deployed backend container mounts the current `backend/app` at `/app/app`
and the repository at `/workspace`. Its process started on 29 August, without a
reload flag in Compose. Mount contents do not prove which module versions are
already loaded in memory. The frontend serves baked image assets. Accordingly,
this is a **working-tree source baseline**, not a claim that production is running
this exact commit. No production process was entered to establish a version.

Recorded deployed image IDs: backend
`8222093dcb80a0818942f7cba9c746afe0a21cadc0581d7ab26ae005f51f2207`,
frontend `bfc737ce8250f904de1df9dd2a89b94b73a556845c9dd42e34462b8c72c466da`.
The backend image supplies Python/uv tooling only; the tests import the snapshot
and use a new lockfile-installed environment. Other image IDs are in
[images.json](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t/images.json>).

## Results

| Check | Result |
|---|---|
| Locked Python dependencies; npm ci without lifecycle scripts | Passed |
| Python syntax compilation | 259 application/test files passed |
| Existing backend suite, including contracts | **744 passed**, 6.6 seconds in initial run; final run also 744 passed |
| New PostgreSQL persistence regressions | **6 passed**, 1.52 seconds |
| Existing frontend suite | **16 passed** across four files |
| Frontend TypeScript + Vite build | Passed |
| Repository-targeted Alfred Ruff | Passed |
| Repository-targeted Alfred Mypy | Passed, 25 source files |
| Fresh Alembic upgrade/current | Passed, `20260713_0002` head |
| Alembic schema comparison | **Failed: existing index drift**, exit 255 |
| Snapshot Compose parse, clean environment | Passed; no services started from Compose |
| Source integrity / git diff whitespace | Passed |
| Production container comparison / cleanup | Passed; no owned containers remain |

The runner deliberately returns nonzero for the schema-comparison failure. No
assertion, golden fixture or expected result was weakened. Raw outputs and JUnit
files are retained in the
[final run directory](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t>).

Alembic reports four migration-created HNSW indexes absent from metadata,
`ix_vehicle_person_assignments_person_id`, and visitor-pass source-reference
index differences (`ux_visitor_passes_source_reference` plus nonunique versus
unique `ix_visitor_passes_source_reference`). It also warns about the foreign-key
cycle between `dependency_update_analyses` and `external_dependencies`. These
come from untouched schema/migration code and appear before the new persistence
tests execute. The check generated no migration and changed no schema. The
result does not establish drift in the production database.

During harness development, Compose plugin discovery failed under a clean HOME,
and mount ordering produced a false production-change result. Those were harness
failures, not application failures. Both were corrected and the full final run
completed with production unchanged. The first run is retained at
`/var/folders/_k/nfq42yxj571g_v63cy61skq40000gn/T/iacs-phase1-58iv9ivq`.

## Isolation and cleanup evidence

[isolation.log](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t/isolation.log>)
confirms loopback-only networking, isolated service connectivity and absent
production data/config/socket paths. Docker's actual namespace/mount/port/resource
configuration is in
[isolation-inspect.log](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t/isolation-inspect.log>).
Backend/PostgreSQL/Redis share a network-none namespace; frontend checks also use
network none. No production network, host ports, devices or Docker socket were
attached. All test storage binds are under the unique final run directory.

[production-before.json](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t/production-before.json>)
and [production-after.json](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t/production-after.json>)
agree on all five IACS containers' IDs, images, start times, restart counts,
running state and mounts. All remain running with restart count zero.
[cleanup.json](</Users/jas/Documents/IACS Regression Baselines/iacs-phase1-b00e6l8t/cleanup.json>)
records no cleanup failures or remaining containers. A final Docker label query
also found no Phase 1 containers from either run. Both host runner processes
completed; container removal ended their child processes. Downloaded image cache
and isolated results/dependency/storage directories are retained intentionally.

## Limits and next step

The [behaviour matrix and reproducible command](phase1.md) distinguish fake-session
coverage from the six real-persistence tests. Remaining gaps include full
transactional access/presence flows, simultaneous gate claims, crash-window
recovery, unfinished notification delivery, maintenance queue side effects,
complete schedule/DST boundaries and real confirmation-consumption races.
Redis readiness is real; existing stream behaviour tests use fake Redis.
Production startup and historical-schema upgrades were not exercised. No full
container image builds, external dependency vulnerability audit, live integration
smoke, hardware test or load test was run. No standalone frontend lint command
exists; TypeScript validation is part of the successful build.

Recommended next step: the single synthetic unknown-plate persistence regression
described in the matrix document, with a committed denial, explainable movement,
no presence change, zero gate rows/calls and safe replay. Keep production code
unchanged unless a separately reviewed injection seam is necessary. Phase 2 has
not begun.
