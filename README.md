# Intelligent Access Control System

IACS turns license-plate reads into durable access and movement decisions,
presence, alerts, and audited gate/garage operations. It includes Home Assistant
and UniFi Protect integrations, notification workflows, Alfred conversational
operations, and a realtime React console.

Use the [documentation index](docs/README.md) for current guides and
[AGENTS.md](AGENTS.md) for repository-specific agent instructions.

## Development setup

For a new local development instance:

```bash
cp .env.example .env
mkdir -p data/backend data/chat_attachments data/postgres data/redis logs/backend logs/frontend
docker compose up --build
```

Review the example configuration before starting. Normal startup runs migrations
when `IACS_AUTO_CREATE_SCHEMA=true` and starts background integrations. An existing
installation's Compose stack is operational infrastructure; deployment and
production migration are separate authorized tasks. Use the isolated harness
below for regression work.

| Service | Default host address | Container port |
| --- | --- | --- |
| React console / API proxy | `http://localhost:8089` | 80 |
| Backend API | `http://localhost:8088/api/v1` | 8000 |
| PostgreSQL | `127.0.0.1:5432` | 5432 |
| Redis | `127.0.0.1:6379` | 6379 |

Ports are configured in `.env`; Compose uses host bind mounts only. The backend
mounts `backend/app` read-only at `/app/app` and the repository at `/workspace`
for workspace log inspection. Frontend assets are built into its image, so UI
source edits require a new frontend build to appear in Compose.

On first development start, the backend generates `data/backend/auth-secret.key`.
It signs sessions and protects stored dynamic secrets. Back it up, keep it out of
Git, and use Settings → Auth for supported rotation. Non-development startup
requires an existing secret file or a non-default `IACS_AUTH_SECRET_KEY`.
Complete the first-run Admin setup through the console.

For Nginx Proxy Manager, target `http://<docker-host-ip>:8089`, enable WebSocket
support, and retain forwarded headers. The frontend proxies `/api/*` and
WebSocket upgrades to the backend. Use `/api/v1` for application integrations.
Backend port `8088` is available for direct API debugging.

Anonymous read-only checks:

```bash
curl -fsS http://localhost:8089/api/v1/health
curl -fsS http://localhost:8089/api/v1/auth/status
```

Operational data and mutations use authenticated routes. Gate commands,
announcements, notification sends/tests, and other privileged actions require
Admin confirmation and durable audit. Simulation arrival/misread endpoints can
actuate hardware; use isolated fixtures for tests. See
[hardware safety](docs/agent/hardware-safety.md) before live integration work.

## Development and validation

Backend entrypoints and owners are in the [backend guide](docs/agent/backend.md);
routes, typed clients, and styling are in the [frontend guide](docs/agent/frontend.md).
Use [architecture](docs/architecture.md) when changing service boundaries or
retiring features. API routing is defined in `backend/app/api/router.py` and
`backend/app/api/v1/`; frontend contracts live in `frontend/src/api/`.

The [isolated harness](docs/validation/phase1.md) snapshots working-tree source,
including new first-party files, and uses disposable resources without production
credentials or provider access. Its full mode includes backend and frontend
checks, migrations, persistence, recovery diagnostics, and database restore:

```bash
docker build --target development -t iacs-validation-tooling:local backend
PHASE1_BACKEND_IMAGE=iacs-validation-tooling:local \
  python3 scripts/phase1/validate.py --mode full --allow-downloads \
  --evidence-root /private/tmp/iacs-validation-evidence
```

The build prepares Python/uv tooling without starting application services; the
explicit tag avoids depending on the local Compose project name. This permits
image downloads and exact-lock dependency installation. Use
`--reuse-dependencies /absolute/path/to/prior-run` instead when a matching trusted
preparation exists. Source-only checks use `--mode snapshot`; they do not validate
application behavior. The `scripts/backend-pytest` wrapper can select the running
Compose backend and is unsuitable for isolated regression.

Schema changes use Alembic; there is no runtime compatibility schema bootstrap.
The application also does not update its own dependencies or UniFi packages.
The [updater retirement note](docs/releases/remove-dependency-updaters.md) retains
migration and rollback constraints.
