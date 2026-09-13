"""Schema characterization; host-safe static modes, isolated-only PostgreSQL mode.

No application or third-party import occurs until the isolated preflight passes.
Diagnostic failures are failures, not xfails or passing characterization tests.
See docs/validation/recovery-schema.md before running the PostgreSQL mode.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from urllib.parse import urlsplit, urlunsplit
import uuid


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "scripts/phase1/fixtures/schema"
SENTINEL = "schema_contract_future_model_sentinel"


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory() -> list[dict]:
    records = []
    for path in sorted((ROOT / "backend/alembic/versions").glob("*.py")):
        tree = ast.parse(path.read_text())
        record = {"path": str(path.relative_to(ROOT)), "sha256": fingerprint(path)}
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                        record[target.id] = ast.literal_eval(node.value)
            if isinstance(node, ast.ImportFrom) and (
                node.module == "app" or (node.module or "").startswith("app.")
            ):
                issues.append({"line": node.lineno, "kind": "application_import"})
            if isinstance(node, ast.Import):
                if any(alias.name == "app" or alias.name.startswith("app.") for alias in node.names):
                    issues.append({"line": node.lineno, "kind": "application_import"})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"create_all", "drop_all"}:
                    issues.append({"line": node.lineno, "kind": "metadata_schema_mutation"})
        record["semantic_independence_violations"] = issues
        records.append(record)
    return ordered_chain(records)


def ordered_chain(records: list[dict]) -> list[dict]:
    """Fail explicitly on branches/missing parents/cycles, never infer date order."""
    if len({row.get("revision") for row in records}) != len(records):
        raise ValueError("Duplicate/missing migration revision")
    remaining = list(records)
    ordered = []
    parent = None
    while remaining:
        candidates = [row for row in remaining if row.get("down_revision") == parent]
        if len(candidates) != 1:
            raise ValueError("Migration chain is branched, disconnected or cyclic")
        row = candidates[0]
        ordered.append(row)
        remaining.remove(row)
        parent = row["revision"]
    return ordered


def fixture_manifest() -> dict:
    return json.loads((FIXTURES / "manifest.json").read_text())


def check_fixture_integrity() -> None:
    for historical in fixture_manifest()["historical_sources"]:
        for relative, expected in historical["source_sha256"].items():
            if fingerprint(FIXTURES / historical["name"] / relative) != expected:
                raise ValueError("Historical source fixture hash changed: " + relative)


def static_report() -> dict:
    check_fixture_integrity()
    rows = inventory()
    return {
        "mode": "static",
        "status": "FAIL" if any(row["semantic_independence_violations"] for row in rows) else "PASS",
        "migrations": rows,
        "historical_fixture_integrity": "PASS",
        "model_sha256": fingerprint(ROOT / "backend/app/models/core.py"),
        "limitation": "AST tripwire only; does not execute or prove PostgreSQL schema equivalence.",
    }


def validate_environment(env: dict[str, str], interfaces: set[str], workspace: Path) -> str:
    """Pure part of the preflight, also covered by the host self-test."""
    if workspace != Path("/workspace") or interfaces != {"lo"}:
        raise ValueError("Requires /workspace source in a loopback-only Linux namespace")
    url = urlsplit(env.get("IACS_DATABASE_URL", ""))
    name = url.path.removeprefix("/")
    if (
        url.scheme != "postgresql+asyncpg"
        or url.hostname != "127.0.0.1"
        or url.port != 5432
        or url.username != "phase1"
        or url.password != "synthetic-phase1-only"
        or not re.fullmatch(r"iacs_p1_[a-z0-9_]+", name)
        or url.query
        or url.fragment
    ):
        raise ValueError("Requires the synthetic phase1 loopback PostgreSQL database")
    required = {
        "IACS_ENVIRONMENT": "testing",
        "IACS_AUTO_CREATE_SCHEMA": "false",
        "IACS_SEED_DEMO_DATA": "false",
        "IACS_AUTH_SECRET_KEY": "phase1-synthetic-auth-root-never-production",
    }
    if any(env.get(key) != value for key, value in required.items()):
        raise ValueError("Requires explicit isolated test/bootstrap/auth configuration")
    redis = urlsplit(env.get("IACS_REDIS_URL", ""))
    if redis.hostname != "127.0.0.1" or redis.query or redis.fragment:
        raise ValueError("Requires isolated loopback Redis configuration")
    return name


def isolated_preflight() -> str:
    interfaces = Path("/sys/class/net")
    if not interfaces.is_dir():
        raise ValueError("PostgreSQL mode is unavailable on the host")
    name = validate_environment(dict(os.environ), {p.name for p in interfaces.iterdir()}, ROOT)
    forbidden = [
        Path("/var/run/docker.sock"), Path("/run/docker.sock"),
        ROOT / ".env", ROOT / "backend/.env", ROOT / "data", ROOT / "logs",
    ]
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise ValueError("Runtime data, environment file or Docker socket is present")
    return name


def scratch_url(name: str) -> str:
    if not re.fullmatch(r"iacs_p1_schema_[a-f0-9]{12}_[a-z_]+", name):
        raise ValueError("Unowned scratch database identifier")
    original = urlsplit(os.environ["IACS_DATABASE_URL"])
    return urlunsplit((original.scheme, original.netloc, "/" + name, "", ""))


def safe_error(output: str) -> str:
    """Logs are synthetic, but redact URLs/auth values defensively anyway."""
    for key in ("IACS_DATABASE_URL", "IACS_REDIS_URL", "IACS_AUTH_SECRET_KEY"):
        if os.environ.get(key):
            output = output.replace(os.environ[key], "[redacted]")
    output = re.sub(r"postgresql(?:\+asyncpg)?://[^\s\"']+", "[redacted-db-url]", output)
    return output.replace("synthetic-phase1-only", "[redacted]")[-12000:]


def migrate(name: str, direction: str, revision: str, output: Path, label: str,
            historical: str | None = None, sentinel: bool = False) -> bool:
    env = dict(os.environ, IACS_DATABASE_URL=scratch_url(name), PYTHONDONTWRITEBYTECODE="1")
    if historical or sentinel:
        args = [sys.executable, str(Path(__file__).resolve()), "worker", "--target", revision]
        args.extend(["--historical", historical] if historical else ["--sentinel"])
    else:
        args = [sys.executable, "-m", "alembic", direction, revision]
    try:
        result = subprocess.run(args, cwd=ROOT / "backend", env=env, capture_output=True,
                                text=True, timeout=180, check=False)
        log = f"exit_code={result.returncode}\n" + safe_error(result.stdout + result.stderr)
        ok = result.returncode == 0
    except subprocess.TimeoutExpired:
        log, ok = "BLOCKED: migration subprocess timed out and was killed\n", False
    (output / (label + ".log")).write_text(log)
    return ok


SCHEMA_QUERIES = {
    "tables": """SELECT c.relname AS name, c.relkind::text AS kind,
        c.relrowsecurity AS row_security, c.relforcerowsecurity AS force_row_security
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname""",
    "columns": """SELECT c.relname AS table_name,a.attname AS name,
        format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS not_null,
        pg_get_expr(d.adbin,d.adrelid) AS default_expression,
        a.attidentity::text AS identity,a.attgenerated::text AS generated
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0
        AND NOT a.attisdropped ORDER BY c.relname,a.attname""",
    "constraints": """SELECT c.relname AS table_name,k.conname AS name,
        k.contype::text AS type,pg_get_constraintdef(k.oid,true) AS definition,
        k.convalidated AS validated FROM pg_constraint k
        JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname,k.conname""",
    "indexes": """SELECT tablename AS table_name,indexname AS name,indexdef AS definition
        FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname""",
    "enums": """SELECT t.typname AS name,e.enumlabel AS label,e.enumsortorder AS sort_order
        FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid
        JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public'
        ORDER BY t.typname,e.enumsortorder""",
    "sequences": """SELECT sequencename AS name,data_type::text,start_value,min_value,
        max_value,increment_by,cycle,cache_size FROM pg_sequences
        WHERE schemaname='public' ORDER BY sequencename""",
    "triggers": """SELECT c.relname AS table_name,t.tgname AS name,
        pg_get_triggerdef(t.oid,true) AS definition FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",
    "extensions": "SELECT extname AS name,extversion AS version FROM pg_extension ORDER BY extname",
}


async def capture_schema(connect, name: str, output: Path, label: str) -> dict:
    connection = await connect(scratch_url(name).replace("+asyncpg", ""), timeout=10)
    try:
        result = {key: [dict(row) for row in await connection.fetch(sql)]
                  for key, sql in SCHEMA_QUERIES.items()}
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await connection.close()
    (output / (label + ".json")).write_text(json.dumps({"revision": revision, "schema": result},
                                                    indent=2, sort_keys=True) + "\n")
    return result


def differences(left: dict, right: dict) -> dict:
    diff = {}
    for category in sorted(set(left) | set(right)):
        a = {json.dumps(row, sort_keys=True) for row in left.get(category, [])}
        b = {json.dumps(row, sort_keys=True) for row in right.get(category, [])}
        if a != b:
            diff[category] = {"left_only": [json.loads(row) for row in sorted(a - b)],
                              "right_only": [json.loads(row) for row in sorted(b - a)]}
    return diff


def comparison_status(label: str, diff: dict) -> str:
    """Keep the one evidenced historical correction explicit and tightly bounded."""
    if not diff:
        return "PASS"
    column = {"generated": "", "identity": "", "name": "auth_session_version",
              "not_null": True, "table_name": "users", "type": "integer"}
    expected = {"columns": {
        "left_only": [dict(column, default_expression=None)],
        "right_only": [dict(column, default_expression="0")],
    }}
    if label == "historical-pre_recovery-revision-stability" and diff == expected:
        return "ACCEPTED_DIFFERENCE"
    return "FAIL"


async def postgres_run(output: Path) -> int:
    # No application imports. The migration subprocesses import models only after preflight.
    base_name = isolated_preflight()
    check_fixture_integrity()
    import asyncpg

    output = output.resolve()
    if not output.is_relative_to(Path("/results")) or output == Path("/results"):
        raise ValueError("Evidence directory must be a new child of /results")
    output.mkdir(parents=True, exist_ok=False)
    prefix = "iacs_p1_schema_" + uuid.uuid4().hex[:12] + "_"
    owned = []
    checks = []
    admin = await asyncpg.connect(os.environ["IACS_DATABASE_URL"].replace("+asyncpg", ""), timeout=10)
    if await admin.fetchval("SELECT current_database()") != base_name:
        await admin.close()
        raise ValueError("Connected database disagrees with isolated preflight")

    def record(label: str, outcome: str, detail: str) -> None:
        checks.append({"check": label, "status": outcome, "detail": detail})

    def compare(label: str, left: dict, right: dict) -> None:
        diff = differences(left, right)
        (output / (label + "-diff.json")).write_text(json.dumps(diff, indent=2, sort_keys=True) + "\n")
        outcome = comparison_status(label, diff)
        detail = "Exact schema comparison; database names/row data/column order excluded"
        if outcome == "ACCEPTED_DIFFERENCE":
            detail = ("Only users.auth_session_version DEFAULT 0 differs: preserve the historical "
                      "security migration's default for old writers. Revision 20260912_0002 "
                      "converges previously installed schemas; head equivalence must still be exact.")
        record(label, outcome, detail)

    async def create(suffix: str) -> str:
        name = prefix + suffix
        scratch_url(name)  # Strict identifier validation before SQL interpolation.
        await admin.execute('CREATE DATABASE "' + name + '" TEMPLATE template0')
        owned.append(name)
        return name

    try:
        rows = inventory()
        first, head = rows[0]["revision"], rows[-1]["revision"]
        pre_recovery = "20260713_0002"
        for fixture_name in (None, "pre_recovery"):
            export_args = [sys.executable, str(Path(__file__).resolve()), "worker", "--export-ddl", str(output)]
            if fixture_name:
                export_args.extend(["--ddl-model-fixture", fixture_name])
            export_result = subprocess.run(export_args, cwd=ROOT / "backend", capture_output=True,
                                           text=True, timeout=60, check=False)
            label = "pre-recovery-ddl-export" if fixture_name else "baseline-ddl-export"
            (output / (label + ".log")).write_text(safe_error(export_result.stdout + export_result.stderr))
            record(label, "PASS" if export_result.returncode == 0 else "FAIL",
                   "Metadata DDL export; compare against corresponding actual PostgreSQL schema before freezing")
        fresh_db = await create("fresh")
        if not migrate(fresh_db, "upgrade", head, output, "fresh-upgrade"):
            record("fresh-head", "FAIL", "Migration failed; see fresh-upgrade.log")
            fresh = None
        else:
            fresh = await capture_schema(asyncpg.connect, fresh_db, output, "fresh-head")
            record("fresh-head", "PASS", "Empty database upgraded through current revision chain")
        staged_db = await create("staged")
        staged = {}
        for row in rows:
            revision = row["revision"]
            if not migrate(staged_db, "upgrade", revision, output, "staged-" + revision):
                record("staged-upgrade", "FAIL", "Failed at " + revision)
                break
            staged[revision] = await capture_schema(asyncpg.connect, staged_db, output, "staged-" + revision)
        if fresh is not None and head in staged:
            compare("fresh-vs-staged-head", fresh, staged[head])
        else:
            record("fresh-vs-staged-head", "BLOCKED", "A prerequisite migration failed")
        if fresh is not None and pre_recovery in staged:
            if migrate(fresh_db, "downgrade", pre_recovery, output, "roundtrip-downgrade"):
                downgraded = await capture_schema(asyncpg.connect, fresh_db, output, "downgraded-pre-recovery")
                compare("direct-vs-downgraded-pre-recovery", staged[pre_recovery], downgraded)
                if migrate(fresh_db, "upgrade", head, output, "roundtrip-upgrade"):
                    recovered = await capture_schema(asyncpg.connect, fresh_db, output, "roundtrip-head")
                    compare("fresh-vs-roundtrip-head", fresh, recovered)
                else:
                    record("fresh-vs-roundtrip-head", "FAIL", "Re-upgrade failed")
            else:
                record("direct-vs-downgraded-pre-recovery", "FAIL", "Empty-data downgrade failed")
        else:
            record("direct-vs-downgraded-pre-recovery", "BLOCKED", "A prerequisite migration failed")

        if head in staged:
            connection = await asyncpg.connect(scratch_url(staged_db).replace("+asyncpg", ""), timeout=10)
            try:
                await connection.execute("""INSERT INTO notification_runs
                    (id,trigger_event,subject,severity,status,context,delivered_count,failed_count,
                     skipped_count,failures,skipped_reasons,queued_at,recovery_version)
                    VALUES ('00000000-0000-0000-0000-000000000001','schema.probe','Synthetic probe',
                    'info','queued','{}'::jsonb,0,0,0,'[]'::jsonb,'[]'::jsonb,now(),1)""")
            finally:
                await connection.close()
            accepted = migrate(staged_db, "downgrade", pre_recovery, output, "recovery-downgrade-guard")
            marker = "Notification recovery records exist" in (output / "recovery-downgrade-guard.log").read_text()
            after = await capture_schema(asyncpg.connect, staged_db, output, "guard-retained-head")
            connection = await asyncpg.connect(scratch_url(staged_db).replace("+asyncpg", ""), timeout=10)
            try:
                retained = await connection.fetchval("SELECT recovery_version FROM notification_runs WHERE id='00000000-0000-0000-0000-000000000001'")
                revision = await connection.fetchval("SELECT version_num FROM alembic_version")
            finally:
                await connection.close()
            ok = not accepted and marker and retained == 1 and revision == head and not differences(staged[head], after)
            record("recovery-downgrade-guard", "PASS" if ok else "FAIL",
                   "Expected explicit refusal; synthetic eligibility, schema and revision must survive")
        else:
            record("recovery-downgrade-guard", "BLOCKED", "Staged head migration failed")

        sentinel_db = await create("sentinel")
        if migrate(sentinel_db, "upgrade", first, output, "sentinel-upgrade", sentinel=True):
            schema = await capture_schema(asyncpg.connect, sentinel_db, output, "sentinel-baseline")
            leaked = any(row["name"] == SENTINEL for row in schema["tables"])
            record("baseline-future-model-independence", "FAIL" if leaked else "PASS",
                   "In-memory future table " + ("leaked into" if leaked else "excluded from") + " baseline revision")
        else:
            record("baseline-future-model-independence", "BLOCKED", "Sentinel baseline migration failed")

        for fixture in fixture_manifest()["historical_sources"]:
            label = "historical-" + fixture["name"]
            historic_db = await create(fixture["name"])
            if not migrate(historic_db, "upgrade", fixture["target_revision"], output,
                           label + "-create", historical=fixture["name"]):
                record(label, "BLOCKED", "Historical source reconstruction failed; no historical DB dump exists")
                continue
            historical = await capture_schema(asyncpg.connect, historic_db, output, label + "-source-schema")
            if fixture["target_revision"] in staged:
                compare(label + "-revision-stability", historical, staged[fixture["target_revision"]])
            if migrate(historic_db, "upgrade", head, output, label + "-upgrade"):
                upgraded = await capture_schema(asyncpg.connect, historic_db, output, label + "-head")
                if fresh is not None:
                    compare(label + "-vs-fresh-head", fresh, upgraded)
                else:
                    record(label + "-vs-fresh-head", "BLOCKED", "Fresh migration failed")
            else:
                record(label + "-vs-fresh-head", "FAIL", "Historical code-defined schema could not upgrade")
        report = {"mode": "postgres", "checks": checks, "source": static_report(),
                  "server_version": await admin.fetchval("SHOW server_version"),
                  "limitations": fixture_manifest()["limitations"]}
    except Exception as exc:
        record("probe-execution", "BLOCKED", type(exc).__name__ + ": " + safe_error(str(exc)))
        report = {"mode": "postgres", "checks": checks}
    finally:
        # Only databases successfully created by this invocation can be dropped.
        for name in reversed(owned):
            try:
                await admin.execute('DROP DATABASE "' + name + '"')
            except Exception as exc:
                record("scratch-cleanup", "FAIL", name + ": " + type(exc).__name__)
        await admin.close()
    report["status"] = "FAIL" if any(row["status"] not in {"PASS", "ACCEPTED_DIFFERENCE"}
                                     for row in checks) else "PASS"
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for row in checks:
        print(row["status"] + ": " + row["check"] + " — " + row["detail"])
    print("Evidence: " + str(output))
    return 0 if report["status"] == "PASS" else 1


def load_exact_module(name: str, path: Path, package: bool = False) -> None:
    spec = importlib.util.spec_from_file_location(name, path,
        submodule_search_locations=[str(path.parent)] if package else None)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load inspected historical model fixture")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


def load_historical_models(name: str) -> Path:
    model_root = FIXTURES / name / "backend/app"
    import app
    import app.db
    load_exact_module("app.db.base", model_root / "db/base.py")
    load_exact_module("app.models", model_root / "models/__init__.py", package=True)
    app.models = sys.modules["app.models"]
    return model_root


def worker(args) -> int:
    isolated_preflight()
    check_fixture_integrity()
    from alembic import command
    from alembic.config import Config
    config = Config(str(ROOT / "backend/alembic.ini"))
    if args.historical:
        fixture = next(row for row in fixture_manifest()["historical_sources"]
                       if row["name"] == args.historical)
        if args.target != fixture["target_revision"]:
            raise ValueError("Historical reconstruction target differs from provenance")
        # Preserve old execution even after the current baseline is corrected.
        config.set_main_option("script_location", str(FIXTURES / args.historical / "backend/alembic"))
        load_historical_models(args.historical)
    elif args.sentinel:
        from sqlalchemy import Column, Integer, Table
        from app import models  # noqa: F401
        from app.db.base import Base
        Table(SENTINEL, Base.metadata, Column("id", Integer, primary_key=True))
    elif args.export_ddl:
        from sqlalchemy import create_mock_engine
        model_root = (load_historical_models(args.ddl_model_fixture)
                      if args.ddl_model_fixture else ROOT / "backend/app")
        from app import models  # noqa: F401
        from app.db.base import Base
        destination = args.export_ddl.resolve()
        if not destination.is_relative_to(Path("/results")) or not destination.is_dir():
            raise ValueError("DDL export requires an existing /results evidence directory")
        statements = []

        def capture(sql, *_multiparams, **_params):
            statements.append(str(sql.compile(dialect=engine.dialect)).strip())

        engine = create_mock_engine("postgresql://", capture)
        Base.metadata.create_all(engine, checkfirst=False)
        upgrades = ["CREATE EXTENSION IF NOT EXISTS vector", *statements]
        statements.clear()
        downgrade_note = None
        if args.ddl_model_fixture == "initial":
            # The historical model left this cyclic FK unnamed. PostgreSQL's
            # actual name is captured in historical-initial-source-schema.json.
            # Keep upgrade bytes unchanged; name the cycle edge only so the
            # frozen downgrade can remove it before dropping the two tables.
            table = Base.metadata.tables["external_dependencies"]
            constraint = next(
                fk for fk in table.foreign_key_constraints
                if list(fk.column_keys) == ["latest_analysis_id"]
            )
            constraint.name = "external_dependencies_latest_analysis_id_fkey"
            constraint.use_alter = True
            downgrade_note = "Cyclic FK named from observed historical PostgreSQL schema; historical upgrade unchanged."
        Base.metadata.drop_all(engine, checkfirst=False)
        ddl = {"upgrade": upgrades, "downgrade": statements,
               "downgrade_note": downgrade_note,
               "model_sha256": fingerprint(model_root / "models/core.py"),
               "historical_fixture": args.ddl_model_fixture,
               "meaning": "Selected model metadata DDL, before later revisions; no row data, no database connection."}
        prefix = args.ddl_model_fixture.replace("_", "-") if args.ddl_model_fixture else "current"
        (destination / (prefix + "-baseline-ddl.json")).write_text(json.dumps(ddl, indent=2) + "\n")
        (destination / (prefix + "-baseline.sql")).write_text(";\n\n".join(upgrades) + ";\n")
        return 0
    else:
        raise ValueError("Worker requires an explicit historical fixture or sentinel")
    command.upgrade(config, args.target)
    return 0


class StaticSelfTests(unittest.TestCase):
    def test_fixtures_have_exact_captured_bytes(self):
        check_fixture_integrity()

    def test_chain_uses_revision_links_not_filenames(self):
        rows = inventory()
        self.assertEqual(rows[0]["revision"], "20260531_0000")
        self.assertEqual(rows[1]["revision"], "20260509_0001")

    def test_chain_rejects_no_progress_and_branches(self):
        for records in ([{"revision": "a", "down_revision": "b"}],
                        [{"revision": "a", "down_revision": None},
                         {"revision": "b", "down_revision": None}]):
            with self.assertRaises(ValueError):
                ordered_chain(records)

    def test_environment_rejects_host_routes_and_live_database(self):
        env = {"IACS_DATABASE_URL": "postgresql+asyncpg://phase1:synthetic-phase1-only@127.0.0.1:5432/iacs_p1_selftest",
               "IACS_REDIS_URL": "redis://127.0.0.1:6379/0", "IACS_ENVIRONMENT": "testing",
               "IACS_AUTH_SECRET_KEY": "phase1-synthetic-auth-root-never-production",
               "IACS_AUTO_CREATE_SCHEMA": "false", "IACS_SEED_DEMO_DATA": "false"}
        self.assertEqual(validate_environment(env, {"lo"}, Path("/workspace")), "iacs_p1_selftest")
        for interfaces, workspace, url in [
            ({"lo", "eth0"}, Path("/workspace"), env["IACS_DATABASE_URL"]),
            ({"lo"}, Path("/Users/source"), env["IACS_DATABASE_URL"]),
            ({"lo"}, Path("/workspace"), env["IACS_DATABASE_URL"].replace("iacs_p1_selftest", "iacs")),
            ({"lo"}, Path("/workspace"), env["IACS_DATABASE_URL"] + "?host=other"),
            ({"lo"}, Path("/workspace"), env["IACS_DATABASE_URL"].replace("127.0.0.1", "postgres")),
        ]:
            with self.assertRaises(ValueError):
                validate_environment(dict(env, IACS_DATABASE_URL=url), interfaces, workspace)

    def test_schema_diff_does_not_hide_defaults_or_indexes(self):
        self.assertEqual(differences({"columns": []}, {"columns": []}), {})
        self.assertIn("columns", differences({"columns": [{"default": None}]},
                                            {"columns": [{"default": "0"}]}))

    def test_historical_default_correction_is_exact_and_never_waives_head_parity(self):
        column = {"generated": "", "identity": "", "name": "auth_session_version",
                  "not_null": True, "table_name": "users", "type": "integer"}
        diff = differences({"columns": [dict(column, default_expression=None)]},
                           {"columns": [dict(column, default_expression="0")]})
        self.assertEqual(comparison_status("historical-pre_recovery-revision-stability", diff),
                         "ACCEPTED_DIFFERENCE")
        self.assertEqual(comparison_status("historical-pre_recovery-vs-fresh-head", diff), "FAIL")
        self.assertEqual(comparison_status("historical-pre_recovery-revision-stability",
                         dict(diff, indexes={"left_only": [], "right_only": [{}]})), "FAIL")
        diff["columns"]["right_only"][0]["default_expression"] = "1"
        self.assertEqual(comparison_status("historical-pre_recovery-revision-stability", diff), "FAIL")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    commands.add_parser("self-test", help="Host-safe stdlib tests; no app imports or DB")
    commands.add_parser("static", help="Host-safe independence diagnostic; nonzero when violated")
    pg = commands.add_parser("postgres", help="Isolated phase1 namespace only; owns disposable scratch DBs")
    pg.add_argument("--output-dir", type=Path, required=True)
    child = commands.add_parser("worker", help=argparse.SUPPRESS)
    child.add_argument("--target")
    child.add_argument("--ddl-model-fixture", choices=["initial", "pre_recovery"])
    choice = child.add_mutually_exclusive_group(required=True)
    choice.add_argument("--historical", choices=["initial", "pre_recovery"])
    choice.add_argument("--sentinel", action="store_true")
    choice.add_argument("--export-ddl", type=Path)
    argv = sys.argv[1:]
    if not argv:
        argv = ["postgres", "--output-dir", "/results/schema-contract-" + uuid.uuid4().hex[:12]]
    elif argv[0] in {"--static", "--self-test"}:
        argv[0] = argv[0][2:]
    args = parser.parse_args(argv)
    if args.mode == "self-test":
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StaticSelfTests))
        return 0 if result.wasSuccessful() else 1
    if args.mode == "static":
        report = static_report()
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.mode == "worker":
        return worker(args)
    return asyncio.run(postgres_run(args.output_dir))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print("BLOCKED: " + safe_error(str(error)), file=sys.stderr)
        raise SystemExit(2) from None
