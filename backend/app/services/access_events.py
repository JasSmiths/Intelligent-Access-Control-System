import asyncio
import json
import re
import uuid
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
from app.models import AccessEvent, Anomaly, Person, Presence, Vehicle, VisitorPass
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    PresenceState,
    TimingClassification,
    VisitorPassStatus,
    VisitorPassType,
)
from app.modules.notifications.base import NotificationContext
from app.services.alert_snapshots import alert_snapshot_metadata_from_event
from app.services.dvla import NormalizedDvlaVehicle, lookup_normalized_vehicle_registration
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_malfunctions import active_stuck_open_malfunction_at
from app.services.leaderboard import get_leaderboard_service
from app.services.maintenance import is_maintenance_mode_active
from app.services.notifications import get_notification_service
from app.services.schedules import ScheduleEvaluation, evaluate_schedule_id, evaluate_vehicle_schedule
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.snapshots import (
    SNAPSHOT_HEIGHT,
    SNAPSHOT_WIDTH,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
    access_event_snapshot_payload,
    apply_snapshot_to_access_event,
    get_snapshot_manager,
)
from app.services.snapshot_recovery import protect_event_id_from_access_event
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    TELEMETRY_CATEGORY_LPR,
    audit_log_event_payload,
    telemetry,
    write_audit_log,
)
from app.services.unifi_protect import get_unifi_protect_service
from app.services.vehicle_visual_detections import (
    get_vehicle_presence_tracker,
    get_vehicle_visual_detection_recorder,
)
from app.services.visitor_passes import get_visitor_pass_service, serialize_visitor_pass

logger = get_logger(__name__)

GATE_OBSERVATION_PAYLOAD_KEY = "_iacs_gate_observation"
GATE_MALFUNCTION_PAYLOAD_KEY = "_iacs_gate_malfunction"
KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY = "_iacs_known_vehicle_plate_match"
VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY = "_iacs_visitor_pass_plate_match"
VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY = "vehicle_visual_detection"
VISITOR_PASS_PAYLOAD_KEY = "visitor_pass"
VEHICLE_SESSION_PAYLOAD_KEY = "vehicle_session"
GATE_CAMERA_IDENTIFIER = "camera.gate"
ARRIVAL_GATE_STATES = {GateState.CLOSED}
DEPARTURE_GATE_STATES = {GateState.OPEN, GateState.OPENING, GateState.CLOSING}
EXACT_PLATE_GATE_CYCLE_SUPPRESSION_SECONDS = 60.0
MAX_SUPPRESSED_SESSION_READS = 20


def dvla_mot_alert_required(mot_status: str | None) -> bool:
    normalized = (mot_status or "").strip().casefold().replace("_", " ")
    return bool(normalized and normalized not in {"valid", "not required"})


def dvla_tax_alert_required(tax_status: str | None) -> bool:
    return bool(tax_status and tax_status.strip().casefold() not in {"taxed", "sorn"})


def _known_vehicle_plate_match_from_read(read: PlateRead) -> dict[str, Any] | None:
    match = (read.raw_payload or {}).get(KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY)
    return match if isinstance(match, dict) else None


def _is_exact_known_vehicle_plate_match(read: PlateRead) -> bool:
    match = _known_vehicle_plate_match_from_read(read)
    return bool(match and match.get("exact"))


def _visitor_pass_plate_match_from_read(read: PlateRead) -> dict[str, Any] | None:
    match = (read.raw_payload or {}).get(VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY)
    return match if isinstance(match, dict) else None


def _gate_malfunction_from_read(read: PlateRead) -> dict[str, Any] | None:
    malfunction = (read.raw_payload or {}).get(GATE_MALFUNCTION_PAYLOAD_KEY)
    return malfunction if isinstance(malfunction, dict) else None


def _is_visitor_pass_plate_match(read: PlateRead) -> bool:
    return bool(_visitor_pass_plate_match_from_read(read))


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
                1 if _is_visitor_pass_plate_match(read) else 0,
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
    debounce_expires_at: datetime
    gate_cycle_expires_at: datetime
    direction: AccessDirection | None = None
    decision: AccessDecision | None = None


@dataclass(frozen=True)
class FinalizedPlateEvent:
    event_id: str
    direction: AccessDirection
    decision: AccessDecision
    occurred_at: datetime


@dataclass
class VehicleSessionContext:
    source: str
    registration_number: str
    normalized_registration_number: str
    camera_id: str | None = None
    device_id: str | None = None
    protect_event_ids: set[str] = field(default_factory=set)


@dataclass
class ActiveVehicleSession:
    event_id: str
    source: str
    registration_number: str
    normalized_registration_number: str
    started_at: datetime
    last_seen_at: datetime
    direction: AccessDirection
    decision: AccessDecision
    camera_id: str | None = None
    device_id: str | None = None
    protect_event_ids: set[str] = field(default_factory=set)


@dataclass
class VehicleSessionSuppression:
    session: ActiveVehicleSession
    reason: str
    matched_by: str
    evidence: dict[str, Any] | None = None


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
        self._recent_visitor_pass_resolutions: list[ResolvedPlateWindow] = []
        self._active_vehicle_sessions: list[ActiveVehicleSession] = []

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stop_event.clear()
        self._worker = asyncio.create_task(self._process_queue(), name="lpr-debounce-worker")
        event_bus.subscribe(self._handle_realtime_event)
        logger.info("access_event_service_started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            await self._worker
        await self._flush_all_pending()
        event_bus.unsubscribe(self._handle_realtime_event)
        logger.info("access_event_service_stopped")

    async def enqueue_plate_read(self, read: PlateRead) -> None:
        if await is_maintenance_mode_active():
            return
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
                if await is_maintenance_mode_active():
                    self._clear_pending_reads()
                    continue
                await self._handle_queued_read(read)
            except asyncio.TimeoutError:
                pass
            await self._flush_expired_windows()

    async def _handle_queued_read(self, read: PlateRead) -> None:
        read = await self._read_with_known_vehicle_match(read)
        read = await self._read_with_gate_malfunction_context(read)
        gate_malfunction = _gate_malfunction_from_read(read)
        known_vehicle_match = _known_vehicle_plate_match_from_read(read)
        if gate_malfunction and not known_vehicle_match:
            await self._ignore_unknown_gate_malfunction_read(read)
            return

        if not gate_malfunction:
            exact_suppression_reason = self._exact_resolution_suppression_reason(read)
            if exact_suppression_reason:
                await self._publish_suppressed_read(read, reason=exact_suppression_reason)
                return

            vehicle_session_suppression = await self._vehicle_session_suppression(read)
            if vehicle_session_suppression:
                await self._annotate_suppressed_session_read(read, vehicle_session_suppression)
                await self._publish_suppressed_read(read, reason=vehicle_session_suppression.reason)
                return

        if not _known_vehicle_plate_match_from_read(read):
            read = await self._read_with_visitor_pass_departure_match(read)
        if self._suppress_after_visitor_pass_resolution(read):
            await self._publish_suppressed_read(read, reason="visitor_pass_plate_already_resolved_in_debounce_window")
            return

        window = self._add_to_debounce_window(read)
        if _is_exact_known_vehicle_plate_match(read):
            window = self._pop_exact_known_plate_window(window)
            await self._finalize_exact_known_plate_window(window)
        elif _is_visitor_pass_plate_match(read):
            window = self._pop_related_read_window(window, read)
            await self._finalize_visitor_pass_window(window, read)

    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        if event.type == "maintenance_mode.changed" and event.payload.get("is_active") is True:
            self._clear_pending_reads()

    def _clear_pending_reads(self) -> None:
        self._pending = []
        self._recent_exact_resolutions = []
        self._recent_visitor_pass_resolutions = []
        self._active_vehicle_sessions = []

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

    async def _read_with_gate_malfunction_context(self, read: PlateRead) -> PlateRead:
        gate_observation = self._gate_observation_from_read(read)
        gate_state = self._coerce_gate_state(gate_observation.get("state"))
        if gate_state not in DEPARTURE_GATE_STATES:
            return read

        async with AsyncSessionLocal() as session:
            malfunction = await active_stuck_open_malfunction_at(
                session,
                observed_at=read.captured_at,
                gate_state=gate_state,
                gate_entity_id=gate_observation.get("entity_id"),
            )
        if not malfunction:
            return read

        raw_payload = dict(read.raw_payload or {})
        raw_payload[GATE_MALFUNCTION_PAYLOAD_KEY] = malfunction.as_payload()
        return PlateRead(
            registration_number=read.registration_number,
            confidence=read.confidence,
            source=read.source,
            captured_at=read.captured_at,
            raw_payload=raw_payload,
        )

    async def _ignore_unknown_gate_malfunction_read(self, read: PlateRead) -> None:
        gate_observation = self._gate_observation_from_read(read)
        gate_malfunction = _gate_malfunction_from_read(read) or {}
        detail = {
            "registration_number": read.registration_number,
            "detected_registration_number": _detected_registration_number(read),
            "confidence": read.confidence,
            "source": read.source,
            "captured_at": read.captured_at.isoformat(),
            "gate_state": gate_observation.get("state"),
            "gate_observation": gate_observation,
            "gate_malfunction": gate_malfunction,
            "malfunction_id": gate_malfunction.get("id"),
            "reason": "gate_malfunction_unknown_vehicle",
        }
        logger.info("plate_read_ignored_during_gate_malfunction", extra=detail)
        await event_bus.publish("plate_read.ignored", detail)
        try:
            async with AsyncSessionLocal() as session:
                row = await write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_LPR,
                    action="plate_read.gate_malfunction_ignored",
                    actor="System",
                    target_entity="PlateRead",
                    target_label=read.registration_number,
                    metadata=detail,
                )
                await session.commit()
                await session.refresh(row)
            await event_bus.publish("audit.log.created", audit_log_event_payload(row))
        except Exception as exc:
            logger.warning(
                "plate_read_gate_malfunction_ignore_audit_failed",
                extra={"registration_number": read.registration_number, "error": str(exc)},
            )

    async def _read_with_visitor_pass_departure_match(self, read: PlateRead) -> PlateRead:
        plate = self._normalize_registration_number(read.registration_number)
        if not plate:
            return read

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(VisitorPass.id, VisitorPass.arrival_time)
                    .where(
                        (
                            (VisitorPass.status == VisitorPassStatus.USED)
                            | (
                                (VisitorPass.pass_type == VisitorPassType.DURATION)
                                & (VisitorPass.status == VisitorPassStatus.ACTIVE)
                            )
                        ),
                        VisitorPass.number_plate == plate,
                        VisitorPass.departure_time.is_(None),
                        VisitorPass.arrival_time.is_not(None),
                        VisitorPass.arrival_time <= read.captured_at,
                    )
                    .order_by(VisitorPass.arrival_time.desc(), VisitorPass.created_at.desc())
                    .limit(1)
                )
            ).first()
        if not row:
            return read

        visitor_pass_id, _arrival_time = row
        if self._visitor_pass_candidate_kind(read) != "departure":
            return read

        raw_payload = dict(read.raw_payload or {})
        raw_payload[VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY] = {
            "kind": "departure",
            "visitor_pass_id": str(visitor_pass_id),
            "registration_number": plate,
        }
        return PlateRead(
            registration_number=plate,
            confidence=read.confidence,
            source=read.source,
            captured_at=read.captured_at,
            raw_payload=raw_payload,
        )

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
            finalized = await self._finalize_window(window)
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
        self._remember_exact_plate_resolution(window, finalized)

    def _pop_exact_known_plate_window(self, window: DebounceWindow) -> DebounceWindow:
        exact_read = next(
            (read for read in window.reads if _is_exact_known_vehicle_plate_match(read)),
            window.best_read,
        )
        return self._pop_related_read_window(window, exact_read)

    def _pop_related_read_window(self, window: DebounceWindow, anchor_read: PlateRead) -> DebounceWindow:
        max_seconds = self._runtime.lpr_debounce_max_seconds if self._runtime else settings.lpr_debounce_max_seconds
        related: list[DebounceWindow] = []
        remaining: list[DebounceWindow] = []
        for item in self._pending:
            if item is window:
                continue
            if item.best_read.source == anchor_read.source and self._window_overlaps_anchor_read(
                item,
                anchor_read,
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
        return self._window_overlaps_anchor_read(window, exact_read, max_seconds)

    def _window_overlaps_anchor_read(
        self,
        window: DebounceWindow,
        anchor_read: PlateRead,
        max_seconds: float,
    ) -> bool:
        anchor_at = anchor_read.captured_at
        return window.first_seen <= anchor_at <= window.first_seen + timedelta(seconds=max_seconds)

    async def _finalize_visitor_pass_window(self, window: DebounceWindow, anchor_read: PlateRead) -> None:
        try:
            await self._finalize_window(window)
        except Exception:
            logger.exception(
                "access_event_finalize_failed",
                extra={
                    "candidate_count": len(window.reads),
                    "best_registration_number": window.best_read.registration_number,
                    "reason": "visitor_pass_departure_plate",
                },
            )
            await event_bus.publish(
                "access_event.finalize_failed",
                {
                    "registration_number": window.best_read.registration_number,
                    "candidate_count": len(window.reads),
                    "reason": "visitor_pass_departure_plate",
                },
            )
            return
        self._remember_visitor_pass_resolution(window, anchor_read)

    def _remember_exact_plate_resolution(
        self,
        window: DebounceWindow,
        finalized: FinalizedPlateEvent | None = None,
    ) -> None:
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
        debounce_expires_at = window.first_seen + timedelta(seconds=max_seconds)
        gate_cycle_expires_at = debounce_expires_at
        if finalized and finalized.decision == AccessDecision.GRANTED:
            gate_cycle_expires_at = max(
                gate_cycle_expires_at,
                window.first_seen + timedelta(seconds=EXACT_PLATE_GATE_CYCLE_SUPPRESSION_SECONDS),
            )
        self._recent_exact_resolutions.append(
            ResolvedPlateWindow(
                source=exact_read.source,
                registration_number=exact_read.registration_number,
                first_seen=window.first_seen,
                debounce_expires_at=debounce_expires_at,
                gate_cycle_expires_at=gate_cycle_expires_at,
                direction=finalized.direction if finalized else None,
                decision=finalized.decision if finalized else None,
            )
        )

    def _exact_resolution_suppression_reason(self, read: PlateRead) -> str | None:
        self._prune_recent_exact_resolutions(read.captured_at)
        match = _known_vehicle_plate_match_from_read(read)
        read_gate_state = self._coerce_gate_state(self._gate_observation_from_read(read).get("state"))
        for resolution in self._recent_exact_resolutions:
            if read.source != resolution.source:
                continue
            if resolution.first_seen <= read.captured_at <= resolution.debounce_expires_at:
                if match and read.registration_number != resolution.registration_number:
                    continue
                return "exact_known_vehicle_plate_already_resolved_in_debounce_window"
            if not (
                resolution.decision == AccessDecision.GRANTED
                and resolution.direction in {AccessDirection.ENTRY, AccessDirection.EXIT}
                and match
                and read.registration_number == resolution.registration_number
                and read_gate_state != GateState.CLOSED
                and resolution.first_seen <= read.captured_at <= resolution.gate_cycle_expires_at
            ):
                continue
            return "exact_known_vehicle_plate_already_resolved_in_gate_cycle"
        return None

    def _prune_recent_exact_resolutions(self, now: datetime) -> None:
        self._recent_exact_resolutions = [
            resolution
            for resolution in self._recent_exact_resolutions
            if max(resolution.debounce_expires_at, resolution.gate_cycle_expires_at) >= now
        ]

    def _remember_visitor_pass_resolution(self, window: DebounceWindow, anchor_read: PlateRead) -> None:
        max_seconds = self._runtime.lpr_debounce_max_seconds if self._runtime else settings.lpr_debounce_max_seconds
        self._recent_visitor_pass_resolutions.append(
            ResolvedPlateWindow(
                source=anchor_read.source,
                registration_number=anchor_read.registration_number,
                first_seen=window.first_seen,
                debounce_expires_at=window.first_seen + timedelta(seconds=max_seconds),
                gate_cycle_expires_at=window.first_seen + timedelta(seconds=max_seconds),
            )
        )

    def _suppress_after_visitor_pass_resolution(self, read: PlateRead) -> bool:
        self._prune_recent_visitor_pass_resolutions(read.captured_at)
        if _is_exact_known_vehicle_plate_match(read) or _is_visitor_pass_plate_match(read):
            return False
        for resolution in self._recent_visitor_pass_resolutions:
            if read.source != resolution.source:
                continue
            if not (resolution.first_seen <= read.captured_at <= resolution.debounce_expires_at):
                continue
            return True
        return False

    def _prune_recent_visitor_pass_resolutions(self, now: datetime) -> None:
        self._recent_visitor_pass_resolutions = [
            resolution
            for resolution in self._recent_visitor_pass_resolutions
            if resolution.debounce_expires_at >= now
        ]

    async def _publish_suppressed_read(self, read: PlateRead, *, reason: str) -> None:
        match = _known_vehicle_plate_match_from_read(read) or {}
        await event_bus.publish(
            "plate_read.suppressed",
            {
                "registration_number": read.registration_number,
                "detected_registration_number": match.get("detected_registration_number") or read.registration_number,
                "source": read.source,
                "reason": reason,
            },
        )

    async def _vehicle_session_suppression(self, read: PlateRead) -> VehicleSessionSuppression | None:
        context = self._vehicle_session_context_from_read(read)
        if not context.normalized_registration_number:
            return None
        idle_seconds = self._vehicle_session_idle_seconds()
        self._prune_active_vehicle_sessions(read.captured_at, idle_seconds)

        for session in sorted(self._active_vehicle_sessions, key=lambda item: item.last_seen_at, reverse=True):
            matched_by = self._vehicle_session_match(session, context, read)
            if not matched_by:
                continue
            evidence = await self._vehicle_session_presence_evidence(session, context, read, idle_seconds)
            if read.captured_at <= session.last_seen_at + timedelta(seconds=idle_seconds) or evidence:
                return VehicleSessionSuppression(
                    session=session,
                    reason="vehicle_session_already_active",
                    matched_by=matched_by,
                    evidence=evidence,
                )

        return await self._vehicle_session_db_fallback(read, context, idle_seconds)

    def _vehicle_session_idle_seconds(self) -> float:
        configured = (
            getattr(self._runtime, "lpr_vehicle_session_idle_seconds", None)
            if self._runtime
            else settings.lpr_vehicle_session_idle_seconds
        )
        try:
            value = float(configured)
        except (TypeError, ValueError):
            value = settings.lpr_vehicle_session_idle_seconds
        return max(10.0, value)

    def _prune_active_vehicle_sessions(self, now: datetime, idle_seconds: float) -> None:
        horizon = timedelta(seconds=max(idle_seconds * 3, idle_seconds + 300.0))
        self._active_vehicle_sessions = [
            session
            for session in self._active_vehicle_sessions
            if session.last_seen_at + horizon >= now
        ][-100:]

    def _vehicle_session_match(
        self,
        session: ActiveVehicleSession,
        context: VehicleSessionContext,
        read: PlateRead,
    ) -> str | None:
        if read.source != session.source:
            return None
        same_plate = (
            context.normalized_registration_number == session.normalized_registration_number
            or self._is_similar_plate(
                context.normalized_registration_number,
                session.normalized_registration_number,
            )
        )
        if same_plate:
            return "registration_number"

        same_event = bool(context.protect_event_ids & session.protect_event_ids)
        if not same_event:
            return None

        if _known_vehicle_plate_match_from_read(read):
            return None
        return "protect_event_id"

    async def _vehicle_session_presence_evidence(
        self,
        session: ActiveVehicleSession,
        context: VehicleSessionContext,
        read: PlateRead,
        idle_seconds: float,
    ) -> dict[str, Any] | None:
        return await get_vehicle_presence_tracker().recent_evidence(
            registration_number=context.registration_number,
            event_ids=context.protect_event_ids | session.protect_event_ids,
            camera_id=context.camera_id or session.camera_id,
            device_id=context.device_id or session.device_id,
            observed_at=read.captured_at,
            max_age_seconds=idle_seconds,
        )

    async def _vehicle_session_db_fallback(
        self,
        read: PlateRead,
        context: VehicleSessionContext,
        idle_seconds: float,
    ) -> VehicleSessionSuppression | None:
        lookup_horizon = timedelta(seconds=max(idle_seconds * 3, 3600.0))
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(AccessEvent)
                    .where(
                        AccessEvent.source == read.source,
                        AccessEvent.occurred_at <= read.captured_at,
                        AccessEvent.occurred_at >= read.captured_at - lookup_horizon,
                    )
                    .order_by(AccessEvent.occurred_at.desc())
                    .limit(50)
                )
            ).all()

        for event in rows:
            candidate = self._vehicle_session_from_event(event)
            if not candidate:
                continue
            matched_by = self._vehicle_session_match(candidate, context, read)
            if not matched_by:
                continue
            evidence = await self._vehicle_session_presence_evidence(candidate, context, read, idle_seconds)
            if read.captured_at <= candidate.last_seen_at + timedelta(seconds=idle_seconds) or evidence:
                self._upsert_active_vehicle_session(candidate)
                return VehicleSessionSuppression(
                    session=candidate,
                    reason="vehicle_session_already_active",
                    matched_by=f"db_{matched_by}",
                    evidence=evidence,
                )
        return None

    async def _annotate_suppressed_session_read(
        self,
        read: PlateRead,
        suppression: VehicleSessionSuppression,
    ) -> None:
        context = self._vehicle_session_context_from_read(read)
        session = suppression.session
        session.last_seen_at = max(session.last_seen_at, read.captured_at)
        session.protect_event_ids.update(context.protect_event_ids)
        session.camera_id = session.camera_id or context.camera_id
        session.device_id = session.device_id or context.device_id

        try:
            event_uuid = uuid.UUID(session.event_id)
        except ValueError:
            return

        async with AsyncSessionLocal() as db:
            event = await db.get(AccessEvent, event_uuid)
            if not event:
                return
            payload = dict(event.raw_payload or {})
            vehicle_session = payload.get(VEHICLE_SESSION_PAYLOAD_KEY)
            vehicle_session = dict(vehicle_session) if isinstance(vehicle_session, dict) else {}
            vehicle_session.setdefault("id", str(event.id))
            vehicle_session.setdefault("started_at", event.occurred_at.isoformat())
            vehicle_session.setdefault("registration_number", event.registration_number)
            vehicle_session.setdefault(
                "normalized_registration_number",
                self._normalize_registration_number(event.registration_number),
            )
            vehicle_session["last_seen_at"] = read.captured_at.isoformat()
            vehicle_session["last_gate_state"] = self._gate_observation_from_read(read).get("state")
            vehicle_session["suppressed_read_count"] = int(vehicle_session.get("suppressed_read_count") or 0) + 1
            vehicle_session["last_suppressed_reason"] = suppression.reason
            vehicle_session["last_matched_by"] = suppression.matched_by
            if suppression.evidence:
                vehicle_session["last_presence_evidence"] = self._vehicle_presence_evidence_payload(suppression.evidence)

            protect_event_ids = set(self._string_list(vehicle_session.get("protect_event_ids")))
            protect_event_ids.update(context.protect_event_ids)
            vehicle_session["protect_event_ids"] = sorted(protect_event_ids)

            ocr_variants = set(self._string_list(vehicle_session.get("ocr_variants")))
            ocr_variants.add(_detected_registration_number(read))
            ocr_variants.add(read.registration_number)
            vehicle_session["ocr_variants"] = sorted(value for value in ocr_variants if value)

            suppressed_reads = vehicle_session.get("suppressed_reads")
            suppressed_reads = list(suppressed_reads) if isinstance(suppressed_reads, list) else []
            suppressed_reads.append(self._suppressed_session_read_payload(read, suppression))
            vehicle_session["suppressed_reads"] = suppressed_reads[-MAX_SUPPRESSED_SESSION_READS:]

            payload[VEHICLE_SESSION_PAYLOAD_KEY] = vehicle_session
            event.raw_payload = payload
            await db.commit()

    def _initial_vehicle_session_payload(
        self,
        window: DebounceWindow,
        read: PlateRead,
        event: AccessEvent,
    ) -> dict[str, Any]:
        context = self._vehicle_session_context_from_read(read)
        variants = {
            _detected_registration_number(item)
            for item in window.reads
            if _detected_registration_number(item)
        }
        variants.update(item.registration_number for item in window.reads if item.registration_number)
        return {
            "id": str(event.id),
            "source": read.source,
            "registration_number": read.registration_number,
            "normalized_registration_number": context.normalized_registration_number,
            "started_at": window.first_seen.isoformat(),
            "last_seen_at": window.updated_at.isoformat(),
            "direction": event.direction.value,
            "decision": event.decision.value,
            "camera_id": context.camera_id,
            "device_id": context.device_id,
            "protect_event_ids": sorted(context.protect_event_ids),
            "ocr_variants": sorted(variants),
            "last_gate_state": self._gate_observation_from_read(read).get("state"),
            "suppressed_read_count": 0,
            "suppressed_reads": [],
        }

    def _remember_vehicle_session(
        self,
        event: AccessEvent,
        window: DebounceWindow,
        read: PlateRead,
    ) -> None:
        context = self._vehicle_session_context_from_read(read)
        if not context.normalized_registration_number:
            return
        self._upsert_active_vehicle_session(
            ActiveVehicleSession(
                event_id=str(event.id),
                source=read.source,
                registration_number=read.registration_number,
                normalized_registration_number=context.normalized_registration_number,
                started_at=window.first_seen,
                last_seen_at=window.updated_at,
                direction=event.direction,
                decision=event.decision,
                camera_id=context.camera_id,
                device_id=context.device_id,
                protect_event_ids=set(context.protect_event_ids),
            )
        )

    def _upsert_active_vehicle_session(self, session: ActiveVehicleSession) -> None:
        self._active_vehicle_sessions = [
            item for item in self._active_vehicle_sessions if item.event_id != session.event_id
        ]
        self._active_vehicle_sessions.append(session)

    def _vehicle_session_from_event(self, event: AccessEvent) -> ActiveVehicleSession | None:
        payload = dict(event.raw_payload or {})
        session_payload = payload.get(VEHICLE_SESSION_PAYLOAD_KEY)
        session_payload = session_payload if isinstance(session_payload, dict) else {}
        best_payload = payload.get("best")
        context = self._vehicle_session_context_from_payload(
            best_payload if isinstance(best_payload, dict) else {},
            source=event.source,
            registration_number=event.registration_number,
        )
        normalized = str(
            session_payload.get("normalized_registration_number")
            or context.normalized_registration_number
            or self._normalize_registration_number(event.registration_number)
        )
        if not normalized:
            return None
        last_seen_at = self._datetime_from_payload(session_payload.get("last_seen_at")) or event.occurred_at
        started_at = self._datetime_from_payload(session_payload.get("started_at")) or event.occurred_at
        return ActiveVehicleSession(
            event_id=str(event.id),
            source=event.source,
            registration_number=event.registration_number,
            normalized_registration_number=normalized,
            started_at=started_at,
            last_seen_at=last_seen_at,
            direction=event.direction,
            decision=event.decision,
            camera_id=str(session_payload.get("camera_id") or context.camera_id or "") or None,
            device_id=str(session_payload.get("device_id") or context.device_id or "") or None,
            protect_event_ids=set(self._string_list(session_payload.get("protect_event_ids")))
            | context.protect_event_ids,
        )

    def _vehicle_session_context_from_read(self, read: PlateRead) -> VehicleSessionContext:
        return self._vehicle_session_context_from_payload(
            read.raw_payload or {},
            source=read.source,
            registration_number=read.registration_number,
        )

    def _vehicle_session_context_from_payload(
        self,
        payload: dict[str, Any],
        *,
        source: str,
        registration_number: str,
    ) -> VehicleSessionContext:
        return VehicleSessionContext(
            source=source,
            registration_number=registration_number,
            normalized_registration_number=self._normalize_registration_number(registration_number),
            camera_id=self._first_payload_value(payload, ("cameraId", "camera_id", "sensorId", "sensor_id")),
            device_id=self._first_payload_value(payload, ("device", "deviceId", "device_id")),
            protect_event_ids=set(self._payload_values(payload, ("eventId", "event_id")))
            | self._event_ids_from_paths(payload),
        )

    def _suppressed_session_read_payload(
        self,
        read: PlateRead,
        suppression: VehicleSessionSuppression,
    ) -> dict[str, Any]:
        context = self._vehicle_session_context_from_read(read)
        return {
            "registration_number": read.registration_number,
            "detected_registration_number": _detected_registration_number(read),
            "captured_at": read.captured_at.isoformat(),
            "confidence": read.confidence,
            "source": read.source,
            "gate_state": self._gate_observation_from_read(read).get("state"),
            "reason": suppression.reason,
            "matched_by": suppression.matched_by,
            "protect_event_ids": sorted(context.protect_event_ids),
            "presence_evidence": self._vehicle_presence_evidence_payload(suppression.evidence)
            if suppression.evidence
            else None,
        }

    def _vehicle_presence_evidence_payload(self, evidence: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "source",
            "source_detail",
            "active",
            "observed_at",
            "registration_number",
            "event_id",
            "camera_id",
            "device_id",
            "age_seconds",
        )
        return {key: evidence.get(key) for key in keys if evidence.get(key) is not None}

    def _payload_values(self, value: Any, keys: tuple[str, ...]) -> list[str]:
        normalized_keys = {self._payload_key(key) for key in keys}
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if self._payload_key(str(key)) in normalized_keys:
                    found.extend(self._scalar_payload_values(item))
                found.extend(self._payload_values(item, keys))
        elif isinstance(value, list):
            for item in value:
                found.extend(self._payload_values(item, keys))
        return self._dedupe_strings(found)

    def _scalar_payload_values(self, value: Any) -> list[str]:
        if value is None or isinstance(value, bool):
            return []
        if isinstance(value, str | int | float):
            text = str(value).strip()
            return [text] if text else []
        if isinstance(value, list):
            return [item for value_item in value for item in self._scalar_payload_values(value_item)]
        return []

    def _first_payload_value(self, value: Any, keys: tuple[str, ...]) -> str | None:
        values = self._payload_values(value, keys)
        return values[0] if values else None

    def _event_ids_from_paths(self, value: Any) -> set[str]:
        ids: set[str] = set()
        for path in self._payload_values(value, ("eventPath", "eventLocalLink", "event_local_link")):
            match = re.search(r"/event/([^/?#]+)", path)
            if match:
                ids.add(match.group(1))
        return ids

    def _payload_key(self, key: str) -> str:
        return re.sub(r"[^a-z0-9]", "", key.lower())

    def _string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return self._dedupe_strings(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _dedupe_strings(self, values: Any) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    def _datetime_from_payload(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def _is_similar_plate(self, left: str, right: str) -> bool:
        if left == right:
            return True
        threshold = self._runtime.lpr_similarity_threshold if self._runtime else settings.lpr_similarity_threshold
        return SequenceMatcher(a=left, b=right).ratio() >= threshold

    async def _flush_expired_windows(self) -> None:
        if await is_maintenance_mode_active():
            self._clear_pending_reads()
            return
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
        if await is_maintenance_mode_active():
            self._clear_pending_reads()
            return
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

    async def _finalize_window(self, window: DebounceWindow) -> FinalizedPlateEvent | None:
        if await is_maintenance_mode_active():
            self._clear_pending_reads()
            return
        read = window.best_read
        direction_read = read if _is_visitor_pass_plate_match(read) else window.first_read
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
            vehicle = await self._lookup_active_vehicle(session, read, trace)
            person = vehicle.owner if vehicle else None
            runtime = self._runtime or await get_runtime_config()
            identity_active = bool(vehicle and (not person or person.is_active))
            if not vehicle:
                visitor_pass, visitor_pass_mode = await self._match_visitor_pass(session, read, direction_read, trace)
            else:
                visitor_pass, visitor_pass_mode = None, None
            schedule_evaluation = await self._schedule_evaluation_for_detection(
                session,
                vehicle=vehicle,
                identity_active=identity_active,
                visitor_pass=visitor_pass,
                visitor_pass_mode=visitor_pass_mode,
                captured_at=read.captured_at,
                runtime=runtime,
                trace=trace,
            )
            allowed = bool(schedule_evaluation and schedule_evaluation.allowed and (identity_active or visitor_pass))
            direction_span = trace.start_span(
                "Direction Classification",
                attributes={
                    "allowed": allowed,
                    "gate_observation": self._gate_observation_from_read(direction_read),
                    "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                },
            )
            direction, direction_resolution = await self._resolve_direction(
                session,
                direction_read,
                person,
                allowed,
                vehicle=vehicle,
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
            vehicle_visual_detection = await self._vehicle_visual_detection_for_read(
                read,
                wait_for_match=vehicle is None,
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
                raw_payload=self._access_event_raw_payload(
                    window=window,
                    read=read,
                    schedule_evaluation=schedule_evaluation,
                    direction_resolution=direction_resolution,
                    vehicle_visual_detection=vehicle_visual_detection,
                    visitor_pass=visitor_pass,
                    visitor_pass_mode=visitor_pass_mode,
                    trace_id=trace.trace_id,
                ),
            )
            session.add(event)
            await session.flush()
            event.raw_payload = {
                **(event.raw_payload or {}),
                VEHICLE_SESSION_PAYLOAD_KEY: self._initial_vehicle_session_payload(window, read, event),
            }
            await self._capture_event_snapshot(event, trace=trace)

            if visitor_pass:
                visitor_service = get_visitor_pass_service()
                if visitor_pass_mode == "arrival":
                    await visitor_service.record_arrival(
                        session,
                        visitor_pass,
                        event=event,
                        dvla_enrichment=dvla_enrichment,
                        visual_detection=vehicle_visual_detection,
                        trace_id=trace.trace_id,
                    )
                elif visitor_pass_mode == "departure":
                    await visitor_service.record_departure(session, visitor_pass, event=event)

            anomalies = await self._build_anomalies(
                session,
                event,
                person,
                vehicle,
                allowed,
                visitor_pass=visitor_pass,
            )
            session.add_all(anomalies)

            if allowed and person:
                await self._update_presence(session, person, event)

            if visitor_pass:
                await session.flush()
                await session.refresh(visitor_pass)
                visitor_pass_realtime_payload = serialize_visitor_pass(visitor_pass)
            else:
                visitor_pass_realtime_payload = None
            access_event_realtime_payload = self._access_event_realtime_payload(
                event,
                anomaly_count=len(anomalies),
                visitor_pass=visitor_pass,
                visitor_pass_mode=visitor_pass_mode,
            )

            await session.commit()
            self._remember_vehicle_session(event, window, read)
            persistence_span.finish(
                output_payload={
                    "event_id": str(event.id),
                    "anomaly_count": len(anomalies),
                    "presence_updated": bool(allowed and person),
                    "snapshot_path": event.snapshot_path,
                    "snapshot_bytes": event.snapshot_bytes,
                }
            )

        await event_bus.publish(
            "access_event.finalized",
            access_event_realtime_payload,
        )
        if visitor_pass and visitor_pass_realtime_payload:
            await event_bus.publish(
                "visitor_pass.used" if visitor_pass_mode == "arrival" else "visitor_pass.departure_recorded",
                {"visitor_pass": visitor_pass_realtime_payload},
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
                "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                "visitor_name": visitor_pass.visitor_name if visitor_pass else None,
            },
        )
        return FinalizedPlateEvent(
            event_id=str(event.id),
            direction=direction,
            decision=decision,
            occurred_at=event.occurred_at,
        )

    async def _lookup_active_vehicle(self, session: AsyncSession, read: PlateRead, trace: Any) -> Vehicle | None:
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
        return vehicle

    async def _match_visitor_pass(
        self,
        session: AsyncSession,
        read: PlateRead,
        direction_read: PlateRead,
        trace: Any,
    ) -> tuple[VisitorPass | None, str | None]:
        visitor_pass_mode = self._visitor_pass_candidate_kind(direction_read)
        visitor_span = trace.start_span(
            "Visitor Pass Matching",
            attributes={
                "registration_number": read.registration_number,
                "candidate_kind": visitor_pass_mode,
            },
        )
        visitor_service = get_visitor_pass_service()
        visitor_pass: VisitorPass | None = None
        if visitor_pass_mode == "arrival":
            visitor_pass = await visitor_service.claim_active_pass(
                session,
                occurred_at=read.captured_at,
                registration_number=read.registration_number,
            )
        elif visitor_pass_mode == "departure":
            visitor_pass = await visitor_service.find_departure_pass(
                session,
                occurred_at=read.captured_at,
                registration_number=read.registration_number,
            )
        visitor_span.finish(
            output_payload={
                "matched": bool(visitor_pass),
                "mode": visitor_pass_mode,
                "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                "visitor_name": visitor_pass.visitor_name if visitor_pass else None,
            }
        )
        return visitor_pass, visitor_pass_mode

    async def _schedule_evaluation_for_detection(
        self,
        session: AsyncSession,
        *,
        vehicle: Vehicle | None,
        identity_active: bool,
        visitor_pass: VisitorPass | None,
        visitor_pass_mode: str | None,
        captured_at: datetime,
        runtime: RuntimeConfig,
        trace: Any,
    ) -> ScheduleEvaluation | None:
        person = vehicle.owner if vehicle else None
        schedule_span = trace.start_span(
            "Schedule & Access Rule Evaluation",
            attributes={
                "identity_active": identity_active,
                "vehicle_id": str(vehicle.id) if vehicle else None,
                "person_id": str(person.id) if person else None,
                "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
            },
        )
        if vehicle and identity_active:
            schedule_evaluation = await evaluate_vehicle_schedule(
                session,
                vehicle,
                captured_at,
                timezone_name=runtime.site_timezone,
                default_policy=runtime.schedule_default_policy,
            )
        elif visitor_pass:
            schedule_evaluation = ScheduleEvaluation(
                allowed=True,
                source=f"visitor_pass_{visitor_pass_mode or 'match'}",
                reason=f"Visitor pass matched for {visitor_pass.visitor_name}.",
            )
        else:
            schedule_evaluation = None
        schedule_span.finish(output_payload=self._schedule_evaluation_payload(schedule_evaluation))
        return schedule_evaluation

    def _schedule_evaluation_payload(self, schedule_evaluation: ScheduleEvaluation | None) -> dict[str, Any]:
        return {
            "allowed": schedule_evaluation.allowed if schedule_evaluation else False,
            "source": schedule_evaluation.source if schedule_evaluation else "none",
            "schedule_id": str(schedule_evaluation.schedule_id)
            if schedule_evaluation and schedule_evaluation.schedule_id
            else None,
            "schedule_name": schedule_evaluation.schedule_name if schedule_evaluation else None,
            "override_id": str(schedule_evaluation.override_id)
            if schedule_evaluation and schedule_evaluation.override_id
            else None,
            "override_ends_at": schedule_evaluation.override_ends_at.isoformat()
            if schedule_evaluation and schedule_evaluation.override_ends_at
            else None,
            "reason": schedule_evaluation.reason if schedule_evaluation else "No active vehicle identity matched.",
        }

    def _access_event_raw_payload(
        self,
        *,
        window: DebounceWindow,
        read: PlateRead,
        schedule_evaluation: ScheduleEvaluation | None,
        direction_resolution: dict[str, Any],
        vehicle_visual_detection: dict[str, Any] | None,
        visitor_pass: VisitorPass | None,
        visitor_pass_mode: str | None,
        trace_id: str,
    ) -> dict[str, Any]:
        return {
            "best": read.raw_payload,
            "schedule": self._schedule_evaluation_payload(schedule_evaluation),
            "debounce": {
                "candidate_count": len(window.reads),
                "candidates": [
                    {
                        "registration_number": item.registration_number,
                        "detected_registration_number": _detected_registration_number(item),
                        "confidence": item.confidence,
                        "captured_at": item.captured_at.isoformat(),
                        "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(item),
                        "visitor_pass_plate_match": _visitor_pass_plate_match_from_read(item),
                    }
                    for item in window.reads
                ],
            },
            "direction_resolution": direction_resolution,
            VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY: vehicle_visual_detection,
            VISITOR_PASS_PAYLOAD_KEY: self._visitor_pass_payload(visitor_pass, visitor_pass_mode),
            "telemetry": {"trace_id": trace_id},
        }

    async def _capture_event_snapshot(
        self,
        event: AccessEvent,
        *,
        trace: Any | None = None,
    ) -> None:
        if not event.id:
            return
        manager = get_snapshot_manager()
        protect_event_id = protect_event_id_from_access_event(event)
        span = (
            trace.start_span(
                "Capture Access Event Snapshot",
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                attributes={
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "camera": GATE_CAMERA_IDENTIFIER,
                },
            )
            if trace
            else None
        )
        capture_error: Exception | None = None
        metadata = None
        source = "camera_snapshot"
        try:
            metadata = await manager.capture_access_event_snapshot(
                event.id,
                camera=GATE_CAMERA_IDENTIFIER,
            )
        except Exception as exc:
            capture_error = exc
            logger.info(
                "access_event_snapshot_capture_skipped",
                extra={
                    "event_id": str(event.id),
                    "registration_number": event.registration_number,
                    "camera": GATE_CAMERA_IDENTIFIER,
                    "error": str(exc),
                    "fallback_protect_event_id": protect_event_id,
                },
            )

        if metadata is None and protect_event_id:
            try:
                media = await get_unifi_protect_service().event_thumbnail(
                    protect_event_id,
                    width=SNAPSHOT_WIDTH,
                    height=SNAPSHOT_HEIGHT,
                )
                metadata = await manager.store_image(
                    media.content,
                    relative_path=access_event_snapshot_relative_path(event.id),
                    url=access_event_snapshot_url(event.id),
                    camera=GATE_CAMERA_IDENTIFIER,
                    captured_at=event.occurred_at,
                )
                source = "protect_event_thumbnail"
            except Exception as exc:
                logger.info(
                    "access_event_snapshot_thumbnail_capture_skipped",
                    extra={
                        "event_id": str(event.id),
                        "registration_number": event.registration_number,
                        "protect_event_id": protect_event_id,
                        "error": str(exc),
                    },
                )
                if capture_error is None:
                    capture_error = exc

        if metadata is None:
            if span:
                output = {
                    "captured": False,
                    "reason": "capture_failed" if capture_error else "unifi_protect_unconfigured",
                    "protect_event_id": protect_event_id,
                }
                if capture_error:
                    span.finish(status="error", error=capture_error, output_payload=output)
                else:
                    span.finish(output_payload=output)
            return

        apply_snapshot_to_access_event(event, metadata)
        if span:
            span.finish(
                output_payload={
                    "captured": True,
                    "source": source,
                    "protect_event_id": protect_event_id,
                    "snapshot_path": metadata.relative_path,
                    "bytes": metadata.bytes,
                    "width": metadata.width,
                    "height": metadata.height,
                }
            )

    def _access_event_realtime_payload(
        self,
        event: AccessEvent,
        *,
        anomaly_count: int,
        visitor_pass: VisitorPass | None,
        visitor_pass_mode: str | None,
    ) -> dict[str, Any]:
        payload = {
            "event_id": str(event.id),
            "access_event_id": str(event.id),
            "person_id": str(event.person_id) if event.person_id else None,
            "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
            "registration_number": event.registration_number,
            "direction": event.direction.value,
            "decision": event.decision.value,
            "confidence": event.confidence,
            "source": event.source,
            "occurred_at": event.occurred_at.isoformat(),
            "event_type": "access_event.finalized",
            "timing_classification": event.timing_classification.value,
            "anomaly_count": anomaly_count,
            "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
            "visitor_name": visitor_pass.visitor_name if visitor_pass else None,
            "visitor_pass_mode": visitor_pass_mode if visitor_pass else None,
        }
        payload.update(access_event_snapshot_payload(event))
        return payload

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
            normalized = await lookup_normalized_vehicle_registration(registration_number, today=today)
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

    async def _vehicle_visual_detection_for_read(
        self,
        read: PlateRead,
        *,
        wait_for_match: bool,
        trace=None,
    ) -> dict[str, Any] | None:
        span = (
            trace.start_span(
                "Vehicle Visual Attribute Match",
                attributes={
                    "registration_number": read.registration_number,
                    "wait_for_match": wait_for_match,
                },
            )
            if trace
            else None
        )
        attempts = 5 if wait_for_match else 1
        recorder = get_vehicle_visual_detection_recorder()
        match: dict[str, Any] | None = None
        for attempt in range(1, attempts + 1):
            match = await recorder.recent_match(
                read.registration_number,
                occurred_at=read.captured_at,
                max_age_seconds=45.0,
            )
            if match:
                break
            if attempt < attempts:
                await asyncio.sleep(0.25)
        if span:
            span.finish(
                output_payload={
                    "matched": bool(match),
                    "observed_vehicle_type": (match or {}).get("observed_vehicle_type"),
                    "observed_vehicle_color": (match or {}).get("observed_vehicle_color"),
                    "source": (match or {}).get("source"),
                    "event_id": (match or {}).get("event_id"),
                }
            )
        return match

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
        visual_detection = self._vehicle_visual_detection_from_event(event)
        detected_vehicle_type = self._fact_text(
            visual_detection.get("observed_vehicle_type")
            or visual_detection.get("vehicle_type")
            or visual_detection.get("detected_vehicle_type")
        )
        detected_vehicle_colour = self._fact_text(
            visual_detection.get("observed_vehicle_color")
            or visual_detection.get("observed_vehicle_colour")
            or visual_detection.get("vehicle_color")
            or visual_detection.get("vehicle_colour")
            or visual_detection.get("detected_vehicle_color")
            or visual_detection.get("detected_vehicle_colour")
        )
        dvla_colour = self._fact_text(dvla.get("colour"))
        vehicle_colour = (
            (dvla_colour or (vehicle.color if vehicle else "") or detected_vehicle_colour)
            if vehicle
            else (detected_vehicle_colour or dvla_colour)
        )
        facts = {
            "message": message,
            "access_event_id": str(event.id),
            "telemetry_trace_id": str(((event.raw_payload or {}).get("telemetry") or {}).get("trace_id") or ""),
            "first_name": person.first_name if person else "",
            "last_name": person.last_name if person else "",
            "display_name": person.display_name if person else "",
            "group_name": group.name if group else "",
            "vehicle_registration_number": event.registration_number,
            "registration_number": event.registration_number,
            "vehicle_display_name": vehicle_display_name,
            "vehicle_make": vehicle_make,
            "vehicle_type": detected_vehicle_type,
            "vehicle_model": vehicle.model if vehicle and vehicle.model else "",
            "vehicle_color": vehicle_colour,
            "vehicle_colour": vehicle_colour,
            "detected_vehicle_type": detected_vehicle_type,
            "detected_vehicle_color": detected_vehicle_colour,
            "detected_vehicle_colour": detected_vehicle_colour,
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

    def _vehicle_visual_detection_from_event(self, event: AccessEvent) -> dict[str, Any]:
        payload = (event.raw_payload or {}).get(VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY)
        return payload if isinstance(payload, dict) else {}

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
        vehicle: Vehicle | None = None,
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

        visitor_pass_match = _visitor_pass_plate_match_from_read(read)
        if isinstance(visitor_pass_match, dict) and visitor_pass_match.get("kind") == "departure":
            resolution["source"] = "visitor_pass_presence"
            resolution["direction"] = AccessDirection.EXIT.value
            resolution["visitor_pass_id"] = visitor_pass_match.get("visitor_pass_id")
            return AccessDirection.EXIT, resolution

        gate_malfunction = _gate_malfunction_from_read(read)
        if gate_malfunction and vehicle:
            previous_event = await self._latest_live_vehicle_event(session, vehicle, read.captured_at)
            direction = (
                AccessDirection.EXIT
                if previous_event and previous_event.direction == AccessDirection.ENTRY
                else AccessDirection.ENTRY
            )
            resolution.update(
                {
                    "source": "gate_malfunction_vehicle_history",
                    "direction": direction.value,
                    "gate_malfunction": gate_malfunction,
                    "previous_live_event_id": str(previous_event.id) if previous_event else None,
                    "previous_live_direction": previous_event.direction.value if previous_event else None,
                    "previous_live_event_at": previous_event.occurred_at.isoformat() if previous_event else None,
                }
            )
            return direction, resolution

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

    async def _latest_live_vehicle_event(
        self,
        session: AsyncSession,
        vehicle: Vehicle,
        before: datetime,
    ) -> AccessEvent | None:
        rows = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    AccessEvent.vehicle_id == vehicle.id,
                    AccessEvent.decision == AccessDecision.GRANTED,
                    AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
                    AccessEvent.occurred_at < before,
                )
                .order_by(AccessEvent.occurred_at.desc(), AccessEvent.id.desc())
                .limit(20)
            )
        ).all()
        return next((event for event in rows if not self._access_event_is_backfilled(event)), None)

    def _access_event_is_backfilled(self, event: AccessEvent) -> bool:
        if "backfill" in str(event.source or "").casefold():
            return True
        raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
        return "backfill" in raw_payload

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

    def _visitor_pass_candidate_kind(self, read: PlateRead) -> str:
        visitor_pass_match = _visitor_pass_plate_match_from_read(read)
        if isinstance(visitor_pass_match, dict) and visitor_pass_match.get("kind") == "departure":
            return "departure"
        gate_observation = self._gate_observation_from_read(read)
        gate_state = self._coerce_gate_state(gate_observation.get("state"))
        if gate_state in DEPARTURE_GATE_STATES:
            return "departure"
        if gate_state in ARRIVAL_GATE_STATES:
            return "arrival"
        explicit = str((read.raw_payload or {}).get("direction") or (read.raw_payload or {}).get("Direction") or "").lower()
        if explicit in {"exit", "leave", "departure", "out"}:
            return "departure"
        return "arrival"

    def _visitor_pass_payload(
        self,
        visitor_pass: VisitorPass | None,
        mode: str | None,
    ) -> dict[str, Any] | None:
        if not visitor_pass:
            return None
        return {
            "id": str(visitor_pass.id),
            "visitor_name": visitor_pass.visitor_name,
            "pass_type": visitor_pass.pass_type.value,
            "status": visitor_pass.status.value,
            "mode": mode,
            "expected_time": visitor_pass.expected_time.isoformat(),
            "window_minutes": visitor_pass.window_minutes,
            "number_plate": visitor_pass.number_plate,
        }

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
            "entity_id": value.get("entity_id"),
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
        *,
        visitor_pass: VisitorPass | None = None,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        if not vehicle:
            if visitor_pass:
                return anomalies
            anomaly = Anomaly(
                event=event,
                anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                severity=AnomalySeverity.WARNING,
                message="Unauthorised Plate, Access Denied",
                context={
                    key: value
                    for key, value in {
                        "registration_number": event.registration_number,
                        "snapshot": alert_snapshot_metadata_from_event(event),
                    }.items()
                    if value is not None
                },
            )
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
