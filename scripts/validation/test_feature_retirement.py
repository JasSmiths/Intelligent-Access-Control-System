"""Populated retirement proofs in a separately owned synthetic PostgreSQL database."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
import uuid

import test_schema_contract as schema


def preflight():
    if os.environ.get("IACS_RECOVERY_PROBES") != "synthetic-only":
        raise RuntimeError("Feature retirement tests require explicit synthetic-only intent")
    base = schema.isolated_preflight()
    for key, value in os.environ.items():
        if key.startswith("IACS_") and any(part in key for part in ("TOKEN", "PASSWORD", "API_KEY")) and value:
            raise RuntimeError("Feature retirement tests refuse integration credentials")
    return base


BASE_NAME = preflight()

# Only the fail-closed isolated namespace can import the database/test runtime.
import asyncpg
import pytest
import pytest_asyncio

BEFORE = "20261002_0009"
REVISION = "20261005_0010"
RETIRED_TABLES = {
    "chat_sessions", "chat_messages", "alfred_approvals", "alfred_memories",
    "alfred_lessons", "alfred_feedback", "alfred_eval_examples",
    "processed_messaging_messages", "messaging_identities",
}
CORE_TABLES = ("access_events", "visitor_passes")
IDS = {name: uuid.UUID(int=index) for index, name in enumerate((
    "access", "pass", "pass_sql_null", "pass_json_null", "pass_string", "pass_array", "backup", "chat", "chat_message", "identity", "message",
    "notification_mixed", "notification_empty", "notification_orphan", "notification_safe",
    "automation_mixed", "automation_empty", "cron_active", "cron_inactive",
    "cron_invalid_mixed", "cron_invalid_empty", "automation_safe",
    "notification_pending", "notification_claimed", "notification_history", "notification_unaffected",
    "automation_claimed", "automation_deleted", "automation_ai_pending", "automation_history", "automation_unaffected",
), start=10001)}
BACKUP_METADATA = {
    "database_backup": "synthetic-retirement.dump", "files_backup": "synthetic-files.tar",
    "backend_image": "synthetic-backend-baseline", "frontend_image": "synthetic-frontend-baseline",
    "schema_revision": BEFORE, "sha256": "a" * 64,
}
CRON_CONFIG = {
    "cron_expression": "30 8 * * 1-5", "timezone": "Europe/London",
    "start_at": "2026-10-05T07:00:00Z", "end_at": "2027-01-01T00:00:00Z",
    "natural_text": "Synthetic weekday schedule", "summary": "Synthetic compiled schedule",
}


async def connection(name):
    return await asyncpg.connect(schema.scratch_url(name).replace("+asyncpg", ""), timeout=10)


@pytest_asyncio.fixture
async def scratch(tmp_path):
    assert tmp_path.is_relative_to(Path("/results")), "Use retained /results pytest --basetemp"
    name = "iacs_validation_schema_" + uuid.uuid4().hex[:12] + "_retirement"
    schema.scratch_url(name)
    admin = await asyncpg.connect(os.environ["IACS_DATABASE_URL"].replace("+asyncpg", ""), timeout=10)
    owned = False
    try:
        assert await admin.fetchval("SELECT current_database()") == BASE_NAME
        await admin.execute('CREATE DATABASE "' + name + '" TEMPLATE template0')
        owned = True
        yield SimpleNamespace(name=name, output=tmp_path)
    finally:
        try:
            if owned:
                await admin.execute('DROP DATABASE "' + name + '"')
                (tmp_path / "cleanup.json").write_text(json.dumps({"scratch_database_dropped": name}))
        finally:
            await admin.close()


async def rows(client, table):
    return [json.loads(row["document"]) for row in await client.fetch(
        f"SELECT to_jsonb(t)::text AS document FROM {table} t ORDER BY id")]


async def snapshot(name):
    client = await connection(name)
    try:
        return {table: await rows(client, table) for table in (
            *CORE_TABLES, "audit_logs", "notification_rules", "automation_rules",
            "notification_runs", "automation_runs",
        )} | {
            "system_settings": [json.loads(r["document"]) for r in await client.fetch(
                "SELECT to_jsonb(t)::text AS document FROM system_settings t ORDER BY key")],
        }
    finally:
        await client.close()


async def metadata_shapes(name):
    client = await connection(name)
    try:
        return {str(row["id"]): {
            "sql_null": row["sql_null"], "json_kind": row["json_kind"], "encoded": row["encoded"],
        } for row in await client.fetch("""SELECT id, source_metadata IS NULL AS sql_null,
            jsonb_typeof(source_metadata) AS json_kind, source_metadata::text AS encoded
            FROM visitor_passes ORDER BY id""")}
    finally:
        await client.close()


def by_id(records, name):
    return next(row for row in records if row["id"] == str(IDS[name]))


async def seed(name):
    client = await connection(name)
    try:
        await client.execute("""INSERT INTO access_events
            (id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification)
            VALUES ($1,'SYNTHETIC1','ENTRY','GRANTED',0.99,'synthetic',now(),'NORMAL')""", IDS["access"])
        await client.execute("""INSERT INTO visitor_passes
            (id,visitor_name,pass_type,expected_time,window_minutes,status,creation_source,
             number_plate,arrival_event_id,source_metadata)
            VALUES ($1,'Synthetic retained visitor','ONE_TIME',now(),30,'USED','ui',
                    'SYNTHETIC1',$2,'{"history":"synthetic retained pass history","calendar":{"id":"synthetic-calendar"},"whatsapp_chat_history":[{"text":"synthetic old reply"}],"whatsapp_pending_time_change":{"requested_time":"synthetic"}}'::jsonb)""", IDS["pass"], IDS["access"])
        for key, metadata in (
            ("pass_sql_null", None), ("pass_json_null", "null"),
            ("pass_string", json.dumps("Synthetic retained scalar metadata")),
            ("pass_array", json.dumps(["synthetic", {"whatsapp_nested_key": "retained array element"}])),
        ):
            await client.execute("""INSERT INTO visitor_passes
                (id,visitor_name,pass_type,expected_time,window_minutes,status,creation_source,source_metadata)
                VALUES ($1,'Synthetic metadata variant','ONE_TIME',now(),30,'USED','ui',$2::jsonb)""",
                IDS[key], metadata)
        await client.execute("""INSERT INTO audit_logs
            (id,category,action,actor,outcome,level,metadata)
            VALUES ($1,'system','deployment.backup','synthetic-operator','success','info',$2::jsonb)""",
            IDS["backup"], json.dumps(BACKUP_METADATA))
        await client.execute("INSERT INTO chat_sessions (id,title) VALUES ($1,'Synthetic retired conversation')", IDS["chat"])
        await client.execute("""INSERT INTO chat_messages (id,session_id,role,content)
            VALUES ($1,$2,'user','Synthetic retired history')""", IDS["chat_message"], IDS["chat"])
        await client.execute("""INSERT INTO messaging_identities
            (id,provider,provider_user_id,provider_display_name)
            VALUES ($1,'discord','synthetic-id','Synthetic retired identity')""", IDS["identity"])
        await client.execute("""INSERT INTO processed_messaging_messages
            (id,provider,provider_message_id,provider_channel_id,author_provider_id,received_at)
            VALUES ($1,'whatsapp','synthetic-message','synthetic-channel','synthetic-author',now())""", IDS["message"])
        for key in ("alfred_reflection_enabled", "discord_bot_token", "whatsapp_enabled", "llm_provider", "site_timezone"):
            await client.execute("""INSERT INTO system_settings (key,category,value,is_secret)
                VALUES ($1,'synthetic',$2::jsonb,false)""", key, json.dumps({"value": "synthetic"}))

        for key, conditions, actions in (
            ("notification_mixed", [{"type": "equals", "value": "@AlfredPhrase"}, {"type": "equals", "value": "@VisitorPassRequestedTime"}, {"type": "equals", "value": "retained"}],
             [{"type": "in_app"}, {"type": "in_app", "message_template": "@VisitorPassCurrentWindow"}, {"type": "discord"}]),
            ("notification_empty", [], [{"type": "whatsapp"}]),
            ("notification_orphan", [], [{"type": "in_app"}]),
            ("notification_safe", [], [{"type": "in_app"}]),
        ):
            await client.execute("""INSERT INTO notification_rules
                (id,name,trigger_event,conditions,actions,is_active)
                VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,true)""",
                IDS[key], key, "agent_anomaly_alert" if key == "notification_orphan" else "authorized_entry",
                json.dumps(conditions), json.dumps(actions))

        ordinary = {"id": "ordinary", "type": "time.every_x", "config": {"interval": 5, "unit": "minutes"}}
        gate = {"id": "gate", "type": "gate.open", "config": {}}
        for key, triggers, conditions, actions, active in (
            ("automation_mixed", [ordinary, {"type": "ai.phrase_received", "config": {}}],
             [{"type": "compare", "value": "@AlfredIssue"}], [gate, {"type": "integration.whatsapp.send_message"}], True),
            ("automation_empty", [ordinary], [], [{"type": "integration.whatsapp.send_message"}], True),
            ("cron_active", [{"id": "clock", "type": "time.ai_text", "config": CRON_CONFIG}], [], [gate], True),
            ("cron_inactive", [{"id": "clock", "type": "time.ai_text", "config": CRON_CONFIG}], [], [gate], False),
            ("cron_invalid_mixed", [ordinary, {"type": "time.ai_text", "config": {"cron_expression": "invalid"}}], [], [gate], True),
            ("cron_invalid_empty", [{"type": "time.ai_text", "config": {"natural_text": "uncompiled"}}], [], [gate], True),
            ("automation_safe", [ordinary], [], [gate], True),
        ):
            await client.execute("""INSERT INTO automation_rules
                (id,name,is_active,triggers,trigger_keys,conditions,actions,run_count,next_run_at)
                VALUES ($1,$2,$3,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,3,'2026-10-06T07:30:00Z')""",
                IDS[key], key, active, json.dumps(triggers), json.dumps([t["type"] for t in triggers]),
                json.dumps(conditions), json.dumps(actions))

        for key, rule_key, state, completed in (
            ("notification_pending", "notification_mixed", "queued", False),
            ("notification_claimed", "notification_empty", "processing", False),
            ("notification_history", "notification_empty", "sent", True),
            ("notification_unaffected", "notification_safe", "queued", False),
        ):
            # The rule snapshot deliberately contains only a surviving in-app action:
            # affected saved-rule identity, not merely a retired channel, must fence it.
            plan = [
                {"rule": {"id": str(IDS[rule_key])}, "action": {"id": "accepted", "type": "in_app"},
                 "state": "accepted", "details": {"delivered_count": 1, "operation_id": "synthetic-accepted"}},
                {"rule": {"id": str(IDS[rule_key])}, "action": {"id": "pending", "type": "in_app"}, "state": "pending"},
            ]
            await client.execute("""INSERT INTO notification_runs
                (id,trigger_event,subject,severity,status,context,delivered_count,failed_count,skipped_count,
                 failures,skipped_reasons,queued_at,recovery_version,delivery_plan,claim_token,lease_expires_at,finished_at)
                VALUES ($1,'authorized_entry','Synthetic','info',$2,'{}',1,0,0,'[]','[]',now(),1,$3::jsonb,
                    $4,CASE WHEN $4::uuid IS NOT NULL THEN now()+interval '1 minute' END,
                    CASE WHEN $5 THEN now() END)""", IDS[key], state, json.dumps(plan),
                uuid.UUID(int=20001) if state == "processing" else None, completed)
        for key, rule_key, trigger, state, completed in (
            ("automation_claimed", "automation_mixed", "time.every_x", "processing", False),
            ("automation_deleted", "automation_empty", "time.every_x", "queued", False),
            ("automation_ai_pending", "cron_active", "time.ai_text", "queued", False),
            ("automation_history", "automation_empty", "time.every_x", "success", True),
            ("automation_unaffected", "automation_safe", "time.every_x", "queued", False),
        ):
            plan = [{"operation_id": "synthetic-completed", "state": "succeeded", "result": {"accepted": True}},
                    {"operation_id": "synthetic-pending", "state": "pending"}]
            await client.execute("""INSERT INTO automation_runs
                (id,rule_id,trigger_key,status,started_at,trigger_payload,context,condition_results,action_results,
                 actor,source,recovery_version,queued_at,action_plan,claim_token,lease_expires_at,finished_at)
                VALUES ($1,$2,$3,$4,now(),'{}','{}','[]','[{"status":"success"}]','System','synthetic',
                    1,now(),$5::jsonb,$6,CASE WHEN $6::uuid IS NOT NULL THEN now()+interval '1 minute' END,
                    CASE WHEN $7 THEN now() END)""", IDS[key], IDS[rule_key], trigger, state, json.dumps(plan),
                uuid.UUID(int=20002) if state == "processing" else None, completed)
    finally:
        await client.close()


def upgrade_with_provider_guard(item):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--upgrade-worker"],
        cwd=schema.ROOT / "backend", env=dict(os.environ, IACS_DATABASE_URL=schema.scratch_url(item.name),
        PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=120, check=False)
    log = schema.safe_error(result.stdout + result.stderr)
    (item.output / "retirement-upgrade.log").write_text(log)
    assert result.returncode == 0, log


def upgrade_worker():
    """Run only Alembic; reject every socket outside the synthetic PostgreSQL port."""
    from alembic import command
    from alembic.config import Config

    original_connect = socket.socket.connect
    forbidden = []

    def guarded_connect(sock, address):
        if not (isinstance(address, tuple) and address[:2] == ("127.0.0.1", 5432)):
            forbidden.append("non_database_socket")
            raise AssertionError("Retirement migration attempted a provider/non-database socket")
        return original_connect(sock, address)

    socket.socket.connect = guarded_connect
    try:
        command.upgrade(Config(str(schema.ROOT / "backend/alembic.ini")), REVISION)
        assert not forbidden, "A caught provider attempt also fails retirement validation"
    finally:
        socket.socket.connect = original_connect
    print("PASS: retirement migrated with provider sockets forbidden")


@pytest.mark.asyncio
async def test_populated_retirement_preserves_history_and_fences_obsolete_work(scratch):
    item = scratch
    assert schema.migrate(item.name, "upgrade", BEFORE, item.output, "prior-upgrade")
    await seed(item.name)
    before = await snapshot(item.name)
    old_metadata = await metadata_shapes(item.name)
    upgrade_with_provider_guard(item)
    after = await snapshot(item.name)
    client = await connection(item.name)
    try:
        assert await client.fetchval("SELECT version_num FROM alembic_version") == REVISION
        tables = set(await client.fetchval("SELECT array_agg(tablename) FROM pg_tables WHERE schemaname='public'"))
        assert not RETIRED_TABLES.intersection(tables)
        assert not await client.fetchval("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
    finally:
        await client.close()
    assert after["access_events"] == before["access_events"]
    expected_pass = dict(by_id(before["visitor_passes"], "pass"))
    expected_pass["source_metadata"] = {key: value for key, value in expected_pass["source_metadata"].items()
                                        if not key.startswith("whatsapp_")}
    expected_passes = [expected_pass if row["id"] == str(IDS["pass"]) else row
                       for row in before["visitor_passes"]]
    assert after["visitor_passes"] == expected_passes
    new_metadata = await metadata_shapes(item.name)
    assert old_metadata[str(IDS["pass_sql_null"])] == {"sql_null": True, "json_kind": None, "encoded": None}
    assert old_metadata[str(IDS["pass_json_null"])] == {"sql_null": False, "json_kind": "null", "encoded": "null"}
    assert old_metadata[str(IDS["pass_string"])]["json_kind"] == "string"
    assert old_metadata[str(IDS["pass_array"])]["json_kind"] == "array"
    for key in ("pass_sql_null", "pass_json_null", "pass_string", "pass_array"):
        assert new_metadata[str(IDS[key])] == old_metadata[str(IDS[key])], key
    backup = by_id(after["audit_logs"], "backup")
    assert backup == by_id(before["audit_logs"], "backup") and backup["metadata"] == BACKUP_METADATA
    old_settings = {x["key"]: x for x in before["system_settings"]}
    new_settings = {x["key"]: x for x in after["system_settings"]}
    assert not any(key.startswith(("alfred_", "discord_", "whatsapp_")) for key in new_settings)
    for key in ("llm_provider", "site_timezone"):
        assert new_settings[key] == old_settings[key]

    mixed = by_id(after["notification_rules"], "notification_mixed")
    assert mixed["is_active"] is False
    assert mixed["actions"] == [{"type": "in_app"}]
    assert mixed["conditions"] == [{"type": "equals", "value": "retained"}]
    assert not {str(IDS["notification_empty"]), str(IDS["notification_orphan"])}.intersection(
        {r["id"] for r in after["notification_rules"]})
    assert by_id(after["notification_rules"], "notification_safe") == by_id(before["notification_rules"], "notification_safe")
    auto_mixed = by_id(after["automation_rules"], "automation_mixed")
    assert not auto_mixed["is_active"] and auto_mixed["next_run_at"] is None
    assert auto_mixed["trigger_keys"] == ["time.every_x"] and auto_mixed["conditions"] == []
    assert [a["type"] for a in auto_mixed["actions"]] == ["gate.open"]
    remaining = {r["id"] for r in after["automation_rules"]}
    assert not {str(IDS["automation_empty"]), str(IDS["cron_invalid_empty"])}.intersection(remaining)
    invalid_mixed = by_id(after["automation_rules"], "cron_invalid_mixed")
    assert not invalid_mixed["is_active"] and invalid_mixed["next_run_at"] is None
    assert invalid_mixed["trigger_keys"] == ["time.every_x"]
    for name, enabled in (("cron_active", True), ("cron_inactive", False)):
        rule = by_id(after["automation_rules"], name)
        assert rule["is_active"] is enabled and rule["next_run_at"] == by_id(before["automation_rules"], name)["next_run_at"]
        assert rule["trigger_keys"] == ["time.cron"]
        assert rule["triggers"] == [{"id": "clock", "type": "time.cron", "config": {
            key: value for key, value in CRON_CONFIG.items() if key not in {"natural_text", "summary"}}}]

    for table, names in (
        ("notification_runs", ("notification_pending", "notification_claimed")),
        ("automation_runs", ("automation_claimed", "automation_deleted", "automation_ai_pending")),
    ):
        assert len(after[table]) == len(before[table]), "Retirement must retain every run"
        for name in names:
            run, old = by_id(after[table], name), by_id(before[table], name)
            assert run["status"] == "review_required" and run["review_reason"] == "assistant_messaging_retired"
            assert run["finished_at"] is not None and run["claim_token"] is None and run["lease_expires_at"] is None
            for column in (("delivery_plan", "delivered_count", "failed_count", "skipped_count", "failures", "skipped_reasons")
                           if table == "notification_runs" else ("action_plan", "action_results", "condition_results", "trigger_payload")):
                assert run[column] == old[column], (name, column)
    assert by_id(after["notification_runs"], "notification_history") == by_id(before["notification_runs"], "notification_history")
    history = by_id(after["automation_runs"], "automation_history")
    old_history = dict(by_id(before["automation_runs"], "automation_history"), rule_id=None)
    assert history == old_history
    for table, name in (("notification_runs", "notification_unaffected"), ("automation_runs", "automation_unaffected")):
        assert by_id(after[table], name) == by_id(before[table], name)

    retirement_audits = [r for r in after["audit_logs"] if r["action"] == "system.feature_retirement"]
    assert retirement_audits and all(r["metadata"] == {"reason": "assistant_messaging_retired"} for r in retirement_audits)
    for name in ("cron_active", "cron_inactive"):
        audit = next(r for r in retirement_audits if r["target_id"] == str(IDS[name]))
        assert audit["diff"]["schedule_converted"] is True and audit["diff"]["disabled"] is False
    # No schema downgrade pretends that dropped rows/settings can be recreated.
    assert not schema.migrate(item.name, "downgrade", BEFORE, item.output, "retirement-downgrade-refused")
    assert "matching database/files and image pair" in (item.output / "retirement-downgrade-refused.log").read_text()
    assert await snapshot(item.name) == after
    (item.output / "retirement-evidence.json").write_text(json.dumps({
        "revision": REVISION, "retired_tables_removed": sorted(RETIRED_TABLES),
        "history_retained": list(CORE_TABLES), "backup_manifest_retained": True,
        "nonobject_source_metadata_preserved": ["sql_null", "json_null", "string", "array"],
        "notification_runs_retained": len(after["notification_runs"]),
        "automation_runs_retained": len(after["automation_runs"]), "provider_sockets_forbidden": True,
        "schema_downgrade_refused_without_mutation": True, "unused_vector_extension_removed": True,
    }, indent=2, sort_keys=True))


@pytest.mark.asyncio
async def test_retirement_fences_captured_messaging_work_and_preserves_completed_equivalents(scratch):
    item = scratch
    assert schema.migrate(item.name, "upgrade", BEFORE, item.output, "captured-prior-upgrade")
    await seed(item.name)
    cases = (
        ("confirmed_origin", {"confirmed_delivery": {"authority": "alfred"}}, None, "in_app"),
        ("visitor_origin", {"visitor_origin": {"id": "synthetic"}}, None, "in_app"),
        ("conversation_origin", {"visitor_conversation_origin": {"id": "synthetic"}}, None, "in_app"),
        ("ephemeral_configuration", {"ephemeral_config": {"message": "synthetic"}}, None, "in_app"),
        ("retired_override", {}, [{"id": "synthetic-override", "actions": [{"type": "whatsapp"}]}], "in_app"),
        ("affected_rule_override", {}, [{"id": str(IDS["notification_mixed"]), "actions": [{"type": "in_app"}]}], "in_app"),
        ("retired_delivery_plan", {}, None, "discord"),
    )
    records = []
    client = await connection(item.name)
    try:
        for index, (name, context, override, pending_type) in enumerate(cases):
            for completed in (False, True):
                ident = uuid.UUID(int=30001 + index * 2 + int(completed))
                records.append((name, str(ident), completed))
                plan = [
                    {"rule": {"id": str(IDS["notification_safe"])},
                     "action": {"id": "accepted", "type": "in_app"}, "state": "accepted",
                     "details": {"delivered_count": 1, "operation_id": "synthetic-accepted"}},
                    {"rule": {"id": str(IDS["notification_safe"])},
                     "action": {"id": "pending", "type": pending_type}, "state": "pending"},
                ]
                await client.execute("""INSERT INTO notification_runs
                    (id,trigger_event,subject,severity,status,context,rules_override,delivered_count,
                     failed_count,skipped_count,failures,skipped_reasons,queued_at,recovery_version,
                     delivery_plan,finished_at)
                    VALUES ($1,'authorized_entry','Synthetic captured work','info',$2,$3::jsonb,$4::jsonb,
                        1,0,0,'[]','[]',now(),1,$5::jsonb,CASE WHEN $6 THEN now() END)""",
                    ident, "sent" if completed else "queued", json.dumps(context),
                    json.dumps(override) if override is not None else None, json.dumps(plan), completed)
    finally:
        await client.close()
    before = {row["id"]: row for row in (await snapshot(item.name))["notification_runs"]}
    upgrade_with_provider_guard(item)
    after = {row["id"]: row for row in (await snapshot(item.name))["notification_runs"]}
    mutable_fields = {"status", "review_reason", "error", "finished_at", "claim_token", "lease_expires_at", "updated_at"}
    for name, ident, completed in records:
        old, current = before[ident], after[ident]
        if completed:
            assert current == old, name
            continue
        assert current["status"] == "review_required", name
        assert current["review_reason"] == current["error"] == "assistant_messaging_retired", name
        assert current["finished_at"] is not None and current["claim_token"] is None and current["lease_expires_at"] is None, name
        assert {key: value for key, value in current.items() if key not in mutable_fields} == {
            key: value for key, value in old.items() if key not in mutable_fields
        }, name
    (item.output / "captured-retirement-evidence.json").write_text(json.dumps({
        "captured_cases_reviewed": [name for name, _, completed in records if not completed],
        "completed_equivalents_unchanged": len(cases), "provider_sockets_forbidden": True,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    if sys.argv[1:] != ["--upgrade-worker"]:
        raise SystemExit("Use the isolated pytest suite; only the guarded upgrade worker is directly executable")
    upgrade_worker()
