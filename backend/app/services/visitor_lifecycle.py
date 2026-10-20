"""Indexed visitor time boundaries; mutation policy remains in VisitorPassService."""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Select, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VisitorPass
from app.models.visitor_timing import EXPIRY_SQL, INVALID_WINDOW_SQL, START_SQL
from app.services.durable_readiness import readiness
from app.services.durable_work import WorkReadiness
from app.services.workflow_session import SessionFactory

BATCH_SIZE = 25


class VisitorLifecycleLane(StrEnum):
    START = "start"
    EXPIRY = "expiry"
    INVALID = "invalid"


LANES = tuple(VisitorLifecycleLane)


async def lifecycle_readiness(sessions: SessionFactory) -> WorkReadiness:
    return await readiness(sessions, (
        f"SELECT t.now FROM visitor_passes,t WHERE {INVALID_WINDOW_SQL} ORDER BY visitor_passes.id LIMIT 1",
        f"SELECT ({START_SQL}) AT TIME ZONE 'UTC' FROM visitor_passes WHERE status='SCHEDULED' ORDER BY ({START_SQL}),id LIMIT 1",
        f"SELECT ({EXPIRY_SQL}) AT TIME ZONE 'UTC' FROM visitor_passes WHERE status='ACTIVE' ORDER BY ({EXPIRY_SQL}),id LIMIT 1",
    ))


def due_passes(
    now: datetime, *, lane: VisitorLifecycleLane = VisitorLifecycleLane.START,
    limit: int = BATCH_SIZE,
) -> Select[tuple[VisitorPass]]:
    """Skip locked rows BEFORE the limit, using one exact partial index per lane."""
    if isinstance(limit, bool) or not 1 <= limit <= BATCH_SIZE:
        raise ValueError("Visitor lifecycle batch limit must be between 1 and 25")
    checked = now.astimezone(UTC).replace(tzinfo=None)
    statement = select(VisitorPass)
    if lane == VisitorLifecycleLane.INVALID:
        statement = statement.where(text(INVALID_WINDOW_SQL)).order_by(VisitorPass.id)
    else:
        expression = literal_column(START_SQL if lane == VisitorLifecycleLane.START else EXPIRY_SQL)
        predicate = "status='SCHEDULED'" if lane == VisitorLifecycleLane.START else "status='ACTIVE'"
        # Literal predicates permit index selection under generic prepared plans.
        statement = statement.where(text(predicate), expression <= checked).order_by(expression, VisitorPass.id)
    return (statement.limit(limit).with_for_update(skip_locked=True)
            .execution_options(populate_existing=True))


async def due_passes_in_session(
    session: AsyncSession, now: datetime, *, first_lane: VisitorLifecycleLane,
) -> list[VisitorPass]:
    """Share 25 actual row locks across lanes; rotate the first lane after commit."""
    start = LANES.index(first_lane)
    selected: list[VisitorPass] = []
    for offset in range(len(LANES)):
        remaining = BATCH_SIZE - len(selected)
        if not remaining:
            break
        lane = LANES[(start + offset) % len(LANES)]
        selected.extend((await session.scalars(due_passes(now, lane=lane, limit=remaining))).all())
    return selected
