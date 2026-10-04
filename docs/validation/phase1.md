# Isolated regression validation

`scripts/phase1/validate.py` snapshots the current checkout and runs checks against
private resources. Use it for backend, schema, or cross-system changes. If source
is mounted into an operational application, make code changes in an isolated
checkout; the harness isolates its execution, not earlier edits to a live mount.
Commands below run from the checkout being assessed.

Full and DB-free execution require Docker and a backend tooling image with Python
3.12 and `uv`. Prepare a dedicated image without starting application services:

```bash
docker build --target development -t iacs-validation-tooling:local backend
export PHASE1_BACKEND_IMAGE=iacs-validation-tooling:local
```

Building may download images/packages. An existing compatible local image can be
selected instead. The default harness tag is `intelligentaccesssystem-backend:latest`;
Compose's generated image tag depends on its project name (`iacs` in `.env.example`).
`--allow-downloads` does not build the backend tooling image. Snapshot mode and
the host-only tests below do not need it.

## Choose the scope

```bash
# Host-only source selector and harness configuration tests.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_source_snapshot.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_harness_configuration.py

# Copy and hash source; no Docker, app imports, installation, or application tests.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py --mode snapshot \
  --evidence-root /private/tmp/iacs-validation-evidence

# Full regression, allowing image downloads and installation from exact locks.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py --mode full \
  --allow-downloads --evidence-root /private/tmp/iacs-validation-evidence

# Full regression using an existing matching, trusted dependency preparation.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py --mode full \
  --reuse-dependencies /absolute/path/to/prior-run \
  --evidence-root /private/tmp/iacs-validation-evidence
```

Full mode is the default. Execution requires `--reuse-dependencies` or
`--allow-downloads`; the bare command deliberately refuses to guess. Reuse must
match the copied Python/Node manifests and locks and the immutable backend/Node
image IDs. It copies dependencies to the new run and checks them offline. It is
not a package-content security attestation. Dependency preparation can execute
package installation code; test isolation does not make untrusted packages safe.

`--mode db-free` runs static checks, DB-free backend tests, frontend tests/build,
and Compose configuration validation without database services or migrations.
It still requires Docker and prepared dependencies. It cannot prove persistence,
command recovery, or schema behavior. `--check-only` and `--no-migrations` are
aliases for snapshot and DB-free modes. Snapshot success proves source capture
only; do not report it as a passing application regression.

For documentation-only changes, validate links, source references, and
`git diff --check`. For frontend-only changes, use the
[frontend guide](../agent/frontend.md) and [browser checks](gui-completion.md).
Do not repeat unrelated suites merely because old milestone notes listed them.

## Current execution inventory

[recovery_checks.py](../../scripts/phase1/recovery_checks.py) owns the mandatory
host, persistence, diagnostic, and schema inventory. The harness rejects missing,
duplicated, or unclassified `scripts/phase1/test_*.py` checks. Add new suites there
with their execution class; avoid maintaining a second test catalog in prose.

Full mode runs:

1. Isolation preflight, host selector/configuration tests, architecture checks,
   Python compilation, DB-free backend pytest, scoped Ruff/mypy plus a broader
   undefined-name check, frontend Vitest/build, and Compose `config --quiet`.
2. Disposable PostgreSQL/Redis setup and Alembic upgrade/current/check.
3. Required schema CLI checks and recovery diagnostics, plus the persistence
   inventory. Each pytest file gets a fresh clone of the migrated template
   database, its own Redis database, and retained pytest artifacts.
4. A synthetic `pg_dump`/`pg_restore` rehearsal, source-integrity verification,
   owned-resource cleanup, and before/after production-container comparison.

Boundary diagnostics and schema checks are mandatory in full mode, not optional
follow-up work. The restore rehearsal is also mandatory; `--database-restore`
is only an explicit spelling of that default. The host lock rejects concurrent
full runs. See [recovery boundaries](recovery-boundaries.md) and
[schema/restore validation](recovery-schema.md) for contracts and proof limits.
[Missed-exit recovery](missed-exit-recovery.md) has both DB-free evidence tests and
mandatory guarded PostgreSQL suites.

Repeat `--persistence-test`, `--diagnostic`, or `--schema-check` to add a
repository-relative test/script. These are additive to full-mode defaults and
become required snapshot inputs. Other modes reject them. A schema CLI takes no
extra arguments and must retain evidence under `/results`. Review any added
script's side effects before execution.

## Source and dependency selection

The snapshot copies tracked files from working-tree bytes, including staged and
unstaged edits, and omits tracked deletions. Safe untracked files in `backend/`,
`frontend/`, `scripts/`, `docs/`, and `.github/` are included automatically.
Relevant ignored files fail closed unless explicitly included or classified as
generated/runtime exclusions. Untracked files elsewhere are listed in the
manifest; use `--include REPO_RELATIVE_FILE` when they affect the task.

Symlinks, traversal paths, directories passed as inclusions, and unsafe inputs
are rejected. Runtime data, logs, credentials, `.env*`, local agent/Git state,
virtual environments, caches, build artifacts, and `node_modules` are excluded.
No checkout, reset, or stash changes the assessed tree. The selection owner is
[source_snapshot.py](../../scripts/phase1/source_snapshot.py).

The backend tooling image must provide Python 3.12 and `uv`; it does not supply
the assessed application/dependency set. `PHASE1_BACKEND_IMAGE` overrides the
default `intelligentaccesssystem-backend:latest`. Node and PostgreSQL/Redis image
pins come from the snapshot's Dockerfile and Compose configuration. Dependency
preparation uses `uv sync --locked --extra dev --no-install-project` and
`npm ci --ignore-scripts` with copied manifests.

## Isolation and evidence

Choose an evidence root outside the repository and operational bind mounts.
Execution modes inspect production container metadata before creating resources;
snapshot mode never contacts Docker, so choose a known private path yourself.
The default root is `~/Documents/IACS Regression Baselines`.

Tests use synthetic credentials, read-only backend source/dependencies, and
network-none containers. Persistence containers share only the isolated
PostgreSQL container's loopback namespace; there are no published ports, provider
routes, host devices, Docker socket, production data, or named volumes. Checks
run serially with bounded CPU/memory. The harness does not run the app lifespan,
production Compose services, live endpoints, or `scripts/backend-pytest` (which
can select the running Compose backend). Demo seeding is disabled.

Each run prints its retained directory. Use:

- `manifest.json`: source identity, included/excluded files, working-tree status,
  hashes/fingerprint, dependency manifests, and host identity.
- `images.json`, `image-references.json`, `dependencies.json`: image and dependency
  identity; accompanying logs retain offline verification.
- `checks.json` and logs: exact commands, classifications, outcomes, and reasons
  checks were not attempted. `results.json` is the compact result summary.
- `artifacts/`: JUnit, schema, diagnostic, and restore evidence. Only this directory
  is writable at `/results`; it does not expose the parent run or database files.
- `source-integrity.json`, `production-before.json`, `production-after.json`,
  and `owned-containers.json`: input preservation and cleanup evidence.

Failed prerequisites, diagnostics, cleanup, or production comparison return
nonzero. A failed migration prevents dependent persistence tests; guarded schema
checks may still run against their own scratch databases. Report the actual
attempted scope and failures, not just a green subset. Retain verbose logs and
summarize relevant results rather than copying them into long-lived guides.

Timeouts and normal interrupts clean up only resources bearing the run's unique
label. SIGKILL or host loss cannot run cleanup; use the retained ownership record
to identify that run's resources. Production deployment, migrations, sends, and
[supervised hardware tests](../agent/hardware-safety.md) remain separate tasks.
