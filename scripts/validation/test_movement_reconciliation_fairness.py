"""Held outcomes must not monopolize the bounded synthetic recovery scan."""
from test_recovery_boundaries import isolated_resources as isolated_resources

from datetime import UTC, datetime
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import GateCommandRecord, MovementSagaRecord, Presence
from app.models.enums import GateCommandState, MovementSagaState
from app.services import movement_reconciliation as reconciliation_owner
from app.services.movement_reconciliation import MovementReconciliationService
from test_movement_admission import evidence, movement, person

pytestmark = pytest.mark.asyncio
OLD = datetime(2026, 9, 1, tzinfo=UTC)


async def held_batch(*, standalone):
    async with AsyncSessionLocal() as session:
        for number in range(1, 26):
            identity = uuid.UUID(int=number)
            if not standalone:
                session.add(MovementSagaRecord(id=identity, idempotency_key=f"synthetic-held-saga-{number}",
                    source="synthetic", occurred_at=OLD, created_at=OLD, updated_at=OLD,
                    state=MovementSagaState.RECONCILIATION_REQUIRED, reconciliation_required=True,
                    intent_payload={}, decision_payload={}, state_history=[]))
                await session.flush()
            session.add(GateCommandRecord(id=identity, idempotency_key=f"synthetic-held-command-{number}",
                source="synthetic", reason="Synthetic unresolved historical command", controller="legacy",
                movement_saga_id=None if standalone else identity,
                state=GateCommandState.RECONCILIATION_REQUIRED, requires_reconciliation=True,
                accepted=None, mechanically_confirmed=False, created_at=OLD, updated_at=OLD,
                command_metadata={"delivery": "unknown"}))
        await session.commit()


async def assert_held_unchanged(*, standalone):
    async with AsyncSessionLocal() as session:
        commands = (await session.scalars(select(GateCommandRecord).where(GateCommandRecord.id <= uuid.UUID(int=25)))).all()
        assert len(commands) == 25
        assert all(row.state == GateCommandState.RECONCILIATION_REQUIRED and row.requires_reconciliation
                   and row.updated_at == OLD and row.command_metadata == {"delivery": "unknown"} for row in commands)
        if not standalone:
            sagas = (await session.scalars(select(MovementSagaRecord).where(MovementSagaRecord.id <= uuid.UUID(int=25)))).all()
            assert len(sagas) == 25
            assert all(row.state == MovementSagaState.RECONCILIATION_REQUIRED and row.updated_at == OLD
                       and row.reconciliation_required and row.admission_status is None for row in sagas)


async def test_twenty_five_held_sagas_do_not_starve_later_verified_admission(monkeypatch):
    monkeypatch.setattr(reconciliation_owner, "apply_person_presence_input_boolean_actions", AsyncMock())
    await held_batch(standalone=False)
    owner = await person()
    event_id, saga_id = await movement(owner)
    # uuid4 IDs are strictly above these intentionally small held IDs.
    assert saga_id.int > 25
    await evidence(event_id, saga_id, verified=True)
    service = MovementReconciliationService()
    assert await service.reconcile_once() == 0
    async with AsyncSessionLocal() as session:
        assert (await session.get(MovementSagaRecord, saga_id)).admission_status is None
        assert await session.get(Presence, owner) is None
    assert await service.reconcile_once() == 1
    async with AsyncSessionLocal() as session:
        assert (await session.get(MovementSagaRecord, saga_id)).admission_status == "verified"
        assert (await session.get(Presence, owner)).last_event_id == event_id
    await assert_held_unchanged(standalone=False)


async def test_twenty_five_held_standalone_commands_do_not_starve_later_verified_receipt():
    await held_batch(standalone=True)
    parent_id = await evidence(None, None, verified=True)
    assert parent_id.int > 25
    service = MovementReconciliationService()
    assert await service.reconcile_once() == 0
    async with AsyncSessionLocal() as session:
        assert not (await session.get(GateCommandRecord, parent_id)).mechanically_confirmed
    assert await service.reconcile_once() == 1
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        assert parent.state == GateCommandState.RECONCILED and parent.mechanically_confirmed
        assert not parent.requires_reconciliation
    await assert_held_unchanged(standalone=True)


async def test_actionable_scan_failure_does_not_block_verified_gate_reconciliation(monkeypatch):
    from types import SimpleNamespace

    reconcile = AsyncMock(side_effect=RuntimeError("Synthetic output scan failure"))
    monkeypatch.setattr(reconciliation_owner, "get_actionable_notification_service",
                        lambda: SimpleNamespace(reconcile_actionable_outputs=reconcile))
    parent_id = await evidence(None, None, verified=True)
    service = MovementReconciliationService()
    cursor = uuid.uuid4()
    service._actionable_cursor = cursor
    assert await service.reconcile_once() == 1
    reconcile.assert_awaited_once_with(after_id=cursor)
    assert service._actionable_cursor == cursor
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, parent_id)
        assert parent.state == GateCommandState.RECONCILED and parent.mechanically_confirmed
        assert not parent.requires_reconciliation
