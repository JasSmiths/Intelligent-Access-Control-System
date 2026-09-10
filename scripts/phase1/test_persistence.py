"""Opt-in PostgreSQL regression tests; run only through the Phase 1 harness."""
from datetime import UTC, datetime
import os
from pathlib import Path
import uuid

import pytest

# Refuse unsafe execution before importing application database owners.
assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
assert '@127.0.0.1:5432/iacs_p1_' in os.environ.get('IACS_DATABASE_URL', '')

from app.db.session import AsyncSessionLocal, engine
from app.models.enums import MovementSagaState
from app.services.movement_ledger import MovementLedgerRepository


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['duplicate', 'ocr_noise', 'vehicle_session_already_active'])
async def test_suppressed_movement_survives_commit_and_new_session(reason):
    repository = MovementLedgerRepository()
    key = 'phase1-' + uuid.uuid4().hex
    try:
        async with AsyncSessionLocal() as session:
            row = await repository.create_movement_saga(
                session, idempotency_key=key, source='synthetic-phase1',
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC), registration_number='SYNTH01',
                state=MovementSagaState.SUPPRESSED, decision_payload={'reason': reason})
            row_id = row.id
            await session.commit()
        async with AsyncSessionLocal() as session:
            row = await repository.movement_saga_by_idempotency_key(session, key)
            assert row.id == row_id
            assert row.state == MovementSagaState.SUPPRESSED
            assert row.decision_payload == {'reason': reason}
            assert row.state_history[-1]['state'] == MovementSagaState.SUPPRESSED.value
            assert not row.gate_command_required
            assert not row.presence_committed
            duplicate = await repository.create_movement_saga(
                session, idempotency_key=key, source='synthetic-phase1',
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
            assert duplicate.id == row_id
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted,gate_state,expected,reconcile', [
    (True, 'opening', 'accepted', False),
    (False, 'closed', 'rejected', False),
    (True, 'closed', 'reconciliation_required', True),
])
async def test_gate_outcome_and_replay_survive_new_coordinator(accepted, gate_state, expected, reconcile):
    from sqlalchemy import func, select
    from app.models import GateCommandRecord
    from app.modules.gate.base import GateCommandResult, GateState
    from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent

    calls = []

    class FakeGate:
        async def open_gate(self, reason, *, bypass_schedule=False):
            calls.append(reason)
            return GateCommandResult(accepted, GateState(gate_state), 'Synthetic outcome')

    def forbidden_factory(name):
        raise AssertionError('A persisted replay must not consult a provider')

    key = 'phase1-' + uuid.uuid4().hex
    intent = GateCommandIntent(reason='synthetic persistence regression', source='synthetic-phase1',
                               gate_key=key, idempotency_key=key)
    try:
        first = await GateCommandCoordinator(lambda name: FakeGate()).execute_open(intent)
        assert first.accepted is accepted
        assert first.requires_reconciliation is reconcile
        await engine.dispose()
        replay = await GateCommandCoordinator(forbidden_factory).execute_open(intent)
        assert replay.command_id == first.command_id
        assert replay.accepted is accepted
        assert replay.requires_reconciliation is reconcile
        assert len(calls) == 1
        async with AsyncSessionLocal() as session:
            row = await session.get(GateCommandRecord, uuid.UUID(first.command_id))
            assert row.state.value == expected
            assert row.requires_reconciliation is reconcile
            count = await session.scalar(select(func.count()).select_from(GateCommandRecord).where(
                GateCommandRecord.idempotency_key == key))
            assert count == 1
    finally:
        await engine.dispose()
