"""Set-based accounting backfill, transactional failure and restore-only downgrade."""
from test_feature_retirement import connection, preflight, scratch as scratch
import test_schema_contract as schema

BASE_NAME = preflight()

import json
import uuid

import pytest

pytestmark = pytest.mark.asyncio
BEFORE, REVISION = "20261014_0018", "20261019_0019"


def identity(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-idle-upgrade:" + name)


async def test_populated_backfill_recognizes_receipts_once_without_changing_history(scratch):
    assert schema.migrate(scratch.name, "upgrade", BEFORE, scratch.output, "before")
    client = await connection(scratch.name)
    states = {"legacy":(None,"success"), "queued":(1,"queued"), "processing":(1,"processing"),
        "success":(1,"success"), "failed":(1,"failed"), "skipped":(1,"skipped"),
        "review":(1,"review_required"), "historical":(1,"success"), "deterministic":(1,"failed"),
        "wrong_category":(1,"success"), "wrong_action":(1,"success")}
    try:
        await client.execute("""INSERT INTO automation_rules
            (id,name,is_active,triggers,trigger_keys,conditions,actions,run_count)
            VALUES($1,'Synthetic preserved rule',false,'[]','[]','[]','[]',11)""", identity("rule"))
        for name, (version, status) in states.items():
            await client.execute("""INSERT INTO automation_runs
                (id,rule_id,trigger_key,status,started_at,finished_at,trigger_payload,context,
                 condition_results,action_results,actor,source,recovery_version,action_plan)
                VALUES($1,$2,'synthetic.idle',$3,now(),now(),'{}','{}','[]','[]','Synthetic','synthetic',$4,$5)""",
                identity(name), identity("rule"), status, version,
                json.dumps([{"operation_id":"preserved-checkpoint", "state":"succeeded"}]))
        receipts = [("historical", "automation_engine", "automation_rule.success", {"run_id":str(identity("historical"))}),
            ("duplicate", "automation_engine", "automation_rule.skipped", {"run_id":str(identity("historical"))}),
            ("deterministic", "automation_engine", "automation_rule.failed", {"run_id":str(identity("deterministic"))}),
            ("wrong_category", "other", "automation_rule.success", {"run_id":str(identity("wrong_category"))}),
            ("wrong_action", "automation_engine", "automation_rule.updated", {"run_id":str(identity("wrong_action"))}),
            ("null_value", "automation_engine", "automation_rule.success", {"run_id":None}),
            ("json_null", "automation_engine", "automation_rule.success", None),
            ("missing", "automation_engine", "automation_rule.success", {}),
            ("unrelated", "automation_engine", "automation_rule.success", {"run_id":"unrelated"}),
            ("array", "automation_engine", "automation_rule.success", [])]
        for name, category, action, metadata in receipts:
            audit_id = uuid.uuid5(identity(name), "completion-audit") if name == "deterministic" else identity("audit-"+name)
            await client.execute("""INSERT INTO audit_logs (id,category,action,actor,outcome,level,metadata)
                VALUES($1,$2,$3,'Synthetic','success','info',$4)""", audit_id, category, action, json.dumps(metadata))
        await client.execute("""INSERT INTO audit_logs(id,category,action,actor,outcome,level)
            VALUES($1,'automation_engine','automation_rule.success','Synthetic','success','info')""", identity("sql-null"))
        before = [dict(row) for row in await client.fetch("SELECT * FROM automation_runs ORDER BY id")]
        audits = [dict(row) for row in await client.fetch("SELECT * FROM audit_logs ORDER BY id")]
        # Force a failure after the backfill to prove DDL/data/version are atomic.
        await client.execute("CREATE INDEX ix_lpr_ingest_observation_ready ON lpr_ingest_events(id)")
    finally:
        await client.close()
    assert not schema.migrate(scratch.name, "upgrade", REVISION, scratch.output, "atomic-failure")
    client = await connection(scratch.name)
    try:
        assert await client.fetchval("SELECT version_num FROM alembic_version") == BEFORE
        assert await client.fetchval("""SELECT count(*) FROM information_schema.columns
            WHERE table_name='automation_runs' AND column_name='completion_audit_pending'""") == 0
        assert [dict(row) for row in await client.fetch("SELECT * FROM automation_runs ORDER BY id")] == before
        await client.execute("DROP INDEX ix_lpr_ingest_observation_ready")
    finally:
        await client.close()
    assert schema.migrate(scratch.name, "upgrade", REVISION, scratch.output, "upgrade")
    client = await connection(scratch.name)
    try:
        after = [dict(row) for row in await client.fetch("SELECT * FROM automation_runs ORDER BY id")]
        pending = {row["id"] for row in after if row.pop("completion_audit_pending")}
        assert pending == {identity(name) for name in ("success","failed","skipped","review","wrong_category","wrong_action")}
        assert after == before
        assert [dict(row) for row in await client.fetch("SELECT * FROM audit_logs ORDER BY id")] == audits
        assert await client.fetchval("SELECT run_count FROM automation_rules") == 11
        await client.execute("DELETE FROM audit_logs")
        assert not await client.fetchval("SELECT completion_audit_pending FROM automation_runs WHERE id=$1", identity("historical"))
    finally:
        await client.close()
    assert not schema.migrate(scratch.name, "downgrade", BEFORE, scratch.output, "restore-required")
    client = await connection(scratch.name)
    try:
        assert await client.fetchval("SELECT version_num FROM alembic_version") == REVISION
    finally:
        await client.close()
    (scratch.output / "accounting-upgrade.json").write_text(json.dumps({
        "preserved_runs":len(before), "preserved_audits":len(audits), "pending":len(pending),
        "atomic_failure":True, "restore_required":True}, indent=2))


async def test_fresh_upgrade_has_false_default_constraint_and_partial_index(scratch):
    assert schema.migrate(scratch.name, "upgrade", REVISION, scratch.output, "fresh")
    client = await connection(scratch.name)
    try:
        column = await client.fetchrow("""SELECT is_nullable,column_default FROM information_schema.columns
            WHERE table_name='automation_runs' AND column_name='completion_audit_pending'""")
        assert column["is_nullable"] == "NO" and column["column_default"] == "false"
        index = await client.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname='ix_automation_run_pending_audit'")
        assert "(finished_at, id)" in index and "completion_audit_pending IS TRUE" in index
        assert await client.fetchval("SELECT count(*) FROM pg_constraint WHERE conname='ck_automation_run_pending_audit_terminal'") == 1
    finally:
        await client.close()
