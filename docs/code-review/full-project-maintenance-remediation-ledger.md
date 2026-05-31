# Full Project Maintenance Audit Remediation Ledger

This ledger tracks the disposition of every actionable item from
`docs/code-review/full-project-maintenance-audit.md`.

Status values:

- `fixed`: implemented in code/config/docs.
- `validated`: covered by an automated or manual validation check.
- `intentionally preserved`: the audit recommended avoiding a risky change for now; the existing compatibility surface remains by design.

## Findings

| # | Audit item | Status | Evidence |
|---|---|---|---|
| 1 | Standard users can mutate access policy and visitor access | fixed, validated | Mutation routes now require Admin dependencies and confirmations; covered by `backend/tests/test_safety_hardening.py`. |
| 2 | Accepted LPR webhook reads are not durable before `202` | fixed, validated | `lpr_ingest_events` persists accepted reads before worker processing; covered by access-event and Ubiquiti LPR tests. |
| 3 | Suppressed reads can publish without durable movement history | fixed, validated | Suppressed reads record durable movement saga state before terminal publish; DB failure paths are tested. |
| 4 | Gate reconciliation is not device-specific | fixed, validated | Reconciliation matches gate command identity to configured provider/external IDs; multi-gate regression coverage added. |
| 5 | Access-device and ESPHome configuration paths lack confirmations | fixed, validated | Access-device/provider/ESPHome mutations and tests consume action confirmations from JSON bodies. |
| 6 | Confirmation/admin tokens leak through URLs or process arguments | fixed, validated | Telemetry/UniFi delete tokens moved to request bodies; load-test tokens are provided by env/stdin and not WebSocket query strings. |
| 7 | Infrastructure services are exposed broadly with dev defaults | fixed, validated | Compose binds Postgres/Redis to loopback by default and rejects non-dev startup with the default DB password. |
| 8 | Simulation LPR endpoints can drive live access decisions | fixed, validated | Live simulation endpoints require Admin confirmation and are covered by simulation hardening tests. |
| 9 | Public automation webhooks rely only on URL keys | fixed, validated | Server-generated keys, optional HMAC/source policy, replay/rate controls, and migration metadata are implemented. |
| 10 | Public webhook body handling has DoS exposure | fixed, validated | Backend request-size middleware covers WhatsApp, LPR, automation webhooks, chat uploads, and direct API traffic. |
| 11 | LPR source allowlist and proxy topology can conflict | fixed, validated | Trusted-proxy-aware verification is configurable; direct backend LPR routing is documented. |
| 12 | Realtime malformed-message contract drift | fixed, validated | Contract is `connection.error`; tests and frontend handling align to that behavior. |
| 13 | CI coverage is too narrow | fixed, validated | CI now runs backend tests, frontend tests/build, compose config, migration smoke, dependency audits, and Docker target builds. |
| 14 | Runtime backend image carries dev/test/browser tooling | fixed, validated | Dockerfile has runtime/development/test targets and runs runtime as non-root. Playwright remains a runtime dependency because PDF reports require it. |
| 15 | Dependency reproducibility is weak on backend | fixed, validated | `uv.lock` workflow is present and release base images are digest-pinned. |
| 16 | Alembic is present but not a complete schema path | fixed, validated | Current metadata baseline and follow-on revisions exist; normal startup runs Alembic to head; legacy bootstrap DDL is behind `IACS_LEGACY_SCHEMA_BOOTSTRAP`. |
| 17 | Upload size contract is inconsistent | fixed, validated | Nginx/backend/frontend chat upload limits align at 25 MB; frontend tests cover client-side rejection. |
| 18 | Settings coercion can silently send invalid numeric values | fixed, validated | Frontend rejects non-finite numeric settings; backend rejects unknown keys; tests cover both. |
| 19 | Log CSV export is formula-injection prone | fixed, validated | CSV export prefixes formula-leading cells; frontend tests cover formula escaping. |
| 20 | Fire-and-forget audit writes weaken durable audit guarantees | fixed, validated | Safety-critical mutation routes use awaited audit writes where they share the mutation transaction; remaining fire-and-forget uses are non-critical telemetry paths. |
| 21 | Notification API can look synchronous when delivery is asynchronous | fixed, validated | Notification enqueue/send APIs are split and durable `notification_runs` return observable run IDs/status. |
| 22 | Action confirmation replay can clobber prior success outcome | fixed, validated | Already-consumed confirmations no longer overwrite their original outcome; replay rejection is separately audited. |
| 23 | Standard users can list and download UniFi Protect backups | fixed, validated | Backup list/download/archive access is Admin-only. |
| 24 | Frontend Docker context lacks `.dockerignore` | fixed, validated | `frontend/.dockerignore` excludes local dependencies, builds, caches, logs, and env files. |
| 25 | Some docs contain machine-local paths | fixed, validated | User-facing docs now use repo-relative paths and local-path references are confined to code-review artifacts. |
| 26 | Dead dashboard controls create false affordances | fixed, validated | `PanelHeader` hides action controls without handlers. |
| 27 | Unknown dynamic setting keys are silently ignored | fixed, validated | Settings updates reject unknown keys with a 400 and key list; frontend validates outgoing numeric values. |

## Refactors And Recommendations

| Item | Status | Evidence |
|---|---|---|
| Keep `backend/app/ai/tools.py` facade while moving tool bodies into `tool_groups/*` | intentionally preserved, validated | Facade shims remain for public imports; domain migration continues behind registry tests. |
| Split `AccessEventService` after durable ingest stabilizes | intentionally preserved, validated | Durable ingest and reconciliation are fixed first; remaining split is limited to focused collaborators to avoid changing the LPR critical path in the same baseline. |
| Split large frontend integration/workflow views after current user edits settle | intentionally preserved, validated | Frontend guardrail tests are in place; broad view decomposition is deferred to scoped follow-up PRs to avoid overwriting active edits. |
| Bootstrap DDL should not remain the default schema path | fixed, validated | Alembic is now the normal startup path; legacy bootstrap DDL requires `IACS_LEGACY_SCHEMA_BOOTSTRAP=true`. |
| Promote outbox semantics for notifications | fixed, validated | `notification_runs` records queued, processing, provider-accepted, failed, and skipped outcomes. |

## Validation Commands

- `docker compose exec -T backend sh -lc 'cd /workspace/backend && python -m pytest'`
- `python3 -m compileall -q backend/app`
- `cd frontend && npm run test`
- `cd frontend && npm run build`
- `docker compose config --quiet`
- `docker compose exec -T backend sh -lc 'cd /workspace/backend && alembic upgrade head && alembic current'`
- Docker target builds: backend `runtime`, backend `development`, frontend `runtime`
