# Schema compatibility and recovery validation

`scripts/phase1/test_schema_contract.py` verifies migration independence and
fresh, staged and historical schema equivalence. It is a mandatory full-mode
schema check in `scripts/phase1/recovery_checks.py`. Migration order comes from
`revision` / `down_revision` links, never filenames or dates; inspect
`backend/alembic/versions/` for the assessed checkout's chain.

## Current migration contract

The baseline revision `20260531_0000` executes frozen DDL from
`backend/alembic/schema/20260531_0000.json`, preserving its original check-first
behavior. It must not import current ORM metadata or acquire newly added models.
Later revisions own schema evolution. Runtime schema bootstrap is not a substitute
for migrations.

Historical source fixtures and their SHA-256 identities are recorded in
`scripts/phase1/fixtures/schema/manifest.json`. Keep them immutable: old migration
code belongs only in those fixtures, outside the active Alembic search path.
They reconstruct the initial and pre-recovery schemas with current locked
libraries and an isolated PostgreSQL engine. They are source fixtures, not
historical database dumps or historical dependency environments.

One deliberately narrow historical comparison accepts a difference:
`historical-pre_recovery-revision-stability` permits only
`users.auth_session_version` changing from no SQL default to `DEFAULT 0`.
The historical security migration specified that default; revision
`20260912_0002` converges installations without changing rows. Extra differences,
a different default or any final-head difference fail. This exception is encoded
and tested in `comparison_status`; do not broaden it to quiet new failures.

## Execution and evidence

These host-only commands use the standard library and do not import IACS or
connect to a database:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_schema_contract.py self-test
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_schema_contract.py static
```

Self-tests check fixture integrity, revision traversal, isolation/input validation
and comparison sensitivity. Static mode checks migration independence with an
AST tripwire. A static pass alone does not prove executable schema equivalence.

Run PostgreSQL checks through [the full isolated harness](phase1.md). They are
included automatically; `--schema-check` adds reviewed scripts. The harness
provides the loopback-only namespace, synthetic credentials, disabled bootstrap
and read-only source. The check creates only uniquely named
`iacs_p1_schema_*` scratch databases and drops the databases it created in
`finally`. It rejects copied runtime data, environment files and Docker sockets.
Do not invoke its PostgreSQL mode directly on the host.

Each invocation writes a new `schema-contract-<id>` directory under the harness's
`artifacts/` mount at `/results`, with `report.json`, per-comparison schema/diff
JSON and migration logs. Existing output directories are rejected. Outcomes are
`PASS`, the one `ACCEPTED_DIFFERENCE`, `FAIL` or `BLOCKED`. Retain every outcome;
blocked prerequisites are not a pass. Exit codes are 0 for successful executed
checks, 1 for a failed/blocked check report and 2 for handled startup/input errors.

## PostgreSQL coverage

- Empty database upgrade to the current head and stepwise upgrade through every
  linked revision must produce the same schema.
- An empty-data downgrade to `20260713_0002`, followed by re-upgrade, must converge
  to head. This is not proof that populated production data can be downgraded.
- Historical source schemas must match their defined checkpoints and converge
  exactly with fresh head after upgrading.
- An injected future ORM table must not appear in the baseline schema.
- Updater retirement must remove its six settings while retaining unrelated
  settings and audit history.
- A synthetic eligible notification recovery record must cause downgrade refusal
  while preserving its row, revision and schema. Other durable-record guards and
  compatible hold behavior are exercised by the persistence suites listed in
  `recovery_checks.py`.

## Release and rollback limits

Deployment, production migration, restore and live tests require separate
authorization. Prepare matching source/image/schema artifacts and read each touched
migration's data-loss and downgrade behavior. Retain durable attempts and
uncertainty; a code rollback cannot safely erase them or make them pending again.
The [updater retirement note](../releases/remove-dependency-updaters.md) explains
its irreversible data removal.

`IACS_RECOVERY_HOLD` selects the normal application's recovery posture through
`backend/app/core/recovery_hold.py`, `backend/app/recovery_hold.py` and the
`backend/app/main.py` lifespan. Startup verifies exact Alembic-head compatibility
without migrating and leaves executors stopped. Middleware allows authentication
and a bounded list of recovery reads, rejects mutations and WebSockets, and
reports readiness as 503. Owner-level guards also refuse external effects. Review
`_READ_PATHS` for the actual permitted inspection endpoints.

A compatible hold build preserves readable durable work for operator review.
Restoring an older backup can lose command/delivery receipts for effects accepted
after that backup. Reconcile that gap before reactivation; a readable schema and
a successful restore do not establish that resuming work is safe.

No production schema or historical dump is represented by these fixtures. Fresh
and staged equality does not establish compatibility with every deployed database,
accumulated data or out-of-band schema change. The harness's synthetic dump/restore
check is separate evidence and does not validate an operator's real backup.
