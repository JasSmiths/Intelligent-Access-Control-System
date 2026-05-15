from datetime import UTC, datetime

import pytest

from app.models import MovementSagaRecord, MovementSessionRecord
from app.models.enums import AccessDecision, AccessDirection, MovementSagaState
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


@pytest.mark.asyncio
async def test_record_movement_session_suppression_updates_durable_session() -> None:
    repository = MovementLedgerRepository()
    observed_at = datetime(2026, 5, 3, 10, 0, tzinfo=UTC)
    row = MovementSessionRecord(
        session_key="session-1",
        source="test",
        registration_number="AB12CDE",
        normalized_registration_number="AB12CDE",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        started_at=observed_at,
        last_seen_at=observed_at,
        protect_event_ids=["event-1"],
        ocr_variants=["AB12CDE"],
        suppressed_reads=[],
        is_active=True,
    )
    session = FakeFlushSession()

    await repository.record_movement_session_suppression(
        session,
        row,
        read_captured_at=observed_at.replace(minute=2),
        idle_expires_at=observed_at.replace(minute=5),
        protect_event_ids={"event-2"},
        ocr_variants={"AB12C0E"},
        last_gate_state="closed",
        reason="vehicle_session_already_active",
        matched_by="movement_session_registration_number",
        presence_evidence={"source": "unifi"},
        suppressed_read_payload={"registration_number": "AB12C0E"},
    )

    assert row.last_seen_at == observed_at.replace(minute=2)
    assert row.protect_event_ids == ["event-1", "event-2"]
    assert row.ocr_variants == ["AB12C0E", "AB12CDE"]
    assert row.suppressed_read_count == 1
    assert row.last_suppressed_reason == "vehicle_session_already_active"
    assert row.suppressed_reads == [{"registration_number": "AB12C0E"}]
    assert session.flushes == 1
