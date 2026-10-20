# Development

Use this guide for a new local instance and routine source work. The
[architecture guide](architecture.md) explains ownership;
[integrations and safety](integrations.md) covers provider and hardware effects;
[validation](validation.md) defines the required checks.

## Report export limits

Preview and PDF activity share a 10,000-row representation limit: selected events,
all-site timeline rows and selected timeline rows count separately. Subjects may
have at most 10,000 vehicle metadata rows for a report. Oversized requests fail
with HTTP 413 and an actionable period/subject message; results are never silently
truncated. PDF image data URLs total at most 25 MiB, with a separate 25 MiB source
read guard and 64 Mi-pixels of source canvas across embedded occurrences.
Multi-frame images and external/non-data profile sources cannot be exported;
upload a supported static raster profile photo. Public preview URLs and stored
photos remain unchanged. Invalid image sources return 400, size limits 413.

Each process admits one export and rejects concurrent requests with 503 and
`Retry-After: 1`. The whole operation has a 120-second deadline (504 on expiry),
with actual cleanup drained before admitting another operation. Final authority
is checked again before the atomic export/audit commit. If commit outcome cannot
be resolved, 503 includes a reconciliation ID; do not blindly retry an unknown
outcome. Accepted commits retain their receipt. See the measured
[runtime resource envelope](validation.md#final-runtime-pdf-capability) when sizing
exports; input caps do not guarantee a fixed memory footprint.

## Start a new local instance

Install Docker with Compose v2. The images supply Python 3.12, PostgreSQL 16 with
pgvector, Redis, and the frontend build tools. For host-side frontend work use
Node.js 22.22.2+ (22.x), 24.15+ (24.x), or 26+ and npm; use host Python 3.12
for the validation entrypoint.
The backend shared image ancestry installs locked Playwright Chromium and native
libraries, so the non-root runtime can render reports. Check final-image packaging
with the [runtime PDF smoke](validation.md#final-runtime-pdf-capability).
Image definitions and dependency installation are in the
[backend Dockerfile](../backend/Dockerfile) and
[frontend Dockerfile](../frontend/Dockerfile).

From the repository root, for a new instance with no existing configuration:

```sh
cp .env.example .env
mkdir -p data/backend data/postgres data/redis logs/backend logs/frontend
docker compose -f docker-compose.yml up --build
```

Review [`.env.example`](../.env.example) before starting. Keep the explicit
`-f docker-compose.yml`: the ignored `docker-compose.override.yml` selects an
operational image pair and installation-specific mounts, and plain
`docker compose` automatically loads it. The obsolete core-reset preview Compose
file and its stopped installation were retired after programme completion.

The [base Compose file](../docker-compose.yml) defines these addresses:

| Service | Default host address | Container port |
| --- | --- | --- |
| Console and API proxy | `http://localhost:8089` | 80 |
| Backend API | `http://localhost:8088/api/v1` | 8000 |
| PostgreSQL | `127.0.0.1:5432` | 5432 |
| Redis | `127.0.0.1:6379` | 6379 |

Frontend and backend ports are published on all host interfaces. PostgreSQL and
Redis bind to loopback by default. Keep initial setup local; replace development
credentials and review network exposure before using an instance on a shared
network.

Compose uses host bind mounts: database files under `data/postgres`, Redis
persistence under `data/redis`, application files under `data/backend`, and logs
under `logs/`. The backend runs as a non-root user; its data and log directories
must be writable by that user. Preserve these directories when recreating
containers.

Normal startup runs Alembic migrations when `IACS_AUTO_CREATE_SCHEMA=true`, then
seeds missing product settings and access-device configuration. It also starts
background services and configured integrations. `IACS_SEED_DEMO_DATA` is
currently ignored; it does not create a sample installation. See
[database startup](../backend/app/db/bootstrap.py) and
[application lifecycle](../backend/app/main.py).

Open `http://localhost:8089` and complete first-run setup. The
[setup endpoint](../backend/app/api/v1/auth.py) creates an active Admin and locks
initial setup once any user exists. Read-only startup checks are:

```sh
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/auth/status
```

## Local cleanup and retention

A development or validation run must never exhaust host disk or take production
offline. The 14 October 2026 incident accumulated repeated dependency copies,
stopped synthetic databases and Docker build cache. This must not recur. Apply
the [disk-space safety procedure](validation.md#disk-space-safety) before creating
more working data; neither `/private/tmp` nor an evidence directory is an
unlimited storage allowance.

Retain resources according to their purpose:

| Resource | Retention rule |
| --- | --- |
| Dependencies and browsers | One verified preparation per required lock/runtime identity. Keep at most one active disposable copy; remove completed copies before another run. Do not silently substitute incompatible dependencies to save space. |
| Synthetic databases and runtime files | Retain commands, fixtures, results and failure diagnostics, then remove run-owned state after container cleanup and a fresh mount check. A failed test is not a reason to retain an entire database indefinitely. If raw state is needed to diagnose a failure, record its size, owner and bounded retention before another run. |
| Review and validation evidence | Keep the starting source needed for task-only diffs, reviewed/tested source identities, new files, sanitized logs, results and required review artifacts. Evidence retention does not require retaining installed packages, browser downloads or raw database directories. |
| Tooling and test images | Reuse compatible immutable images. Remove superseded task images only after checking exact identities, all tags and running/stopped container references; preserve current production and required rollback images. |
| Other checkouts | Preserve uncommitted work and task ownership. Never delete or reset a checkout merely to recover space; use the supported worktree lifecycle after its owner releases it. |

Keep `docker-compose.yml` as the portable development definition and the ignored
`docker-compose.override.yml` as the private operational image/storage receipt.
Keep active `data/backend`, `data/postgres`, `data/redis`, logs and authentication
secrets in place. Retain the latest deployment recovery checkpoint under
`data/checkpoints` and the validation evidence supporting release approval.
Delete older backup and preview trees only after their retention is explicitly
authorized and no container mount uses them. Do not recreate a retired preview
from historical receipts without a separately authorized deployment.

Inventory existing checkpoints before making another full backup. Reuse an
existing image archive when its verified image identities satisfy the new
checkpoint, recording the shared archive reference and checksum. Database and
application-data backups represent separate recovery points even when their sizes
match. Consolidate duplicate archives only when rollback instructions remain
usable; do not remove a complete checkpoint on the strength of an image match.
Never place credentials or production backups in portable review bundles.

Docker's unused volumes can still hold recovery databases. Inspect references
from stopped containers as well as running ones, retain named/nonempty volumes
for individual review, and avoid a blanket volume prune. Historical temporary
containers can leave anonymous volumes behind even when test data used bind
mounts. The validation harness removes its owned containers with `docker rm -f -v`
so their anonymous volumes are removed too; it does not prune existing or
unidentified volumes.

The standalone dashboard prototype and historical design screenshot collections
have been retired; their tracked source and images remain in Git history. The
maintained application lives in `backend` and `frontend`, with regression,
architecture and performance tooling under `scripts`. Keep migrations and their
historical schema fixtures: they are required to validate upgrades and recovery.

Generated build output and Python/type-check caches can be removed when no local
process uses them. The obsolete root virtual environment and broken backend
virtual environment were removed during cleanup. Usable development dependencies
belong under `backend/.venv` and `frontend/node_modules`; restore them using the exact
locked installation commands under [Work on source and dependencies](#work-on-source-and-dependencies).
Historical browser outputs removed from the
checkout are retained outside the repository at
`/Users/jas/.codex/backups/iacs/folder-cleanup-20261012/historical-browser-results`;
future browser runs write fresh outputs to `frontend/test-results`.

## Work on source and dependencies

Backend `app/` is mounted read-only into the development container; the repository
is mounted at `/workspace`. Python edits become visible through the mount, but
the configured Uvicorn command has no automatic reload: restart the development
backend to load them. Dependencies and migrations are baked into the backend
image and require a rebuild when changed. Frontend assets are baked into its
Nginx image, so rebuild the frontend image to show UI edits in Compose.

For frontend hot reload against the local backend:

```sh
cd frontend
npm ci
npm run dev
```

Vite serves port 5173 and proxies API requests and WebSockets to backend port
8088, as defined in [Vite configuration](../frontend/vite.config.ts). Application
clients use relative `/api/v1` URLs and the existing
[typed API modules](../frontend/src/api/).

Integration editor changes belong to the complete keyed provider owner under
[features/integrations](../frontend/src/features/integrations/), with draft/busy
protection supplied by the shared dialog frame. Classify each catalog entry as
managed or scalar and keep managed component coverage exact. A new access-device
provider extends the ordered [binding descriptors](../frontend/src/features/settings/accessDeviceProviders.ts)
and its checked backend policy/adapter contract; do not add provider branches to
the shared editor. Run the inert third-descriptor, confirmation/account lifetime
and affected browser fixtures described in [validation](validation.md).


Install the exact reviewed dependency sets: Python packages come from
[`uv.lock`](../backend/uv.lock), and frontend packages from
[`package-lock.json`](../frontend/package-lock.json). The backend image pins uv
and uses `uv sync --locked`; host-side Python tooling should use the same pinned
uv version from its Dockerfile and `uv sync --locked --extra dev` from `backend/`.
The application does not update dependencies or install runtime package overlays.
Dependency changes belong in reviewed manifests and locks. The
[Project Checks workflow](../.github/workflows/project-checks.yml) audits the
complete locked Python set, including development dependencies, and the frontend
lock, and builds both images.

The backend lock includes `multidict` 7.1.0, which fixes the C-extension
reference leak in [GHSA-54p9-h82j-f925](https://github.com/aio-libs/multidict/security/advisories/GHSA-54p9-h82j-f925).
The frontend lock includes `source-map-js` 1.2.2, which rejects malformed or
oversized indexed source-map offsets ([GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)).
Preserve these fixes when refreshing the dependency locks.

Certifi is constrained to 2026.10.19 or later to remove the compromised emSign
trust root ([GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2)).
Rebuild backend images after updating this lock so the installed CA bundle changes.

The October 2026 refresh updates application and development dependencies,
including `uiprotect==23.1.2` and Mako 1.4.3 (the Windows
`TemplateLookup` traversal fix is in 1.4.2,
[GHSA-5639-2j2p-m4mx](https://github.com/sqlalchemy/mako/security/advisories/GHSA-5639-2j2p-m4mx)).
Apprise 2 returns a result object; the notification adapter accepts only a
successful result and retains failure handling for partial delivery, no matching
services and timeouts. ESPHome resolves to 46.10.0 on the container platform;
the lock retains 45.6.2 on Intel macOS because newer ESPHome releases cap
`cryptography` below the reviewed application version on that platform.
Python and JavaScript Playwright have independent locked versions; install each
package's matching browsers. The UniFi wrapper uses raw-event conversion, camera
video exports and the current SDK camera projection contract; see the
[SDK integration contract](integrations.md#unifi-protect-sdk-contract).

SQLAlchemy stays on the patched 2.0 line (`>=2.0.54,<2.1`). Review transaction
and fixture ownership separately before changing this dependency bound; retain
the supported recovery expiry and rollback assertions.

Use the [isolated validation harness](validation.md) for backend regression.
If backend source is mounted into a running installation, perform code changes
and validation in an isolated checkout. Existing installation startup,
deployment, production migration, provider sends, and hardware tests require
separate authorization.

## Change review and handoff

Before editing, record the checkout path, branch/HEAD and existing changes. Reuse
an appropriate isolated checkout when source is mounted into a running application.
Use a named task branch for new isolated work; identify existing detached work
explicitly at handoff. Preserve the original working state and separate pre-existing
changes from the implementation diff. Do not claim work is committed, merged or
deployed when it is only present as local edits.

Before implementation, identify the domain owner, callers and contracts using
[architecture](architecture.md#extension-rules). State the behavior being preserved
and, for a hot path, how read/query count, retained history and payload size scale.
Extend an existing owner when its responsibility fits; a new feature should not
require copying policy into routes, the shell, vendor modules or multiple facades.

Before handing off a change, review the complete implementation against these
requirements and fix concrete issues found:

1. Policy, transactions, authorization, hardware and durable handoffs have the
   existing owners; composition is inert and dependencies remain explicit.
2. Modules have cohesive responsibilities and typed contracts. New owners are
   included in the applicable harness type targets. No new cycle, compatibility
   shim, blanket lint suppression or weakened architecture baseline is introduced.
3. Collection/history reads and payloads have explicit bounds; media and related
   records do not hide unbounded reads, lazy loads or per-row queries. Relevant
   performance evidence preserves equivalent results and states its limits.
4. Frontend selection, invalidation, cancellation, permissions and dirty drafts
   remain correct, including selected records outside the current page. Feature
   mutation/retry refreshes await their own read owners; unrelated operational
   failures must not relabel an accepted save or deletion as a failed mutation.
5. Callers, tests, registrations, migrations and affected guides change together.
   Run the [required checks](validation.md#choose-the-checks); fix failures and
   rerun checks affected by the repair. Do not substitute DB-free checks for full
   persistence, recovery and restore validation.

Handoff includes a plain-English result, the exact worktree and branch/HEAD,
commit state, checks and retained evidence, unresolved limitations and deployment
status. Explain measured performance using the tested dataset and operation; do
not turn a synthetic substep benchmark into an end-to-end latency claim.
Also report starting/final free space, peak task storage when measured, retained
dependency/image identities, disposable cleanup results and any remaining storage
owner or retention decision. A release handoff must not leave unbounded temporary
data behind.

## Configuration, authentication, and proxying

[`core/config.py`](../backend/app/core/config.py) owns `IACS_` bootstrap
configuration: database and Redis connections, paths, environment, CORS, trusted
hosts, public URL, and schema/recovery posture. Compose reads `.env` and supplies
the backend environment. Product settings, such as timezone, auth cookie options,
LPR tuning, and provider credentials, are managed through the console and stored
in the database by [the settings service](../backend/app/services/settings.py).
Environment defaults seed missing settings; they do not overwrite existing
database settings on each restart.

On first development startup, [auth secret loading](../backend/app/core/auth_secret.py)
generates `data/backend/auth-secret.key`. This root secret signs sessions and
protects stored secrets through [Fernet encryption](../backend/app/core/crypto.py).
Back it up with the database and application files, and keep it out of Git.
Use Settings → Auth & Security for supported file-backed rotation. The advanced
`IACS_AUTH_SECRET_KEY` override disables UI rotation. Outside development/test
environments, startup requires an existing non-placeholder secret file or
environment override and rejects the default PostgreSQL password.

For a reverse proxy, target the frontend port 8089 and enable WebSocket upgrades.
[Nginx](../frontend/nginx.conf) forwards `/api/` and WebSockets to the backend;
port 8088 remains available for direct API debugging. Set `IACS_PUBLIC_BASE_URL`
to the externally reachable URL, restrict `IACS_TRUSTED_HOSTS`, and configure
`IACS_CORS_ORIGINS` for the browser origins in use. With HTTPS, enable the secure
auth cookie in Auth & Security. `IACS_ROOT_PATH` declares a backend proxy subpath;
the supplied frontend configuration assumes hosting at `/`, so a subpath also
needs an explicitly matched proxy/frontend configuration.

## Schema upgrades, backup, and recovery

Use [Alembic migrations](../backend/alembic/versions/) for schema changes.
`IACS_AUTO_CREATE_SCHEMA` controls `alembic upgrade head` at startup; it is not an
ORM table bootstrap. For a managed deployment, disable automatic migration and
apply an explicitly authorized migration using the matching backend artifact.
Stop old workers before upgrading, retain matching source and backend/frontend
images, and back up PostgreSQL plus application files and the auth secret before
any destructive change.

Read the touched migrations' upgrade and downgrade behavior. The
[updater retirement](../backend/alembic/versions/20260920_0008_remove_dependency_updaters.py)
drops data and only recreates empty structures on downgrade. The
[assistant/messaging retirement](../backend/alembic/versions/20261005_0010_retire_assistant_messaging.py)
refuses downgrade and requires a matching database/file backup and image pair.
Changing images alone cannot recover retired data. Retained files from retired
features are not permission to delete them during an upgrade.

[`IACS_RECOVERY_HOLD`](../backend/app/recovery_hold.py) starts a compatible image
without migration or background executors, checks that the retained database
revision matches that image's Alembic head, and permits authentication plus a
bounded set of recovery reads. It rejects mutations and WebSockets and reports
readiness as 503. Before reactivation, reconcile external effects accepted after
the restored backup: those receipts may be missing from the restored database.
The harness's synthetic restore rehearsal validates its fixtures, not an
installation's real backup or permission to resume delivery.

## Directory read contracts

People and vehicle list endpoints under `/api/v1` return
`{items, total, next_cursor}`. The default page has 50 items; `limit` must be
between 1 and 200. Search uses `q`, active-state filtering uses `active`, and
people may be filtered by `group_id`. Cursors belong to the same filters and
ordering; discard them when a filter changes. Ordering includes the record ID
to handle equal display names or registrations.

Use `GET /people/{id}` and `GET /vehicles/{id}` for detail. Explicit `ids`
lookups and vehicle `registrations` lookups are bounded to 200 values per request.
Use the typed directory client for URL encoding, pagination and selected-record
hydration. Repository callers have migrated together; the old array response is
retired. External consumers must adopt the page contract before deployment.

The normalized active-plate index is migration `20261006_0011`. It adds no
columns or data transformations, and its downgrade removes only that index.
Production migration and deployment remain separately authorized operations.

Vehicle information uses additive migration `20261007_0012`: nullable summary
provenance fields, separate per-vehicle snapshots and leased arrival jobs. It does
not backfill or contact providers. Downgrade refuses populated snapshots/jobs;
use a matching backup/source restore instead of deleting durable work. DVSA is
disabled by default. Configure it only through the existing confirmed settings
workflow; local credential files are not runtime dependencies. Public contracts
and cache/recovery behaviour are documented in [integrations](integrations.md#vehicle-information-and-mot-history).

Durable access work uses migration `20261007_0013`. Its downgrade refuses
populated intake, window, completion, publication, HA or chart state; it does not
turn durable work back into the retired RAM pipeline. Recover with a verified
matching source/image/schema and database/file backup, or a compatible fix-forward
image. An old image alone cannot process this state safely. Inspect uncertain
physical receipts and outstanding original-authority work under recovery hold
before enabling workers; a restore cannot undo effects that happened after backup.
