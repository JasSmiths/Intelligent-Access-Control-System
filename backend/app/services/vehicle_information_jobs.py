"""Durable optional arrival enrichment, independent of the LPR decision worker."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
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
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.vehicle_information import get_vehicle_information_service
from app.services.vehicle_information_store import apply_information

logger = get_logger(__name__)
INFORMATION_SLOTS = 2
INFORMATION_CLAIM_PAGE = 32
INFORMATION_LEASE_SECONDS = 120
INFORMATION_RENEWAL_SECONDS = 10
INFORMATION_TIMEOUT_SECONDS = 90


@asynccontextmanager
async def _worker_session() -> AsyncIterator[AsyncSession]:
    """Cancel active work once, then retain its actual session/connection close."""
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await drain_owned(session.close())


def eligible_arrival(direction: AccessDirection, gate_state: GateState) -> bool:
    return direction == AccessDirection.ENTRY or (
        direction != AccessDirection.EXIT and gate_state in ARRIVAL_GATE_STATES
    )


async def enqueue_arrival(event: AccessEvent) -> None:
    """Called only after live access execution; historical owners do not call this."""
    # Temporary postcommit caller of the single participant reservation. Core
    # wiring will reserve this atomically from its already locked completion origin.
    async with _worker_session() as session:
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
    """Two actual process-local slots, including provider cancellation cleanup."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._operations: set[asyncio.Task[None]] = set()
        self._wake = asyncio.Event()
        self._stopping = False
        self._scan_progress = False

    async def start(self) -> None:
        if self._task is None:
            if self._cleanup is not None and not self._cleanup.done():
                raise RuntimeError("information_cleanup_in_progress")
            self._cleanup = None
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="vehicle-information")

    async def stop(self) -> None:
        self._stopping = True
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop_owned(), name="vehicle-information-stop")
        await drain_owned(self._cleanup)

    async def _stop_owned(self) -> None:
        if self._task is not None:
            if not self._task.done() and not self._task.cancelling():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        # Shared provider clients belong to main's resources, after all users.

    def wake(self) -> None:
        self._wake.set()

    async def _claim(self, capacity: int = INFORMATION_SLOTS) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
        self._scan_progress = False
        if capacity <= 0:
            return []
        capacity = min(capacity, INFORMATION_SLOTS)
        async with _worker_session() as session:
            now = await database_now(session)
            # Scan metadata only. A candidate is not a lease or an admitted slot.
            identities = list(await session.scalars(
                select(VehicleInformationJob.event_id)
                .where(
                    VehicleInformationJob.status.in_(("queued", "processing")),
                    or_(
                        VehicleInformationJob.deadline <= now,
                        VehicleInformationJob.lease_until.is_(None),
                        VehicleInformationJob.lease_until <= now,
                    ),
                )
                .order_by(VehicleInformationJob.created_at, VehicleInformationJob.event_id)
                .limit(INFORMATION_CLAIM_PAGE)
            ))
            claimed: list[tuple[uuid.UUID, uuid.UUID, str]] = []
            leased: list[VehicleInformationJob] = []
            for identity in identities:
                job = await session.scalar(select(VehicleInformationJob)
                    .where(VehicleInformationJob.event_id == identity)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True))
                now = await database_now(session)
                if job is None or job.status not in {"queued", "processing"}:
                    continue
                if job.deadline <= now:
                    job.status, job.outcome, job.completed_at = "expired", "deadline_expired", now
                    job.lease_token, job.lease_until = None, None
                    self._scan_progress = True
                    continue
                if job.lease_until is not None and job.lease_until > now:
                    continue  # Both active processing leases and queued retry due times.
                if (job.origin_at is None or job.readiness != "committed_decision"
                    or job.deadline != job.origin_at + timedelta(seconds=300)):
                    job.status, job.readiness, job.outcome = "held", "held", "original_origin_unavailable"
                    job.completed_at, job.lease_token, job.lease_until = now, None, None
                    self._scan_progress = True
                    continue
                token = uuid.uuid4()
                job.status, job.lease_token, job.lease_until = (
                    "processing", token, now + timedelta(seconds=INFORMATION_LEASE_SECONDS)
                )
                leased.append(job)
                claimed.append((job.event_id, token, job.registration_number))
                if len(claimed) == capacity:
                    break
            await session.flush()
            final = await database_now(session)
            if any(job.lease_until is None or job.lease_until <= final or job.deadline <= final for job in leased):
                raise PipelineLeaseLost("information_claim_expired")
            await session.commit()
            return claimed

    async def _run(self) -> None:
        try:
            while not self._stopping:
                try:
                    for task in tuple(self._operations):
                        if task.done():
                            self._operations.remove(task)
                            task.result()
                    self._wake.clear()
                    jobs = await self._claim(INFORMATION_SLOTS - len(self._operations))
                    for job in jobs:
                        self._operations.add(asyncio.create_task(
                            self._process(*job), name="vehicle-information-operation"
                        ))
                    if self._operations:
                        await asyncio.wait(tuple(self._operations), timeout=0.5,
                                           return_when=asyncio.FIRST_COMPLETED)
                    elif not self._scan_progress:
                        try:
                            await asyncio.wait_for(self._wake.wait(), timeout=2)
                        except TimeoutError:
                            pass
                except Exception as exc:  # noqa: BLE001 - durable optional work remains recoverable
                    logger.warning("vehicle_information_worker_failed",
                                   extra={"exception_class": type(exc).__name__})
                    await asyncio.sleep(2)
        finally:
            children = tuple(self._operations)
            for child in children:
                if not child.done() and not child.cancelling():
                    child.cancel()
            await drain_owned(asyncio.gather(*children, return_exceptions=True))
            self._operations.clear()

    async def _renew_claim(self, claim: InformationClaim) -> None:
        async with _worker_session() as session:
            job = await session.get(VehicleInformationJob, claim.event_id,
                                    with_for_update=True, populate_existing=True)
            now = await database_now(session)
            job = check_information_claim(job, claim, now)
            old_until = job.lease_until
            if old_until is None:
                raise PipelineLeaseLost("information_lease_lost")
            job.lease_until = now + timedelta(seconds=INFORMATION_LEASE_SECONDS)
            await session.flush()
            final = await database_now(session)
            if old_until <= final or claim.deadline <= final:
                raise PipelineLeaseLost("information_lease_lost")
            await session.commit()

    async def _renew(self, claim: InformationClaim, operation: asyncio.Task[None], lost: asyncio.Event) -> None:
        try:
            while not operation.done():
                await asyncio.sleep(INFORMATION_RENEWAL_SECONDS)
                if not operation.done():
                    await self._renew_claim(claim)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - lost database authority cancels the owned optional operation
            lost.set()
            if not operation.done() and not operation.cancelling():
                operation.cancel()

    async def _fail(self, event_id: uuid.UUID, token: uuid.UUID, plate: str, error: Exception,
                    claim: InformationClaim | None = None) -> None:
        async with _worker_session() as session:
            job = await session.get(VehicleInformationJob, event_id,
                                    with_for_update=True, populate_existing=True)
            now = await database_now(session)
            if (job is None or job.status != "processing" or job.lease_token != token
                or job.registration_number != plate or job.lease_until is None or job.lease_until <= now):
                return
            if claim is not None and (job.vehicle_id != claim.vehicle_id
                or job.origin_at != claim.origin_at or job.deadline != claim.deadline):
                return
            old_until = job.lease_until
            invalid = (job.origin_at is None or job.readiness != "committed_decision"
                       or job.deadline != job.origin_at + timedelta(seconds=300))
            job.status = "held" if invalid else "expired" if job.deadline <= now else "failed"
            if invalid:
                job.readiness = "held"
            job.outcome = ("original_origin_unavailable" if invalid else
                           "deadline_expired" if job.status == "expired" else
                           "registration_changed" if isinstance(error, PipelineLeaseLost)
                           and str(error) == "information_registration_changed" else "enrichment_failed")
            job.completed_at, job.lease_token, job.lease_until = now, None, None
            await session.flush()
            if old_until <= await database_now(session):
                raise PipelineLeaseLost("information_failure_lease_lost")
            await session.commit()

    async def _prepare(
        self, event_id: uuid.UUID, token: uuid.UUID, plate: str
    ) -> tuple[InformationClaim, float]:
        async with _worker_session() as session:
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
            if job.lease_until is None:
                raise PipelineLeaseLost("information_lease_lost")
            return claim, min(float(INFORMATION_TIMEOUT_SECONDS),
                              (claim.deadline - now).total_seconds(),
                              (job.lease_until - now).total_seconds())

    async def _process(self, event_id: uuid.UUID, token: uuid.UUID, plate: str) -> None:
        # Timeout/lease-loss owns one cancellation of the whole operation. Stop
        # cannot re-cancel active database/provider cleanup through its parent.
        child = asyncio.create_task(self._process_owned(event_id, token, plate),
                                    name="vehicle-information-job")
        try:
            await asyncio.shield(child)
        finally:
            if not child.done() and not child.cancelling():
                child.cancel()
            await drain_owned(asyncio.gather(child, return_exceptions=True))

    async def _process_owned(self, event_id: uuid.UUID, token: uuid.UUID, plate: str) -> None:
        operation: asyncio.Task[None] | None = None
        renewal: asyncio.Task[None] | None = None
        lost = asyncio.Event()
        error: Exception | None = None
        claim: InformationClaim | None = None
        try:
            async with asyncio.timeout(INFORMATION_TIMEOUT_SECONDS):
                config = await get_runtime_config()
                claim, budget = await self._prepare(event_id, token, plate)
                operation = asyncio.create_task(self._execute(claim, budget, config),
                                                name="vehicle-information-result")
                renewal = asyncio.create_task(self._renew(claim, operation, lost),
                                              name="vehicle-information-renew")
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    parent = asyncio.current_task()
                    if lost.is_set() and (parent is None or not parent.cancelling()):
                        raise PipelineLeaseLost("information_lease_lost") from None
                    raise
        except Exception as exc:  # noqa: BLE001 - optional failure is fenced after owned children settle
            error = PipelineLeaseLost("information_lease_lost") if lost.is_set() else exc
        finally:
            children = tuple(child for child in (operation, renewal) if child is not None)
            for child in children:
                if not child.done() and not child.cancelling():
                    child.cancel()
            await drain_owned(asyncio.gather(*children, return_exceptions=True))
        if error is not None:
            logger.warning("vehicle_information_job_failed",
                           extra={"event_id": str(event_id), "exception_class": type(error).__name__})
            await self._fail(event_id, token, plate, error, claim)

    async def _execute(self, claim: InformationClaim, budget: float, config: RuntimeConfig) -> None:
        event_id, plate = claim.event_id, claim.registration_number
        async with asyncio.timeout(budget):
            # The short current-identity transaction has closed before I/O.
            lookup = await get_vehicle_information_service().lookup(plate, config=config)
            async with _worker_session() as session:
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
                    if min(old_until, claim.deadline) <= await database_now(session):
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


@lru_cache
def get_vehicle_information_worker() -> VehicleInformationWorker:
    return VehicleInformationWorker()
