"""Machine notification activation in a disposable PostgreSQL transaction only."""

from test_recovery_boundaries import isolated_resources as isolated_resources

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
