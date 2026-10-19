"""Fenced database ownership for the serial access coordinator and its windows.

These operations own no hardware/provider ports. In-session writes never commit;
the outer event/saga/intents transaction owns the final check and commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import (
    HistoricalDiscoveryProgress,
    LprCoordinatorState,
    LprDebounceWindow,
    LprIngestEvent,
)
from app.services.access.completion_database import access_session
from app.services.access.pipeline_contracts import (
    MAX_PLATE_READ_PROCESSING_ATTEMPTS,
    CoordinatorBoundary,
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


async def hold_window_in_session(
    session: AsyncSession,
    claim: WindowClaim,
    *,
    reason: str,
    detail: str | None = None,
) -> None:
    """Retain every acknowledged member when the original processing budget ends."""
    window = await assert_window_in_session(session, claim)
    now = await database_now(session)
    await session.execute(
        update(LprIngestEvent)
        .where(
            LprIngestEvent.window_id == claim.window_id,
        )
        .values(
            status="held",
            processed_at=now,
            processing_started_at=None,
            terminal_reason=reason[:120],
            last_error=detail[:500] if detail else None,
            lease_token=None,
            lease_until=None,
        )
    )
    window.state, window.reason = "held", reason[:120]
    window.last_error = detail[:500] if detail else None
    window.lease_token, window.lease_until = None, None
    await session.flush()
    await assert_coordinator_in_session(session, claim.coordinator)


async def preparation_failed_in_session(
    session: AsyncSession,
    claim: IngestClaim,
    *,
    detail: str,
    max_attempts: int,
) -> bool:
    """Recovery increments the original ingest budget; no RAM retry counter."""
    row = await assert_ingest_in_session(session, claim)
    row.last_error = detail[:500]
    if row.attempt_count < max_attempts:
        await session.flush()
        await assert_ingest_in_session(session, claim)
        return True
    coordinator = await assert_coordinator_in_session(session, claim.coordinator)
    row.status, row.terminal_reason = "held", "processing_attempts_exhausted"
    row.processed_at, row.processing_started_at = await database_now(session), None
    row.lease_token, row.lease_until = None, None
    coordinator.boundary_phase, coordinator.flush_clock = "flushing", await database_now(session)
    coordinator.flush_upper_ordinal = coordinator.next_open_ordinal - 1
    coordinator.flush_completed_ordinal, coordinator.finalizing_window_id = 0, None
    await session.flush()
    await assert_coordinator_in_session(session, claim.coordinator)
    return False


async def finalization_failed_in_session(
    session: AsyncSession,
    claim: WindowClaim,
    *,
    detail: str,
    max_attempts: int,
) -> bool:
    """Use the chosen ingest's TOTAL processing budget, including preparation.

    A committed window is never passed here: command/admission recovery belongs
    to its already committed saga and command journal, not original execution.
    """
    window = await assert_window_in_session(session, claim)
    row = await session.get(
        LprIngestEvent, window.best_ingest_id, with_for_update=True, populate_existing=True
    )
    if row is None:
        raise PipelineLeaseLost("chosen_ingest_unavailable")
    if row.attempt_count >= max_attempts:
        await hold_window_in_session(
            session, claim, reason="processing_attempts_exhausted", detail=detail
        )
        return False
    # The next fenced reclaim charges the next execution attempt. Recording a
    # handled failure must not charge that same attempt a second time.
    row.last_error, window.last_error = detail[:500], detail[:500]
    await session.flush()
    await assert_window_in_session(session, claim)
    return True


class AccessPipelineStore:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    async def backlog_status(self) -> dict[str, int | float | str | None]:
        """Indexed scalar diagnostics; readiness never needs durable backlog detail."""
        async with access_session(self.session_factory) as session:
            waiting = (LprIngestEvent.observation_state == "ready",
                       LprIngestEvent.status.in_(["pending", "processing"]),
                       LprIngestEvent.window_id.is_(None))
            windows = LprDebounceWindow.state.in_(["open", "finalizing"])
            queued, pending, ingest_at, window_at, discovery_pending, discovery_held, discovery_reason = (await session.execute(select(
                select(func.count()).select_from(LprIngestEvent).where(*waiting).scalar_subquery(),
                select(func.count()).select_from(LprDebounceWindow).where(windows).scalar_subquery(),
                select(func.min(LprIngestEvent.received_at)).where(*waiting).scalar_subquery(),
                select(func.min(LprDebounceWindow.min_received_at)).where(windows).scalar_subquery(),
                select(func.count()).select_from(HistoricalDiscoveryProgress).where(
                    HistoricalDiscoveryProgress.state != 'held',
                    (HistoricalDiscoveryProgress.state != 'completed') |
                    HistoricalDiscoveryProgress.queued_start_ms.is_not(None)).scalar_subquery(),
                select(func.count()).select_from(HistoricalDiscoveryProgress).where(
                    HistoricalDiscoveryProgress.state == 'held').scalar_subquery(),
                select(HistoricalDiscoveryProgress.hold_reason).where(
                    HistoricalDiscoveryProgress.state == 'held').order_by(
                        HistoricalDiscoveryProgress.updated_at.desc(), HistoricalDiscoveryProgress.provider_key).limit(1).scalar_subquery(),
            ))).one()
            oldest = min((value for value in (ingest_at, window_at) if value is not None), default=None)
            now = await database_now(session)
            return {"queue_depth": queued, "pending_windows": pending,
                    "historical_discovery_pending": discovery_pending,
                    "historical_discovery_held": discovery_held,
                    "historical_discovery_hold_reason": discovery_reason,
                    "oldest_pending_at": oldest.isoformat() if oldest is not None else None,
                    "oldest_pending_age_seconds": max(0.0, (now - oldest).total_seconds()) if oldest is not None else None}

    async def acquire_coordinator(self) -> CoordinatorClaim | None:
        async with access_session(self.session_factory) as session:
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
        async with access_session(self.session_factory) as session:
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
        async with access_session(self.session_factory) as session:
            row = await assert_coordinator_in_session(session, claim)
            row.lease_token, row.lease_until = None, None
            await session.commit()

    async def has_pending_work(self, claim: CoordinatorClaim) -> bool:
        """One read-only idle probe; any work still enters the locked boundary path.

        This snapshot grants no mutation authority. Arrival after the snapshot is
        observed by the wake hint or the unchanged fallback poll; all processing
        and lease checks retain their existing locked owners.
        """
        ready = (
            select(LprIngestEvent.ready_ordinal)
            .where(
                LprIngestEvent.observation_state == "ready",
                LprIngestEvent.ready_ordinal > LprCoordinatorState.handled_ready_ordinal,
            )
            .order_by(LprIngestEvent.ready_ordinal)
            .limit(1)
            .correlate(LprCoordinatorState)
            .scalar_subquery()
            .is_not(None)
        )
        # Plain EXISTS can choose a sequential scan for a correlated cursor or
        # generic state parameter, expecting an early match. Ordered one-row
        # lookups retain indexed bounds even when all retained history is handled.
        windows = (
            select(LprDebounceWindow.open_ordinal)
            .where(LprDebounceWindow.state == "open")
            .order_by(LprDebounceWindow.source, LprDebounceWindow.open_ordinal)
            .limit(1)
            .scalar_subquery()
            .is_not(None)
        )
        async with access_session(self.session_factory) as session:
            result = (await session.execute(
                select(LprCoordinatorState, func.clock_timestamp(), ready, windows)
                .where(LprCoordinatorState.id == 1)
            )).one_or_none()
            if result is None:
                raise PipelineLeaseLost("coordinator_missing")
            row, now, has_ready, has_windows = result
            if not isinstance(now, datetime):
                raise PipelineLeaseLost("database_clock_unavailable")
            _check_coordinator(row, claim, now)
            retained_boundary = any(value is not None for value in (
                row.boundary_phase, row.boundary_ingest_id, row.finalizing_window_id,
                row.flush_clock, row.flush_upper_ordinal, row.flush_completed_ordinal,
            ))
            return retained_boundary or bool(has_ready) or bool(has_windows)

    async def boundary(self, claim: CoordinatorClaim) -> CoordinatorBoundary:
        async with access_session(self.session_factory) as session:
            row = await assert_coordinator_in_session(session, claim)
            phase = row.boundary_phase or "idle"
            if phase not in {
                "idle",
                "preparing",
                "flushing",
                "immediate_finalizing",
                "flush_finalizing",
            }:
                raise PipelineLeaseLost("coordinator_boundary_unavailable")
            return CoordinatorBoundary(
                cast(
                    Literal[
                        "idle", "preparing", "flushing", "immediate_finalizing", "flush_finalizing"
                    ],
                    phase,
                ),
                row.boundary_ingest_id,
                row.finalizing_window_id,
            )

    async def claim_next_read(self, claim: CoordinatorClaim) -> IngestClaim | None:
        """Lowest visible ready order; terminal metadata pages never imply exhaustion."""
        while True:
            async with access_session(self.session_factory) as session:
                coordinator = await assert_coordinator_in_session(session, claim)
                if coordinator.boundary_phase is not None:
                    raise PipelineLeaseLost("unfinished_read_flush_boundary")
                # At most50 small identities/statuses. Hydrate only the one read
                # actually admitted; original vendor bodies are not queue entries.
                page = (
                    await session.execute(
                        select(
                            LprIngestEvent.id, LprIngestEvent.status, LprIngestEvent.ready_ordinal
                        )
                        .where(
                            LprIngestEvent.observation_state == "ready",
                            LprIngestEvent.ready_ordinal > coordinator.handled_ready_ordinal,
                        )
                        .order_by(LprIngestEvent.ready_ordinal)
                        .limit(50)
                        .with_for_update()
                    )
                ).all()
                selected_id = None
                for identity, status, ordinal in page:
                    if status in {"succeeded", "skipped", "failed", "held"}:
                        coordinator.handled_ready_ordinal = ordinal
                        continue
                    selected_id = identity
                    break
                if selected_id is None:
                    await session.flush()
                    await assert_coordinator_in_session(session, claim)
                    await session.commit()
                    if len(page) == 50:
                        continue  # short committed terminal-prefix checkpoint
                    return None
                selected = await session.get(
                    LprIngestEvent, selected_id, with_for_update=True, populate_existing=True
                )
                if (
                    selected is None
                    or selected.window_id is not None
                    or selected.original_observation is None
                ):
                    raise PipelineLeaseLost("unowned_ready_membership")
                now = await database_now(session)
                _check_coordinator(coordinator, claim, now)
                selected.status, selected.lease_token = "processing", uuid.uuid4()
                selected.lease_until = min(
                    coordinator.lease_until or now,
                    now + timedelta(seconds=COORDINATOR_LEASE_SECONDS),
                )
                selected.processing_started_at = now
                selected.attempt_count += 1
                coordinator.boundary_ingest_id, coordinator.boundary_phase = (
                    selected.id,
                    "preparing",
                )
                result = IngestClaim(selected.id, selected.lease_token, claim)
                await session.flush()
                await assert_ingest_in_session(session, result)
                await session.commit()
                return result

    async def recover_preparing_read(self, claim: CoordinatorClaim) -> IngestClaim:
        async with access_session(self.session_factory) as session:
            coordinator = await assert_coordinator_in_session(session, claim)
            if coordinator.boundary_phase != "preparing" or coordinator.boundary_ingest_id is None:
                raise PipelineLeaseLost("preparing_boundary_unavailable")
            row = await session.get(
                LprIngestEvent, coordinator.boundary_ingest_id, with_for_update=True
            )
            now = await database_now(session)
            _check_coordinator(coordinator, claim, now)
            if (
                row is None
                or row.status != "processing"
                or row.window_id is not None
                or row.original_observation is None
            ):
                raise PipelineLeaseLost("preparing_read_unavailable")
            row.lease_token = uuid.uuid4()
            row.lease_until = min(
                coordinator.lease_until or now, now + timedelta(seconds=COORDINATOR_LEASE_SECONDS)
            )
            row.processing_started_at, row.attempt_count = now, row.attempt_count + 1
            result = IngestClaim(row.id, row.lease_token, claim)
            await session.flush()
            await assert_ingest_in_session(session, result)
            await session.commit()
            return result

    async def begin_flush_in_session(self, session: AsyncSession, claim: IngestClaim) -> None:
        await assert_ingest_in_session(session, claim)
        coordinator = await assert_coordinator_in_session(session, claim.coordinator)
        if (
            coordinator.boundary_ingest_id != claim.ingest_id
            or coordinator.boundary_phase != "preparing"
        ):
            raise PipelineLeaseLost("read_boundary_changed")
        coordinator.boundary_phase, coordinator.flush_clock = (
            "flushing",
            await database_now(session),
        )
        coordinator.flush_upper_ordinal = coordinator.next_open_ordinal - 1
        coordinator.flush_completed_ordinal, coordinator.finalizing_window_id = 0, None
        await session.flush()
        await assert_ingest_in_session(session, claim)

    async def suppress_read_in_session(
        self,
        session: AsyncSession,
        claim: IngestClaim,
        *,
        reason: str,
        status: Literal["succeeded", "skipped", "held"] = "skipped",
        saga_id: uuid.UUID | None = None,
    ) -> None:
        row = await assert_ingest_in_session(session, claim)
        coordinator = await assert_coordinator_in_session(session, claim.coordinator)
        if coordinator.boundary_ingest_id != row.id or coordinator.boundary_phase != "preparing":
            raise PipelineLeaseLost("read_boundary_changed")
        row.status, row.terminal_reason, row.movement_saga_id = status, reason, saga_id
        row.processed_at, row.processing_started_at = await database_now(session), None
        row.lease_token, row.lease_until = None, None
        coordinator.boundary_phase, coordinator.flush_clock = (
            "flushing",
            await database_now(session),
        )
        coordinator.flush_upper_ordinal = coordinator.next_open_ordinal - 1
        coordinator.flush_completed_ordinal, coordinator.finalizing_window_id = 0, None
        await session.flush()
        await assert_coordinator_in_session(session, claim.coordinator)

    async def next_flush_window(
        self,
        claim: CoordinatorClaim,
        *,
        quiet_seconds: float,
        max_seconds: float,
    ) -> WindowClaim | None:
        """Resume finalization before looking past a one-read→flush boundary."""
        async with access_session(self.session_factory) as session:
            coordinator = await assert_coordinator_in_session(session, claim)
            if coordinator.boundary_phase == "preparing":
                raise PipelineLeaseLost("read_preparation_unfinished")
            if coordinator.boundary_phase in {"immediate_finalizing", "flush_finalizing"}:
                window = (
                    await session.get(
                        LprDebounceWindow,
                        coordinator.finalizing_window_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                    if coordinator.finalizing_window_id is not None
                    else None
                )
                now = await database_now(session)
                _check_coordinator(coordinator, claim, now)
                if window is None:
                    raise PipelineLeaseLost("finalizing_window_unavailable")
                if window.state == "finalizing":
                    window.lease_token, window.coordinator_epoch = uuid.uuid4(), claim.epoch
                    window.lease_until = min(
                        coordinator.lease_until or now,
                        now + timedelta(seconds=COORDINATOR_LEASE_SECONDS),
                    )
                    result = WindowClaim(window.id, window.lease_token, claim)
                    ingest = await session.get(LprIngestEvent, window.best_ingest_id,
                                               with_for_update=True, populate_existing=True)
                    if ingest is None:
                        await hold_window_in_session(session, result, reason="window_evidence_unavailable")
                    elif ingest.attempt_count >= MAX_PLATE_READ_PROCESSING_ATTEMPTS:
                        await hold_window_in_session(session, result, reason="processing_attempts_exhausted")
                    else:
                        ingest.attempt_count += 1
                        await session.flush()
                        await assert_window_in_session(session, result)
                        await session.commit()
                        return result
                if window.state not in {"completed", "suppressed", "held"}:
                    raise PipelineLeaseLost("finalizing_window_outcome_unavailable")
                if coordinator.boundary_phase == "immediate_finalizing":
                    coordinator.flush_clock = now
                    coordinator.flush_upper_ordinal = coordinator.next_open_ordinal - 1
                    coordinator.flush_completed_ordinal = 0
                else:
                    coordinator.flush_completed_ordinal = window.open_ordinal
                coordinator.boundary_phase, coordinator.finalizing_window_id = "flushing", None
            if coordinator.boundary_phase is None:
                coordinator.boundary_phase, coordinator.flush_clock = (
                    "flushing",
                    await database_now(session),
                )
                coordinator.flush_upper_ordinal = coordinator.next_open_ordinal - 1
                coordinator.flush_completed_ordinal = 0
            if coordinator.boundary_phase != "flushing" or coordinator.flush_clock is None:
                raise PipelineLeaseLost("flush_boundary_unavailable")
            while True:
                page = list(
                    await session.scalars(
                        select(LprDebounceWindow)
                        .where(
                            LprDebounceWindow.state == "open",
                            LprDebounceWindow.open_ordinal
                            > int(coordinator.flush_completed_ordinal or 0),
                            LprDebounceWindow.open_ordinal
                            <= int(coordinator.flush_upper_ordinal or 0),
                        )
                        .order_by(LprDebounceWindow.open_ordinal)
                        .limit(50)
                        .with_for_update()
                    )
                )
                if not page:
                    if coordinator.boundary_ingest_id is not None:
                        ingest = await session.get(
                            LprIngestEvent,
                            coordinator.boundary_ingest_id,
                            with_for_update=True,
                            populate_existing=True,
                        )
                        if (
                            ingest is None
                            or ingest.ready_ordinal is None
                            or (ingest.status == "processing" and ingest.window_id is None)
                        ):
                            raise PipelineLeaseLost("flush_ingest_outcome_unavailable")
                        coordinator.handled_ready_ordinal = max(
                            coordinator.handled_ready_ordinal, ingest.ready_ordinal
                        )
                    coordinator.boundary_ingest_id, coordinator.boundary_phase = None, None
                    coordinator.flush_clock, coordinator.flush_upper_ordinal = None, None
                    coordinator.flush_completed_ordinal = None
                    await session.flush()
                    await assert_coordinator_in_session(session, claim)
                    await session.commit()
                    return None
                for window in page:
                    age = (coordinator.flush_clock - window.first_seen).total_seconds()
                    quiet = (coordinator.flush_clock - window.updated_at).total_seconds()
                    if age < max_seconds and quiet < quiet_seconds:
                        coordinator.flush_completed_ordinal = window.open_ordinal
                        continue
                    now = await database_now(session)
                    _check_coordinator(coordinator, claim, now)
                    window.state, window.reason = "finalizing", "debounce_timeout"
                    window.lease_token, window.coordinator_epoch = uuid.uuid4(), claim.epoch
                    window.lease_until = min(
                        coordinator.lease_until or now,
                        now + timedelta(seconds=COORDINATOR_LEASE_SECONDS),
                    )
                    coordinator.boundary_phase, coordinator.finalizing_window_id = (
                        "flush_finalizing",
                        window.id,
                    )
                    result = WindowClaim(window.id, window.lease_token, claim)
                    await session.flush()
                    await assert_window_in_session(session, result)
                    await session.commit()
                    return result
                await session.flush()
                await assert_coordinator_in_session(session, claim)
