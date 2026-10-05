# Core reset

This reset removes Alfred chat, tools, memory, training and approvals; Discord and
WhatsApp intake/delivery; visitor conversations; AI schedule parsing, calendar-name
extraction, investigation questions and notification rewriting. Camera image
analysis remains. Access/presence, LPR, hardware owners, schedules, visitor passes,
notifications, both workflow engines, maintenance, backups and durable history remain.

Directory, Settings, provider forms and visitor pass editing now have focused
frontend owners with the existing layout and typed API contracts. Manual duration
passes take a registration directly; new passes/conversions require it. Legacy
passes remain editable. Calendar names use deterministic title parsing.

Migration `20261005_0010` removes nine retired tables, their settings and the unused vector extension. It removes
retired rule nodes, disables changed valid rules for review, and deletes rules that
have no usable trigger/actions. Valid stored AI cron schedules become ordinary cron
schedules with their timezone and boundaries retained. Unfinished affected runs
become review-required; completed history, checkpoints and access audit remain.
Retired `whatsapp_` pass metadata is removed; contact, plate, validity and calendar
provenance are preserved. Removed-feature confirmations are cancelled.

Before any upgrade, stop old workers and retain a matching database dump, backend
files/attachments, auth secret and image pair. Downgrade is deliberately refused:
return by restoring that matching data and selecting the matching images. Switching
an old image onto a migrated database is unsupported. Production rollout requires
separate authorization.

The [isolated preview](../validation/preview.md) retains the original image pair
and a direct copy of current data. Its private network blocks provider/hardware
access. The original system remained unchanged during isolated validation.


## Production cutover — 5 October 2026

Production now runs the tested backend/frontend pair under
`production-20261005-core-reset`. The existing production database was upgraded
from `20261002_0009` to `20261005_0010`; the preview database was not promoted.
PostgreSQL and Redis services, operational bind paths, authentication material,
core records and delivery checkpoints were preserved.

A fresh stopped-system database/files/auth backup was restored and migrated in
an isolated rehearsal before the production migration. The new application
started under recovery hold. Retained secret settings and the calendar session
bundle were readable, and Home Assistant, all three ESPHome devices and UniFi
passed read-only connectivity checks. Normal operation was then enabled;
readiness, authenticated core reads and live integration status passed.
One notification rule using a retired trigger was removed as expected.

The private deployment receipt is in the original checkout at
`data/backups/deployments/core-reset-20261005T185443Z/completed.json`. Its directory holds
the matching backup, original selector, image IDs, checksums and restore evidence.
Keep it private and retain it alongside the original image pair. The
[project layout](../project-layout.md) describes the current checkout and relocated
archives; historical receipts retain their original paths with mappings in
`data/relocations-20261005.json`. Production
rollback still requires restoring matching data; capture and reconcile any
post-cutover effects before restoring an earlier journal.
