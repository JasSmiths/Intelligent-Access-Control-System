# Paired-image reset preview

Keep the production Compose files and deployed images unchanged. Build in an
isolated checkout and use `docker-compose.preview.yml` as a standalone file,
never as an override of the production stack. The preview has independent
bind-mounted PostgreSQL, Redis, backend files, attachments and logs. Only its
frontend is published, on `127.0.0.1:8189`; production stays on port 8089.

## Data and isolation

A direct database copy retains users, encrypted integration settings, workflow
definitions and pending work. Take a logical `pg_dump -Fc` from production;
restore with `pg_restore --no-owner --no-privileges` into the private preview
database. Do not copy a running PostgreSQL data directory. Copy backend files,
chat attachments and the effective auth secret into private preview directories,
without displaying their contents. A configured `IACS_AUTH_SECRET_KEY` takes
precedence over the mounted key file; preserve that effective key and any
previous rotation key. Redis starts empty so live leases, queues
and sessions are not shared or replayed from a cache copy. Retain the original
database dump and file copy independently of the writable preview.

Before starting an application container with copied data, verify the preview
backend network has `Internal=true`, IPv6 disabled and
`com.docker.network.bridge.gateway_mode_ipv4=isolated`. Verify it has no route
to the host or an external address. Docker's [isolated gateway mode](https://docs.docker.com/engine/network/port-publishing/#gateway-modes)
removes the host bridge address; plain `internal:true` is insufficient. The
backend, PostgreSQL and Redis attach only to that network. The frontend also
joins a separate web network for its loopback port and reverse proxy. No live
`.env`, Docker socket, source checkout or production data path is mounted.

Provider-dependent features are unavailable in this preview. Copied workers
may record unsuccessful attempts in the private database; these are not live
test results. Do not connect the preview backend to another network. Hardware,
provider sends and production deployment require separate authorization under
the [hardware guide](../agent/hardware-safety.md).

Open `http://iacs-reset.localhost:8189` in the browser to keep its cookies
separate from production. Copied dynamic auth settings take precedence over
environment defaults, so the default preview cookie name alone cannot isolate
a restored database. Browsers resolve `.localhost` names to loopback. Log in
with the copied user account at the preview address. Its records
diverge from production after the snapshot; neither environment synchronizes
changes back to the other.
Verify the restored `auth_cookie_secure` policy before using HTTP: the database
also overrides that environment default. A Secure cookie requires an
appropriate browser/TLS setup; do not treat an HTTP 200 login response alone
as proof that the browser can retain and return the session cookie.

## Preparing a future preview pair

Record both running image IDs before development. Give those IDs immutable
baseline tags, then build each reset milestone under new paired tags. Never
overwrite a baseline tag or use a moving `latest` tag. Keep a private receipt
with image IDs, source snapshot identity, database revision and snapshot path.
Building an image does not replace running containers.

Store a private environment file outside tracked source, containing:

```dotenv
IACS_PREVIEW_BACKEND_IMAGE=iacs-backend:reset-20261005-03
IACS_PREVIEW_FRONTEND_IMAGE=iacs-frontend:reset-20261005-03
IACS_PREVIEW_ROOT=/absolute/private/path/to/preview-storage
IACS_PREVIEW_PORT=8189
```

For a future build in an isolated checkout, choose an unused milestone tag. The
example below uses `reset-20261005-03`; verify that it is unused before building.
Do not rebuild the retained original or reset tags:

```sh
docker build --target runtime -t iacs-backend:reset-20261005-03 backend
docker build -t iacs-frontend:reset-20261005-03 frontend
```

Use the private environment file explicitly for every preview command:

```sh
docker compose --project-name iacs-reset-preview --env-file /absolute/private/path/preview.env -f docker-compose.preview.yml config --quiet
docker compose --project-name iacs-reset-preview --env-file /absolute/private/path/preview.env -f docker-compose.preview.yml up -d postgres redis
# Restore the dump and verify isolation before proceeding.
docker compose --project-name iacs-reset-preview --env-file /absolute/private/path/preview.env -f docker-compose.preview.yml run --rm --no-deps --entrypoint /app/.venv/bin/alembic backend upgrade head
docker compose --project-name iacs-reset-preview --env-file /absolute/private/path/preview.env -f docker-compose.preview.yml up -d --no-build backend frontend
docker compose --project-name iacs-reset-preview --env-file /absolute/private/path/preview.env -f docker-compose.preview.yml down
```

Apply migrations explicitly to the preview database; automatic schema updates
remain disabled. Do not start the backend against an unprepared database.
Keep all preview storage outside operational bind mounts; directories must be
writable by the image's respective application user.

Compare production and preview through their separate browser addresses.
After destructive retirement migrations, changing the image alone cannot
restore compatibility. To run an older preview pair, stop this preview and
select an independent storage directory restored from its matching database
and file snapshot. Preserve the candidate storage for returning to it later.
Production rollback likewise needs matching images and recoverable data.

## Verification

Check standalone Compose configuration, storage separation, image IDs, network
membership and blocked backend egress before application startup. Check the
preview health endpoint and login without running integration tests or
simulation injection. Confirm production container IDs/image IDs and bind
paths are unchanged afterward. Continue using the [isolated harness](phase1.md)
for source, application, schema and restore regression; this browser preview
does not replace it.

## Switching the retained local previews

Run these commands from the current checkout described in
[project layout](../project-layout.md). The private selectors are:

- `data/previews/original.env`: original pre-retirement backend/frontend images
  with `data/previews/original/` data at revision `20261002_0009`.
- `data/previews/reset.env`: the reset backend/frontend images with
  `data/previews/reset/` data at revision `20261005_0010`.

Both use the same preview address, so stop the preview before selecting the other.
The original production images also retain their `baseline-reset-20261005` tags.
These commands use the standalone preview project and do not select production.

Switch to the original preview:

```sh
docker compose --project-name iacs-reset-preview --env-file data/previews/reset.env -f docker-compose.preview.yml down
docker compose --project-name iacs-reset-preview --env-file data/previews/original.env -f docker-compose.preview.yml up -d --no-build --wait
```

Return to the reset:

```sh
docker compose --project-name iacs-reset-preview --env-file data/previews/original.env -f docker-compose.preview.yml down
docker compose --project-name iacs-reset-preview --env-file data/previews/reset.env -f docker-compose.preview.yml up -d --no-build --wait
```

These selectors pair images with their own prepared data. Keep the files private
and preserve both directories plus `data/previews/snapshots/` and
`data/previews/receipts/`; do not substitute a production mount. Changes made in
either preview stay in that version. Historical receipts retain their embedded
paths; `data/relocations-20261005.json` maps their relocated storage.
