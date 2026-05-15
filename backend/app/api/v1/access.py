import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import current_user
from app.db.session import get_db_session
from app.models import GateCommandRecord, MovementSagaRecord, User
from app.models.enums import GateCommandState, MovementSagaState

router = APIRouter()


@router.get("/movements")
async def list_movements(
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
    state: MovementSagaState | None = None,
    reconciliation_required: bool | None = None,
    limit: int = Query(default=100, ge=1, le=250),
) -> list[dict[str, Any]]:
    query = (
        select(MovementSagaRecord)
        .options(selectinload(MovementSagaRecord.gate_commands))
        .order_by(MovementSagaRecord.occurred_at.desc(), MovementSagaRecord.created_at.desc())
        .limit(limit)
    )
    if state:
        query = query.where(MovementSagaRecord.state == state)
    if reconciliation_required is not None:
        query = query.where(MovementSagaRecord.reconciliation_required.is_(reconciliation_required))
    rows = (await session.scalars(query)).all()
    return [_movement_payload(row, include_history=False) for row in rows]


@router.get("/movements/{movement_id}")
async def movement_detail(
    movement_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    row = await session.scalar(
        select(MovementSagaRecord)
        .options(selectinload(MovementSagaRecord.gate_commands))
        .where(MovementSagaRecord.id == movement_id)
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Movement saga was not found.")
    return _movement_payload(row, include_history=True)


@router.get("/gate-commands")
async def list_gate_commands(
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
    state: GateCommandState | None = None,
    requires_reconciliation: bool | None = None,
    limit: int = Query(default=100, ge=1, le=250),
) -> list[dict[str, Any]]:
    query = (
        select(GateCommandRecord)
        .order_by(GateCommandRecord.created_at.desc())
        .limit(limit)
    )
    if state:
        query = query.where(GateCommandRecord.state == state)
    if requires_reconciliation is not None:
        query = query.where(GateCommandRecord.requires_reconciliation.is_(requires_reconciliation))
    rows = (await session.scalars(query)).all()
    return [_gate_command_payload(row) for row in rows]


def _movement_payload(row: MovementSagaRecord, *, include_history: bool) -> dict[str, Any]:
    payload = {
        "id": str(row.id),
        "idempotency_key": row.idempotency_key,
        "source": row.source,
        "state": row.state.value,
        "access_event_id": str(row.access_event_id) if row.access_event_id else None,
        "person_id": str(row.person_id) if row.person_id else None,
        "vehicle_id": str(row.vehicle_id) if row.vehicle_id else None,
        "registration_number": row.registration_number,
        "direction": row.direction.value if row.direction else None,
        "decision": row.decision.value if row.decision else None,
        "occurred_at": row.occurred_at.isoformat(),
        "gate_command_required": row.gate_command_required,
        "presence_committed": row.presence_committed,
        "reconciliation_required": row.reconciliation_required,
        "failure_detail": row.failure_detail,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "gate_commands": [_gate_command_payload(command) for command in row.gate_commands],
    }
    if include_history:
        payload["intent_payload"] = row.intent_payload
        payload["decision_payload"] = row.decision_payload
        payload["state_history"] = row.state_history
    return payload


def _gate_command_payload(row: GateCommandRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "idempotency_key": row.idempotency_key,
        "movement_saga_id": str(row.movement_saga_id) if row.movement_saga_id else None,
        "access_event_id": str(row.access_event_id) if row.access_event_id else None,
        "state": row.state.value,
        "action": row.action,
        "source": row.source,
        "gate_key": row.gate_key,
        "controller": row.controller,
        "reason": row.reason,
        "actor": row.actor,
        "registration_number": row.registration_number,
        "bypass_schedule": row.bypass_schedule,
        "leased_at": row.leased_at.isoformat() if row.leased_at else None,
        "lease_expires_at": row.lease_expires_at.isoformat() if row.lease_expires_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "accepted": row.accepted,
        "gate_state": row.gate_state,
        "detail": row.detail,
        "mechanically_confirmed": row.mechanically_confirmed,
        "requires_reconciliation": row.requires_reconciliation,
        "exception_class": row.exception_class,
        "metadata": row.command_metadata,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
