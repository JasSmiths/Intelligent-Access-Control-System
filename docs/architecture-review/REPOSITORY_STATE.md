# Canonical repository after recovery cutover

The current development source is `/Users/jas/Documents/Intelligent Access System`.
The 655 release-manifest files were consolidated here and verified against the
running release source on 2026-09-13. Source fingerprint:
`ce3f77ea82458d6977ea1d9b24a71dd798014c8fcaf7b60f25d3ee3f1e7a888b`.
The obsolete `backend/app/services/whatsapp_messaging.py` facade was removed.
Unrelated files, live data, secrets and Git history were preserved. Changes remain
uncommitted; no push occurred.

`docker-compose.override.yml` pins the accepted backend/frontend images and mounts
this repository at `/workspace` read-only. Running Python comes from the image,
not a host application overlay. Editing source does not deploy it: use an isolated
validated build and explicit release. The updater remains disabled pending its
separate operational validation.

The external release-preparation and implementation-checkpoint directories are
historical release evidence, not development repositories. Do not continue work
in their source copies. The application no longer mounts release-preparation.

## Preserved rollback material

`/Users/jas/Documents/IACS Cutovers/2026-09-13-_g0vb8c1/private/` retains the verified
pre-upgrade full source/runtime archive, PostgreSQL dump and exact images. It
contains sensitive data; do not publish it. Source consolidation did not alter
those archives. Detailed receipts and rollback constraints are in that directory's
parent `CUTOVER.md`. Preserve this backup until an explicit retention decision.

A pre-upgrade database restore discards newer writes and needs separate approval.
Do not run old executors against the new schema as a rollback shortcut. For a
compatible pause, retain current images and set IACS_RECOVERY_HOLD=true in the
current override, then recreate backend through the controlled release procedure.
Do not restore an old override blindly: it may reference a historical workspace.

## Verification scope

All 655 deployed source hashes match. The source-snapshot suite passed 22 tests;
the isolated harness snapshot mode reproduced the accepted fingerprint; diff
whitespace checks passed. No application behavior changed, and full integration
suites were not rerun for this exact-source relocation. Live readiness passed
after the same-image backend recreation.
