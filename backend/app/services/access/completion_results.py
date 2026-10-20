"""Accepted optional results and bounded visitor-link readiness handoffs.

All writes participate in the event owner's transaction. The result identity is
independent of the event's public revision; no provider or commit port lives here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessCompletionWork, AccessEvent, VehicleInformationJob, VisitorPass
from app.services.access.completion_contracts import RetainedResultReference
from app.services.access.completion_store import (
    OPTIONAL_SECONDS,
    _link_references,
    assert_completion_before_commit,
    assert_completion_in_session,
    finish_completion_in_session,
    lock_completion_event_in_session,
)
from app.services.access.pipeline_contracts import CompletionClaim, PipelineLeaseLost
from app.services.access.pipeline_store import database_now
from app.services.access.publication import mark_public_event_changed_in_session
from app.services.durable_work import WorkTopic, notify_work


def compact_text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def reference_payload(reference: RetainedResultReference) -> dict[str, Any]:
    return {
        "result_id": str(reference.result_id),
        "revision": reference.revision,
        "accepted_at": reference.accepted_at.isoformat(),
    }


async def _hold_link_in_session(
    session: AsyncSession, work: AccessCompletionWork, outcome: str
) -> None:
    work.state, work.readiness, work.outcome = "held", "held", outcome
    work.completed_at = await database_now(session)


async def _readiness_target_in_session(
    session: AsyncSession, work: AccessCompletionWork
) -> uuid.UUID | None:
    result = work.result
    if not isinstance(result, dict):
        await _hold_link_in_session(session, work, "link_checkpoint_unavailable")
        return None
    target = result.get("visitor_id")
    try:
        identity = uuid.UUID(target) if isinstance(target, str) else None
    except ValueError:
        identity = None
    if identity is None or str(identity) != target:
        await _hold_link_in_session(session, work, "canonical_target_unavailable")
        return None
    requested, applied = result.get("requested"), result.get("applied")
    if (
        not isinstance(requested, dict)
        or not isinstance(applied, dict)
        or set(applied) - {"visual", "information"}
        or (requested and not _link_references(work))
    ):
        await _hold_link_in_session(session, work, "link_references_unavailable")
        return None
    return identity


async def release_visitor_link_in_session(session: AsyncSession, event: AccessEvent) -> None:
    """Admission owner already holds Saga/Event/Visitor; never lock them from work."""
    work = await session.scalar(
        select(AccessCompletionWork)
        .where(
            AccessCompletionWork.event_id == event.id, AccessCompletionWork.stage == "visitor_link"
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if work is None or work.state in {"held", "skipped"}:
        return
    visitor_id = await _readiness_target_in_session(session, work)
    if visitor_id is None:
        return
    result = work.result
    if result.get("requested") and result.get("requested") == result.get("applied"):
        return
    # A nonlocking readiness hint cannot authorize the applicator. Its exact
    # canonical saga/consumed reservation/current arrival are locked there.
    arrived = (
        await session.scalar(
            select(VisitorPass.id).where(
                VisitorPass.id == visitor_id, VisitorPass.arrival_event_id == event.id
            )
        )
        if visitor_id is not None
        else None
    )
    if result.get("requested") and arrived is not None:
        work.readiness = "retained_result_link"
        if work.state != "leased":
            work.state, work.due_at, work.completed_at, work.outcome = (
                "queued",
                await database_now(session),
                None,
                None,
            )
            work.lease_token, work.lease_until = None, None

            await notify_work(session, WorkTopic.COMPLETION)
    elif work.state != "leased":
        work.state, work.readiness = "waiting", "canonical_arrival_and_result"


async def request_visitor_result_in_session(
    session: AsyncSession, event: AccessEvent, reference: RetainedResultReference
) -> None:
    """Event serializes result writers; a leased linker consumes the latest set.

    expected_revision identifies the immutable origin, not a frozen requested set.
    If acceptance waits behind the linker Event lock, it queues one trailing link.
    """
    work = await session.scalar(
        select(AccessCompletionWork)
        .where(
            AccessCompletionWork.event_id == event.id, AccessCompletionWork.stage == "visitor_link"
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if work is None or work.state in {"held", "skipped"}:
        return
    if await _readiness_target_in_session(session, work) is None:
        return
    if (
        work.origin_at is None
        or work.deadline is None
        or not work.origin_at <= reference.accepted_at <= work.deadline
    ):
        await _hold_link_in_session(session, work, "retained_result_origin_unavailable")
        return
    result = dict(work.result)
    requested = dict(result.get("requested") or {})
    old = requested.get(reference.kind)
    value = reference_payload(reference)
    if isinstance(old, dict):
        old_revision = old.get("revision")
        if (
            type(old_revision) is not int
            or old_revision > reference.revision
            or (old_revision == reference.revision and old != value)
        ):
            raise PipelineLeaseLost("retained_result_identity_changed")
        if old == value:
            return
    requested[reference.kind] = value
    result["requested"] = requested
    work.result = result
    await session.flush()
    await release_visitor_link_in_session(session, event)


async def prepare_visual_in_session(session: AsyncSession, claim: CompletionClaim) -> float:
    """Current identity is checked before closing this short transaction for I/O."""
    if claim.stage != "visual":
        raise ValueError("Visual preparation requires its explicit stage")
    identity = await session.scalar(
        select(AccessEvent.id).where(AccessEvent.id == claim.event_id).with_for_update()
    )
    if identity is None:
        raise PipelineLeaseLost("completion_event_unavailable")
    work = await assert_completion_in_session(session, claim)
    now = await database_now(session)
    if work.deadline is None:
        raise PipelineLeaseLost("completion_origin_changed")
    return min(90.0, (work.deadline - now).total_seconds())


async def accept_visual_in_session(
    session: AsyncSession, claim: CompletionClaim, visual: dict[str, Any]
) -> RetainedResultReference:
    if claim.stage != "visual":
        raise ValueError("Visual acceptance requires its explicit stage")
    event = await lock_completion_event_in_session(session, claim)
    work = await assert_completion_in_session(session, claim)
    now = await database_now(session)
    reference = RetainedResultReference("visual", work.id, 1, now)
    colour = next(
        (
            text
            for key in (
                "observed_vehicle_color",
                "observed_vehicle_colour",
                "vehicle_color",
                "vehicle_colour",
            )
            if (text := compact_text(visual.get(key))) is not None
        ),
        None,
    )
    work.result = {"accepted": reference_payload(reference), "projection": {"colour": colour}}
    event.raw_payload = {**(event.raw_payload or {}), "vehicle_visual_detection": visual}
    await finish_completion_in_session(session, claim, state="succeeded", outcome="visual_accepted")
    await request_visitor_result_in_session(session, event, reference)
    await mark_public_event_changed_in_session(session, event.id)
    await assert_completion_before_commit(session, claim, finished=True)
    return reference


@dataclass(frozen=True)
class InformationClaim:
    event_id: uuid.UUID
    token: uuid.UUID
    registration_number: str
    vehicle_id: uuid.UUID | None
    origin_at: datetime
    deadline: datetime


def check_information_claim(
    job: VehicleInformationJob | None, claim: InformationClaim, now: datetime
) -> VehicleInformationJob:
    if (
        job is None
        or job.event_id != claim.event_id
        or job.lease_token != claim.token
        or job.status != "processing"
        or job.lease_until is None
        or job.lease_until <= now
        or job.registration_number != claim.registration_number
        or job.vehicle_id != claim.vehicle_id
        or job.origin_at != claim.origin_at
        or job.deadline != claim.deadline
        or job.readiness != "committed_decision"
        or job.deadline <= now
        or job.deadline != claim.origin_at + timedelta(seconds=OPTIONAL_SECONDS)
    ):
        raise PipelineLeaseLost("information_lease_or_origin_lost")
    return job


async def reserve_information_in_session(
    session: AsyncSession, event: AccessEvent, *, origin_at: datetime | None
) -> None:
    """Event is already locked. Replay cannot reset any original job provenance."""
    now = await database_now(session)
    valid = isinstance(origin_at, datetime) and origin_at.tzinfo is not None
    deadline = (
        origin_at + timedelta(seconds=OPTIONAL_SECONDS) if valid and origin_at is not None else now
    )
    state = "queued" if valid and deadline > now else "expired" if valid else "held"
    await session.execute(
        insert(VehicleInformationJob)
        .values(
            event_id=event.id,
            vehicle_id=event.vehicle_id,
            registration_number=event.registration_number,
            created_at=now,
            origin_at=origin_at if valid else None,
            deadline=deadline,
            readiness="committed_decision" if valid else "held",
            status=state,
            outcome=None
            if state == "queued"
            else "deadline_expired"
            if valid
            else "original_origin_unavailable",
            completed_at=None if state == "queued" else now,
        )
        .on_conflict_do_nothing(index_elements=[VehicleInformationJob.event_id])
    )
    await notify_work(session, WorkTopic.INFORMATION)


async def accept_information_in_session(
    session: AsyncSession,
    event: AccessEvent,
    job: VehicleInformationJob,
    claim: InformationClaim,
    *,
    make: str | None,
    colour: str | None,
) -> RetainedResultReference:
    """Caller owns Event -> Vehicle -> Job; required notices remain in that commit."""
    now = await database_now(session)
    check_information_claim(job, claim, now)
    if (
        event.registration_number != claim.registration_number
        or event.vehicle_id != claim.vehicle_id
    ):
        raise PipelineLeaseLost("information_registration_changed")
    job.accepted_result_revision += 1
    job.accepted_result_at = now
    job.accepted_result = {"make": compact_text(make), "colour": compact_text(colour)}
    reference = RetainedResultReference("information", event.id, job.accepted_result_revision, now)
    await request_visitor_result_in_session(session, event, reference)
    await mark_public_event_changed_in_session(session, event.id)
    return reference


async def assert_information_before_commit(
    session: AsyncSession,
    event: AccessEvent,
    job: VehicleInformationJob,
    claim: InformationClaim,
    *,
    lease_until: datetime,
) -> None:
    """Terminal/retry writes may clear token, but no other owner sees them precommit."""
    await session.flush()
    now = await database_now(session)
    if (
        lease_until <= now
        or claim.deadline <= now
        or job.origin_at != claim.origin_at
        or job.deadline != claim.deadline
        or event.registration_number != claim.registration_number
        or event.vehicle_id != claim.vehicle_id
        or job.registration_number != claim.registration_number
        or job.vehicle_id != claim.vehicle_id
    ):
        raise PipelineLeaseLost("information_lease_or_origin_lost")
