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
npm run test:e2e
```

Browser installation can download packages. The
[Playwright configuration](../frontend/playwright.config.ts) runs Chromium and
WebKit on loopback port 5174. Its [Vite configuration](../frontend/vite.e2e.config.ts)
has no backend proxy. The [browser suites](../frontend/e2e/) fulfill synthetic
`/api/v1` requests, reject unexpected requests and close WebSockets. Outside CI,
Playwright may reuse an existing server: check that port 5174 belongs to this
fixture-only configuration before running.

Review affected screens at desktop, tablet, narrow phone and short landscape
sizes. Check overflow, readable text, reachable actions, focus, editor dismissal
and retained state across resizing; include relevant themes and role restrictions.
The responsive suite owns the viewport matrix and safe-area checks. Review
screenshots under `frontend/test-results/playwright/`; traces and failure
screenshots use the same output directory. Browser emulation does not establish
physical-device keyboard, zoom, hinge or safe-area behavior. Report unavailable
device checks as limits.

## Isolated harness

[validate.py](../scripts/validation/validate.py) copies and hashes the current source,
then executes against private resources. It does not isolate earlier edits to
an operational bind mount: use an isolated checkout for code changes when the
backend is mounted into a running application. Do not use `scripts/backend-pytest`
for isolated regression; it can select the running Compose backend.

Execution needs Docker and a compatible local backend tooling image with Python
3.12 and `uv`. Build a dedicated image, allowing its image/package downloads:

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
immutable backend/Node image identities; it copies dependencies into the new
run and verifies them offline. Reuse verifies dependency selection, not package
trust. Installation may execute package code.

The harness runs the repository dependency/side-effect scan once as
`architecture-boundaries`. Backend tests separately cover the scanner algorithms.
Access persistence tests verify command ordering and outcomes; synthetic timing
samples are not performance acceptance checks.

Full mode runs host source/configuration tests, architecture and Python checks,
backend tests, full-source Ruff checks, undefined-name checks for backend and
isolated validation fixtures, checked-body mypy checks, strict typed
contract checks, frontend tests/build and Compose configuration
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

Prerequisite, diagnostic, cleanup or production-comparison failures return
nonzero. A failed migration prevents dependent persistence checks; schema checks
can still assess their own scratch databases. Normal interrupts clean up only
containers owned by the run. After SIGKILL or host loss, use the retained ownership
record to identify that run's resources. Do not delete unrelated resources.

## Recovery and schema proof limits

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

[Directory persistence checks](../scripts/validation/test_directory_operations.py)
exercise 1,000 people and 2,000 vehicles, cursor ties/filter changes, selected IDs
outside the current page, totals/search and bounded payloads. They retain
`DIRECTORY_TARGET_SCALE_EVIDENCE` in pytest output.
[Report benchmarks](../scripts/benchmarks/reports.py) compare the old history
strategy with predecessor reads at small and one-million-movement scales and
assert equal duration output. [Snapshot benchmarks](../scripts/benchmarks/directory_snapshots.py)
compare complete-history ranking with indexed per-plate latest-row reads.
[Plate query plans](../scripts/benchmarks/plate_lookup.sql) use a temporary
2,000-registration table and roll it back. The required
[prepared-query regression](../scripts/validation/test_plate_lookup_index.py)
checks the actual worker query with PostgreSQL forced to use a generic prepared
plan, so repeated requests retain the normalized active-plate index.

Run these benchmarks only in disposable, network-isolated PostgreSQL namespaces
with synthetic credentials and locked tools. The Python scripts fail closed on
non-synthetic environments; inspect their CLI and guards before running. Retain
JSON measurements and query plans outside the repository. Record the dataset,
query/returned-row counts, repeated timings and peak Python allocation together.
Timings vary with host contention; these are relative structural checks, not
production latency budgets. Report output/PDF costs and PostgreSQL memory are
not included in the report predecessor benchmark. Small datasets can incur a
fixed allocation increase from the additional bounded queries.
