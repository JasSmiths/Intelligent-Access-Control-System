# Dependency updater retirement

Application-managed dependency and UniFi Protect package updaters are removed.
Normal integrations, UniFi cameras/events/snapshots/settings and the UniFi Protect
General and Exposes views remain. `uiprotect` is installed with the backend's
locked dependencies; its version is defined by `backend/pyproject.toml` and
`backend/uv.lock`, not runtime overlays.

Alembic revision `20260920_0008` removes updater jobs, backups, analyses,
external-dependency records and six updater-backup settings. Downgrade recreates
empty table structures only. Removed database rows/settings require a database
backup to recover; unrelated settings and audit history remain. Existing updater
backup archives, logs and overlay files on disk are retained but unused.

Any rollout or rollback requires separate authorization; see the
[schema and recovery limits](../validation/recovery-schema.md). Stop old updater/backend
processes, prepare the operational database backup, apply the approved migration
and deploy matching backend/frontend artifacts. An image rollback alone cannot
restore dropped data. File cleanup requires its own reviewed scope; do not delete
old archives merely because the updater no longer uses them.
