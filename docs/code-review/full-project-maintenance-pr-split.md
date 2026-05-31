# Full Project Maintenance Audit PR Split

Use this order when turning the current remediation branch into small PRs. Each
PR should be rebased on the previous one and validated before opening the next.

1. **Safety and Confirmation Hardening**
   - Auth dependencies, action-confirmation helper usage, replay handling, Admin-only routes, and safety-hardening tests.
   - Validate with backend safety/confirmation tests plus full compile.

2. **LPR and Gate Reliability**
   - Durable LPR ingest model/service path, suppressed-read durability, gate-specific reconciliation, health/degraded behavior, and LPR/reconciliation tests.
   - Validate with access-event, Ubiquiti LPR, movement ledger, and reconciliation tests.

3. **Webhook and Operational Hardening**
   - Automation webhook key/HMAC/source/rate controls, backend request-size limits, trusted proxy handling, compose hardening, default-password rejection, load-test token handling.
   - Validate with webhook/automation tests, compose config, and smoke checks.

4. **Schema, Packaging, and CI**
   - Alembic baseline/follow-on revisions, `uv.lock`, Dockerfile runtime/dev targets, digest-pinned images, migration smoke, dependency audit jobs, Docker target builds.
   - Validate with empty-DB Alembic smoke, backend pytest, Docker target builds, and CI dry-run review.

5. **Frontend Safeguards**
   - 25 MB upload guard, CSV formula escaping, Admin-only controls, confirmation UI calls, route error boundary, frontend `.dockerignore`, and Vitest guardrail tests.
   - Validate with `npm run test` and `npm run build`.

6. **Notification Delivery Semantics**
   - Durable `notification_runs`, run IDs/statuses in notification test APIs, event payload run correlation, and workflow tests.
   - Validate with `tests/test_notification_workflows.py` and frontend build for API compatibility.

7. **Documentation and Audit Ledger**
   - Remediation ledger, README/phase docs, and production hardening notes.
   - Validate docs links and rerun the full project validation command set.

Keep `backend/app/ai/tools.py`, `AccessEventService`, `IntegrationsView.tsx`, and
`WorkflowViews.tsx` decomposition out of the initial safety PRs unless a small
compatibility wrapper is required by a tested behavior change.
