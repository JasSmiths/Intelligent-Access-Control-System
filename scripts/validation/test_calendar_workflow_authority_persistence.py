"""Actual checked workflow attempts and late participant lock/authority drift."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import copy
import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.session import AsyncSessionLocal, engine
from app.models import AuditLog, AutomationRule, AutomationRun, MaintenanceModeState, VisitorPass
from app.services.automation_execution import AutomationClaimLost, AutomationRunStore
from app.services.automation_intake import reserve_occurrence
from app.services.calendar_workflow_authority import CalendarWorkflowAuthority
from app.services.visitor_passes import VisitorPassService
from app.services.workflows.automation_definition import captured_automation_context
from app.services.workflows.execution_contracts import checked_action, checked_execution

pytestmark = pytest.mark.asyncio


async def attempted(*, conditions=None):
    action = {"id": "inert-calendar-action", "type": "integration.icloud_calendar.sync",
              "config": {"provider": "icloud_calendar", "action": "sync_calendars"}}
    rule = AutomationRule(name="Inert calendar workflow", is_active=True,
        triggers=[{"id": "clock", "type": "time.every_x", "config": {"interval": 1, "unit": "minutes"}}],
        trigger_keys=["time.every_x"], conditions=conditions or [], actions=[action])
    store = AutomationRunStore()
    async with AsyncSessionLocal() as session:
        session.add(rule)
        await session.flush()
        identity = await reserve_occurrence(session, rule, captured_automation_context("time.every_x", {}),
            origin_kind="synthetic", origin_id=str(uuid.uuid4()), actor="Automation Engine", source="inert")
        await session.commit()
    claimed = await store.claim(identity)
    async with AsyncSessionLocal() as session:
        item = await store.begin_action(session, identity, claimed.claim_token, 0)
        await session.commit()
    execution = checked_execution({"run_id": str(identity), "claim_token": str(claimed.claim_token),
        "index": 0, "operation_id": item["operation_id"], "idempotency_key": item["idempotency_key"]})
    return rule.id, identity, store, execution, checked_action(item["action"])


async def test_current_participant_early_and_final_check_do_not_mutate_workflow_receipts(isolated_resources):
    _, identity, store, execution, action = await attempted()
    before = await store.get(identity)
    authority = CalendarWorkflowAuthority(execution, action, runs=store)
    async with AsyncSessionLocal() as session:
        await authority.authorize(session, False)
        assert not session.dirty and not session.new
    assert engine.pool.checkedout() == 0
    async with AsyncSessionLocal() as session:
        await authority.authorize(session, True)
        assert not session.dirty and not session.new
        await session.commit()
    after = await store.get(identity)
    assert after.action_plan == before.action_plan and after.condition_results == before.condition_results
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("drift", ["inactive", "rule_change", "claim", "expiry", "operation", "idempotency", "action_target"])
async def test_current_checked_workflow_drift_denies_participant(drift, isolated_resources):
    rule_id, identity, store, execution, action = await attempted()
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, rule_id)
        run = await session.get(AutomationRun, identity)
        if drift == "inactive":
            rule.is_active = False
        elif drift == "rule_change":
            rule.name = "Changed after occurrence"
        elif drift == "claim":
            run.claim_token = uuid.uuid4()
        elif drift == "expiry":
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            plan = copy.deepcopy(run.action_plan)
            if drift == "action_target":
                plan[0]["action"]["id"] = "different-action"
            else:
                plan[0]["operation_id" if drift == "operation" else "idempotency_key"] = str(uuid.uuid4())
            run.action_plan = plan
        await session.commit()
    authority = CalendarWorkflowAuthority(execution, action, runs=store)
    async with AsyncSessionLocal() as session:
        with pytest.raises((ValueError, AutomationClaimLost)):
            await authority.authorize(session, True)
        await session.rollback()
    assert engine.pool.checkedout() == 0


async def test_current_maintenance_condition_is_rechecked_under_unchanged_rule(isolated_resources):
    _, _, store, execution, action = await attempted(conditions=[{
        "id": "maintenance-condition", "type": "maintenance_mode.disabled", "config": {}}])
    authority = CalendarWorkflowAuthority(execution, action, runs=store)
    async with AsyncSessionLocal() as session:
        await authority.authorize(session, False)
    async with AsyncSessionLocal() as session:
        session.add(MaintenanceModeState(id=1, is_active=True))
        await session.commit()
    try:
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match="condition_failed"):
                await authority.authorize(session, True)
            await session.rollback()
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(text("DELETE FROM maintenance_mode_state"))
            await session.commit()


@pytest.mark.parametrize("contended", ["rule", "run"])
async def test_final_nowait_conflict_rolls_back_real_visitor_mutation_and_required_audit(contended, isolated_resources):
    rule_id, identity, store, execution, action = await attempted()
    authority = CalendarWorkflowAuthority(execution, action, runs=store)
    async with AsyncSessionLocal() as blocker:
        model, key = (AutomationRule, rule_id) if contended == "rule" else (AutomationRun, identity)
        await blocker.scalar(select(model).where(model.id == key).with_for_update())
        async with AsyncSessionLocal() as participant:
            created = await VisitorPassService().create_pass(participant,
                visitor_name="Inert atomic calendar visitor", expected_time=datetime.now(UTC) + timedelta(hours=1),
                source="icloud_calendar", source_reference="inert-calendar-authority:" + str(uuid.uuid4()),
                actor="Automation Engine")
            await participant.flush()
            assert await participant.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.target_id == str(created.id))) == 1
            started = monotonic()
            with pytest.raises(DBAPIError):
                await authority.authorize(participant, True)
            assert monotonic() - started < 1
            await participant.rollback()
        await blocker.rollback()
    async with AsyncSessionLocal() as observer:
        assert await observer.get(VisitorPass, created.id) is None
        assert await observer.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.target_id == str(created.id))) == 0
    assert engine.pool.checkedout() == 0
