# Development

Use this guide for a new local instance and routine source work. The
[architecture guide](architecture.md) explains ownership;
[integrations and safety](integrations.md) covers provider and hardware effects;
[validation](validation.md) defines the required checks.

## Start a new local instance

Install Docker with Compose v2. The images supply Python 3.12, PostgreSQL 16 with
pgvector, Redis, and the frontend build tools. For host-side frontend work use
Node.js 22 and npm; use host Python 3.12 for the validation entrypoint.
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
`-f docker-compose.yml`: the committed `docker-compose.override.yml` selects an
operational image pair and installation-specific mounts, and plain
`docker compose` automatically loads it. The standalone preview Compose file
also targets separately prepared images and storage; it is not a development
override.

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

The backend lock includes `multidict` 6.9.1, which fixes the C-extension
reference leak in [GHSA-54p9-h82j-f925](https://github.com/aio-libs/multidict/security/advisories/GHSA-54p9-h82j-f925).
The frontend lock includes `source-map-js` 1.2.2, which rejects malformed or
oversized indexed source-map offsets ([GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)).
Preserve these fixes when refreshing the dependency locks.

Use the [isolated validation harness](validation.md) for backend regression.
If backend source is mounted into a running installation, perform code changes
and validation in an isolated checkout. Existing installation startup,
deployment, production migration, provider sends, and hardware tests require
separate authorization.

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
