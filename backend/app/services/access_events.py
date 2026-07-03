import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.providers import analyze_image_with_provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.modules.gate.base import GateState
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError
from app.modules.registry import UnsupportedModuleError, get_gate_controller
from app.modules.lpr.base import PlateRead
from app.models import (
    AccessEvent,
    Anomaly,
    GateCommandRecord,
    LprIngestEvent,
    MovementSagaRecord,
    MovementSessionRecord,
    Person,
    Presence,
    Vehicle,
    VisitorPass,
)
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    MovementSagaState,
    PresenceState,
    TimingClassification,
    VisitorPassStatus,
    VisitorPassType,
)
from app.modules.notifications.base import NotificationContext
from app.services.access.hardware import (
    open_gate_for_access_event,
    publish_gate_open_skipped,
)
from app.services.access.payloads import (
    access_event_realtime_payload,
    authorized_entry_message,
    notification_facts,
    vehicle_display_name,
)
from app.services.access.snapshots import capture_access_event_snapshot
from app.services.snapshots import alert_snapshot_metadata_from_event
from app.services.dvla import NormalizedDvlaVehicle, lookup_normalized_vehicle_registration
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_commands import GateCommandOutcome
from app.services.gate_malfunctions import active_stuck_open_malfunction_at
from app.services.leaderboard import get_leaderboard_service
from app.services.lpr_ingest import (
    LPR_INGEST_STATUS_FAILED,
    LPR_INGEST_STATUS_SKIPPED,
    LprIngestRepository,
)
from app.services.lpr_zone_shadow import (
    LPR_ZONE_FILTER_SUPPRESSION_REASON,
    evaluate_lpr_zone_filter_for_read,
    get_lpr_zone_shadow_service,
)
from app.services.maintenance import is_maintenance_mode_active
from app.services.movement.presence import commit_presence_for_event
from app.services.movement.sessions import (
    ARRIVAL_GATE_STATES,
    DEPARTURE_GATE_STATES,
    ExternalVehicleSessionMatch,
    VEHICLE_SESSION_PAYLOAD_KEY,
    MovementSessionService,
    candidate_registration_numbers as _candidate_registration_numbers,
    coerce_access_direction,
    coerce_gate_state,
    datetime_from_payload,
    detected_registration_number as _detected_registration_number,
    explicit_direction_from_read,
    gate_observation_from_read,
    known_vehicle_plate_match_from_read as _known_vehicle_plate_match_from_read,
    normalize_registration_number,
    plates_are_similar,
    presence_evidence_payload,
    read_direction_hint,
)
from app.services.movement_ledger import get_movement_ledger_repository, movement_saga_summary
from app.services.movement_fsm import (
    CameraTieBreakerEvidence,
    MovementDirectionFSM,
    MovementIntent,
    MovementSuppressionFSM,
    PlateReadMovementEvidence,
    ResolvedMovementWindow,
)
from app.services.notifications import get_notification_service
from app.services.person_presence_input_booleans import apply_person_presence_input_boolean_actions
from app.services.schedules import ScheduleEvaluation, evaluate_vehicle_schedule
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    TELEMETRY_CATEGORY_LPR,
    audit_log_event_payload,
    telemetry,
    write_audit_log,
)
from app.services.unifi_protect import get_unifi_protect_service
from app.services.vehicle_visual_detections import (
    get_vehicle_visual_detection_recorder,
)
from app.services.visitor_passes import get_visitor_pass_service, serialize_visitor_pass

logger = get_logger(__name__)

__all__ = [
    "AccessEventService",
    "get_access_event_service",
]

GATE_OBSERVATION_PAYLOAD_KEY = "_iacs_gate_observation"
PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY = "_iacs_preserve_gate_observation"
GATE_MALFUNCTION_PAYLOAD_KEY = "_iacs_gate_malfunction"
KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY = "_iacs_known_vehicle_plate_match"
VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY = "_iacs_visitor_pass_plate_match"
PROCESSING_ATTEMPT_PAYLOAD_KEY = "_iacs_processing_attempt"
INGEST_METADATA_PAYLOAD_KEY = "_iacs_ingest"
LPR_INGEST_EVENT_PAYLOAD_KEY = "_iacs_lpr_ingest_event"
WEBHOOK_TRACE_PAYLOAD_KEY = "webhook_trace"
VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY = "vehicle_visual_detection"
VISITOR_PASS_PAYLOAD_KEY = "visitor_pass"
EXTERNAL_ADMISSION_PAYLOAD_KEY = "external_admission"
EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED = "gate_state_changed"
EXTERNAL_ADMISSION_SOURCE_LPR_OPEN_GATE = "lpr_open_gate_read"
EXTERNAL_ADMISSION_SOURCE_VEHICLE_SESSION = "external_vehicle_session"
EXTERNAL_ADMISSION_ORIGINAL_SUPERSEDED_REASON = "external_admission_confirmed"
EXTERNAL_ADMISSION_DEPARTURE_REASON = "external_departure_recorded"
GATE_CAMERA_IDENTIFIER = "camera.gate"
MAX_PLATE_READ_PROCESSING_ATTEMPTS = 3
PLATE_READ_RETRY_BACKOFF_SECONDS = (0.5, 2.0, 5.0)
WORKER_STALL_QUEUE_SECONDS = 60.0
CAMERA_TIEBREAKER_MIN_CONFIDENCE = 0.60
def dvla_mot_alert_required(mot_status: str | None) -> bool:
    normalized = (mot_status or "").strip().casefold().replace("_", " ")
    return bool(normalized and normalized not in {"valid", "not required"})


def dvla_tax_alert_required(tax_status: str | None) -> bool:
    return bool(tax_status and tax_status.strip().casefold() not in {"taxed", "sorn"})


def _is_exact_known_vehicle_plate_match(read: PlateRead) -> bool:
    match = _known_vehicle_plate_match_from_read(read)
    return bool(match and match.get("exact"))


def _visitor_pass_plate_match_from_read(read: PlateRead) -> dict[str, Any] | None:
    match = (read.raw_payload or {}).get(VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY)
    return match if isinstance(match, dict) else None


def _external_admission_from_read(read: PlateRead) -> dict[str, Any] | None:
    match = (read.raw_payload or {}).get(EXTERNAL_ADMISSION_PAYLOAD_KEY)
    return match if isinstance(match, dict) else None


def _gate_malfunction_from_read(read: PlateRead) -> dict[str, Any] | None:
    malfunction = (read.raw_payload or {}).get(GATE_MALFUNCTION_PAYLOAD_KEY)
    return malfunction if isinstance(malfunction, dict) else None


def _is_visitor_pass_plate_match(read: PlateRead) -> bool:
    return bool(_visitor_pass_plate_match_from_read(read))


def _plate_read_with_payload(
    read: PlateRead,
    raw_payload: dict[str, Any],
    *,
    registration_number: str | None = None,
) -> PlateRead:
    return replace(
        read,
        registration_number=registration_number or read.registration_number,
        raw_payload=raw_payload,
    )


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
                1 if _external_admission_from_read(read) else 0,
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
    first_seen: datetime
    debounce_expires_at: datetime


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
        self._recent_visitor_pass_resolutions: list[ResolvedPlateWindow] = []
        self._movement_direction_fsm = MovementDirectionFSM()
        self._movement_suppression_fsm = MovementSuppressionFSM()
        self._movement_ledger = get_movement_ledger_repository()
        self._movement_sessions = MovementSessionService(
            ledger_provider=lambda: self._movement_ledger,
            session_factory=lambda: AsyncSessionLocal(),
        )
        self._lpr_ingest_repository = LprIngestRepository()
        self._started_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None
        self._last_processed_at: datetime | None = None
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None
        self._consecutive_failures = 0
        self._total_failures = 0

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stop_event.clear()
        self._started_at = datetime.now(tz=UTC)
        self._last_heartbeat_at = self._started_at
        self._worker = asyncio.create_task(self._process_queue(), name="lpr-debounce-worker")
        self._worker.add_done_callback(self._handle_worker_done)
        event_bus.subscribe(self._handle_realtime_event, scope="all_workers")
        event_bus.subscribe(self._handle_local_realtime_event)
        try:
            await self._enqueue_pending_lpr_ingest_rows()
        except Exception as exc:
            self._record_worker_failure(exc)
            logger.warning(
                "lpr_ingest_startup_recovery_failed",
                extra={"error": self._safe_exception_detail(exc)},
            )
        logger.info("access_event_service_started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            await self._worker
        await self._flush_all_pending()
        event_bus.unsubscribe(self._handle_realtime_event)
        event_bus.unsubscribe(self._handle_local_realtime_event)
        logger.info("access_event_service_stopped")

    def status(self) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        worker_running = bool(self._worker and not self._worker.done())
        queue_depth = self._queue.qsize()
        heartbeat_age_seconds = (
            max(0.0, (now - self._last_heartbeat_at).total_seconds())
            if self._last_heartbeat_at
            else None
        )
        stalled = bool(
            worker_running
            and queue_depth > 0
            and heartbeat_age_seconds is not None
            and heartbeat_age_seconds > WORKER_STALL_QUEUE_SECONDS
        )
        if not worker_running:
            state = "down"
        elif stalled or self._consecutive_failures:
            state = "degraded"
        else:
            state = "ok"
        return {
            "status": state,
            "worker_running": worker_running,
            "queue_depth": queue_depth,
            "pending_windows": len(self._pending),
            "movement_sessions_source": "durable",
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_heartbeat_at": self._last_heartbeat_at.isoformat() if self._last_heartbeat_at else None,
            "last_processed_at": self._last_processed_at.isoformat() if self._last_processed_at else None,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at.isoformat() if self._last_error_at else None,
            "consecutive_failures": self._consecutive_failures,
            "total_failures": self._total_failures,
            "stalled": stalled,
        }

    async def enqueue_plate_read(self, read: PlateRead) -> None:
        if await is_maintenance_mode_active():
            return
        received_at = datetime.now(tz=UTC)
        read = self._read_with_webhook_trace(read, received_at)
        ingest_row, should_wake_worker = await self._persist_lpr_ingest_read(read, received_at=received_at)
        read = self._read_with_lpr_ingest_event(read, ingest_row)
        if not should_wake_worker:
            logger.info(
                "plate_read_ingest_duplicate_ignored",
                extra={
                    "registration_number": read.registration_number,
                    "source": read.source,
                    "captured_at": read.captured_at.isoformat(),
                    "ingest_event_id": str(ingest_row.id),
                    "ingest_status": ingest_row.status,
                },
            )
            return
        read = await self._read_with_gate_observation(read)
        gate_observation = gate_observation_from_read(read)
        logger.info(
            "plate_read_received",
            extra={
                "registration_number": read.registration_number,
                "confidence": read.confidence,
                "source": read.source,
                "captured_at": read.captured_at.isoformat(),
                "received_at": received_at.isoformat(),
                "capture_to_receive_ms": _datetime_delta_ms(received_at, read.captured_at),
                "gate_state": gate_observation.get("state"),
                "gate_observed_at": gate_observation.get("observed_at"),
                "ingest_event_id": str(ingest_row.id),
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

    def _read_with_webhook_trace(self, read: PlateRead, received_at: datetime) -> PlateRead:
        raw_payload = dict(read.raw_payload or {})
        existing = raw_payload.get(WEBHOOK_TRACE_PAYLOAD_KEY)
        existing = existing if isinstance(existing, dict) else {}
        ingest = raw_payload.get(INGEST_METADATA_PAYLOAD_KEY)
        ingest = ingest if isinstance(ingest, dict) else {}
        webhook_received_at = (
            datetime_from_payload(existing.get("received_at"))
            or datetime_from_payload(existing.get("webhook_received_at"))
            or datetime_from_payload(ingest.get("webhook_received_at"))
            or received_at
        )
        captured_to_webhook_ms = (
            self._float_from_payload(existing.get("captured_to_webhook_ms"))
            if "captured_to_webhook_ms" in existing
            else self._float_from_payload(ingest.get("captured_to_webhook_ms"))
        )
        if captured_to_webhook_ms is None:
            captured_to_webhook_ms = _datetime_delta_ms(webhook_received_at, read.captured_at)
        raw_payload[WEBHOOK_TRACE_PAYLOAD_KEY] = {
            "request_id": ingest.get("request_id"),
            "webhook_trace_id": ingest.get("webhook_trace_id"),
            "path": ingest.get("path"),
            "payload_shape_version": ingest.get("payload_shape_version"),
            **existing,
            "source": read.source,
            "registration_number": read.registration_number,
            "captured_at": read.captured_at.isoformat(),
            "received_at": webhook_received_at.astimezone(UTC).isoformat(),
            "webhook_received_at": webhook_received_at.astimezone(UTC).isoformat(),
            "captured_to_webhook_ms": captured_to_webhook_ms,
        }
        return _plate_read_with_payload(read, raw_payload)

    def _webhook_trace_from_read(self, read: PlateRead) -> dict[str, Any]:
        payload = (read.raw_payload or {}).get(WEBHOOK_TRACE_PAYLOAD_KEY)
        if isinstance(payload, dict):
            return dict(payload)
        return {
            "source": read.source,
            "registration_number": read.registration_number,
            "captured_at": read.captured_at.isoformat(),
            "received_at": None,
            "captured_to_webhook_ms": None,
        }

    def _webhook_trace_for_window(self, window: DebounceWindow) -> dict[str, Any]:
        first_read = getattr(window, "first_read", None)
        if first_read is None:
            first_read = min(window.reads, key=lambda read: read.captured_at)
        trace = self._webhook_trace_from_read(first_read)
        trace["candidate_count"] = len(window.reads)
        trace["window_first_seen"] = window.first_seen.isoformat()
        trace["window_updated_at"] = window.updated_at.isoformat()
        return trace

    async def _persist_lpr_ingest_read(
        self,
        read: PlateRead,
        *,
        received_at: datetime,
    ) -> tuple[LprIngestEvent, bool]:
        idempotency_key = self._lpr_ingest_idempotency_key(read)
        payload = self._lpr_ingest_normalized_payload(read)
        return await self._lpr_ingest_repo().persist_read(
            read,
            received_at=received_at,
            idempotency_key=idempotency_key,
            normalized_payload=payload,
        )

    async def _enqueue_pending_lpr_ingest_rows(self) -> int:
        rows = await self._lpr_ingest_repo().pending_rows()
        for row in rows:
            await self._queue.put(self._read_from_lpr_ingest_event(row))
        if rows:
            logger.info("lpr_ingest_pending_rows_enqueued", extra={"count": len(rows)})
        return len(rows)

    async def _claim_lpr_ingest_for_processing(self, read: PlateRead) -> bool:
        ingest_id = self._lpr_ingest_event_id_from_read(read)
        if ingest_id is None:
            return True
        return await self._lpr_ingest_repo().claim_for_processing(ingest_id)

    async def _mark_lpr_ingest_succeeded(
        self,
        read: PlateRead,
        *,
        movement_saga_id: uuid.UUID | None = None,
        access_event_id: uuid.UUID | None = None,
    ) -> None:
        ingest_id = self._lpr_ingest_event_id_from_read(read)
        if ingest_id is None:
            return
        await self._lpr_ingest_repo().mark_succeeded(
            ingest_id,
            access_event_id=access_event_id,
            movement_saga_id=movement_saga_id,
        )

    async def _mark_lpr_ingest_terminal(self, read: PlateRead, *, status: str, detail: str) -> None:
        ingest_id = self._lpr_ingest_event_id_from_read(read)
        if ingest_id is None:
            return
        await self._lpr_ingest_repo().mark_terminal(ingest_id, status=status, detail=detail)

    def _lpr_ingest_repo(self) -> LprIngestRepository:
        self._lpr_ingest_repository.session_factory = AsyncSessionLocal
        return self._lpr_ingest_repository

    def _lpr_ingest_idempotency_key(self, read: PlateRead) -> str:
        return f"lpr-ingest:{self._movement_saga_idempotency_key(read)}"

    def _lpr_ingest_normalized_payload(self, read: PlateRead) -> dict[str, Any]:
        return {
            "version": 1,
            "registration_number": read.registration_number,
            "confidence": read.confidence,
            "source": read.source,
            "captured_at": read.captured_at.isoformat(),
            "raw_payload": read.raw_payload or {},
            "candidate_registration_numbers": list(read.candidate_registration_numbers),
        }

    def _read_with_lpr_ingest_event(self, read: PlateRead, row: LprIngestEvent) -> PlateRead:
        raw_payload = dict(read.raw_payload or {})
        raw_payload[LPR_INGEST_EVENT_PAYLOAD_KEY] = {
            "id": str(row.id),
            "idempotency_key": row.idempotency_key,
            "status": row.status,
        }
        return _plate_read_with_payload(read, raw_payload)

    def _read_from_lpr_ingest_event(self, row: LprIngestEvent) -> PlateRead:
        payload = dict(row.normalized_payload or {})
        captured_at = row.captured_at
        raw_payload = payload.get("raw_payload")
        read = PlateRead(
            registration_number=str(payload.get("registration_number") or row.registration_number),
            confidence=float(payload.get("confidence") or 0.0),
            source=str(payload.get("source") or row.source),
            captured_at=captured_at,
            raw_payload=raw_payload if isinstance(raw_payload, dict) else {},
            candidate_registration_numbers=tuple(
                str(candidate)
                for candidate in payload.get("candidate_registration_numbers", [])
                if str(candidate or "").strip()
            ),
        )
        return self._read_with_lpr_ingest_event(read, row)

    def _lpr_ingest_event_id_from_read(self, read: PlateRead) -> uuid.UUID | None:
        metadata = (read.raw_payload or {}).get(LPR_INGEST_EVENT_PAYLOAD_KEY)
        if not isinstance(metadata, dict):
            return None
        value = metadata.get("id")
        if not value:
            return None
        try:
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _float_from_payload(self, value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def _read_with_gate_observation(self, read: PlateRead) -> PlateRead:
        if self._should_preserve_supplied_gate_observation(read):
            return read

        observed_at = datetime.now(tz=UTC)
        detail: str | None = None
        try:
            gate = get_gate_controller("configured")
            state = coerce_gate_state(await gate.current_state()) or GateState.UNKNOWN
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
            "controller": "configured",
            "detail": detail,
        }
        return _plate_read_with_payload(read, raw_payload)

    def _should_preserve_supplied_gate_observation(self, read: PlateRead) -> bool:
        raw_payload = read.raw_payload or {}
        if isinstance(raw_payload.get(GATE_OBSERVATION_PAYLOAD_KEY), dict):
            return True
        return bool(
            read.source in {"simulator", "simulation_e2e"}
            and raw_payload.get(PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY) is True
            and isinstance(raw_payload.get(GATE_OBSERVATION_PAYLOAD_KEY), dict)
        )

    async def _process_queue(self) -> None:
        while not self._stop_event.is_set():
            read_for_retry: PlateRead | None = None
            try:
                self._last_heartbeat_at = datetime.now(tz=UTC)
                self._runtime = await get_runtime_config()
                self._timezone = ZoneInfo(self._runtime.site_timezone)
                try:
                    read = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    read = None

                if read is not None:
                    read_for_retry = read
                    if not await self._claim_lpr_ingest_for_processing(read):
                        read_for_retry = None
                        continue
                    if await is_maintenance_mode_active():
                        self._clear_pending_reads()
                        await self._mark_lpr_ingest_terminal(
                            read,
                            status=LPR_INGEST_STATUS_SKIPPED,
                            detail="maintenance_mode_active",
                        )
                        await self._publish_terminal_read(
                            read,
                            "plate_read.skipped",
                            reason="maintenance_mode_active",
                        )
                        read_for_retry = None
                        continue
                    read = await self._read_with_gate_observation(read)
                    read_for_retry = read
                    await self._handle_queued_read(read)
                    self._last_processed_at = datetime.now(tz=UTC)
                    if self._processing_attempt(read) > 0:
                        await self._publish_terminal_read(
                            read,
                            "plate_read.recovered",
                            reason="retry_succeeded",
                        )
                    read_for_retry = None

                await self._flush_expired_windows()
                if read is None and self._queue.empty():
                    await self._enqueue_pending_lpr_ingest_rows()
                self._consecutive_failures = 0
            except Exception as exc:
                await self._handle_worker_iteration_failure(exc, read_for_retry=read_for_retry)

    def _handle_worker_done(self, task: asyncio.Task) -> None:
        if self._stop_event.is_set() or task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            return
        self._record_worker_failure(exc)
        logger.error(
            "access_event_worker_stopped_unexpectedly",
            extra={"error": self._safe_exception_detail(exc)},
        )
        try:
            asyncio.create_task(
                event_bus.publish(
                    "access_event.worker_failed",
                    {
                        "status": "down",
                        "queue_depth": self._queue.qsize(),
                        "pending_windows": len(self._pending),
                        "error": self._safe_exception_detail(exc),
                    },
                )
            )
        except RuntimeError:
            logger.warning(
                "access_event_worker_failure_event_not_published",
                extra={"error": self._safe_exception_detail(exc)},
            )

    async def _handle_worker_iteration_failure(
        self,
        exc: Exception,
        *,
        read_for_retry: PlateRead | None,
    ) -> None:
        self._record_worker_failure(exc)
        payload = {
            "status": "degraded",
            "queue_depth": self._queue.qsize(),
            "pending_windows": len(self._pending),
            "consecutive_failures": self._consecutive_failures,
            "error": self._safe_exception_detail(exc),
        }
        if read_for_retry is not None:
            payload.update(self._read_failure_payload(read_for_retry, exc, stage="worker"))
        logger.error("access_event_worker_iteration_failed", extra=payload)
        await event_bus.publish("access_event.worker_degraded", payload)
        if read_for_retry is not None:
            await self._retry_or_fail_read(read_for_retry, exc, stage="worker")
            return
        await self._sleep_until_retry(self._worker_backoff_seconds())

    def _record_worker_failure(self, exc: BaseException) -> None:
        self._total_failures += 1
        self._consecutive_failures += 1
        self._last_error = self._safe_exception_detail(exc)
        self._last_error_at = datetime.now(tz=UTC)

    def _worker_backoff_seconds(self) -> float:
        index = min(
            max(self._consecutive_failures - 1, 0),
            len(PLATE_READ_RETRY_BACKOFF_SECONDS) - 1,
        )
        return PLATE_READ_RETRY_BACKOFF_SECONDS[index]

    async def _retry_or_fail_read(
        self,
        read: PlateRead,
        exc: Exception,
        *,
        stage: str,
        reason: str | None = None,
        candidate_count: int | None = None,
    ) -> bool:
        attempt = self._processing_attempt(read) + 1
        payload = self._read_failure_payload(
            read,
            exc,
            stage=stage,
            attempt=attempt,
            reason=reason,
            candidate_count=candidate_count,
        )
        if self._stop_event.is_set() or attempt >= MAX_PLATE_READ_PROCESSING_ATTEMPTS:
            logger.error("plate_read_processing_failed_permanently", extra=payload)
            await self._mark_lpr_ingest_terminal(
                read,
                status=LPR_INGEST_STATUS_FAILED,
                detail=self._safe_exception_detail(exc),
            )
            await event_bus.publish("plate_read.failed", payload)
            return False

        backoff_seconds = PLATE_READ_RETRY_BACKOFF_SECONDS[
            min(attempt - 1, len(PLATE_READ_RETRY_BACKOFF_SECONDS) - 1)
        ]
        await event_bus.publish(
            "plate_read.retrying",
            {**payload, "next_retry_seconds": backoff_seconds},
        )
        await self._sleep_until_retry(backoff_seconds)
        if self._stop_event.is_set():
            return False
        await self._queue.put(self._read_with_processing_attempt(read, attempt))
        return True

    async def _publish_terminal_read(
        self,
        read: PlateRead,
        event_type: str,
        *,
        reason: str,
        candidate_count: int | None = None,
    ) -> None:
        payload = {
            "registration_number": read.registration_number,
            "detected_registration_number": _detected_registration_number(read),
            "source": read.source,
            "captured_at": read.captured_at.isoformat(),
            "attempt": self._processing_attempt(read),
            "reason": reason,
        }
        if candidate_count is not None:
            payload["candidate_count"] = candidate_count
        await event_bus.publish(event_type, payload)

    def _read_failure_payload(
        self,
        read: PlateRead,
        exc: Exception,
        *,
        stage: str,
        attempt: int | None = None,
        reason: str | None = None,
        candidate_count: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "registration_number": read.registration_number,
            "detected_registration_number": _detected_registration_number(read),
            "source": read.source,
            "captured_at": read.captured_at.isoformat(),
            "attempt": self._processing_attempt(read) if attempt is None else attempt,
            "max_attempts": MAX_PLATE_READ_PROCESSING_ATTEMPTS,
            "stage": stage,
            "error": self._safe_exception_detail(exc),
        }
        if reason:
            payload["reason"] = reason
        if candidate_count is not None:
            payload["candidate_count"] = candidate_count
        return payload

    def _processing_attempt(self, read: PlateRead) -> int:
        try:
            return max(0, int((read.raw_payload or {}).get(PROCESSING_ATTEMPT_PAYLOAD_KEY) or 0))
        except (TypeError, ValueError):
            return 0

    def _read_with_processing_attempt(self, read: PlateRead, attempt: int) -> PlateRead:
        raw_payload = dict(read.raw_payload or {})
        raw_payload[PROCESSING_ATTEMPT_PAYLOAD_KEY] = attempt
        return _plate_read_with_payload(read, raw_payload)

    async def _sleep_until_retry(self, seconds: float) -> None:
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    def _safe_exception_detail(self, exc: BaseException) -> str:
        detail = str(exc).replace("\n", " ").strip()
        if len(detail) > 240:
            detail = f"{detail[:237]}..."
        return f"{exc.__class__.__name__}: {detail}" if detail else exc.__class__.__name__

    async def _handle_queued_read(self, read: PlateRead) -> None:
        read = await self._read_with_known_vehicle_match(read)
        read = await self._read_with_gate_malfunction_context(read)
        gate_malfunction = _gate_malfunction_from_read(read)
        known_vehicle_match = _known_vehicle_plate_match_from_read(read)
        if gate_malfunction and not known_vehicle_match:
            await self._ignore_unknown_gate_malfunction_read(read)
            return

        if await self._suppress_by_live_lpr_zone_filter(read):
            return

        if not gate_malfunction:
            exact_suppression_reason = await self._exact_resolution_suppression_reason(read)
            if exact_suppression_reason:
                await self._publish_suppressed_read(read, reason=exact_suppression_reason)
                return

            if not known_vehicle_match:
                read = await self._read_with_external_admission_match(read)

            if not _external_admission_from_read(read):
                vehicle_session_suppression = await self._movement_sessions.suppression_for_read(
                    read,
                    runtime=self._runtime,
                )
                if vehicle_session_suppression:
                    await self._movement_sessions.annotate_suppressed_read(
                        read,
                        vehicle_session_suppression,
                        runtime=self._runtime,
                    )
                    await self._publish_suppressed_read(read, reason=vehicle_session_suppression.reason)
                    return

        external_admission = _external_admission_from_read(read)
        if not _known_vehicle_plate_match_from_read(read) and not external_admission:
            read = await self._read_with_visitor_pass_departure_match(read)
        if self._suppress_after_visitor_pass_resolution(read):
            await self._publish_suppressed_read(read, reason="visitor_pass_plate_already_resolved_in_debounce_window")
            return

        window = self._add_to_debounce_window(read)
        if _is_exact_known_vehicle_plate_match(read):
            window = self._pop_exact_known_plate_window(window)
            await self._finalize_window_or_fail(window, reason="exact_known_vehicle_plate")
        elif external_admission:
            window = self._pop_related_read_window(window, read)
            await self._finalize_window_or_fail(window, reason="external_unknown_vehicle_admission")
        elif _is_visitor_pass_plate_match(read):
            window = self._pop_related_read_window(window, read)
            if await self._finalize_window_or_fail(window, reason="visitor_pass_departure_plate"):
                self._remember_visitor_pass_resolution(window, read)

    async def _suppress_by_live_lpr_zone_filter(self, read: PlateRead) -> bool:
        mode = getattr(self._runtime, "lpr_zone_filter_mode", "shadow") if self._runtime else "shadow"
        try:
            decision = evaluate_lpr_zone_filter_for_read(read, mode=mode)
        except Exception as exc:
            logger.warning(
                "lpr_zone_filter_evaluation_failed",
                extra={
                    "registration_number": read.registration_number,
                    "source": read.source,
                    "error": self._safe_exception_detail(exc),
                },
            )
            return False
        if not decision.should_suppress_live:
            return False
        try:
            await get_lpr_zone_shadow_service().record_decision(
                read,
                access_event_id=None,
                actual_decision=None,
                actual_direction=None,
                actual_outcome=decision.actual_outcome,
                mode=mode,
                decision_override=decision,
            )
        except Exception as exc:
            logger.warning(
                "lpr_zone_filter_live_suppression_log_failed",
                extra={
                    "registration_number": read.registration_number,
                    "source": read.source,
                    "error": self._safe_exception_detail(exc),
                },
            )
        await self._publish_suppressed_read(read, reason=LPR_ZONE_FILTER_SUPPRESSION_REASON)
        return True

    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        if event.type == "maintenance_mode.changed" and event.payload.get("is_active") is True:
            self._clear_pending_reads()

    async def _handle_local_realtime_event(self, event: RealtimeEvent) -> None:
        if event.type != "gate.state_changed":
            return
        state = coerce_gate_state(event.payload.get("state"))
        previous_state = coerce_gate_state(event.payload.get("previous_state"))
        if state not in DEPARTURE_GATE_STATES:
            return
        if previous_state in DEPARTURE_GATE_STATES:
            return
        await self._record_external_gate_open_admission(event)

    async def _record_external_gate_open_admission(self, event: RealtimeEvent) -> None:
        if await is_maintenance_mode_active():
            return
        observed_at = (
            datetime_from_payload(event.payload.get("state_changed_at"))
            or datetime_from_payload(event.created_at)
            or datetime.now(tz=UTC)
        )
        runtime = self._runtime or await get_runtime_config()
        result: tuple[AccessEvent, list[Anomaly], dict[str, Any]] | None = None
        async with AsyncSessionLocal() as session:
            if await self._recent_iacs_gate_open_command(session, observed_at):
                return
            match = await self._movement_sessions.external_admission_candidate_for_gate_open(
                session,
                opened_at=observed_at,
                runtime=runtime,
            )
            if not match:
                return
            result = await self._persist_external_gate_open_admission(
                session,
                match,
                observed_at=observed_at,
                gate_payload=event.payload,
                runtime=runtime,
            )
            if result is None:
                return
            await session.commit()

        access_event, anomalies, realtime_payload = result
        await event_bus.publish("access_event.finalized", realtime_payload)
        for anomaly in anomalies:
            await get_notification_service().notify(
                NotificationContext(
                    event_type=anomaly.anomaly_type.value,
                    subject=access_event.registration_number,
                    severity=anomaly.severity.value,
                    facts=notification_facts(
                        access_event,
                        None,
                        None,
                        anomaly.message,
                    ),
                )
            )

    async def _recent_iacs_gate_open_command(self, session: AsyncSession, observed_at: datetime) -> GateCommandRecord | None:
        command_window_start = observed_at - timedelta(seconds=90)
        command_window_end = observed_at + timedelta(seconds=15)
        return await session.scalar(
            select(GateCommandRecord)
            .where(
                GateCommandRecord.action == "open",
                GateCommandRecord.accepted.is_(True),
                GateCommandRecord.started_at.is_not(None),
                GateCommandRecord.started_at <= command_window_end,
                or_(
                    GateCommandRecord.started_at >= command_window_start,
                    GateCommandRecord.completed_at >= command_window_start,
                ),
            )
            .order_by(GateCommandRecord.started_at.desc(), GateCommandRecord.created_at.desc())
            .limit(1)
        )

    async def _persist_external_gate_open_admission(
        self,
        session: AsyncSession,
        match: ExternalVehicleSessionMatch,
        *,
        observed_at: datetime,
        gate_payload: dict[str, Any],
        runtime: RuntimeConfig,
    ) -> tuple[AccessEvent, list[Anomaly], dict[str, Any]] | None:
        session_id = str(getattr(match.session, "id", "") or "")
        movement_saga = await self._movement_ledger.create_movement_saga(
            session,
            idempotency_key=f"movement:external-admission:arrival:{session_id}",
            source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            occurred_at=observed_at,
            registration_number=str(getattr(match.session, "registration_number", "") or match.access_event.registration_number),
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
            state=MovementSagaState.DIRECTION_RESOLVED,
            intent_payload={
                "source": EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
                "registration_number": str(
                    getattr(match.session, "registration_number", "") or match.access_event.registration_number
                ),
                "captured_at": observed_at.isoformat(),
                "allowed": True,
                "person_id": None,
                "vehicle_id": None,
                "external_admission": {
                    "mode": "arrival",
                    "source": EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
                    "original_denied_access_event_id": str(match.access_event.id),
                    "original_denied_movement_session_id": session_id,
                },
                "gate_observation": self._gate_observation_from_gate_event(gate_payload, observed_at),
            },
        )
        if movement_saga.access_event_id:
            return None

        external_admission = self._external_admission_payload(
            match,
            mode="arrival",
            source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            observed_at=observed_at,
            gate_observation=self._gate_observation_from_gate_event(gate_payload, observed_at),
            gate_payload=gate_payload,
        )
        read = self._external_gate_open_read(match, observed_at=observed_at, gate_payload=gate_payload, external_admission=external_admission)
        window = DebounceWindow(first_seen=observed_at, updated_at=observed_at, reads=[read])
        direction, direction_resolution = self._external_admission_direction_resolution(read, external_admission)
        schedule_evaluation = ScheduleEvaluation(
            allowed=True,
            source="external_admission_arrival",
            reason="Unknown vehicle was admitted by an external gate opening while Protect still showed it present.",
        )
        webhook_trace = self._webhook_trace_for_window(window)
        event = AccessEvent(
            vehicle=None,
            person_id=None,
            registration_number=read.registration_number,
            direction=direction,
            decision=AccessDecision.GRANTED,
            confidence=read.confidence,
            source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            occurred_at=observed_at,
            timing_classification=TimingClassification.UNKNOWN,
            raw_payload=self._access_event_raw_payload(
                window=window,
                read=read,
                schedule_evaluation=schedule_evaluation,
                direction_resolution=direction_resolution,
                vehicle_visual_detection=None,
                visitor_pass=None,
                visitor_pass_mode=None,
                external_admission=external_admission,
                trace_id=uuid.uuid4().hex,
                finalize_started_at=datetime.now(tz=UTC),
                webhook_trace=webhook_trace,
            ),
        )
        session.add(event)
        await session.flush()
        await self._movement_ledger.transition_movement_saga(
            session,
            movement_saga,
            MovementSagaState.COMPLETED,
            detail="external_gate_open_admission_recorded",
            access_event_id=event.id,
            gate_command_required=False,
            presence_committed=False,
            decision_payload=direction_resolution,
        )
        event.raw_payload = {
            **(event.raw_payload or {}),
            VEHICLE_SESSION_PAYLOAD_KEY: self._movement_sessions.initial_payload(
                window.reads,
                first_seen=window.first_seen,
                updated_at=window.updated_at,
                read=read,
                event=event,
            ),
        }
        await self._movement_sessions.remember_session_in_db(
            session,
            event,
            window.reads,
            first_seen=window.first_seen,
            updated_at=window.updated_at,
            read=read,
            movement_saga_id=movement_saga.id,
            runtime=runtime,
        )
        await self._movement_sessions.mark_external_session_superseded(
            session,
            match.session,
            reason=EXTERNAL_ADMISSION_ORIGINAL_SUPERSEDED_REASON,
            matched_by=match.matched_by,
            evidence=match.evidence,
            event_id=event.id,
            observed_at=observed_at,
        )
        await capture_access_event_snapshot(event)
        anomalies = await self._build_anomalies(
            session,
            event,
            None,
            None,
            True,
            external_admission=external_admission,
        )
        session.add_all(anomalies)
        event.raw_payload = self._raw_payload_with_movement_saga(
            event.raw_payload,
            state="completed",
            gate_command_required=False,
            presence_committed=False,
            movement_saga=movement_saga,
            detail="External gate opening recorded; no IACS hardware command was sent.",
        )
        realtime_payload = access_event_realtime_payload(
            event,
            anomaly_count=len(anomalies),
            visitor_pass=None,
            visitor_pass_mode=None,
        )
        saga_payload = (event.raw_payload or {}).get("movement_saga")
        if isinstance(saga_payload, dict):
            realtime_payload["movement_saga"] = saga_payload
        return event, anomalies, realtime_payload

    def _external_gate_open_read(
        self,
        match: ExternalVehicleSessionMatch,
        *,
        observed_at: datetime,
        gate_payload: dict[str, Any],
        external_admission: dict[str, Any],
    ) -> PlateRead:
        gate_observation = self._gate_observation_from_gate_event(gate_payload, observed_at)
        raw_payload = {
            GATE_OBSERVATION_PAYLOAD_KEY: gate_observation,
            EXTERNAL_ADMISSION_PAYLOAD_KEY: external_admission,
            WEBHOOK_TRACE_PAYLOAD_KEY: {
                "source": EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
                "registration_number": str(
                    getattr(match.session, "registration_number", "") or match.access_event.registration_number
                ),
                "captured_at": observed_at.isoformat(),
                "received_at": observed_at.isoformat(),
                "webhook_received_at": observed_at.isoformat(),
                "captured_to_webhook_ms": 0.0,
            },
            "gate_state_changed": {
                key: gate_payload.get(key)
                for key in (
                    "source",
                    "entity_id",
                    "device_key",
                    "name",
                    "state",
                    "raw_state",
                    "previous_state",
                    "state_changed_at",
                )
                if gate_payload.get(key) is not None
            },
        }
        return PlateRead(
            registration_number=str(
                getattr(match.session, "registration_number", "") or match.access_event.registration_number
            ),
            confidence=float(getattr(match.access_event, "confidence", None) or 1.0),
            source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            captured_at=observed_at,
            raw_payload=raw_payload,
        )

    def _gate_observation_from_gate_event(self, payload: dict[str, Any], observed_at: datetime) -> dict[str, Any]:
        state = coerce_gate_state(payload.get("state")) or GateState.UNKNOWN
        return {
            "state": state.value,
            "observed_at": observed_at.isoformat(),
            "controller": payload.get("source") or "gate_state_changed",
            "entity_id": payload.get("entity_id") or payload.get("device_key"),
            "detail": "Gate opened outside IACS while an unknown vehicle was still detected at the gate.",
        }

    def _clear_pending_reads(self) -> None:
        self._pending = []
        self._recent_visitor_pass_resolutions = []

    def _add_to_debounce_window(self, read: PlateRead) -> DebounceWindow:
        for window in self._pending:
            best = window.best_read
            threshold = self._runtime.lpr_similarity_threshold if self._runtime else settings.lpr_similarity_threshold
            if read.source == best.source and plates_are_similar(
                read.registration_number, best.registration_number, threshold
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
        best_match: dict[str, Any] | None = None
        best_rank: tuple[bool, float, int, str] | None = None
        for index, candidate in enumerate(_candidate_registration_numbers(read)):
            match = self._known_vehicle_plate_match(candidate, registrations, threshold)
            if not match:
                continue
            rank = (
                bool(match["exact"]),
                float(match["similarity"]),
                -index,
                str(match["registration_number"]),
            )
            if best_rank is None or rank > best_rank:
                best_match = match
                best_rank = rank

        if not best_match:
            return read

        raw_payload = dict(read.raw_payload or {})
        raw_payload[KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY] = best_match
        return _plate_read_with_payload(read, raw_payload, registration_number=str(best_match["registration_number"]))

    async def _active_vehicle_registrations(self) -> list[str]:
        async with AsyncSessionLocal() as session:
            registrations = (
                await session.scalars(
                    select(Vehicle.registration_number).where(Vehicle.is_active.is_(True))
                )
            ).all()
        return [str(registration) for registration in registrations]

    async def _read_with_gate_malfunction_context(self, read: PlateRead) -> PlateRead:
        gate_observation = gate_observation_from_read(read)
        gate_state = coerce_gate_state(gate_observation.get("state"))
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
        return _plate_read_with_payload(read, raw_payload)

    async def _read_with_external_admission_match(self, read: PlateRead) -> PlateRead:
        gate_state = coerce_gate_state(gate_observation_from_read(read).get("state"))
        if gate_state not in DEPARTURE_GATE_STATES:
            return read
        runtime = self._runtime or await get_runtime_config()
        async with AsyncSessionLocal() as session:
            departure = await self._movement_sessions.external_departure_candidate_for_read(
                session,
                read,
                runtime=runtime,
            )
            if departure:
                return self._read_with_external_admission_payload(
                    read,
                    departure,
                    mode="departure",
                    source=EXTERNAL_ADMISSION_SOURCE_VEHICLE_SESSION,
                )
            if await self._recent_iacs_gate_open_command(session, read.captured_at):
                return read
            admission = await self._movement_sessions.external_admission_candidate_for_read(
                session,
                read,
                runtime=runtime,
            )
            if admission:
                return self._read_with_external_admission_payload(
                    read,
                    admission,
                    mode="arrival",
                    source=EXTERNAL_ADMISSION_SOURCE_LPR_OPEN_GATE,
                )
        return read

    def _read_with_external_admission_payload(
        self,
        read: PlateRead,
        match: ExternalVehicleSessionMatch,
        *,
        mode: str,
        source: str,
    ) -> PlateRead:
        raw_payload = dict(read.raw_payload or {})
        raw_payload[EXTERNAL_ADMISSION_PAYLOAD_KEY] = self._external_admission_payload(
            match,
            mode=mode,
            source=source,
            observed_at=read.captured_at,
            gate_observation=gate_observation_from_read(read),
        )
        registration_number = str(
            getattr(match.session, "registration_number", None)
            or read.registration_number
        )
        return _plate_read_with_payload(read, raw_payload, registration_number=registration_number)

    def _external_admission_payload(
        self,
        match: ExternalVehicleSessionMatch,
        *,
        mode: str,
        source: str,
        observed_at: datetime,
        gate_observation: dict[str, Any] | None = None,
        gate_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_row = match.session
        linked_event = match.access_event
        payload: dict[str, Any] = {
            "mode": mode,
            "source": source,
            "observed_at": observed_at.isoformat(),
            "registration_number": str(
                getattr(session_row, "registration_number", None)
                or getattr(linked_event, "registration_number", "")
                or ""
            ),
            "normalized_registration_number": str(
                getattr(session_row, "normalized_registration_number", "") or ""
            ),
            "matched_by": match.matched_by,
            "linked_access_event_id": str(linked_event.id),
            "linked_movement_session_id": str(getattr(session_row, "id", "")),
            "presence_evidence": presence_evidence_payload(match.evidence),
            "gate_observation": gate_observation,
        }
        if gate_payload:
            payload["gate_state_changed"] = {
                key: gate_payload.get(key)
                for key in (
                    "source",
                    "entity_id",
                    "device_key",
                    "name",
                    "state",
                    "raw_state",
                    "previous_state",
                    "state_changed_at",
                )
                if gate_payload.get(key) is not None
            }
        if mode == "arrival":
            payload["original_denied_access_event_id"] = str(linked_event.id)
            payload["original_denied_movement_session_id"] = str(getattr(session_row, "id", ""))
        elif mode == "departure":
            payload["external_admission_access_event_id"] = str(linked_event.id)
            payload["external_admission_movement_session_id"] = str(getattr(session_row, "id", ""))
        return payload

    def _external_admission_direction_resolution(
        self,
        read: PlateRead,
        external_admission: dict[str, Any],
    ) -> tuple[AccessDirection, dict[str, Any]]:
        mode = str(external_admission.get("mode") or "").strip().lower()
        direction = AccessDirection.EXIT if mode == "departure" else AccessDirection.ENTRY
        source = (
            "external_gate_open"
            if external_admission.get("source") == EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED
            else EXTERNAL_ADMISSION_SOURCE_VEHICLE_SESSION
            if mode == "departure"
            else "external_gate_open"
        )
        return direction, {
            "source": source,
            "direction": direction.value,
            "movement_state": "completed",
            "gate_observation": gate_observation_from_read(read),
            "external_admission": external_admission,
            "hardware_actions_suppressed": True,
            "reason": (
                "Unknown vehicle was admitted by an external gate opening while Protect still showed it present."
                if mode != "departure"
                else "Linked externally admitted unknown vehicle departure was recorded from the later plate read."
            ),
        }

    async def _ignore_unknown_gate_malfunction_read(self, read: PlateRead) -> None:
        gate_observation = gate_observation_from_read(read)
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
        await self._mark_lpr_ingest_terminal(
            read,
            status=LPR_INGEST_STATUS_SKIPPED,
            detail="gate_malfunction_unknown_vehicle",
        )
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
        plate = normalize_registration_number(read.registration_number)
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
        return _plate_read_with_payload(read, raw_payload, registration_number=plate)

    def _known_vehicle_plate_match(
        self,
        detected_registration_number: str,
        stored_registration_numbers: list[str],
        threshold: float,
    ) -> dict[str, Any] | None:
        detected = normalize_registration_number(detected_registration_number)
        if not detected:
            return None

        best_match: dict[str, Any] | None = None
        for stored_registration_number in stored_registration_numbers:
            stored_lookup = str(stored_registration_number).strip().upper().replace(" ", "")
            stored = normalize_registration_number(stored_lookup)
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

    def _window_overlaps_anchor_read(
        self,
        window: DebounceWindow,
        anchor_read: PlateRead,
        max_seconds: float,
    ) -> bool:
        anchor_at = anchor_read.captured_at
        return window.first_seen <= anchor_at <= window.first_seen + timedelta(seconds=max_seconds)

    async def _exact_resolution_suppression_reason(self, read: PlateRead) -> str | None:
        match = _known_vehicle_plate_match_from_read(read)
        try:
            async with AsyncSessionLocal() as session:
                rows = await self._movement_ledger.movement_sessions_for_exact_suppression(
                    session,
                    source=read.source,
                    captured_at=read.captured_at,
                )
        except (ProgrammingError, RuntimeError):
            logger.warning(
                "movement_session_exact_suppression_unavailable",
                extra={"registration_number": read.registration_number, "source": read.source},
            )
            return None
        decision = self._movement_suppression_fsm.classify_exact_plate_read(
            PlateReadMovementEvidence(
                source=read.source,
                registration_number=read.registration_number,
                captured_at=read.captured_at,
                gate_state=coerce_gate_state(gate_observation_from_read(read).get("state")),
                direction_hint=read_direction_hint(read),
                has_known_vehicle_match=bool(match),
            ),
            (
                ResolvedMovementWindow(
                    source=row.source,
                    registration_number=row.registration_number,
                    first_seen=row.started_at,
                    debounce_expires_at=row.debounce_expires_at or row.started_at,
                    gate_cycle_expires_at=row.gate_cycle_expires_at or row.debounce_expires_at or row.started_at,
                    direction=row.direction,
                    decision=row.decision,
                )
                for row in rows
            ),
        )
        return decision.reason

    def _remember_visitor_pass_resolution(self, window: DebounceWindow, anchor_read: PlateRead) -> None:
        max_seconds = self._runtime.lpr_debounce_max_seconds if self._runtime else settings.lpr_debounce_max_seconds
        self._recent_visitor_pass_resolutions.append(
            ResolvedPlateWindow(
                source=anchor_read.source,
                first_seen=window.first_seen,
                debounce_expires_at=window.first_seen + timedelta(seconds=max_seconds),
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
        saga = await self._record_suppressed_movement_read(read, reason=reason)
        await self._mark_lpr_ingest_succeeded(read, movement_saga_id=saga.id)
        await event_bus.publish(
            "plate_read.suppressed",
            {
                "registration_number": read.registration_number,
                "detected_registration_number": match.get("detected_registration_number") or read.registration_number,
                "source": read.source,
                "reason": reason,
            },
        )

    async def _record_suppressed_movement_read(self, read: PlateRead, *, reason: str) -> MovementSagaRecord:
        async with AsyncSessionLocal() as session:
            saga = await self._movement_ledger.create_movement_saga(
                session,
                idempotency_key=f"movement-suppressed:{self._movement_saga_idempotency_key(read)}:{reason}",
                source=read.source,
                occurred_at=read.captured_at,
                registration_number=_detected_registration_number(read),
                state=MovementSagaState.SUPPRESSED,
                intent_payload={
                    "source": read.source,
                    "captured_at": read.captured_at.isoformat(),
                    "registration_number": read.registration_number,
                    "detected_registration_number": _detected_registration_number(read),
                    "confidence": read.confidence,
                },
                decision_payload={"suppression_reason": reason},
            )
            await self._movement_ledger.transition_movement_saga(
                session,
                saga,
                MovementSagaState.SUPPRESSED,
                detail=reason,
                reconciliation_required=False,
            )
            await session.commit()
            return saga

    async def _flush_expired_windows(self) -> None:
        if await is_maintenance_mode_active():
            self._clear_pending_reads()
            return None
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
            await self._finalize_window_or_fail(window, reason="debounce_window_expired")

    async def _flush_all_pending(self) -> None:
        if await is_maintenance_mode_active():
            self._clear_pending_reads()
            return None
        pending = self._pending
        self._pending = []
        for window in pending:
            await self._finalize_window_or_fail(window, reason="service_stopping")

    async def _handle_finalize_failure(
        self,
        window: DebounceWindow,
        exc: Exception,
        *,
        reason: str,
    ) -> None:
        payload = {
            "registration_number": window.best_read.registration_number,
            "detected_registration_number": _detected_registration_number(window.best_read),
            "source": window.best_read.source,
            "candidate_count": len(window.reads),
            "reason": reason,
            "error": self._safe_exception_detail(exc),
        }
        next_attempt = self._processing_attempt(window.best_read) + 1
        await event_bus.publish(
            "access_event.finalize_failed",
            {
                **payload,
                "will_retry": (
                    not self._stop_event.is_set()
                    and next_attempt < MAX_PLATE_READ_PROCESSING_ATTEMPTS
                ),
                "attempt": next_attempt,
                "max_attempts": MAX_PLATE_READ_PROCESSING_ATTEMPTS,
            },
        )
        await self._retry_or_fail_read(
            window.best_read,
            exc,
            stage="finalize",
            reason=reason,
            candidate_count=len(window.reads),
        )

    async def _finalize_window_or_fail(self, window: DebounceWindow, *, reason: str) -> bool:
        try:
            await self._finalize_window(window)
            return True
        except Exception as exc:
            logger.exception(
                "access_event_finalize_failed",
                extra={
                    "candidate_count": len(window.reads),
                    "best_registration_number": window.best_read.registration_number,
                    "reason": reason,
                },
            )
            await self._handle_finalize_failure(window, exc, reason=reason)
            return False

    async def _finalize_window(self, window: DebounceWindow) -> None:
        if await is_maintenance_mode_active():
            self._clear_pending_reads()
            return
        read = window.best_read
        external_admission = _external_admission_from_read(read)
        direction_read = read if _is_visitor_pass_plate_match(read) or external_admission else window.first_read
        finalize_started_at = datetime.now(tz=UTC)
        webhook_trace = self._webhook_trace_for_window(window)
        webhook_received_at = (
            datetime_from_payload(webhook_trace.get("received_at"))
            or datetime_from_payload(webhook_trace.get("captured_at"))
            or window.first_seen
        )
        captured_to_webhook_ms = self._float_from_payload(webhook_trace.get("captured_to_webhook_ms"))
        if captured_to_webhook_ms is None:
            captured_to_webhook_ms = _datetime_delta_ms(webhook_received_at, window.first_read.captured_at)
        webhook_to_finalize_ms = _datetime_delta_ms(finalize_started_at, webhook_received_at)
        logger.info(
            "plate_read_finalize_started",
            extra={
                "registration_number": read.registration_number,
                "source": read.source,
                "candidate_count": len(window.reads),
                "first_seen": window.first_seen.isoformat(),
                "updated_at": window.updated_at.isoformat(),
                "finalize_started_at": finalize_started_at.isoformat(),
                "first_seen_to_finalize_ms": _datetime_delta_ms(finalize_started_at, window.first_seen),
                "last_read_to_finalize_ms": _datetime_delta_ms(finalize_started_at, window.updated_at),
                "captured_to_webhook_ms": captured_to_webhook_ms,
                "webhook_to_finalize_ms": webhook_to_finalize_ms,
                "exact_known_vehicle": _is_exact_known_vehicle_plate_match(read),
                "visitor_pass_match": _is_visitor_pass_plate_match(read) is not None,
                "external_admission_mode": external_admission.get("mode") if external_admission else None,
            },
        )
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
                "webhook_received_at": webhook_received_at.isoformat(),
                "captured_to_webhook_ms": captured_to_webhook_ms,
                "webhook_to_finalize_ms": webhook_to_finalize_ms,
                WEBHOOK_TRACE_PAYLOAD_KEY: webhook_trace,
            },
        )
        trace.record_span(
            "Camera Capture to Webhook Receipt",
            started_at=window.first_read.captured_at,
            ended_at=webhook_received_at,
            attributes={
                "source": window.first_read.source,
                "captured_to_webhook_ms": captured_to_webhook_ms,
            },
            output_payload={
                "registration_number": window.first_read.registration_number,
                "confidence": window.first_read.confidence,
                "captured_at": window.first_read.captured_at.isoformat(),
                "received_at": webhook_received_at.isoformat(),
                "captured_to_webhook_ms": captured_to_webhook_ms,
            },
        )
        trace.record_span(
            "Webhook Receipt to Debounce Finalization",
            started_at=webhook_received_at,
            ended_at=finalize_started_at,
            attributes={
                "candidate_count": len(window.reads),
                "selected_registration_number": read.registration_number,
                "selected_confidence": read.confidence,
                "first_seen": window.first_seen.isoformat(),
                "updated_at": window.updated_at.isoformat(),
                "webhook_to_finalize_ms": webhook_to_finalize_ms,
            },
            output_payload={
                "candidates": [
                    {
                        "registration_number": item.registration_number,
                        "detected_registration_number": _detected_registration_number(item),
                        "confidence": item.confidence,
                        "captured_at": item.captured_at.isoformat(),
                        "candidate_registration_numbers": list(_candidate_registration_numbers(item)),
                        WEBHOOK_TRACE_PAYLOAD_KEY: self._webhook_trace_from_read(item),
                    }
                    for item in window.reads
                ],
            },
        )
        presence_updated = False
        gate_command_required = False
        gate_open_skipped = False
        gate_outcome: GateCommandOutcome | None = None
        movement_saga = None
        async with AsyncSessionLocal() as session:
            vehicle = await self._lookup_active_vehicle(session, read, trace)
            person = vehicle.owner if vehicle else None
            runtime = self._runtime or await get_runtime_config()
            identity_active = bool(vehicle and (not person or person.is_active))
            if external_admission:
                visitor_pass, visitor_pass_mode = None, None
            elif not vehicle:
                visitor_pass, visitor_pass_mode = await self._match_visitor_pass(session, read, direction_read, trace)
            else:
                visitor_pass, visitor_pass_mode = None, None
            if external_admission:
                mode = str(external_admission.get("mode") or "arrival")
                schedule_evaluation = ScheduleEvaluation(
                    allowed=True,
                    source=f"external_admission_{mode}",
                    reason=(
                        "Unknown vehicle was admitted externally and is being tracked as a vehicle session."
                        if mode == "arrival"
                        else "Externally admitted unknown vehicle departure was linked to the active vehicle session."
                    ),
                )
            else:
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
            allowed = bool(
                schedule_evaluation
                and schedule_evaluation.allowed
                and (identity_active or visitor_pass or external_admission)
            )
            direction_span = trace.start_span(
                "Direction Classification",
                attributes={
                    "allowed": allowed,
                    "gate_observation": gate_observation_from_read(direction_read),
                    "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                    "external_admission_mode": external_admission.get("mode") if external_admission else None,
                },
            )
            if external_admission:
                direction, direction_resolution = self._external_admission_direction_resolution(
                    direction_read,
                    external_admission,
                )
            else:
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
            movement_saga = await self._movement_ledger.create_movement_saga(
                session,
                idempotency_key=self._movement_saga_idempotency_key(read),
                source=read.source,
                occurred_at=read.captured_at,
                registration_number=read.registration_number,
                person_id=person.id if person else None,
                vehicle_id=vehicle.id if vehicle else None,
                direction=direction,
                decision=decision,
                state=MovementSagaState.DIRECTION_RESOLVED,
                intent_payload=self._movement_intent_payload(read, person, vehicle, allowed),
                decision_payload=direction_resolution,
            )
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
                    external_admission=external_admission,
                    trace_id=trace.trace_id,
                    finalize_started_at=finalize_started_at,
                    webhook_trace=webhook_trace,
                ),
            )
            session.add(event)
            await session.flush()
            await self._movement_ledger.transition_movement_saga(
                session,
                movement_saga,
                MovementSagaState.PHYSICAL_COMMAND_PENDING
                if decision == AccessDecision.GRANTED
                and direction == AccessDirection.ENTRY
                and self._automatic_open_allowed(direction_resolution)
                else MovementSagaState.COMPLETED,
                detail="access_event_persisted",
                access_event_id=event.id,
                gate_command_required=(
                    decision == AccessDecision.GRANTED
                    and direction == AccessDirection.ENTRY
                    and self._automatic_open_allowed(direction_resolution)
                ),
                presence_committed=False,
                decision_payload=direction_resolution,
            )
            event.raw_payload = {
                **(event.raw_payload or {}),
                VEHICLE_SESSION_PAYLOAD_KEY: self._movement_sessions.initial_payload(
                    window.reads,
                    first_seen=window.first_seen,
                    updated_at=window.updated_at,
                    read=read,
                    event=event,
                ),
            }
            await capture_access_event_snapshot(event, trace=trace)

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
            if external_admission:
                await self._mark_external_admission_linked_session(
                    session,
                    external_admission,
                    event_id=event.id,
                    observed_at=read.captured_at,
                )

            anomalies = await self._build_anomalies(
                session,
                event,
                person,
                vehicle,
                allowed,
                visitor_pass=visitor_pass,
                external_admission=external_admission,
            )
            session.add_all(anomalies)

            automatic_entry = (
                decision == AccessDecision.GRANTED
                and direction == AccessDirection.ENTRY
            )
            gate_command_required = automatic_entry and self._automatic_open_allowed(direction_resolution)
            gate_open_skipped = automatic_entry and not gate_command_required

            if allowed and person and not gate_command_required:
                presence_updated = await self._update_presence(session, person, event)

            event.raw_payload = self._raw_payload_with_movement_saga(
                event.raw_payload,
                state=(
                    "physical_command_pending"
                    if gate_command_required
                    else "completed"
                    if not (allowed and person) or presence_updated
                    else "presence_stale_skipped"
                ),
                gate_command_required=gate_command_required,
                presence_committed=presence_updated,
                movement_saga=movement_saga,
                detail=None if gate_command_required else "No blocking physical gate command is pending.",
            )
            if not gate_command_required:
                await self._movement_ledger.transition_movement_saga(
                    session,
                    movement_saga,
                    MovementSagaState.COMPLETED,
                    detail="no_blocking_gate_command",
                    presence_committed=presence_updated,
                    gate_command_required=False,
                )

            if visitor_pass:
                await session.flush()
                await session.refresh(visitor_pass)
                visitor_pass_realtime_payload = serialize_visitor_pass(visitor_pass)
            else:
                visitor_pass_realtime_payload = None
            access_realtime_payload = access_event_realtime_payload(
                event,
                anomaly_count=len(anomalies),
                visitor_pass=visitor_pass,
                visitor_pass_mode=visitor_pass_mode,
            )
            await self._lpr_ingest_repo().mark_ids_succeeded_in_session(
                session,
                [
                    ingest_id
                    for item in window.reads
                    if (ingest_id := self._lpr_ingest_event_id_from_read(item))
                ],
                access_event_id=event.id,
                movement_saga_id=movement_saga.id if movement_saga else None,
            )

            await session.commit()
            await self._record_lpr_zone_shadow_decision(
                read,
                event=event,
                decision=decision,
                direction=direction,
                person=person,
                vehicle=vehicle,
                visitor_pass=visitor_pass,
            )

        if gate_command_required:
            gate_outcome = await open_gate_for_access_event(
                event,
                person,
                open_garage_doors=True,
                trace=trace,
                dvla_enrichment=dvla_enrichment,
                movement_saga_id=str(movement_saga.id) if movement_saga else None,
            )
            saga_state = (
                "physical_command_accepted"
                if gate_outcome.accepted
                else "physical_command_failed"
            )
            async with AsyncSessionLocal() as session:
                persisted_event = await session.get(AccessEvent, event.id)
                if persisted_event:
                    persisted_saga = await session.merge(movement_saga) if movement_saga else None
                    if gate_outcome.accepted and gate_outcome.requires_reconciliation:
                        saga_state = "physical_command_accepted_pending_reconciliation"
                    elif (
                        gate_outcome.accepted
                        and allowed
                        and person
                    ):
                        presence_updated = await self._update_presence(session, person, persisted_event)
                        saga_state = "presence_committed"
                        if not presence_updated:
                            saga_state = "physical_command_accepted_presence_stale_skipped"
                    if persisted_saga:
                        await self._movement_ledger.transition_movement_saga(
                            session,
                            persisted_saga,
                            MovementSagaState.RECONCILIATION_REQUIRED
                            if gate_outcome.requires_reconciliation
                            else MovementSagaState.COMPLETED
                            if gate_outcome.accepted
                            else MovementSagaState.FAILED,
                            detail=gate_outcome.detail,
                            presence_committed=presence_updated,
                            reconciliation_required=gate_outcome.requires_reconciliation,
                            failure_detail=None if gate_outcome.accepted else gate_outcome.detail,
                        )
                    persisted_event.raw_payload = self._raw_payload_with_movement_saga(
                        persisted_event.raw_payload,
                        state=saga_state,
                        gate_command_required=True,
                        presence_committed=presence_updated,
                        gate_outcome=gate_outcome,
                        movement_saga=persisted_saga or movement_saga,
                        detail=gate_outcome.detail,
                    )
                    await session.commit()
                    event.raw_payload = persisted_event.raw_payload
                else:
                    logger.error(
                        "access_event_missing_for_movement_saga_update",
                        extra={"event_id": str(event.id), "registration_number": event.registration_number},
                    )
        elif gate_open_skipped:
            await publish_gate_open_skipped(event, direction_resolution, person)

        if not (gate_command_required and gate_outcome and not gate_outcome.accepted):
            await self._movement_sessions.remember_session(
                event,
                window.reads,
                first_seen=window.first_seen,
                updated_at=window.updated_at,
                read=read,
                movement_saga_id=movement_saga.id if movement_saga else None,
                runtime=self._runtime,
            )
        if presence_updated and person:
            try:
                await apply_person_presence_input_boolean_actions(
                    person,
                    event,
                    source="access_event_presence_commit",
                )
            except Exception as exc:
                logger.warning(
                    "person_presence_input_boolean_unhandled_failure",
                    extra={
                        "event_id": str(event.id),
                        "person_id": str(person.id),
                        "error": str(exc),
                    },
                )
        persistence_span.finish(
            output_payload={
                "event_id": str(event.id),
                "anomaly_count": len(anomalies),
                "presence_updated": presence_updated,
                "gate_command_required": gate_command_required,
                "gate_command_accepted": gate_outcome.accepted if gate_outcome else None,
                "snapshot_path": event.snapshot_path,
                "snapshot_bytes": event.snapshot_bytes,
            }
        )
        saga_payload = (event.raw_payload or {}).get("movement_saga")
        if isinstance(saga_payload, dict):
            access_realtime_payload["movement_saga"] = saga_payload

        await event_bus.publish(
            "access_event.finalized",
            access_realtime_payload,
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
        if (
            decision == AccessDecision.GRANTED
            and direction == AccessDirection.ENTRY
            and gate_outcome
            and gate_outcome.accepted
            and person
        ):
            await get_notification_service().notify(
                NotificationContext(
                    event_type="authorized_entry",
                    subject=f"{person.display_name} arrived at the gate",
                    severity=AnomalySeverity.INFO.value,
                    facts=notification_facts(
                        event,
                        person,
                        vehicle,
                        authorized_entry_message(person, vehicle),
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
                    facts=notification_facts(
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
                "external_admission_mode": external_admission.get("mode") if external_admission else None,
                "external_admission_source": external_admission.get("source") if external_admission else None,
            },
        )
    async def _record_lpr_zone_shadow_decision(
        self,
        read: PlateRead,
        *,
        event: AccessEvent,
        decision: AccessDecision,
        direction: AccessDirection,
        person: Person | None,
        vehicle: Vehicle | None,
        visitor_pass: VisitorPass | None,
    ) -> None:
        try:
            await get_lpr_zone_shadow_service().record_decision(
                read,
                access_event_id=event.id,
                actual_decision=decision.value,
                actual_direction=direction.value,
                actual_outcome=None,
                person_id=person.id if person else None,
                vehicle_id=vehicle.id if vehicle else None,
                visitor_pass_id=visitor_pass.id if visitor_pass else None,
                mode=getattr(self._runtime, "lpr_zone_filter_mode", "shadow") if self._runtime else "shadow",
            )
        except Exception as exc:
            logger.warning(
                "lpr_zone_shadow_record_failed",
                extra={
                    "event_id": str(event.id),
                    "registration_number": read.registration_number,
                    "error": self._safe_exception_detail(exc),
                },
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
                "vehicle": vehicle_display_name(vehicle, read.registration_number) if vehicle else None,
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
        finalize_started_at: datetime,
        webhook_trace: dict[str, Any],
        external_admission: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "best": read.raw_payload,
            WEBHOOK_TRACE_PAYLOAD_KEY: webhook_trace,
            "schedule": self._schedule_evaluation_payload(schedule_evaluation),
            "debounce": {
                "candidate_count": len(window.reads),
                "first_seen": window.first_seen.isoformat(),
                "updated_at": window.updated_at.isoformat(),
                "finalize_started_at": finalize_started_at.isoformat(),
                "candidates": [
                    {
                        "registration_number": item.registration_number,
                        "detected_registration_number": _detected_registration_number(item),
                        "confidence": item.confidence,
                        "captured_at": item.captured_at.isoformat(),
                        "candidate_registration_numbers": list(_candidate_registration_numbers(item)),
                        "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(item),
                        "visitor_pass_plate_match": _visitor_pass_plate_match_from_read(item),
                        WEBHOOK_TRACE_PAYLOAD_KEY: self._webhook_trace_from_read(item),
                    }
                    for item in window.reads
                ],
            },
            "direction_resolution": direction_resolution,
            VEHICLE_VISUAL_DETECTION_PAYLOAD_KEY: vehicle_visual_detection,
            VISITOR_PASS_PAYLOAD_KEY: self._visitor_pass_payload(visitor_pass, visitor_pass_mode),
            EXTERNAL_ADMISSION_PAYLOAD_KEY: external_admission,
            "telemetry": {"trace_id": trace_id},
        }

    def _movement_saga_idempotency_key(self, read: PlateRead) -> str:
        return f"movement:{read.source}:{read.registration_number}:{read.captured_at.isoformat()}"

    def _movement_intent_payload(
        self,
        read: PlateRead,
        person: Person | None,
        vehicle: Vehicle | None,
        allowed: bool,
    ) -> dict[str, Any]:
        explicit_direction = explicit_direction_from_read(read)
        return {
            "source": read.source,
            "captured_at": read.captured_at.isoformat(),
            "registration_number": read.registration_number,
            "allowed": allowed,
            "person_id": str(person.id) if person else None,
            "vehicle_id": str(vehicle.id) if vehicle else None,
            "gate_observation": gate_observation_from_read(read),
            "explicit_direction": explicit_direction.value if explicit_direction else None,
            "known_vehicle_plate_match": _known_vehicle_plate_match_from_read(read),
            "visitor_pass_plate_match": _visitor_pass_plate_match_from_read(read),
            "external_admission": _external_admission_from_read(read),
            "gate_malfunction": _gate_malfunction_from_read(read),
        }

    def _raw_payload_with_movement_saga(
        self,
        raw_payload: dict[str, Any] | None,
        *,
        state: str,
        gate_command_required: bool,
        presence_committed: bool,
        gate_outcome: GateCommandOutcome | None = None,
        movement_saga: Any | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(raw_payload or {})
        summary = movement_saga_summary(movement_saga)
        payload["movement_saga"] = {
            **(summary or {}),
            "state": state,
            "gate_command_required": gate_command_required,
            "presence_committed": presence_committed,
            "detail": detail,
            "gate": gate_outcome.as_payload() if gate_outcome else None,
        }
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
        gate_state = coerce_gate_state(gate_observation.get("state")) if isinstance(gate_observation, dict) else GateState.UNKNOWN
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
            "fuel_type": getattr(vehicle, "fuel_type", None),
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
        vehicle.fuel_type = normalized.fuel_type
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
                    facts=notification_facts(
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
                    facts=notification_facts(
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
        gate_observation = gate_observation_from_read(read)
        gate_state = coerce_gate_state(gate_observation.get("state")) or GateState.UNKNOWN
        visitor_pass_match = _visitor_pass_plate_match_from_read(read)
        visitor_pass_departure = isinstance(visitor_pass_match, dict) and visitor_pass_match.get("kind") == "departure"
        gate_malfunction = _gate_malfunction_from_read(read)
        previous_event: AccessEvent | None = None
        previous_event_payload: dict[str, Any] = {}
        if gate_malfunction and (person or vehicle):
            previous_event = await self._latest_live_person_or_vehicle_event(
                session,
                person=person,
                vehicle=vehicle,
                before=read.captured_at,
            )
            match_scope = self._previous_event_match_scope(previous_event, person=person, vehicle=vehicle)
            previous_event_payload = {
                "previous_live_match_scope": match_scope,
                "previous_live_event_id": str(previous_event.id) if previous_event else None,
                "previous_live_direction": previous_event.direction.value if previous_event else None,
                "previous_live_event_at": previous_event.occurred_at.isoformat() if previous_event else None,
            }

        presence_state = await self._presence_state_for_person(session, person) if person else None
        camera_tiebreaker: CameraTieBreakerEvidence | None = None
        while True:
            decision = self._movement_direction_fsm.resolve(
                MovementIntent(
                    source=read.source,
                    captured_at=read.captured_at,
                    registration_number=read.registration_number,
                    allowed=allowed,
                    person_known=person is not None,
                    vehicle_known=vehicle is not None,
                    gate_state=gate_state,
                    gate_observation=gate_observation,
                    presence_state=presence_state,
                    explicit_direction=explicit_direction_from_read(read),
                    visitor_pass_departure=visitor_pass_departure,
                    gate_malfunction=gate_malfunction,
                    previous_live_direction=previous_event.direction if previous_event else None,
                    previous_live_event_payload=previous_event_payload,
                    camera_tiebreaker=camera_tiebreaker,
                )
            )
            if decision.requires_external_evidence != "camera_tiebreaker" or not person:
                if visitor_pass_departure and isinstance(visitor_pass_match, dict):
                    decision.resolution["visitor_pass_id"] = visitor_pass_match.get("visitor_pass_id")
                return decision.direction, decision.resolution

            camera_decision = (
                await self._resolve_duplicate_arrival_with_camera(read, person, trace=trace)
                if trace
                else await self._resolve_duplicate_arrival_with_camera(read, person)
            )
            camera_tiebreaker = CameraTieBreakerEvidence(
                direction=coerce_access_direction(camera_decision.get("direction")),
                confidence=self._coerce_confidence(camera_decision.get("confidence")),
                clear=self._camera_tiebreaker_is_clear(camera_decision),
                payload=camera_decision,
            )

    async def _presence_state_for_person(
        self,
        session: AsyncSession,
        person: Person,
    ) -> PresenceState | None:
        presence = await session.get(Presence, person.id)
        return presence.state if presence else None

    def _camera_tiebreaker_is_clear(self, camera_decision: dict[str, Any]) -> bool:
        confidence = self._coerce_confidence(camera_decision.get("confidence"))
        return confidence is not None and confidence >= CAMERA_TIEBREAKER_MIN_CONFIDENCE

    async def _latest_live_person_or_vehicle_event(
        self,
        session: AsyncSession,
        *,
        person: Person | None,
        vehicle: Vehicle | None,
        before: datetime,
    ) -> AccessEvent | None:
        identity_filters = []
        if person and getattr(person, "id", None):
            identity_filters.append(AccessEvent.person_id == person.id)
        if vehicle and getattr(vehicle, "id", None):
            identity_filters.append(AccessEvent.vehicle_id == vehicle.id)
        if not identity_filters:
            return None
        rows = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    or_(*identity_filters),
                    AccessEvent.decision == AccessDecision.GRANTED,
                    AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
                    AccessEvent.occurred_at < before,
                )
                .order_by(AccessEvent.occurred_at.desc(), AccessEvent.id.desc())
                .limit(20)
            )
        ).all()
        return next((event for event in rows if not self._access_event_is_backfilled(event)), None)

    def _previous_event_match_scope(
        self,
        event: AccessEvent | None,
        *,
        person: Person | None,
        vehicle: Vehicle | None,
    ) -> str | None:
        if not event:
            return None
        scopes = []
        if person and getattr(event, "person_id", None) == getattr(person, "id", None):
            scopes.append("person")
        if vehicle and getattr(event, "vehicle_id", None) == getattr(vehicle, "id", None):
            scopes.append("vehicle")
        if scopes:
            return "+".join(scopes)
        if person and vehicle:
            return "person_or_vehicle"
        if person:
            return "person"
        if vehicle:
            return "vehicle"
        return None

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
        return coerce_gate_state(gate_observation.get("state")) == GateState.CLOSED

    def _visitor_pass_candidate_kind(self, read: PlateRead) -> str:
        visitor_pass_match = _visitor_pass_plate_match_from_read(read)
        if isinstance(visitor_pass_match, dict) and visitor_pass_match.get("kind") == "departure":
            return "departure"
        gate_observation = gate_observation_from_read(read)
        gate_state = coerce_gate_state(gate_observation.get("state"))
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

    async def _mark_external_admission_linked_session(
        self,
        session: AsyncSession,
        external_admission: dict[str, Any],
        *,
        event_id: uuid.UUID,
        observed_at: datetime,
    ) -> None:
        mode = str(external_admission.get("mode") or "").strip().lower()
        if mode == "departure":
            session_id = external_admission.get("external_admission_movement_session_id") or external_admission.get(
                "linked_movement_session_id"
            )
            reason = EXTERNAL_ADMISSION_DEPARTURE_REASON
        else:
            session_id = external_admission.get("original_denied_movement_session_id") or external_admission.get(
                "linked_movement_session_id"
            )
            reason = EXTERNAL_ADMISSION_ORIGINAL_SUPERSEDED_REASON
        if not session_id:
            return
        try:
            row_id = uuid.UUID(str(session_id))
        except (TypeError, ValueError):
            return
        row = await session.get(MovementSessionRecord, row_id)
        if not row:
            return
        await self._movement_sessions.mark_external_session_superseded(
            session,
            row,
            reason=reason,
            matched_by=str(external_admission.get("matched_by") or external_admission.get("source") or "external_admission"),
            evidence=external_admission.get("presence_evidence")
            if isinstance(external_admission.get("presence_evidence"), dict)
            else None,
            event_id=event_id,
            observed_at=observed_at,
        )

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
        external_admission: dict[str, Any] | None = None,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        if not vehicle:
            if visitor_pass:
                return anomalies
            external_mode = str((external_admission or {}).get("mode") or "").strip().lower()
            if external_mode == "departure":
                return anomalies
            anomaly = Anomaly(
                event=event,
                anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                severity=AnomalySeverity.WARNING,
                message=(
                    "Unauthorised Plate, Admitted Externally"
                    if external_mode == "arrival"
                    else "Unauthorised Plate, Access Denied"
                ),
                context={
                    key: value
                    for key, value in {
                        "registration_number": event.registration_number,
                        "snapshot": alert_snapshot_metadata_from_event(event),
                        "external_admission": external_admission if external_mode == "arrival" else None,
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
    ) -> bool:
        if not getattr(event, "person_id", None) and getattr(person, "id", None):
            event.person_id = person.id
        return await commit_presence_for_event(session, event, log_prefix="access_event")


def _datetime_delta_ms(end: datetime, start: datetime) -> float:
    return round(max(0.0, (end - start).total_seconds()) * 1000.0, 3)


@lru_cache
def get_access_event_service() -> AccessEventService:
    return AccessEventService()
