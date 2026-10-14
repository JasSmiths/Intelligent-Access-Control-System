"""Populated forward recipient migration; old runtime/provider modules not used."""
from test_feature_retirement import scratch as scratch, connection, preflight
import test_schema_contract as schema

BASE_NAME = preflight()

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.notification_runs import NotificationRunStore
from app.services.notifications import NotificationService

pytestmark = pytest.mark.asyncio
BEFORE, REVISION = "20261014_0016", "20261014_0017"


def identity(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-recipient-upgrade:" + name)


def action(target="apprise:1", *, mode="selected"):
    return {"id": "mobile", "type": "mobile", "target_mode": mode,
        "target_ids": [target] if target else [], "title": "Synthetic", "message": "Synthetic"}


async def test_populated_upgrade_holds_only_ambiguous_work_and_preserves_receipts(scratch):
    assert schema.migrate(scratch.name, "upgrade", BEFORE, scratch.output, "before")
    client = await connection(scratch.name)
    definitions = {
        "selected": action(), "inactive": action(), "all": action("", mode="all"),
        "ha": action("home_assistant_mobile:notify.mobile_app_synthetic"),
        "visibility": {**action("", mode="all"), "variable_recipients": {"message_template": [{"occurrence": 0, "name": "FirstName", "target_ids": ["apprise:1"]}]}},
        "voice_empty": {"id": "voice", "type": "voice", "target_mode": "selected", "target_ids": []},
    }
    accepted = {"rule": {"id": str(identity("selected")), "name": "Selected"}, "action": action(),
        "state": "accepted", "reason": "provider_accepted", "accepted_any": True,
        "destination_outcomes": [{"target": "apprise", "delivery": "accepted"}]}
    pending = {"rule": accepted["rule"], "action": action(), "state": "pending"}
    unknown = {**accepted, "state": "unknown", "reason": "delivery_outcome_unknown",
        "destination_outcomes": [{"target": "apprise", "delivery": "unknown"}]}
    rows = {
        "mixed": ("processing", [accepted, pending], None, False, None),
        "completed": ("provider_accepted", [accepted], None, True, None),
        "uncertain": ("review_required", [unknown, pending], None, True, "delivery_outcome_unknown"),
        "safe": ("queued", [{"rule": accepted["rule"], "action": definitions["ha"], "state": "pending"}], None, False, None),
        "unplanned": ("queued", None, None, False, None),
        "override": ("queued", None, [{"actions": [action()]}], False, None),
        "frozen": ("queued", [{"rule": accepted["rule"], "action": {**action("", mode="all"), "frozen_apprise_indexes": [1]}, "state": "pending"}], None, False, None),
    }
    try:
        for name, config in definitions.items():
            await client.execute("""INSERT INTO notification_rules (id,name,trigger_event,conditions,actions,is_active)
                VALUES ($1,$2,'synthetic.legacy_recipient','[]'::jsonb,$3::jsonb,$4)""",
                identity(name), name, json.dumps([config]), name != "inactive")
        for name, (status, plan, override, finished, reason) in rows.items():
            await client.execute("""INSERT INTO notification_runs
                (id,trigger_event,subject,severity,status,context,delivered_count,failed_count,skipped_count,
                 failures,skipped_reasons,queued_at,recovery_version,delivery_plan,rules_override,finished_at,review_reason,claim_token,lease_expires_at)
                VALUES ($1,'synthetic.legacy_recipient','Synthetic','info',$2,'{}'::jsonb,$3,0,0,
                '[]'::jsonb,'[]'::jsonb,now(),1,$4::jsonb,$5::jsonb,$6,$7,$8,now()+interval '1 minute')""",
                identity(name), status, 1 if name in {"mixed", "completed"} else 0,
                json.dumps(plan) if plan is not None else None, json.dumps(override) if override is not None else None,
                datetime.now(UTC) if finished else None, reason, uuid.uuid4() if not finished else None)
        before = {name: dict(await client.fetchrow("SELECT * FROM notification_runs WHERE id=$1", identity(name))) for name in rows}
    finally:
        await client.close()
    assert schema.migrate(scratch.name, "upgrade", REVISION, scratch.output, "upgrade")
    client = await connection(scratch.name)
    try:
        active = {row["name"]: row["is_active"] for row in await client.fetch("SELECT name,is_active FROM notification_rules")}
        assert active == {"selected": False, "inactive": False, "all": True, "ha": True, "visibility": False, "voice_empty": False}
        after = {name: dict(await client.fetchrow("SELECT * FROM notification_runs WHERE id=$1", identity(name))) for name in rows}
        for name in ("mixed", "unplanned", "override", "frozen"):
            assert after[name]["status"] == "review_required" and after[name]["review_reason"] == "recipient_binding_required"
            assert after[name]["claim_token"] is None and after[name]["lease_expires_at"] is None
        for name in rows:
            assert after[name]["delivery_plan"] == before[name]["delivery_plan"]
            assert after[name]["rules_override"] == before[name]["rules_override"]
            assert after[name]["delivered_count"] == before[name]["delivered_count"]
        for name in ("completed", "uncertain", "safe"):
            assert after[name] == before[name]
        assert await client.fetchval("SELECT count(*) FROM audit_logs WHERE action='system.recipient_binding_hold'") == 8
        (scratch.output / "upgrade-evidence.json").write_text(json.dumps({"rules": active,
            "run_status": {name: row["status"] for name, row in after.items()}, "plans_receipts_preserved": True}, indent=2))
    finally:
        await client.close()
    # A fresh runtime's real store/dispatcher cannot reclaim held work.
    test_engine = create_async_engine(schema.scratch_url(scratch.name))
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    service = NotificationService(run_store=NotificationRunStore(session_factory=factory))
    try:
        for name in ("mixed", "unplanned", "override", "frozen", "uncertain"):
            assert not await service.dispatcher.run_once(identity(name))
    finally:
        await test_engine.dispose()
    assert not schema.migrate(scratch.name, "downgrade", BEFORE, scratch.output, "downgrade-refused")
