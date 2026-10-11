from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import GateCommandRecord, MovementSagaRecord
from app.models.enums import (
    GateCommandState,
    MovementSagaState,
)
from app.modules.gate.base import GateState
from app.services.movement_reconciliation import (
    MovementReconciliationService,
    _latest_reconciliation_command,
)


class FakeFlushSession:
    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


class FakeClockSession(FakeFlushSession):
    async def scalar(self, _statement):
        return datetime.now(tz=UTC)


def test_latest_reconciliation_command_includes_accepted_unverified_command() -> None:
    verified = GateCommandRecord(
        idempotency_key="verified",
        source="test",
        gate_key="default",
        controller="fake",
        reason="verified",
        state=GateCommandState.ACCEPTED,
        mechanically_confirmed=True,
    )
    unverified = GateCommandRecord(
        idempotency_key="unverified",
        source="test",
        gate_key="default",
        controller="fake",
        reason="unverified",
        state=GateCommandState.ACCEPTED,
        mechanically_confirmed=False,
    )

    assert _latest_reconciliation_command([verified, unverified]) is unverified


@pytest.mark.asyncio
async def test_reconcile_stale_leased_command_holds_unknown_when_gate_stays_closed(monkeypatch) -> None:
    service = MovementReconciliationService()
    now = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)
    command = GateCommandRecord(
        idempotency_key="leased",
        source="test",
        gate_key="default",
        controller="fake",
        reason="lease",
        state=GateCommandState.LEASED,
        lease_token="token",
        lease_expires_at=now - timedelta(minutes=5),
        mechanically_confirmed=False,
    )
    saga = MovementSagaRecord(
        idempotency_key="movement",
        source="test",
        occurred_at=now - timedelta(minutes=6),
        state=MovementSagaState.PHYSICAL_COMMAND_PENDING,
        intent_payload={},
        decision_payload={},
        state_history=[],
        gate_commands=[command],
    )
    saga.created_at = now - timedelta(minutes=6)
    saga.updated_at = now - timedelta(minutes=5)
    command.updated_at = now - timedelta(minutes=5)
    published: list[tuple[str, str]] = []

    async def fake_current_gate_state():
        return GateState.CLOSED

    async def fake_gate_open_observation_after_command(_session, _command):
        return None

    async def fake_publish_failed(row, detail):
        published.append((str(row.id), detail))

    monkeypatch.setattr(service, "_publish_saga_failed", fake_publish_failed)

    count = await service._reconcile_saga(FakeFlushSession(), saga, ha_reservations=[])

    assert count == 1
    assert command.state == GateCommandState.RECONCILIATION_REQUIRED
    assert command.requires_reconciliation is True
    assert command.accepted is None
    assert command.command_metadata["delivery"] == "unknown"
    assert command.lease_token is None
    assert saga.state == MovementSagaState.RECONCILIATION_REQUIRED
    assert saga.reconciliation_required is True
    assert published == []


@pytest.mark.asyncio
async def test_historical_command_without_target_journal_remains_held(monkeypatch) -> None:
    service = MovementReconciliationService()
    command = GateCommandRecord(id=uuid4(), idempotency_key="historical-no-target", source="test",
        gate_key="default", controller="configured", reason="historical", accepted=True,
        state=GateCommandState.RECONCILIATION_REQUIRED, requires_reconciliation=True,
        mechanically_confirmed=False, completed_at=datetime.now(tz=UTC) - timedelta(days=2))
    # Elapsed time and today's configured gate cannot establish this old command's
    # physical identity. The removal of timeout-to-failure is deliberate.
    assert await service._reconcile_standalone_gate_command(FakeFlushSession(), command) == 0
    assert command.state == GateCommandState.RECONCILIATION_REQUIRED
    assert command.requires_reconciliation is True
    assert command.mechanically_confirmed is False


@pytest.mark.asyncio
async def test_reconciliation_projects_admission_separately_from_other_targets(monkeypatch) -> None:
    from app.services.access_device_commands import AccessDeviceCommandJournal

    command = GateCommandRecord(id=uuid4(), idempotency_key="journal-targets", source="test",
        gate_key="default", controller="configured", reason="journal", accepted=False,
        state=GateCommandState.RECONCILIATION_REQUIRED, requires_reconciliation=True,
        mechanically_confirmed=False, command_metadata={"recovery_version": 2, "target_plan": {"version": 1}})
    projection = {"target_receipts": [{"device_key": "entry", "verified": True},
                                      {"device_key": "secondary", "verified": False}],
                  "admission_verified": True, "mechanically_confirmed": False,
                  "accepted": False, "delivery": "unknown", "requires_reconciliation": True, "state": "open"}

    async def projected(_self, _session, _parent, **_kwargs):
        return projection

    monkeypatch.setattr(AccessDeviceCommandJournal, "gate_command_projection", projected)
    assert await AccessDeviceCommandJournal().reconcile_parent_in_session(FakeClockSession(), command) == projection
    assert command.command_metadata["admission_verified"] is True
    assert command.accepted is False
    assert command.mechanically_confirmed is False
    assert command.requires_reconciliation is True
    assert command.state == GateCommandState.RECONCILIATION_REQUIRED


@pytest.mark.asyncio
async def test_verified_unknown_receipt_does_not_fabricate_provider_acceptance(monkeypatch) -> None:
    from app.services.access_device_commands import AccessDeviceCommandJournal

    command = GateCommandRecord(id=uuid4(), idempotency_key="verified-unknown", source="test",
        gate_key="default", controller="configured", reason="journal", accepted=False,
        state=GateCommandState.RECONCILIATION_REQUIRED, requires_reconciliation=True,
        mechanically_confirmed=False, command_metadata={"recovery_version": 2, "target_plan": {"version": 1}})

    async def projected(_self, _session, _parent, **_kwargs):
        return {"target_receipts": [], "admission_verified": True, "mechanically_confirmed": True,
                "accepted": False, "delivery": "unknown", "requires_reconciliation": False, "state": "open"}

    monkeypatch.setattr(AccessDeviceCommandJournal, "gate_command_projection", projected)
    assert await AccessDeviceCommandJournal().reconcile_parent_in_session(FakeClockSession(), command) is not None
    assert command.accepted is False
    assert command.requires_reconciliation is False
    assert command.mechanically_confirmed is True
    assert command.state == GateCommandState.RECONCILED


@pytest.mark.asyncio
async def test_recent_pending_saga_without_command_waits_for_grace_period() -> None:
    service = MovementReconciliationService()
    now = datetime.now(tz=UTC)
    saga = MovementSagaRecord(
        idempotency_key="movement-pending",
        source="test",
        occurred_at=now,
        state=MovementSagaState.PHYSICAL_COMMAND_PENDING,
        intent_payload={},
        decision_payload={},
        state_history=[],
        gate_commands=[],
    )
    saga.created_at = now
    saga.updated_at = now

    count = await service._reconcile_saga(FakeFlushSession(), saga, ha_reservations=[])

    assert count == 0
    assert saga.state == MovementSagaState.PHYSICAL_COMMAND_PENDING
