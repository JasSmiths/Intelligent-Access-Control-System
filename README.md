# IACS

Intelligent Access Control + Presence System turns license-plate reads into
access decisions, recorded movements, presence, alerts, and audited gate and
garage operations. Its React console manages people, vehicles, schedules,
visitor passes, integrations, notifications, and automations, with history,
reports, and camera image analysis.

The backend uses FastAPI, PostgreSQL, and Redis. Home Assistant, UniFi Protect,
and ESPHome connect IACS to cameras and access devices.

## Documentation

| Task | Guide |
| --- | --- |
| Run a development instance; understand configuration and storage | [Development](docs/development.md) |
| Find the owner; understand access, movement, workflows, and the console | [Architecture](docs/architecture.md) |
| Change integrations or code that can affect devices and external services | [Integrations and safety](docs/integrations.md) |
| Choose checks and interpret validation evidence | [Validation](docs/validation.md) |

[AGENTS.md](AGENTS.md) contains repository instructions for coding agents.
Current source and tests define behavior; these guides do not establish that a
checkout has been deployed or validated.

## Local development

For a **new local instance**, install Docker with Compose, then run:

```sh
cp .env.example .env
mkdir -p data/backend data/postgres data/redis logs/backend logs/frontend
# Review .env before starting; this explicitly selects the development Compose file.
docker compose -f docker-compose.yml up --build
```

Open `http://localhost:8089` and complete the first Admin setup. The backend API
is at `http://localhost:8088/api/v1`. PostgreSQL and Redis default to loopback
ports 5432 and 6379. See [development](docs/development.md) before using an existing
installation: startup can migrate its database and start configured integrations.

Use the [isolated validation harness](docs/validation.md) for regression work.
Simulation endpoints can affect hardware. Live commands, provider sends,
production migrations, and deployment need separate authorization.

[prototypes/premium-dashboard](prototypes/premium-dashboard/README.md) is a
separate fixture-driven design prototype with its own instructions.
