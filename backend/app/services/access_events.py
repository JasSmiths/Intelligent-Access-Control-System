import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from difflib import SequenceMatcher
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.modules.lpr.base import PlateRead
from app.models import AccessEvent, Anomaly, Person, Presence, ScheduleAssignment, Vehicle
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    PresenceState,
    ScheduleKind,
    TimingClassification,
)
from app.modules.notifications.base import NotificationContext
from app.services.event_bus import event_bus
from app.services.notifications import get_notification_service
from app.services.settings import RuntimeConfig, get_runtime_config

logger = get_logger(__name__)


@dataclass
class DebounceWindow:
    first_seen: datetime
    updated_at: datetime
    reads: list[PlateRead] = field(default_factory=list)

    @property
    def best_read(self) -> PlateRead:
        return max(self.reads, key=lambda read: (read.confidence, read.captured_at))


class AccessEventService:
    """Coordinates plate reads into access events.

    Hardware adapters produce normalized `PlateRead` objects. This service owns
    debounce, authorization, anomaly detection, and historical classification.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[PlateRead] = asyncio.Queue()
        self._pending: list[DebounceWindow] = []
        self._worker: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._timezone = ZoneInfo(settings.site_timezone)
        self._runtime: RuntimeConfig | None = None

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stop_event.clear()
        self._worker = asyncio.create_task(self._process_queue(), name="lpr-debounce-worker")
        logger.info("access_event_service_started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            await self._worker
        await self._flush_all_pending()
        logger.info("access_event_service_stopped")

    async def enqueue_plate_read(self, read: PlateRead) -> None:
        logger.info(
            "plate_read_received",
            extra={
                "registration_number": read.registration_number,
                "confidence": read.confidence,
                "source": read.source,
            },
        )
        await self._queue.put(read)
        await event_bus.publish(
            "plate_read.received",
            {
                "registration_number": read.registration_number,
                "confidence": read.confidence,
                "source": read.source,
            },
        )

    async def _process_queue(self) -> None:
        while not self._stop_event.is_set():
            self._runtime = await get_runtime_config()
            self._timezone = ZoneInfo(self._runtime.site_timezone)
            try:
                read = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                self._add_to_debounce_window(read)
            except asyncio.TimeoutError:
                pass
            await self._flush_expired_windows()

    def _add_to_debounce_window(self, read: PlateRead) -> None:
        for window in self._pending:
            best = window.best_read
            if read.source == best.source and self._is_similar_plate(
                read.registration_number, best.registration_number
            ):
                window.reads.append(read)
                window.updated_at = read.captured_at
                return

        self._pending.append(
            DebounceWindow(first_seen=read.captured_at, updated_at=read.captured_at, reads=[read])
        )

    def _is_similar_plate(self, left: str, right: str) -> bool:
        if left == right:
            return True
        threshold = self._runtime.lpr_similarity_threshold if self._runtime else settings.lpr_similarity_threshold
        return SequenceMatcher(a=left, b=right).ratio() >= threshold

    async def _flush_expired_windows(self) -> None:
        now = datetime.now(tz=UTC)
        ready: list[DebounceWindow] = []
        waiting: list[DebounceWindow] = []

        for window in self._pending:
            quiet_for = (now - window.updated_at).total_seconds()
            total_age = (now - window.first_seen).total_seconds()
            if (
                quiet_for >= (self._runtime.lpr_debounce_quiet_seconds if self._runtime else settings.lpr_debounce_quiet_seconds)
                or total_age >= (self._runtime.lpr_debounce_max_seconds if self._runtime else settings.lpr_debounce_max_seconds)
            ):
                ready.append(window)
            else:
                waiting.append(window)

        self._pending = waiting
        for window in ready:
            await self._finalize_window(window)

    async def _flush_all_pending(self) -> None:
        pending = self._pending
        self._pending = []
        for window in pending:
            await self._finalize_window(window)

    async def _finalize_window(self, window: DebounceWindow) -> None:
        read = window.best_read
        async with AsyncSessionLocal() as session:
            vehicle = await session.scalar(
                select(Vehicle)
                .options(selectinload(Vehicle.owner))
                .where(
                    Vehicle.registration_number == read.registration_number,
                    Vehicle.is_active.is_(True),
                )
            )

            person = vehicle.owner if vehicle else None
            allowed = bool(person and person.is_active and await self._is_allowed_now(session, person))
            direction = await self._resolve_direction(session, read, person, allowed)
            decision = AccessDecision.GRANTED if allowed else AccessDecision.DENIED
            timing = (
                await self._classify_timing(session, person, direction, read.captured_at)
                if allowed and person
                else TimingClassification.UNKNOWN
            )

            event = AccessEvent(
                vehicle=vehicle,
                person_id=person.id if person else None,
                registration_number=read.registration_number,
                direction=direction,
                decision=decision,
                confidence=read.confidence,
                source=read.source,
                occurred_at=read.captured_at,
                timing_classification=timing,
                raw_payload={
                    "best": read.raw_payload,
                    "debounce": {
                        "candidate_count": len(window.reads),
                        "candidates": [
                            {
                                "registration_number": item.registration_number,
                                "confidence": item.confidence,
                                "captured_at": item.captured_at.isoformat(),
                            }
                            for item in window.reads
                        ],
                    },
                },
            )
            session.add(event)
            await session.flush()

            anomalies = await self._build_anomalies(session, event, person, allowed)
            session.add_all(anomalies)

            if allowed and person:
                await self._update_presence(session, person, event)

            await session.commit()

        await event_bus.publish(
            "access_event.finalized",
            {
                "registration_number": read.registration_number,
                "direction": direction.value,
                "decision": decision.value,
                "confidence": read.confidence,
                "timing_classification": timing.value,
                "anomaly_count": len(anomalies),
            },
        )
        for anomaly in anomalies:
            await get_notification_service().notify(
                NotificationContext(
                    event_type=anomaly.anomaly_type.value,
                    subject=event.registration_number,
                    severity=anomaly.severity.value,
                    facts={
                        "message": anomaly.message,
                        "direction": event.direction.value,
                        "decision": event.decision.value,
                        "source": event.source,
                    },
                )
            )

    async def _is_allowed_now(self, session: AsyncSession, person: Person) -> bool:
        local_now = datetime.now(tz=UTC).astimezone(self._timezone)
        assignments = (
            await session.scalars(
                select(ScheduleAssignment)
                .options(selectinload(ScheduleAssignment.time_slot))
                .where(
                    or_(
                        ScheduleAssignment.person_id == person.id,
                        and_(
                            person.group_id is not None,
                            ScheduleAssignment.group_id == person.group_id,
                        ),
                    )
                )
            )
        ).all()

        for assignment in assignments:
            slot = assignment.time_slot
            if slot.is_active and self._time_slot_matches(slot, local_now):
                return True
        return False

    def _time_slot_matches(self, slot, local_now: datetime) -> bool:
        if slot.kind == ScheduleKind.ALWAYS:
            return True
        if slot.kind == ScheduleKind.ONE_TIME:
            return bool(slot.starts_at and slot.ends_at and slot.starts_at <= local_now <= slot.ends_at)
        if slot.kind == ScheduleKind.WEEKLY:
            if slot.days_of_week and local_now.weekday() not in slot.days_of_week:
                return False
            if slot.start_time and slot.end_time:
                return self._time_in_range(slot.start_time, slot.end_time, local_now.time())
            return True
        return False

    def _time_in_range(self, start: time, end: time, current: time) -> bool:
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end

    async def _resolve_direction(
        self, session: AsyncSession, read: PlateRead, person: Person | None, allowed: bool
    ) -> AccessDirection:
        explicit = str(read.raw_payload.get("direction") or read.raw_payload.get("Direction") or "").lower()
        if explicit in {"entry", "enter", "arrival", "in"}:
            return AccessDirection.ENTRY if allowed else AccessDirection.DENIED
        if explicit in {"exit", "leave", "departure", "out"}:
            return AccessDirection.EXIT if allowed else AccessDirection.DENIED
        if not allowed or not person:
            return AccessDirection.DENIED

        presence = await session.get(Presence, person.id)
        if presence and presence.state == PresenceState.PRESENT:
            return AccessDirection.EXIT
        return AccessDirection.ENTRY

    async def _classify_timing(
        self, session: AsyncSession, person: Person, direction: AccessDirection, occurred_at: datetime
    ) -> TimingClassification:
        local_event = occurred_at.astimezone(self._timezone)
        event_minutes = local_event.hour * 60 + local_event.minute

        historical = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    AccessEvent.person_id == person.id,
                    AccessEvent.direction == direction,
                    AccessEvent.decision == AccessDecision.GRANTED,
                    AccessEvent.occurred_at < occurred_at,
                )
                .order_by(AccessEvent.occurred_at.desc())
                .limit(20)
            )
        ).all()

        if len(historical) < 3:
            return TimingClassification.UNKNOWN

        historical_minutes = [
            item.occurred_at.astimezone(self._timezone).hour * 60
            + item.occurred_at.astimezone(self._timezone).minute
            for item in historical
        ]
        average_minutes = sum(historical_minutes) / len(historical_minutes)
        delta = event_minutes - average_minutes

        if delta < -45:
            return TimingClassification.EARLIER_THAN_USUAL
        if delta > 45:
            return TimingClassification.LATER_THAN_USUAL
        return TimingClassification.NORMAL

    async def _build_anomalies(
        self, session: AsyncSession, event: AccessEvent, person: Person | None, allowed: bool
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        if not person:
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                    severity=AnomalySeverity.CRITICAL,
                    message=f"Unauthorized plate {event.registration_number} was denied.",
                    context={"registration_number": event.registration_number},
                )
            )
            return anomalies

        if not allowed:
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.OUTSIDE_SCHEDULE,
                    severity=AnomalySeverity.WARNING,
                    message=f"{person.display_name} arrived outside an allowed schedule.",
                    context={"person_id": str(person.id)},
                )
            )
            return anomalies

        presence = await session.get(Presence, person.id)

        if presence and event.direction == AccessDirection.ENTRY and presence.state == PresenceState.PRESENT:
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.DUPLICATE_ENTRY,
                    severity=AnomalySeverity.WARNING,
                    message=f"{person.display_name} produced an entry while already present.",
                    context={"person_id": str(person.id)},
                )
            )
        if presence and event.direction == AccessDirection.EXIT and presence.state == PresenceState.EXITED:
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.DUPLICATE_EXIT,
                    severity=AnomalySeverity.INFO,
                    message=f"{person.display_name} produced an exit while already marked exited.",
                    context={"person_id": str(person.id)},
                )
            )
        return anomalies

    async def _update_presence(
        self, session: AsyncSession, person: Person, event: AccessEvent
    ) -> None:
        presence = await session.get(Presence, person.id)
        if not presence:
            presence = Presence(person_id=person.id)
            session.add(presence)

        presence.state = (
            PresenceState.PRESENT
            if event.direction == AccessDirection.ENTRY
            else PresenceState.EXITED
        )
        presence.last_event_id = event.id
        presence.last_changed_at = event.occurred_at


@lru_cache
def get_access_event_service() -> AccessEventService:
    return AccessEventService()
