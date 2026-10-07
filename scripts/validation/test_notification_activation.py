"""Machine notification activation in a disposable PostgreSQL transaction only."""

from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.session import AsyncSessionLocal
from app.models import AuditLog, AutomationRule, AutomationRun, NotificationRule
from app.services import automations, notification_rules

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def isolated_notification_rows(isolated_resources):
    async def clear():
        async with AsyncSessionLocal() as session:
            await session.execute(text("TRUNCATE notification_rules CASCADE"))
            await session.commit()
    await clear()
    try:
        yield
    finally:
        await clear()


async def seed():
    async with AsyncSessionLocal() as session:
        notice = NotificationRule(name="Synthetic Activation", trigger_event="authorized_entry",
                                  conditions=[], actions=[{"type": "in_app"}], is_active=False)
        session.add(notice)
        await session.flush()
        workflow = AutomationRule(name="Synthetic Machine Toggle", is_active=True,
                                  triggers=[{"type": "time.every_x", "config": {"interval": 1, "unit": "hours"}}],
                                  trigger_keys=["time.every_x"], conditions=[],
                                  actions=[{"id": "toggle", "type": "notification.enable", "config": {"notification_rule_id": str(notice.id)}}])
        session.add(workflow)
        await session.commit()
        return notice.id, workflow.id


async def test_activation_locks_target_and_participates_in_outer_rollback():
    target, _ = await seed()
    async with AsyncSessionLocal() as owner:
        receipt = await notification_rules.set_automation_activation(owner, reference={"notification_rule_id": str(target)}, active=True)
        assert receipt["is_active"] is True
        async with AsyncSessionLocal() as observer:
            assert (await observer.get(NotificationRule, target)).is_active is False
            with pytest.raises(DBAPIError) as error:
                await observer.scalar(select(NotificationRule).where(NotificationRule.id == target).with_for_update(nowait=True))
            assert error.value.orig.sqlstate == "55P03"
        await owner.rollback()
    async with AsyncSessionLocal() as observer:
        assert (await observer.get(NotificationRule, target)).is_active is False
        assert not (await observer.scalars(select(AuditLog))).all()


async def test_real_automation_commits_toggle_with_machine_run_and_audit():
    target, workflow = await seed()
    result = await automations.AutomationService().execute_rule(
        str(workflow), trigger_key="time.every_x", trigger_payload={}, actor="Automation Engine", source="scheduler",
    )
    assert result["status"] == "success"
    async with AsyncSessionLocal() as session:
        assert (await session.get(NotificationRule, target)).is_active is True
        runs = (await session.scalars(select(AutomationRun))).all()
        audits = (await session.scalars(select(AuditLog))).all()
    assert len(runs) == len(audits) == 1
    assert runs[0].actor == audits[0].actor == "Automation Engine"
    assert audits[0].actor_user_id is None and audits[0].action == "automation_rule.success"
    assert runs[0].action_results[0]["notification_rule_id"] == str(target)
    assert audits[0].metadata_["action_results"][0]["is_active"] is True


async def test_failed_machine_audit_rolls_back_activation_and_checkpoint_retaining_occurrence(monkeypatch):
    target, workflow = await seed()
    monkeypatch.setattr(automations, "write_audit_log", AsyncMock(side_effect=RuntimeError("synthetic audit failure")))
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        await automations.AutomationService().execute_rule(str(workflow), trigger_key="time.every_x", trigger_payload={})
    async with AsyncSessionLocal() as session:
        assert (await session.get(NotificationRule, target)).is_active is False
        runs = (await session.scalars(select(AutomationRun))).all()
        assert len(runs) == 1 and runs[0].status == "queued"
        assert runs[0].action_plan[0]["state"] == "pending" and runs[0].action_results == []
        assert not (await session.scalars(select(AuditLog))).all()


@pytest.mark.parametrize("fail_audit", [False, True])
async def test_extension_transaction_commits_domain_reservation_receipt_and_audit_together(monkeypatch, fail_audit):
    from app.models import NotificationRun
    from app.services.automation_actions import AutomationActionExecutor
    from app.services.automation_execution import AutomationRunStore
    from app.services.notification_runs import NotificationRunStore
    from app.services.workflows.action_definitions import ACTION_BY_TYPE, ACTION_DEFINITIONS, ActionDefinition, NotificationReference
    from app.services.workflows.execution_contracts import checked_execution
    from app.services.workflows import export_contracts

    target, workflow = await seed()
    action_type = "fixture.domain.reserve"
    definition = ActionDefinition(
        type=action_type, group="fixture", group_label="Fixture", label="Reserve",
        description="Test-only caller transaction domain handoff", configuration=NotificationReference,
        execution="transactional", effect="configuration", editor="notification_reference",
    )
    monkeypatch.setitem(ACTION_BY_TYPE, action_type, definition)
    monkeypatch.setattr(export_contracts, "ACTION_DEFINITIONS", (*ACTION_DEFINITIONS, definition))
    generated = export_contracts.render_contracts()
    assert f'"{action_type}": {{ "notification_rule_id": string; "notification_rule_name": string }}' in generated
    assert f'"{action_type}": {{\n    "editor": "notification_reference"' in generated
    assert definition.defaults() == {"notification_rule_id": "", "notification_rule_name": ""}
    async with AsyncSessionLocal() as session:
        row = await session.get(AutomationRule, workflow)
        row.actions = [{"id": "reservation", "type": action_type,
                        "config": {"notification_rule_id": str(target)}}]
        await session.commit()

    calls = []
    original_bindings = AutomationActionExecutor._bind_handlers

    async def reserve_domain(session, action, _context, _rule, execution):
        identity = checked_execution(execution)
        owned = await session.get(AutomationRun, uuid.UUID(identity["run_id"]))
        item = owned.action_plan[identity["index"]]
        assert owned.claim_token == uuid.UUID(identity["claim_token"])
        assert item["state"] == "pending" and "attempted_at" not in item
        assert item["operation_id"] == identity["operation_id"] == identity["idempotency_key"]
        assert action["config"] == {"notification_rule_id": str(target), "notification_rule_name": ""}
        assert item["action"]["config"] == action["config"]
        receipt = await notification_rules.set_automation_activation(
            session, reference=action["config"], active=True,
        )
        await NotificationRunStore().enqueue_in_session(
            session, {"event_type": "fixture", "subject": "Inert domain reservation",
                      "severity": "info", "facts": {"notification_rule_id": action["config"]["notification_rule_id"]}},
            run_id=uuid.UUID(identity["operation_id"]),
        )
        calls.append(identity)
        return {"id": action["id"], "type": action["type"], "status": "success",
                "reservation_id": identity["operation_id"], **receipt}

    monkeypatch.setattr(AutomationActionExecutor, "_bind_handlers",
        lambda self: {**original_bindings(self), action_type: reserve_domain})
    service = automations.AutomationService()
    forbidden = AsyncMock(side_effect=AssertionError("External/independently committing port called"))
    def forbidden_owner():
        raise AssertionError("Hardware owner accessed by transactional action")
    service.action_executor.gates = service.action_executor.devices = forbidden_owner
    service.action_executor.maintenance_change = forbidden
    service.action_executor.integrations = forbidden
    original_audit = automations.write_audit_log
    observed = []

    async def audit_in_transaction(session, **kwargs):
        async with AsyncSessionLocal() as observer:
            persisted = (await observer.scalars(select(AutomationRun))).one()
            assert persisted.action_plan[0]["state"] == "pending"
            assert (await observer.get(NotificationRule, target)).is_active is False
            assert not (await observer.scalars(select(NotificationRun))).all()
            assert not (await observer.scalars(select(AuditLog))).all()
        observed.append(True)
        if fail_audit:
            raise RuntimeError("synthetic extension audit failure")
        return await original_audit(session, **kwargs)

    monkeypatch.setattr(automations, "write_audit_log", audit_in_transaction)
    if fail_audit:
        with pytest.raises(RuntimeError, match="synthetic extension audit failure"):
            await service.execute_rule(str(workflow), trigger_key="time.every_x", trigger_payload={})
        async with AsyncSessionLocal() as session:
            run = (await session.scalars(select(AutomationRun))).one()
            assert run.status == "queued" and run.action_plan[0]["state"] == "pending"
            assert run.action_results == []
            assert not (await session.scalars(select(NotificationRun))).all()
            assert not (await session.scalars(select(AuditLog))).all()
            assert (await session.get(NotificationRule, target)).is_active is False
        monkeypatch.setattr(automations, "write_audit_log", original_audit)
        assert await service.dispatcher.run_once(run.id)
    else:
        result = await service.execute_rule(str(workflow), trigger_key="time.every_x", trigger_payload={})
        assert result["status"] == "success"
    async with AsyncSessionLocal() as session:
        run = (await session.scalars(select(AutomationRun))).one()
        reservations = (await session.scalars(select(NotificationRun))).all()
        audits = (await session.scalars(select(AuditLog))).all()
        assert run.status == "success" and run.action_plan[0]["state"] == "succeeded"
        assert "attempted_at" not in run.action_plan[0]
        assert len(reservations) == len(audits) == 1
        assert reservations[0].context["facts"]["notification_rule_id"] == str(target)
        assert str(reservations[0].id) == run.action_plan[0]["operation_id"] == run.action_results[0]["reservation_id"]
        assert audits[0].metadata_["action_results"][0]["reservation_id"] == str(reservations[0].id)
        assert (await session.get(NotificationRule, target)).is_active is True
    assert not await service.dispatcher.run_once(run.id)
    assert len({call["operation_id"] for call in calls}) == 1 and observed
    forbidden.assert_not_awaited()


async def test_expired_claim_cannot_call_transactional_handler_or_reserve_domain_work(monkeypatch):
    from app.services.automation_execution import AutomationClaimLost
    from app.services.automation_intake import reserve_occurrence
    from app.services.workflows.automation_definition import captured_automation_context
    from app.models import NotificationRun
    from sqlalchemy import update

    target, workflow = await seed()
    service = automations.AutomationService()
    async with AsyncSessionLocal() as session:
        row = await session.get(AutomationRule, workflow)
        identity = await reserve_occurrence(
            session, row, captured_automation_context("time.every_x", {}),
            origin_kind="fixture", origin_id="expired", actor="Fixture", source="fixture",
        )
        await session.commit()
    claimed = await service.run_store.claim(identity)
    handler = AsyncMock(side_effect=AssertionError("Expired claim invoked handler"))
    service.action_executor.handlers["notification.enable"] = handler
    async with AsyncSessionLocal() as session:
        await session.execute(update(AutomationRun).where(AutomationRun.id == identity).values(
            lease_expires_at=text("clock_timestamp() - interval '1 second'")))
        await session.commit()
    with pytest.raises(AutomationClaimLost):
        await service.prepare_action_dispatch(claimed, claimed.claim_token, 0)
    handler.assert_not_awaited()
    async with AsyncSessionLocal() as session:
        assert (await session.get(NotificationRule, target)).is_active is False
        assert not (await session.scalars(select(NotificationRun))).all()
        assert not (await session.scalars(select(AuditLog))).all()
