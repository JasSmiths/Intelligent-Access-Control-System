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
            await session.execute(text("DROP TABLE IF EXISTS fixture_vehicle_refresh_reservations"))
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


@pytest.mark.parametrize("fail_audit", [False, True])
async def test_known_vehicle_refresh_extension_uses_current_owner_and_atomic_reservation(monkeypatch, fail_audit):
    """Hypothetical automation origin: no event-keyed arrival job is fabricated.

    The small SQL fixture represents the additional domain reservation a real
    manual-origin feature would need. No runtime worker can discover this table.
    """
    from app.models import AccessEvent, Vehicle, VehicleInformationJob
    from app.services import vehicle_information, vehicle_information_jobs, vehicle_information_store
    from app.modules.dvla.vehicle_enquiry import normalize_registration_number
    from app.services.automation_actions import AutomationActionExecutor
    from app.services.workflows.action_definitions import ACTION_BY_TYPE, ActionDefinition, Configuration
    from app.services.workflows.execution_contracts import checked_execution

    class VehicleRefreshConfiguration(Configuration):
        vehicle_id: str = ""

    action_type = "fixture.vehicle_information.refresh"
    definition = ActionDefinition(type=action_type, group="fixture", group_label="Fixture",
        label="Refresh known vehicle", description="Inert manual-origin reservation exercise",
        configuration=VehicleRefreshConfiguration, execution="transactional",
        effect="configuration", editor="none")
    monkeypatch.setitem(ACTION_BY_TYPE, action_type, definition)
    _, workflow = await seed()
    async with AsyncSessionLocal() as session:
        await session.execute(text("CREATE TABLE fixture_vehicle_refresh_reservations ("
            "operation_id uuid PRIMARY KEY, vehicle_id uuid NOT NULL REFERENCES vehicles(id), "
            "registration_number text NOT NULL, origin_run_id uuid NOT NULL, state text NOT NULL)"))
        vehicle = Vehicle(registration_number="P9REFRESH", is_active=True, information_outcome="found")
        session.add(vehicle)
        rule = await session.get(AutomationRule, workflow)
        await session.flush()
        vehicle_id = vehicle.id
        rule.actions = [{"id": "refresh", "type": action_type, "config": {"vehicle_id": str(vehicle_id)}}]
        await session.commit()

    calls = []
    original_bindings = AutomationActionExecutor._bind_handlers
    async def reserve_refresh(session, action, _context, _rule, execution):
        identity = checked_execution(execution)
        run = await session.get(AutomationRun, uuid.UUID(identity["run_id"]))
        item = run.action_plan[identity["index"]]
        assert run.claim_token == uuid.UUID(identity["claim_token"])
        assert item["state"] == "pending" and "attempted_at" not in item
        assert item["operation_id"] == identity["operation_id"] == identity["idempotency_key"]
        current = await session.scalar(select(Vehicle).where(
            Vehicle.id == uuid.UUID(action["config"]["vehicle_id"])).with_for_update()
            .execution_options(populate_existing=True))
        assert current is not None and current.is_active
        # The feature owner clears its aggregate in the caller's transaction;
        # a test-only reservation records current identity, never stale config.
        await vehicle_information_store.invalidate_information_in_session(session, current)
        await session.execute(text("INSERT INTO fixture_vehicle_refresh_reservations "
            "(operation_id,vehicle_id,registration_number,origin_run_id,state) "
            "VALUES (:operation,:vehicle,:plate,:origin,'reserved') ON CONFLICT DO NOTHING"),
            {"operation": uuid.UUID(identity["operation_id"]), "vehicle": current.id,
             "plate": normalize_registration_number(current.registration_number), "origin": run.id})
        calls.append(identity["operation_id"])
        return {"id": action["id"], "type": action["type"], "status": "success",
                "reservation_id": identity["operation_id"], "vehicle_id": str(current.id),
                "registration_number": current.registration_number}
    monkeypatch.setattr(AutomationActionExecutor, "_bind_handlers",
        lambda self: {**original_bindings(self), action_type: reserve_refresh})
    forbidden = AsyncMock(side_effect=AssertionError("Fixture cannot call a provider"))
    monkeypatch.setattr(vehicle_information_jobs, "get_vehicle_information_service", forbidden)
    monkeypatch.setattr(vehicle_information.VehicleInformationService, "_fetch", forbidden)
    service = automations.AutomationService()
    original_audit = automations.write_audit_log
    async def required_audit(session, **kwargs):
        async with AsyncSessionLocal() as observer:
            assert not (await observer.execute(text("SELECT operation_id FROM fixture_vehicle_refresh_reservations"))).all()
            assert (await observer.get(Vehicle, vehicle_id)).information_outcome == "found"
            run = (await observer.scalars(select(AutomationRun))).one()
            assert run.action_plan[0]["state"] == "pending" and run.action_results == []
        if fail_audit:
            raise RuntimeError("synthetic refresh audit failure")
        return await original_audit(session, **kwargs)
    monkeypatch.setattr(automations, "write_audit_log", required_audit)
    try:
        # Registration changes after enqueue configuration are resolved by the
        # actual domain participant, not copied from the action's stored config.
        async with AsyncSessionLocal() as session:
            current = await session.get(Vehicle, vehicle_id)
            current.registration_number = "P9CURRENT"
            await session.commit()
        if fail_audit:
            with pytest.raises(RuntimeError, match="synthetic refresh audit failure"):
                await service.execute_rule(str(workflow), trigger_key="time.every_x", trigger_payload={})
            async with AsyncSessionLocal() as session:
                run = (await session.scalars(select(AutomationRun))).one()
                assert run.status == "queued" and run.action_results == []
                assert run.action_plan[0]["state"] == "pending"
                assert not (await session.execute(text("SELECT operation_id FROM fixture_vehicle_refresh_reservations"))).all()
                assert (await session.get(Vehicle, vehicle_id)).information_outcome == "found"
                assert not (await session.scalars(select(AuditLog))).all()
            monkeypatch.setattr(automations, "write_audit_log", original_audit)
            assert await service.dispatcher.run_once(run.id)
        else:
            assert (await service.execute_rule(str(workflow), trigger_key="time.every_x", trigger_payload={}))['status'] == 'success'
        async with AsyncSessionLocal() as session:
            run = (await session.scalars(select(AutomationRun))).one()
            reservation = (await session.execute(text("SELECT * FROM fixture_vehicle_refresh_reservations"))).mappings().one()
            audit = (await session.scalars(select(AuditLog))).one()
            assert run.status == "success" and run.action_plan[0]["state"] == "succeeded"
            assert "attempted_at" not in run.action_plan[0]
            assert str(reservation['operation_id']) == run.action_results[0]['reservation_id'] == run.action_plan[0]['operation_id']
            assert reservation['vehicle_id'] == vehicle_id and reservation['registration_number'] == 'P9CURRENT'
            assert audit.metadata_['action_results'][0]['reservation_id'] == str(reservation['operation_id'])
            assert (await session.get(Vehicle, vehicle_id)).information_outcome is None
            assert not (await session.scalars(select(AccessEvent))).all()
            assert not (await session.scalars(select(VehicleInformationJob))).all()
        assert not await service.dispatcher.run_once(run.id)
        assert len(set(calls)) == 1
        forbidden.assert_not_awaited()
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(text("DROP TABLE fixture_vehicle_refresh_reservations"))
            await session.commit()
