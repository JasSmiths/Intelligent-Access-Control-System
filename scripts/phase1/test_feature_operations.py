"""Feature mutation parity against PostgreSQL; only for the isolated harness."""

import os
from pathlib import Path
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, func

assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
assert "@127.0.0.1:5432/iacs_p1_" in os.environ.get("IACS_DATABASE_URL", "")
pytestmark = pytest.mark.asyncio

from app.ai.context import set_chat_tool_context
from app.ai.tool_groups import visitor_passes_handlers as alfred
from app.api.dependencies import current_user
from app.api.v1 import visitor_passes as api
from app.db.session import AsyncSessionLocal, engine
from app.models import User, VisitorPass, AuditLog
from app.models.enums import UserRole
from app.services import action_confirmations, visitor_passes as owner
from app.services.telemetry import actor_from_user


@pytest_asyncio.fixture(autouse=True)
async def isolated(monkeypatch):
    monkeypatch.setattr(action_confirmations, "emit_audit_log", lambda **kw: None)

    async def config():
        return SimpleNamespace(site_timezone="UTC")

    async def publish(*args, **kw):
        pass

    monkeypatch.setattr(alfred, "get_runtime_config", config)
    monkeypatch.setattr(owner.event_bus, "publish", publish)
    yield
    await engine.dispose()


async def user(role=UserRole.ADMIN, active=True):
    row = User(
        username=uuid.uuid4().hex,
        full_name="Synthetic Operator",
        password_hash="unused",
        role=role,
        is_active=active,
    )
    async with AsyncSessionLocal() as s:
        s.add(row)
        await s.commit()
    return row


async def call(channel, action, actor, data=None, row_id=None, confirmed=True):
    data = dict(data or {})
    if channel == "alfred":
        token = set_chat_tool_context({"user_id": str(actor.id), "user_role": "admin"})
        try:
            if row_id:
                data["pass_id"] = str(row_id)
            return await getattr(alfred, action + "_visitor_pass")({**data, "confirm": confirmed})
        finally:
            set_chat_tool_context({}, token=token)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/visitor-passes")
    app.dependency_overrides[current_user] = lambda: actor
    if confirmed:
        payload = {k: v for k, v in data.items() if v is not None}
        if row_id:
            payload["pass_id"] = str(row_id)
        async with AsyncSessionLocal() as s:
            c = await action_confirmations.create_action_confirmation(
                s, user=actor, action="visitor_pass." + action, payload=payload
            )
        data["confirmation_token"] = c["confirmation_token"]
    url = (
        "/api/v1/visitor-passes"
        + (f"/{row_id}" if row_id else "")
        + ("/cancel" if action == "cancel" else "")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        response = await client.request("PATCH" if action == "update" else "POST", url, json=data)
    if response.status_code >= 400:
        return {"error": response.json()["detail"]}
    return {"visitor_pass": response.json()}


def values():
    return {
        "visitor_name": " Synthetic Visitor ",
        "expected_time": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        "window_minutes": 30,
        "visitor_phone": "447700900123",
    }


async def audits(actor):
    async with AsyncSessionLocal() as s:
        return (
            await s.scalars(
                select(AuditLog)
                .where(AuditLog.actor_user_id == actor.id, AuditLog.action.like("visitor_pass.%"))
                .order_by(AuditLog.timestamp)
            )
        ).all()


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_pass_crud_shared_fields_actor_and_phone_clear(channel):
    actor = await user()
    data = values()
    created = await call(channel, "create", actor, data)
    row = created["visitor_pass"]
    assert row["visitor_name"] == "Synthetic Visitor"
    updated = await call(channel, "update", actor, {"visitor_phone": ""}, row["id"])
    assert updated["visitor_pass"].get("visitor_phone") is None
    async with AsyncSessionLocal() as s:
        persisted = await s.get(VisitorPass, uuid.UUID(row["id"]))
        assert persisted.visitor_phone is None
    cancelled = await call(channel, "cancel", actor, {"reason": "synthetic test"}, row["id"])
    assert cancelled["visitor_pass"]["status"] == "cancelled"
    rows = await audits(actor)
    assert [r.action for r in rows] == [
        "visitor_pass.create",
        "visitor_pass.update",
        "visitor_pass.cancel",
    ]
    assert all(r.actor == actor_from_user(actor) for r in rows)
    assert rows[1].diff["old"]["visitor_phone"] == "447700900123"
    assert rows[1].diff["new"]["visitor_phone"] is None


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_pass_preview_has_no_mutation(channel):
    actor = await user()
    result = await call(channel, "create", actor, values(), confirmed=False)
    assert result.get("error") or result.get("requires_confirmation")
    assert await audits(actor) == []
    async with AsyncSessionLocal() as s:
        assert (
            await s.scalar(
                select(func.count())
                .select_from(VisitorPass)
                .where(VisitorPass.created_by_user_id == actor.id)
            )
            == 0
        )


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_pass_audit_failure_rolls_back(channel, monkeypatch):
    actor = await user()

    async def fail(*args, **kw):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(owner, "write_audit_log", fail)
    if channel == "alfred":
        with pytest.raises(RuntimeError):
            await call(channel, "create", actor, values())
    else:
        assert "error" in await call(channel, "create", actor, values())
    async with AsyncSessionLocal() as s:
        assert (
            await s.scalar(
                select(func.count())
                .select_from(VisitorPass)
                .where(VisitorPass.created_by_user_id == actor.id)
            )
            == 0
        )


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_pass_realtime_failure_keeps_committed_success(channel, monkeypatch):
    actor = await user()

    async def fail(*args, **kw):
        raise RuntimeError("synthetic realtime failure")

    monkeypatch.setattr(owner.event_bus, "publish", fail)
    result = await call(channel, "create", actor, values())
    assert "visitor_pass" in result
    assert len(await audits(actor)) == 1


@pytest.mark.parametrize("role,active", [(UserRole.STANDARD, True), (UserRole.ADMIN, False)])
async def test_alfred_pass_uses_real_actor_not_claimed_role(role, active):
    actor = await user(role, active)
    result = await call("alfred", "create", actor, values())
    assert "error" in result
    assert await audits(actor) == []


from app.ai.tool_groups import notifications_handlers as notification_alfred
from app.api.v1 import notifications as notification_api
from app.models import NotificationRule
from app.services import notification_rules


def notification_values():
    return {
        "name": " Synthetic Workflow ",
        "trigger_event": "authorized_entry",
        "conditions": [],
        "actions": [
            {"type": "in_app", "title_template": "@Subject", "message_template": "@Message"}
        ],
        "is_active": False,
    }


async def notification_call(channel, action, actor, data=None, row_id=None, confirmed=True):
    data = dict(data or {})
    if channel == "alfred":
        token = set_chat_tool_context({"user_id": str(actor.id), "user_role": "admin"})
        try:
            if row_id:
                data["rule_id"] = str(row_id)
            return await getattr(notification_alfred, action + "_notification_workflow")(
                {**data, "confirm": confirmed}
            )
        finally:
            set_chat_tool_context({}, token=token)
    app = FastAPI()
    app.include_router(notification_api.router, prefix="/api/v1/notifications")
    app.dependency_overrides[current_user] = lambda: actor
    if confirmed:
        payload = {"rule_id": str(row_id)} if action == "delete" else data
        async with AsyncSessionLocal() as s:
            c = await action_confirmations.create_action_confirmation(
                s, user=actor, action="notification_rule." + action, payload=payload
            )
        data["confirmation_token"] = c["confirmation_token"]
    url = "/api/v1/notifications/rules" + (f"/{row_id}" if row_id else "")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        response = await client.request(
            {"create": "POST", "update": "PATCH", "delete": "DELETE"}[action], url, json=data
        )
    if response.status_code >= 400:
        return {"error": response.json()["detail"]}
    return {"workflow": response.json() if response.content else None}


@pytest_asyncio.fixture(autouse=True)
async def notification_preview(monkeypatch):
    async def preview(*args, **kw):
        return {"synthetic": True}

    monkeypatch.setattr(
        notification_alfred,
        "get_notification_service",
        lambda: SimpleNamespace(preview_rule=preview),
    )


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_notification_mutation_audits_and_fields(channel):
    actor = await user()
    created = await notification_call(channel, "create", actor, notification_values())
    row = created["workflow"]
    assert row["name"] == "Synthetic Workflow"
    assert row["is_active"] is False
    updated = await notification_call(channel, "update", actor, {"name": "Renamed"}, row["id"])
    assert updated["workflow"]["name"] == "Renamed"
    assert updated["workflow"]["actions"] == row["actions"]
    await notification_call(channel, "delete", actor, row_id=row["id"])
    async with AsyncSessionLocal() as s:
        assert await s.get(NotificationRule, uuid.UUID(row["id"])) is None
        rows = (
            await s.scalars(
                select(AuditLog)
                .where(
                    AuditLog.actor_user_id == actor.id, AuditLog.action.like("notification_rule.%")
                )
                .order_by(AuditLog.timestamp)
            )
        ).all()
    assert [x.action for x in rows] == [
        "notification_rule.create",
        "notification_rule.update",
        "notification_rule.delete",
    ]
    assert all(x.actor == actor_from_user(actor) and x.metadata_["source"] == channel for x in rows)


@pytest.mark.parametrize("channel", ["api", "alfred"])
@pytest.mark.parametrize("changes", [{"name": "   "}, {"name": "x" * 161}, {"actions": []}])
async def test_notification_invalid_update_preserves_row(channel, changes):
    actor = await user()
    row = (await notification_call(channel, "create", actor, notification_values()))["workflow"]
    result = await notification_call(channel, "update", actor, changes, row["id"])
    assert "error" in result
    async with AsyncSessionLocal() as s:
        persisted = await s.get(NotificationRule, uuid.UUID(row["id"]))
        assert persisted.name == row["name"]
        assert persisted.actions == row["actions"]


@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_notification_audit_failure_is_atomic(action, monkeypatch):
    actor = await user()
    data = notification_values()
    async with AsyncSessionLocal() as s:
        row = await notification_rules.create_rule(s, data, user=actor, source="api")

    async def fail(*args, **kw):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(notification_rules, "write_audit_log", fail)
    async with AsyncSessionLocal() as s:
        with pytest.raises(RuntimeError):
            if action == "create":
                await notification_rules.create_rule(
                    s, {**data, "name": "No commit"}, user=actor, source="api"
                )
            elif action == "update":
                await notification_rules.update_rule(
                    s, row.id, {"name": "No commit"}, user=actor, source="api"
                )
            else:
                await notification_rules.delete_rule(s, row.id, user=actor, source="api")
    async with AsyncSessionLocal() as s:
        persisted = await s.get(NotificationRule, row.id)
        assert persisted.name == row.name
        assert (
            await s.scalar(
                select(func.count())
                .select_from(NotificationRule)
                .where(NotificationRule.name == "No commit")
            )
            == 0
        )


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_notification_confirmation_prevents_write(channel):
    actor = await user()
    result = await notification_call(
        channel, "create", actor, notification_values(), confirmed=False
    )
    assert result.get("error") or result.get("requires_confirmation")
    async with AsyncSessionLocal() as s:
        assert (
            await s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.actor_user_id == actor.id,
                    AuditLog.action == "notification_rule.create",
                )
            )
            == 0
        )


async def test_notification_real_actor_required():
    actor = await user(UserRole.STANDARD)
    result = await notification_call("alfred", "create", actor, notification_values())
    assert result["error_code"] == "forbidden"


async def test_notification_preview_failure_does_not_fail_mutation(monkeypatch):
    async def fail(*args, **kw):
        raise RuntimeError("preview unavailable")

    monkeypatch.setattr(
        notification_alfred, "get_notification_service", lambda: SimpleNamespace(preview_rule=fail)
    )
    actor = await user()
    result = await notification_call("alfred", "create", actor, notification_values())
    assert result["created"] is True
    assert result["warnings"]


async def test_notification_concurrent_partial_updates():
    import asyncio

    actor = await user()
    async with AsyncSessionLocal() as s:
        row = await notification_rules.create_rule(
            s, notification_values(), user=actor, source="api"
        )

    async def edit(changes):
        async with AsyncSessionLocal() as s:
            await notification_rules.update_rule(s, row.id, changes, user=actor, source="api")

    await asyncio.gather(edit({"name": "Both survive"}), edit({"is_active": True}))
    async with AsyncSessionLocal() as s:
        persisted = await s.get(NotificationRule, row.id)
        assert persisted.name == "Both survive"
        assert persisted.is_active is True


from app.ai.tool_groups import automations_handlers as automation_alfred
from app.api.v1 import automations as automation_api
from app.models import AutomationRule
from app.services import automations as automation_owner


def automation_values():
    return {
        "name": " Synthetic Automation ",
        "description": "original",
        "triggers": [
            {"type": "webhook.received", "config": {"webhook_key": "synthetic-" + uuid.uuid4().hex}}
        ],
        "conditions": [],
        "actions": [{"type": "maintenance_mode.disable", "config": {}}],
        "is_active": False,
    }


@pytest_asyncio.fixture(autouse=True)
async def automation_preview(monkeypatch):
    async def preview(*args, **kw):
        return {"synthetic": True}

    monkeypatch.setattr(automation_owner.AutomationService, "dry_run_rule", preview)


async def automation_call(channel, action, actor, data=None, row_id=None, confirmed=True):
    data = dict(data or {})
    if channel == "alfred":
        token = set_chat_tool_context({"user_id": str(actor.id), "user_role": "admin"})
        try:
            if row_id:
                data["automation_id"] = str(row_id)
            return await getattr(
                automation_alfred, ("edit" if action == "update" else action) + "_automation"
            )({**data, "confirm": confirmed})
        finally:
            set_chat_tool_context({}, token=token)
    app = FastAPI()
    app.include_router(automation_api.router, prefix="/api/v1/automations")
    app.dependency_overrides[current_user] = lambda: actor
    if confirmed:
        payload = {"rule_id": str(row_id)} if action == "delete" else data
        async with AsyncSessionLocal() as s:
            c = await action_confirmations.create_action_confirmation(
                s, user=actor, action="automation_rule." + action, payload=payload
            )
        data["confirmation_token"] = c["confirmation_token"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        response = await client.request(
            {"create": "POST", "update": "PATCH", "delete": "DELETE"}[action],
            "/api/v1/automations/rules" + (f"/{row_id}" if row_id else ""),
            json=data,
        )
    if response.status_code >= 400:
        return {"error": response.json()["detail"]}
    return {"automation": response.json() if response.content else None}


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_automation_crud_audit_and_action_only_hardening(channel):
    actor = await user()
    created = await automation_call(channel, "create", actor, automation_values())
    row = created["automation"]
    assert row["name"] == "Synthetic Automation"
    assert row["triggers"][0]["config"]["require_hmac"] is False
    updated = await automation_call(
        channel, "update", actor, {"actions": [{"type": "gate.open", "config": {}}]}, row["id"]
    )
    assert updated["automation"]["triggers"][0]["config"]["require_hmac"] is True
    async with AsyncSessionLocal() as s:
        persisted = await s.get(AutomationRule, uuid.UUID(row["id"]))
        assert persisted.triggers[0]["config"]["require_hmac"] is True
    await automation_call(channel, "delete", actor, row_id=row["id"])
    async with AsyncSessionLocal() as s:
        assert await s.get(AutomationRule, uuid.UUID(row["id"])) is None
        rows = (
            await s.scalars(
                select(AuditLog)
                .where(
                    AuditLog.actor_user_id == actor.id, AuditLog.action.like("automation_rule.%")
                )
                .order_by(AuditLog.timestamp)
            )
        ).all()
    assert [x.action for x in rows] == [
        "automation_rule.create",
        "automation_rule.update",
        "automation_rule.delete",
    ]
    assert all(x.actor == actor_from_user(actor) for x in rows)


@pytest.mark.parametrize("channel", ["api", "alfred"])
@pytest.mark.parametrize(
    "changes", [{"name": "   "}, {"name": "x" * 161}, {"actions": []}, {"triggers": []}]
)
async def test_automation_invalid_updates_roll_back(channel, changes):
    actor = await user()
    row = (await automation_call(channel, "create", actor, automation_values()))["automation"]
    result = await automation_call(channel, "update", actor, changes, row["id"])
    assert "error" in result
    async with AsyncSessionLocal() as s:
        persisted = await s.get(AutomationRule, uuid.UUID(row["id"]))
        assert persisted.name == row["name"]
        assert persisted.actions == row["actions"]


@pytest.mark.parametrize("action", ["create", "update", "delete"])
async def test_automation_audit_failure_rolls_back(action, monkeypatch):
    actor = await user()
    values_ = automation_values()
    service = automation_owner.AutomationService()
    async with AsyncSessionLocal() as s:
        row = await service.create_rule(s, **values_, created_by=actor)
        await s.commit()

    async def fail(*args, **kw):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(automation_owner, "write_audit_log", fail)
    with pytest.raises(RuntimeError):
        async with AsyncSessionLocal() as s:
            if action == "create":
                await service.create_rule(s, **{**values_, "name": "No commit"}, created_by=actor)
            else:
                current = await s.get(AutomationRule, row.id)
                if action == "update":
                    await service.update_rule(s, current, actor=actor, name="No commit")
                else:
                    await service.delete_rule(s, current, actor=actor)
            await s.commit()
    async with AsyncSessionLocal() as s:
        persisted = await s.get(AutomationRule, row.id)
        assert persisted.name == row.name
        assert (
            await s.scalar(
                select(func.count())
                .select_from(AutomationRule)
                .where(AutomationRule.name == "No commit")
            )
            == 0
        )


@pytest.mark.parametrize("channel", ["api", "alfred"])
async def test_automation_confirmation_prevents_mutation(channel):
    actor = await user()
    result = await automation_call(channel, "create", actor, automation_values(), confirmed=False)
    assert result.get("error") or result.get("requires_confirmation")
    async with AsyncSessionLocal() as s:
        assert (
            await s.scalar(
                select(func.count())
                .select_from(AutomationRule)
                .where(AutomationRule.created_by_user_id == actor.id)
            )
            == 0
        )


async def test_automation_requires_real_admin():
    actor = await user(UserRole.STANDARD)
    result = await automation_call("alfred", "create", actor, automation_values())
    assert "error" in result
