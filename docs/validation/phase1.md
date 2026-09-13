# Phase 1: isolated regression baseline

Run the harness against an isolated checkout when the development checkout is
bind-mounted into a running application. Never edit the live checkout merely to
prepare a test. Commands below are relative to the checkout being assessed.

## Commands and explicit execution modes

```sh
# Host-only selector/mode/cleanup tests: no IACS imports or Docker.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_source_snapshot.py

# Copy/hash source only. No Docker, dependency installation, app imports or tests.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py \
  --check-only --evidence-root /private/tmp/iacs-validation-evidence

# Full isolated baseline, explicitly authorizing image/package downloads.
# This installs the existing exact locks; it does not change dependencies.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py \
  --mode full --allow-downloads \
  --evidence-root /private/tmp/iacs-validation-evidence

# Reuse a prior exact-lock preparation without network-enabled installation.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py \
  --mode full --reuse-dependencies /absolute/path/to/prior-evidence-run \
  --evidence-root /private/tmp/iacs-validation-evidence \
  --schema-check scripts/phase1/test_schema_contract.py \
  --diagnostic scripts/phase1/test_recovery_boundaries.py

# Database-free checks: no PostgreSQL/Redis containers, migrations or persistence.
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/validate.py \
  --no-migrations --reuse-dependencies /absolute/path/to/prior-evidence-run \
  --evidence-root /private/tmp/iacs-validation-evidence
```

`--mode snapshot` is equivalent to `--check-only`; `--mode db-free` is equivalent
to `--no-migrations`. Full mode is the default, but execution fails before any
resource is created unless `--reuse-dependencies` or `--allow-downloads` is explicit.
No-migration mode rejects persistence, schema-check and diagnostic requests. It
never quietly creates a schema or runs a schema-dependent suite. Snapshot mode
only verifies source selection/copying; a successful snapshot is not a passing
application validation. Snapshot mode cannot discover production mounts because
it does not contact Docker: choose a known private evidence directory.

Repeat `--include REPO_RELATIVE_FILE` to require a new/ignored fixture or another
file outside the standard first-party roots. Repeat `--persistence-test` for extra
schema-dependent pytest files, `--diagnostic` for separately reported inert pytest
probes, and `--schema-check` for guarded schema CLI scripts. These execution targets
are automatically required snapshot inclusions and validated before resource
creation. A CLI schema check takes no extra arguments; its evidence belongs below
`/results`. Review any new script's side effects before requesting execution.

The runner retains a unique directory beneath `--evidence-root` (default
`~/Documents/IACS Regression Baselines`). `manifest.json` records HEAD, branch,
working-tree status, source selection, per-file SHA-256 hashes, a combined source
fingerprint, dependency manifest hashes and host Python/platform identity.
`images.json` records immutable image IDs; `image-references.json` records the
source declarations. `checks.json` records exact commands, working directory,
classification and passed/failed/not-attempted status; logs retain outcomes.
JUnit XML and schema-check evidence are under the run's `artifacts/` directory
(the only directory mounted writable at `/results`). `results.json` remains the compact exit-code summary.

Any failed check, diagnostic, prerequisite, cleanup or production comparison
returns nonzero. Diagnostics are intentionally separate from the ordinary
contract suite and a failing safety probe is never reported as green, skipped or
an expected failure. If migration fails, schema-dependent tests are explicitly
not attempted; independent guarded schema CLI checks may still run in scratch
DBs. Full mode always runs the required inventory in
`scripts/phase1/recovery_checks.py`; extra CLI selections are additive. This
includes the PostgreSQL gate journal suite, which is intentionally skipped in
DB-free mode. A DB-free pass does not validate those command persistence cases.

## Source completeness and reproducible dependencies

- Tracked source is copied from the current working tree, preserving staged and
  unstaged bytes in the snapshot. Safe untracked files under `backend/`,
  `frontend/`, `scripts/`, `docs/` and `.github/` are discovered automatically.
  Ignored files in those first-party roots fail closed unless explicitly included
  or classified as excluded generated/runtime material. New application files
  cannot silently disappear from the assessed snapshot. Untracked files outside
  these roots are listed in the manifest and need `--include` when relevant.
- Deleted tracked files remain absent. Invalid explicit inclusions, absolute or
  parent-traversing paths, directories and symlinks (including parent symlinks)
  are rejected. A selected source symlink fails the snapshot, rather than being
  silently omitted. Runtime paths, `.env*`, credentials/key files, local agent/Git
  directories, virtual environments, caches, database/log/capture files,
  `node_modules`, build outputs and test-results directories are excluded before
  content reads. No Git checkout/reset/stash touches existing work.
- The backend runtime image must provide Python 3.12 and `uv` (default
  `intelligentaccesssystem-backend:latest`; override with `PHASE1_BACKEND_IMAGE`).
  The image supplies tooling, not the assessed application or dependency set.
  Node's pinned image comes from the snapshot's `frontend/Dockerfile`;
  PostgreSQL/Redis pins come from its `docker-compose.yml`. Missing or ambiguous
  literal pins fail instead of falling back to historical hardcoded versions.
- New dependency preparation mounts only copied manifests and private dependency
  directories, uses `uv sync --locked --extra dev --no-install-project` and
  `npm ci --ignore-scripts`, and requires `--allow-downloads`. Package builds can
  execute installation code; this isolates application credentials and networks
  used by tests, not malicious package installers. The repository's locks and
  dependency versions are unchanged.
- `--reuse-dependencies` expects `python-deps/.venv`, `js-deps/node_modules`, their
  copied manifests/locks and `images.json` from a trusted previous preparation.
  All four manifests/locks and backend/Node immutable image IDs must match.
  Dependencies are copied into the new run so test caches cannot alter old
  evidence. Before tests, network-none containers run offline `uv sync --check`,
  compare installed Node package versions with lockfile entries, and run
  `npm ls --all --json`. Python distribution versions, Python/platform, uv, Node
  and npm identities are retained. A matching directory name or green build is
  not sufficient proof of exact dependencies. Reuse verifies installed metadata;
  it is not a tamper-proof package-content attestation.

## Runtime safety and cleanup

- Before creating evidence or copying source/dependencies in execution modes,
  inspect production container metadata and reject overlap with production bind
  mounts. Evidence must also be separate from the assessed repository. Snapshot
  mode's host-only limitation is stated above.
- All DB-free backend checks use their own network-none namespace and synthetic
  loopback port 1 database/Redis URLs. The backend test suite, compilation and
  targeted Ruff/mypy checks run before services start. Any unmocked persistence
  dependency fails visibly; classify it or provide an explicit inert fixture.
- Full mode then starts PostgreSQL with `--network none`. Redis, migrations,
  persistence and diagnostics share only that container's loopback namespace.
  There are no host ports, LAN/Internet routes, Docker socket, host devices,
  production credentials/data or named volumes. Storage is fresh per-run bind
  directories, and auth/database values are fixed synthetic inputs. Backend source
  and dependencies are read-only; `/results` exposes only `artifacts/`, never the
  parent run directory, source, dependency environments or PostgreSQL files.
  Frontend builds may create outputs in their copied frontend directory; final
  `source-integrity.json` verifies all original input hashes and makes changed or
  deleted inputs fail the run. New generated outputs are outside that hash set.
- The preflight uses unconditional runtime guards (not removable Python asserts),
  checks interfaces, synthetic configuration and runtime/socket absence, and
  verifies that a documentation-only outbound address is unreachable. DB-free
  preflight does not connect to databases. Persistence preflight requires the
  synthetic `iacs_p1_` database and Redis to become ready. Connection resources
  are closed on both success and failure. Docker inspection retains actual mounts,
  network, resources and port configuration for the persistence namespace.
- Every check is serial with one CPU and 1.5 GiB per container; Vitest uses one
  worker. Diagnostics run immediately after migration and before the stateful
  persistence suites, with fixed `IACS_RECOVERY_PROBES=synthetic-only`; their
  fixtures must clean their owned rows. Schema CLIs get a fixed synthetic marker
  and must create/drop separate prefixed scratch databases. Neither option
  forwards host credentials. No load tests or live provider probes occur.
- Owned container names and a unique label are recorded before launch. Timeout or
  interruption kills and waits for the owned host process group. Finally remove
  only containers with the matching run label and verify none remain. A second
  interrupt is ignored during cleanup. Cleanup and the before/after production
  identity/image/start/restart/running/mount comparison cannot silently pass if
  inspection fails. SIGKILL/host loss cannot execute cleanup; the retained
  `owned-containers.json` and label identify precisely which resources to recover.

The harness never invokes `scripts/backend-pytest`, production Compose operations,
production endpoints, Uvicorn or `app.main.lifespan`. The main lifespan owns live
workers/integrations and is not an isolated fixture. Auto schema creation and demo
seeding remain disabled. Compose receives only a clean host environment and is
parsed with `config --quiet`, never started.

## Persistence coverage and its limits

`backend/tests/` is classified as DB-free in the harness; in-process ASGI clients
and simulation runner tests must use inert dependencies. Cleanup of the async
engine alone does not establish database isolation. The dedicated
`scripts/phase1/test_*.py` persistence list runs only after a successful disposable
migration. New persistence scripts are explicit additions, not accidentally
collected into DB-free tests.

Fresh Alembic upgrade/current/schema comparison proves the assessed fresh schema,
not upgrade compatibility with historical production schemas. The original
baseline's dependency on current `Base.metadata` remains a separately reported
schema hazard until repaired by its approved package. The guarded schema contract
CLI compares frozen historical fixture/upgrade evidence and cleans its scratch
DBs; a failure remains visible. No production schema/data/version is queried.
The historical coverage matrix below is background, not a claim that current
checks passed; use the exact run's manifest, checks and JUnit evidence.

## Original Phase 1 coverage matrix

This records the initial assessment. The milestone-specific sections below record
the persistence coverage added since that assessment.

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

## Schedule operation persistence coverage

`scripts/phase1/test_schedule_operations.py` runs alongside the original
persistence suite in the isolated harness. It exercises real API/Alfred CRUD and
assignment transactions, shared audit, confirmation/role denials, duplicate and
in-use failures, concurrent partial updates, vehicle inheritance, and temporary
override rollback/realtime failure. The tests refuse execution without the
harness's loopback-only namespace and synthetic database URL. See
[the milestone 2 handoff](milestone2-schedules.md) for exact results and limits.

## Access pipeline persistence coverage (milestone 5)

The previously deferred unknown-plate regression is now included in
`scripts/phase1/test_access_pipeline.py`, alongside complete normalized-ingest
arrival/departure, visitor links/audits, schedule denial, external admission and
exit, exact/OCR suppression, committed replay and concurrent finalization tests.
The actual gate coordinator and garage/device owner use fake providers. Independent
PostgreSQL sessions verify the commit before a command, rollback of a visitor claim,
verified presence/session commit, failure/cancellation after a command, and recovery
without a repeated physical command. Historical backfill and all seven simulation
scenarios also persist in the isolated database.

Optional-enrichment failure/cancellation and field ownership have database tests.
Latency properties use identical synthetic delays before and after the change.
These do not simulate a real camera/relay or measure sustained queue throughput.
See [milestone 5](milestone5-access.md) for the remaining interruption windows.

## Notification recovery coverage

The persistence suite includes `test_notification_recovery.py`: real competing
claims, lease/token fencing, lost wakeups, cancellation, interrupted action delivery,
post-send checkpoint failure, partial outcomes, historical review, stable gate-outbox
identity, and old-schema migration/downgrade guards. Fakes replace all providers.
Milestone 4 adds a new head after 20260713_0002; fresh-head comparison and isolated
historical-schema upgrade are separate checks. See its handoff for limitations.
