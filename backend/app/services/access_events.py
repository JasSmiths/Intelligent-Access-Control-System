import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.home_assistant.covers import command_cover, enabled_cover_entities
from app.modules.registry import UnsupportedModuleError, get_gate_controller
from app.modules.lpr.base import PlateRead
from app.models import AccessEvent, Anomaly, Person, Presence, Vehicle
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    PresenceState,
    TimingClassification,
)
from app.modules.notifications.base import NotificationContext
from app.services.event_bus import event_bus
from app.services.notifications import get_notification_service
from app.services.schedules import evaluate_schedule_id, evaluate_vehicle_schedule
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
            try:
                await self._finalize_window(window)
            except Exception:
                logger.exception(
                    "access_event_finalize_failed",
                    extra={
                        "candidate_count": len(window.reads),
                        "best_registration_number": window.best_read.registration_number,
                    },
                )
                await event_bus.publish(
                    "access_event.finalize_failed",
                    {
                        "registration_number": window.best_read.registration_number,
                        "candidate_count": len(window.reads),
                    },
                )

    async def _flush_all_pending(self) -> None:
        pending = self._pending
        self._pending = []
        for window in pending:
            try:
                await self._finalize_window(window)
            except Exception:
                logger.exception(
                    "access_event_finalize_failed",
                    extra={
                        "candidate_count": len(window.reads),
                        "best_registration_number": window.best_read.registration_number,
                    },
                )

    async def _finalize_window(self, window: DebounceWindow) -> None:
        read = window.best_read
        async with AsyncSessionLocal() as session:
            vehicle = await session.scalar(
                select(Vehicle)
                .options(
                    selectinload(Vehicle.schedule),
                    selectinload(Vehicle.owner).selectinload(Person.group),
                    selectinload(Vehicle.owner).selectinload(Person.schedule),
                )
                .where(
                    Vehicle.registration_number == read.registration_number,
                    Vehicle.is_active.is_(True),
                )
            )

            person = vehicle.owner if vehicle else None
            runtime = self._runtime or await get_runtime_config()
            identity_active = bool(vehicle and (not person or person.is_active))
            schedule_evaluation = (
                await evaluate_vehicle_schedule(
                    session,
                    vehicle,
                    read.captured_at,
                    timezone_name=runtime.site_timezone,
                    default_policy=runtime.schedule_default_policy,
                )
                if vehicle and identity_active
                else None
            )
            allowed = bool(identity_active and schedule_evaluation and schedule_evaluation.allowed)
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
                    "schedule": {
                        "allowed": schedule_evaluation.allowed if schedule_evaluation else False,
                        "source": schedule_evaluation.source if schedule_evaluation else "none",
                        "schedule_id": str(schedule_evaluation.schedule_id) if schedule_evaluation and schedule_evaluation.schedule_id else None,
                        "schedule_name": schedule_evaluation.schedule_name if schedule_evaluation else None,
                        "reason": schedule_evaluation.reason if schedule_evaluation else "No active vehicle identity matched.",
                    },
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

            anomalies = await self._build_anomalies(session, event, person, vehicle, allowed)
            session.add_all(anomalies)

            if allowed and person:
                await self._update_presence(session, person, event)

            await session.commit()

        await event_bus.publish(
            "access_event.finalized",
            {
                "event_id": str(event.id),
                "registration_number": read.registration_number,
                "direction": direction.value,
                "decision": decision.value,
                "confidence": read.confidence,
                "source": event.source,
                "occurred_at": event.occurred_at.isoformat(),
                "timing_classification": timing.value,
                "anomaly_count": len(anomalies),
            },
        )
        if decision == AccessDecision.GRANTED and direction == AccessDirection.ENTRY:
            gate_opened = await self._open_gate_for_event(event, person)
            if gate_opened and person:
                await get_notification_service().notify(
                    NotificationContext(
                        event_type="authorized_entry",
                        subject=f"{person.display_name} arrived at the gate",
                        severity=AnomalySeverity.INFO.value,
                        facts=self._notification_facts(
                            event,
                            person,
                            vehicle,
                            self._authorized_entry_message(person, vehicle),
                        ),
                    )
                )

        for anomaly in anomalies:
            await get_notification_service().notify(
                NotificationContext(
                    event_type=anomaly.anomaly_type.value,
                    subject=event.registration_number,
                    severity=anomaly.severity.value,
                    facts=self._notification_facts(event, person, vehicle, anomaly.message),
                )
                )

    async def _open_gate_for_event(self, event: AccessEvent, person: Person | None) -> bool:
        reason = (
            f"Automatic LPR grant for {event.registration_number}"
            f"{f' ({person.display_name})' if person else ''}"
        )
        gate_opened = False
        try:
            gate = get_gate_controller(settings.gate_controller)
            result = await gate.open_gate(reason)
        except UnsupportedModuleError as exc:
            logger.error(
                "gate_controller_unavailable",
                extra={
                    "registration_number": event.registration_number,
                    "event_id": str(event.id),
                    "error": str(exc),
                },
            )
            await event_bus.publish(
                "gate.open_failed",
                {
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "detail": str(exc),
                },
            )
            await get_notification_service().notify(
                NotificationContext(
                    event_type="gate_open_failed",
                    subject=event.registration_number,
                    severity=AnomalySeverity.CRITICAL.value,
                    facts=self._notification_facts(
                        event,
                        person,
                        event.vehicle,
                        str(exc),
                    ),
                )
            )
        else:
            event_type = "gate.open_requested" if result.accepted else "gate.open_failed"
            await event_bus.publish(
                event_type,
                {
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "accepted": result.accepted,
                    "state": result.state.value,
                    "detail": result.detail,
                },
            )
            if result.accepted:
                logger.info(
                    "gate_open_requested_for_access_event",
                    extra={
                        "registration_number": event.registration_number,
                        "event_id": str(event.id),
                        "state": result.state.value,
                    },
                )
                gate_opened = True
            else:
                logger.error(
                    "gate_open_failed_for_access_event",
                    extra={
                        "registration_number": event.registration_number,
                        "event_id": str(event.id),
                        "state": result.state.value,
                        "detail": result.detail,
                    },
                )
                await get_notification_service().notify(
                    NotificationContext(
                        event_type="gate_open_failed",
                        subject=event.registration_number,
                        severity=AnomalySeverity.CRITICAL.value,
                        facts=self._notification_facts(
                            event,
                            person,
                            event.vehicle,
                            result.detail or "Automatic gate open command failed.",
                        ),
                    )
                )

        await self._open_garage_doors_for_event(event, person, reason)
        return gate_opened

    async def _open_garage_doors_for_event(
        self, event: AccessEvent, person: Person | None, reason: str
    ) -> None:
        if not person or not person.garage_door_entity_ids:
            return

        config = await get_runtime_config()
        selected_ids = set(person.garage_door_entity_ids)
        entities = [
            entity
            for entity in enabled_cover_entities(
                config.home_assistant_garage_door_entities,
                default_open_service=config.home_assistant_gate_open_service,
            )
            if str(entity["entity_id"]) in selected_ids
        ]
        if not entities:
            return

        client = HomeAssistantClient()
        async with AsyncSessionLocal() as schedule_session:
            schedule_evaluations = {
                str(entity["entity_id"]): await evaluate_schedule_id(
                    schedule_session,
                    entity.get("schedule_id"),
                    event.occurred_at,
                    timezone_name=config.site_timezone,
                    default_policy=config.schedule_default_policy,
                    source="garage_door",
                )
                for entity in entities
            }

        for entity in entities:
            schedule_evaluation = schedule_evaluations[str(entity["entity_id"])]
            if not schedule_evaluation.allowed:
                detail = schedule_evaluation.reason or f"{entity.get('name') or entity['entity_id']} is outside its assigned schedule."
                await event_bus.publish(
                    "garage_door.open_failed",
                    {
                        "event_id": str(event.id),
                        "registration_number": event.registration_number,
                        "person_id": str(person.id),
                        "person": person.display_name,
                        "entity_id": str(entity["entity_id"]),
                        "name": str(entity.get("name") or entity["entity_id"]),
                        "accepted": False,
                        "state": "schedule_denied",
                        "detail": detail,
                    },
                )
                await get_notification_service().notify(
                    NotificationContext(
                        event_type="garage_door_open_failed",
                        subject=event.registration_number,
                        severity=AnomalySeverity.WARNING.value,
                        facts=self._notification_facts(
                            event,
                            person,
                            event.vehicle,
                            detail,
                            garage_door=str(entity.get("name") or entity["entity_id"]),
                            entity_id=str(entity["entity_id"]),
                        ),
                    )
                )
                continue

            outcome = await command_cover(client, entity, "open", reason)
            event_type = "garage_door.open_requested" if outcome.accepted else "garage_door.open_failed"
            await event_bus.publish(
                event_type,
                {
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "person_id": str(person.id),
                    "person": person.display_name,
                    "entity_id": outcome.entity_id,
                    "name": outcome.name,
                    "accepted": outcome.accepted,
                    "state": outcome.state,
                    "detail": outcome.detail,
                },
            )
            if outcome.accepted:
                logger.info(
                    "garage_door_open_requested_for_access_event",
                    extra={
                        "registration_number": event.registration_number,
                        "event_id": str(event.id),
                        "person_id": str(person.id),
                        "entity_id": outcome.entity_id,
                        "state": outcome.state,
                    },
                )
                continue

            logger.error(
                "garage_door_open_failed_for_access_event",
                extra={
                    "registration_number": event.registration_number,
                    "event_id": str(event.id),
                    "person_id": str(person.id),
                    "entity_id": outcome.entity_id,
                    "detail": outcome.detail,
                },
            )
            await get_notification_service().notify(
                NotificationContext(
                    event_type="garage_door_open_failed",
                    subject=event.registration_number,
                    severity=AnomalySeverity.CRITICAL.value,
                    facts=self._notification_facts(
                        event,
                        person,
                        event.vehicle,
                        outcome.detail or f"Automatic garage door open command failed for {outcome.name}.",
                        garage_door=outcome.name,
                        entity_id=outcome.entity_id,
                    ),
                )
            )

    def _notification_facts(
        self,
        event: AccessEvent,
        person: Person | None,
        vehicle: Vehicle | None,
        message: str,
        **extra: str,
    ) -> dict[str, str]:
        group = person.group if person else None
        vehicle_display_name = self._vehicle_display_name(vehicle, event.registration_number)
        facts = {
            "message": message,
            "first_name": person.first_name if person else "",
            "last_name": person.last_name if person else "",
            "display_name": person.display_name if person else "",
            "group_name": group.name if group else "",
            "vehicle_registration_number": event.registration_number,
            "registration_number": event.registration_number,
            "vehicle_display_name": vehicle_display_name,
            "vehicle_make": vehicle.make or "" if vehicle else "",
            "vehicle_model": vehicle.model or "" if vehicle else "",
            "vehicle_color": vehicle.color or "" if vehicle else "",
            "object_pronoun": "them",
            "possessive_determiner": "their",
            "direction": event.direction.value,
            "decision": event.decision.value,
            "source": event.source,
            "timing_classification": event.timing_classification.value,
            "occurred_at": event.occurred_at.isoformat(),
        }
        facts.update(extra)
        return facts

    def _authorized_entry_message(self, person: Person, vehicle: Vehicle | None) -> str:
        first_name = person.first_name or person.display_name.split(" ", 1)[0]
        possessive = f"{first_name}'" if first_name.lower().endswith("s") else f"{first_name}'s"
        vehicle_label = self._vehicle_display_name(vehicle, "")
        if vehicle_label:
            return f"{possessive} {vehicle_label} has been detected at the gate. I've let them in."
        return f"{person.display_name} has been detected at the gate. I've let them in."

    def _vehicle_display_name(self, vehicle: Vehicle | None, fallback: str) -> str:
        if not vehicle:
            return fallback
        parts = [vehicle.make, vehicle.model]
        label = " ".join(part for part in parts if part)
        return label or vehicle.description or vehicle.registration_number or fallback

    async def _resolve_direction(
        self, session: AsyncSession, read: PlateRead, person: Person | None, allowed: bool
    ) -> AccessDirection:
        explicit = str(read.raw_payload.get("direction") or read.raw_payload.get("Direction") or "").lower()
        if explicit in {"entry", "enter", "arrival", "in"}:
            return AccessDirection.ENTRY if allowed else AccessDirection.DENIED
        if explicit in {"exit", "leave", "departure", "out"}:
            return AccessDirection.EXIT if allowed else AccessDirection.DENIED
        if not allowed:
            return AccessDirection.DENIED
        if not person:
            return AccessDirection.ENTRY

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
        self,
        session: AsyncSession,
        event: AccessEvent,
        person: Person | None,
        vehicle: Vehicle | None,
        allowed: bool,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        if not vehicle:
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
            subject = person.display_name if person else event.registration_number
            anomalies.append(
                Anomaly(
                    event=event,
                    anomaly_type=AnomalyType.OUTSIDE_SCHEDULE,
                    severity=AnomalySeverity.WARNING,
                    message=f"{subject} was denied by schedule or access policy.",
                    context={
                        "person_id": str(person.id) if person else None,
                        "vehicle_id": str(vehicle.id),
                    },
                )
            )
            return anomalies

        if not person:
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
