"""Durable automation cutover against disposable PostgreSQL and inert sinks."""

from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

import asyncio
import copy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import AuditLog, AutomationRule, AutomationRun, NotificationRun
from app.modules.access_devices.base import CommandDelivery
from app.modules.gate.base import GateCommandResult, GateState
from app.services import automation_integration_actions, automation_intake, automations
from app.services.workflows.automation_definition import captured_automation_context
from app.services.gate_commands import GateCommandCoordinator
from app.services.notification_runs import ClaimLost, NotificationRunStore
from app.services.notifications import NotificationService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def inert_boundaries(monkeypatch, isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE notification_runs CASCADE"))
        await session.commit()
    monkeypatch.setattr(automation_integration_actions, "integration_action_status", AsyncMock(return_value=SimpleNamespace(enabled=True, disabled_reason=None)))
    preview = {"version": 1, "action": "open", "targets": [{"device_key": "synthetic_gate"}]}
    monkeypatch.setattr(automation_intake, "AccessDeviceConfiguration", lambda: SimpleNamespace(preview_gate_open=AsyncMock(return_value=preview)))


def action(kind="gate.open", identity="first"):
    config = {"target_mode": "all", "message_template": "Synthetic message"} if kind == "integration.whatsapp.send_message" else {}
    return {"id": identity, "type": kind, "config": config, "reason_template": "Synthetic recovery"}


async def seed(actions=None, *, due=False, trigger="time.every_x"):
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        row = AutomationRule(name="Synthetic recovery workflow", is_active=True,
            triggers=[{"id": "tick", "type": trigger, "config": {"interval": 1, "unit": "hours"}}], trigger_keys=[trigger],
            conditions=[], actions=actions if actions is not None else [action()],
            next_run_at=now - timedelta(seconds=1) if due else now + timedelta(hours=1))
        session.add(row)
        await session.commit()
        return row.id


async def reserve(service, rule_id, *, seconds_old=0, origin=None):
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, rule_id)
        now = await session.scalar(select(func.clock_timestamp()))
        payload = {"occurred_at": now.isoformat(), "scheduled_for": (now - timedelta(seconds=seconds_old)).isoformat()}
        context = captured_automation_context(rule.trigger_keys[0], payload)
        identity = await automation_intake.reserve_occurrence(session, rule, context, origin_kind="synthetic",
            origin_id=origin or str(uuid.uuid4()), actor="Automation Engine", source="synthetic")
        await session.commit()
        return identity


def gate_sink(monkeypatch, calls, *, accepted=True, delivery=CommandDelivery.ACCEPTED, blocked=None, release=None, before_authorize=None):
    async def open_gate(reason, *, bypass_schedule=False, command_context):
        async with AsyncSessionLocal() as session:
            rows = (await session.scalars(select(AutomationRun))).all()
            run = next(row for row in rows if any(item["operation_id"] == command_context.intent_id for item in row.action_plan or []))
            checkpoint = next(item for item in run.action_plan if item["operation_id"] == command_context.intent_id)
            assert checkpoint["state"] == "attempting", "Provider was reached before its durable attempt checkpoint"
        if before_authorize:
            await before_authorize()
        async with AsyncSessionLocal() as session:
            try:
                await command_context.authorize_dispatch(session)
            except ValueError:
                return GateCommandResult(False, GateState.UNKNOWN, "Synthetic current authority denial", delivery=CommandDelivery.NOT_SENT)
            await session.commit()
        calls.append(command_context)
        if blocked:
            blocked.set()
            await _bounded(release.wait())
        return GateCommandResult(accepted, GateState.OPENING if accepted else GateState.UNKNOWN, "Synthetic receipt",
            delivery=delivery, metadata={"mechanically_confirmed": accepted and delivery == CommandDelivery.ACCEPTED,
                "admission_verified": accepted and delivery == CommandDelivery.ACCEPTED,
                "requires_reconciliation": delivery == CommandDelivery.UNKNOWN})
    monkeypatch.setattr(automations, "get_gate_command_coordinator", lambda: GateCommandCoordinator(lambda _: SimpleNamespace(open_gate=open_gate)))


async def test_committed_occurrence_without_wakeup_is_dispatched_and_receipt_retained(monkeypatch):
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    identity = await reserve(service, await seed())
    assert await automations.AutomationService().dispatcher.run_once()
    row = await service.run_store.get(identity)
    assert row.status == "success" and row.action_plan[0]["state"] == "succeeded"
    assert len(calls) == 1 and calls[0].intent_id == row.action_plan[0]["operation_id"]
    assert calls[0].idempotency_key == calls[0].intent_id and calls[0].expires_at is not None
    assert await service.dispatcher.run_once(identity) is False
    assert len(calls) == 1
    async with AsyncSessionLocal() as session:
        assert (await session.get(AutomationRule, row.rule_id)).run_count == 1
        assert len((await session.scalars(select(AuditLog))).all()) == 1


async def test_fresh_workers_do_not_duplicate_inflight_action(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls, blocked=entered, release=release)
    identity = await reserve(service, await seed())
    task = asyncio.create_task(service.dispatcher.run_once(identity))
    try:
        await _bounded(entered.wait())
        assert await automations.AutomationService().dispatcher.run_once(identity) is False
    finally:
        release.set()
        await _bounded(task)
    assert len(calls) == 1


async def test_expired_attempt_stays_unknown_and_late_worker_cannot_resend(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls, blocked=entered, release=release)
    identity = await reserve(service, await seed())
    task = asyncio.create_task(service.dispatcher.run_once(identity))
    try:
        await _bounded(entered.wait())
        async with AsyncSessionLocal() as session:
            await session.execute(update(AutomationRun).where(AutomationRun.id == identity).values(lease_expires_at=text("clock_timestamp() - interval '1 second'")))
            await session.commit()
        assert await automations.AutomationService().dispatcher.run_once(identity) is False
    finally:
        release.set()
        await _bounded(task)
    row = await service.run_store.get(identity)
    assert row.status == "review_required" and row.action_plan[0]["state"] == "unknown"
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["disabled", "fingerprint"])
async def test_unattempted_actions_use_current_rule(monkeypatch, change):
    calls, service, rule_id = [], automations.AutomationService(), await seed()
    gate_sink(monkeypatch, calls)
    identity = await reserve(service, rule_id)
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, rule_id)
        if change == "disabled":
            rule.is_active = False
        else:
            rule.actions = [action("garage_door.close")]
        await session.commit()
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert calls == [] and row.action_plan[0]["state"] == "skipped"
    assert row.action_results[0]["reason"] == ("rule_not_active" if change == "disabled" else "rule_changed_since_occurrence")


async def test_current_rule_rechecked_at_hardware_boundary_after_attempt_checkpoint(monkeypatch):
    calls, service, rule_id = [], automations.AutomationService(), await seed()
    async def disable():
        async with AsyncSessionLocal() as session:
            rule = await session.get(AutomationRule, rule_id)
            rule.is_active = False
            await session.commit()
    gate_sink(monkeypatch, calls, before_authorize=disable)
    identity = await reserve(service, rule_id)
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert calls == [] and row.action_plan[0]["state"] == "failed"
    assert row.action_results[0]["delivery"] == "not_sent"


async def test_scheduled_stale_hardware_skips_but_notification_handoff_survives(monkeypatch):
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    identity = await reserve(service, await seed([action(), action("integration.whatsapp.send_message", "notice")]), seconds_old=61)
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert calls == [] and [item["state"] for item in row.action_plan] == ["skipped", "succeeded"]
    assert row.action_results[0]["reason"] == "scheduled_hardware_expired"
    async with AsyncSessionLocal() as session:
        notice = await session.get(NotificationRun, uuid.UUID(row.action_results[1]["notification_run_id"]))
        assert notice.status == "queued" and notice.claim_token is None


async def test_unknown_hardware_inhibits_next_hardware_but_allows_notification_handoff(monkeypatch):
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls, accepted=False, delivery=CommandDelivery.UNKNOWN)
    identity = await reserve(service, await seed([action(), action("gate.open", "second"), action("integration.whatsapp.send_message", "notice")]))
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert len(calls) == 1 and row.status == "review_required" and row.review_reason
    assert [item["state"] for item in row.action_plan] == ["unknown", "skipped", "succeeded"]
    assert row.action_results[2]["status"] == "queued" and row.action_results[2]["delivered_count"] == 0


async def test_scheduler_reserves_and_advances_once_without_execution_handoff():
    first, second, rule_id = automations.AutomationService(), automations.AutomationService(), await seed(due=True)
    counts = await _bounded(asyncio.gather(first._reserve_due_rules(), second._reserve_due_rules()))
    assert sum(counts) == 1
    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(select(AutomationRun))).all()
        rule = await session.get(AutomationRule, rule_id)
    assert len(rows) == 1 and rows[0].status == "queued" and rows[0].action_plan[0]["state"] == "pending"
    assert rule.next_run_at > rows[0].queued_at
    assert await first._reserve_due_rules() == 0


async def test_scheduler_transaction_failure_retains_due_rule_without_partial_reservation(monkeypatch):
    service, rule_id = automations.AutomationService(), await seed(due=True)
    original = automations.reserve_occurrence
    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic reserve failure")
    monkeypatch.setattr(automations, "reserve_occurrence", fail)
    with pytest.raises(RuntimeError, match="synthetic reserve failure"):
        await service._reserve_due_rules()
    async with AsyncSessionLocal() as session:
        assert not (await session.scalars(select(AutomationRun))).all()
        assert (await session.get(AutomationRule, rule_id)).next_run_at < await session.scalar(select(func.clock_timestamp()))


async def test_audit_failure_after_external_success_recovers_audit_without_replaying(monkeypatch):
    service, calls = automations.AutomationService(), []
    gate_sink(monkeypatch, calls)
    identity = await reserve(service, await seed())
    original = automations.write_audit_log
    monkeypatch.setattr(automations, "write_audit_log", AsyncMock(side_effect=RuntimeError("synthetic audit failure")))
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        await service.dispatcher.run_once(identity)
    assert (await service.run_store.get(identity)).action_plan[0]["state"] == "succeeded" and len(calls) == 1
    monkeypatch.setattr(automations, "write_audit_log", original)
    await service.recover_completion_audits()
    await service.recover_completion_audits()
    assert len(calls) == 1
    async with AsyncSessionLocal() as session:
        assert len((await session.scalars(select(AuditLog))).all()) == 1


@pytest.mark.parametrize("change", ["disabled", "fingerprint", "origin"])
async def test_queued_notification_handoff_rechecks_current_automation_before_attempt(change):
    service, rule_id = automations.AutomationService(), await seed([action("integration.whatsapp.send_message")])
    identity = await reserve(service, rule_id)
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    notification_id = uuid.UUID(row.action_results[0]["notification_run_id"])
    store = NotificationRunStore()
    claimed = await store.claim(notification_id)
    await store.save_plan(notification_id, claimed.claim_token, [{"state": "pending", "action": {"type": "whatsapp"}}])
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, rule_id)
        if change == "disabled":
            rule.is_active = False
        elif change == "fingerprint":
            rule.actions = [action("integration.whatsapp.send_message", "changed")]
        else:
            notice = await session.get(NotificationRun, notification_id)
            context = copy.deepcopy(notice.context)
            context["automation_origin"]["run_id"] = str(uuid.uuid4())
            notice.context = context
        await session.commit()
    with pytest.raises(ClaimLost, match="no longer authorizes"):
        await store.begin_action(notification_id, claimed.claim_token, 0, authorize_origin=NotificationService().authorize_attempt)
    retained = await store.get(notification_id)
    assert retained.status == "skipped" and retained.delivery_plan[0]["state"] == "skipped"
    assert retained.claim_token is None and retained.delivered_count == 0


async def test_valid_handoff_current_authority_allows_exactly_one_notification_attempt():
    service = automations.AutomationService()
    identity = await reserve(service, await seed([action("integration.whatsapp.send_message")]))
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    notice_id = uuid.UUID(row.action_results[0]["notification_run_id"])
    assert str(notice_id) == row.action_plan[0]["operation_id"]
    store = NotificationRunStore()
    claimed = await store.claim(notice_id)
    await store.save_plan(notice_id, claimed.claim_token, [{"state": "pending", "action": {"type": "whatsapp"}}])
    await store.begin_action(notice_id, claimed.claim_token, 0, authorize_origin=NotificationService().authorize_attempt)
    assert (await store.get(notice_id)).delivery_plan[0]["state"] == "attempting"
    with pytest.raises(ClaimLost, match="already attempted"):
        await store.begin_action(notice_id, claimed.claim_token, 0, authorize_origin=NotificationService().authorize_attempt)


async def test_committed_notification_handoff_and_action_receipt_roll_back_together_on_audit_failure(monkeypatch):
    service = automations.AutomationService()
    identity = await reserve(service, await seed([action("integration.whatsapp.send_message")]))
    monkeypatch.setattr(automations, "write_audit_log", AsyncMock(side_effect=RuntimeError("synthetic audit failure")))
    with pytest.raises(RuntimeError):
        await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert row.status == "queued" and row.action_plan[0]["state"] == "pending" and row.action_results == []
    async with AsyncSessionLocal() as session:
        assert not (await session.scalars(select(NotificationRun))).all()



async def test_exhausted_one_shot_schedule_stays_enabled_without_future_occurrence_or_self_cancellation():
    service, rule_id = automations.AutomationService(), await seed([action("integration.whatsapp.send_message")])
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        rule = await session.get(AutomationRule, rule_id)
        rule.triggers = [{"id": "once", "type": "time.specific_datetime", "config": {"run_at": (now - timedelta(seconds=1)).isoformat(), "recurrence": "none"}}]
        rule.trigger_keys, rule.next_run_at = ["time.specific_datetime"], now - timedelta(seconds=1)
        await session.commit()
    assert await service._reserve_due_rules() == 1
    assert await service.dispatcher.run_once()
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, rule_id)
        run = await session.scalar(select(AutomationRun))
        assert rule.is_active and rule.next_run_at is None
    assert await service._reserve_due_rules() == 0
    notice_id = uuid.UUID(run.action_results[0]["notification_run_id"])
    store = NotificationRunStore()
    claimed = await store.claim(notice_id)
    await store.save_plan(notice_id, claimed.claim_token, [{"state": "pending", "action": {"type": "whatsapp"}}])
    await store.begin_action(notice_id, claimed.claim_token, 0, authorize_origin=NotificationService().authorize_attempt)
    assert (await store.get(notice_id)).delivery_plan[0]["state"] == "attempting"


async def test_notification_origin_without_explicit_authorizer_fails_closed():
    service = automations.AutomationService()
    identity = await reserve(service, await seed([action("integration.whatsapp.send_message")]))
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    notice_id = uuid.UUID(row.action_results[0]["notification_run_id"])
    store = NotificationRunStore()
    claimed = await store.claim(notice_id)
    await store.save_plan(notice_id, claimed.claim_token, [{"state": "pending"}])
    with pytest.raises(ClaimLost):
        await store.begin_action(notice_id, claimed.claim_token, 0)
    assert (await store.get(notice_id)).delivery_plan[0]["reason"] == "notification_origin_validator_missing"


async def test_rule_disable_does_not_erase_already_accepted_notification_action():
    service, rule_id = automations.AutomationService(), await seed([action("integration.whatsapp.send_message")])
    identity = await reserve(service, rule_id)
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    notice_id = uuid.UUID(row.action_results[0]["notification_run_id"])
    store, authorize = NotificationRunStore(), NotificationService().authorize_attempt
    claimed = await store.claim(notice_id)
    await store.save_plan(notice_id, claimed.claim_token, [{"state": "pending"}, {"state": "pending"}])
    await store.begin_action(notice_id, claimed.claim_token, 0, authorize_origin=authorize)
    await store.finish_action(notice_id, claimed.claim_token, 0, {"state": "accepted"})
    async with AsyncSessionLocal() as session:
        rule = await session.get(AutomationRule, rule_id)
        rule.is_active = False
        await session.commit()
    with pytest.raises(ClaimLost):
        await store.begin_action(notice_id, claimed.claim_token, 1, authorize_origin=authorize)
    retained = await store.get(notice_id)
    assert [item["state"] for item in retained.delivery_plan] == ["accepted", "skipped"]
    assert retained.status == "provider_accepted" and retained.delivered_count == 1 and retained.skipped_count == 1


@pytest.mark.parametrize("kind", ["vehicle", "visitor"])
@pytest.mark.parametrize("revoke", [False, True], ids=["valid-after-60s", "revoked"])
async def test_notification_retains_900s_lifetime_but_rechecks_current_domain(monkeypatch, kind, revoke):
    from app.models import AccessEvent, LprIngestEvent, Vehicle, VisitorPass
    from app.models.enums import AccessDecision, AccessDirection, VisitorPassStatus
    from app.services.access import authorization
    monkeypatch.setattr(authorization, "get_runtime_config_for_session", AsyncMock(return_value=SimpleNamespace(site_timezone="Europe/London", schedule_default_policy="allow")))
    trigger = "vehicle.known_plate" if kind == "vehicle" else "visitor_pass.used"
    rule_id = await seed([action("integration.whatsapp.send_message")], trigger=trigger)
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        observed = now - timedelta(seconds=120)
        vehicle = Vehicle(registration_number="SYNTH07", is_active=True) if kind == "vehicle" else None
        if vehicle:
            session.add(vehicle)
            await session.flush()
        event = AccessEvent(vehicle_id=vehicle.id if vehicle else None, registration_number="SYNTH07", direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED, confidence=1, source="synthetic", occurred_at=observed, raw_payload={})
        session.add(event)
        await session.flush()
        session.add(LprIngestEvent(idempotency_key=f"synthetic:{event.id}", source="synthetic", registration_number="SYNTH07",
            captured_at=observed, received_at=observed, access_event_id=event.id, status="completed", normalized_payload={}))
        visitor = None
        if kind == "visitor":
            visitor = VisitorPass(visitor_name="Synthetic visitor", expected_time=observed, status=VisitorPassStatus.USED,
                arrival_event_id=event.id, arrival_time=observed, number_plate=None)
            session.add(visitor)
            await session.flush()
            event.raw_payload = {"visitor_pass": {"id": str(visitor.id)}}
        payload = {"access_event_id": str(event.id), "vehicle_id": str(vehicle.id) if vehicle else None,
            "visitor_pass_id": str(visitor.id) if visitor else None, "decision": "granted", "occurred_at": observed.isoformat()}
        await session.commit()
        vehicle_id, visitor_id = vehicle.id if vehicle else None, visitor.id if visitor else None
    service = automations.AutomationService()
    result = await service.execute_rule(str(rule_id), trigger_key=trigger, trigger_payload=payload)
    notice_id = uuid.UUID(result["run"]["action_results"][0]["notification_run_id"])
    if revoke:
        async with AsyncSessionLocal() as session:
            if vehicle_id:
                (await session.get(Vehicle, vehicle_id)).is_active = False
            else:
                (await session.get(VisitorPass, visitor_id)).status = VisitorPassStatus.CANCELLED
            await session.commit()
    store = NotificationRunStore()
    claimed = await store.claim(notice_id)
    await store.save_plan(notice_id, claimed.claim_token, [{"state": "pending"}])
    if revoke:
        with pytest.raises(ClaimLost):
            await store.begin_action(notice_id, claimed.claim_token, 0, authorize_origin=NotificationService().authorize_attempt)
        assert (await store.get(notice_id)).delivery_plan[0]["reason"] == "recognition_authorization_changed"
    else:
        await store.begin_action(notice_id, claimed.claim_token, 0, authorize_origin=NotificationService().authorize_attempt)
        assert (await store.get(notice_id)).delivery_plan[0]["state"] == "attempting"


@pytest.mark.parametrize("mode,targets,phone,expected", [
    ("all", [], "", ["whatsapp:*"]),
    ("selected", ["11111111-1111-1111-1111-111111111111"], "", ["whatsapp:admin:11111111-1111-1111-1111-111111111111"]),
    ("dynamic", [], "+44 7700 900000", ["whatsapp:number:447700900000"]),
    ("selected", [], "", []),
    ("selected", ["malformed"], "", []),
    ("dynamic", [], "", []),
])
async def test_notification_handoff_preserves_target_selection_without_empty_means_all(mode, targets, phone, expected):
    item = action("integration.whatsapp.send_message")
    item["config"].update(target_mode=mode, target_user_ids=targets, phone_number_template=phone)
    service = automations.AutomationService()
    identity = await reserve(service, await seed([item]))
    await service.dispatcher.run_once(identity)
    run = await service.run_store.get(identity)
    async with AsyncSessionLocal() as session:
        notices = (await session.scalars(select(NotificationRun))).all()
    if expected:
        assert len(notices) == 1 and notices[0].rules_override[0]["actions"][0]["target_ids"] == expected
        assert run.action_results[0]["status"] == "queued" and run.action_results[0]["delivered_count"] == 0
        rendered = NotificationService().render_rule(notices[0].rules_override[0],
            SimpleNamespace(event_type="automation.whatsapp", subject=notices[0].context["subject"], severity="info", facts=notices[0].context["facts"]))
        assert rendered["actions"][0]["title"] == "Synthetic message" and rendered["actions"][0]["message"] == ""
    else:
        assert notices == [] and run.action_results[0]["reason"] == "no_whatsapp_targets"


@pytest.mark.parametrize("uncertain_second", [False, True])
async def test_garage_targets_keep_stable_per_target_identity_and_partial_truth(monkeypatch, uncertain_second):
    from test_recovery_boundaries import _device_outcome
    service, calls = automations.AutomationService(), []
    keys = ["synthetic_first", "synthetic_second"]
    async def preview(key, command, **kwargs):
        return {"version": 1, "action": command, "target_device_key": key, "targets": [{"device_key": key}]}
    async def command(key, requested, reason, **kwargs):
        async with AsyncSessionLocal() as session:
            await kwargs["authorize_dispatch"](session)
            await session.commit()
        calls.append((key, kwargs))
        result = _device_outcome(key, True, "unknown" if uncertain_second and key == keys[1] else "opening")
        return result
    monkeypatch.setattr(automation_intake, "automation_garage_targets", AsyncMock(return_value=[SimpleNamespace(key=key) for key in keys]))
    monkeypatch.setattr(automation_intake, "AccessDeviceConfiguration", lambda: SimpleNamespace(preview_device_command=preview))
    monkeypatch.setattr(automations, "get_access_device_service", lambda: SimpleNamespace(command_device=command))
    identity = await reserve(service, await seed([action("garage_door.open")]))
    await service.dispatcher.run_once(identity)
    run = await service.run_store.get(identity)
    action_id = uuid.UUID(run.action_plan[0]["operation_id"])
    assert [key for key, _ in calls] == keys
    for key, kwargs in calls:
        expected = str(uuid.uuid5(action_id, key))
        assert kwargs["intent_id"] == kwargs["idempotency_key"] == expected
        assert kwargs["target_plan"]["target_device_key"] == key and kwargs["expires_at"] is not None
    assert run.status == ("review_required" if uncertain_second else "success")
    assert run.action_results[0]["requires_reconciliation"] is uncertain_second
    assert len(run.action_results[0]["outcomes"]) == 2
    assert run.action_results[0]["outcomes"][0]["verified"] is True
    await automations.AutomationService().dispatcher.run_once(identity)
    assert len(calls) == 2
