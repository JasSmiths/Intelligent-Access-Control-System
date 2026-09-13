# Recovery foundation: schema characterization

## Current implementation gate (2026-09-12)

The initial diagnostic outcomes below are retained as evidence, not current
release claims. The lead froze revision `20260531_0000` to the exact earliest
May schema in `backend/alembic/schema/20260531_0000.json`, including the prior
check-first semantics, independently of application metadata. Revision
`20260912_0002` establishes the historical auth-session default on every path.
`evidence/schema-freeze-3xzlu66a` passed fresh/staged/roundtrip/head comparisons,
both historical upgrade comparisons, future-model independence and cleanup.

One historical checkpoint has an explicit `ACCEPTED_DIFFERENCE`: the exact
pre-recovery source created `users.auth_session_version` without a SQL default,
whereas its historical security migration specified `DEFAULT 0`. The comparator
accepts only that exact column/default difference at that exact checkpoint;
extra differences, different defaults and any final-head difference still fail.
The rationale is compatible old writers and stable security-session behavior.
No rows are rewritten. Six host self-tests include this acceptance boundary.

New record migrations require another fresh/historical equivalence run plus
populated-record downgrade refusal and compatible hold-build tests before release.
No production database, backup or restore has been inspected or executed.

This package adds probes and immutable historical source fixtures only. It does
not change application models, migration history, Alembic configuration or live
data. The implementation lead owns those changes separately. The application
source under the original repository is a live bind mount; run the recovery
work from the isolated copy recorded by the recovery baseline manifest.

## Source evidence and scope

The frozen source is dirty work based on commit
`69f9d8cfc1f77417a57105223f4e0772ae62de5d`. The original evidence manifest is
`/private/tmp/iacs-recovery-7ggriel9/evidence/baseline.json`; portable source
fingerprints and historical fixture provenance are in
`scripts/phase1/fixtures/schema/manifest.json`.

Every current migration was inspected, including the untracked notification
recovery migration. Ordering follows revision links, not dates:

| Revision | Schema ownership and caveat |
| --- | --- |
| `20260531_0000` | Imports current `app.models` and `Base`; creates/drops current metadata. Its meaning changes with application models. |
| `20260509_0001` | Vector extension, four semantic embedding columns and filtered HNSW indexes. Follows the later-dated baseline. |
| `20260531_0002` | Durable LPR ingest table/indexes and webhook hardening fields/indexes; explicit additive SQL, largely `IF NOT EXISTS`. |
| `20260531_0003` | Notification run table/indexes with explicit SQL defaults. A metadata-created table makes `CREATE TABLE IF NOT EXISTS` skip these defaults. |
| `20260624_0001` | Auth session fields, webhook nonces, processed messaging IDs and revoked-token tables/indexes. |
| `20260713_0001` | Replaces visitor departure lookup predicate to retain open duration departures. |
| `20260713_0002` | Reconciles person-assignment and visitor source-reference indexes. |
| `20260912_0001` | Seven notification run recovery columns and one gate outbox eligibility column; refuses downgrade when eligible recovery records exist. |

The baseline was introduced at commit
`4bdce566db9bfb1801a0bb393eb447db73eed674`. Its migration file is unchanged at
the frozen source. Fixtures contain exact Git source bytes from that commit and
from `69f9d8cfc1f77417a57105223f4e0772ae62de5d`: models, enums, model registration,
Base, migration files and Alembic environment. SHA-256 validation protects their
identity. Historical migrations are deliberately kept inside fixtures and are
never added to the application's migration search path.

Historical reconstruction uses those model/migration sources with **current
locked dependencies and the isolated PostgreSQL engine**, plus the current
settings parser (synthetic environment only). It is evidence of a code-defined
schema at that commit. It is not a historical database dump, production schema,
historical dependency reproduction or proof about accumulated data. No such
database/dump was available or accessed for this package. The deliberately
retained `people.home_assistant_presence_entity_id` column is not fabricated in
these fresh source reconstructions; testing preservation of a real historical
database containing it remains an explicit gap.

## Commands and meanings

The script imports only the Python standard library in its host modes. From the
isolated recovery checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_schema_contract.py --self-test
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_schema_contract.py --static
```

`--self-test` checks fixture hashes, linked revision ordering, no-progress
rejection, namespace/URL rejection and schema diff sensitivity. It does not
import IACS or contact a database. `--static` returns **1** when it finds
application imports or metadata schema mutation in a migration. This diagnostic
failure is recorded separately from the passing self-tests; do not use `xfail`,
`|| true`, baseline acceptance or a passing test of the defect as a release gate.

The no-argument command is for the harness's `--schema-check` hook. It creates a
unique `/results/schema-contract-<id>` evidence directory. An equivalent explicit
command **inside the already preflighted isolated namespace only** is:

```bash
/deps/.venv/bin/python /workspace/scripts/phase1/test_schema_contract.py postgres \
  --output-dir /results/schema-contract-reviewed-run
```

The explicit output directory must be a new child of `/results`. A stale
directory is rejected rather than overwriting earlier evidence. Exit codes:
**0** all executed contract checks passed; **1** a diagnostic/check failed or was
blocked after execution began; **2** startup/preflight/input failed. Never infer
a PostgreSQL pass from the static result or from the script's ability to compile.

For the authorized implementation lead using the current phase1 harness:

```bash
python3 scripts/phase1/validate.py \
  --schema-check scripts/phase1/test_schema_contract.py \
  --include scripts/phase1/test_schema_contract.py \
  --include scripts/phase1/fixtures/schema/manifest.json
```

Until tracked, also supply a separate `--include` for **every** file beneath
`scripts/phase1/fixtures/schema/initial/` and `pre_recovery/`; use
`rg --files scripts/phase1/fixtures/schema` for the exact list. Include this
document if collecting a full review snapshot. The lead's invocation owns
dependency reuse, evidence directory selection and serialization; no parallel
Docker runs or production-selecting `scripts/backend-pytest` are allowed.

## Isolation and ownership

Before any application/third-party import or PostgreSQL connection, runtime
mode requires all of:

- Source resolves to `/workspace`, in Linux with exactly the `lo` interface.
- No Docker socket, source `.env`/`backend/.env`, runtime `data/` or `logs/`.
- Explicit testing environment, auto-create/seed disabled, synthetic phase1
  auth root, loopback-only Redis configuration.
- PostgreSQL URL is exactly the phase1 synthetic user/password, loopback host,
  port 5432, an `iacs_p1_` database name, and no URL query/fragment overrides.

It verifies the connected base database name, then creates only uniquely named
`iacs_p1_schema_<random-id>_<scenario>` databases from `template0`. All migration
and synthetic row writes target those owned scratch databases. The base harness
database is used only to create/drop scratch databases and read engine identity.
Only names successfully created by this invocation enter its cleanup list.
Cleanup errors fail the report; the script does not kill other connections or
drop an existing/shared database. The harness's disposable PostgreSQL instance
must provide CREATE/DROP DATABASE privileges and the installed vector extension.

Application startup, `init_database`, seeding, workers, provider adapters, Redis
actions and live services are never invoked. Schema records contain definitions
only, never table rows or personal data. The downgrade guard inserts one clearly
synthetic notification row in its own scratch database. Logs redact URL/auth
values defensively. No production credentials are accepted by the preflight.

## PostgreSQL checks and retained artifacts

| Probe | Contract and evidence |
| --- | --- |
| `baseline-ddl-export` | Compiles current metadata against a PostgreSQL mock dialect after preflight. Exports `current-baseline-ddl.json` (ordered upgrade/downgrade statements and model hash) and `current-baseline.sql`. No connection or DDL execution occurs in the export itself. Compare against the actual staged baseline schema before using it as frozen DDL. |
| `pre-recovery-ddl-export` | Exports exact `69f9d8c` model fixtures to `pre-recovery-baseline-ddl.json` and `pre-recovery-baseline.sql`, independently of current application models. Added after the first full run; requires its own retained execution result. |
| `fresh-head` | Empty scratch database upgraded to the current head. |
| `fresh-vs-staged-head` | A different scratch database stops at each of all eight linked revisions, then reaches head. Compares schemas against fresh. A pass alone does not prove historical independence: both paths still use today's baseline implementation. |
| `direct-vs-downgraded-pre-recovery` | Compares direct migration to `20260713_0002` with current head downgraded there on empty synthetic data. Detects future recovery columns created prematurely by the baseline. |
| `fresh-vs-roundtrip-head` | Re-upgrades that downgraded database and checks final schema equivalence. |
| `recovery-downgrade-guard` | Inserts a synthetic eligible notification run. Requires the explicit migration refusal, original revision, unchanged schema and retained eligibility. Other errors are failures, not a successful guard. |
| `baseline-future-model-independence` | Adds one in-memory model table, without source edits, before upgrading only the initial revision. The sentinel must not appear in the database. |
| `historical-initial-*` | Reconstructs the initial baseline from exact May source; compares revision meaning against current source, then upgrades using the current later migrations and compares head. Missing later migrations/schema drift fail honestly. |
| `historical-pre_recovery-*` | Reconstructs exact committed pre-recovery source, compares its checkpoint with current source, then upgrades with current migrations and compares head. |

Each schema artifact records the actual Alembic revision. Comparison covers
public tables, column types/nullability/defaults/identity/generated values,
constraints including validation, indexes including expressions/predicates,
enum labels/order, sequence definitions, noninternal triggers and extensions.
Database names/OIDs, row contents, column physical order and sequence current
values are deliberately excluded. No compared defaults/indexes are suppressed
by Alembic's `include_object` policy. The inspected migrations create no durable
views, policies, standalone functions or domains; those objects need explicit
comparison support if future migrations introduce them. This is not a full
PostgreSQL schema dump or backup.

`report.json`, per-stage migration logs, schema snapshots and `*-diff.json`
contain the concrete outcomes. Known failures do not prevent unrelated probes
from running; a setup exception reports its dependent checks as blocked. No
schema-hazard diagnostic is hidden inside the passing backend test total.

## Recorded outcomes at package handoff

Commands were run from `/private/tmp/iacs-recovery-7ggriel9/work`:

| Command | Observed outcome |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_schema_contract.py --self-test` | **PASS**, 5 stdlib tests. No model import, dependency change or database connection. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_schema_contract.py --static` | **FAIL**, exit 1. Baseline application imports at lines 12–13 and current metadata create/drop at lines 25/30. Eight revisions inventoried; historical fixture hashes pass. |
| `PYTHONDONTWRITEBYTECODE=1 python3 scripts/phase1/test_schema_contract.py` on the host | **BLOCKED as required**, exit 2 before any connection: PostgreSQL mode is unavailable on the host. |
| PostgreSQL schema checks | **Executed by the lead** in `/private/tmp/iacs-recovery-7ggriel9/evidence/iacs-phase1-j23gc90q/schema-contract-acf6a4ab9a5c/`. Six checks pass and five fail; details below. PostgreSQL 16.15. No live database was used. |

The lead's initial full-run source snapshot is
`/private/tmp/iacs-recovery-7ggriel9/evidence/iacs-phase1-j23gc90q/source`.
Its schema CLI used source SHA-256
`0cb4fe32852d46516220fba65321cae6a4b8cc78071b18e6bcbaaf0f09afd9d7`.
The subsequent pre-recovery DDL export addition has source SHA-256
`0ded881abbb44fa8c9d58cd27f449c55cb522543666a4d326928fb6ff92133b1` and passed the
five stdlib self-tests. Its new export branch had not yet run at this handoff;
the existing PostgreSQL results apply to the first fingerprint only.

Observed PostgreSQL passes: current DDL export, fresh head, fresh/staged head
equivalence, fresh/downgrade/re-upgrade head equivalence, the explicit recovery
downgrade guard and historical pre-recovery-to-head equivalence. Failures:
direct versus downgraded pre-recovery schema; sentinel leakage; historical
initial and pre-recovery revision stability; historical initial-to-head
equivalence. The last failure is exactly one column default, detailed below.
Both historical schemas actually upgraded successfully; no missing migration
table/field was found in these source-defined upgrade probes.

The standalone candidate export command, inside the isolated namespace, is:

```bash
/deps/.venv/bin/python /workspace/scripts/phase1/test_schema_contract.py worker \
  --export-ddl /results/schema-contract-reviewed-run \
  --ddl-model-fixture pre_recovery
```

Here the destination must already exist. This compiles DDL but does not create a
database. Each output includes the model hash; retain those bytes for review.
Subsequent authorized changes/runs need their own source fingerprints. This
document is not a substitute for the final harness report.

## Minimum baseline correction for the migration owner

The completed PostgreSQL comparison changes the initial recommendation: freeze
**the exact initial May source-defined schema**, not a September compatibility
baseline. The isolated May-to-current upgrade succeeded and differs from fresh
head only in the known auth-session default. The existing later revisions thus
cover all other measured structural changes. Earlier pre-recovery candidate DDL
remains useful comparison evidence, but is not the preferred replacement.

The exporter supports `--ddl-model-fixture initial` and writes
`initial-baseline-ddl.json` / `initial-baseline.sql`. The lead owns this candidate
export, its subsequent downgrade serialization fix and all migration edits.
The initial model contains an unnamed cyclic foreign key; mock downgrade
compilation needs the actual reflected constraint name
`external_dependencies_latest_analysis_id_fkey`, observed in the retained
historical initial schema. Capturing unchanged upgrade DDL before applying that
name for downgrade serialization preserves source evidence. This explicit
correction is not a licence to change old fixture source bytes.

Replace revision `20260531_0000` application imports and current metadata
create/drop calls with reviewed migration-local DDL from that initial fixture.
Preserve every revision ID and parent link, vector extension behavior, later
embedding/index migrations, data and the deliberately retained legacy column.
Freeze matching ordered downgrade statements, including enum cleanup. Do not
regenerate historical DDL from new feature models.

Acceptance gates for the lead's separate implementation:

1. Preserve old actual baseline/pre-recovery/head schemas and source/DDL exports.
2. Candidate initial schema equals the historical initial source schema. Execute
   fresh upgrade, each intermediate checkpoint and historical upgrades under
   the unchanged revision ordering.
3. Final candidate head differs from the old frozen head only by the declared
   auth-session `DEFAULT 0` correction below. Both historical upgrade paths
   converge exactly to that candidate head.
4. Sentinel independence, static independence, direct/downgraded pre-recovery
   equivalence, empty-data roundtrip and recovery downgrade guard pass.
5. Keep any historical checkpoint differences visible. The committed September
   metadata fixture lacked the auth-session server default, whereas the actual
   security migration added it; only that exact reviewed difference may be
   classified separately. No other schema difference is implicitly approved.

These remain source-defined reconstructions with current locked dependencies,
not historical production dumps. Do not guess older migrations, silently stamp
head, create tables during runtime bootstrap, alter fixtures to make checks pass,
or drop data to satisfy comparison. Actual deployed databases at head do not
rerun the corrected baseline. Their compatibility needs separately authorized
backup/restore rehearsal, schema/version inspection and the forward migration.
After recovery-version rows exist, code rollback retains additive schema and
evidence; raw downgrade remains forbidden by its existing guard.

### Concrete historical convergence correction

The retained `historical-initial-vs-fresh-head-diff.json` contains exactly one
difference: `users.auth_session_version` is an integer, NOT NULL on both paths,
but its server default is absent on a fresh current install and `0` on the
May-source upgrade. No tables, other columns, indexes, constraints, enum labels,
sequence definitions, triggers or extensions differ in that comparison.
`20260624_0001` explicitly adds this field with `DEFAULT 0`; the metadata baseline
creates it earlier without a server default, causing the migration to skip it.

Converge on **DEFAULT 0**. The migration owner should add `server_default="0"`
to the model declaration and a new forward migration:

```sql
ALTER TABLE users ALTER COLUMN auth_session_version SET DEFAULT 0;
```

This changes no existing values. It preserves the explicitly deployed security
migration's default and insert compatibility for older code that does not set
the newly added field. Dropping the default merely to match fresh metadata
could break that compatibility. Let the forward migration converge both the
frozen pre-recovery baseline and existing installations; do not rewrite the
historical source fixtures or change old row values. Accept this as a declared
schema-contract correction when comparing the candidate head against the
frozen old head: the sole allowed difference is this default. After that
correction, fresh and both historical upgrade paths must agree exactly.

Freezing the actual initial fixture should restore initial-revision stability.
The pre-recovery committed-metadata checkpoint can retain the single explicit
auth-default difference explained above; preserve that evidence and require
exact final-head convergence. Never suppress other drift under that exception.
