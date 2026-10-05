"""Automatic garage output recovery; refused outside the isolated PG namespace."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal
from app.models import AccessDeviceCommandRecord, AccessEvent, AuditLog, NotificationRun
from app.modules.gate.base import CommandDelivery, GateState
from app.services.access.delivery import recover_garage_outcome_outputs, reserve_garage_outcome_outputs
from app.services.access_device_commands import AccessDeviceCommandJournal, device_command_receipt
from app.services.access_devices import AccessDeviceService
from app.services.notification_runs import NotificationRunStore
from test_movement_admission import movement, person

pytestmark = pytest.mark.asyncio


async def garage_operation(state="unknown", *, attributed=True):
    event_id, _saga = await movement(await person())
    device_id = uuid.uuid4()
    target = {"target_device_id": str(device_id), "device_key": f"synthetic_garage_{device_id.hex[:8]}",
        "kind": "garage_door", "binding_fingerprint": "a" * 64,
        "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": "cover.synthetic_garage"}]}}
    origin = {"kind": "automatic_access_garage", "access_event_id": str(event_id), "target_label": "Synthetic garage"}
    intent = str(uuid.uuid5(event_id, f"automatic-garage-open:{device_id}"))
    operation_key = f"garage-command:open:{device_id}:event:{event_id}"
    journal = AccessDeviceCommandJournal()
    async with AsyncSessionLocal() as session:
        claim = await journal.claim(session, target=target, action="open", intent_id=intent,
            operation_key=operation_key, gate_command_id=None, expires_at=None,
            origin_context=origin if attributed else None)
        await session.commit()
    if state == "verified_without_send":
        await journal.finish_without_send(claim, detail="Synthetic garage is already open.",
            observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)})
    elif state != "prepared":
        async with AsyncSessionLocal() as session:
            assert await journal.begin_attempt(session, claim, provider="home_assistant", external_id="cover.synthetic_garage")
            await session.commit()
        if state != "attempting":
            await journal.finish_attempt(claim,
                delivery=CommandDelivery.ACCEPTED if state == "verified_accepted" else CommandDelivery(state),
                state=GateState.UNKNOWN, detail="Synthetic provider receipt",
                observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)}
                    if state == "verified_accepted" else None)
    return SimpleNamespace(event_id=event_id, target=target, origin=origin, intent=intent,
                           operation_key=operation_key, claim=claim)


async def expire_operation(identity):
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, identity)
        row.lease_expires_at = await session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
        await session.commit()


@pytest.mark.parametrize("state,delivery,notice", [
    ("unknown", "unknown", True), ("accepted", "accepted", False),
    ("rejected", "rejected", True), ("not_sent", "not_sent", True),
    ("verified_without_send", "not_sent", False), ("verified_accepted", "accepted", False),
])
async def test_crash_after_receipt_recovers_truthful_garage_outputs_once(state, delivery, notice):
    operation = await garage_operation(state)
    assert await recover_garage_outcome_outputs() == 1
    assert await recover_garage_outcome_outputs() == 0
    async with AsyncSessionLocal() as session:
        assert await reserve_garage_outcome_outputs(session, command_id=operation.claim.record.id) is False
        row = await session.get(AccessDeviceCommandRecord, operation.claim.record.id)
        assert row.outcome_recorded_at is not None
        assert row.origin_context == operation.origin
        assert device_command_receipt(row)["delivery"] == delivery
        if state == "unknown":
            assert row.state == "unknown" and row.accepted is None
        audit = await session.get(AuditLog, uuid.uuid5(row.id, "access.garage.outcome.audit"))
        assert audit.action == "garage_door.open.automatic" and audit.outcome == delivery
        assert audit.metadata_["origin_resolution"] == "retained_event"
        assert audit.metadata_["command_id"] == str(row.id)
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1
        notices = list((await session.scalars(select(NotificationRun))).all())
        assert len(notices) == int(notice)
        if notice:
            assert notices[0].id == uuid.uuid5(row.id, "access.garage.outcome.notification")
            assert notices[0].trigger_event == "garage_door_open_failed"
            assert notices[0].context["facts"]["delivery"] == delivery
            if state == "unknown":
                assert "uncertain" in notices[0].context["facts"]["message"]


async def test_concurrent_inline_completion_and_recovery_capture_one_output():
    operation = await garage_operation()

    async def inline():
        async with AsyncSessionLocal() as session:
            captured = await reserve_garage_outcome_outputs(session, command_id=operation.claim.record.id)
            await session.commit()
            return int(captured)

    results = await asyncio.wait_for(asyncio.gather(inline(), recover_garage_outcome_outputs()), 8)
    assert sum(results) == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 1


async def test_failed_notification_reservation_rolls_back_audit_and_output_checkpoint(monkeypatch):
    operation = await garage_operation()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic notification reservation fault")

    with monkeypatch.context() as patch:
        patch.setattr(NotificationRunStore, "enqueue_in_session", fail)
        with pytest.raises(RuntimeError, match="synthetic notification"):
            await recover_garage_outcome_outputs()
    async with AsyncSessionLocal() as session:
        assert (await session.get(AccessDeviceCommandRecord, operation.claim.record.id)).outcome_recorded_at is None
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 0
    assert await recover_garage_outcome_outputs() == 1


@pytest.mark.parametrize("state", ["prepared", "attempting"])
async def test_active_command_lease_is_neither_expired_nor_reported_by_delivery(state):
    operation = await garage_operation(state)
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, operation.claim.record.id)
        before = row.state, row.lease_token, row.lease_expires_at, row.updated_at
        assert await reserve_garage_outcome_outputs(session, command_id=row.id) is False
        await session.commit()
    assert await recover_garage_outcome_outputs() == 0
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, operation.claim.record.id)
        assert (row.state, row.lease_token, row.lease_expires_at, row.updated_at) == before
        assert row.outcome_recorded_at is None
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.parametrize("state,retained", [("prepared", "not_sent"), ("attempting", "unknown")])
async def test_expired_operation_is_reported_with_retained_delivery_certainty(state, retained):
    operation = await garage_operation(state)
    await expire_operation(operation.claim.record.id)
    assert await recover_garage_outcome_outputs() == 1
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, operation.claim.record.id)
        assert row.state == retained and row.outcome_recorded_at is not None
        assert row.accepted is (False if retained == "not_sent" else None)
        assert row.lease_token is None and row.lease_expires_at is None


async def test_active_and_locked_older_commands_do_not_starve_ready_output():
    active = await garage_operation("prepared")
    locked = await garage_operation()
    ready = await garage_operation()
    async with AsyncSessionLocal() as holder:
        await holder.get(AccessDeviceCommandRecord, locked.claim.record.id, with_for_update=True)
        assert await asyncio.wait_for(recover_garage_outcome_outputs(limit=1), 8) == 1
        async with AsyncSessionLocal() as reader:
            assert (await reader.get(AccessDeviceCommandRecord, ready.claim.record.id)).outcome_recorded_at is not None
            assert (await reader.get(AccessDeviceCommandRecord, active.claim.record.id)).outcome_recorded_at is None
        await holder.rollback()
    assert await recover_garage_outcome_outputs(limit=1) == 1


async def test_late_physical_evidence_preserves_initial_review_notice_without_duplicate():
    operation = await garage_operation()
    assert await recover_garage_outcome_outputs() == 1
    async with AsyncSessionLocal() as session:
        captured_at = (await session.get(AccessDeviceCommandRecord, operation.claim.record.id)).outcome_recorded_at
    row = await AccessDeviceCommandJournal().reconcile_observation(operation.claim.record.id,
        binding_fingerprint=operation.target["binding_fingerprint"],
        observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)})
    assert row.state == "verified" and row.accepted is None
    assert await recover_garage_outcome_outputs() == 0
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, row.id)
        assert row.outcome_recorded_at == captured_at
        audit = await session.get(AuditLog, uuid.uuid5(row.id, "access.garage.outcome.audit"))
        assert audit.outcome == "unknown" and audit.metadata_["verified"] is False
        assert device_command_receipt(row)["verified"] is True
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == 1


@pytest.mark.parametrize("state,notice", [("unknown", True), ("verified_without_send", False)])
async def test_missing_event_retains_explicit_review_audit_without_false_delivery(state, notice):
    operation = await garage_operation(state)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AccessEvent).where(AccessEvent.id == operation.event_id))
        await session.commit()
    assert await recover_garage_outcome_outputs() == 1
    assert await recover_garage_outcome_outputs() == 0
    async with AsyncSessionLocal() as session:
        audit = await session.get(AuditLog, uuid.uuid5(operation.claim.record.id, "access.garage.outcome.audit"))
        assert audit.metadata_["review_reason"] == "origin_event_missing" and audit.level == "warning"
        assert audit.metadata_["access_event_id"] == str(operation.event_id)
        assert audit.outcome == ("unknown" if notice else "not_sent")
        assert await session.scalar(select(func.count()).select_from(NotificationRun)) == int(notice)


async def test_historical_unattributed_journal_is_not_reconstructed_or_reported():
    operation = await garage_operation(attributed=False)
    assert await recover_garage_outcome_outputs() == 0
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, operation.claim.record.id)
        assert row.origin_context is None and row.outcome_recorded_at is None
        assert row.state == "unknown"
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_origin_cannot_be_changed_or_added_to_retained_operation():
    operation = await garage_operation()
    journal = AccessDeviceCommandJournal()
    for context in ({**operation.origin, "target_label": "Different synthetic label"}, None):
        with pytest.raises(ValueError, match="cannot be rebound"):
            await journal.replay(operation_key=operation.operation_key,
                target_id=operation.target["target_device_id"], action="open", intent_id=operation.intent,
                binding_fingerprint=operation.target["binding_fingerprint"], origin_context=context)
    legacy = await garage_operation(attributed=False)
    with pytest.raises(ValueError, match="cannot be rebound"):
        await journal.replay(operation_key=legacy.operation_key, target_id=legacy.target["target_device_id"],
            action="open", intent_id=legacy.intent, binding_fingerprint=legacy.target["binding_fingerprint"],
            origin_context=legacy.origin)


@pytest.mark.parametrize("mutation", ["event", "extra_key", "kind", "blank_label", "long_label"])
async def test_invalid_or_unbound_origin_is_rejected_before_any_claim(mutation):
    event_id, target_id = uuid.uuid4(), uuid.uuid4()
    origin = {"kind": "automatic_access_garage", "access_event_id": str(event_id), "target_label": "Synthetic garage"}
    if mutation == "event":
        origin["access_event_id"] = str(uuid.uuid4())
    elif mutation == "extra_key":
        origin["untrusted_extra"] = "synthetic"
    elif mutation == "kind":
        origin["kind"] = "manual"
    else:
        origin["target_label"] = "" if mutation == "blank_label" else "x" * 161
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError):
            await AccessDeviceCommandJournal().claim(session,
                target={"target_device_id": str(target_id), "kind": "garage_door"}, action="open",
                intent_id=str(uuid.uuid5(event_id, f"automatic-garage-open:{target_id}")),
                operation_key=f"garage-command:open:{target_id}:event:{event_id}",
                gate_command_id=None, expires_at=None, origin_context=origin)
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(AccessDeviceCommandRecord)) == 0


async def test_origin_on_service_requires_current_access_authorization_participant():
    with pytest.raises(ValueError, match="authorization participant"):
        await AccessDeviceService().command_device("synthetic_garage", "open", "Synthetic",
            intent_id="synthetic", idempotency_key="synthetic", origin_context={})
