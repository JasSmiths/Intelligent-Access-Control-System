# Validation

Run checks against the checkout being changed. Preserve unrelated changes and
save verbose output to a local log. Report the commands, exit status, retained
evidence, failures and unattempted checks; repeat checks only after relevant
changes or to resolve a failure.

## Choose the checks

| Change | Required validation |
| --- | --- |
| Documentation | Check links, referenced paths and commands; run `git diff --check`. |
| Frontend | In `frontend/`, run `npm run build` and `npm test`. Add browser checks for layout, interaction, navigation or role changes. |
| Backend, schema or cross-system | Run the full isolated harness below. |
| Harness or source selection | Also run `python3 scripts/validation/test_source_snapshot.py` and `python3 scripts/validation/test_harness_configuration.py`. |

Current source and tests are the contract. A past passing run does not validate
the current checkout. Live provider tests, hardware operation, deployment and
production migrations require separate authorization; see
[integrations and safety](integrations.md).

## Frontend browser checks

With the locked frontend dependencies installed, run from `frontend/`:

```sh
npx playwright install chromium webkit
CI=1 npm run test:e2e
```

Browser installation can download packages. The
[Playwright configuration](../frontend/playwright.config.ts) runs Chromium and
WebKit on loopback port 5174. Its [Vite configuration](../frontend/vite.e2e.config.ts)
has no backend proxy. The [browser suites](../frontend/e2e/) fulfill synthetic
`/api/v1` requests, reject unexpected requests and close WebSockets. Outside CI,
Playwright may reuse an existing server: check that port 5174 belongs to this
fixture-only configuration before running. Final candidate gates use `CI=1` so
a stale server cannot silently test a different checkout.

Review affected screens at desktop, tablet, narrow phone and short landscape
sizes. Check overflow, readable text, reachable actions, focus, editor dismissal
and retained state across resizing; include relevant themes and role restrictions.
The responsive suite owns the viewport matrix and safe-area checks. Review
screenshots under `frontend/test-results/playwright/`; traces and failure
screenshots use the same output directory. Browser emulation does not establish
physical-device keyboard, zoom, hinge or safe-area behavior. Report unavailable
device checks as limits.

The [theme startup fixture](../frontend/e2e/theme-startup.spec.ts) withholds application
code to check the initial canvas, then loads and reloads the dashboard with
saved, system, missing and invalid appearance settings in Chromium/WebKit. It
also covers unavailable storage, theme controls, system appearance changes and
desktop/tablet/phone/short-landscape screenshots. Run with
`CI=1 npm run test:e2e -- e2e/theme-startup.spec.ts`. This checks document painting;
verify Safari's pull-to-refresh transition separately on a physical iPhone.

The [search palette suite](../frontend/e2e/search-palette.spec.ts) covers phone
result scrolling with a synthetic keyboard viewport, direct tap/Enter navigation,
desktop previews, tablet/landscape resizing, animated dismissal and focus
restoration, and reduced motion.
It retains light/dark phone and desktop screenshots. Verify keyboard opening and
closing on a physical iPhone before claiming device-specific keyboard coverage.

The [feature-read fixtures](../frontend/e2e/feature-reads.spec.ts) exercise remote
subject search beyond the former first-500 boundary, selected-ID hydration and
coalesced preview reads. The [ownership evidence fixture](../frontend/e2e/ownership-evidence.spec.ts)
records request counts and loaded JSON bytes for equivalent initial-page,
confirmed mutation, navigation and realtime-burst workflows at small and target
scale. Run the identical fixture against the preserved baseline and candidate,
retain source/runtime/lock/fixture identities, and compare equivalent visible
outputs. Report the beyond-500 case separately as correctness evidence. Loaded
payload changes do not establish reduced database work or production speedups;
unchanged unpaged group/schedule contracts and the population-sized public
presence compatibility read remain explicit limitations. Build and unit evidence uses the guide's locked
Node 22 runtime; host browser runtime identity is recorded separately. If development
effect replay changes request counts, retain those results and compare the same
fixture against locked production assets served sequentially from disposable
preview servers. Record requested/completed/aborted reads and define the payload
measurement.

The [integration receipt fixtures](../frontend/e2e/integration-receipts.spec.ts)
verify that accepted settings and Apprise mutations retain their result
after a provider metadata read fails, while rejected mutations remain failures.
Composed owner tests also cover accepted pass/device/user changes, local refresh
isolation and observed retry errors. These inert fixtures do not send to providers.

The [provider editor browser suite](../frontend/e2e/provider-editors.spec.ts)
drives actual realtime invalidation while preserving each complete provider draft,
checks dirty and pending dismissal, and retains desktop, phone, short landscape
and dark tablet screenshots in Chromium/WebKit. It exercises accepted protected writes and account disposal. The
[composed editor tests](../frontend/src/features/integrations/providerEditors.test.tsx)
cover account/provider replacement during confirmation and after write acceptance,
late ESPHome stream reads, explicit managed/scalar coverage and provider-local
metadata failure. [Client lifetime tests](../frontend/src/api/integrationMutationLifetime.test.ts)
prove stopped-before-write outcomes and continued observation of started requests.

The [frontend descriptor fixture](../frontend/src/features/settings/accessDeviceProviders.test.tsx)
injects an inert third provider through the real discovery/editor/conversion/save
owner and confirmed binding write. It also checks HA/ESPHome order and conversion,
missing/blank bindings, partial accepted phases with fresh-confirmation retry,
blocked save/delete/create races and late pre-mutation reads. This fixture is
synthetic; it does not register or actuate a live provider. Backend provider-policy
extension evidence and final integrated E1 conformance remain separate gates.


## Retirement and remediation checks

The [calendar retirement suite](../scripts/validation/test_calendar_retirement.py)
proves fresh creation, populated upgrade, atomic blockers, mixed-plan holds,
completed/uncertain receipt preservation, manual pass survival, open USED departure
matching and guarded irreversible downgrade with non-database sockets forbidden.
Historical published migrations and checksum-pinned schema fixtures remain intact.
[Auth-root persistence](../scripts/validation/test_auth_root_persistence.py) retains
shared settings rotation, audit, cancellation and uncertain file/DB outcome coverage.
Calendar SDK/account/editor tests were retired with their executable feature.

[Access efficiency](../scripts/validation/test_access_efficiency.py) measures 16 and
2,000 registrations using two warm-ups and seven samples, photo-heavy identities,
unknown/OCR matching, narrow camera probes, auth and four concurrent UI readers.
Target actual/generic predecessor plans also assert identity/time index conditions,
one emitted row per branch and no unrelated-history filtering. It retains query
counts, loaded scalar byte proxies,
Python allocation peaks and p50/p95. Seven samples give coarse tail estimates;
scalar/JSON sizes are not database wire bytes or process RSS. Matching is complete
and unchanged; no directory cap or stale authorization cache is added.

## Final runtime PDF capability

The [runtime PDF smoke](../scripts/validation/runtime_pdf_smoke.py) checks the final
runtime target using its own configured non-root user, installed environment,
locked Chromium and [actual report template](../backend/app/services/report_templates/person_movements.html).
It starts no application services, uses a synthetic snapshot, disables networking
and mounts only a newly owned output directory. It never overlays application
source, a development virtual environment or downloaded browser files.

```sh
python3 scripts/validation/runtime_pdf_smoke.py --build \
  --image iacs-backend:validation-runtime-pdf \
  --evidence-root /private/tmp/iacs-runtime-pdf-evidence
```

Building may download locked dependencies, native packages and the matching
Playwright browser. Omit `--build` to check an already-built immutable image. The
retained result includes the image ID/size/user, source identity, build duration
when built, smoke duration and PDF artifact. The installed smoke also records
template/renderer/lock hashes, the Python environment and Playwright version.
CI checks its final runtime image before the full isolated harness. This proves
synthetic PDF capability; supported maximum export sizes are verified separately.

The installed [smoke fixture](../backend/tools/report_pdf_smoke.py) also supports
`max-images`, `max-rows` and `external` for inert resource/content checks. Keep the
immutable runtime image, wrapper/fixture hash and Docker command with each result.
The report PostgreSQL suite checks shared row/vehicle bounds, compact SQL columns,
repeatable source configuration, final authority, audit rollback and real blocked
SQL drain. Unit/lifespan fixtures separately cover actual ASGI disconnect,
thread/driver acquisition, cancellation and accepted/uncertain commit cleanup.

P8 retained maximum diagnostics use image `6f23f9cb891d` and unchanged renderer,
template and lock bytes. The two-image fixture reaches exactly 25 MiB of complete
data URLs and 64 Mi-pixels. It failed with a 256 MiB `/tmp` allocation and passed
with 512 MiB, observing 337,842,176 bytes of temporary storage. The combined
10,000-row plus maximum-image fixture failed at the 2 GiB RAM and 2 GiB swap
allowance (cgroup OOM recorded). It passed at a 4 GiB RAM cap with a configured
4 GiB swap allowance and 512 MiB `/tmp`: cgroup RAM peak 3,889,774,592 bytes,
sampled swap peak 1,076,711,424 bytes, sampled `/tmp` peak 314,765,312 bytes.
The resulting PDF has 359 pages, two images and the final row; representative
pages were visually checked. These are measured single-export synthetic envelopes,
not proof that 4 GiB total/no-swap suffices, a hard RSS bound, or capacity for normal
application allocations, concurrent work or multiple workers. Compose resource
settings are unchanged. All failed runs and cgroup/temp/browser diagnostics remain
in `p8-report-runtime-limits`, including `resource-envelope-summary.json`.

Before/final source-read evidence runs the same small and target fixtures against
approved pre-report commit `ef1647b2` and the candidate. Capture equal public output,
actual SQL/projections and complete plans, ORM instances and unused blob scalar
bytes materialized, Python allocation peaks and JSON size. Scalar UTF-8 bytes are
not wire traffic; PDF resource measurements are a separate gate. The corrected
`p8-report-read-comparison/comparison.json` pair has equal 30,083/3,413,699-byte
public output at small/target scales. Target Python allocation peak is
44,181,925 → 15,710,081 bytes and full unused blob scalar values loaded are
23,285,210 → 0 bytes. Query count is 14 → 13 at target, but 11 → 13 at small;
selected-event EXPLAIN execution is 0.682 → 4.298 ms despite overall measured
preview time 1.108 → 0.725 s. These show less materialization, not universal SQL
speedup. Cached-bind plan attempts and nondeterministic initial photo timestamps
remain superseded evidence with explicit reasons.

## Isolated harness

Complete the [disk-space safety checks](#disk-space-safety) before preparing or
running the harness. These apply to focused, DB-free, browser and image/restore
checks as well as full runs.

[validate.py](../scripts/validation/validate.py) copies and hashes the current source,
then executes against private resources. It does not isolate earlier edits to
an operational bind mount: use an isolated checkout for code changes when the
backend is mounted into a running application. Do not use `scripts/backend-pytest`
for isolated regression; it can select the running Compose backend.

Use Python 3.12 or newer for the host harness and source checks; the architecture
scanner parses Python 3.12 syntax. Execution also needs Docker and a compatible
local backend tooling image with Python 3.12 and `uv`. Build a dedicated image, allowing its image/package downloads:

```sh
docker build --target development -t iacs-validation-tooling:local backend
export IACS_VALIDATION_BACKEND_IMAGE=iacs-validation-tooling:local
```

An existing compatible image can be selected instead. Without the override, the
harness uses `intelligentaccesssystem-backend:latest`. `--allow-downloads` can pull
missing images but does not build this tooling image. Node, PostgreSQL and Redis
image references come from the copied Dockerfile and Compose configuration.

The harness lives under `scripts/validation/`. Use the current image override
above; obsolete overrides are rejected rather than accepted as aliases. Runs use
`iacs-validation-*` evidence/container names, the `iacs.validation` ownership label,
and `iacs_validation_*` synthetic databases. The harness and simulator require the
matching `IACS_VALIDATION_MODE` and synthetic identity. Retained evidence from
earlier runs keeps its original names and contents.

Run from the repository root. Choose an evidence directory outside the
repository and operational bind mounts:

```sh
# Full regression with exact-lock installation and image pulls permitted.
python3 scripts/validation/validate.py --allow-downloads \
  --evidence-root /private/tmp/iacs-validation-evidence

# Deliberately keep one new verified preparation for later read-only reuse.
python3 scripts/validation/validate.py --allow-downloads --retain-dependencies \
  --evidence-root /private/tmp/iacs-validation-evidence

# Full regression with an existing trusted dependency preparation.
python3 scripts/validation/validate.py \
  --reuse-dependencies /absolute/path/to/prior-run \
  --evidence-root /private/tmp/iacs-validation-evidence

# Source capture only: no Docker, imports, installs or application tests.
python3 scripts/validation/validate.py --mode snapshot \
  --evidence-root /private/tmp/iacs-validation-evidence
```

Full mode is the default. Both `full` and `db-free` require an explicit
`--allow-downloads` or `--reuse-dependencies`; the bare command refuses to guess.
Preparation uses `uv sync --locked --extra dev --no-install-project` and
`npm ci --ignore-scripts`. Reuse must match the copied manifests, lockfiles and
immutable backend/Node image identities; it mounts the preparation read-only and
verifies it offline without copying packages. Reuse verifies dependency selection, not package
trust. Installation may execute package code.

The [storage owner](../scripts/validation/storage.py) checks host and Docker VM
capacity before allocation and monitors subprocesses/copies. The cached VM probe
uses an immutable image ID with `--pull=never`. All harness modes and runtime smoke
share one execution lock, held through protected cleanup. Reused packages remain
read-only; a small writable package-link directory holds Vite/Vitest caches.
Fresh installations are disposable by default; `--retain-dependencies` explicitly
keeps a successfully verified new preparation. It cannot accompany reuse or
snapshot mode. Keep only the preparations required by current locks/runtime IDs.
After owned containers drain, cleanup removes recorded disposable directories only
after checking all existing container mounts. Inspection failure preserves files
and fails cleanup. Source, logs, artifacts and designated preparations survive.

The harness runs the repository dependency/side-effect scan once as
`architecture-boundaries`. Backend tests separately cover the scanner algorithms.
Access persistence tests verify command ordering and outcomes; synthetic timing
samples are not performance acceptance checks.

Full mode runs host source/configuration tests, architecture and Python checks,
backend tests, full-source Ruff checks, undefined-name checks for backend and
isolated validation fixtures, checked-body mypy checks, strict typed
contract checks, generated workflow contract freshness, frontend tests/build and Compose configuration
validation. It then migrates disposable PostgreSQL, checks Alembic state and
drift, runs persistence suites with separate database/Redis state, required
schema and recovery diagnostics, and a synthetic `pg_dump`/`pg_restore` rehearsal.
[recovery_checks.py](../scripts/validation/recovery_checks.py) owns the required
inventory; new `scripts/validation/test_*.py` suites must be classified there.

`--mode db-free` runs the checks that need no database services or migrations;
it cannot establish persistence, recovery or schema behavior. `--check-only`
aliases snapshot mode; `--no-migrations` aliases DB-free mode. Snapshot success
establishes source capture only. Full mode already requires the restore rehearsal;
`--database-restore` simply spells out that default. Additional `--persistence-test`,
`--diagnostic` and `--schema-check` selections are additive and full-mode only.
Review their side effects before running them.

## Disk-space safety

Development and validation must never exhaust the host disk or take production
offline. The 14 October 2026 incident showed that repeatedly retaining dependency
copies and synthetic databases can consume tens of gigabytes despite successful
container cleanup. Required evidence must remain compact and separate from
disposable working data. Moving the same data to another folder on the same disk
does not recover capacity.

1. **Inventory before every allocation.** Before each snapshot, installation,
   focused/full/DB-free/browser run, image build or backup, record host free space,
   task/evidence/cache sizes and Docker storage. Include existing runs outside the
   checkout, other project checkouts and checkpoints in the inventory. Identify
   the filesystems containing evidence and the Docker VM; check each affected
   filesystem. On this macOS host, read-only checks include:

   ```sh
   df -h . /private/tmp
   du -sh data/validation data/checkpoints
   docker system df
   ```

   Also measure the selected evidence/cache directories. Replace host-specific
   paths when working elsewhere. Estimate peak additional space, including copied
   dependencies, temporary image layers, database/restore state and archives.
   Missing or unreliable accounting is a reason to stop before a bulk run.
2. **Enforce a reserve and budget.** Keep at least **30 GiB free** on every affected
   filesystem. Admit a run only with at least the greater of **40 GiB** or
   **30 GiB plus its estimated peak additional space** available. The default
   budget for all task-generated disposable storage is **10 GiB**, including
   dependencies, browsers, synthetic databases and additional test images, not
   10 GiB per run. Exclude production data and required rollback backups from that
   working budget, but include their occupancy in free-space accounting. If a
   workload needs a larger budget, stop and agree the capacity/retention plan with
   the owner before allocating it; do not silently lower the reserve.
   Current accounting conservatively includes retained task evidence and source
   too, so generated files cannot escape the budget by living beside evidence.
   Accounting never grants permission to delete those retained files. Docker image
   sizes and VM free-space changes are conservative proxies: shared layers can be
   overcounted, and unrelated VM growth can abort a run. Polling is not a hard
   filesystem quota; blocked filesystem calls remain a detection limitation.
3. **Bound active work and reuse.** Run only one bulk IACS validation, dependency
   preparation or image build at a time on this host, including ad-hoc focused
   runners. Reuse one preparation with matching locks and immutable runtime
   identities. Prefer a verified read-only shared preparation with separate
   writable caches. Retire ad-hoc runners that still copy dependencies or bypass
   admission, accounting, cancellation cleanup or the shared execution lock.
   Remove superseded disposable state before admitting the next
   run. Do not create a new checkout, environment, browser installation or image
   merely to retry a check when a compatible one already exists.
4. **Monitor and stop safely.** Check free space and task growth at least every
   **30 seconds** during long copies, downloads, builds and database checks, and
   between phases. At less than **40 GiB free**, stop admitting new bulk work and
   reclaim owned disposable state. At or below the **30 GiB reserve**, or when
   the task budget would be exceeded, cancel further task allocation and drain
   owned work using its cleanup path. Record the incomplete check; do not delete
   production files, kill unrelated processes or restart live services to make a
   test finish. A guard must cover setup and failure paths, not just test success.
5. **Clean up and retain proof after every run.** This includes failures,
   cancellation and interrupted runs. First retain source fingerprints, test
   inventory, commands/exit codes, failure logs and required review evidence.
   Verify container ownership and cleanup records; check mounts on **all running
   and stopped containers**, including ancestor/descendant paths. Then remove only
   the run's disposable dependency copies, synthetic PostgreSQL/Redis directories
   and generated caches. Preserve the designated reusable preparation.
   Synthetic PostgreSQL runs as the runner's UID/GID with all capabilities
   dropped. Its data retains private `0700` permissions while remaining readable
   to the same runner for disk accounting and owned cleanup on Linux. Check
   exact image IDs, aliases and container references before removing superseded
   test images; do not use forced image removal. Inspect build cache before
   clearing unused entries, and account for other applications sharing Docker.
   Never use blanket system/volume pruning or manually delete the Docker VM disk.
   Unknown or nonempty legacy volumes and rollback checkpoints need individual
   retention review. Record deleted paths/identities, cleanup failures and final
   free space; leave source, evidence and production state intact.

After a disk-exhaustion incident, bulk validation remains paused until resource
guards and cleanup are implemented and checked for admission, growth, failure and
cancellation. Documentation is not enforcement. Small host-only documentation
checks and tests of those guards may proceed without starting bulk runs. Keep an
exact continuation point; do not weaken regression coverage to fit the budget.
See [local retention rules](development.md#local-cleanup-and-retention) for recovery
checkpoints, image archives and other checkouts.

## Source, isolation and evidence

[source_snapshot.py](../scripts/validation/source_snapshot.py) copies tracked files
from working-tree bytes, including staged and unstaged edits, and omits tracked
deletions. Safe untracked files under `backend/`, `frontend/`, `scripts/`, `docs/`
and `.github/` are included automatically. Relevant ignored source fails closed
unless explicitly included or classified as an exclusion. Use `--include
REPO_RELATIVE_FILE` for required safe files outside automatic selection. Symlinks,
traversal and directory inclusions are rejected. Credentials, `.env*`, runtime
data, logs, local Git/agent state, caches and generated dependencies are excluded.

Tests use synthetic credentials and read-only backend inputs. Test containers
have no external network, published ports, provider routes, host devices, Docker
socket or production data. Persistence checks share only the disposable database
container's loopback namespace. The harness does not start production Compose
services or the application lifespan. Dependency installation is a separate
network-enabled preparation step. Full runs are serialized by a host lock.

Each run prints its retained directory. `manifest.json` records source selection
and hashes; `images.json` and `dependencies.json` record runtime identities.
`checks.json`, `results.json` and logs record commands, outcomes and skipped
checks. `artifacts/` holds pytest, schema, diagnostic and restore evidence.
`source-integrity.json`, production before/after records, `owned-containers.json`
and `cleanup.json` record preservation and resource cleanup.

CI runs [ci_evidence.py](../scripts/validation/ci_evidence.py) with `if: always()`
before uploading its new curated directory. Its explicit producer allowlist keeps
named source/result/check, dependency, storage and cleanup reports; known command
logs; each suite's JUnit and query measurements; named schema/restore summaries;
and the runtime smoke report/PDF when produced. It does not recursively upload
`artifacts/`: pytest temporary trees, generated keys, input images, raw database
state/dumps, environments, caches and unrelated JSON/log files remain excluded.
The restore rehearsal still creates, restores and verifies its disposable dump.
The collector never deletes owning-test output. `collection.json` records retained
hashes, bytes, missing reports and partial/failed runs. A claimed successful run
missing required proof fails collection; partial failure evidence is still staged.
Symlinks/escapes are refused, storage admission and the shared bulk lock apply,
and an existing destination cannot be reused. Add intentional producer locations
and collector tests together when new evidence is required. The current CI job
does not produce browser artifacts; browser checks retain their separately scoped
failure evidence locally. Local collector tests do not establish a hosted upload.

Prerequisite, diagnostic, cleanup or production-comparison failures return
nonzero. A failed migration prevents dependent persistence checks; schema checks
can still assess their own scratch databases. Normal interrupts clean up only
containers owned by the run. After SIGKILL or host loss, use the retained ownership
record to identify that run's resources. Do not delete unrelated resources.

## Recovery and schema proof limits

Security/reliability regressions run through the actual isolated API, transport
and PostgreSQL owners in `test_account_authority.py`, `test_realtime_authority.py`,
`test_notification_recipient_binding.py` and `test_historical_discovery_persistence.py`
under `scripts/validation/`. The populated upgrade fixtures are
`test_notification_binding_upgrade.py` and `test_historical_discovery_upgrade.py`.
These are registered full-harness suites; copied route bodies are not substitutes.
The historical suite also exercises the real periodic loop, repeated bounded
cycles, dense timestamps, interrupted acceptance and fresh authority/lease fences.
All external provider boundaries remain inert and unmocked external sockets fail.

Affected browser coverage is `frontend/e2e/realtime-invalidation.spec.ts` and
`frontend/e2e/notification-recipients.spec.ts` in Chromium and WebKit, including
desktop and phone widths. API fixtures cannot fall through to production; run with
network isolation and the matching locked browser dependencies. Retain screenshots,
failure traces and exact source hashes. Socket-overhead measurements use three
synthetic accounts, one/ten connections, five warmups and 50 samples; statement
counts and ASGI timings are proxies, not database wire bytes or production latency.

[Schema checks](../scripts/validation/test_schema_contract.py) exercise frozen
historical fixtures, fresh/staged migrations, schema parity and guarded downgrade
behavior. [The restore rehearsal](../scripts/validation/database_restore.py) checks
synthetic schema, linked rows, sequences, revision and uncertain work while
preserving its source database. These checks do not validate a production backup,
every deployed database, or downgrading populated production data. Read the
touched [migrations](../backend/alembic/versions/) before an authorized upgrade or
rollback; retirement migrations can irreversibly remove data.

For an authorized rollback or restore, use a matching source/image/schema pair
and retain durable attempts and uncertainty. `IACS_RECOVERY_HOLD=true` starts a
compatible application in inspection posture: [startup](../backend/app/main.py)
verifies exact Alembic-head compatibility without migrating and leaves executors
stopped. [Recovery middleware](../backend/app/recovery_hold.py) permits
authentication and bounded recovery reads, rejects other requests and WebSockets,
and returns 503 for readiness. Its `_READ_PATHS` defines the permitted reads.

An older backup can lose receipts for external effects accepted after that
backup. Reconcile that gap before reactivation; a readable schema and successful
restore do not establish that resuming work is safe. A code rollback must not
erase durable attempts or reset uncertain work to pending.

## Maintainability and performance checks

The dependency guard requires zero detected runtime cycles. It also checks vendor
business back-imports, directory route transaction ownership, shared UI imports,
and command/presence ownership. Its only retained construction exception is the
existing synthetic simulation fixture. Keep its algorithm tests alongside changes
to the scanner. Static scanning does not prove dynamic imports or every ORM write.

Treat these checks as gates for future work. Do not add new cycle/writer allowances
to [the baseline](../scripts/architecture/baseline.json), narrow lint coverage or
remove failing assertions to accommodate a new dependency. A justified exception
at an existing boundary needs a local explanation and behavior coverage; it must
not mask a provider failure or change uncertain-command recovery semantics.

When introducing or extracting an owner/contract module, classify its owner in
[the single type policy](../scripts/validation/type_targets.py). The harness derives
checked-body and strict commands from that policy; strict targets also enter the
checked-body command. Cohesive package targets include new modules automatically.
Mixed access/workflow packages have finite legacy overrides preserving the
reviewed coverage; new files inherit the package default. Do not add legacy
exemptions. The frozen [initial coverage fixture](../scripts/validation/type_coverage_baseline.json)
is a regression floor, not a second registration list. Host tests reject missing
protected coverage, invalid/overlapping owners and unclassified suites. Full-source lint covers `app`;
the `test-names` check covers undefined names in backend and validation fixtures,
including cases skipped in DB-free mode. Fixtures must patch the actual owner or
inject its port, not restore retired imports or convenience getters. Classify new
isolated suites in [the required inventory](../scripts/validation/recovery_checks.py)
and run both host harness/source-selection tests when that machinery changes.

For directory or selector changes, cover page/filter boundaries, ties and malformed
cursors, selected IDs outside the first page, inactive/deleted metadata, refresh
with unchanged first-page data, disabled/cancelled reads and draft preservation.
Compact-photo tests must prove the raw columns stay unloaded and do not lazy load,
while URLs, fallback snapshots and full-media detail reads still work.

For hot-path changes, compare equivalent results before timing them. Use both small
and target-scale synthetic fixtures with the same locks/tools; retain dataset size,
query count, scanned/returned rows, actual query plans, repeated timings and peak
allocation/payload size where applicable. Verify functional indexes with the real
bound query and generic prepared plans. Check latest-snapshot lookups for absent
plates as well as matches. Do not add brittle wall-clock assertions to unit tests
or infer production latency, PostgreSQL memory or PDF performance from a narrower
Python/query benchmark. Record any fixed overhead or unresolved growth in the
selected-period output.

[Directory persistence checks](../scripts/validation/test_directory_operations.py)
exercise 1,000 people and 2,000 vehicles, cursor ties/filter changes, selected IDs
outside the current page, totals/search and bounded payloads. They retain
`directory-target-scale.json` in the suite's pytest temporary directory, including
complete EXPLAIN JSON for every captured query, query counts, returned items,
payloads, repeated timings and Python allocation peaks. Passing pytest capture
does not discard these artifacts.
[Report benchmarks](../scripts/benchmarks/reports.py) compare the old history
strategy with predecessor reads at small and one-million-movement scales and
assert equal duration output. [Snapshot benchmarks](../scripts/benchmarks/directory_snapshots.py)
compare complete-history ranking with indexed per-plate latest-row reads.
[Plate query plans](../scripts/benchmarks/plate_lookup.sql) use a temporary
2,000-registration table and roll it back. The required
[prepared-query regression](../scripts/validation/test_plate_lookup_index.py)
checks the actual worker query with PostgreSQL forced to use a generic prepared
plan, so repeated requests retain the normalized active-plate index. It writes
`plate-generic-plan.json` with the actual compiled query and complete plan,
including execution/buffer details. These artifacts include the exact harness
source fingerprint, backend image, dependency lock hashes and Python/package
identities through [the evidence writer](../scripts/benchmarks/evidence.py).

Run these benchmarks only in disposable, network-isolated PostgreSQL namespaces
with synthetic credentials and locked tools. The Python scripts fail closed on
non-synthetic environments; inspect their CLI and guards before running. Retain
JSON measurements and complete query plans outside the repository. Run the Python
benchmark entrypoints as modules from the source root with the backend on
`PYTHONPATH` (`python -m scripts.benchmarks.reports` or
`python -m scripts.benchmarks.directory_snapshots`); both require `--output`.
Supply `IACS_IMAGE_IDENTITY` for a standalone container. Standalone source-tree
fingerprints identify available safe files and are labeled separately from the
harness snapshot fingerprint. Record the dataset,
query/returned-row counts, repeated timings and peak Python allocation together.
Timings vary with host contention; these are relative structural checks, not
production latency budgets. Report output/PDF costs and PostgreSQL memory are
not included in the report predecessor benchmark. Small datasets can incur a
fixed allocation increase from the additional bounded queries.

The required [vehicle information persistence suite](../scripts/validation/test_vehicle_information.py)
checks leased jobs, notification handoff/deadlines, stale registration writes,
server-paged snapshots, Redis throttling and bounded summary reads at 2,000 vehicles.
Plate-change fixtures prove snapshot/summary rollback with failed audit, caller
transaction ownership, cached replacement and rejection of late old-plate results
while preserving user-entered fields. Inert hardware HTTP fixtures require the
real AsyncSession commit port before returning rejected or uncertain gate/cover
receipts; no provider is invoked.
Provider/token, date precedence and cache coalescing tests use synthetic responses.
The [MOT browser suite](../frontend/e2e/mot-history.spec.ts) uses fixture-only HTTP
and closed WebSockets, covering lazy history, pagination and retained editor drafts
in Chromium/WebKit at desktop, tablet, phone and short landscape sizes. Screenshots
remain under the existing Playwright evidence directory. No test contacts DVSA,
DVLA, notification providers or hardware; passing fixtures do not validate live credentials.

The MOT persistence suite also retains `mot-scale-20.json` and
`mot-scale-2000.json` in its test artifact folders. These compare identical
directory pages before and after adding 25 tests per stored snapshot, including
query count, returned rows, payload size, Python allocation peaks and the indexed
snapshot-read plan. Nullable NI/commercial fields are inert fixtures, never live
provider calls.

### Diagnostics and observer checks

The [writer fixtures](../backend/tests/test_diagnostic_writer.py) exercise actual
20/2,000-record blocked intake, the shared 1,024-record/16-MiB budget, accepted-prefix
flush and the ten-second work deadline with actual cleanup ownership. The
[observer fixtures](../backend/tests/test_observer_lifetimes.py) cover nested inline
publication, repeated cancellation, scheduler coalescing and HA refresh reentry.
The registered [PostgreSQL suite](../scripts/validation/test_diagnostics_audit_persistence.py)
proves 250 diagnostic rows use three INSERT batches and required audits survive closed
diagnostic intake; HA and unmatched-trigger audit failures roll back their participant
transaction. [Command-journal fixtures](../backend/tests/test_access_device_command_journal.py)
use actual SQL audit failure to prove uncertain delivery fencing, truthful recovery,
idempotent outcome audit and full post-close hint compatibility. These selected checks
support owner integration; the full integrated harness remains the stage gate.

### Generated workflow contract freshness

The full and db-free harness run
`python -m app.services.workflows.export_contracts --check` inside the locked
backend dependency environment. CI uses the same harness. After changing pure
action/channel config or defaults, run
`python -m app.services.workflows.export_contracts` from `backend/` with those
locked dependencies to regenerate `frontend/src/api/generated/workflows.ts`.
Unsupported schema constructs fail explicitly; do not hand-edit generated output.

Workflow conformance covers exhaustive bindings/config/editor defaults, rejected
new writes, historical display, unsupported pending review holds and unchanged
attempted identities. The disposable PostgreSQL notification-activation suite
also proves a test-only new transactional action reserves domain work with its
mutation, receipt and audit in one commit, rolls them all back on audit failure,
preserves retry identity and forbids expired claims and external ports.


### Completion repository checkpoints

The isolated [completion persistence suite](../scripts/validation/test_access_completion_persistence.py)
checks immutable window/ingest receipt provenance, explicit no-ingest external
origins, bounded ID selection/free-slot leasing, expired-prefix continuation,
origin versus public revision, caller rollback, lease expiry after blocked locks,
clean final flush and retained-link envelope readiness. Forged future diagnostic
trace times do not extend optional provider authority. Boolean JSON corruption
fixtures explicitly mark the JSONB attribute modified because Python otherwise
compares `True` equal to `1` and can suppress the intended test UPDATE.

These focused repository checks do not substitute for the full harness or prove
stage-specific provider result acceptance, canonical visitor-link mutation,
frontend revisions or composed lifecycle behavior. Those remain required P6
runtime/applicator gates. Retain failed setup/progress runs and exact source
mappings when correcting a fixture; never describe replacement output as the
original run.


The [accepted-result PostgreSQL suite](../scripts/validation/test_access_result_persistence.py)
checks actual compact visual/information receipts, late USED/closed-window arrival
hydration, rejected late acceptance, immutable origins, canonical saga/reservation
proof, partial-to-final coherence and field ownership through replacements/clears.
It exercises required-audit rollback, actual blocked-lock lease/deadline expiry,
cancellation rollback and no duplicate enrichment audit. Fat event/vehicle fixtures
assert preparers select identity columns without raw evidence/photo columns; this is
a SQL selection proof, not an RSS or throughput benchmark. Controlled test clock
advancement separates T+4-minute acceptance from T+6-minute linking without renewing
provider or hardware authority. Other expiry fixtures cross actual PostgreSQL time
while blocked on a lock. Linkers have no provider capability. Vehicle information
is reserved in the owning transaction with original receipt provenance; the old
enqueue bridge is retired. Complete integrated P6 validation remains a separate gate.

The [chart coalescer suite](../scripts/validation/test_leaderboard_coalescer_persistence.py)
checks progress during arrivals on every actual aggregation, silent/live trailing
provenance, latest-generation crash reclaim, atomic leader/intent rollback, original
expiry, a real blocked final-configuration clock boundary, legacy unattempted
retirement and unchanged attempted UNKNOWN recovery. The [historical candidate
suite](../scripts/validation/test_historical_candidates_persistence.py) covers
concurrent startup/periodic discovery of one provider identity, immutable original
facts, drain outside the discovery window or with discovery disabled, actual HA
root-busy whole-transaction rollback, five-attempt holds and one media preparation
across retries. Provider and actual file-thread cancellation must drain before
releasing ownership; rollback retains media and a committed link prevents cleanup.
Only terminal skipped, proved-unlinked token paths may be deleted. The existing
historical pipeline case also reads the actual `/api/v1` list/detail and durable
publication consumers. These are isolated correctness fixtures; integrated P6
lifecycle/full-harness and burst performance gates remain separate.

Malformed retained verification states, visitor UUIDs and result-readiness envelopes
must become named durable holds. Their negative fixtures verify accepted source
results remain retained and no SQL conversion or exception/requeue loop substitutes
for the hold. Earlier passing snapshots remain evidence of their exact source;
corrected-source results are mapped explicitly.


The [pipeline foundation suite](../scripts/validation/test_access_pipeline_persistence.py)
also checks HA URLs longer than 2,048 characters and entities longer than 255,
including equal prefixes with distinct full identities and retained UNKNOWN effects.
It verifies TEXT/nonindexed identity columns alongside bounded target keys, and
refuses downgrade before DDL when any accepted information revision, timestamp or
projection alone would be lost. These are inert schema tests, without provider I/O.


The [information-worker PostgreSQL suite](../scripts/validation/test_vehicle_information_worker.py)
checks actual free-capacity leases, competing owners, terminal-page continuation,
original retry/deadline provenance, fresh renewal/failure clocks after a blocked
lock, clean-flush final expiry, takeover and conservative malformed-origin holds.
Accepted information projections survive those outcomes. Provider fixtures verify
no database connection over I/O and no result write from a lost claim. These are
correctness checks, without a performance claim.

[Shared-client lifetime tests](../backend/tests/test_vehicle_information_lifecycle.py)
exercise sibling provider failure, repeated cancellation of lookup/connection tests,
two occupied worker slots during blocked cleanup, result-transaction cleanup and
concurrent stop/close. Real main lifespan fixtures cross its shortened outer stop
timeout, keep clients open for notification enrichment, and distinguish an admitted
ASGI lookup's completion from its parent's later unrelated work. Both client close
attempts remain owned. Actual AsyncSession close barriers cover settings cache
misses, normal/cancelled worker exits and main claim/preparation shutdown. Genuine
PostgreSQL fixtures keep the event row locked through an active-work timeout or
stop, proving SQL cancellation finishes without starting a provider or renewing
authority. Claim, renewal, failure and result normal-close barriers remain owned
through first and repeated cancellation. Provider responses are inert; this does
not test a live DVLA/DVSA account or the deployment server's general request-drain
behavior. The before/after history-growth read fixture supplies one real runtime
outside both measurements so the independent two-second settings cache cannot
change the measured query count; actual measured SQL is retained.

When the local validation backend image changes, prepare exact-lock dependencies
again with `--allow-downloads` and the explicitly selected
`IACS_VALIDATION_BACKEND_IMAGE`, then retain the resulting immutable image IDs and
dependency manifests. Matching Python versions alone does not satisfy the harness's
reuse guard. Historical image/setup failures and prior results remain attached to
their original evidence; replacement runs receive distinct identities.


The [media persistence suite](../scripts/validation/test_access_media_persistence.py)
is registered in the full harness. It verifies snapshot event/anomaly/checkpoint/
publication atomicity, final expiry rollback, exact thumbnail identity, immutable
compact input, current registration and pre-I/O deadline guards. It also covers
startup completion-owner exclusion, claim-specific artifact cleanup versus unknown
commit retention, postcommit zone signals and no duplicate inserts after signal
failure. The original five-second camera window uses immutable receipt provenance;
forged future trace metadata cannot renew it.

[Media artifact tests](../backend/tests/test_access_completion_media.py) retain
legacy/claim-specific restore and exact deletion contracts, actual compact/write/
archive thread cleanup, a delayed actual camera child, hydrated alert projection,
and full large relevant zone arrays. [Lifetime tests](../backend/tests/test_access_completion_lifecycle.py)
prove two occupied slots during cleanup, independent publication, single cancellation
after renewal loss, actual session close, deadline consumption and an injected owner
through real main lifespan before DB disposal. That injection is composition proof,
not production activation or final provider shutdown-order approval.

The conservative absolute start deadline is sampled before the awaited DB clock
response and survives actual close/preparation/scheduling. Both completion and
information have one retained parent timer rescheduled only downward; nested timers
must not cancel an already-cleaning child again. A fresh final DB fence still decides
acceptance. Publication's start guard uses lease expiry independently of optional
five-minute work. These are inert correctness fixtures, without live providers or
new production timing/memory claims. Exact run identities, failed snapshots and
carried-source mappings live in the closure/candidate receipts.

The whole [publication unit file](../backend/tests/test_access_publication.py)
also characterizes renewal loss after slow preparation and before the send
window expires, with blocked actual transport cleanup. Crossing that deadline
and repeatedly cancelling stop cannot deliver a second cancellation, release
the lane, ACK/retry lost ownership or admit another identifier. A stalled actual
transport child cannot start after its original deadline. Its complete18-case
PG/Redis suite is rerun for the corresponding runtime correction.


The [confirmation rejection persistence suite](../scripts/validation/test_confirmation_rejections.py)
uses real preheld Admin row locks and both production outer adapters to prove
rollback-before-audit without a foreign-key self-block. It covers missing, consumed
and invalid tokens, rollback of caller mutations and token rejection state, token
reuse after rollback, original scalar correlation, and visible required-audit failures.
Missing-token 428 remains unaudited. Complete existing notification, authority,
feature and schedule suites run with it; their old detached-audit monkeypatches
are retired. [Confirmation units](../backend/tests/test_action_confirmations.py)
include actual session-close ownership under repeated cancellation. This prerequisite
does not activate HA effects or close the integrated P6/P8 gates.


The originally dormant, now integrated [HA store persistence suite](../scripts/validation/test_ha_presence_store.py)
uses the existing fail-closed disposable PostgreSQL guard. It exercises participant
rollback, current Person/settings/maintenance authority, original receipt budgets,
shared-target ordering, immutable old receipts, UNKNOWN barriers, required-audit
failure, complete terminal-page continuation and fresh post-lock/post-flush lease
checks. A blocked accepted-receipt close must prevent a second owner claiming until
actual cleanup and exact-token release; terminal renewal cannot rewrite the receipt.
Selected SQL with a large profile photo proves the binding projection excludes that
blob. Long complete identities remain lossless. The complete resident recovery suites
check the canonical fingerprint extraction, and foundation checks retain the frozen
0013/model contract. These source fixtures do not imply HA startup, live provider I/O
or completion of the pending core/runtime gate.

Focused runners may mount the retained exact-lock dependency directory read-only at
/deps after checking the manifest hashes and immutable tooling image. Their receipts
record the concrete dependency path and that no duplicate copy was created. Source,
runtime, results and disposable database binds remain isolated. The full harness's
dependency preparation contract is unchanged.

The originally dormant, now integrated [worker lifetime fixtures](../backend/tests/test_ha_presence_worker.py)
cover receipt ownership loss during SQL and actual close, confirmed-claim close,
original send deadlines, accepted POST truth during cancelled optional observation,
repeated stop and dedicated-client cleanup. The
[acknowledgement PostgreSQL suite](../scripts/validation/test_ha_presence_resolutions.py)
prepares exact preview/confirmation/audit/barrier atomicity, retired origins,
matching receipt retry after newer active work, live local/durable capacity rejection,
stale projections, revoked Admin authority and rollback-before-rejection-audit cases.
Focused run `iacs-validation-dkfy1aiw` passes 100 PostgreSQL cases: foundation 15,
resident 26, store 40 and acknowledgement 19, with migration parity, isolation,
unchanged source, empty cleanup and equal operational snapshots. The acknowledgement
tests retain 13 SQLAlchemy transaction-reset warnings after the explicit original
transaction rollback. A subsequent one-line auth fake signature correction leaves
all runtime, schema and dependency bytes unchanged. The corrected immutable Linux
run passes 1,375 backend tests with 29 expected skips, types 100/83 and Ruff/F821.
The earlier helper setup failures and stale fake failure remain retained. These
focused checks do not replace the full integrated harness or approve HA activation.

The HA setting-creation regression pauses the actual `settings.update_settings`
writer after its advisory lock is acquired and before a missing URL row is created.
The reservation participant must report `ha_setting_busy`, roll back the complete
owning transaction, and on retry retain its original origin/deadline while reading
the writer's committed connection. It must reach the setting fence rather than
pass solely because an auth-root lock is busy. This corrects a namespace mismatch
found after the dormant owner's original focused checks; that evidence remains
retained separately from the affected store rerun.


## UniFi bounded stream fixtures

[Runtime fixtures](../backend/tests/test_unifi_protect_runtime.py) use inert SDK
messages, fake operations and isolated recorders. They cover 20/2,000-input bursts,
callback count/byte budgets including active work, probe pending/active context
bytes, exact release, retention caps/TTL, immediate gap invalidation, webhook
survival, full-field extractor parity, original timestamps/deadlines, late probes,
provider replacement and current evidence. Cancellation-resistant operation/worker
and blocked-close cases verify actual cleanup; reentrant lifecycle rejection is
checked before any state mutation and followed by a successful external stop.
Delayed actual-child fixtures close admission, replace the provider or expire the
original deadline before I/O and assert no SDK call begins. Confirmed test fixtures
retain accepted receipts after repeated caller cancellation and actual cleanup,
preserve failure before acceptance and verify ordinary read cancellation.
These fixtures do not contact UniFi, actuate devices or establish production sizing.

[Installed SDK fixtures](../backend/tests/test_unifi_protect_sdk.py) construct the
locked client and models with socket connections denied and only HTTP transport
replaced. They check raw event conversion/filtering and request bounds, exact
clip camera/time exports and media denial, ongoing/empty failures, snapshots,
package-channel permissions, current visual/audio detections, LPR/vehicle metadata
and three-channel subscription cleanup. Real public WebSocket frames verify
vehicle evidence at event start/end and SDK disconnect invalidation. Existing
runtime/lifecycle fixtures
retain cancellation, disconnect/gap invalidation and provider-replacement checks.

The [SDK workload](../scripts/benchmarks/unifi_sdk.py) runs the same small and
20,000-event source fixtures against baseline/candidate source snapshots and their
own exact locked SDKs. Retain equivalent public-output hashes, request/response row
counts, Python peak allocation and JSON bytes, plus 20/2,000 private and public camera projections.
It denies sockets and makes no SQL calls; SQL plans are not applicable to this
vendor-read comparison. Synthetic elapsed times are not production guarantees.

Retain source/tool/lock identities with before/after callback workload evidence.
Queue/context accounting bounds retained work; SDK response and synchronous
extractor allocation before admission is a separate transient cost. Supplemental
socket-denied macOS checks do not replace the required Linux isolated harness,
persistence, migrations and restore rehearsal.


## Compact read candidate checks

The [compact-read PG suite](../scripts/validation/test_compact_reads_persistence.py)
checks rank/group/nullable media semantics, selected snapshot lookup including
unknown byte sizes and unavailable latest files, four-field public presence order,
one-statement counts/roster, C-collated name ties, recorded-before-current forecast
attribution, duplicate owner/assignment identity and DST/horizon boundaries. It
asserts executed column projections and no entity hydration. The
[browser fixture](../frontend/e2e/compact-presence.spec.ts) checks global/hidden
counts, six-name order and recent-event Exited Today at phone, desktop and dark
tablet sizes; it must run in both configured browsers on owned source assets.

[Compact read measurements](../scripts/benchmarks/compact_reads.py) run the same
public entry points and fixed synthetic inputs in immutable baseline and candidate
snapshots with exact locked dependencies. Run only inside the guide's loopback-only
migrated disposable database/container; the script refuses other database/network
namespaces. Examples within that owned environment:

```sh
python -m scripts.benchmarks.compact_reads --phase baseline --suite charts --events 10000 --output /evidence/chart-small.json
python -m scripts.benchmarks.compact_reads --phase candidate --suite charts --events 1000000 --output /evidence/chart-target.json
python -m scripts.benchmarks.compact_reads --phase candidate --suite presence --people 2000 --output /evidence/presence-target.json
python -m scripts.benchmarks.compact_reads --phase candidate --suite forecast --people 2000 --density high --output /evidence/forecast-target-high.json
```

Run **each** workload against **both** source snapshots, using the identical fixture
body and all arguments except phase/output path. The full matrix is charts
10,000/1,000,000 events; presence 20/2,000 people; forecast 20/2,000 people at low
(12 weekly days × two movements) and high (60 days × four movements) density.
A separate small `--fat-unused` experiment transfers synthetic unused photo/raw
strings on the baseline without making million-event blobs a forced OOM test.
Each receipt retains fixed input cardinality, source/tool/lock identities, three
query/time/Python-allocation samples, actual executed driver SQL/parameters and
complete EXPLAIN ANALYZE plans, plus public output bytes/SHA and JSON. Chart
`generated_at` alone is removed from equality; exact snapshot-time tie behavior
has a separate documented fixture. Assert equivalent supported output before
interpreting cost changes. Python allocations are not process RSS; timings are
synthetic, full-history aggregation remains, and forecast/public population reads
are not constant-memory claims. Prepared fixtures and bounded progress checks do
not substitute for the complete comparison matrix, full isolated harness and
exact-candidate review. Retain the original failed attempts and map fixture-only
repairs separately from changes to application behavior.


On restart, preserve historical results but disclose absent raw evidence rather
than treating the ledger as a current gate. The historical P7 comparison and
required checks use the persistent evidence sibling at
`/Users/jas/.codex/worktrees/iacs-maintainability-frontend/validation-evidence`.
The comparison is recorded in `p7-compact-comparison/comparison.json` there.
The complete 11-pair comparison retains equal public outputs and three samples
per phase. Its target chart is slightly slower and selected unknown-snapshot
bitmap scans remain material; lower Python allocation does not establish bounded
scanned work, a general speedup, process RSS or PostgreSQL memory bounds.


### Composed core checkpoints

The [event-driven worker suite](../scripts/validation/test_event_driven_workers.py)
checks committed notifications, rollback, subscription catch-up, reconnect,
generation races, listener failure, bounded batches and deadline ordering against
isolated PostgreSQL. The [upgrade suite](../scripts/validation/test_event_driven_upgrade.py)
checks migration 20's indexes, preserved claims and receipts, atomic failure and
the restore-required downgrade. Backend scheduling tests cover cancellation,
capacity, held work and coordinator renewal failure. These supplement the earlier
durable accounting tests below.

The [event-driven cost suite](../scripts/validation/test_event_driven_efficiency.py)
executes actual worker loops for 125 seconds. Each healthy idle lane may perform
at most three readiness reads, allowing initial scheduling and two 60-second repair
reads. Startup/acquisition is recorded outside the measured window; LPR lease renewals
and listener liveness queries are recorded separately. Compact readiness queries
are exercised with 100 and 100,000 retained rows, empty queues, future work and
multiple due batches. Custom and generic plans must keep examined rows bounded
and discovery must avoid locks, writes and payload hydration. Counts, plans,
equivalent outputs, repeated timings and Python allocations are retained in
`query-efficiency.json`. An optional read-only frozen baseline compares the old
actual loops using `IACS_EVENT_DRIVEN_BASELINE`; candidate budgets remain mandatory
without it. Timings are evidence, not CI thresholds or production CPU guarantees.
Both source versions use the same migrated synthetic schema. Python allocations
include query-capture instrumentation and retained SQL records; they exclude
PostgreSQL memory and process RSS.

The checked [timer inventory](../scripts/validation/background_polling_inventory.json)
records the owner and purpose of remaining timers. Its mandatory host check fails
when timer sites change without a corresponding review. The
[zone refresh browser fixture](../frontend/e2e/zone-shadow-refresh.spec.ts) checks
committed hints, no 15-second polling, 60-second visible repair and retained editor
state in Chromium and WebKit using synthetic HTTP and WebSocket fixtures.
See [background workers](background-workers.md) for the scheduling contract and
the deliberate hardware, security and recovery timers that remain.

The mandatory [idle worker suite](../scripts/validation/test_idle_background_work.py)
checks terminal accounting, rollback/retry, concurrent callers, deterministic receipts,
deleted rules, legacy exclusion and audit purge; it also drives empty LPR boundaries,
local and fallback wakeups, arrivals during the probe, retained flush/preparing state,
debounce expiry, terminal-prefix advancement, lease takeover and cancellation/shutdown.
The existing [automation dispatch suite](../scripts/validation/test_automation_dispatch.py)
proves audit failure recovery cannot replay the provider action. The
[upgrade suite](../scripts/validation/test_idle_background_upgrade.py) checks fresh and
populated migration, null/duplicate/unrelated receipt metadata, preserved counters and
execution checkpoints, transactional migration failure and the restore-required downgrade.

The [idle cost suite](../scripts/validation/test_idle_query_efficiency.py) enforces
one application SELECT per empty LPR pass, no UPDATE or SELECT FOR UPDATE, and no
payload hydration. Empty audit discovery reads no audit history. Custom and generic
prepared plans must use the pending-audit partial index and indexed LPR existence
checks. Fixtures compare 1,000/4,000 and 100,000/400,000 runs/audits with empty and
nonempty pending queues, plus 1,000/100,000 handled LPR ingests and completed windows. Actual worker queries,
equivalent outputs, plan rows/buffers, repeated timings and Python allocation peaks
are retained in each suite's `query-efficiency.json`. Old discovery has a 750 ms
statement timeout; timeouts remain evidence. Correctness and bounded database work
are gates; timings are not. Python allocations exclude PostgreSQL memory and process
RSS. Synthetic measurements do not establish production CPU savings.
The same fixture executes the migration's exact backfill SQL at both scales and
requires one pass over completion receipts, avoiding a repeated materialized-history
scan when the receipt set exceeds PostgreSQL's working memory.

The [actual pipeline suite](../scripts/validation/test_access_pipeline.py) drives
persisted intake, coordinator claims and window execution. Its blocked visual
completion and blocked vehicle-information fixtures require a subsequent core
decision to commit while optional work remains blocked, then prove cancellation
and replay cannot issue another gate command. Required camera preparation is not
an optional latency fixture. Camera and database-close fixtures retain actual
child cleanup through repeated cancellation and reject success selected after
the original decision deadline.

The [window suite](../scripts/validation/test_access_window_persistence.py) covers
expired restart/reclaim against the total original three-attempt budget, whole
window holds, stop/restart retention, actual health backlog and authenticated
80-member evidence pagination beyond the 50-item preview. Its 4/80 distinct-window
comparison builds equivalent pending state with 128 KiB vendor bodies, compares
first matching window/rank and retained work, captures indexed 50-row query pages
and EXPLAIN ANALYZE/BUFFERS output. Python allocation peaks measure load/append,
not setup, PostgreSQL memory or process RSS; local timings are not production
claims. Near-limit webhook comparisons separately retain complete evidence and
bounded preview equivalence. Preserve original failing runs and the affected-suite
correction mappings in the closure record; selected checks do not replace the
required full composed harness or browser checks.

The final composed gate must use one frozen source snapshot after the P8 and
E1–E5 fixture integration. Earlier focused approvals retain their stated scope.
Lifecycle fixtures cover shared HA-client disposal after blocked notification
drain, private-client ownership and repeated shutdown cancellation. Parent audit
fixtures verify mandatory expiry rollback, existing deterministic audit recovery
and full standalone/garage refresh payloads after the actual database close.
The harness removes only containers whose exact validation label matches its
owned prefix, including their anonymous image-declared volumes; it never prunes
unidentified volumes. Source and harness guards protect that cleanup boundary.

## Retained programme validation evidence

The completed maintainability programme's final evidence index is
`/Users/jas/.codex/worktrees/iacs-maintainability-programme/validation-evidence/final-candidate/evidence-index.json`.
It binds approved source commit `c8dd4a4eca88c5351a72a7171d1cc6da70326641`
and tree `a312785f075f2c832eede0a343c1dfd4e8bf9ea6` to commands, logs,
source mappings, runtime image identities and independent review approval.
This is historical validation of those exact bytes, not a gate for later edits
or a statement of the current deployment state.

The mapped composed gate combines full run `iacs-validation-9v9u9m9t` with
whole-backend/static amendment `unit-amendment-uf0y3y3h`: 1,495 backend cases,
1,145 PostgreSQL cases across 62 suites, 376 frontend cases and the frontend
build passed. The 35 journal cases skipped in the database-free invocation
passed in the PostgreSQL journal suite. Source/configuration checks passed
22/30 cases; architecture and 141 checked/117 strict type targets passed
without baseline expansion or rule relaxation. Browser evidence maps 182
distinct cases: 36 freshly run Chromium/WebKit cases and 146 unchanged retained
cases. Runtime PDF, installed SDK import and performance evidence retain their
exact-source mappings and stated limits.

This is a mapped composed gate, not one all-green full run. The full run's
exact-clock assertion failure was replaced by the deterministic fixture in
`c92cf2d` and the passing whole-backend/static amendment. Earlier failed runs
remain failed evidence. Historical raw evidence under
`/private/tmp/iacs-maintainability-programme` was lost after restart and remains
unavailable; recorded approval facts do not imply those raw files are retained.
Keep the external final evidence index and its referenced files.

The superseded review, remediation plan, P6 design and detailed closure ledger
were removed from the current documentation after programme completion. They
remain in Git history at `e5ec5c9`; for example, retrieve the historical ledger
without changing the checkout:

```sh
git show e5ec5c9:docs/architecture-maintainability-closure.md
```

Use [Architecture](architecture.md) for implemented owners and extension rules,
[Development](development.md) for setup and recovery, and
[Integrations and safety](integrations.md) for provider and physical effects.
