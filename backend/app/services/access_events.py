import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.providers import analyze_image_with_provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.modules.gate.base import GateState
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError
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
from app.services.alert_snapshots import capture_alert_snapshot
from app.services.dvla import NormalizedDvlaVehicle, lookup_normalized_vehicle_registration
from app.services.event_bus import event_bus
from app.services.leaderboard import get_leaderboard_service
from app.services.notifications import get_notification_service
from app.services.schedules import evaluate_schedule_id, evaluate_vehicle_schedule
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, TELEMETRY_CATEGORY_LPR, telemetry
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

GATE_OBSERVATION_PAYLOAD_KEY = "_iacs_gate_observation"
KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY = "_iacs_known_vehicle_plate_match"
GATE_CAMERA_IDENTIFIER = "camera.gate"
ARRIVAL_GATE_STATES = {GateState.CLOSED}
DEPARTURE_GATE_STATES = {GateState.OPEN, GateState.OPENING, GateState.CLOSING}


def dvla_mot_alert_required(mot_status: str | None) -> bool:
    return bool(mot_status and mot_status.strip().casefold() != "valid")


def dvla_tax_alert_required(tax_status: str | None) -> bool:
    return bool(tax_status and tax_status.strip().casefold() not in {"taxed", "sorn"})


def _known_vehicle_plate_match_from_read(read: PlateRead) -> dict[str, Any] | None:
    match = (read.raw_payload or {}).get(KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY)
    return match if isinstance(match, dict) else None


def _is_exact_known_vehicle_plate_match(read: PlateRead) -> bool:
    match = _known_vehicle_plate_match_from_read(read)
    return bool(match and match.get("exact"))


def _detected_registration_number(read: PlateRead) -> str:
    match = _known_vehicle_plate_match_from_read(read)
    return str(match.get("detected_registration_number") or read.registration_number) if match else read.registration_number


@dataclass
class DebounceWindow:
    first_seen: datetime
    updated_at: datetime
    reads: list[PlateRead] = field(default_factory=list)

    @property
    def best_read(self) -> PlateRead:
        return max(
            self.reads,
            key=lambda read: (
                1 if _is_exact_known_vehicle_plate_match(read) else 0,
                read.confidence,
                read.captured_at,
            ),
        )

    @property
    def first_read(self) -> PlateRead:
        return min(self.reads, key=lambda read: read.captured_at)


@dataclass(frozen=True)
class ResolvedPlateWindow:
    source: str
    registration_number: str
    first_seen: datetime
    expires_at: datetime


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
        self._recent_exact_resolutions: list[ResolvedPlateWindow] = []

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
        read = await self._read_with_gate_observation(read)
        gate_observation = self._gate_observation_from_read(read)
        logger.info(
            "plate_read_received",
            extra={
                "registration_number": read.registration_number,
                "confidence": read.confidence,
                "source": read.source,
                "gate_state": gate_observation.get("state"),
            },
        )
        await self._queue.put(read)
        await event_bus.publish(
            "plate_read.received",
            {
                "registration_number": read.registration_number,
                "confidence": read.confidence,
                "source": read.source,
                "gate_state": gate_observation.get("state"),
            },
        )

    async def _read_with_gate_observation(self, read: PlateRead) -> PlateRead:
        observed_at = datetime.now(tz=UTC)
        detail: str | None = None
        try:
            gate = get_gate_controller(settings.gate_controller)
            state = self._coerce_gate_state(await gate.current_state()) or GateState.UNKNOWN
        except UnsupportedModuleError as exc:
            state = GateState.UNKNOWN
            detail = str(exc)
        except Exception as exc:
            state = GateState.UNKNOWN
            detail = str(exc)
            logger.warning(
                "gate_state_observation_failed",
                extra={
                    "registration_number": read.registration_number,
                    "source": read.source,
                    "error": str(exc),
                },
            )

        raw_payload = dict(read.raw_payload or {})
        raw_payload[GATE_OBSERVATION_PAYLOAD_KEY] = {
            "state": state.value,
            "observed_at": observed_at.isoformat(),
            "controller": settings.gate_controller,
            "detail": detail,
        }
        return PlateRead(
            registration_number=read.registration_number,
            confidence=read.confidence,
            source=read.source,
            captured_at=read.captured_at,
            raw_payload=raw_payload,
        )

    async def _process_queue(self) -> None:
        while not self._stop_event.is_set():
            self._runtime = await get_runtime_config()
            self._timezone = ZoneInfo(self._runtime.site_timezone)
            try:
                read = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                await self._handle_queued_read(read)
            except asyncio.TimeoutError:
                pass
            await self._flush_expired_windows()

    async def _handle_queued_read(self, read: PlateRead) -> None:
        read = await self._read_with_known_vehicle_match(read)
        if self._suppress_after_exact_resolution(read):
            await self._publish_suppressed_read(read)
            return

        window = self._add_to_debounce_window(read)
        if _is_exact_known_vehicle_plate_match(read):
            window = self._pop_exact_known_plate_window(window)
            await self._finalize_exact_known_plate_window(window)

    def _add_to_debounce_window(self, read: PlateRead) -> DebounceWindow:
        for window in self._pending:
            best = window.best_read
            if read.source == best.source and self._is_similar_plate(
                read.registration_number, best.registration_number
            ):
                window.reads.append(read)
                window.updated_at = read.captured_at
                return window

        window = DebounceWindow(first_seen=read.captured_at, updated_at=read.captured_at, reads=[read])
        self._pending.append(window)
        return window

    async def _read_with_known_vehicle_match(self, read: PlateRead) -> PlateRead:
        registrations = await self._active_vehicle_registrations()
        threshold = self._runtime.lpr_similarity_threshold if self._runtime else settings.lpr_similarity_threshold
        match = self._known_vehicle_plate_match(read.registration_number, registrations, threshold)
        if not match:
            return read

        raw_payload = dict(read.raw_payload or {})
        raw_payload[KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY] = match
        return PlateRead(
            registration_number=str(match["registration_number"]),
            confidence=read.confidence,
            source=read.source,
            captured_at=read.captured_at,
            raw_payload=raw_payload,
        )

    async def _active_vehicle_registrations(self) -> list[str]:
        async with AsyncSessionLocal() as session:
            registrations = (
                await session.scalars(
                    select(Vehicle.registration_number).where(Vehicle.is_active.is_(True))
                )
            ).all()
        return [str(registration) for registration in registrations]

    def _known_vehicle_plate_match(
        self,
        detected_registration_number: str,
        stored_registration_numbers: list[str],
        threshold: float,
    ) -> dict[str, Any] | None:
        detected = self._normalize_registration_number(detected_registration_number)
        if not detected:
            return None

        best_match: dict[str, Any] | None = None
        for stored_registration_number in stored_registration_numbers:
            stored_lookup = str(stored_registration_number).strip().upper().replace(" ", "")
            stored = self._normalize_registration_number(stored_lookup)
            if not stored:
                continue
            similarity = 1.0 if detected == stored else SequenceMatcher(a=detected, b=stored).ratio()
            exact = detected == stored
            if not exact and similarity < threshold:
                continue
            candidate = {
                "detected_registration_number": detected,
                "registration_number": stored_lookup or stored,
                "normalized_registration_number": stored,
                "similarity": similarity,
                "threshold": threshold,
                "exact": exact,
            }
            if not best_match or (
                candidate["exact"],
                candidate["similarity"],
                candidate["registration_number"],
            ) > (
                best_match["exact"],
                best_match["similarity"],
                best_match["registration_number"],
            ):
                best_match = candidate
        return best_match

    def _normalize_registration_number(self, registration_number: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", registration_number).upper()

    async def _finalize_exact_known_plate_window(self, window: DebounceWindow) -> None:
        try:
            await self._finalize_window(window)
        except Exception:
            logger.exception(
                "access_event_finalize_failed",
                extra={
                    "candidate_count": len(window.reads),
                    "best_registration_number": window.best_read.registration_number,
                    "reason": "exact_known_vehicle_plate",
                },
            )
            await event_bus.publish(
                "access_event.finalize_failed",
                {
                    "registration_number": window.best_read.registration_number,
                    "candidate_count": len(window.reads),
                    "reason": "exact_known_vehicle_plate",
                },
            )
            return
        self._remember_exact_plate_resolution(window)

    def _pop_exact_known_plate_window(self, window: DebounceWindow) -> DebounceWindow:
        exact_read = next(
            (read for read in window.reads if _is_exact_known_vehicle_plate_match(read)),
            window.best_read,
        )
        max_seconds = self._runtime.lpr_debounce_max_seconds if self._runtime else settings.lpr_debounce_max_seconds
        related: list[DebounceWindow] = []
        remaining: list[DebounceWindow] = []
        for item in self._pending:
            if item is window:
                continue
            if item.best_read.source == exact_read.source and self._window_overlaps_exact_read(
                item,
                exact_read,
                max_seconds,
            ):
                related.append(item)
                continue
            remaining.append(item)

        self._pending = remaining
        if not related:
            return window

        reads = [
            read
            for related_window in [window, *related]
            for read in related_window.reads
        ]
        return DebounceWindow(
            first_seen=min(read.captured_at for read in reads),
            updated_at=max(read.captured_at for read in reads),
            reads=reads,
        )

    def _window_overlaps_exact_read(
        self,
        window: DebounceWindow,
        exact_read: PlateRead,
        max_seconds: float,
    ) -> bool:
        exact_at = exact_read.captured_at
        return window.first_seen <= exact_at <= window.first_seen + timedelta(seconds=max_seconds)

    def _remember_exact_plate_resolution(self, window: DebounceWindow) -> None:
        exact_read = next(
            (
                read
                for read in sorted(window.reads, key=lambda item: item.captured_at)
                if _is_exact_known_vehicle_plate_match(read)
            ),
            None,
        )
        if not exact_read:
            return
        max_seconds = self._runtime.lpr_debounce_max_seconds if self._runtime else settings.lpr_debounce_max_seconds
        self._recent_exact_resolutions.append(
            ResolvedPlateWindow(
                source=exact_read.source,
                registration_number=exact_read.registration_number,
                first_seen=window.first_seen,
                expires_at=window.first_seen + timedelta(seconds=max_seconds),
            )
        )

    def _suppress_after_exact_resolution(self, read: PlateRead) -> bool:
        self._prune_recent_exact_resolutions(read.captured_at)
        match = _known_vehicle_plate_match_from_read(read)
        for resolution in self._recent_exact_resolutions:
            if read.source != resolution.source:
                continue
            if not (resolution.first_seen <= read.captured_at <= resolution.expires_at):
                continue
            if match and read.registration_number != resolution.registration_number:
                continue
            return True
        return False

    def _prune_recent_exact_resolutions(self, now: datetime) -> None:
        self._recent_exact_resolutions = [
            resolution
            for resolution in self._recent_exact_resolutions
            if resolution.expires_at >= now
        ]

    async def _publish_suppressed_read(self, read: PlateRead) -> None:
        match = _known_vehicle_plate_match_from_read(read) or {}
        await event_bus.publish(
            "plate_read.suppressed",
            {
                "registration_number": read.registration_number,
                "detected_registration_number": match.get("detected_registration_number") or read.registration_number,
                "source": read.source,
                "reason": "exact_known_vehicle_plate_already_resolved_in_debounce_window",
            },
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
        direction_read = window.first_read
        finalize_started_at = datetime.now(tz=UTC)
        trace = telemetry.start_trace(
            f"Plate Detection - {read.registration_number}",
            category=TELEMETRY_CATEGORY_LPR,
            source=read.source,
            registration_number=read.registration_number,
            started_at=window.first_seen,
            context={
                "candidate_count": len(window.reads),
                "source": read.source,
                "first_seen": window.first_seen.isoformat(),
                "finalize_started_at": finalize_started_at.isoformat(),
            },
        )
        trace.record_span(
            "Webhook Received",
            started_at=window.first_seen,
            ended_at=window.first_seen,
            attributes={"source": window.first_read.source},
            output_payload={
                "registration_number": window.first_read.registration_number,
                "confidence": window.first_read.confidence,
                "captured_at": window.first_read.captured_at.isoformat(),
            },
        )
        trace.record_span(
            "Debounce & Confidence Aggregation",
            started_at=window.first_seen,
            ended_at=finalize_started_at,
            attributes={
                "candidate_count": len(window.reads),
                "selected_registration_number": read.registration_number,
                "selected_confidence": read.confidence,
            },
            output_payload={
                "candidates": [
                    {
                        "registration_number": item.registration_number,
                        "detected_registration_number": _detected_registration_number(item),
                        "confidence": item.confidence,
                        "captured_at": item.captured_at.isoformat(),
                    }
                    for item in window.reads
                ],
            },
        )
        async with AsyncSessionLocal() as session:
            vehicle_span = trace.start_span(
                "Plate Verification against Vehicle DB",
                attributes={"registration_number": read.registration_number},
            )
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
            vehicle_span.finish(
                output_payload={
                    "matched": bool(vehicle),
                    "vehicle_id": str(vehicle.id) if vehicle else None,
                    "person_id": str(vehicle.person_id) if vehicle and vehicle.person_id else None,
                    "vehicle": self._vehicle_display_name(vehicle, read.registration_number) if vehicle else None,
                    "owner": vehicle.owner.display_name if vehicle and vehicle.owner else None,
                    "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(read),
                }
            )

            person = vehicle.owner if vehicle else None
            runtime = self._runtime or await get_runtime_config()
            identity_active = bool(vehicle and (not person or person.is_active))
            schedule_span = trace.start_span(
                "Schedule & Access Rule Evaluation",
                attributes={
                    "identity_active": identity_active,
                    "vehicle_id": str(vehicle.id) if vehicle else None,
                    "person_id": str(person.id) if person else None,
                },
            )
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
            schedule_span.finish(
                output_payload={
                    "allowed": schedule_evaluation.allowed if schedule_evaluation else False,
                    "source": schedule_evaluation.source if schedule_evaluation else "none",
                    "schedule_id": str(schedule_evaluation.schedule_id) if schedule_evaluation and schedule_evaluation.schedule_id else None,
                    "schedule_name": schedule_evaluation.schedule_name if schedule_evaluation else None,
                    "reason": schedule_evaluation.reason if schedule_evaluation else "No active vehicle identity matched.",
                }
            )
            allowed = bool(identity_active and schedule_evaluation and schedule_evaluation.allowed)
            direction_span = trace.start_span(
                "Direction Classification",
                attributes={
                    "allowed": allowed,
                    "gate_observation": self._gate_observation_from_read(direction_read),
                },
            )
            direction, direction_resolution = await self._resolve_direction(
                session,
                direction_read,
                person,
                allowed,
                trace=trace,
            )
            direction_span.finish(
                output_payload={
                    "direction": direction.value,
                    "resolution": direction_resolution,
                }
            )
            decision = AccessDecision.GRANTED if allowed else AccessDecision.DENIED
            timing_span = trace.start_span(
                "Presence Timing Classification",
                attributes={
                    "allowed": allowed,
                    "person_id": str(person.id) if person else None,
                    "direction": direction.value,
                },
            )
            timing = (
                await self._classify_timing(session, person, direction, read.captured_at)
                if allowed and person
                else TimingClassification.UNKNOWN
            )
            timing_span.finish(output_payload={"timing_classification": timing.value})

            dvla_enrichment = await self._dvla_enrichment_for_event(
                vehicle=vehicle,
                registration_number=read.registration_number,
                direction=direction,
                direction_resolution=direction_resolution,
                runtime=runtime,
                trace=trace,
            )

            persistence_span = trace.start_span(
                "Persist Access Event, Presence, and Anomalies",
                attributes={"decision": decision.value, "direction": direction.value},
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
                                "detected_registration_number": _detected_registration_number(item),
                                "confidence": item.confidence,
                                "captured_at": item.captured_at.isoformat(),
                                "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(item),
                            }
                            for item in window.reads
                        ],
                    },
                    "direction_resolution": direction_resolution,
                    "telemetry": {
                        "trace_id": trace.trace_id,
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
            persistence_span.finish(
                output_payload={
                    "event_id": str(event.id),
                    "anomaly_count": len(anomalies),
                    "presence_updated": bool(allowed and person),
                }
            )

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
        if dvla_enrichment:
            await self._notify_compliance_issues(event, person, vehicle, dvla_enrichment)
        if decision == AccessDecision.GRANTED and direction == AccessDirection.ENTRY and event.vehicle_id:
            try:
                await get_leaderboard_service().evaluate_known_overtake(event.id)
            except Exception as exc:
                logger.warning(
                    "leaderboard_overtake_evaluation_failed",
                    extra={"event_id": str(event.id), "registration_number": event.registration_number, "error": str(exc)},
                )
        if decision == AccessDecision.GRANTED and direction == AccessDirection.ENTRY:
            if not self._automatic_open_allowed(direction_resolution):
                await self._publish_gate_open_skipped(event, direction_resolution)
                gate_opened = False
            else:
                gate_opened = await self._open_gate_for_event(
                    event,
                    person,
                    open_garage_doors=True,
                    trace=trace,
                    dvla_enrichment=dvla_enrichment,
                )
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
                            dvla_enrichment=dvla_enrichment,
                        ),
                    )
                )

        for anomaly in anomalies:
            await get_notification_service().notify(
                NotificationContext(
                    event_type=anomaly.anomaly_type.value,
                    subject=event.registration_number,
                    severity=anomaly.severity.value,
                    facts=self._notification_facts(
                        event,
                        person,
                        vehicle,
                        anomaly.message,
                        dvla_enrichment=dvla_enrichment,
                    ),
                )
                )
        trace.finish(
            status="ok",
            level="warning" if anomalies or decision == AccessDecision.DENIED else "info",
            summary=f"{decision.value.title()} {direction.value} for plate {event.registration_number}",
            access_event_id=event.id,
            context={
                "event_id": str(event.id),
                "decision": decision.value,
                "direction": direction.value,
                "timing_classification": timing.value,
                "anomaly_count": len(anomalies),
            },
        )

    async def _dvla_enrichment_for_event(
        self,
        *,
        vehicle: Vehicle | None,
        registration_number: str,
        direction: AccessDirection,
        direction_resolution: dict[str, Any],
        runtime: RuntimeConfig,
        trace: Any | None = None,
    ) -> dict[str, str | None] | None:
        if not self._should_run_dvla_enrichment(direction, direction_resolution):
            return None

        today = self._dvla_cache_date(runtime.site_timezone)
        span = (
            trace.start_span(
                "DVLA Vehicle Enrichment",
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                attributes={
                    "registration_number": registration_number,
                    "known_vehicle": bool(vehicle),
                    "vehicle_id": str(vehicle.id) if vehicle else None,
                    "last_lookup_date": (
                        vehicle.last_dvla_lookup_date.isoformat()
                        if vehicle and vehicle.last_dvla_lookup_date
                        else None
                    ),
                    "cache_date": today.isoformat(),
                },
            )
            if trace
            else None
        )

        if vehicle and vehicle.last_dvla_lookup_date == today:
            payload = self._vehicle_dvla_payload(vehicle)
            if span:
                span.finish(status="ok", output_payload={"status": "cached"})
            return payload

        try:
            normalized = await lookup_normalized_vehicle_registration(registration_number)
        except DvlaVehicleEnquiryError as exc:
            detail = self._sanitize_dvla_error(exc)
            if span:
                span.finish(status="error", output_payload={"status": "failed"}, error=detail)
            await self._publish_dvla_enrichment_failure(registration_number, exc.status_code, detail)
            return None
        except Exception as exc:
            detail = self._sanitize_dvla_error(exc)
            if span:
                span.finish(status="error", output_payload={"status": "failed"}, error=detail)
            await self._publish_dvla_enrichment_failure(registration_number, None, detail)
            return None

        if vehicle:
            self._apply_dvla_enrichment(vehicle, normalized, today)
            payload = self._vehicle_dvla_payload(vehicle)
            status = "refreshed"
        else:
            payload = normalized.as_payload()
            status = "ephemeral"

        if span:
            span.finish(status="ok", output_payload={"status": status})
        return payload

    def _should_run_dvla_enrichment(
        self,
        direction: AccessDirection,
        direction_resolution: dict[str, Any],
    ) -> bool:
        if direction == AccessDirection.EXIT:
            return False
        if direction == AccessDirection.ENTRY:
            return True

        gate_observation = direction_resolution.get("gate_observation") or {}
        gate_state = self._coerce_gate_state(gate_observation.get("state")) if isinstance(gate_observation, dict) else GateState.UNKNOWN
        return gate_state in ARRIVAL_GATE_STATES

    def _dvla_cache_date(self, timezone_name: str) -> date:
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception:
            timezone = self._timezone
        return datetime.now(tz=timezone).date()

    def _vehicle_dvla_payload(self, vehicle: Vehicle) -> dict[str, str | None]:
        return {
            "registration_number": vehicle.registration_number,
            "make": vehicle.make,
            "colour": vehicle.color,
            "mot_status": vehicle.mot_status,
            "tax_status": vehicle.tax_status,
            "mot_expiry": vehicle.mot_expiry.isoformat() if vehicle.mot_expiry else None,
            "tax_expiry": vehicle.tax_expiry.isoformat() if vehicle.tax_expiry else None,
        }

    def _apply_dvla_enrichment(
        self,
        vehicle: Vehicle,
        normalized: NormalizedDvlaVehicle,
        lookup_date: date,
    ) -> None:
        if normalized.make:
            vehicle.make = normalized.make
        if normalized.colour:
            vehicle.color = normalized.colour
        vehicle.mot_status = normalized.mot_status
        vehicle.tax_status = normalized.tax_status
        vehicle.mot_expiry = normalized.mot_expiry
        vehicle.tax_expiry = normalized.tax_expiry
        vehicle.last_dvla_lookup_date = lookup_date

    async def _publish_dvla_enrichment_failure(
        self,
        registration_number: str,
        status_code: int | None,
        detail: str,
    ) -> None:
        logger.warning(
            "lpr_dvla_enrichment_failed",
            extra={
                "registration_number": registration_number,
                "status_code": status_code,
                "error": detail,
            },
        )
        await event_bus.publish(
            "dvla.enrichment_failed",
            {
                "registration_number": registration_number,
                "status_code": status_code,
                "error": detail,
            },
        )

    def _sanitize_dvla_error(self, exc: Exception) -> str:
        detail = str(exc).replace("\n", " ").strip()
        return detail or exc.__class__.__name__

    async def _notify_compliance_issues(
        self,
        event: AccessEvent,
        person: Person | None,
        vehicle: Vehicle | None,
        dvla_enrichment: dict[str, str | None],
    ) -> None:
        mot_status = dvla_enrichment.get("mot_status")
        tax_status = dvla_enrichment.get("tax_status")
        notifications = []
        if dvla_mot_alert_required(mot_status):
            notifications.append(
                NotificationContext(
                    event_type="expired_mot_detected",
                    subject=f"Expired MOT detected for {event.registration_number}",
                    severity=AnomalySeverity.WARNING.value,
                    facts=self._notification_facts(
                        event,
                        person,
                        vehicle,
                        f"DVLA reports MOT status {mot_status} for {event.registration_number}.",
                        dvla_enrichment=dvla_enrichment,
                    ),
                )
            )
        if dvla_tax_alert_required(tax_status):
            notifications.append(
                NotificationContext(
                    event_type="expired_tax_detected",
                    subject=f"Expired tax detected for {event.registration_number}",
                    severity=AnomalySeverity.WARNING.value,
                    facts=self._notification_facts(
                        event,
                        person,
                        vehicle,
                        f"DVLA reports tax status {tax_status} for {event.registration_number}.",
                        dvla_enrichment=dvla_enrichment,
                    ),
                )
            )

        for context in notifications:
            await get_notification_service().notify(context)

    async def _open_gate_for_event(
        self,
        event: AccessEvent,
        person: Person | None,
        *,
        open_garage_doors: bool,
        trace: Any | None = None,
        dvla_enrichment: dict[str, str | None] | None = None,
    ) -> bool:
        reason = (
            f"Automatic LPR grant for {event.registration_number}"
            f"{f' ({person.display_name})' if person else ''}"
        )
        gate_opened = False
        gate_span = (
            trace.start_span(
                "Home Assistant Gate Open Command Sent",
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                attributes={
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "controller": settings.gate_controller,
                },
                input_payload={"reason": reason},
            )
            if trace
            else None
        )
        try:
            gate = get_gate_controller(settings.gate_controller)
            result = await gate.open_gate(reason)
        except UnsupportedModuleError as exc:
            if gate_span:
                gate_span.finish(status="error", error=exc)
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
                        dvla_enrichment=dvla_enrichment,
                    ),
                )
            )
        else:
            if gate_span:
                gate_span.finish(
                    status="ok" if result.accepted else "error",
                    output_payload={
                        "accepted": result.accepted,
                        "state": result.state.value,
                        "detail": result.detail,
                    },
                    error=None if result.accepted else result.detail,
                )
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
                            dvla_enrichment=dvla_enrichment,
                        ),
                    )
                )

        if gate_opened and open_garage_doors:
            await self._open_garage_doors_for_event(
                event,
                person,
                reason,
                trace=trace,
                dvla_enrichment=dvla_enrichment,
            )
        return gate_opened

    async def _publish_gate_open_skipped(
        self, event: AccessEvent, direction_resolution: dict[str, Any]
    ) -> None:
        gate_observation = direction_resolution.get("gate_observation") or {}
        await event_bus.publish(
            "gate.open_skipped",
            {
                "event_id": str(event.id),
                "registration_number": event.registration_number,
                "state": gate_observation.get("state") or GateState.UNKNOWN.value,
                "detail": (
                    "Automatic gate and garage-door commands require the top gate "
                    "to be closed at plate-read time."
                ),
            },
        )

    async def _open_garage_doors_for_event(
        self,
        event: AccessEvent,
        person: Person | None,
        reason: str,
        *,
        trace: Any | None = None,
        dvla_enrichment: dict[str, str | None] | None = None,
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
            garage_span = (
                trace.start_span(
                    "Home Assistant Garage Door Command",
                    category=TELEMETRY_CATEGORY_INTEGRATIONS,
                    attributes={
                        "event_id": str(event.id),
                        "entity_id": str(entity["entity_id"]),
                        "name": str(entity.get("name") or entity["entity_id"]),
                    },
                    input_payload={"reason": reason, "action": "open"},
                )
                if trace
                else None
            )
            if not schedule_evaluation.allowed:
                detail = (
                    schedule_evaluation.reason
                    or f"{entity.get('name') or entity['entity_id']} is outside its assigned schedule."
                )
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
                            dvla_enrichment=dvla_enrichment,
                            garage_door=str(entity.get("name") or entity["entity_id"]),
                            entity_id=str(entity["entity_id"]),
                        ),
                    )
                )
                if garage_span:
                    garage_span.finish(
                        status="error",
                        output_payload={
                            "accepted": False,
                            "state": "schedule_denied",
                            "detail": detail,
                        },
                        error=detail,
                    )
                continue

            outcome = await command_cover(client, entity, "open", reason)
            if garage_span:
                garage_span.finish(
                    status="ok" if outcome.accepted else "error",
                    output_payload={
                        "accepted": outcome.accepted,
                        "state": outcome.state,
                        "detail": outcome.detail,
                    },
                    error=None if outcome.accepted else outcome.detail,
                )
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
                        dvla_enrichment=dvla_enrichment,
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
        *,
        dvla_enrichment: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, str]:
        group = person.group if person else None
        vehicle_display_name = self._vehicle_display_name(vehicle, event.registration_number)
        dvla = dvla_enrichment or {}
        vehicle_make = self._fact_text(dvla.get("make")) or (vehicle.make if vehicle else "") or ""
        vehicle_colour = self._fact_text(dvla.get("colour")) or (vehicle.color if vehicle else "") or ""
        facts = {
            "message": message,
            "first_name": person.first_name if person else "",
            "last_name": person.last_name if person else "",
            "display_name": person.display_name if person else "",
            "group_name": group.name if group else "",
            "vehicle_registration_number": event.registration_number,
            "registration_number": event.registration_number,
            "vehicle_display_name": vehicle_display_name,
            "vehicle_make": vehicle_make,
            "vehicle_model": vehicle.model if vehicle and vehicle.model else "",
            "vehicle_color": vehicle_colour,
            "vehicle_colour": vehicle_colour,
            "mot_status": self._fact_text(dvla.get("mot_status")),
            "mot_expiry": self._fact_text(dvla.get("mot_expiry")),
            "tax_status": self._fact_text(dvla.get("tax_status")),
            "tax_expiry": self._fact_text(dvla.get("tax_expiry")),
            "object_pronoun": "them",
            "possessive_determiner": "their",
            "direction": event.direction.value,
            "decision": event.decision.value,
            "source": event.source,
            "timing_classification": event.timing_classification.value,
            "occurred_at": event.occurred_at.isoformat(),
        }
        facts.update({key: self._fact_text(value) for key, value in extra.items()})
        return facts

    def _fact_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)

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
        self,
        session: AsyncSession,
        read: PlateRead,
        person: Person | None,
        allowed: bool,
        *,
        trace: Any | None = None,
    ) -> tuple[AccessDirection, dict[str, Any]]:
        gate_observation = self._gate_observation_from_read(read)
        gate_state = self._coerce_gate_state(gate_observation.get("state"))
        resolution: dict[str, Any] = {
            "source": "unknown",
            "gate_observation": gate_observation,
        }

        if not allowed:
            resolution["source"] = "access_denied"
            resolution["direction"] = AccessDirection.DENIED.value
            return AccessDirection.DENIED, resolution

        if gate_state in ARRIVAL_GATE_STATES:
            direction = AccessDirection.ENTRY
            resolution["source"] = "gate_state"
            resolution["direction"] = direction.value
            if person and await self._person_is_present(session, person):
                camera_decision = (
                    await self._resolve_duplicate_arrival_with_camera(read, person, trace=trace)
                    if trace
                    else await self._resolve_duplicate_arrival_with_camera(read, person)
                )
                resolution["camera_tiebreaker"] = camera_decision
                camera_direction = camera_decision.get("direction")
                if camera_direction in {AccessDirection.ENTRY.value, AccessDirection.EXIT.value}:
                    direction = AccessDirection(camera_direction)
                    resolution["source"] = "camera_tiebreaker"
                    resolution["direction"] = direction.value
            return direction, resolution

        if gate_state in DEPARTURE_GATE_STATES:
            resolution["source"] = "gate_state"
            resolution["direction"] = AccessDirection.EXIT.value
            return AccessDirection.EXIT, resolution

        explicit = str(read.raw_payload.get("direction") or read.raw_payload.get("Direction") or "").lower()
        if explicit in {"entry", "enter", "arrival", "in"}:
            resolution["source"] = "payload"
            resolution["direction"] = AccessDirection.ENTRY.value
            return AccessDirection.ENTRY, resolution
        if explicit in {"exit", "leave", "departure", "out"}:
            resolution["source"] = "payload"
            resolution["direction"] = AccessDirection.EXIT.value
            return AccessDirection.EXIT, resolution
        if not person:
            resolution["source"] = "default_entry_no_person"
            resolution["direction"] = AccessDirection.ENTRY.value
            return AccessDirection.ENTRY, resolution

        if await self._person_is_present(session, person):
            resolution["source"] = "presence"
            resolution["direction"] = AccessDirection.EXIT.value
            return AccessDirection.EXIT, resolution
        resolution["source"] = "presence"
        resolution["direction"] = AccessDirection.ENTRY.value
        return AccessDirection.ENTRY, resolution

    async def _person_is_present(self, session: AsyncSession, person: Person) -> bool:
        presence = await session.get(Presence, person.id)
        return bool(presence and presence.state == PresenceState.PRESENT)

    async def _resolve_duplicate_arrival_with_camera(
        self, read: PlateRead, person: Person, *, trace: Any | None = None
    ) -> dict[str, Any]:
        runtime = self._runtime or await get_runtime_config()
        prompt = (
            "You are resolving an access-control direction conflict. "
            f"The top gate was closed when plate {read.registration_number} was read, "
            f"but {person.display_name} is already marked present. "
            "Inspect the gate camera snapshot and decide whether the visible vehicle is facing "
            "towards the camera, which means Arriving, or away from the camera, which means Leaving. "
            'Return only JSON like {"direction":"entry|exit|unknown","confidence":0.0,"reason":"short reason"}.'
        )
        camera_span = (
            trace.start_span(
                "LLM Vision Direction Tie-breaker",
                category=TELEMETRY_CATEGORY_LPR,
                attributes={
                    "provider": "openai",
                    "model": runtime.openai_model,
                    "camera": GATE_CAMERA_IDENTIFIER,
                    "prompt_category": "access_direction_tiebreaker",
                },
            )
            if trace
            else None
        )
        artifact: dict[str, Any] | None = None
        try:
            media = await get_unifi_protect_service().snapshot(
                GATE_CAMERA_IDENTIFIER,
                width=runtime.unifi_protect_snapshot_width,
                height=runtime.unifi_protect_snapshot_height,
            )
            if trace:
                artifact = await telemetry.store_artifact(
                    media.content,
                    content_type=media.content_type,
                    kind="camera_snapshot",
                    trace_id=trace.trace_id,
                    span_id=camera_span.span_id if camera_span else None,
                    metadata={
                        "camera": GATE_CAMERA_IDENTIFIER,
                        "registration_number": read.registration_number,
                        "person_id": str(person.id),
                    },
                )
            result = await analyze_image_with_provider(
                "openai",
                prompt=prompt,
                image_bytes=media.content,
                mime_type=media.content_type,
            )
        except Exception as exc:
            if camera_span:
                camera_span.finish(
                    status="error",
                    error=exc,
                    output_payload={
                        "artifact": artifact,
                        "direction": "unknown",
                    },
                )
            logger.warning(
                "access_direction_camera_tiebreaker_failed",
                extra={
                    "registration_number": read.registration_number,
                    "person_id": str(person.id),
                    "camera": GATE_CAMERA_IDENTIFIER,
                    "error": str(exc),
                },
            )
            return {
                "camera": GATE_CAMERA_IDENTIFIER,
                "provider": "openai",
                "direction": "unknown",
                "confidence": 0.0,
                "error": str(exc),
                "artifact": artifact,
            }

        direction, confidence, reason = self._parse_camera_direction_analysis(result.text)
        if camera_span:
            camera_span.finish(
                output_payload={
                    "artifact": artifact,
                    "provider": "openai",
                    "model": runtime.openai_model,
                    "direction": direction,
                    "confidence": confidence,
                    "reason": reason,
                    "analysis": result.text[:1000],
                }
            )
        await event_bus.publish(
            "access_event.direction_tiebreaker",
            {
                "registration_number": read.registration_number,
                "person_id": str(person.id),
                "person": person.display_name,
                "camera": GATE_CAMERA_IDENTIFIER,
                "provider": "openai",
                "direction": direction,
                "confidence": confidence,
                "reason": reason,
            },
        )
        return {
            "camera": GATE_CAMERA_IDENTIFIER,
            "provider": "openai",
            "direction": direction,
            "confidence": confidence,
            "reason": reason,
            "analysis": result.text[:1000],
            "artifact": artifact,
        }

    def _parse_camera_direction_analysis(self, text: str) -> tuple[str, float | None, str | None]:
        parsed = self._json_object_from_text(text)
        if parsed:
            direction = self._normalize_camera_direction(parsed.get("direction"))
            confidence = self._coerce_confidence(parsed.get("confidence"))
            reason = str(parsed.get("reason") or "").strip() or None
            if direction != "unknown":
                return direction, confidence, reason

        normalized = text.lower()
        if any(
            phrase in normalized
            for phrase in ("away from the camera", "facing away", "leaving", "departing", "rear of")
        ):
            return AccessDirection.EXIT.value, None, text[:240].strip() or None
        if any(
            phrase in normalized
            for phrase in (
                "towards the camera",
                "toward the camera",
                "facing the camera",
                "arriving",
                "approaching",
            )
        ):
            return AccessDirection.ENTRY.value, None, text[:240].strip() or None
        return "unknown", None, text[:240].strip() or None

    def _json_object_from_text(self, text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                stripped,
                flags=re.IGNORECASE | re.DOTALL,
            ).strip()
        candidates = [stripped]
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if match:
            candidates.append(match.group(0))
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _normalize_camera_direction(self, value: Any) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"entry", "enter", "arrival", "arriving", "toward_camera", "towards_camera"}:
            return AccessDirection.ENTRY.value
        if normalized in {"exit", "leave", "leaving", "departure", "departing", "away_from_camera"}:
            return AccessDirection.EXIT.value
        return "unknown"

    def _coerce_confidence(self, value: Any) -> float | None:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        if confidence > 1:
            confidence = confidence / 100
        return max(0.0, min(confidence, 1.0))

    def _automatic_open_allowed(self, direction_resolution: dict[str, Any]) -> bool:
        gate_observation = direction_resolution.get("gate_observation") or {}
        return self._coerce_gate_state(gate_observation.get("state")) == GateState.CLOSED

    def _gate_observation_from_read(self, read: PlateRead) -> dict[str, Any]:
        value = (read.raw_payload or {}).get(GATE_OBSERVATION_PAYLOAD_KEY)
        if not isinstance(value, dict):
            return {
                "state": GateState.UNKNOWN.value,
                "observed_at": None,
                "detail": "No gate observation captured.",
            }
        state = self._coerce_gate_state(value.get("state")) or GateState.UNKNOWN
        return {
            "state": state.value,
            "observed_at": value.get("observed_at"),
            "controller": value.get("controller"),
            "detail": value.get("detail"),
        }

    def _coerce_gate_state(self, value: Any) -> GateState | None:
        if isinstance(value, GateState):
            return value
        try:
            return GateState(str(value or "").lower())
        except ValueError:
            return None

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
            anomaly = Anomaly(
                event=event,
                anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                severity=AnomalySeverity.WARNING,
                message="Unauthorised Plate, Access Denied",
                context={"registration_number": event.registration_number},
            )
            await capture_alert_snapshot(anomaly)
            anomalies.append(anomaly)
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
