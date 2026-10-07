"""Fenced database ownership for the serial access coordinator and its windows.

These operations own no hardware/provider ports. In-session writes never commit;
the outer event/saga/intents transaction owns the final check and commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import LprCoordinatorState, LprDebounceWindow, LprIngestEvent
from app.services.access.pipeline_contracts import (
    CoordinatorClaim,
    IngestClaim,
    PipelineLeaseLost,
    WindowClaim,
)

COORDINATOR_LEASE_SECONDS = 120
COORDINATOR_RENEWAL_SECONDS = 10


async def database_now(session: AsyncSession) -> datetime:
    """Sample after locks; transaction-start now() cannot fence a blocked owner."""
    value = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise PipelineLeaseLost("database_clock_unavailable")
    return value


async def _coordinator_row(session: AsyncSession) -> LprCoordinatorState:
    await session.execute(
        insert(LprCoordinatorState)
        .values(
            id=1,
            epoch=0,
            next_open_ordinal=1,
            handled_ready_ordinal=0,
        )
        .on_conflict_do_nothing(index_elements=[LprCoordinatorState.id])
    )
    row = await session.get(LprCoordinatorState, 1, with_for_update=True, populate_existing=True)
    if row is None:
        raise PipelineLeaseLost("coordinator_missing")
    return row


def _check_coordinator(row: LprCoordinatorState, claim: CoordinatorClaim, now: datetime) -> None:
    if (
        row.epoch != claim.epoch
        or row.lease_token != claim.token
        or row.lease_until is None
        or row.lease_until <= now
    ):
        raise PipelineLeaseLost("coordinator_lease_lost")


async def assert_coordinator_in_session(
    session: AsyncSession, claim: CoordinatorClaim
) -> LprCoordinatorState:
    row = await session.get(LprCoordinatorState, 1, with_for_update=True, populate_existing=True)
    if row is None:
        raise PipelineLeaseLost("coordinator_missing")
    _check_coordinator(row, claim, await database_now(session))
    return row


async def assert_ingest_in_session(session: AsyncSession, claim: IngestClaim) -> LprIngestEvent:
    coordinator = await assert_coordinator_in_session(session, claim.coordinator)
    row = await session.get(
        LprIngestEvent, claim.ingest_id, with_for_update=True, populate_existing=True
    )
    now = await database_now(session)
    _check_coordinator(coordinator, claim.coordinator, now)
    if (
        row is None
        or row.status != "processing"
        or row.lease_token != claim.token
        or row.lease_until is None
        or row.lease_until <= now
    ):
        raise PipelineLeaseLost("ingest_lease_lost")
    return row


async def assert_window_in_session(
    session: AsyncSession,
    claim: WindowClaim,
    *,
    completed: bool = False,
) -> LprDebounceWindow:
    coordinator = await assert_coordinator_in_session(session, claim.coordinator)
    row = await session.get(
        LprDebounceWindow, claim.window_id, with_for_update=True, populate_existing=True
    )
    now = await database_now(session)
    _check_coordinator(coordinator, claim.coordinator, now)
    allowed = {"completed"} if completed else {"finalizing"}
    if (
        row is None
        or row.state not in allowed
        or row.lease_token != claim.token
        or row.coordinator_epoch != claim.coordinator.epoch
        or row.lease_until is None
        or row.lease_until <= now
    ):
        raise PipelineLeaseLost("window_lease_lost")
    return row


async def terminalize_window_in_session(
    session: AsyncSession,
    claim: WindowClaim,
    *,
    event_id: uuid.UUID,
    saga_id: uuid.UUID,
) -> None:
    """Whole-window terminalization joins the caller's event/saga/intent commit."""
    window = await assert_window_in_session(session, claim)
    now = await database_now(session)
    await session.execute(
        update(LprIngestEvent)
        .where(LprIngestEvent.window_id == claim.window_id)
        .values(
            status="succeeded",
            access_event_id=event_id,
            movement_saga_id=saga_id,
            processed_at=now,
            processing_started_at=None,
            last_error=None,
            terminal_reason="window_committed",
            lease_token=None,
            lease_until=None,
        )
    )
    window.state, window.access_event_id, window.movement_saga_id = "completed", event_id, saga_id
    await session.flush()
    await assert_window_in_session(session, claim, completed=True)
    # Caller rechecks again immediately before commit if later required writes
    # acquire blocking locks. Cancellation rolls the whole transaction back.


class AccessPipelineStore:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    async def acquire_coordinator(self) -> CoordinatorClaim | None:
        async with self.session_factory() as session:
            row = await _coordinator_row(session)
            now = await database_now(session)
            if (
                row.lease_token is not None
                and row.lease_until is not None
                and row.lease_until > now
            ):
                return None
            row.epoch += 1
            row.lease_token, row.lease_until = (
                uuid.uuid4(),
                now + timedelta(seconds=COORDINATOR_LEASE_SECONDS),
            )
            claim = CoordinatorClaim(row.lease_token, row.epoch, row.lease_until)
            await session.commit()
            return claim

    async def renew_coordinator(self, claim: CoordinatorClaim) -> CoordinatorClaim:
        async with self.session_factory() as session:
            row = await session.get(
                LprCoordinatorState, 1, with_for_update=True, populate_existing=True
            )
            if row is None:
                raise PipelineLeaseLost("coordinator_missing")
            now = await database_now(session)
            _check_coordinator(row, claim, now)
            row.lease_until = now + timedelta(seconds=COORDINATOR_LEASE_SECONDS)
            renewed = CoordinatorClaim(claim.token, claim.epoch, row.lease_until)
            await session.commit()
            return renewed

    async def release_coordinator(self, claim: CoordinatorClaim) -> None:
        async with self.session_factory() as session:
            row = await assert_coordinator_in_session(session, claim)
            row.lease_token, row.lease_until = None, None
            await session.commit()
