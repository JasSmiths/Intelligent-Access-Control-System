"""Bounded direction history for one live decision, including audited operator exits."""
from datetime import datetime

from sqlalchemy import String, cast, literal_column, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models import AccessEvent, AuditLog, MovementSagaRecord, Person, Vehicle
from app.models.core import LIVE_ACCESS_EVENT_PREDICATE
from app.models.enums import MovementSagaState


async def latest_confirmed_movement(
    session: AsyncSession, *, person: Person | None, vehicle: Vehicle | None,
    before: datetime, include_person_history: bool = True,
) -> AccessEvent | None:
    base = select(AccessEvent.id, AccessEvent.occurred_at).where(
        AccessEvent.decision == literal_column("'GRANTED'"),
        AccessEvent.direction.in_([literal_column("'ENTRY'"), literal_column("'EXIT'")]),
        AccessEvent.occurred_at < before,
    )
    order = (AccessEvent.occurred_at.desc(), AccessEvent.id.desc())
    # Permission can be granted before a device definitively refuses entry.
    # A missing legacy saga retains its old meaning; uncertain commands keep
    # their conservative direction and their independent reconciliation hold.
    denied_admission = select(MovementSagaRecord.id).where(
        MovementSagaRecord.access_event_id == AccessEvent.id,
        MovementSagaRecord.admission_status == "denied",
    ).exists()
    live = base.where(LIVE_ACCESS_EVENT_PREDICATE, ~denied_admission)
    branches = []
    if person and getattr(person, "id", None) and include_person_history:
        branches.append(live.where(AccessEvent.person_id == person.id).order_by(*order).limit(1))
    if vehicle and getattr(vehicle, "id", None):
        branches.append(live.where(AccessEvent.vehicle_id == vehicle.id).order_by(*order).limit(1))
        if person and getattr(person, "id", None):
            # External observations can carry arbitrary source and JSON. Only a
            # committed historical movement with its separate operator audit counts.
            audit = select(AuditLog.id).where(
                AuditLog.target_entity == "AccessEvent", AuditLog.target_id == cast(AccessEvent.id, String),
                AuditLog.category == "access_presence", AuditLog.action == "access_event.operator_reported_exit",
                AuditLog.outcome == "success",
            ).exists()
            committed = select(MovementSagaRecord.id).where(
                MovementSagaRecord.access_event_id == AccessEvent.id,
                MovementSagaRecord.state == MovementSagaState.COMPLETED,
                MovementSagaRecord.admission_status == "historical", MovementSagaRecord.presence_committed.is_(True),
                MovementSagaRecord.gate_command_required.is_(False),
            ).exists()
            branches.append(base.where(
                AccessEvent.vehicle_id == vehicle.id, AccessEvent.person_id == person.id,
                AccessEvent.direction == literal_column("'EXIT'"),
                AccessEvent.source == literal_column("'operator_reported_backfill'"), audit, committed,
            ).order_by(*order).limit(1))
    if not branches:
        return None
    candidates = (union_all(*branches) if len(branches) > 1 else branches[0]).subquery()
    winner = select(candidates.c.id).order_by(
        candidates.c.occurred_at.desc(), candidates.c.id.desc()).limit(1).scalar_subquery()
    return await session.scalar(select(AccessEvent).where(AccessEvent.id == winner).options(
        load_only(AccessEvent.id, AccessEvent.person_id, AccessEvent.vehicle_id,
                  AccessEvent.direction, AccessEvent.occurred_at, raiseload=True)))
