from datetime import UTC, datetime, timedelta

import pytest

from app.models import GateCommandRecord, GateStateObservation, MovementSagaRecord
from app.models.enums import GateCommandState, MovementSagaState
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
async def test_reconcile_stale_leased_command_marks_failed_when_gate_stays_closed(monkeypatch) -> None:
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

    async def fake_notify(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_current_gate_state", fake_current_gate_state)
    monkeypatch.setattr(service, "_gate_open_observation_after_command", fake_gate_open_observation_after_command)
    monkeypatch.setattr(service, "_publish_saga_failed", fake_publish_failed)
    monkeypatch.setattr(service, "_notify_reconciliation_failure", fake_notify)

    count = await service._reconcile_saga(FakeFlushSession(), saga)

    assert count == 1
    assert command.state == GateCommandState.FAILED
    assert command.requires_reconciliation is False
    assert command.lease_token is None
    assert saga.state == MovementSagaState.FAILED
    assert saga.reconciliation_required is False
    assert published


@pytest.mark.asyncio
async def test_reconcile_uses_open_observation_after_command_even_if_current_gate_closed(monkeypatch) -> None:
    service = MovementReconciliationService()
    now = datetime(2026, 5, 15, 18, 0, tzinfo=UTC)
    command = GateCommandRecord(
        idempotency_key="accepted-unverified",
        source="test",
        gate_key="default",
        controller="fake",
        reason="automatic lpr",
        state=GateCommandState.RECONCILIATION_REQUIRED,
        accepted=True,
        requires_reconciliation=True,
        mechanically_confirmed=False,
        started_at=now - timedelta(seconds=70),
        completed_at=now - timedelta(seconds=69),
    )
    saga = MovementSagaRecord(
        idempotency_key="movement-observed-open",
        source="test",
        occurred_at=now - timedelta(seconds=70),
        state=MovementSagaState.RECONCILIATION_REQUIRED,
        reconciliation_required=True,
        intent_payload={},
        decision_payload={},
        state_history=[],
        gate_commands=[command],
    )
    command.updated_at = now - timedelta(seconds=69)
    saga.created_at = now - timedelta(seconds=70)
    saga.updated_at = now - timedelta(seconds=69)
    observation = GateStateObservation(
        gate_entity_id="cover.top_gate",
        gate_name="Top Gate",
        state=GateState.OPEN.value,
        raw_state="open",
        previous_state=GateState.CLOSED.value,
        observed_at=now - timedelta(seconds=68),
        source="home_assistant_websocket",
    )
    published: list[tuple[str, str]] = []

    async def fake_gate_open_observation_after_command(_session, _command):
        return observation

    async def fake_current_gate_state():
        return GateState.CLOSED

    async def fake_publish_reconciled(row, command_row, state):
        published.append((str(row.id), state.value))

    monkeypatch.setattr(service, "_gate_open_observation_after_command", fake_gate_open_observation_after_command)
    monkeypatch.setattr(service, "_current_gate_state", fake_current_gate_state)
    monkeypatch.setattr(service, "_publish_reconciled", fake_publish_reconciled)

    count = await service._reconcile_saga(FakeFlushSession(), saga)

    assert count == 1
    assert command.state == GateCommandState.RECONCILED
    assert command.requires_reconciliation is False
    assert command.mechanically_confirmed is True
    assert saga.state == MovementSagaState.COMPLETED
    assert saga.reconciliation_required is False
    assert "Gate open observation reconciled" in command.detail
    assert published == [(str(saga.id), GateState.OPEN.value)]


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

    count = await service._reconcile_saga(FakeFlushSession(), saga)

    assert count == 0
    assert saga.state == MovementSagaState.PHYSICAL_COMMAND_PENDING
