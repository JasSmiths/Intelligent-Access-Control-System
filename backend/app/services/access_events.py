from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    Anomaly,
    GateCommandRecord,
    LprIngestEvent,
    MovementSagaRecord,
    Vehicle,
    VisitorPass,
)
from app.models.enums import MovementSagaState, VisitorPassStatus, VisitorPassType
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.modules.notifications.base import NotificationContext
from app.modules.registry import UnsupportedModuleError, get_gate_controller
from app.services.access.enrichment import AccessEnrichment, independently
from app.services.access.execution import AccessExecution
from app.services.access.payloads import access_event_realtime_payload, notification_facts
from app.services.access.reads import (
    EXTERNAL_ADMISSION_PAYLOAD_KEY,
    EXTERNAL_ADMISSION_SOURCE_LPR_OPEN_GATE,
    EXTERNAL_ADMISSION_SOURCE_VEHICLE_SESSION,
    GATE_MALFUNCTION_PAYLOAD_KEY,
    GATE_OBSERVATION_PAYLOAD_KEY,
    INGEST_METADATA_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    LPR_INGEST_EVENT_PAYLOAD_KEY,
    MAX_PLATE_READ_PROCESSING_ATTEMPTS,
    PLATE_READ_RETRY_BACKOFF_SECONDS,
    PRESERVE_GATE_OBSERVATION_PAYLOAD_KEY,
    PROCESSING_ATTEMPT_PAYLOAD_KEY,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
    WEBHOOK_TRACE_PAYLOAD_KEY,
    WORKER_STALL_QUEUE_SECONDS,
    DebounceWindow,
    ResolvedPlateWindow,
    _external_admission_from_read,
    _external_admission_payload,
    _float_from_payload,
    _gate_malfunction_from_read,
    _is_exact_known_vehicle_plate_match,
    _is_visitor_pass_plate_match,
    _movement_saga_idempotency_key,
    _plate_read_with_payload,
    _visitor_pass_candidate_kind,
    _webhook_trace_for_window,
    _webhook_trace_from_read,
    lpr_ingest_id_from_read,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_malfunctions import active_stuck_open_malfunction_at
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
from app.services.movement.sessions import (
    DEPARTURE_GATE_STATES,
    ExternalVehicleSessionMatch,
    MovementSessionService,
    coerce_gate_state,
    datetime_from_payload,
    gate_observation_from_read,
    normalize_registration_number,
    plates_are_similar,
    read_direction_hint,
)
from app.services.movement.sessions import (
    candidate_registration_numbers as _candidate_registration_numbers,
)
from app.services.movement.sessions import (
    detected_registration_number as _detected_registration_number,
)
from app.services.movement.sessions import (
    known_vehicle_plate_match_from_read as _known_vehicle_plate_match_from_read,
)
from app.services.movement_fsm import (
    MovementSuppressionFSM,
    PlateReadMovementEvidence,
    ResolvedMovementWindow,
)
from app.services.movement_ledger import get_movement_ledger_repository
from app.services.notifications import get_notification_service
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_LPR,
    audit_log_event_payload,
    telemetry,
    write_audit_log,
)

logger = get_logger(__name__)

__all__ = [
    "AccessEventService",
    "get_access_event_service",
]

class AccessEventService:
    """Coordinates plate reads into access events.

    Hardware adapters produce normalized `PlateRead` objects. This service owns
    durable ingest, queue/debounce and suppression, then delegates evidence,
    policy, durable execution and optional enrichment to the access owners.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[PlateRead] = asyncio.Queue()
        self._pending: list[DebounceWindow] = []
        self._worker: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._runtime: RuntimeConfig | None = None
        self._recent_visitor_pass_resolutions: list[ResolvedPlateWindow] = []
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
        except Exception as exc:  # noqa: BLE001 - worker recovery or fail-closed evidence boundary
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
            _float_from_payload(existing.get("captured_to_webhook_ms"))
            if "captured_to_webhook_ms" in existing
            else _float_from_payload(ingest.get("captured_to_webhook_ms"))
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
        ingest_id = lpr_ingest_id_from_read(read)
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
        ingest_id = lpr_ingest_id_from_read(read)
        if ingest_id is None:
            return
        await self._lpr_ingest_repo().mark_succeeded(
            ingest_id,
            access_event_id=access_event_id,
            movement_saga_id=movement_saga_id,
        )

    async def _mark_lpr_ingest_terminal(self, read: PlateRead, *, status: str, detail: str) -> None:
        ingest_id = lpr_ingest_id_from_read(read)
        if ingest_id is None:
            return
        await self._lpr_ingest_repo().mark_terminal(ingest_id, status=status, detail=detail)

    def _lpr_ingest_repo(self) -> LprIngestRepository:
        self._lpr_ingest_repository.session_factory = AsyncSessionLocal
        return self._lpr_ingest_repository

    def _lpr_ingest_idempotency_key(self, read: PlateRead) -> str:
        return f"lpr-ingest:{_movement_saga_idempotency_key(read)}"

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
        except Exception as exc:  # noqa: BLE001 - worker recovery or fail-closed evidence boundary
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
                try:
                    read = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except TimeoutError:
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
            except Exception as exc:  # noqa: BLE001 - worker recovery or fail-closed evidence boundary
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
        except TimeoutError:
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
        except Exception as exc:  # noqa: BLE001 - worker recovery or fail-closed evidence boundary
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
        except Exception as exc:  # noqa: BLE001 - worker recovery or fail-closed evidence boundary
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
            result = await AccessExecution(self._movement_ledger, self._movement_sessions, self._lpr_ingest_repo())._persist_external_gate_open_admission(
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
        await independently("external_snapshot", access_event.id, lambda: AccessEnrichment(runtime).capture_snapshot(access_event))
        realtime_payload.update(access_event_realtime_payload(access_event, anomaly_count=len(anomalies), visitor_pass=None, visitor_pass_mode=None))
        await independently("external_realtime", access_event.id, lambda: event_bus.publish("access_event.finalized", realtime_payload))
        for anomaly in anomalies:
            async def notify_anomaly(anomaly: Anomaly = anomaly) -> None:
                await get_notification_service().notify(NotificationContext(
                    event_type=anomaly.anomaly_type.value, subject=access_event.registration_number,
                    severity=anomaly.severity.value,
                    facts=notification_facts(access_event, None, None, anomaly.message)))
            await independently("external_anomaly_notification", access_event.id, notify_anomaly)

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
        raw_payload[EXTERNAL_ADMISSION_PAYLOAD_KEY] = _external_admission_payload(
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
        except Exception as exc:  # noqa: BLE001 - worker recovery or fail-closed evidence boundary
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
        if _visitor_pass_candidate_kind(read) != "departure":
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
                idempotency_key=f"movement-suppressed:{_movement_saga_idempotency_key(read)}:{reason}",
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
            await self._finalize_window_or_fail(window, reason="debounce_window_expired")

    async def _flush_all_pending(self) -> None:
        if await is_maintenance_mode_active():
            self._clear_pending_reads()
            return
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
        webhook_trace = _webhook_trace_for_window(window)
        webhook_received_at = (
            datetime_from_payload(webhook_trace.get("received_at"))
            or datetime_from_payload(webhook_trace.get("captured_at"))
            or window.first_seen
        )
        captured_to_webhook_ms = _float_from_payload(webhook_trace.get("captured_to_webhook_ms"))
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
                        WEBHOOK_TRACE_PAYLOAD_KEY: _webhook_trace_from_read(item),
                    }
                    for item in window.reads
                ],
            },
        )
        execution = AccessExecution(self._movement_ledger, self._movement_sessions, self._lpr_ingest_repo())
        result = await execution.execute(
            window, read=read, direction_read=direction_read,
            runtime=self._runtime or await get_runtime_config(), trace=trace,
            finalize_started_at=finalize_started_at, webhook_trace=webhook_trace)
        if result is None:
            trace.finish(status="ok", summary="Previously committed movement reused; no hardware replay.")
            return
        await AccessEnrichment(result.runtime).run(result, trace=trace)

def _datetime_delta_ms(end: datetime, start: datetime) -> float:
    return round(max(0.0, (end - start).total_seconds()) * 1000.0, 3)

@lru_cache
def get_access_event_service() -> AccessEventService:
    return AccessEventService()
