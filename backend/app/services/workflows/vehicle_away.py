"""Capture vehicle absence from recorded movements before notification delivery."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessEvent, MovementSagaRecord
from app.models.enums import AccessDecision, AccessDirection


def vehicle_time_away_label(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(seconds) or seconds <= 0:
        return ""
    minutes = int(seconds // 60)
    days = minutes // (24 * 60)
    for count, unit in ((days // 30, "month"), (days // 7, "week"), (days, "day")):
        if count:
            return f"after {count} {unit}{'s' if count != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    if hours:
        duration = f"{hours}hr{'s' if hours != 1 else ''}"
        return f"after {duration}{f' {minutes}m' if minutes else ''}"
    return f"after {minutes}m" if minutes else "after less than a minute"


async def vehicle_time_away_seconds(session: AsyncSession, event: AccessEvent) -> float | None:
    """Only the same vehicle's immediately preceding eligible exit starts an absence.

    Arrival time, rather than dispatch time, is the endpoint. Pending/failed
    admissions cannot interrupt an absence; legacy granted history is supported.
    """
    if event.direction != AccessDirection.ENTRY or event.decision != AccessDecision.GRANTED:
        return None
    identity = (
        AccessEvent.vehicle_id == event.vehicle_id
        if event.vehicle_id
        else AccessEvent.registration_number == event.registration_number
    )
    saga = select(MovementSagaRecord.id).where(MovementSagaRecord.access_event_id == AccessEvent.id)
    eligible_saga = saga.where(
        MovementSagaRecord.admission_status.in_(("verified", "not_required", "historical"))
    )
    previous = await session.scalar(
        select(AccessEvent)
        .where(
            identity,
            AccessEvent.decision == AccessDecision.GRANTED,
            AccessEvent.direction.in_((AccessDirection.ENTRY, AccessDirection.EXIT)),
            AccessEvent.occurred_at < event.occurred_at,
            or_(~saga.exists(), eligible_saga.exists()),
        )
        .order_by(
            AccessEvent.occurred_at.desc(),
            AccessEvent.created_at.desc(),
            AccessEvent.id.desc(),
        )
        .limit(1)
    )
    if previous is None or previous.direction != AccessDirection.EXIT:
        return None
    return (_aware(event.occurred_at) - _aware(previous.occurred_at)).total_seconds()


def _aware(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
