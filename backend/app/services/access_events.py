from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import (
    GateCommandRecord,
    LprDebounceWindow,
    LprIngestEvent,
    MovementSagaRecord,
    Vehicle,
    VisitorPass,
)
from app.models.enums import MovementSagaState, VisitorPassStatus, VisitorPassType
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.access.completion_database import access_session
from app.services.access.execution import AccessExecution
from app.services.access.pipeline_contracts import (
    MAX_PLATE_READ_PROCESSING_ATTEMPTS,
    CoordinatorClaim,
    IngestClaim,
    PipelineLeaseLost,
    TrustedGateObservation,
    WindowClaim,
)
from app.services.access.pipeline_store import (
    COORDINATOR_RENEWAL_SECONDS,
    AccessPipelineStore,
    assert_ingest_in_session,
    assert_window_in_session,
    finalization_failed_in_session,
    hold_window_in_session,
    preparation_failed_in_session,
)
from app.services.access.plate_matching import match_candidates
from app.services.access.read_codec import read_from_ingest
from app.services.access.reads import (
    EXTERNAL_ADMISSION_PAYLOAD_KEY,
    EXTERNAL_ADMISSION_SOURCE_LPR_OPEN_GATE,
    EXTERNAL_ADMISSION_SOURCE_VEHICLE_SESSION,
    GATE_MALFUNCTION_PAYLOAD_KEY,
    GATE_OBSERVATION_PAYLOAD_KEY,
    INGEST_METADATA_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    PLATE_READ_RETRY_BACKOFF_SECONDS,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
    WEBHOOK_TRACE_PAYLOAD_KEY,
    WORKER_STALL_QUEUE_SECONDS,
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
)
from app.services.access.window_store import AccessWindowStore
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_controller import UnsupportedModuleError, get_gate_controller
from app.services.gate_malfunctions import active_stuck_open_malfunction_at
from app.services.leaderboard import get_leaderboard_service
from app.services.lpr_ingest import (
    LPR_GATE_CAPTURE_TIMEOUT_SECONDS,
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
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_LPR,
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
        self._wake = asyncio.Event()
        self._cleanup: asyncio.Task[None] | None = None
        self._pipeline = AccessPipelineStore(session_factory=lambda: AsyncSessionLocal())
        self._windows = AccessWindowStore(session_factory=lambda: AsyncSessionLocal())
        self._active_ingest: IngestClaim | None = None
        self._active_window: WindowClaim | None = None
        self._coordinator_owned = False
        self._worker: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._runtime: RuntimeConfig | None = None
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
        if self._worker is not None and not self._worker.done():
            return
        if self._cleanup is not None and not self._cleanup.done():
            raise RuntimeError("access_cleanup_in_progress")
        self._cleanup = None
        self._stop_event.clear()
        self._started_at = self._last_heartbeat_at = datetime.now(tz=UTC)
        event_bus.subscribe(self._handle_realtime_event, scope="all_workers",
            event_types=frozenset({"maintenance_mode.changed"}))
        event_bus.subscribe(self._handle_local_realtime_event,
            event_types=frozenset({"gate.state_changed"}))
        self._worker = asyncio.create_task(self._process_queue(), name="lpr-durable-coordinator")
        self._wake.set()
        logger.info("access_event_service_started")

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()
        event_bus.unsubscribe(self._handle_realtime_event)
        event_bus.unsubscribe(self._handle_local_realtime_event)
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop_owned(), name="access-coordinator-stop")
        await drain_owned(self._cleanup)

    async def _stop_owned(self) -> None:
        if self._worker is not None:
            if not self._worker.done() and not self._worker.cancelling():
                self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        # No early finalization. Original memberships/boundaries remain in SQL
        # for the next owner; cancellation rolls any uncommitted domain work back.
        logger.info("access_event_service_stopped")

    def status(self) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        worker_running = bool(self._worker and not self._worker.done())
        heartbeat_age_seconds = (
            max(0.0, (now - self._last_heartbeat_at).total_seconds())
            if self._last_heartbeat_at
            else None
        )
        stalled = bool(
            worker_running
            and (self._active_ingest is not None or self._active_window is not None)
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
            "intake_source": "durable",
            "coordinator_owned": self._coordinator_owned,
            "movement_sessions_source": "durable",
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_heartbeat_at": self._last_heartbeat_at.isoformat()
            if self._last_heartbeat_at
            else None,
            "last_processed_at": self._last_processed_at.isoformat()
            if self._last_processed_at
            else None,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at.isoformat() if self._last_error_at else None,
            "consecutive_failures": self._consecutive_failures,
            "total_failures": self._total_failures,
            "stalled": stalled,
        }

    async def backlog_status(self) -> dict[str, int | float | str | None]:
        return await self._pipeline.backlog_status()

    async def enqueue_plate_read(
        self, read: PlateRead, *, observation: TrustedGateObservation | None = None
    ) -> None:
        received_at = datetime.now(tz=UTC)
        read = self._read_with_webhook_trace(read, received_at)
        ingest_row, should_wake_worker = await self._persist_lpr_ingest_read(
            read, received_at=received_at
        )
        read = read_from_ingest(ingest_row)
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
        capture_token = ingest_row.capture_token
        if capture_token is None:
            return
        if await is_maintenance_mode_active():
            await self._lpr_ingest_repo().suppress_capture(
                ingest_row.id, capture_token, "maintenance_mode_active"
            )
            return
        read = await self._read_with_gate_observation(read, observation=observation)
        gate_observation = gate_observation_from_read(read)
        observed_row = await self._lpr_ingest_repo().complete_observation(
            ingest_row.id, capture_token, gate_observation
        )
        if observed_row is None:
            return
        read = read_from_ingest(observed_row)
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
        self._wake.set()
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

    async def _read_with_gate_observation(
        self, read: PlateRead, *, observation: TrustedGateObservation | None = None
    ) -> PlateRead:
        if observation is not None:
            if not isinstance(observation, TrustedGateObservation):
                raise ValueError("An explicit typed trusted gate observation is required")
            return _plate_read_with_payload(
                read,
                {
                    **(read.raw_payload or {}),
                    GATE_OBSERVATION_PAYLOAD_KEY: observation.as_payload(),
                },
            )

        observed_at = datetime.now(tz=UTC)
        detail: str | None = None
        try:
            gate = get_gate_controller("configured")
            async with asyncio.timeout(LPR_GATE_CAPTURE_TIMEOUT_SECONDS):
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

    async def _process_queue(self) -> None:
        while not self._stop_event.is_set():
            claim: CoordinatorClaim | None = None
            renewal: asyncio.Task[None] | None = None
            operation: asyncio.Task[bool] | None = None
            try:
                claim = await self._pipeline.acquire_coordinator()
                if claim is None:
                    await self._wait_for_wake()
                    continue
                self._coordinator_owned = True
                renewal = asyncio.create_task(
                    self._renew_coordinator(claim), name="lpr-coordinator-renewal"
                )
                while not self._stop_event.is_set():
                    self._active_ingest, self._active_window = None, None
                    self._wake.clear()
                    operation = asyncio.create_task(
                        self._process_boundary(claim), name="lpr-read-flush-boundary"
                    )
                    try:
                        done, _ = await asyncio.wait(
                            {operation, renewal}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if renewal in done:
                            renewal.result()
                            raise PipelineLeaseLost("coordinator_renewal_stopped")
                        worked = await operation
                        self._consecutive_failures = 0
                        if not worked:
                            await self._wait_for_wake()
                    except PipelineLeaseLost:
                        raise
                    except Exception as exc:  # noqa: BLE001 - retain the original durable processing phase.
                        self._record_worker_failure(exc)
                        await self._record_boundary_failure(exc)
                        await self._sleep_until_retry(self._worker_backoff_seconds())
                    finally:
                        if operation is not None and not operation.done():
                            operation.cancel()
                            await asyncio.gather(operation, return_exceptions=True)
                        operation = None
            except PipelineLeaseLost:
                pass  # The new SQL owner resumes the original durable boundary.
            except Exception as exc:  # noqa: BLE001 - database outages leave durable intake recoverable.
                self._record_worker_failure(exc)
                logger.warning(
                    "access_coordinator_failed", extra={"error": self._safe_exception_detail(exc)}
                )
                await self._sleep_until_retry(self._worker_backoff_seconds())
            finally:
                if operation is not None and not operation.done():
                    operation.cancel()
                if renewal is not None and not renewal.done():
                    renewal.cancel()
                await asyncio.gather(
                    *(task for task in (operation, renewal) if task is not None),
                    return_exceptions=True,
                )
                self._coordinator_owned = False
                if claim is not None:
                    try:
                        await self._pipeline.release_coordinator(claim)
                    except Exception as exc:  # noqa: BLE001 - expiry still fences abandoned ownership.
                        logger.warning(
                            "access_coordinator_release_failed",
                            extra={"error": self._safe_exception_detail(exc)},
                        )

    async def _renew_coordinator(self, claim: CoordinatorClaim) -> None:
        while True:
            await asyncio.sleep(COORDINATOR_RENEWAL_SECONDS)
            await self._pipeline.renew_coordinator(claim)

    async def _wait_for_wake(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=0.5)
        except TimeoutError:
            pass

    async def _process_boundary(self, coordinator: CoordinatorClaim) -> bool:
        async with asyncio.timeout(90):
            self._last_heartbeat_at = datetime.now(tz=UTC)
            if not await self._pipeline.has_pending_work(coordinator):
                self._last_processed_at = datetime.now(tz=UTC)
                return False
            self._runtime = await get_runtime_config()
            boundary = await self._pipeline.boundary(coordinator)
            ingest = None
            worked = boundary.phase != "idle"
            if boundary.phase == "preparing":
                ingest = await self._pipeline.recover_preparing_read(coordinator)
            elif boundary.phase == "idle":
                ingest = await self._pipeline.claim_next_read(coordinator)
            if ingest is not None:
                worked = True
                self._active_ingest = ingest
                immediate = await self._handle_queued_read(ingest)
                self._active_ingest = None
                if immediate is not None:
                    self._active_window = immediate
                    await self._finalize_window(immediate)
                    self._active_window = None
            maintenance = await is_maintenance_mode_active()
            while not self._stop_event.is_set():
                window = await self._pipeline.next_flush_window(
                    coordinator,
                    quiet_seconds=0 if maintenance else self._runtime.lpr_debounce_quiet_seconds,
                    max_seconds=0 if maintenance else self._runtime.lpr_debounce_max_seconds,
                )
                if window is None:
                    break
                worked = True
                self._active_window = window
                await self._finalize_window(window)
                self._active_window = None
            self._last_processed_at = datetime.now(tz=UTC)
            return worked

    async def _record_boundary_failure(self, exc: Exception) -> None:
        detail = self._safe_exception_detail(exc)
        async with access_session(AsyncSessionLocal) as session:
            if self._active_window is not None:
                window = await session.get(LprDebounceWindow, self._active_window.window_id)
                if window is not None and window.state == "completed":
                    # Hardware/receipt failure after the core commit is reconciled
                    # from the command journal. Never rerun original execution.
                    return
                retry = await finalization_failed_in_session(
                    session,
                    self._active_window,
                    detail=detail,
                    max_attempts=MAX_PLATE_READ_PROCESSING_ATTEMPTS,
                )
                identity = self._active_window.window_id
            elif self._active_ingest is not None:
                retry = await preparation_failed_in_session(
                    session,
                    self._active_ingest,
                    detail=detail,
                    max_attempts=MAX_PLATE_READ_PROCESSING_ATTEMPTS,
                )
                identity = self._active_ingest.ingest_id
            else:
                return
            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_LPR,
                action="plate_read.processing_retry" if retry else "plate_read.processing_held",
                actor="System",
                target_entity="LprIngestEvent" if self._active_ingest else "LprDebounceWindow",
                target_id=identity,
                outcome="retry" if retry else "held",
                metadata={
                    "error": detail,
                    "budget": "original_total_processing",
                    "max_attempts": MAX_PLATE_READ_PROCESSING_ATTEMPTS,
                },
            )
            await session.commit()

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

    async def _handle_queued_read(self, claim: IngestClaim) -> WindowClaim | None:
        async with access_session(AsyncSessionLocal) as session:
            row = await assert_ingest_in_session(session, claim)
            read = read_from_ingest(row)
            exhausted = row.attempt_count > MAX_PLATE_READ_PROCESSING_ATTEMPTS
        if exhausted or await is_maintenance_mode_active():
            async with access_session(AsyncSessionLocal) as session:
                await self._pipeline.suppress_read_in_session(
                    session,
                    claim,
                    status="held",
                    reason="processing_attempts_exhausted"
                    if exhausted
                    else "maintenance_mode_active",
                )
                await session.commit()
            return None
        read = await self._read_with_known_vehicle_match(read)
        read = await self._read_with_gate_malfunction_context(read)
        malfunction = _gate_malfunction_from_read(read)
        known = _known_vehicle_plate_match_from_read(read)
        reason = "unknown_plate_during_gate_malfunction" if malfunction and not known else None
        suppression = None
        if reason is None and await self._suppress_by_live_lpr_zone_filter(read):
            reason = LPR_ZONE_FILTER_SUPPRESSION_REASON
        if reason is None and not malfunction:
            reason = await self._exact_resolution_suppression_reason(read)
            if reason is None:
                if not known:
                    read = await self._read_with_external_admission_match(read)
                if not _external_admission_from_read(read):
                    suppression = await self._movement_sessions.suppression_for_read(
                        read, runtime=self._runtime
                    )
                    reason = suppression.reason if suppression is not None else None
        external = _external_admission_from_read(read)
        if reason is None and not _known_vehicle_plate_match_from_read(read) and not external:
            read = await self._read_with_visitor_pass_departure_match(read)
        runtime = self._runtime or await get_runtime_config()
        async with access_session(AsyncSessionLocal) as session:
            await assert_ingest_in_session(session, claim)
            if reason is None and await self._windows.suppress_after_visitor_resolution_in_session(
                session, claim, read
            ):
                reason = "visitor_pass_plate_already_resolved_in_debounce_window"
            if reason is not None:
                if suppression is not None:
                    await self._movement_sessions.annotate_suppressed_read_in_session(
                        session, read, suppression, runtime=runtime, ingest_id=claim.ingest_id
                    )
                saga = await self._record_suppressed_movement_read(session, read, reason=reason)
                await self._pipeline.suppress_read_in_session(
                    session, claim, reason=reason, status="succeeded", saga_id=saga.id
                )
                await write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_LPR,
                    action="plate_read.gate_malfunction_ignored"
                    if malfunction and not known
                    else "plate_read.suppressed",
                    actor="System",
                    target_entity="LprIngestEvent",
                    target_id=claim.ingest_id,
                    target_label=read.registration_number,
                    metadata={"reason": reason, "source": read.source},
                )
                await session.commit()
                await event_bus.publish("plate_read.suppressed", {
                    "registration_number": read.registration_number,
                    "detected_registration_number": _detected_registration_number(read),
                    "source": read.source,
                    "reason": reason,
                    "ingest_event_id": str(claim.ingest_id),
                })
                return None
            summary = await self._windows.append_in_session(
                session, claim, read, threshold=runtime.lpr_similarity_threshold
            )
            immediate_reason = (
                "exact_known_vehicle_plate"
                if _is_exact_known_vehicle_plate_match(read)
                else "external_unknown_vehicle_admission"
                if external
                else "visitor_pass_departure_plate"
                if _is_visitor_pass_plate_match(read)
                else None
            )
            immediate = None
            if immediate_reason is not None:
                summary = await self._windows.merge_related_in_session(
                    session,
                    claim,
                    summary.evidence.window_id,
                    anchor_read=read,
                    max_seconds=runtime.lpr_debounce_max_seconds,
                )
                immediate = await self._windows.claim_immediate_in_session(
                    session, claim, summary.evidence.window_id, reason=immediate_reason
                )
            else:
                await self._pipeline.begin_flush_in_session(session, claim)
            await session.commit()
            return immediate

    async def _suppress_by_live_lpr_zone_filter(self, read: PlateRead) -> bool:
        mode = (
            getattr(self._runtime, "lpr_zone_filter_mode", "shadow") if self._runtime else "shadow"
        )
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
        return True

    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        if event.type == "maintenance_mode.changed":
            self._wake.set()

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
        if self._stop_event.is_set() or await is_maintenance_mode_active():
            return
        observed_at = datetime_from_payload(
            event.payload.get("state_changed_at")
        ) or datetime_from_payload(event.created_at)
        if observed_at is None:
            return
        runtime = self._runtime or await get_runtime_config()
        async with access_session(AsyncSessionLocal) as session:
            if await self._recent_iacs_gate_open_command(session, observed_at):
                return
            match = await self._movement_sessions.external_admission_candidate_for_gate_open(
                session, opened_at=observed_at, runtime=runtime
            )
            if match is None:
                return
            result = await AccessExecution(
                self._movement_ledger, self._movement_sessions
            ).persist_external_gate_open_admission(
                session, match, observed_at=observed_at, gate_payload=event.payload, runtime=runtime
            )
            if result is not None:
                await session.commit()
                get_leaderboard_service().wake()

    async def _recent_iacs_gate_open_command(
        self, session: AsyncSession, observed_at: datetime
    ) -> GateCommandRecord | None:
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

    async def _read_with_known_vehicle_match(self, read: PlateRead) -> PlateRead:
        candidates = _candidate_registration_numbers(read)
        registrations = await self._matching_vehicle_registrations(candidates)
        threshold = (
            self._runtime.lpr_similarity_threshold
            if self._runtime
            else settings.lpr_similarity_threshold
        )
        best_match = match_candidates(candidates, registrations, threshold)

        if not best_match:
            return read

        raw_payload = dict(read.raw_payload or {})
        raw_payload[KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY] = best_match
        return _plate_read_with_payload(
            read, raw_payload, registration_number=str(best_match["registration_number"])
        )

    async def _matching_vehicle_registrations(self, candidates: tuple[str, ...]) -> list[str]:
        if not candidates:
            return []
        async with access_session(AsyncSessionLocal) as session:
            # These fixed SQL literals match the functional index even when PostgreSQL
            # chooses a generic prepared plan. Plate candidates remain bound values.
            normalized = func.upper(
                func.regexp_replace(
                    Vehicle.registration_number,
                    literal_column("'[^A-Za-z0-9]'"),
                    literal_column("''"),
                    literal_column("'g'"),
                )
            )
            exact = list(
                (
                    await session.scalars(
                        select(Vehicle.registration_number).where(
                            Vehicle.is_active.is_(True),
                            normalized.in_(candidates),
                        )
                    )
                ).all()
            )
            if exact:
                return exact
            return list(
                (
                    await session.scalars(
                        select(Vehicle.registration_number).where(
                            Vehicle.is_active.is_(True),
                        )
                    )
                ).all()
            )

    async def _read_with_gate_malfunction_context(self, read: PlateRead) -> PlateRead:
        gate_observation = gate_observation_from_read(read)
        gate_state = coerce_gate_state(gate_observation.get("state"))
        if gate_state not in DEPARTURE_GATE_STATES:
            return read

        async with access_session(AsyncSessionLocal) as session:
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
        async with access_session(AsyncSessionLocal) as session:
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
            getattr(match.session, "registration_number", None) or read.registration_number
        )
        return _plate_read_with_payload(read, raw_payload, registration_number=registration_number)

    async def _read_with_visitor_pass_departure_match(self, read: PlateRead) -> PlateRead:
        plate = normalize_registration_number(read.registration_number)
        if not plate:
            return read

        async with access_session(AsyncSessionLocal) as session:
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

    async def _exact_resolution_suppression_reason(self, read: PlateRead) -> str | None:
        match = _known_vehicle_plate_match_from_read(read)
        try:
            async with access_session(AsyncSessionLocal) as session:
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
                    gate_cycle_expires_at=row.gate_cycle_expires_at
                    or row.debounce_expires_at
                    or row.started_at,
                    direction=row.direction,
                    decision=row.decision,
                )
                for row in rows
            ),
        )
        return decision.reason

    async def _record_suppressed_movement_read(
        self,
        session: AsyncSession,
        read: PlateRead,
        *,
        reason: str,
    ) -> MovementSagaRecord:
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
        return saga

    async def _finalize_window(self, claim: WindowClaim) -> None:
        async with access_session(AsyncSessionLocal) as session:
            await assert_window_in_session(session, claim)
            if await is_maintenance_mode_active():
                await hold_window_in_session(session, claim, reason="maintenance_mode_active")
                await write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_LPR,
                    action="plate_read.processing_held",
                    actor="System",
                    target_entity="LprDebounceWindow",
                    target_id=claim.window_id,
                    outcome="held",
                    metadata={"reason": "maintenance_mode_active"},
                )
                await session.commit()
                return
            window = await self._windows.load_in_session(session, claim.window_id)
        read = window.best_read
        direction_read = window.anchor_read or window.first_read
        finalize_started_at = datetime.now(tz=UTC)
        webhook_trace = _webhook_trace_for_window(window)
        trace = telemetry.start_trace(
            f"Plate Detection - {read.registration_number}",
            category=TELEMETRY_CATEGORY_LPR,
            source=read.source,
            registration_number=read.registration_number,
            started_at=window.summary.first_seen,
            context={
                "candidate_count": window.summary.count,
                "preview_count": len(window.preview),
                "evidence_reference": {"version": 2, "window_id": str(claim.window_id)},
                "source": read.source,
                "first_seen": window.summary.first_seen.isoformat(),
                "finalize_started_at": finalize_started_at.isoformat(),
                "webhook_received_at": window.summary.min_received_at.isoformat(),
                WEBHOOK_TRACE_PAYLOAD_KEY: webhook_trace,
            },
        )
        trace.record_span(
            "Webhook Receipt to Debounce Finalization",
            started_at=window.summary.min_received_at,
            ended_at=finalize_started_at,
            attributes={
                "candidate_count": window.summary.count,
                "selected_registration_number": read.registration_number,
            },
            output_payload={
                "candidates": [item.as_payload() for item in window.preview],
                "preview_complete": window.summary.count == len(window.preview),
            },
        )
        result = await AccessExecution(
            self._movement_ledger, self._movement_sessions
        ).execute(
            window,
            claim=claim,
            read=read,
            direction_read=direction_read,
            runtime=self._runtime or await get_runtime_config(),
            trace=trace,
            finalize_started_at=finalize_started_at,
            webhook_trace=webhook_trace,
        )
        trace.finish(
            status="ok",
            summary=(
                "Committed access decision and durable completion reservations."
                if result is not None
                else "Original movement outcome reused or held; no hardware replay."
            ),
        )


def _datetime_delta_ms(end: datetime, start: datetime) -> float:
    return round(max(0.0, (end - start).total_seconds()) * 1000.0, 3)


@lru_cache
def get_access_event_service() -> AccessEventService:
    return AccessEventService()
