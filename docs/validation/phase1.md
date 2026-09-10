# Phase 1: isolated regression baseline

Run from any directory, with Docker available:

```sh
python3 '/Users/jas/Documents/Intelligent Access System/scripts/phase1/validate.py'
```

The runner prints a unique results directory under `~/Documents/IACS Regression Baselines`, outside the repository. It retains
source hashes, the source snapshot, image IDs, check logs, JUnit results, isolated
storage and before/after production container metadata. Nonzero exit means a
check, prerequisite, cleanup or production comparison failed. It never invokes
`scripts/backend-pytest`, production Compose operations, or production endpoints.

Prerequisite: a local IACS development image with Python 3.12 and `uv` (default
`intelligentaccesssystem-backend:latest`; override with `PHASE1_BACKEND_IMAGE`).
Its immutable ID is recorded. The image supplies tools, not the assessed source
or Python dependency set: a fresh environment is installed from the snapshot's
`uv.lock` with `uv sync --locked --extra dev --no-install-project`. Node and service
images use the repository's pinned references. No image build or production image
retagging occurs. Downloads may require registry/package network availability.

## Snapshot and safety boundaries

- Copy tracked files from the working tree, including staged/unstaged source
  edits, with a SHA-256 manifest and HEAD/status/diff summary. Deliberately include
  the pre-existing untracked privacy workflow and Phase 1 harness/documentation.
  Other untracked files are excluded. Deleted files stay absent. Symlinks,
  `.env*`, `data`, `logs`, virtual environments, caches, `node_modules` and `dist`
  are excluded. No Git checkout/reset/stash touches the user's work.
- Verify the result directory does not overlap any production bind mount.
- Dependency preparation has network access and mounts only copied package
  manifests and fresh dependency directories. NPM lifecycle scripts are disabled.
  It never runs tests or application startup. Dependency package builds are
  ordinary package installation, so this is not a malicious-package sandbox.
- PostgreSQL starts with `--network none`. Redis and backend checks join that
  network namespace: **only loopback exists**, with no route to the host, LAN,
  other Docker networks or Internet. Frontend checks use their own network-none
  namespace. No host ports, host devices, Docker socket, production credentials,
  production data or named volumes are mounted. PostgreSQL/Redis storage is a
  fresh per-run bind directory. Synthetic auth and database values are explicit.
- The preflight checks interfaces, absence of runtime mounts/config/socket,
  database name and Redis availability. A connection to the documentation-only
  address `192.0.2.1` must fail. Docker inspection records actual namespaces,
  mounts, resource constraints and port configuration.
- One check at a time, one CPU and 1.5 GiB limit per container; one Vitest worker.
  Only PostgreSQL and Redis remain running alongside a check. No load tests.
- Every created container gets a unique run label and is recorded before launch.
  Finally, including interruption/error, remove only those labelled resources
  and confirm none remain. Container removal terminates their child processes.
  Inspect production IDs, image IDs, start times, restart counts, running state
  and mounts before and after. SIGKILL/host loss cannot execute cleanup; use the
  retained owned-container list and run label to inspect/remove only that run.

## Startup and fixture review

`backend/tests/conftest.py` only disposes the async engine after each test. The
existing suite uses fake sessions, monkeypatches, fake providers and in-process
ASGI clients. ASGI simulation tests use a local test app and patched runners;
these are not calls to the production simulation endpoints. No suite fixture
starts the main lifespan or creates/drops a real schema.

`app.main.lifespan` would initialize the database and start integration,
notification, automation, access, reconciliation and backfill services. The
harness never launches Uvicorn or enters that lifespan. Auto schema creation and
demo seeding are explicitly disabled. Network isolation is the final boundary
even if a test accidentally attempts provider I/O.

Alembic reads the explicit isolated URL. The baseline migration calls current
`Base.metadata.create_all`, later migrations use schema/index SQL, and the vector
extension is installed only in the fresh test database. Upgrade/current/schema
comparison run without application startup. This proves fresh installation,
not upgrade compatibility with historical production schemas. No migration is
edited and no production schema/data/version is queried.

## Coverage matrix

All paths below are relative to `backend/tests/` unless stated otherwise.
"Fake" includes in-memory ORM objects, fake sessions and monkeypatched providers;
it does **not** establish SQL transaction guarantees. See retained JUnit results
for the precise pass/fail status of individual tests.

| Behaviour | Existing coverage | Persistence and important gap |
|---|---|---|
| Resident arrivals/departures | `contracts/test_access_contracts.py`, `test_access_events.py`, `test_movement_fsm.py` | Pure/Fake; no committed full ingest-to-event-to-presence transaction test. |
| Visitor arrivals/departures | Access contracts; `test_visitor_passes.py` arrival, open visit, repeat visit and departure cases | Fake sessions; real pass claim/expiry/departure atomicity unproved. |
| Unknown vehicles never command hardware | Unknown access contract; access event and FSM denial tests | Pure/Fake; contract asserts `physical_action_required=False`, not a persisted command count across complete ingest. |
| Duplicate/OCR/session suppression is durable | Suppression contract; `test_movement_sessions.py`, `test_movement_ledger.py` | Existing tests fake flush/session. New `scripts/phase1/test_persistence.py` verifies repository commit/re-read and repeated key for three synthetic reasons; classification-to-ledger integration remains a gap. |
| Gate idempotency/rejection/ambiguous acceptance/reconciliation | `test_gate_commands.py`, `test_movement_ledger.py`, `test_movement_reconciliation.py`, gate contracts | Existing tests use fake controllers/sessions. New PostgreSQL tests run the coordinator with a fake gate and prove accepted/rejected/unverified outcome persistence and replay through a new coordinator without consulting a provider. Concurrent claims and crash-window recovery remain unproved. |
| Presence follows committed decisions | Presence contracts; access events; reconciliation commit-presence tests | Fake sessions/payloads; no rollback/commit ordering proof across real event, saga and presence rows. |
| Restart/historical backfill is hardware-free | `test_restart_backfill.py`; reconciliation historical repair test skips input-boolean job | Fake candidate processing/providers; no full process restart or durable backfill replay test. |
| Maintenance mode | `test_action_confirmations.py`, `test_automations.py`, `test_webhooks.py` | Fake/in-process API; real queue clearing and absence of durable side effects unproved. |
| Schedules/timezones/pass validity | `test_schedule_evidence.py`, visitor-pass validity/window/status tests; investigations DST ranges | Pure/Fake; complete DST boundary matrix plus real schedule/pass race coverage needed. |
| Admin authorisation and confirmation | `test_safety_hardening.py`, `test_action_confirmations.py`, API tests | In-process routes with dependency overrides/fake consumption; no real durable one-use confirmation race. Includes the user's pending confirmation fix. |
| Notification partial success/expiry/unfinished delivery | `test_notification_workflows.py` secondary sender partial success; `test_actionable_notifications.py` token expiry; queued-versus-processing notification-run tests; WhatsApp delivery status tests | Fake senders/sessions; persisted unfinished run restart/retry/one-use delivery proof missing. |
| Automation dry runs/hardware safety | `test_automations.py`, workflow contracts | Fake actions/sessions; dry-run preview and confirmation/HMAC assertions, no durable zero-side-effect comparison. |
| WhatsApp visitor isolation/Admin-to-Alfred | `test_whatsapp_messaging.py`, messaging contracts | Fake sessions/providers; visitor sandbox, privileged plate refusal, unknown sender and Admin routing covered. Cross-pass persistence isolation remains unproved. |
| Alfred confirmation/fail-closed execution | `test_chat_agent.py`, `contracts/test_alfred_v3_contracts.py`, automation/pass tool tests | Fake LLM/tools/sessions; no paid APIs. Durable confirmation consumption and real transaction recovery remain gaps. |

## Recommended next small step

Add one PostgreSQL-backed access regression that submits a synthetic unknown
plate through the existing access service with fail-on-call fake gate/garage,
notification and vehicle-lookup providers. Acceptance: denial and explainable
movement persist after opening a new session; no gate-command row or presence
change exists; each hardware fake has zero calls; replay is safe. Run via this
harness and keep application code unchanged. If service injection prevents this,
propose the smallest seam separately before changing production code.
