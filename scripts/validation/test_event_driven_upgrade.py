"""Index-only scheduling upgrade preserves claims, receipts and rollback atomicity."""
from test_feature_retirement import connection, preflight, scratch as scratch
import test_schema_contract as schema

BASE_NAME = preflight()

import ast
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio
BEFORE, REVISION = "20261019_0019", "20261020_0020"
MIGRATION = Path(__file__).resolve().parents[2] / "backend/alembic/versions/20261020_0020_durable_worker_wakes.py"
INDEXES = next(ast.literal_eval(node.value) for node in ast.parse(MIGRATION.read_text()).body
               if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "INDEXES" for target in node.targets))


async def history(client):
    result = {}
    for table in sorted({entry[0] for entry in INDEXES} | {"notification_runs", "automation_runs", "vehicle_information_jobs"}):
        result[table] = [dict(row) for row in await client.fetch("SELECT * FROM " + table + " ORDER BY 1")]
    return result


async def index_state(client):
    return {row["relname"]:dict(row) for row in await client.fetch("""SELECT c.relname,t.relname AS table_name,
        i.indisvalid,i.indisready,i.indisunique,i.indnkeyatts,pg_get_expr(i.indpred,i.indrelid) AS predicate,
        pg_get_indexdef(i.indexrelid) AS definition FROM pg_index i
        JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_class t ON t.oid=i.indrelid
        WHERE c.relname=ANY($1::text[]) ORDER BY c.relname""", [entry[1] for entry in INDEXES])}


async def test_populated_upgrade_is_index_only_atomic_and_restore_required(scratch):
    assert schema.migrate(scratch.name, "upgrade", BEFORE, scratch.output, "before")
    client = await connection(scratch.name)
    try:
        await client.execute("""INSERT INTO automation_rules(id,name,is_active,triggers,trigger_keys,conditions,actions,run_count)
            VALUES(md5('wake-rule')::uuid,'Synthetic preserved rule',false,'[]','[]','[]','[]',7)""")
        await client.execute("""INSERT INTO automation_runs(id,rule_id,trigger_key,status,started_at,trigger_payload,context,
            condition_results,action_results,actor,source,recovery_version,queued_at,claim_token,lease_expires_at,claim_count,action_plan)
            VALUES(md5('wake-run')::uuid,md5('wake-rule')::uuid,'synthetic.wake','processing',now(),'{}','{"version":1}',
            '[]','[]','Synthetic','synthetic',1,now(),md5('wake-lease')::uuid,now()+interval '1 hour',3,
            '[{"state":"attempting","operation_id":"preserved-attempt"}]')""")
        await client.execute("""INSERT INTO notification_runs(id,trigger_event,subject,severity,status,context,delivered_count,
            failed_count,skipped_count,failures,skipped_reasons,queued_at,recovery_version,claim_token,lease_expires_at,delivery_plan)
            VALUES(md5('wake-notification')::uuid,'synthetic.wake','Synthetic','info','processing','{}',1,0,0,'[]','[]',now(),1,
            md5('wake-notification-lease')::uuid,now()+interval '1 hour','[{"state":"accepted","receipt":"preserved"}]')""")
        await client.execute("""INSERT INTO access_events(id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification)
            VALUES(md5('wake-access')::uuid,'SYN0001','ENTRY','GRANTED',1,'synthetic',now(),'UNKNOWN')""")
        await client.execute("""INSERT INTO access_completion_work(id,event_id,stage,origin,readiness,state,due_at,deadline,
            lease_token,lease_until,attempt_count,result)
            VALUES(md5('wake-completion')::uuid,md5('wake-access')::uuid,'snapshot','{"version":1}',
            'committed_decision','leased',now(),now()+interval '5 minutes',md5('wake-completion-lease')::uuid,
            now()+interval '1 minute',2,'{"preserved_checkpoint":true}')""")
        await client.execute("""INSERT INTO vehicle_information_jobs(event_id,registration_number,created_at,origin_at,deadline,status,
            readiness,lease_token,lease_until,accepted_result_revision,accepted_result_at,accepted_result)
            VALUES(md5('wake-access')::uuid,'SYN0001',now(),now(),now()+interval '300 seconds','processing','committed_decision',
            md5('wake-information-lease')::uuid,now()+interval '1 minute',1,now(),'{"make":"Preserved"}')""")
        for suffix, start, end, minutes in (("explicit", "now()", "now()+interval '1 hour'", 30),
                                           ("legacy-zero", "NULL", "NULL", 0),
                                           ("legacy-large", "NULL", "NULL", 2000)):
            await client.execute("""INSERT INTO visitor_passes(id,visitor_name,pass_type,expected_time,window_minutes,
                valid_from,valid_until,status,creation_source)
                VALUES(md5($1)::uuid,'Synthetic retained visitor','ONE_TIME',now(),$2,""" + start + "," + end + ",'SCHEDULED','synthetic')", suffix, minutes)
        before = await history(client)
        # Collision at the last DDL statement proves prior indexes and version
        # changes roll back together rather than leaving a partly upgraded schema.
        _, collision, _, _ = INDEXES[-1]
        await client.execute("CREATE INDEX " + collision + " ON notification_action_contexts(id)")
    finally:
        await client.close()
    assert not schema.migrate(scratch.name, "upgrade", REVISION, scratch.output, "atomic-failure")
    client = await connection(scratch.name)
    try:
        assert await client.fetchval("SELECT version_num FROM alembic_version") == BEFORE
        assert set(await index_state(client)) == {collision}
        assert await history(client) == before
        await client.execute("DROP INDEX " + collision)
    finally:
        await client.close()
    assert schema.migrate(scratch.name, "upgrade", REVISION, scratch.output, "upgrade")
    client = await connection(scratch.name)
    try:
        assert await history(client) == before
        assert await client.fetchval("SELECT run_count FROM automation_rules") == 7
        indexes = await index_state(client)
        assert len(indexes) == len(INDEXES)
        for table, name, fields, predicate in INDEXES:
            row = indexes[name]
            assert row["table_name"] == table and row["indisvalid"] and row["indisready"]
            assert not row["indisunique"] and row["indnkeyatts"] == len(fields)
            assert row["predicate"]
            if name.startswith("ix_visitor_pass_lifecycle"):
                assert "UTC" in row["definition"] and "CASE" in row["definition"]
    finally:
        await client.close()
    assert not schema.migrate(scratch.name, "downgrade", BEFORE, scratch.output, "restore-required")
    client = await connection(scratch.name)
    try:
        assert await client.fetchval("SELECT version_num FROM alembic_version") == REVISION
        assert await index_state(client) == indexes
        assert await history(client) == before
    finally:
        await client.close()
    (scratch.output / "event-driven-upgrade.json").write_text(json.dumps({"index_count":len(INDEXES),
        "atomic_failure":True,"preserved_claims_and_receipts":True,"restore_required":True,"indexes":indexes}, indent=2))


async def test_fresh_upgrade_creates_all_valid_partial_indexes(scratch):
    assert schema.migrate(scratch.name, "upgrade", REVISION, scratch.output, "fresh")
    client = await connection(scratch.name)
    try:
        indexes = await index_state(client)
        assert set(indexes) == {entry[1] for entry in INDEXES}
        assert all(row["indisvalid"] and row["indisready"] and row["predicate"] for row in indexes.values())
    finally:
        await client.close()
