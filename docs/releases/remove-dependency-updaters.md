# Dependency updater retirement

The dependency and UniFi Protect package updater systems were removed. Normal
integrations, UniFi cameras/events/snapshots/settings, and UniFi Protect General
and Exposes views remain available. UniFi uses image-installed `uiprotect==16.10.0`.

Alembic revision `20260920_0008` removes updater jobs,
backups, analyses, external-dependency records, and six updater-backup settings.
Downgrade recreates empty table structures only; recover removed database data or
settings from a database backup. Existing backup archives, logs, and overlay files
on disk are retained but unused. Historical audit history is retained.

A separately authorized rollout stops old updater/backend processes, takes a
database backup using the operational process, applies the migration, and deploys
matching backend and frontend artifacts. An image rollback alone cannot restore
dropped data.
