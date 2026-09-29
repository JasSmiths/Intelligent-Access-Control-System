# GUI and Access Pulse live release — 28 September 2026

The reviewed GUI (`1573446`) and the existing Access Pulse chart replacement were combined as source commit `ff16a84` and deployed to the local IACS console.

Only `iacs-backend` and `iacs-frontend` were recreated, in that order. PostgreSQL and Redis kept their original container IDs and start times. No database migration, hardware test, notification test or provider test was run. Existing application configuration and Git index staging were preserved.

The frontend uses image `sha256:cda4224dd2ae65d1fcf4c5be3a0ca5b36d569fc082a2faa5d96c413c5d69165c`. The backend retains image `sha256:cefed3dea699bde948af82a1f4c6cf9c54c64e7c282805f896122c40e4d5e4e7` with four read-only API file binds: `access.py`, `events.py`, `action_confirmations.py`, and `history.py`. The existing data, log and notification-owner mounts are retained.

## Verification

- Combined frontend build and 239 unit tests passed.
- Six Chromium/WebKit Dashboard responsive checks passed with Access Pulse.
- The reviewed backend previously passed all 146 isolated Phase 1 checks. Runtime owner compatibility and network-isolated imports were checked before rollout; Astra approved the rollout approach.
- Both direct backend and frontend-proxied health returned HTTP 200 / ok.
- Authenticated live Events, Movements and Alerts history requests returned HTTP 200; no server tracebacks were observed after restart.
- The signed-in Dashboard displayed Access Pulse. Served entry assets matched the new frontend image.

## Rollback evidence

`/private/tmp/iacs-gui-live-release-20260928` contains the pre-release source/override copies, absent-file markers, unchanged original Git index digest, before/after container identities and release manifest. The old frontend image remains available locally.

Rollback must restore the prior override and relevant host source files, including removing newly introduced files only if they still match the release hashes. Do not overwrite subsequent edits. Recreate the backend, check health, then recreate the frontend using the previous image. Database and Redis rollback is not part of this release.

## iPhone navigation correction — 29 September 2026

Deployed reviewed commit `891d548` as frontend image `sha256:1bb5b3a3f9288d03bd12b2c6670b97f4fe4a9fcb3dc9abb70b1042d1d4035a09`. Landscape phones now use the full labeled navigation drawer, with safe-area spacing, scrolling Settings links, rotation handling and focus restoration. Access Pulse remains included.

The frontend build, 239 unit tests and eight focused Chromium/WebKit checks passed; Astra approved source and visual evidence with no blockers. Fourteen other responsive checks passed during the broader regression run; its old profile-menu assumptions were updated and the affected checks then passed in the focused run. `git diff --check` passed.

Only the frontend container was recreated. Backend, PostgreSQL and Redis container IDs and start times remained unchanged. Live HTML matched the new image; all four entry assets and frontend-proxied API health returned HTTP 200. All five changed source/test/documentation files matched the reviewed commit, and the original Git index was preserved. No hardware, notification or provider test was performed.

Rollback/source manifests, logs and a representative screenshot are retained under `/private/tmp/iacs-iphone-nav-release-20260929`; the previous frontend image and Compose override are retained. Verification used browser emulation rather than a physical iPhone.
