"""Recognition ordering against real isolated PG owners and inert gate sinks.

The imported guard runs before application imports. No provider, live settings,
worker lifecycle or wall-clock sleep is used. Synthetic parent states represent
core pauses; child receipts and admission classifications use their real owners.
"""
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
from app.models import AccessEvent, AccessDeviceCommandRecord, AutomationRule, AutomationRun, GateCommandRecord, LprIngestEvent, MovementSagaRecord, NotificationRun, Vehicle
from app.models.enums import AccessDecision, AccessDirection, GateCommandState, MovementSagaState
from app.modules.gate.base import CommandDelivery, GateState
from app.services import automation_intake, automations
from app.services.access import authorization
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.automation_execution import AutomationActionWait, AutomationClaimLost
from app.services.movement.admission import finalize_in_session
from test_automation_dispatch import action, gate_sink, reserve, seed

pytestmark = pytest.mark.asyncio


def target(key):
    return {"target_device_id": str(uuid.uuid4()), "device_key": key, "kind": "gate",
        "binding_fingerprint": "a" * 64,
        "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": f"cover.{key}"}]}}


@pytest_asyncio.fixture(autouse=True)
async def inert_configuration(monkeypatch, isolated_resources):
    from app.services import automation_integration_actions

    monkeypatch.setattr(authorization, "get_runtime_config_for_session", AsyncMock(return_value=SimpleNamespace(
        site_timezone="Europe/London", schedule_default_policy="allow")))
    monkeypatch.setattr(automation_integration_actions, "integration_action_status",
        AsyncMock(return_value=SimpleNamespace(enabled=True, disabled_reason=None)))
    previews = []

    async def preview(**kwargs):
        previews.append({key: value for key, value in kwargs.items() if key != "session"})
        selected = target("synthetic_rule_gate")
        return {"version": 1, "action": "open", "targets": [selected],
                "admission_target_device_id": selected["target_device_id"]}

    monkeypatch.setattr(automation_intake, "AccessDeviceConfiguration",
        lambda: SimpleNamespace(preview_gate_open=preview))
    return previews


async def origin(*, notice_first=False, trigger="vehicle.known_plate", decision=AccessDecision.GRANTED,
                 saga_present=True):
    """Origin and occurrence become visible atomically, before any core command."""
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        vehicle = Vehicle(registration_number="SYNORDER", is_active=True)
        session.add(vehicle)
        await session.flush()
        event = AccessEvent(vehicle_id=vehicle.id, registration_number=vehicle.registration_number,
            direction=AccessDirection.ENTRY, decision=decision, source="synthetic_ordering",
            confidence=1, occurred_at=now, raw_payload={})
        session.add(event)
        await session.flush()
        session.add(LprIngestEvent(idempotency_key=f"synthetic:{event.id}", source=event.source,
            registration_number=vehicle.registration_number, captured_at=now, received_at=now,
            access_event_id=event.id, status="completed", normalized_payload={}))
        saga = None
        if saga_present:
            saga = MovementSagaRecord(idempotency_key=f"synthetic:{event.id}", source=event.source,
                access_event_id=event.id, vehicle_id=vehicle.id, registration_number=vehicle.registration_number,
                occurred_at=now, direction=event.direction, decision=decision,
                state=MovementSagaState.PHYSICAL_COMMAND_PENDING, gate_command_required=True,
                admission_status="pending", reconciliation_required=True)
            session.add(saga)
        hardware, notice = action(), action("integration.whatsapp.send_message", "notice")
        rule = AutomationRule(name="Synthetic admission ordering", is_active=True,
            triggers=[{"type": trigger, "config": {}}], trigger_keys=[trigger], conditions=[],
            actions=[notice, hardware] if notice_first else [hardware, notice])
        session.add(rule)
        await session.flush()
        runs = await automation_intake.reserve_trigger(session, trigger, {
            "access_event_id": str(event.id), "vehicle_id": str(vehicle.id),
            "registration_number": vehicle.registration_number, "decision": decision.value,
            "direction": "entry", "occurred_at": now.isoformat()},
            origin_kind="access_event.finalized", origin_id=str(event.id))
        assert len(runs) == 1
        await session.commit()
        return SimpleNamespace(event_id=event.id, saga_id=saga.id if saga else None,
            run_id=runs[0], rule_id=rule.id, observed_at=now)


async def primary_receipts(origin, states, *, leased=False, finalize=True):
    """Create exact core identity and actual journal receipts without providers."""
    journal = AccessDeviceCommandJournal()
    targets = [target(f"synthetic_primary_{index}") for index in range(len(states))]
    intent = str(uuid.uuid5(origin.event_id, "automatic-gate-open"))
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        parent = GateCommandRecord(idempotency_key=f"gate-command:open:default:event:{origin.event_id}",
            movement_saga_id=origin.saga_id, access_event_id=origin.event_id,
            source="synthetic_core", controller="configured", reason="Synthetic primary admission",
            state=GateCommandState.LEASED, lease_token=uuid.uuid4().hex,
            leased_at=now, lease_expires_at=now + timedelta(seconds=120), started_at=now,
            command_metadata={"intent_id": intent, "recovery_version": 2,
                "target_plan": {"targets": targets, "admission_target_device_id": targets[0]["target_device_id"]}})
        session.add(parent)
        await session.commit()
    for selected, state in zip(targets, states):
        if state == "missing":
            continue
        async with AsyncSessionLocal() as session:
            claim = await journal.claim(session, target=selected, action="open", intent_id=intent,
                operation_key=parent.idempotency_key, gate_command_id=str(parent.id), expires_at=None)
            await session.commit()
        if state == "prepared":
            continue
        if state == "not_sent":
            await journal.finish_without_send(claim, detail="Synthetic definite no-send")
            continue
        async with AsyncSessionLocal() as session:
            assert await journal.begin_attempt(session, claim, provider="home_assistant",
                external_id=selected["binding_snapshot"]["providers"][0]["external_id"])
            await session.commit()
        if state == "attempting":
            continue
        async with AsyncSessionLocal() as session:
            observed = await session.scalar(select(func.clock_timestamp()))
        await journal.finish_attempt(claim,
            delivery=CommandDelivery.ACCEPTED if state == "verified" else CommandDelivery(state),
            state=GateState.UNKNOWN, detail="Synthetic controller receipt",
            observation={"provider": "home_assistant", "state": "open", "observed_at": observed} if state == "verified" else None)
    async with AsyncSessionLocal() as session:
        current = await session.get(GateCommandRecord, parent.id)
        if not leased:
            projection = await journal.gate_command_projection(session, current)
            current.state = GateCommandState.RECONCILIATION_REQUIRED if projection["requires_reconciliation"] else (
                GateCommandState.ACCEPTED if projection["accepted"] else GateCommandState.REJECTED)
            current.requires_reconciliation = projection["requires_reconciliation"]
            current.completed_at = await session.scalar(select(func.clock_timestamp()))
            current.lease_token = current.lease_expires_at = None
        if finalize:
            await finalize_in_session(session, saga_id=origin.saga_id)
        await session.commit()
    return parent.id


async def make_due(identity):
    # Advance only the synthetic queue eligibility clock; never reset an attempt.
    async with AsyncSessionLocal() as session:
        await session.execute(update(AutomationRun).where(AutomationRun.id == identity,
            AutomationRun.status == "queued", AutomationRun.claim_token.is_(None))
            .values(lease_expires_at=text("clock_timestamp() - interval '1 microsecond'")))
        await session.commit()


@pytest.mark.parametrize("notice_first", [False, True])
async def test_origin_commit_cannot_race_primary_command_and_restart_preserves_order(monkeypatch, notice_first, inert_configuration):
    committed, release = asyncio.Event(), asyncio.Event()
    state = {}

    async def core():
        state["origin"] = await origin(notice_first=notice_first)
        committed.set()
        await _bounded(release.wait())
        state["parent"] = await primary_receipts(state["origin"], ["verified", "rejected"])

    task = asyncio.create_task(core())
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    try:
        await _bounded(committed.wait())
        item = state["origin"]
        assert await service.dispatcher.run_once(item.run_id)
        waiting = await service.run_store.get(item.run_id)
        hardware = next(part for part in waiting.action_plan if part["action"]["type"] == "gate.open")
        assert waiting.status == "queued" and waiting.claim_token is None
        assert hardware["state"] == "pending" and not hardware.get("attempted_at")
        assert hardware["wait_reason"] == "primary_admission_not_settled"
        assert hardware["automatic_entry_policy"] is True
        assert [part["state"] for part in waiting.action_plan] == (["succeeded", "pending"] if notice_first else ["pending", "pending"])
        assert not calls
        for _ in range(3):
            assert await automations.AutomationService().dispatcher.run_once(item.run_id) is False
        unchanged = await service.run_store.get(item.run_id)
        assert unchanged.claim_count == waiting.claim_count and unchanged.queued_at == waiting.queued_at
    finally:
        release.set()
        await _bounded(task)
    await make_due(item.run_id)
    assert await automations.AutomationService().dispatcher.run_once(item.run_id)
    finished = await service.run_store.get(item.run_id)
    assert finished.status == "success" and all(part["state"] == "succeeded" for part in finished.action_plan)
    assert len(calls) == 1 and calls[0].automatic_entry_policy is True
    assert calls[0].intent_id == hardware["operation_id"]
    assert calls[0].intent_id != str(uuid.uuid5(item.event_id, "automatic-gate-open"))
    assert calls[0].expires_at == item.observed_at + timedelta(seconds=60)
    assert await service.dispatcher.run_once(item.run_id) is False
    assert finished.queued_at == waiting.queued_at
    assert inert_configuration[0]["automatic_entry_policy"] is True


@pytest.mark.parametrize("states,leased", [(["accepted"], False), (["unknown"], False),
    (["attempting"], True), (["prepared"], True), (["verified", "missing"], True),
    (["verified", "unknown"], False), (["verified", "accepted"], False), (["verified"], True)])
async def test_any_unsettled_primary_stage_holds_hardware_and_later_notice(monkeypatch, states, leased):
    item, calls, service = await origin(), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    await primary_receipts(item, states, leased=leased)
    assert await service.dispatcher.run_once(item.run_id)
    waiting = await service.run_store.get(item.run_id)
    assert waiting.status == "queued" and [part["state"] for part in waiting.action_plan] == ["pending", "pending"]
    assert waiting.action_plan[0]["wait_reason"].startswith("primary_")
    assert not waiting.action_plan[0].get("attempted_at") and not calls


@pytest.mark.parametrize("state", ["verified", "rejected", "not_sent"])
async def test_definitively_settled_primary_allows_separate_rule_intent(monkeypatch, state):
    item, calls, service = await origin(), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    parent_id = await primary_receipts(item, [state])
    assert await service.dispatcher.run_once(item.run_id)
    run = await service.run_store.get(item.run_id)
    assert run.status == "success" and len(calls) == 1
    assert calls[0].command_id != str(parent_id) and calls[0].automatic_entry_policy is True
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        assert parent.idempotency_key == f"gate-command:open:default:event:{item.event_id}"
        assert parent.command_metadata["intent_id"] != calls[0].intent_id


@pytest.mark.parametrize("settled", [False, True])
async def test_exact_sixty_seconds_retains_policy_and_later_is_reviewable_skip(settled):
    item, service = await origin(), automations.AutomationService()
    if settled:
        await primary_receipts(item, ["verified"])
    async with AsyncSessionLocal() as session:
        run, rule = await session.get(AutomationRun, item.run_id), await session.get(AutomationRule, item.rule_id)
        deadlines = {}
        at_boundary = await service._preflight(session, run, run.action_plan[0], rule=rule,
            now=item.observed_at + timedelta(seconds=60), deadlines=deadlines)
        assert (at_boundary is None) if settled else isinstance(at_boundary, AutomationActionWait)
        assert deadlines["expires_at"] == item.observed_at + timedelta(seconds=60)
        expired = await service._preflight(session, run, run.action_plan[0], rule=rule,
            now=item.observed_at + timedelta(seconds=60, microseconds=1))
        assert expired["reason_code"] == "recognition_hardware_expired"
        assert expired["command_sent"] is False and expired["requires_review"] is True


async def test_expired_wait_skips_unattempted_hardware_then_hands_off_notice(monkeypatch):
    item, calls, service = await origin(), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    await service.dispatcher.run_once(item.run_id)
    queued_at = (await service.run_store.get(item.run_id)).queued_at
    async with AsyncSessionLocal() as session:
        old = await session.scalar(select(func.clock_timestamp())) - timedelta(seconds=61)
        await session.execute(update(AccessEvent).where(AccessEvent.id == item.event_id).values(occurred_at=old))
        await session.execute(update(LprIngestEvent).where(LprIngestEvent.access_event_id == item.event_id)
            .values(captured_at=old, received_at=old))
        await session.commit()
    await make_due(item.run_id)
    assert await automations.AutomationService().dispatcher.run_once(item.run_id)
    run = await service.run_store.get(item.run_id)
    assert [part["state"] for part in run.action_plan] == ["skipped", "succeeded"]
    assert run.status == "review_required" and run.review_reason == "recognition_hardware_expired"
    assert not calls and not run.action_plan[0].get("attempted_at") and run.queued_at == queued_at
    async with AsyncSessionLocal() as session:
        notice = await session.get(NotificationRun, uuid.UUID(run.action_results[1]["notification_run_id"]))
        assert notice.status == "queued" and notice.claim_token is None


async def test_waiting_run_does_not_starve_other_due_rule(monkeypatch):
    item, calls, service = await origin(), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    await service.dispatcher.run_once(item.run_id)
    independent = await reserve(service, await seed())
    assert await service.dispatcher.run_once()
    assert (await service.run_store.get(independent)).status == "success"
    assert (await service.run_store.get(item.run_id)).status == "queued"
    assert len(calls) == 1 and calls[0].automatic_entry_policy is False


async def test_attempted_hardware_cannot_be_deferred_or_reset():
    item, service = await origin(), automations.AutomationService()
    claimed = await service.run_store.claim(item.run_id)
    async with AsyncSessionLocal() as session:
        await service.run_store.begin_action(session, item.run_id, claimed.claim_token, 0)
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(AutomationClaimLost):
            await service.run_store.defer_action(session, item.run_id, claimed.claim_token, 0,
                AutomationActionWait("synthetic", item.observed_at + timedelta(seconds=5),
                    item.observed_at + timedelta(seconds=60)))
        await session.rollback()
    current = await service.run_store.get(item.run_id)
    assert current.action_plan[0]["state"] == "attempting" and current.action_plan[0]["attempted_at"]


@pytest.mark.parametrize("state", ["unknown", "accepted", "missing"])
async def test_stale_terminal_parent_summary_cannot_hide_unsettled_target(monkeypatch, state):
    item, calls, service = await origin(), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    parent_id = await primary_receipts(item, ["verified", state], finalize=False)
    async with AsyncSessionLocal() as session:
        # Contradictory stored summary: the read guard must still consult each
        # actual child, and must never materialize a missing no-send receipt.
        saga = await session.get(MovementSagaRecord, item.saga_id)
        saga.admission_status, saga.reconciliation_required = "verified", False
        parent = await session.get(GateCommandRecord, parent_id)
        parent.state, parent.requires_reconciliation = GateCommandState.ACCEPTED, False
        before = await session.scalar(select(func.count()).select_from(AccessDeviceCommandRecord))
        await session.commit()
    await service.dispatcher.run_once(item.run_id)
    current = await service.run_store.get(item.run_id)
    assert current.action_plan[0]["state"] == "pending"
    assert current.action_plan[0]["wait_reason"] == "primary_gate_targets_not_settled" and not calls
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AccessDeviceCommandRecord)) == before


async def test_missing_primary_saga_is_pending_not_permission_to_send(monkeypatch):
    item, calls, service = await origin(saga_present=False), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    await service.dispatcher.run_once(item.run_id)
    current = await service.run_store.get(item.run_id)
    assert current.status == "queued" and not calls
    assert current.action_plan[0]["wait_reason"] == "primary_admission_not_settled"


@pytest.mark.parametrize("trigger", ["time.every_x", "webhook.received"])
async def test_nonrecognition_standing_rule_does_not_acquire_entry_policy(monkeypatch, trigger, inert_configuration):
    calls, service = [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    identity = await reserve(service, await seed(trigger=trigger))
    assert await service.dispatcher.run_once(identity)
    assert (await service.run_store.get(identity)).status == "success" and len(calls) == 1
    assert calls[0].automatic_entry_policy is False
    assert inert_configuration[0]["automatic_entry_policy"] is False


async def test_outside_schedule_denial_does_not_wait_for_nonexistent_primary(monkeypatch):
    item, calls, service = await origin(trigger="vehicle.outside_schedule", decision=AccessDecision.DENIED), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    assert await service.dispatcher.run_once(item.run_id)
    assert (await service.run_store.get(item.run_id)).status == "success" and len(calls) == 1
    assert calls[0].automatic_entry_policy is True


async def test_old_recognition_plan_is_withheld_instead_of_reinterpreted(monkeypatch):
    item, calls, service = await origin(), [], automations.AutomationService()
    gate_sink(monkeypatch, calls)
    await primary_receipts(item, ["verified"])
    async with AsyncSessionLocal() as session:
        row = await session.get(AutomationRun, item.run_id)
        plan = copy.deepcopy(row.action_plan)
        plan[0].pop("automatic_entry_policy")
        row.action_plan = plan
        await session.commit()
    await service.dispatcher.run_once(item.run_id)
    run = await service.run_store.get(item.run_id)
    assert not calls and run.status == "review_required"
    assert run.action_results[0]["reason_code"] == "recognition_entry_policy_not_captured"
    assert [part["state"] for part in run.action_plan] == ["skipped", "succeeded"]
