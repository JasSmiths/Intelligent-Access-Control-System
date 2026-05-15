from datetime import UTC, datetime

import pytest

from app.models import MovementSagaRecord
from app.models.enums import MovementSagaState
from app.services.gate_commands import GateCommandIntent
from app.services.movement_ledger import MovementLedgerRepository, gate_command_idempotency_key


class FakeFlushSession:
    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


def test_gate_command_idempotency_prefers_explicit_key() -> None:
    intent = GateCommandIntent(
        reason="open",
        source="test",
        event_id="event-1",
        idempotency_key="durable-key",
    )

    assert gate_command_idempotency_key(intent) == "durable-key"


def test_gate_command_idempotency_uses_access_event_when_available() -> None:
    intent = GateCommandIntent(reason="open", source="test", event_id="event-1")

    assert gate_command_idempotency_key(intent) == "gate-command:open:default:event:event-1"


@pytest.mark.asyncio
async def test_movement_transition_rejects_stale_state() -> None:
    repository = MovementLedgerRepository()
    row = MovementSagaRecord(
        idempotency_key="movement-1",
        source="test",
        occurred_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        state=MovementSagaState.COMPLETED,
        intent_payload={},
        decision_payload={},
        state_history=[],
    )
    session = FakeFlushSession()

    changed = await repository.transition_movement_saga(
        session,
        row,
        MovementSagaState.DIRECTION_RESOLVED,
        detail="stale_retry",
    )

    assert changed is False
    assert row.state == MovementSagaState.COMPLETED
    assert session.flushes == 0


@pytest.mark.asyncio
async def test_movement_transition_records_forward_history() -> None:
    repository = MovementLedgerRepository()
    row = MovementSagaRecord(
        idempotency_key="movement-2",
        source="test",
        occurred_at=datetime(2026, 5, 3, 10, 0, tzinfo=UTC),
        state=MovementSagaState.PHYSICAL_COMMAND_PENDING,
        intent_payload={},
        decision_payload={},
        state_history=[],
    )
    session = FakeFlushSession()

    changed = await repository.transition_movement_saga(
        session,
        row,
        MovementSagaState.RECONCILIATION_REQUIRED,
        detail="accepted_without_mechanical_confirmation",
        reconciliation_required=True,
    )

    assert changed is True
    assert row.state == MovementSagaState.RECONCILIATION_REQUIRED
    assert row.reconciliation_required is True
    assert row.state_history[-1]["detail"] == "accepted_without_mechanical_confirmation"
    assert session.flushes == 1
