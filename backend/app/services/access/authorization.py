"""Current recognition authority, shared by access and automation dispatch.

Only durable server observations can authorize a recognition-driven effect.
This owner reads current domain facts; it neither resolves new identity nor
reserves/consumes visitors, calls providers, or updates presence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AccessEvent, LprIngestEvent, Person, Schedule, ScheduleOverride, SystemSetting, Vehicle
from app.models.enums import AccessDecision
from app.services.access.reads import VISITOR_PASS_PAYLOAD_KEY
from app.services.schedules import evaluate_vehicle_schedule
from app.services.settings import get_runtime_config_for_session
from app.services.visitor_passes import get_visitor_pass_service

RECOGNITION_MAX_AGE_SECONDS = 60


class RecognitionAuthorizationDenied(ValueError):
    """A new hardware attempt is not authorized; an existing receipt is retained."""


@dataclass(frozen=True)
class RecognitionDispatchCheckpoint:
    event_id: uuid.UUID
    checked_at: datetime
    expires_at: datetime
    vehicle_id: uuid.UUID | None
    person_id: uuid.UUID | None
    visitor_pass_id: uuid.UUID | None


def recognition_deadline(captured_at: datetime, first_received_at: datetime) -> datetime:
    if captured_at.tzinfo is None or first_received_at.tzinfo is None:
        raise RecognitionAuthorizationDenied("Recognition timestamps must include a timezone.")
    return min(captured_at.astimezone(UTC), first_received_at.astimezone(UTC)) + timedelta(seconds=RECOGNITION_MAX_AGE_SECONDS)


async def recognition_deadline_for_event(session: AsyncSession, event: AccessEvent) -> datetime:
    received_at = await session.scalar(select(func.min(LprIngestEvent.received_at))
                                      .where(LprIngestEvent.access_event_id == event.id))
    if received_at is None:
        raise RecognitionAuthorizationDenied("A durable first recognition receipt is required.")
    return recognition_deadline(event.occurred_at, received_at)


async def assert_current_recognition_authorization(
    session: AsyncSession, *, event_id: uuid.UUID | str,
    allow_vehicle_schedule_override: bool = False, now: datetime | None = None,
) -> RecognitionDispatchCheckpoint:
    """Hold current authority through the caller's durable hardware checkpoint.

    Domain locks never surround provider I/O. Contending policy changes produce
    a definite pre-attempt denial rather than waiting in an inverse lock order.
    The returned deadline must also reach the command owner's begin_attempt.
    """
    return await _authorize(session, event_id=event_id,
        allow_vehicle_schedule_override=allow_vehicle_schedule_override, now=now, hardware=True)


async def assert_current_recognition_domain_authorization(
    session: AsyncSession, *, event_id: uuid.UUID | str,
    allow_vehicle_schedule_override: bool = False, now: datetime | None = None,
) -> RecognitionDispatchCheckpoint:
    """Current identity/visitor authority for a durable operational notification.

    This cannot authorize hardware: it deliberately leaves dispatch age to the
    notification owner's existing 900-second limit. Captured origin provenance
    remains required; replay and unknown observations never acquire authority.
    """
    return await _authorize(session, event_id=event_id,
        allow_vehicle_schedule_override=allow_vehicle_schedule_override, now=now, hardware=False)


async def _authorize(session: AsyncSession, *, event_id: uuid.UUID | str,
                     allow_vehicle_schedule_override: bool, now: datetime | None,
                     hardware: bool) -> RecognitionDispatchCheckpoint:
    try:
        # RELEASE SAVEPOINT retains successful row locks until the outer attempt
        # transaction commits. A NOWAIT refusal rolls back just this checkpoint.
        async with session.begin_nested():
            return await _current_facts(session, event_id=event_id,
                allow_vehicle_schedule_override=allow_vehicle_schedule_override,
                now=now, hardware=hardware)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise RecognitionAuthorizationDenied(
                "Current access authority is being changed; no new attempt was made.") from exc
        raise


async def _current_facts(session: AsyncSession, *, event_id: uuid.UUID | str,
                         allow_vehicle_schedule_override: bool, now: datetime | None,
                         hardware: bool) -> RecognitionDispatchCheckpoint:
    checked_at = now or await session.scalar(select(func.clock_timestamp()))
    try:
        identity = uuid.UUID(str(event_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecognitionAuthorizationDenied("A durable recognition event is required.") from exc
    event = await session.scalar(select(AccessEvent).where(AccessEvent.id == identity)
                                 .execution_options(populate_existing=True))
    if event is None:
        raise RecognitionAuthorizationDenied("Recognition event is no longer available.")
    payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    if ("backfill" in event.source.casefold() or "backfill" in payload
            or payload.get("backfilled") or payload.get("skip_automation_actions")):
        raise RecognitionAuthorizationDenied("Historical observations cannot initiate hardware.")
    deadline = await recognition_deadline_for_event(session, event)
    if hardware and checked_at > deadline:
        raise RecognitionAuthorizationDenied("Recognition is older than 60 seconds.")
    visitor_id = None
    if event.vehicle_id:
        vehicle = await session.scalar(select(Vehicle).where(Vehicle.id == event.vehicle_id)
            .with_for_update(read=True, nowait=True).execution_options(populate_existing=True))
        owner = None
        if vehicle is not None and vehicle.person_id:
            owner = await session.scalar(select(Person).where(Person.id == vehicle.person_id)
                .with_for_update(read=True, nowait=True).execution_options(populate_existing=True))
        if (vehicle is None or not vehicle.is_active or vehicle.registration_number != event.registration_number
                or vehicle.person_id != event.person_id or (vehicle.person_id and (owner is None or not owner.is_active))):
            raise RecognitionAuthorizationDenied("Vehicle or person identity is no longer authorized.")
        schedule_ids = {value for value in (vehicle.schedule_id, owner.schedule_id if owner else None) if value}
        if schedule_ids:
            await session.scalars(select(Schedule).where(Schedule.id.in_(schedule_ids)).order_by(Schedule.id)
                .with_for_update(read=True, nowait=True).execution_options(populate_existing=True))
        if owner is not None:
            # New overrides only grant access. Lock all existing rows that could
            # grant this identity so their revocation cannot race the checkpoint.
            await session.scalars(select(ScheduleOverride).where(ScheduleOverride.person_id == owner.id)
                .order_by(ScheduleOverride.id).with_for_update(read=True, nowait=True)
                .execution_options(populate_existing=True))
        await session.scalars(select(SystemSetting).where(SystemSetting.key.in_(
            ["site_timezone", "schedule_default_policy"])).order_by(SystemSetting.key)
            .with_for_update(read=True, nowait=True).execution_options(populate_existing=True))
        # Refresh relationship snapshots after their authoritative rows are locked.
        vehicle = await session.scalar(select(Vehicle).where(Vehicle.id == vehicle.id)
            .options(selectinload(Vehicle.schedule), selectinload(Vehicle.owner).selectinload(Person.schedule))
            .execution_options(populate_existing=True))
        config = await get_runtime_config_for_session(session)
        evaluation = await evaluate_vehicle_schedule(session, vehicle, checked_at,
            timezone_name=config.site_timezone, default_policy=config.schedule_default_policy)
        if not evaluation.allowed and not allow_vehicle_schedule_override:
            raise RecognitionAuthorizationDenied("Current vehicle permissions do not allow access.")
    else:
        visitor = payload.get(VISITOR_PASS_PAYLOAD_KEY)
        if not isinstance(visitor, dict) or not visitor.get("id") or event.decision != AccessDecision.GRANTED:
            raise RecognitionAuthorizationDenied("Unknown recognition cannot initiate hardware.")
        visitor_service = get_visitor_pass_service()
        validate_visitor = (visitor_service.assert_dispatch_validity if hardware
                            else visitor_service.assert_arrival_notification_validity)
        visitor_id = await validate_visitor(
            session, event=event, visitor_pass_id=visitor["id"], checked_at=checked_at)
    return RecognitionDispatchCheckpoint(event.id, checked_at, deadline, event.vehicle_id, event.person_id, visitor_id)
