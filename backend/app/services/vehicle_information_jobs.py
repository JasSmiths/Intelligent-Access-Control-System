"""Durable optional arrival enrichment, independent of the LPR decision worker."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import defer, selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    LprIngestEvent,
    MovementSagaRecord,
    Person,
    Vehicle,
    VehicleInformationJob,
)
from app.models.enums import AccessDirection
from app.modules.gate.base import GateState
from app.modules.notifications.base import NotificationContext
from app.services.access.completion_results import (
    InformationClaim,
    accept_information_in_session,
    assert_information_before_commit,
    check_information_claim,
    reserve_information_in_session,
)
from app.services.access.payloads import notification_facts
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.pipeline_store import database_now
from app.services.event_bus import event_bus
from app.services.movement.sessions import ARRIVAL_GATE_STATES
from app.services.notifications import get_notification_service
from app.services.settings import get_runtime_config
from app.services.vehicle_information import get_vehicle_information_service
from app.services.vehicle_information_store import apply_information

logger = get_logger(__name__)


def eligible_arrival(direction: AccessDirection, gate_state: GateState) -> bool:
    return direction == AccessDirection.ENTRY or (
        direction != AccessDirection.EXIT and gate_state in ARRIVAL_GATE_STATES
    )


async def enqueue_arrival(event: AccessEvent) -> None:
    """Called only after live access execution; historical owners do not call this."""
    # Temporary postcommit caller of the single participant reservation. Core
    # wiring will reserve this atomically from its already locked completion origin.
    async with AsyncSessionLocal() as session:
        current = await session.get(
            AccessEvent, event.id, with_for_update=True, populate_existing=True
        )
        if current is None:
            return
        total = await session.scalar(
            select(func.count())
            .select_from(LprIngestEvent)
            .where(LprIngestEvent.access_event_id == current.id)
        )
        valid, origin_at = (
            await session.execute(
                select(func.count(), func.min(LprIngestEvent.received_at))
                .select_from(LprIngestEvent)
                .join(MovementSagaRecord, LprIngestEvent.movement_saga_id == MovementSagaRecord.id)
                .where(
                    LprIngestEvent.access_event_id == current.id,
                    LprIngestEvent.status == "succeeded",
                    MovementSagaRecord.access_event_id == current.id,
                    MovementSagaRecord.registration_number == current.registration_number,
                )
            )
        ).one()
        await reserve_information_in_session(
            session, current, origin_at=origin_at if total and valid == total else None
        )
        await session.commit()
    get_vehicle_information_worker().wake()


class VehicleInformationWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="vehicle-information")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await get_vehicle_information_service().close()
        get_vehicle_information_service.cache_clear()

    def wake(self) -> None:
        self._wake.set()

    async def _claim(self) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        async with AsyncSessionLocal() as session:
            now = datetime.now(UTC)
            # Expired work is terminal; no startup sweeps requeue historical work.
            await session.execute(
                update(VehicleInformationJob)
                .where(
                    VehicleInformationJob.status.in_(("queued", "processing")),
                    VehicleInformationJob.deadline <= now,
                )
                .values(
                    status="expired",
                    outcome="deadline_expired",
                    completed_at=now,
                    lease_token=None,
                    lease_until=None,
                )
            )
            rows = list(
                (
                    await session.scalars(
                        select(VehicleInformationJob)
                        .where(
                            VehicleInformationJob.deadline > now,
                            or_(
                                (VehicleInformationJob.status == "queued")
                                & (
                                    VehicleInformationJob.lease_until.is_(None)
                                    | (VehicleInformationJob.lease_until <= now)
                                ),
                                (VehicleInformationJob.status == "processing")
                                & (VehicleInformationJob.lease_until < now),
                            ),
                        )
                        .order_by(VehicleInformationJob.created_at, VehicleInformationJob.event_id)
                        .limit(32)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            claimed = []
            for job in rows[:2]:
                token = uuid.uuid4()
                job.status, job.lease_token, job.lease_until = (
                    "processing",
                    token,
                    now + timedelta(seconds=120),
                )
                claimed.append((job.event_id, token, job.registration_number))
            await session.commit()
            return claimed

    async def _run(self) -> None:
        while True:
            try:
                jobs = await self._claim()
                if jobs:
                    await asyncio.gather(*(self._process(*job) for job in jobs))
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                except TimeoutError:
                    pass
            except Exception as exc:  # noqa: BLE001 - background boundary, cancellation propagates
                logger.warning(
                    "vehicle_information_worker_failed",
                    extra={"exception_class": type(exc).__name__},
                )
                await asyncio.sleep(2)

    async def _prepare(
        self, event_id: uuid.UUID, token: uuid.UUID, plate: str
    ) -> tuple[InformationClaim, float]:
        async with AsyncSessionLocal() as session:
            event = (
                await session.execute(
                    select(AccessEvent.id, AccessEvent.registration_number, AccessEvent.vehicle_id)
                    .where(AccessEvent.id == event_id)
                    .with_for_update()
                )
            ).one_or_none()
            vehicle = (
                (
                    await session.execute(
                        select(Vehicle.id, Vehicle.registration_number)
                        .where(Vehicle.id == event.vehicle_id)
                        .with_for_update()
                    )
                ).one_or_none()
                if event and event.vehicle_id
                else None
            )
            job = await session.get(
                VehicleInformationJob, event_id, with_for_update=True, populate_existing=True
            )
            if job is None or job.origin_at is None:
                raise PipelineLeaseLost("information_origin_unavailable")
            claim = InformationClaim(
                event_id, token, plate, job.vehicle_id, job.origin_at, job.deadline
            )
            now = await database_now(session)
            check_information_claim(job, claim, now)
            if (
                event is None
                or event.registration_number != plate
                or event.vehicle_id != job.vehicle_id
                or (job.vehicle_id and (vehicle is None or vehicle.registration_number != plate))
            ):
                raise PipelineLeaseLost("information_registration_changed")
            return claim, min(90.0, (claim.deadline - now).total_seconds())

    async def _process(self, event_id: uuid.UUID, token: uuid.UUID, plate: str) -> None:
        try:
            config = await get_runtime_config()
            claim, budget = await self._prepare(event_id, token, plate)
            async with asyncio.timeout(budget):
                # The short current-identity transaction has closed before I/O.
                lookup = await get_vehicle_information_service().lookup(plate, config=config)
                async with AsyncSessionLocal() as session:
                    event = await session.get(
                        AccessEvent, event_id, with_for_update=True, populate_existing=True
                    )
                    vehicle = (
                        await session.scalar(
                            select(Vehicle)
                            .options(defer(Vehicle.vehicle_photo_data_url))
                            .where(Vehicle.id == claim.vehicle_id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                        if claim.vehicle_id
                        else None
                    )
                    job = await session.get(
                        VehicleInformationJob,
                        event_id,
                        with_for_update=True,
                        populate_existing=True,
                    )
                    now = await database_now(session)
                    job = check_information_claim(job, claim, now)
                    old_until = job.lease_until
                    if old_until is None:
                        raise PipelineLeaseLost("information_lease_lost")
                    if (
                        event is None
                        or event.registration_number != plate
                        or event.vehicle_id != claim.vehicle_id
                        or (
                            claim.vehicle_id
                            and (vehicle is None or vehicle.registration_number != plate)
                        )
                    ):
                        job.status, job.outcome, job.completed_at = (
                            "skipped",
                            "registration_changed",
                            now,
                        )
                        job.lease_token, job.lease_until = None, None
                        await session.flush()
                        if old_until <= await database_now(session):
                            raise PipelineLeaseLost("information_lease_lost")
                        await session.commit()
                        return
                    info = lookup.information
                    if vehicle is not None:
                        info = await apply_information(
                            session, vehicle, lookup, timezone=config.site_timezone
                        )
                    event.raw_payload = {
                        **(event.raw_payload or {}),
                        "vehicle_information": info.model_dump(mode="json"),
                    }
                    await accept_information_in_session(
                        session, event, job, claim, make=info.make, colour=info.colour
                    )
                    retry_times = [
                        result.retry_at or now + timedelta(seconds=60)
                        for result in info.providers.values()
                        if result.status in {"failed", "deferred"}
                    ]
                    retry_at = min(retry_times) if retry_times else None
                    if retry_at and now < retry_at < job.deadline:
                        job.status, job.outcome = "queued", "deferred"
                        job.lease_token, job.lease_until = None, retry_at
                        await assert_information_before_commit(
                            session, event, job, claim, lease_until=old_until
                        )
                        await session.commit()
                        return
                    person = (
                        await session.scalar(
                            select(Person)
                            .options(selectinload(Person.group))
                            .where(Person.id == event.person_id)
                        )
                        if event.person_id
                        else None
                    )
                    notices: list[tuple[str, str]] = []
                    if info.mot_freshness == "fresh" and info.mot_status in {"Expired", "Overdue"}:
                        notices.append(
                            (
                                "expired_mot_detected",
                                f"{(info.mot_source or 'MOT').upper()} reports MOT {info.mot_status.lower()} for {plate}.",
                            )
                        )
                    dvla = info.providers.get("dvla")
                    if (
                        dvla
                        and dvla.status == "found"
                        and info.last_dvla_lookup_date
                        == now.astimezone(ZoneInfo(config.site_timezone)).date()
                        and info.tax_status
                        and info.tax_status.casefold() not in {"taxed", "sorn", "unknown"}
                    ):
                        notices.append(
                            (
                                "expired_tax_detected",
                                f"DVLA reports tax status {info.tax_status} for {plate}.",
                            )
                        )
                    for event_type, message in notices:
                        facts = notification_facts(
                            event,
                            person,
                            vehicle,
                            message,
                            vehicle_information=info.model_dump(mode="json"),
                        )
                        facts["vehicle_information_event_id"] = str(event.id)
                        await get_notification_service().enqueue_in_session(
                            session,
                            NotificationContext(
                                event_type=event_type,
                                subject=message,
                                severity="warning",
                                facts=facts,
                            ),
                            dispatch_id=uuid.uuid5(event.id, event_type),
                        )
                    job.status, job.outcome, job.completed_at = "completed", "enriched", now
                    job.lease_token, job.lease_until = None, None
                    await assert_information_before_commit(
                        session, event, job, claim, lease_until=old_until
                    )
                    await session.commit()
                get_notification_service().dispatcher.wake()
                await event_bus.publish(
                    "vehicle_information.updated",
                    {
                        "event_id": str(event_id),
                        "vehicle_id": str(job.vehicle_id) if job.vehicle_id else None,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - optional work cannot stop intake; no provider details
            logger.warning(
                "vehicle_information_job_failed",
                extra={"event_id": str(event_id), "exception_class": type(exc).__name__},
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(VehicleInformationJob)
                    .where(
                        VehicleInformationJob.event_id == event_id,
                        VehicleInformationJob.lease_token == token,
                    )
                    .values(
                        status="failed",
                        outcome="registration_changed"
                        if isinstance(exc, PipelineLeaseLost)
                        and str(exc) == "information_registration_changed"
                        else "enrichment_failed",
                        completed_at=datetime.now(UTC),
                        lease_token=None,
                        lease_until=None,
                    )
                )
                await session.commit()


@lru_cache
def get_vehicle_information_worker() -> VehicleInformationWorker:
    return VehicleInformationWorker()
