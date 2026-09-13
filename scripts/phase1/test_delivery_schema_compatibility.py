"""0007 compatibility proofs in separately owned scratch PostgreSQL databases.

No application imports occur before preflight. Run serially through the isolated
phase1 namespace; each test owns, migrates and drops its own database. The base
harness database receives only an identity read and CREATE/DROP DATABASE commands.
"""
from __future__ import annotations

import asyncio
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
        raise RuntimeError("Delivery schema tests require explicit synthetic-only intent")
    base = schema.isolated_preflight()
    for key, value in os.environ.items():
        if key.startswith("IACS_") and any(part in key for part in ("TOKEN", "PASSWORD", "API_KEY")) and value:
            raise RuntimeError("Delivery schema tests refuse integration credential environment")
    return base


BASE_NAME = preflight()

# Third-party imports follow the same fail-closed guard as application workers.
import asyncpg
import pytest
import pytest_asyncio

BEFORE = "20260912_0006"
REVISION = "20260912_0007"
GUARD = "Incoming or access delivery recovery records exist"
MESSAGE_ID = uuid.UUID("00000000-0000-0000-0000-000000000701")
COMMAND_ID = uuid.UUID("00000000-0000-0000-0000-000000000702")
TARGET_ID = uuid.UUID("00000000-0000-0000-0000-000000000703")


async def connection(name):
    return await asyncpg.connect(schema.scratch_url(name).replace("+asyncpg", ""), timeout=10)


async def execute(name, statement, *values):
    client = await connection(name)
    try:
        return await client.execute(statement, *values)
    finally:
        await client.close()


async def current_rows(name):
    client = await connection(name)
    try:
        return {
            "revision": await client.fetchval("SELECT version_num FROM alembic_version"),
            "messages": [dict(row) for row in await client.fetch("SELECT * FROM processed_messaging_messages ORDER BY id")],
            "commands": [dict(row) for row in await client.fetch("SELECT * FROM access_device_command_records ORDER BY id")],
        }
    finally:
        await client.close()


@pytest_asyncio.fixture
async def scratch(tmp_path):
    assert tmp_path.is_relative_to(Path("/results")), "Use a retained /results child as pytest --basetemp"
    name = "iacs_p1_schema_" + uuid.uuid4().hex[:12] + "_delivery"
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
                # No FORCE or unrelated termination. Every connection/subprocess
                # must already have closed; leaked ownership is a cleanup failure.
                await admin.execute('DROP DATABASE "' + name + '"')
                (tmp_path / "cleanup.json").write_text(json.dumps({"scratch_database_dropped": name}))
        finally:
            await admin.close()


def migrate(item, direction, revision, label, *, expected=True):
    actual = schema.migrate(item.name, direction, revision, item.output, label)
    assert actual is expected, (item.output / (label + ".log")).read_text()


async def captured(item, label):
    return await schema.capture_schema(asyncpg.connect, item.name, item.output, label)


async def insert_old_message(name):
    await execute(name, """INSERT INTO processed_messaging_messages
      (id,provider,provider_message_id,provider_channel_id,author_provider_id,received_at,created_at,updated_at)
      VALUES ($1,'synthetic','synthetic-historical-id','synthetic-channel','synthetic-author',now(),now(),now())""", MESSAGE_ID)


async def insert_old_command(name):
    await execute(name, """INSERT INTO access_device_command_records
      (id,target_device_id,device_key,action,intent_id,idempotency_key,state,binding_snapshot,binding_fingerprint)
      VALUES ($1,$2,'synthetic_garage','open','synthetic-historical-intent','synthetic-historical-command',
              'unknown','{}'::jsonb,repeat('a',64))""", COMMAND_ID, TARGET_ID)


@pytest.mark.asyncio
async def test_historical_null_rows_remain_inert_after_upgrade_and_duplicate_delivery(scratch):
    item = scratch
    migrate(item, "upgrade", BEFORE, "old-upgrade")
    await insert_old_message(item.name)
    await insert_old_command(item.name)
    old = await current_rows(item.name)
    migrate(item, "upgrade", REVISION, "delivery-upgrade")
    before = await current_rows(item.name)
    message, command = before["messages"][0], before["commands"][0]
    assert all(message[key] is None for key in (
        "recovery_version", "state", "envelope", "routing_context", "available_at", "batch_id", "claim_token",
        "lease_expires_at", "claimed_at", "handled_at", "reply_plan", "result", "review_reason"))
    assert command["origin_context"] is None and command["outcome_recorded_at"] is None
    assert all(message[key] == value for key, value in old["messages"][0].items())
    assert all(command[key] == value for key, value in old["commands"][0].items())
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--historical-worker"],
        cwd=schema.ROOT / "backend", env=dict(os.environ, IACS_DATABASE_URL=schema.scratch_url(item.name),
        PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=60, check=False)
    (item.output / "historical-worker.log").write_text(schema.safe_error(result.stdout + result.stderr))
    assert result.returncode == 0, schema.safe_error(result.stdout + result.stderr)
    assert await current_rows(item.name) == before
    # A schema-only downgrade is permitted when the new fields are still NULL.
    migrate(item, "downgrade", BEFORE, "historical-null-downgrade")
    assert await current_rows(item.name) == old


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["incoming_received", "incoming_handled", "garage_pending", "garage_recorded"])
async def test_populated_delivery_recovery_refuses_downgrade_without_partial_schema_or_data_loss(scratch, kind):
    item = scratch
    migrate(item, "upgrade", REVISION, "head-upgrade")
    if kind.startswith("incoming"):
        await insert_old_message(item.name)
        await execute(item.name, """UPDATE processed_messaging_messages SET recovery_version=1,state=$1,
            envelope='{"message":{"type":"text","text":"synthetic"}}'::jsonb,
            routing_context='{"kind":"denied"}'::jsonb,available_at=now() WHERE id=$2""",
            "received" if kind.endswith("received") else "handled", MESSAGE_ID)
    else:
        await insert_old_command(item.name)
        await execute(item.name, """UPDATE access_device_command_records
            SET origin_context='{"kind":"automatic_access_garage"}'::jsonb,
                outcome_recorded_at=CASE WHEN $1 THEN now() ELSE NULL END WHERE id=$2""",
            kind.endswith("recorded"), COMMAND_ID)
    before_schema, before_rows = await captured(item, "before-guard"), await current_rows(item.name)
    migrate(item, "downgrade", BEFORE, "populated-downgrade-refused", expected=False)
    assert GUARD in (item.output / "populated-downgrade-refused.log").read_text()
    after_schema = await captured(item, "after-guard")
    diff = schema.differences(before_schema, after_schema)
    (item.output / "guard-diff.json").write_text(json.dumps(diff, indent=2, sort_keys=True))
    assert not diff
    assert await current_rows(item.name) == before_rows
    assert before_rows["revision"] == REVISION


@pytest.mark.asyncio
async def test_no_data_downgrade_upgrade_has_exact_catalog_equivalence(scratch):
    item = scratch
    migrate(item, "upgrade", BEFORE, "old-upgrade")
    before = await captured(item, "direct-before")
    migrate(item, "upgrade", REVISION, "first-upgrade")
    upgraded = await captured(item, "first-upgraded")
    migrate(item, "downgrade", BEFORE, "empty-downgrade")
    downgraded = await captured(item, "downgraded")
    assert not schema.differences(before, downgraded)
    migrate(item, "upgrade", REVISION, "second-upgrade")
    restored = await captured(item, "restored")
    diff = schema.differences(upgraded, restored)
    (item.output / "roundtrip-diff.json").write_text(json.dumps(diff, indent=2, sort_keys=True))
    assert not diff
    rows = await current_rows(item.name)
    assert rows == {"revision": REVISION, "messages": [], "commands": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["version", "missing_envelope", "null_envelope", "array_envelope", "missing_routing", "missing_available", "bad_state", "null_origin_kind", "missing_origin_kind", "bad_origin_kind", "outcome_without_origin"])
async def test_delivery_constraints_reject_malformed_recovery_records(scratch, invalid):
    item = scratch
    migrate(item, "upgrade", REVISION, "head-upgrade")
    await insert_old_message(item.name)
    await insert_old_command(item.name)
    before = await current_rows(item.name)
    if invalid in {"null_origin_kind", "missing_origin_kind", "bad_origin_kind", "outcome_without_origin"}:
        if invalid == "outcome_without_origin":
            statement, args = "UPDATE access_device_command_records SET outcome_recorded_at=now() WHERE id=$1", (COMMAND_ID,)
            constraint = "ck_device_command_output_origin"
        else:
            payload = {} if invalid == "missing_origin_kind" else {"kind": None if invalid == "null_origin_kind" else "unsupported"}
            statement, args = "UPDATE access_device_command_records SET origin_context=$1::jsonb WHERE id=$2", (json.dumps(payload), COMMAND_ID)
            constraint = "ck_device_command_origin"
    else:
        assignments = {"recovery_version": "1", "state": "'received'", "envelope": "'{}'::jsonb",
            "routing_context": "'{}'::jsonb", "available_at": "now()"}
        field, value = {
            "version": ("recovery_version", "2"), "missing_envelope": ("envelope", "NULL"),
            "null_envelope": ("envelope", "'null'::jsonb"), "array_envelope": ("envelope", "'[]'::jsonb"),
            "missing_routing": ("routing_context", "NULL"), "missing_available": ("available_at", "NULL"),
            "bad_state": ("state", "'unsupported'"),
        }[invalid]
        assignments[field] = value
        statement = "UPDATE processed_messaging_messages SET " + ",".join(key + "=" + value for key, value in assignments.items()) + " WHERE id=$1"
        args, constraint = (MESSAGE_ID,), "ck_incoming_message_recovery"
    with pytest.raises(asyncpg.CheckViolationError) as failure:
        await execute(item.name, statement, *args)
    assert failure.value.constraint_name == constraint
    assert await current_rows(item.name) == before


async def historical_worker():
    """Only DB claim/projection owners are called; every output sink is forbidden."""
    from sqlalchemy import func, select
    from unittest.mock import AsyncMock
    from app.db.session import AsyncSessionLocal, engine
    from app.models import AuditLog, AutomationRun, NotificationRun
    from app.services.messaging.incoming_messages import IncomingMessageStore
    from app.services.access import delivery

    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        if not (isinstance(address, tuple) and address[:2] == ("127.0.0.1", 5432)):
            raise AssertionError("Historical compatibility worker attempted a non-PostgreSQL socket")
        return original_connect(sock, address)

    socket.socket.connect = guarded_connect
    forbidden = AsyncMock(side_effect=AssertionError("Historical NULL records produced a durable output"))
    delivery.write_audit_log = forbidden
    delivery.NotificationRunStore.enqueue_in_session = forbidden
    try:
        store = IncomingMessageStore()
        assert await store.claim() is None
        assert await store.claim(MESSAGE_ID) is None
        async with AsyncSessionLocal() as session:
            same = await store.accept_in_session(session, provider="synthetic", provider_message_id="synthetic-historical-id",
                provider_channel_id="synthetic-channel", author_provider_id="synthetic-author",
                envelope={"message": {"type": "text", "text": "synthetic duplicate"}},
                routing_context={"kind": "denied"}, received_at=None)
            assert same == MESSAGE_ID
            assert await delivery.reserve_garage_outcome_outputs(session, command_id=COMMAND_ID) is False
            await session.commit()
        assert await delivery.recover_garage_outcome_outputs() == 0
        async with AsyncSessionLocal() as session:
            for model in (AuditLog, AutomationRun, NotificationRun):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
        forbidden.assert_not_awaited()
    finally:
        await engine.dispose()
        socket.socket.connect = original_connect
    print("PASS: historical inbox duplicate/claim and unattributed garage recovery remained inert")


if __name__ == "__main__":
    if sys.argv[1:] != ["--historical-worker"]:
        raise SystemExit("Use isolated pytest invocation; this script only exposes its bounded historical worker")
    asyncio.run(historical_worker())
