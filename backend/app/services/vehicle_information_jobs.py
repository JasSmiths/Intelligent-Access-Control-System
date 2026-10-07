"""Durable optional arrival enrichment, independent of the LPR decision worker."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import defer, selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Person, Vehicle, VehicleInformationJob, VisitorPass
from app.models.enums import AccessDirection
from app.modules.gate.base import GateState
from app.modules.notifications.base import NotificationContext
from app.services.access.payloads import notification_facts
from app.services.event_bus import event_bus
from app.services.movement.sessions import ARRIVAL_GATE_STATES
from app.services.notifications import get_notification_service
from app.services.settings import get_runtime_config
from app.services.vehicle_information import get_vehicle_information_service
from app.services.vehicle_information_store import apply_information
from app.services.visitor_passes import get_visitor_pass_service

logger = get_logger(__name__)


def eligible_arrival(direction: AccessDirection, gate_state: GateState) -> bool:
    return direction == AccessDirection.ENTRY or (direction != AccessDirection.EXIT and gate_state in ARRIVAL_GATE_STATES)


async def enqueue_arrival(event: AccessEvent) -> None:
    """Called only after live access execution; historical owners do not call this."""
    now = datetime.now(UTC)
    occurred = event.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    if occurred < now - timedelta(minutes=5):
        return
    async with AsyncSessionLocal() as session:
        await session.execute(insert(VehicleInformationJob).values(
            event_id=event.id, vehicle_id=event.vehicle_id, registration_number=event.registration_number,
            created_at=now, deadline=now + timedelta(minutes=5), status="queued",
        ).on_conflict_do_nothing(index_elements=[VehicleInformationJob.event_id]))
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
            await session.execute(update(VehicleInformationJob).where(
                VehicleInformationJob.status.in_(("queued", "processing")), VehicleInformationJob.deadline <= now
            ).values(status="expired", outcome="deadline_expired", completed_at=now, lease_token=None, lease_until=None))
            rows = list((await session.scalars(select(VehicleInformationJob).where(
                VehicleInformationJob.deadline > now,
                or_((VehicleInformationJob.status == "queued") &
                    (VehicleInformationJob.lease_until.is_(None) | (VehicleInformationJob.lease_until <= now)),
                    (VehicleInformationJob.status == "processing") & (VehicleInformationJob.lease_until < now)),
            ).order_by(VehicleInformationJob.created_at, VehicleInformationJob.event_id).limit(32)
                .with_for_update(skip_locked=True))).all())
            claimed = []
            for job in rows[:2]:
                token = uuid.uuid4()
                job.status, job.lease_token, job.lease_until = "processing", token, now + timedelta(seconds=120)
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
                logger.warning("vehicle_information_worker_failed", extra={"exception_class": type(exc).__name__})
                await asyncio.sleep(2)

    async def _process(self, event_id: uuid.UUID, token: uuid.UUID, plate: str) -> None:
        try:
            async with asyncio.timeout(90):
                config = await get_runtime_config()
                # No ORM session or checked-out connection survives provider I/O.
                lookup = await get_vehicle_information_service().lookup(plate, config=config)
                async with AsyncSessionLocal() as session:
                    job = await session.get(VehicleInformationJob, event_id, with_for_update=True)
                    now = datetime.now(UTC)
                    if not job or job.lease_token != token or job.status != "processing":
                        return
                    if job.deadline <= now:
                        job.status, job.outcome, job.completed_at = "expired", "deadline_expired", now
                        await session.commit()
                        return
                    event = await session.get(AccessEvent, event_id, with_for_update=True)
                    if event is None or event.registration_number != plate:
                        job.status, job.outcome, job.completed_at = "skipped", "registration_changed", now
                        await session.commit()
                        return
                    info = lookup.information
                    vehicle = None
                    if job.vehicle_id:
                        vehicle = await session.scalar(select(Vehicle).options(defer(Vehicle.vehicle_photo_data_url))
                            .where(Vehicle.id == job.vehicle_id).with_for_update())
                        if not vehicle or vehicle.registration_number != plate:
                            job.status, job.outcome, job.completed_at = "skipped", "registration_changed", now
                            await session.commit()
                            return
                        info = await apply_information(session, vehicle, lookup, timezone=config.site_timezone)
                    event.raw_payload = {**(event.raw_payload or {}), "vehicle_information": info.model_dump(mode="json")}
                    visitor = await session.scalar(select(VisitorPass).where(VisitorPass.arrival_event_id == event.id).with_for_update())
                    if visitor:
                        await get_visitor_pass_service().enrich_arrival(session, visitor, event_id=event.id,
                            vehicle_information=info.model_dump(mode="json"), visual_detection=None)
                    retry_times = [result.retry_at or now + timedelta(seconds=60) for result in info.providers.values()
                        if result.status in {"failed", "deferred"}]
                    retry_at = min(retry_times) if retry_times else None
                    if retry_at and now < retry_at < job.deadline:
                        job.status, job.outcome = "queued", "deferred"
                        job.lease_token, job.lease_until = None, retry_at
                        await session.commit()
                        return
                    person = await session.scalar(select(Person).options(selectinload(Person.group)).where(Person.id == event.person_id)) if event.person_id else None
                    notices: list[tuple[str, str]] = []
                    if info.mot_freshness == "fresh" and info.mot_status in {"Expired", "Overdue"}:
                        notices.append(("expired_mot_detected", f"{(info.mot_source or 'MOT').upper()} reports MOT {info.mot_status.lower()} for {plate}."))
                    dvla = info.providers.get("dvla")
                    if (dvla and dvla.status == "found"
                            and info.last_dvla_lookup_date == now.astimezone(ZoneInfo(config.site_timezone)).date() and info.tax_status
                            and info.tax_status.casefold() not in {"taxed", "sorn", "unknown"}):
                        notices.append(("expired_tax_detected", f"DVLA reports tax status {info.tax_status} for {plate}."))
                    for event_type, message in notices:
                        facts = notification_facts(event, person, vehicle, message, vehicle_information=info.model_dump(mode="json"))
                        facts["vehicle_information_event_id"] = str(event.id)
                        await get_notification_service().enqueue_in_session(session, NotificationContext(
                            event_type=event_type, subject=message, severity="warning", facts=facts,
                        ), dispatch_id=uuid.uuid5(event.id, event_type))
                    job.status, job.outcome, job.completed_at = "completed", "enriched", now
                    job.lease_token, job.lease_until = None, None
                    await session.commit()
                get_notification_service().dispatcher.wake()
                await event_bus.publish("vehicle_information.updated", {"event_id": str(event_id),
                    "vehicle_id": str(job.vehicle_id) if job.vehicle_id else None})
        except Exception as exc:  # noqa: BLE001 - optional work cannot stop intake; no provider details
            logger.warning("vehicle_information_job_failed", extra={"event_id": str(event_id), "exception_class": type(exc).__name__})
            async with AsyncSessionLocal() as session:
                await session.execute(update(VehicleInformationJob).where(
                    VehicleInformationJob.event_id == event_id, VehicleInformationJob.lease_token == token,
                ).values(status="failed", outcome="enrichment_failed", completed_at=datetime.now(UTC), lease_token=None, lease_until=None))
                await session.commit()


@lru_cache
def get_vehicle_information_worker() -> VehicleInformationWorker:
    return VehicleInformationWorker()
