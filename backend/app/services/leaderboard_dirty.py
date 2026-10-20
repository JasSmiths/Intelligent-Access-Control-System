"""One durable chart generation; participant writes never aggregate or commit."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import AccessChartDirtyState, AccessEvent
from app.models.enums import AccessDecision, AccessDirection
from app.services.durable_readiness import readiness
from app.services.durable_work import WorkReadiness, WorkTopic, notify_work
from app.services.leaderboard_notifications import CHART_OPTIONAL_SECONDS

CHART_QUIET_SECONDS = 2
CHART_MAX_WAIT_SECONDS = 10
CHART_LEASE_SECONDS = 120
CHART_RENEW_SECONDS = 10


class ChartLeaseLost(RuntimeError):
    """A stale evaluator cannot change leader state or acknowledge work."""


@dataclass(frozen=True)
class ChartClaim:
    generation: int
    token: uuid.UUID
    live_event_id: uuid.UUID | None
    live_origin_at: datetime | None
    live_deadline: datetime | None


class LeaderboardDirtyStore:
    async def readiness(self) -> WorkReadiness:
        return await readiness(
            self.sessions,
            (
                (
                    "SELECT GREATEST(COALESCE(lease_until,t.now),LEAST(last_dirty_at + INTERVAL '2 seconds',first_dirty_at + "
                    "INTERVAL '10 seconds')) FROM access_chart_dirty_state,t WHERE id=1 AND "
                    "requested_generation>acknowledged_generation"
                ),
            ),
        )

    def __init__(self, sessions: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.sessions = sessions

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self.sessions()
        try:
            # These bounds apply only to the evaluator's own short/read sessions,
            # never to a participating access or directory mutation transaction.
            await session.scalar(select(func.set_config("statement_timeout", "30000", True)))
            await session.scalar(select(func.set_config("lock_timeout", "5000", True)))
            yield session
        finally:
            await drain_owned(session.close())

    async def now(self, session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            raise ChartLeaseLost("chart_clock_unavailable")
        return value

    async def mark_access_in_session(
        self, session: AsyncSession, event: AccessEvent, *, origin_at: datetime | None
    ) -> bool:
        if not (
            event.vehicle_id
            and event.decision == AccessDecision.GRANTED
            and event.direction == AccessDirection.ENTRY
        ):
            return False
        await self._mark(session, event.id if origin_at is not None else None, origin_at)
        return True

    async def mark_dirty_in_session(self, session: AsyncSession) -> None:
        await self._mark(session, None, None)

    async def _mark(
        self, session: AsyncSession, event_id: uuid.UUID | None, origin_at: datetime | None
    ) -> None:
        now = await self.now(session)
        if origin_at is not None and (
            origin_at.tzinfo is None or origin_at.utcoffset() is None or origin_at > now
        ):
            raise ValueError("A chart origin must be aware and cannot be in the future")
        deadline = origin_at + timedelta(seconds=CHART_OPTIONAL_SECONDS) if origin_at else None
        statement = insert(AccessChartDirtyState).values(
            id=1, requested_generation=1, acknowledged_generation=0,
            first_dirty_at=now, last_dirty_at=now,
            live_event_id=event_id, live_origin_at=origin_at, live_deadline=deadline,
        )
        await session.execute(statement.on_conflict_do_update(
            index_elements=[AccessChartDirtyState.id],
            set_={
                "requested_generation": AccessChartDirtyState.requested_generation + 1,
                "first_dirty_at": func.coalesce(AccessChartDirtyState.first_dirty_at, now),
                "last_dirty_at": now,
                "live_event_id": event_id, "live_origin_at": origin_at, "live_deadline": deadline,
            },
        ))
        # Silent marks create no new authority. An active claim already captured
        # A's original authority independently; its frozen A is not renewed or
        # revoked here. These fields describe the latest pending/trailing batch.
        await notify_work(session, WorkTopic.CHART)


    async def claim(self) -> ChartClaim | None:
        async with self.session() as session:
            state = await session.scalar(select(AccessChartDirtyState)
                .where(AccessChartDirtyState.id == 1).with_for_update(skip_locked=True))
            if state is None or state.requested_generation <= state.acknowledged_generation:
                return None
            now = await self.now(session)
            if state.lease_until is not None and state.lease_until > now:
                return None
            if state.first_dirty_at is not None and state.last_dirty_at is not None:
                ready = min(state.last_dirty_at + timedelta(seconds=CHART_QUIET_SECONDS),
                            state.first_dirty_at + timedelta(seconds=CHART_MAX_WAIT_SECONDS))
                if ready > now:
                    return None
            # Reclaim the CURRENT requested generation and retained original
            # provenance, never an expired A claim with B's newer origin.
            claim = ChartClaim(state.requested_generation, uuid.uuid4(), state.live_event_id,
                               state.live_origin_at, state.live_deadline)
            state.claim_generation, state.lease_token = claim.generation, claim.token
            state.lease_until = now + timedelta(seconds=CHART_LEASE_SECONDS)
            # New marks during aggregation establish exactly one trailing batch.
            # Keep provenance for crash recovery when no new mark arrives.
            state.first_dirty_at, state.last_dirty_at = None, None
            await session.commit()
            return claim

    async def lock_owned(
        self, session: AsyncSession, claim: ChartClaim
    ) -> tuple[AccessChartDirtyState, datetime]:
        state = await session.get(AccessChartDirtyState, 1, with_for_update=True, populate_existing=True)
        now = await self.now(session)
        if (
            state is None or state.claim_generation != claim.generation
            or state.lease_token != claim.token or state.lease_until is None or state.lease_until <= now
        ):
            raise ChartLeaseLost("chart_lease_lost")
        return state, now

    async def renew(self, claim: ChartClaim) -> None:
        async with self.session() as session:
            state, now = await self.lock_owned(session, claim)
            original_until = state.lease_until
            state.lease_until = now + timedelta(seconds=CHART_LEASE_SECONDS)
            await session.flush()
            if original_until is None or await self.now(session) >= original_until:
                raise ChartLeaseLost("chart_lease_expired_during_renewal")
            await session.commit()

    def acknowledge(self, state: AccessChartDirtyState, claim: ChartClaim) -> None:
        state.acknowledged_generation = claim.generation
        state.claim_generation, state.lease_token, state.lease_until = None, None, None
        if state.requested_generation == claim.generation:
            state.first_dirty_at, state.last_dirty_at = None, None
            state.live_origin_at, state.live_deadline, state.live_event_id = None, None, None
        # A newer requested generation preserves its own timestamps/provenance;
        # it does not invalidate the computed A result or starve leader progress.
