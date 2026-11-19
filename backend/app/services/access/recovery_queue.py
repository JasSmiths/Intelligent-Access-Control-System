"""Fenced receipt-order handoff for resident recovery. No provider or hardware I/O."""
from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import LprDebounceWindow, LprIngestEvent, Vehicle
from app.models.access_pipeline import LprRecoveryBlockedRead, LprRecoveryWindow
from app.services.access.completion_database import access_session
from app.services.access.pipeline_contracts import (
    MAX_PLATE_READ_PROCESSING_ATTEMPTS,
    CoordinatorClaim,
    IngestClaim,
    PipelineLeaseLost,
    WindowClaim,
)
from app.services.access.pipeline_store import (
    COORDINATOR_LEASE_SECONDS,
    assert_coordinator_in_session,
    assert_ingest_in_session,
    assert_window_in_session,
    database_now,
    finalization_failed_in_session,
    hold_window_in_session,
)
from app.services.durable_work import WorkTopic, notify_work


class RecoveryWindowParked(Exception):
    """The original window and its evidence deadline have a durable scheduling owner."""


@dataclass(frozen=True)
class RecoveryJob:
    claim: WindowClaim
    phone_epoch: str | None
    following: bool


async def park_in_session(session: AsyncSession, claim: WindowClaim, *, owner_id: uuid.UUID,
                          vehicle_id: uuid.UUID, phone_epoch: str | None,
                          after_window_id: uuid.UUID | None = None) -> None:
    coordinator = await assert_coordinator_in_session(session, claim.coordinator)
    window = await assert_window_in_session(session, claim)
    existing = await session.get(LprRecoveryWindow, window.id)
    if existing is not None:
        if existing.state != "following" or after_window_id is not None:
            raise PipelineLeaseLost("recovery_window_already_parked")
        existing.state, existing.phone_epoch = "waiting", phone_epoch
        existing.owner_id, existing.vehicle_id = owner_id, vehicle_id
        existing.after_window_id = None
        await session.flush()
        await assert_window_in_session(session, claim)
        return
    if (coordinator.finalizing_window_id != window.id
            or coordinator.boundary_phase not in {"immediate_finalizing", "flush_finalizing"}):
        raise PipelineLeaseLost("recovery_window_boundary_changed")
    session.add(LprRecoveryWindow(window_id=window.id, owner_id=owner_id, vehicle_id=vehicle_id,
        open_ordinal=window.open_ordinal, state="following" if after_window_id else "waiting",
        phone_epoch=phone_epoch, after_window_id=after_window_id))
    if coordinator.boundary_phase == "immediate_finalizing":
        coordinator.flush_clock = await database_now(session)
        coordinator.flush_upper_ordinal = coordinator.next_open_ordinal - 1
        coordinator.flush_completed_ordinal = 0
    else:
        coordinator.flush_completed_ordinal = window.open_ordinal
    coordinator.boundary_phase, coordinator.finalizing_window_id = "flushing", None
    await session.flush()
    await assert_window_in_session(session, claim)
    await notify_work(session, WorkTopic.LPR)


async def _prior_window(session: AsyncSession, registration: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID] | None:
    row = (await session.execute(select(LprRecoveryWindow.window_id, Vehicle.person_id, Vehicle.id)
        .select_from(Vehicle).join(LprRecoveryWindow, or_(LprRecoveryWindow.vehicle_id == Vehicle.id,
                                                         LprRecoveryWindow.owner_id == Vehicle.person_id))
        .where(Vehicle.registration_number == registration, Vehicle.person_id.is_not(None),
               LprRecoveryWindow.state.in_(("waiting", "following")))
        .order_by(LprRecoveryWindow.park_ordinal.desc()).limit(1))).first()
    return (row[0], row[1], row[2]) if row else None


async def defer_related_window(session: AsyncSession, claim: WindowClaim, registration: str) -> bool:
    """An open window for a sibling vehicle may predate parking the resident."""
    match = await _prior_window(session, registration)
    if match is None or match[0] == claim.window_id:
        return False
    earlier, owner, vehicle = match
    coordinator = await assert_coordinator_in_session(session, claim.coordinator)
    window = await assert_window_in_session(session, claim)
    if coordinator.boundary_phase == "flush_finalizing" and coordinator.finalizing_window_id == window.id:
        # A timeout looked at an older, still-open sibling while a related read
        # was parked. Keep its membership open until that read can join normally.
        window.state, window.wait_for_recovery, window.reason = "open", earlier, None
        window.lease_token, window.lease_until, window.coordinator_epoch = None, None, None
        coordinator.boundary_phase, coordinator.finalizing_window_id = "flushing", None
        coordinator.flush_completed_ordinal = window.open_ordinal
        await session.flush()
        await assert_coordinator_in_session(session, claim.coordinator)
        return True
    await park_in_session(session, claim, owner_id=owner, vehicle_id=vehicle,
                          phone_epoch=None, after_window_id=earlier)
    return True


async def defer_related_read(claim: IngestClaim, registration: str) -> bool:
    async with access_session(AsyncSessionLocal) as session:
        coordinator = await assert_coordinator_in_session(session, claim.coordinator)
        ingest = await assert_ingest_in_session(session, claim)
        if coordinator.boundary_ingest_id != ingest.id or coordinator.boundary_phase != "preparing":
            raise PipelineLeaseLost("recovery_read_boundary_changed")
        match = await _prior_window(session, registration)
        earlier = match[0] if match else None
        if earlier is None:
            return False
        session.add(LprRecoveryBlockedRead(ingest_id=ingest.id, window_id=earlier, ready_ordinal=ingest.ready_ordinal))
        ingest.status, ingest.lease_token, ingest.lease_until = "pending", None, None
        ingest.processing_started_at = None
        # This was deliberate scheduling, not a failed attempt.
        ingest.attempt_count -= 1
        coordinator.boundary_phase = "flushing"
        coordinator.flush_clock = await database_now(session)
        coordinator.flush_upper_ordinal = coordinator.next_open_ordinal - 1
        coordinator.flush_completed_ordinal = 0
        await session.flush()
        await assert_coordinator_in_session(session, claim.coordinator)
        await session.commit()
    return True


async def release_in_session(session: AsyncSession, checkpoint: LprRecoveryWindow) -> None:
    waiting = await session.scalar(select(LprRecoveryBlockedRead.ingest_id).where(
        LprRecoveryBlockedRead.window_id == checkpoint.window_id).limit(1))
    if waiting is None:
        await session.delete(checkpoint)
    else:
        checkpoint.state = "released"
    await notify_work(session, WorkTopic.LPR)


async def claim_evidence_jobs(coordinator: CoordinatorClaim, *, active: Collection[uuid.UUID],
                              capacity: int) -> tuple[list[RecoveryJob], bool]:
    if not capacity:
        return [], False
    async with access_session(AsyncSessionLocal) as session:
        previous_finished = select(LprDebounceWindow.id).where(
            LprDebounceWindow.id == LprRecoveryWindow.after_window_id,
            LprDebounceWindow.state.in_(("completed", "held"))).exists()
        query = select(LprRecoveryWindow).where(or_(LprRecoveryWindow.state == "waiting",
            (LprRecoveryWindow.state == "following") & previous_finished))
        if active:
            query = query.where(LprRecoveryWindow.window_id.not_in(active))
        checkpoints = list(await session.scalars(query.order_by(LprRecoveryWindow.open_ordinal).limit(capacity)))
        if not checkpoints:
            return [], False
        # A quiet takeover performs no row locks. Only the current fenced owner
        # can change these checkpoints, and each window is freshly locked below.
        owner = await assert_coordinator_in_session(session, coordinator)
        jobs: list[RecoveryJob] = []
        for checkpoint in checkpoints:
            window = await session.get(LprDebounceWindow, checkpoint.window_id, with_for_update=True)
            if window is None:
                raise PipelineLeaseLost("recovery_window_missing")
            if window.state != "finalizing":
                await release_in_session(session, checkpoint)
                continue
            now = await database_now(session)
            reclaim = (window.coordinator_epoch != coordinator.epoch or window.lease_token is None
                       or window.lease_until is None or window.lease_until <= now)
            if reclaim:
                window.lease_token, window.coordinator_epoch = uuid.uuid4(), coordinator.epoch
            window.lease_until = min(owner.lease_until or now, now + timedelta(seconds=COORDINATOR_LEASE_SECONDS))
            assert window.lease_token is not None
            claim = WindowClaim(window.id, window.lease_token, coordinator)
            if reclaim:
                ingest = await session.get(LprIngestEvent, window.best_ingest_id, with_for_update=True)
                if ingest is None or ingest.attempt_count >= MAX_PLATE_READ_PROCESSING_ATTEMPTS:
                    await hold_window_in_session(session, claim, reason="processing_attempts_exhausted")
                    await release_in_session(session, checkpoint)
                    continue
                ingest.attempt_count += 1
            jobs.append(RecoveryJob(claim, checkpoint.phone_epoch, checkpoint.state == "following"))
        await session.flush()
        await assert_coordinator_in_session(session, coordinator)
        await session.commit()
        return jobs, len(checkpoints) == capacity


async def finish_job(claim: WindowClaim, *, failure: str | None = None) -> None:
    async with access_session(AsyncSessionLocal) as session:
        await assert_coordinator_in_session(session, claim.coordinator)
        checkpoint = await session.get(LprRecoveryWindow, claim.window_id, with_for_update=True)
        window = await session.get(LprDebounceWindow, claim.window_id, with_for_update=True)
        if checkpoint is None or window is None:
            raise PipelineLeaseLost("recovery_window_missing")
        if window.state in {"completed", "held"}:
            await release_in_session(session, checkpoint)
        elif failure is not None:
            if await finalization_failed_in_session(session, claim, detail=failure,
                    max_attempts=MAX_PLATE_READ_PROCESSING_ATTEMPTS):
                window.lease_token = None  # Reclaim charges the durable original retry budget.
            else:
                await release_in_session(session, checkpoint)
        else:
            raise PipelineLeaseLost("recovery_window_unfinished")
        await session.flush()
        await assert_coordinator_in_session(session, claim.coordinator)
        await session.commit()
