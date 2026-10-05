"""Durable automation cutover against disposable PostgreSQL and inert sinks."""

from test_recovery_boundaries import _bounded, isolated_resources as isolated_resources

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import AuditLog, AutomationRule, AutomationRun
from app.modules.access_devices.base import CommandDelivery
from app.modules.gate.base import GateCommandResult, GateState
from app.services import automation_integration_actions, automation_intake, automations
from app.services.workflows.automation_definition import captured_automation_context
from app.services.gate_commands import GateCommandCoordinator

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def inert_boundaries(monkeypatch, isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE notification_runs CASCADE"))
        await session.commit()
    monkeypatch.setattr(automation_integration_actions, "integration_action_status", AsyncMock(return_value=SimpleNamespace(enabled=True, disabled_reason=None)))
    monkeypatch.setattr(automations, "set_automation_activation", AsyncMock(return_value={"enabled": False, "rule_ids": []}))
    preview = {"version": 1, "action": "open", "targets": [{"device_key": "synthetic_gate"}]}
    monkeypatch.setattr(automation_intake, "AccessDeviceConfiguration", lambda: SimpleNamespace(preview_gate_open=AsyncMock(return_value=preview)))


def action(kind="gate.open", identity="first"):
    config = {}
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


async def test_scheduled_stale_hardware_skips_but_safe_activation_survives(monkeypatch):
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    identity = await reserve(service, await seed([action(), action("notification.disable", "notice")]), seconds_old=61)
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert calls == [] and [item["state"] for item in row.action_plan] == ["skipped", "succeeded"]
    assert row.action_results[0]["reason"] == "scheduled_hardware_expired"
    assert row.action_results[1]["status"] == "success"


async def test_unknown_hardware_inhibits_next_hardware_but_allows_safe_activation(monkeypatch):
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls, accepted=False, delivery=CommandDelivery.UNKNOWN)
    identity = await reserve(service, await seed([action(), action("gate.open", "second"), action("notification.disable", "notice")]))
    await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert len(calls) == 1 and row.status == "review_required" and row.review_reason
    assert [item["state"] for item in row.action_plan] == ["unknown", "skipped", "succeeded"]
    assert row.action_results[2]["status"] == "success"


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









async def test_exhausted_one_shot_schedule_stays_enabled_without_future_occurrence_or_self_cancellation():
    service, rule_id = automations.AutomationService(), await seed([action("notification.disable")])
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
    assert run.action_plan[0]["state"] == "succeeded"
