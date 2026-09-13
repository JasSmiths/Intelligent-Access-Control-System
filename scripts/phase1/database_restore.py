"""Synthetic PostgreSQL dump/restore assertions; no runtime or vendor imports.

The harness runs pinned PostgreSQL client tools between prepare and verify. Only
hashes and schema metadata are retained; this never reads a production database.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

import test_schema_contract as schema


def run_rehearsal(harness, images, env, mounts, namespace):
    """Lead-owned orchestration: pinned clients, no published port or live mount."""
    source = "iacs_p1_restore_" + uuid.uuid4().hex[:24]
    target = "iacs_p1_restore_" + uuid.uuid4().hex[:24]
    base = urlsplit(env["IACS_DATABASE_URL"]).path[1:]
    if not re.fullmatch(r"iacs_p1_[a-z0-9_]+", base) or namespace != "container:" + harness.prefix + "-postgres":
        raise ValueError("Restore rehearsal requires the harness-owned PostgreSQL namespace")
    owned = []
    client_env = {"PGPASSWORD": "synthetic-phase1-only"}
    client_mounts = [(harness.run / "artifacts", "/results", "rw")]

    def client(label, command, *, required=True):
        return harness.check(label, harness.container(label, images["postgres"], command,
            env=client_env, mounts=client_mounts, network=namespace), required,
            timeout=180, classification="restore")

    def inspect(mode):
        isolated_env = env | {"IACS_DATABASE_URL": env["IACS_DATABASE_URL"].rsplit("/", 1)[0] + "/" + source}
        label = "database-restore-" + mode
        return harness.check(label, harness.container(label, images["backend"],
            ["/deps/.venv/bin/python", "/workspace/scripts/phase1/database_restore.py", mode,
             "--target", target, "--output", "/results/database-restore"],
            env=isolated_env, mounts=mounts, network=namespace), True, timeout=180, classification="restore")

    try:
        client("database-restore-client-version", ["pg_dump", "--version"])
        client("database-restore-source-create", ["createdb", "-h", "127.0.0.1", "-U", "phase1", "--template=" + base, source])
        owned.append(source)
        inspect("prepare")
        client("database-restore-dump", ["pg_dump", "-h", "127.0.0.1", "-U", "phase1",
            "--format=custom", "--no-owner", "--no-privileges", "--file=/results/database-restore/rehearsal.dump", source])
        client("database-restore-target-create", ["createdb", "-h", "127.0.0.1", "-U", "phase1", "--template=template0", target])
        owned.append(target)
        client("database-restore-load", ["pg_restore", "-h", "127.0.0.1", "-U", "phase1",
            "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges", "--dbname=" + target,
            "/results/database-restore/rehearsal.dump"])
        inspect("verify")
        archive = harness.run / "artifacts/database-restore/rehearsal.dump"
        harness.write("database-restore.json", {"source": source, "target": target,
            "dump_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "postgres_image": images["postgres"], "backend_image": images["backend"],
            "status": "passed", "deployment": "not_performed", "production_data": "not_accessed"})
    finally:
        for index, name in enumerate(reversed(owned)):
            client("database-restore-drop-" + str(index), ["dropdb", "-h", "127.0.0.1", "-U", "phase1", name], required=False)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "verify"))
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    schema.isolated_preflight()
    if not re.fullmatch(r"iacs_p1_restore_[a-f0-9]{24}", args.target):
        raise ValueError("Requires a unique task-owned restore database")
    if args.output.is_symlink() or not args.output.resolve().is_relative_to(Path("/results")):
        raise ValueError("Restore evidence must be retained under /results")
    args.output.mkdir(exist_ok=True, parents=True)
    return args


def database_url(name):
    parts = urlsplit(os.environ["IACS_DATABASE_URL"].replace("+asyncpg", ""))
    return urlunsplit((parts.scheme, parts.netloc, "/" + name, "", ""))


async def identity(connection):
    """Bounded whole synthetic database fingerprint, independent of row order."""
    result = {"schema": {key: [dict(row) for row in await connection.fetch(query)]
                         for key, query in schema.SCHEMA_QUERIES.items()}, "rows": {}, "sequences": {}}
    tables = result["schema"]["tables"]
    if len(tables) > 200:
        raise ValueError("Synthetic restore table limit exceeded")
    for entry in tables:
        name = entry["name"]
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise ValueError("Unexpected synthetic table identifier")
        count = await connection.fetchval(f'SELECT count(*) FROM public."{name}"')
        if count > 10000:
            raise ValueError("Synthetic restore row limit exceeded")
        documents = await connection.fetch(f'SELECT to_jsonb(t)::text AS document FROM public."{name}" t ORDER BY to_jsonb(t)::text')
        digest = hashlib.sha256()
        for document in documents:
            digest.update(document["document"].encode())
            digest.update(b"\n")
        result["rows"][name] = {"count": count, "sha256": digest.hexdigest()}
    for entry in result["schema"]["sequences"]:
        name = entry["name"]
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise ValueError("Unexpected synthetic sequence identifier")
        result["sequences"][name] = dict(await connection.fetchrow(f'SELECT last_value,is_called FROM public."{name}"'))
    result["revisions"] = sorted(await connection.fetch("SELECT version_num FROM alembic_version"), key=lambda row: row[0])
    result["revisions"] = [row[0] for row in result["revisions"]]
    result["normalized_schema"] = await normalized_schema(connection, result["schema"])
    return result


async def normalized_schema(connection, raw):
    """Use PostgreSQL itself to normalize dump/reparse expression representation.

    ARRAY varchar[]->text[] may deparse as per-element casts after pg_restore.
    Raw definitions are still retained. Views exist only in pg_temp and never
    evaluate rows. Index structure is compared separately from its parsed predicate.
    """
    result = json.loads(json.dumps(raw))

    async def expression(table, sql):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise ValueError("Unexpected synthetic expression table")
        await connection.execute('CREATE OR REPLACE TEMP VIEW iacs_restore_expression AS SELECT (' + sql +
                                 ') AS value FROM public."' + table + '"')
        return await connection.fetchval("SELECT pg_get_viewdef('pg_temp.iacs_restore_expression'::regclass, true)")

    for entry in result["constraints"]:
        if entry["type"] == "c":
            definition = entry["definition"]
            if not definition.startswith("CHECK (") or not definition.endswith(")"):
                raise ValueError("Unexpected PostgreSQL check definition")
            entry["definition"] = await expression(entry["table_name"], definition[len("CHECK "):])
    indexes = await connection.fetch("""SELECT tab.relname AS table_name, idx.relname AS name,
        am.amname AS method, i.indisunique AS "unique", i.indisprimary AS "primary",
        i.indisexclusion AS exclusion, i.indimmediate AS immediate, i.indisvalid AS valid,
        i.indnkeyatts AS key_count, i.indnatts AS attribute_count, i.indoption::text AS options,
        idx.reloptions AS storage_options,
        ARRAY(SELECT pg_get_indexdef(i.indexrelid,n,true) FROM generate_series(1,i.indnatts) n) AS keys,
        ARRAY(SELECT ns.nspname||'.'||op.opcname FROM unnest(i.indclass) WITH ORDINALITY u(oid,n)
              JOIN pg_opclass op ON op.oid=u.oid JOIN pg_namespace ns ON ns.oid=op.opcnamespace ORDER BY u.n) AS operator_classes,
        ARRAY(SELECT coalesce(ns.nspname||'.'||c.collname,'') FROM unnest(i.indcollation) WITH ORDINALITY u(oid,n)
              LEFT JOIN pg_collation c ON c.oid=u.oid LEFT JOIN pg_namespace ns ON ns.oid=c.collnamespace ORDER BY u.n) AS collations,
        pg_get_expr(i.indpred,i.indrelid,true) AS predicate
        FROM pg_index i JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_class tab ON tab.oid=i.indrelid
        JOIN pg_namespace ns ON ns.oid=tab.relnamespace JOIN pg_am am ON am.oid=idx.relam
        WHERE ns.nspname='public' ORDER BY tab.relname,idx.relname""")
    result["indexes"] = []
    for row in indexes:
        entry = dict(row)
        if entry["predicate"]:
            entry["predicate"] = await expression(entry["table_name"], entry["predicate"])
        result["indexes"].append(entry)
    await connection.execute("DROP VIEW IF EXISTS pg_temp.iacs_restore_expression")
    return result


async def prepare(connection):
    """Seed linked records plus uncertainty that must survive restoration intact."""
    for name in ("users", "dependency_update_backups", "dependency_update_jobs", "processed_messaging_messages", "access_device_command_records"):
        if await connection.fetchval(f'SELECT count(*) FROM "{name}"'):
            raise ValueError("Restore rehearsal requires a fresh synthetic base database")
    actor, backup, job, message, command, target = [uuid.uuid4() for _ in range(6)]
    await connection.execute("""INSERT INTO users(id,username,first_name,last_name,full_name,password_hash,role,is_active)
        VALUES($1,'synthetic-restore-actor','Synthetic','Admin','Synthetic Restore Admin','unused','ADMIN',true)""", actor)
    await connection.execute("""INSERT INTO dependency_update_backups
        (id,package_name,ecosystem,reason,archive_path,storage_root,checksum_sha256,size_bytes,created_by_user_id,metadata)
        VALUES($1,'synthetic-system','system','restore-rehearsal','/synthetic/never-read.tar.zst','/synthetic',repeat('a',64),1,$2,
        '{"deployment":"not_performed","synthetic":true}'::jsonb)""", backup, actor)
    await connection.execute("""INSERT INTO dependency_update_jobs(id,kind,status,actor,actor_user_id,backup_id,result)
        VALUES($1,'restore','completed','Synthetic Restore Admin',$2,$3,'{"synthetic":true}'::jsonb)""", job, actor, backup)
    await connection.execute("""INSERT INTO processed_messaging_messages
        (id,provider,provider_message_id,provider_channel_id,author_provider_id,recovery_version,state,envelope,routing_context,
         available_at,claimed_at,reply_plan,review_reason)
        VALUES($1,'synthetic','synthetic-interrupted-message','synthetic-channel','synthetic-author',1,'review_required',
        '{"text":"Synthetic test only"}'::jsonb,'{"kind":"denied"}'::jsonb,now(),now(),
        '[{"state":"unknown","recipient":"synthetic"}]'::jsonb,'synthetic interrupted reply')""", message)
    await connection.execute("""INSERT INTO access_device_command_records
        (id,target_device_id,device_key,action,intent_id,idempotency_key,state,binding_snapshot,binding_fingerprint,attempted_at,
         provider_receipts,origin_context)
        VALUES($1,$2,'synthetic_garage','open','synthetic-restore-intent','synthetic-restore-command','unknown',
        '{}'::jsonb,repeat('b',64),now(),'[{"delivery":"unknown"}]'::jsonb,
        '{"kind":"automatic_access_garage","access_event_id":"00000000-0000-0000-0000-000000000001","target_label":"Synthetic garage"}'::jsonb)""", command, target)


async def run(args):
    import asyncpg

    original = urlsplit(os.environ["IACS_DATABASE_URL"]).path[1:]
    connection = await asyncpg.connect(database_url(original), timeout=10)
    try:
        assert await connection.fetchval("SELECT current_database()") == original
        if args.mode == "prepare":
            async with connection.transaction():
                await prepare(connection)
            before = await identity(connection)
            (args.output / "before.json").write_text(json.dumps(before, sort_keys=True, indent=2))
            return
        before = json.loads((args.output / "before.json").read_text())
        source_after = await identity(connection)
        restored = await asyncpg.connect(database_url(args.target), timeout=10)
        try:
            assert await restored.fetchval("SELECT current_database()") == args.target
            after = await identity(restored)
        finally:
            await restored.close()
        checks = {"original_unchanged": source_after == before,
                  "schema_equivalent": not schema.differences(before["normalized_schema"], after["normalized_schema"]),
                  "rows_equivalent": before["rows"] == after["rows"],
                  "sequences_equivalent": before["sequences"] == after["sequences"],
                  "revision_equivalent": before["revisions"] == after["revisions"]}
        (args.output / "after.json").write_text(json.dumps(after, sort_keys=True, indent=2))
        (args.output / "raw-schema-diff.json").write_text(json.dumps(schema.differences(before["schema"], after["schema"]), sort_keys=True, indent=2))
        (args.output / "verification.json").write_text(json.dumps(checks, sort_keys=True, indent=2))
        assert all(checks.values()), checks
        print("PASS: synthetic PostgreSQL schema, linked rows and uncertain work restored; source database unchanged")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(run(arguments()))
