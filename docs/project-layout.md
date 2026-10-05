# Project layout

## One current checkout

The current source is `/Users/jas/Documents/Intelligent Access System` on
`codex/core-reset`. Make changes and run source-based commands from that checkout.
The managed reset worktree is archived after promotion; it is no longer a second
editable version of the project.

The original pre-reset source, including the local changes present before the
reset, is preserved in local Git branch `codex/pre-reset-20261005` and the frozen
`data/archives/original-20261005/source.tar.gz`. These are historical snapshots.
Restoring source alone does not restore the database or undo a migration.

| Source directory | Purpose |
| --- | --- |
| `backend/app/` | Current API, services and provider adapters |
| `backend/alembic/`, `backend/tests/` | Schema history and backend regression checks |
| `frontend/src/`, `frontend/e2e/` | Current console and fixture browser checks |
| `scripts/phase1/` | Isolated validation and restore rehearsal |
| `docs/` | Current guides, release notes and explicitly historical review context |
| `prototypes/premium-dashboard/` | Separate fixture-driven design prototype with its own guide |

See [architecture](architecture.md) and the [backend](agent/backend.md) and
[frontend](agent/frontend.md) guides for code ownership. The old
[maintainability brief](code-review/2026-10-04-maintainability-brief.md) is historical;
the [core reset release note](releases/core-reset.md) records the implemented scope.

## Runtime, backups and evidence

Private data remains outside Git. Operational bind paths stay fixed:
`data/backend/`, `data/postgres/`, `data/redis/`, `logs/backend/` and
`logs/frontend/`. Keep auth material with its matching backend files and database.
Do not rename or replace a live bind directory as part of source cleanup.

| Private directory | Purpose |
| --- | --- |
| `data/backups/database/` | Retained database backups |
| `data/backups/postgres-upgrades/`, `data/backups/redis-upgrades/` | Storage retained from infrastructure upgrades |
| `data/backups/checkpoints/` | Historical checkpoints |
| `data/backups/deployments/` | Deployment receipts and matching restore bundles |
| `data/backups/retired-attachments/` | Files retained from retired features |
| `data/archives/original-20261005/` | Frozen original source archive |
| `data/previews/original/`, `data/previews/reset/` | Independent writable preview data for each image pair |
| `data/previews/snapshots/`, `data/previews/receipts/` | Immutable starting copies and preview receipts |
| `data/validation/core-reset/baseline/`, `data/validation/core-reset/reset/`, `data/validation/core-reset/previous-runs/` | Retained validation evidence |

The final validation run is retained at `data/validation/core-reset/reset/`, with
its source snapshot, results and exact prepared dependencies. Disposable synthetic
runtime data is excluded. Evidence snapshots are frozen records of assessed source,
not additional working checkouts. Future harness execution still follows the
[validation guide](validation/phase1.md).

Run new validation in an external evidence directory, such as `/private/tmp/`;
the harness rejects evidence inside the assessed checkout. The production
container retains a read-only mount of the whole project at `/workspace`, so
dependency reuse also requires a temporary copy outside that bind mount. Copy
the retained reset run to an external temporary directory before passing it to
`--reuse-dependencies`; its recorded backend and Node image identities and locked
dependency checks still apply. Consolidate completed evidence here afterward.

Historical receipts retain their original embedded paths. Consult
`data/relocations-20261005.json` for their current locations; do not rewrite old
receipts to make them appear newly produced.

## Preview selection and production rollback

`data/previews/original.env` pairs the original images with
`data/previews/original/`. `data/previews/reset.env` pairs the reset images with
`data/previews/reset/`. Both selectors are private and use the standalone
`docker-compose.preview.yml` project on loopback port 8189. Stop the preview before
switching selectors. The [preview guide](validation/preview.md) contains the commands
and isolation checks. Keep each backend/frontend image pair with its own database,
files and auth material; an old image cannot use the migrated reset database.

Production rollback requires the matching stopped-system backup and image pair,
including the auth secret and backend files. The cutover restore bundle is
`data/backups/deployments/core-reset-20261005T185443Z/`; its `completed.json` identifies
the deployment. Capture and reconcile post-cutover movements and other durable
effects before restoring an earlier journal. Follow the
[release rollback requirements](releases/core-reset.md) and
[hardware safety guide](agent/hardware-safety.md); choosing an original source branch
or preview selector does not roll back production.
