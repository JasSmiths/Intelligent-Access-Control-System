import asyncio
import copy
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    Anomaly,
    GateStateObservation,
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
    TimingClassification,
)
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.modules.gate.base import GateState
from app.modules.unifi_protect.client import (
    ProtectRecoveryPageInvalid,
    UnifiProtectError,
    protect_connection_key,
)
from app.services import ha_presence_reservations
from app.services.access.historical import (
    HistoricalSessionInput,
    persist_historical_event_in_session,
)
from app.services.access.publication import reserve_initial_publication_in_session
from app.services.ha_presence_contracts import HaCoordinationBusy
from app.services.historical_candidates import (
    HISTORICAL_MEDIA_SECONDS,
    HISTORICAL_PAGE_SIZE,
    HISTORICAL_TRANSACTION_SECONDS,
    HistoricalCandidateStore,
    HistoricalClaim,
    HistoricalClaimLost,
)
from app.services.historical_discovery import (
    DISCOVERY_RAW_LIMIT,
    DISCOVERY_RAW_REQUEST_LIMIT,
    DISCOVERY_RECORD_LIMIT,
    DISCOVERY_REQUEST_LIMIT,
    DISCOVERY_SECONDS,
    DiscoveryClaim,
    DiscoveryStatus,
    HistoricalDiscoveryStore,
    milliseconds,
    utc_from_ms,
)
from app.services.leaderboard import get_leaderboard_service
from app.services.movement.admission import AdmissionResult
from app.services.movement.sessions import payload_values
from app.services.movement_fsm import MovementDirectionFSM, MovementIntent
from app.services.movement_ledger import movement_saga_summary
from app.services.schedules import ScheduleEvaluation, evaluate_vehicle_schedule
from app.services.settings import get_runtime_config
from app.services.snapshots import (
    SNAPSHOT_HEIGHT,
    SNAPSHOT_WIDTH,
    SnapshotError,
    SnapshotMetadata,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
    alert_snapshot_metadata_from_event,
    apply_snapshot_to_access_event,
    get_snapshot_manager,
)
from app.services.telemetry import TELEMETRY_CATEGORY_ACCESS, telemetry, write_audit_log
from app.services.unifi_protect import get_unifi_protect_service
from app.services.visitor_passes import get_visitor_pass_service

logger = get_logger(__name__)

BACKEND_RUNTIME_STATE_RELATIVE_PATH = "runtime/backend-state.json"
BACKEND_RUNTIME_HEARTBEAT_INTERVAL_SECONDS = 15.0
MISSED_EVENT_BACKFILL_DEFAULT_LOOKBACK = timedelta(minutes=30)
MISSED_EVENT_BACKFILL_MAX_LOOKBACK = timedelta(hours=24)
MISSED_EVENT_BACKFILL_OVERLAP = timedelta(minutes=2)
MISSED_EVENT_BACKFILL_DUPLICATE_WINDOW = timedelta(seconds=90)
MISSED_EVENT_TRAILING_OCR_SUPPRESSION_WINDOW = timedelta(seconds=45)
MISSED_EVENT_BACKFILL_SOURCE = "unifi_protect_restart_backfill"
MISSED_EVENT_RECONCILIATION_SOURCE = "unifi_protect_lpr_reconciliation"
MISSED_EVENT_RECONCILIATION_INTERVAL_SECONDS = 120.0
MISSED_EVENT_RECONCILIATION_INITIAL_LOOKBACK = timedelta(minutes=15)
MISSED_EVENT_RECONCILIATION_REASON = "UniFi Protect retained an LPR event that did not arrive through the webhook while the backend was running."


@dataclass(frozen=True)
class ProtectBackfillCandidate:
    protect_event_id: str
    registration_number: str
    captured_at: datetime
    confidence: float
    camera_id: str | None
    camera_name: str | None
    protect_event: dict[str, Any]
    track_candidate: dict[str, Any]


@dataclass
class MissedAccessEventBackfillResult:
    scanned: int = 0
    lpr_candidates: int = 0
    backfilled: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: int = 0
    window_start: str | None = None
    window_end: str | None = None
    reason: str | None = None
    discovery_state: str = "pending"
    discovery_complete: bool = False
    covered_through: str | None = None
    discovery_requests: int = 0
    discovery_reason: str | None = None
    discovery_required_start: str | None = None


def read_backend_runtime_state() -> dict[str, Any]:
    path = _runtime_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


async def run_backend_runtime_heartbeat(
    *,
    started_at: datetime,
    previous_state: dict[str, Any] | None = None,
) -> None:
    started_at = _aware_utc(started_at)
    previous = previous_state if isinstance(previous_state, dict) else {}

    async def write(status: str, *, stopped_at: datetime | None = None) -> None:
        now = datetime.now(tz=UTC)
        payload = {
            "service": "backend",
            "status": status,
            "pid": os.getpid(),
            "started_at": started_at.isoformat(),
            "last_heartbeat_at": now.isoformat(),
            "previous_started_at": previous.get("started_at"),
            "previous_last_heartbeat_at": previous.get("last_heartbeat_at"),
            "previous_stopped_at": previous.get("stopped_at"),
        }
        if stopped_at:
            payload["stopped_at"] = _aware_utc(stopped_at).isoformat()
        await asyncio.to_thread(_write_runtime_state, payload)

    await write("running")
    try:
        while True:
            await asyncio.sleep(BACKEND_RUNTIME_HEARTBEAT_INTERVAL_SECONDS)
            await write("running")
    except asyncio.CancelledError:
        await write("stopped", stopped_at=datetime.now(tz=UTC))
        raise


async def backfill_missed_access_events_safely(
    *,
    previous_runtime_state: dict[str, Any] | None = None,
    startup_at: datetime | None = None,
) -> MissedAccessEventBackfillResult:
    try:
        result = await MissedAccessEventBackfillService().run(
            previous_runtime_state=previous_runtime_state,
            startup_at=startup_at,
        )
        await _audit_backfill_result(result)
        logger.info("missed_access_event_backfill_finished", extra=asdict(result))
        return result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("missed_access_event_backfill_failed", extra={"error": str(exc)})
        result = MissedAccessEventBackfillResult(errors=1, reason=str(exc))
        await _audit_backfill_result(result)
        return result


async def run_missed_access_event_reconciliation(
    *,
    interval_seconds: float = MISSED_EVENT_RECONCILIATION_INTERVAL_SECONDS,
    initial_lookback: timedelta = MISSED_EVENT_RECONCILIATION_INITIAL_LOOKBACK,
) -> None:
    """Continuously reconcile retained Protect LPR events that webhooks missed."""

    service = MissedAccessEventBackfillService(
        source=MISSED_EVENT_RECONCILIATION_SOURCE,
        backfill_reason=MISSED_EVENT_RECONCILIATION_REASON,
    )
    window_start = datetime.now(tz=UTC) - initial_lookback
    while True:
        window_end = datetime.now(tz=UTC)
        try:
            result = await service.run(
                startup_at=window_end,
                window_start_override=window_start - MISSED_EVENT_BACKFILL_OVERLAP,
            )
            await _audit_backfill_result(
                result,
                action="access_event.reconciliation_checked",
                target_label="Protect LPR reconciliation",
            )
            logger.info("missed_access_event_reconciliation_finished", extra=asdict(result))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("missed_access_event_reconciliation_failed", extra={"error": str(exc)})
            result = MissedAccessEventBackfillResult(errors=1, reason=str(exc))
            await _audit_backfill_result(
                result,
                action="access_event.reconciliation_checked",
                target_label="Protect LPR reconciliation",
            )
        if result.covered_through is not None:
            # Earlier work is already retained in the durable frontier/queue.
            # Rewinding here would recursively subtract another overlap on each
            # incomplete cycle and invent an ever-older discovery requirement.
            window_start = max(window_start, _aware_utc(datetime.fromisoformat(result.covered_through)))
        await asyncio.sleep(interval_seconds)


class MissedAccessEventBackfillService:
    def __init__(
        self,
        *,
        source: str = MISSED_EVENT_BACKFILL_SOURCE,
        backfill_reason: str = "UniFi Protect retained an LPR event during a backend/container restart window.",
        candidate_store: HistoricalCandidateStore | None = None,
    ) -> None:
        self.source = source
        self.backfill_reason = backfill_reason
        self._movement_direction_fsm = MovementDirectionFSM()
        self.candidate_store = candidate_store or HistoricalCandidateStore()

    async def run(
        self,
        *,
        previous_runtime_state: dict[str, Any] | None = None,
        startup_at: datetime | None = None,
        window_start_override: datetime | None = None,
    ) -> MissedAccessEventBackfillResult:
        startup = _aware_utc(startup_at or datetime.now(tz=UTC))
        protect = get_unifi_protect_service()
        result = MissedAccessEventBackfillResult(window_end=startup.isoformat())
        drained = await self._drain_candidates(result, limit=HISTORICAL_PAGE_SIZE)

        if not await protect.configured():
            result.reason = "unifi_protect_not_configured"
            return result

        async with AsyncSessionLocal() as session:
            latest_event_at = await session.scalar(select(func.max(AccessEvent.occurred_at)))

        window_start = (
            _aware_utc(window_start_override)
            if window_start_override
            else backfill_window_start(
                previous_runtime_state or {},
                startup,
                latest_event_at=latest_event_at,
            )
        )
        result.window_start = window_start.isoformat()

        discovery = HistoricalDiscoveryStore(self.candidate_store)
        key = protect_connection_key(await get_runtime_config())
        claim, status = await discovery.request_and_claim(key=key, start=milliseconds(window_start),
            end=milliseconds(startup)+1, source=self.source, reason=self.backfill_reason)
        if claim is not None:
            try:
                async with asyncio.timeout(DISCOVERY_SECONDS):
                    status = await self._discover(protect, discovery, claim, result)
            except asyncio.CancelledError:
                try:
                    await drain_owned(discovery.checkpoint(claim, release=True, authoritative=False))
                except HistoricalClaimLost:
                    pass  # A completed/reclaimed checkpoint must not mask cancellation.
                raise
            except Exception as exc:  # noqa: BLE001 - retain incomplete coverage without provider payloads.
                result.discovery_reason = "discovery_budget_expired" if isinstance(exc, TimeoutError) else "discovery_retry_required"
                result.errors += 1
                try:
                    status = await discovery.checkpoint(claim, release=True, authoritative=False)
                except HistoricalClaimLost:
                    status = DiscoveryStatus('pending', False, utc_from_ms(claim.start), 'discovery_lease_lost')
        result.discovery_state = status.state
        result.discovery_complete = status.complete
        result.covered_through = status.covered_through.isoformat()
        result.discovery_reason = status.reason or result.discovery_reason
        result.discovery_required_start = (status.required_start or (utc_from_ms(claim.start) if claim else window_start)).isoformat()
        await self._drain_candidates(result, limit=HISTORICAL_PAGE_SIZE-drained)
        return result

    async def _discover(self, protect: Any, store: HistoricalDiscoveryStore,
                        claim: DiscoveryClaim, result: MissedAccessEventBackfillResult) -> DiscoveryStatus:
        raw_requests = records = 0
        generation = await protect.prepare_discovery(claim.key)
        status = DiscoveryStatus('pending', False, utc_from_ms(claim.start))
        while claim.frontier and records < DISCOVERY_RECORD_LIMIT:
            if not claim.page:
                if raw_requests >= DISCOVERY_RAW_REQUEST_LIMIT or result.discovery_requests >= DISCOVERY_REQUEST_LIMIT:
                    break
                lo, hi = claim.frontier[0]
                raw_requests += 1
                result.discovery_requests += 1
                try:
                    page, generation = await protect.recovery_page(key=claim.key, start_ms=lo, end_ms=hi)
                except ProtectRecoveryPageInvalid:
                    return await store.checkpoint(claim, hold='discovery_invalid_provider_response')
                result.scanned += page.raw_count
                next_claim = copy.deepcopy(claim)
                async with protect.discovery_authority(claim.key, generation):
                    if page.raw_count == DISCOVERY_RAW_LIMIT:
                        if hi-lo <= 1:
                            return await store.checkpoint(claim, hold='discovery_dense_boundary_unproven')
                        mid = (lo+hi)//2
                        next_claim.frontier[:1] = [[lo, mid], [mid, hi]]
                    elif page.events:
                        next_claim.page, next_claim.index = page.events, 0
                    else:
                        next_claim.frontier.pop(0)
                    status = await store.checkpoint(next_claim)
                claim.frontier, claim.page, claim.index = next_claim.frontier, next_claim.page, next_claim.index
                if status.state != "leased":
                    return status
                if not claim.page:
                    continue
            row = claim.page[claim.index]
            records += 1
            candidate_payload = None
            if await self.candidate_store.contains(row['id']):
                result.duplicates += 1
            elif row['type'] != 'smartDetectZone' or not _event_might_have_plate(row):
                result.skipped += 1
            else:
                # Track collection admits one detail lookup and one track read.
                if result.discovery_requests+2 > DISCOVERY_REQUEST_LIMIT:
                    break
                result.discovery_requests += 2
                async def track(identity: str, current_generation: int = generation) -> dict[str, Any]:
                    return await protect.recovery_track(claim.key, current_generation, identity)
                candidate = await self._candidate_from_protect_event(track, row)
                if candidate is None:
                    if any('plate' in str(value).lower() or 'license' in str(value).lower()
                           for value in row.get('smart_detect_types', [])):
                        return await store.checkpoint(claim, hold='discovery_lpr_track_unresolved')
                    result.skipped += 1
                elif not claim.start <= milliseconds(candidate.captured_at) < claim.end:
                    return await store.checkpoint(claim, hold='discovery_capture_time_outside_window')
                else:
                    result.lpr_candidates += 1
                    candidate_payload = asdict(candidate)
                    candidate_payload['captured_at'] = candidate.captured_at.isoformat()
                    candidate_payload['backfill_reason'] = claim.reason
                    candidate_payload['discovery_source'] = claim.source
            # Copy the proposed advancement: an interrupted/rolled-back commit
            # must never mutate the in-memory checkpoint subsequently released.
            next_claim = DiscoveryClaim(claim.key, claim.token, claim.start, claim.end,
                claim.source, claim.reason, [list(value) for value in claim.frontier],
                list(claim.page), claim.index+1)
            if next_claim.index == len(next_claim.page):
                next_claim.frontier.pop(0)
                next_claim.page, next_claim.index = [], 0
            async with protect.discovery_authority(claim.key, generation):
                status = await store.checkpoint(next_claim, candidate=candidate_payload)
            claim.frontier, claim.page, claim.index = next_claim.frontier, next_claim.page, next_claim.index
            if status.state != "leased":
                return status
        return await store.checkpoint(claim, release=True)

    async def _candidate_from_protect_event(
        self,
        track_reader: Callable[[str], Awaitable[dict[str, Any]]],
        protect_event: dict[str, Any],
    ) -> ProtectBackfillCandidate | None:
        protect_event_id = str(protect_event.get("id") or "").strip()
        if not protect_event_id or len(protect_event_id) > 180:
            return None
        if not _event_might_have_plate(protect_event):
            return None
        track = await track_reader(protect_event_id)

        observations = [row for row in track.get("observations", []) if isinstance(row, dict)]
        track_candidate = _best_track_candidate(observations)
        if not track_candidate:
            return None

        registration_number = normalize_registration_number(
            str(
                track_candidate.get("registration_number") or track_candidate.get("raw_value") or ""
            )
        )
        captured_at = (
            _parse_datetime(track_candidate.get("captured_at"))
            or _parse_datetime((track.get("event") or {}).get("start"))
            or _parse_datetime(protect_event.get("start"))
        )
        if not registration_number or not captured_at:
            return None

        event = track.get("event")
        event_payload = event if isinstance(event, dict) else protect_event
        return ProtectBackfillCandidate(
            protect_event_id=protect_event_id,
            registration_number=registration_number,
            captured_at=_aware_utc(captured_at),
            confidence=_confidence_ratio(track_candidate.get("confidence")),
            camera_id=str(event_payload.get("camera_id") or protect_event.get("camera_id") or "")
            or None,
            camera_name=str(
                event_payload.get("camera_name") or protect_event.get("camera_name") or ""
            )
            or None,
            protect_event=event_payload if isinstance(event_payload, dict) else protect_event,
            track_candidate=track_candidate,
        )

    async def _drain_candidates(self, result: MissedAccessEventBackfillResult, *, limit: int) -> int:
        processed = 0
        for _ in range(limit):
            claim = await self.candidate_store.claim()
            if claim is None:
                break
            processed += 1
            try:
                processor = MissedAccessEventBackfillService(
                    source=str(claim.candidate.get("discovery_source") or claim.source),
                    backfill_reason=str(claim.candidate["backfill_reason"]), candidate_store=self.candidate_store)
                created = await processor._process_candidate(claim)
                if created:
                    result.backfilled += 1
                else:
                    result.duplicates += 1
            except asyncio.CancelledError:
                await drain_owned(self._retry_candidate(claim, reason="historical_cancelled"))
                raise
            except HistoricalClaimLost:
                result.errors += 1
            except HaCoordinationBusy:
                await self._retry_candidate(claim, reason="ha_coordination_busy")
                result.errors += 1
            except Exception:  # noqa: BLE001 - retain immutable candidate/media for bounded automatic retry
                await self._retry_candidate(claim, reason="historical_processing_failed")
                result.errors += 1
                logger.warning("Historical candidate processing deferred")
        return processed

    async def _retry_candidate(self, claim: HistoricalClaim, *, reason: str) -> None:
        try:
            await self.candidate_store.retry(claim, reason=reason)
        except HistoricalClaimLost:
            pass  # Expired ownership is settled by the next bounded claim scan.

    async def _process_candidate(self, claim: HistoricalClaim) -> bool:
        saved = claim.candidate
        candidate = ProtectBackfillCandidate(protect_event_id=claim.protect_event_id,
            registration_number=str(saved["registration_number"]), captured_at=claim.captured_at,
            confidence=float(saved["confidence"]), camera_id=saved["camera_id"], camera_name=saved["camera_name"],
            protect_event=saved["protect_event"], track_candidate=saved["track_candidate"])
        async with self.candidate_store.session() as session:
            existing = await self._matching_existing_event(session, candidate)
        if existing is not None:
            async with self.candidate_store.session() as session:
                row = await self.candidate_store.lock_owned(session, claim)
                await self.candidate_store.complete_in_session(session, row, claim, skipped=True)
                await session.commit()
            await self.candidate_store.discard_skipped_media(claim)
            return False
        if claim.media_state == "pending":
            token = await self.candidate_store.begin_media(claim)
            child = asyncio.create_task(self._prepare_protect_thumbnail(claim, token, candidate),
                name="historical-media")
            try:
                async with asyncio.timeout(HISTORICAL_MEDIA_SECONDS):
                    media = await asyncio.shield(child)
            finally:
                if not child.done() and not child.cancelling():
                    child.cancel()
                await drain_owned(asyncio.gather(child, return_exceptions=True))
            await self.candidate_store.finish_media(claim, token, media)
        media = await self.candidate_store.media_result(claim)
        created = await self._create_backfill_event(candidate, claim, media)
        if not created:
            await self.candidate_store.discard_skipped_media(claim)
        return created

    async def _create_backfill_event(
        self, candidate: ProtectBackfillCandidate, claim: HistoricalClaim, media: dict[str, Any]
    ) -> bool:
        config = await get_runtime_config()
        async with asyncio.timeout(HISTORICAL_TRANSACTION_SECONDS), self.candidate_store.session() as session:
            candidate_row = await self.candidate_store.lock_owned(session, claim)
            lease_until = candidate_row.lease_until
            duplicate = await self._matching_existing_event(session, candidate)
            if duplicate:
                await self.candidate_store.complete_in_session(session, candidate_row, claim, skipped=True)
                await session.commit()
                return False

            vehicle = await self._lookup_vehicle(session, candidate.registration_number)
            if not vehicle:
                suppressed_by = await self._recent_known_arrival_for_candidate(session, candidate)
                if suppressed_by:
                    logger.info(
                        "missed_access_event_candidate_suppressed_as_arrival_ocr_noise",
                        extra={
                            "protect_event_id": candidate.protect_event_id,
                            "registration_number": candidate.registration_number,
                            "captured_at": candidate.captured_at.isoformat(),
                            "camera_id": candidate.camera_id,
                            "matched_event_id": str(suppressed_by.id),
                            "matched_registration_number": suppressed_by.registration_number,
                        },
                    )
                    await self.candidate_store.complete_in_session(session, candidate_row, claim, skipped=True)
                    await session.commit()
                    return False
            person = vehicle.owner if vehicle else None
            visitor_pass: VisitorPass | None = None
            visitor_pass_mode: str | None = None
            if not vehicle:
                visitor_pass, visitor_pass_mode = await self._match_visitor_pass(session, candidate)
            identity_active = bool(vehicle and (not person or person.is_active))
            schedule_evaluation = (
                await evaluate_vehicle_schedule(
                    session,
                    vehicle,
                    candidate.captured_at,
                    timezone_name=config.site_timezone,
                    default_policy=config.schedule_default_policy,
                )
                if vehicle and identity_active
                else None
            )
            if visitor_pass:
                schedule_evaluation = ScheduleEvaluation(
                    allowed=True,
                    source=f"visitor_pass_{visitor_pass_mode or 'match'}",
                    reason=f"Visitor pass matched for {visitor_pass.visitor_name}.",
                )
            allowed = bool(
                (schedule_evaluation and schedule_evaluation.allowed and identity_active)
                or visitor_pass
            )
            decision = AccessDecision.GRANTED if allowed else AccessDecision.DENIED
            direction, direction_resolution = await self._direction_for_backfill(
                session,
                person,
                captured_at=candidate.captured_at,
                allowed=allowed,
                visitor_pass_mode=visitor_pass_mode,
            )
            trace = telemetry.start_trace(
                "Missed Access Event Backfill",
                category=TELEMETRY_CATEGORY_ACCESS,
                actor="System",
                source=self.source,
                registration_number=candidate.registration_number,
                context={
                    "protect_event_id": candidate.protect_event_id,
                    "direction": direction.value,
                    "decision": decision.value,
                    "hardware_actions_suppressed": True,
                },
            )
            trace.record_span(
                "Protect restart evidence selected",
                started_at=datetime.now(tz=UTC),
                category=TELEMETRY_CATEGORY_ACCESS,
                output_payload={
                    "protect_event_id": candidate.protect_event_id,
                    "registration_number": candidate.registration_number,
                    "captured_at": candidate.captured_at.isoformat(),
                    "track_candidate": candidate.track_candidate,
                },
            )

            event = AccessEvent(
                id=claim.event_id,
                vehicle_id=vehicle.id if vehicle else None,
                person_id=person.id if person else None,
                registration_number=candidate.registration_number,
                direction=direction,
                decision=decision,
                confidence=candidate.confidence,
                source=self.source,
                occurred_at=candidate.captured_at,
                timing_classification=TimingClassification.UNKNOWN,
                raw_payload={
                    "backfill": {
                        "source": self._backfill_payload_source(),
                        "reason": self.backfill_reason,
                        "created_by": "System",
                        "created_at": datetime.now(tz=UTC).isoformat(),
                        "hardware_actions_suppressed": True,
                        "gate_commands_suppressed": True,
                        "garage_commands_suppressed": True,
                        "notifications_suppressed": True,
                    },
                    "protect_evidence": {
                        "event_id": candidate.protect_event_id,
                        "camera_id": candidate.camera_id,
                        "camera_name": candidate.camera_name,
                        "captured_at": candidate.captured_at.isoformat(),
                        "confidence": candidate.confidence,
                        "event": candidate.protect_event,
                        "track_candidate": candidate.track_candidate,
                    },
                    "schedule": _schedule_evaluation_payload(schedule_evaluation),
                    "direction_resolution": direction_resolution,
                    "visitor_pass": _visitor_pass_payload(visitor_pass, visitor_pass_mode),
                    "telemetry": {"trace_id": trace.trace_id},
                },
            )
            session.add(event)
            await session.flush()

            self._apply_prepared_thumbnail(event, candidate, media)
            anomalies = _build_backfill_anomalies(
                event, person, vehicle, allowed, visitor_pass=visitor_pass
            )
            session.add_all(anomalies)

            finalized = await self._persist_backfill_movement(
                session, event, candidate, direction_resolution=direction_resolution
            )
            movement_saga, presence_updated = finalized.saga, finalized.presence_changed
            event.raw_payload = {
                **(event.raw_payload or {}),
                "movement_saga": movement_saga_summary(movement_saga),
            }

            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_ACCESS,
                action=self._audit_backfill_action(),
                actor="System",
                target_entity="AccessEvent",
                target_id=event.id,
                target_label=event.registration_number,
                metadata={
                    "protect_event_id": candidate.protect_event_id,
                    "camera_name": candidate.camera_name,
                    "direction": event.direction.value,
                    "decision": event.decision.value,
                    "presence_updated": presence_updated,
                    "anomaly_count": len(anomalies),
                    "visitor_pass_id": str(visitor_pass.id) if visitor_pass else None,
                    "visitor_pass_mode": visitor_pass_mode,
                    "hardware_actions_suppressed": True,
                },
                trace_id=trace.trace_id,
            )
            await reserve_initial_publication_in_session(session, event.id)
            await get_leaderboard_service().mark_access_in_session(session, event, origin_at=None)
            await self.candidate_store.complete_in_session(session, candidate_row, claim)
            await session.flush()
            proposals = [finalized.ha_reservation] if finalized.ha_reservation is not None else []
            # Last domain participant: busy rolls back event, session, audit,
            # publication, chart and candidate completion together.
            await ha_presence_reservations.reserve_all_in_session(session, proposals)
            if lease_until is None or await self.candidate_store.now(session) >= lease_until:
                raise HistoricalClaimLost("historical_lease_expired_before_commit")
            await session.commit()
            get_leaderboard_service().wake()

        logger.info(
            "missed_access_event_backfilled",
            extra={
                "event_id": str(event.id),
                "registration_number": event.registration_number,
                "person_id": str(event.person_id) if event.person_id else None,
                "person_name": person.display_name if person else None,
                "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
                "vehicle_make": vehicle.make if vehicle else None,
                "vehicle_model": vehicle.model if vehicle else None,
                "vehicle_color": vehicle.color if vehicle else None,
                "direction": event.direction.value,
                "decision": event.decision.value,
                "occurred_at": event.occurred_at.isoformat(),
                "protect_event_id": candidate.protect_event_id,
                "camera_name": candidate.camera_name,
                "presence_updated": presence_updated,
                "anomaly_count": len(anomalies),
                "snapshot_path": event.snapshot_path,
                "hardware_actions_suppressed": True,
                "gate_commands_suppressed": True,
                "garage_commands_suppressed": True,
            },
        )
        trace.finish(
            status="ok",
            level="warning" if anomalies or decision == AccessDecision.DENIED else "info",
            summary=f"Backfilled {event.direction.value} for plate {event.registration_number}",
            access_event_id=event.id,
            context={
                "event_id": str(event.id),
                "protect_event_id": candidate.protect_event_id,
                "presence_updated": presence_updated,
                "anomaly_count": len(anomalies),
            },
        )
        try:
            await telemetry.flush()
        except Exception:  # noqa: BLE001 - observability cannot undo the committed historical decision
            logger.warning("Historical telemetry flush deferred")
        return True

    async def _persist_backfill_movement(
        self,
        session: AsyncSession,
        event: AccessEvent,
        candidate: ProtectBackfillCandidate,
        *,
        direction_resolution: dict[str, Any],
    ) -> AdmissionResult:
        return await persist_historical_event_in_session(
            session,
            event,
            idempotency_key=f"movement-backfill:{self.source}:{candidate.protect_event_id}",
            evidence={
                **direction_resolution,
                "source": self.source,
                "protect_event_id": candidate.protect_event_id,
                "backfill": True,
            },
            session_input=HistoricalSessionInput(
                debounce_seconds=settings.lpr_debounce_max_seconds,
                idle_seconds=settings.lpr_vehicle_session_idle_seconds,
                camera_id=candidate.camera_id,
                protect_event_ids={candidate.protect_event_id},
                ocr_variants=_candidate_ocr_variants(candidate),
                last_gate_state=_direction_resolution_gate_state(direction_resolution),
            ),
        )

    async def _matching_existing_event(
        self,
        session: AsyncSession,
        candidate: ProtectBackfillCandidate,
    ) -> AccessEvent | None:
        rows = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    AccessEvent.occurred_at
                    >= candidate.captured_at - MISSED_EVENT_BACKFILL_DUPLICATE_WINDOW,
                    AccessEvent.occurred_at
                    <= candidate.captured_at + MISSED_EVENT_BACKFILL_DUPLICATE_WINDOW,
                )
                .order_by(AccessEvent.occurred_at.desc())
                .limit(50)
            )
        ).all()
        for row in rows:
            if candidate.protect_event_id in protect_event_ids_from_payload(row.raw_payload):
                return row
            if row.registration_number == candidate.registration_number:
                return row
        return None

    async def _lookup_vehicle(
        self, session: AsyncSession, registration_number: str
    ) -> Vehicle | None:
        return await session.scalar(
            select(Vehicle)
            .options(
                selectinload(Vehicle.schedule),
                selectinload(Vehicle.owner).selectinload(Person.group),
                selectinload(Vehicle.owner).selectinload(Person.schedule),
            )
            .where(
                Vehicle.registration_number == registration_number,
                Vehicle.is_active.is_(True),
            )
        )

    async def _recent_known_arrival_for_candidate(
        self,
        session: AsyncSession,
        candidate: ProtectBackfillCandidate,
    ) -> AccessEvent | None:
        if not candidate.camera_id:
            return None
        rows = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    AccessEvent.decision == AccessDecision.GRANTED,
                    AccessEvent.direction == AccessDirection.ENTRY,
                    AccessEvent.vehicle_id.is_not(None),
                    AccessEvent.occurred_at <= candidate.captured_at,
                    AccessEvent.occurred_at
                    >= candidate.captured_at - MISSED_EVENT_TRAILING_OCR_SUPPRESSION_WINDOW,
                )
                .order_by(AccessEvent.occurred_at.desc())
                .limit(25)
            )
        ).all()
        for event in rows:
            if candidate.camera_id in _camera_ids_from_access_event(event):
                return event
        return None

    async def _match_visitor_pass(
        self,
        session: AsyncSession,
        candidate: ProtectBackfillCandidate,
    ) -> tuple[VisitorPass | None, str | None]:
        return await get_visitor_pass_service().find_historical_match(
            session,
            occurred_at=candidate.captured_at,
            registration_number=candidate.registration_number,
        )

    async def _direction_for_backfill(
        self,
        session: AsyncSession,
        person: Person | None,
        *,
        captured_at: datetime,
        allowed: bool,
        visitor_pass_mode: str | None = None,
    ) -> tuple[AccessDirection, dict[str, Any]]:
        if not allowed:
            return AccessDirection.DENIED, {
                "source": "access_denied",
                "direction": AccessDirection.DENIED.value,
                "restart_backfill": True,
            }
        if visitor_pass_mode == "departure":
            return AccessDirection.EXIT, {
                "source": "visitor_pass_presence",
                "direction": AccessDirection.EXIT.value,
                "restart_backfill": True,
            }
        if visitor_pass_mode == "arrival":
            return AccessDirection.ENTRY, {
                "source": "visitor_pass_presence",
                "direction": AccessDirection.ENTRY.value,
                "restart_backfill": True,
            }
        gate_observation, closed_at = await _gate_observation_for_backfill(session, captured_at)
        gate_observation_payload = _gate_observation_payload(gate_observation, closed_at=closed_at)
        presence = await session.get(Presence, person.id) if person else None
        decision = self._movement_direction_fsm.resolve(
            MovementIntent(
                source=self.source,
                captured_at=captured_at,
                registration_number="",
                allowed=allowed,
                person_known=person is not None,
                gate_state=_gate_state_from_observation(gate_observation),
                gate_observation=gate_observation_payload or {},
                presence_state=presence.state if presence else None,
            )
        )
        resolution = {
            **decision.resolution,
            "direction": decision.direction.value,
            "restart_backfill": self.source == MISSED_EVENT_BACKFILL_SOURCE,
            "reconciliation": self.source == MISSED_EVENT_RECONCILIATION_SOURCE,
        }
        if gate_observation and resolution.get("source") == "gate_state":
            resolution["source"] = "protect_event_backfill_with_gate_state"
        elif not person and resolution.get("source") == "default_entry_no_person":
            resolution["source"] = "restart_backfill_default"
        elif person and resolution.get("source") == "presence":
            resolution["source"] = "presence_state_at_backfill"
            resolution["previous_presence_state"] = presence.state.value if presence else None
        return decision.direction, resolution

    def _audit_backfill_action(self) -> str:
        if self.source == MISSED_EVENT_RECONCILIATION_SOURCE:
            return "access_event.reconciled"
        return "access_event.restart_backfilled"

    def _backfill_payload_source(self) -> str:
        if self.source == MISSED_EVENT_RECONCILIATION_SOURCE:
            return "protect_reconciliation"
        return "startup_reconciliation"

    async def _prepare_protect_thumbnail(
        self, claim: HistoricalClaim, token: uuid.UUID, candidate: ProtectBackfillCandidate
    ) -> dict[str, Any]:
        relative_path = access_event_snapshot_relative_path(claim.event_id, claim_token=token)
        try:
            media = await get_unifi_protect_service().event_thumbnail(candidate.protect_event_id,
                width=SNAPSHOT_WIDTH, height=SNAPSHOT_HEIGHT)
            metadata = await get_snapshot_manager().store_image(media.content,
                relative_path=relative_path, url=access_event_snapshot_url(claim.event_id),
                camera=candidate.camera_id or candidate.camera_name or "unifi_protect_event",
                captured_at=candidate.captured_at)
            return {"status":"ready", **metadata.as_payload()}
        except (OSError, SnapshotError, UnifiProtectError):
            return {"status":"unavailable", "relative_path":relative_path, "reason":"historical_thumbnail_unavailable"}

    def _apply_prepared_thumbnail(
        self, event: AccessEvent, candidate: ProtectBackfillCandidate, media: dict[str, Any]
    ) -> None:
        if media["status"] == "ready":
            captured_at = _parse_datetime(media["captured_at"])
            if captured_at is None:
                raise ValueError("Historical snapshot capture time is unavailable")
            metadata = SnapshotMetadata(relative_path=media["relative_path"], url=media["url"],
                camera=media["camera"], captured_at=captured_at, content_type=media["content_type"],
                bytes=media["bytes"], width=media["width"], height=media["height"])
            apply_snapshot_to_access_event(event, metadata)
        else:
            event.raw_payload = {**(event.raw_payload or {}), "snapshot_backfill":{
                "status":"unavailable", "protect_event_id":candidate.protect_event_id,
                "reason":"historical_thumbnail_unavailable"}}


def backfill_window_start(
    previous_runtime_state: dict[str, Any],
    startup_at: datetime,
    *,
    latest_event_at: datetime | None = None,
) -> datetime:
    startup = _aware_utc(startup_at)
    previous_anchor = (
        _parse_datetime(previous_runtime_state.get("stopped_at"))
        or _parse_datetime(previous_runtime_state.get("last_heartbeat_at"))
        or _parse_datetime(previous_runtime_state.get("started_at"))
    )
    if previous_anchor and previous_anchor <= startup:
        anchor = previous_anchor
    elif latest_event_at:
        anchor = _aware_utc(latest_event_at)
    else:
        anchor = startup - MISSED_EVENT_BACKFILL_DEFAULT_LOOKBACK

    if anchor > startup:
        anchor = startup - MISSED_EVENT_BACKFILL_DEFAULT_LOOKBACK

    lower_bound = startup - MISSED_EVENT_BACKFILL_MAX_LOOKBACK
    anchor = max(anchor, lower_bound)
    return anchor - MISSED_EVENT_BACKFILL_OVERLAP


def protect_event_ids_from_payload(payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    return set(
        payload_values(payload, ("eventId", "event_id", "protect_event_id", "protect_event_ids"))
    )


def _camera_ids_from_access_event(event: AccessEvent) -> set[str]:
    payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    return set(payload_values(payload, ("cameraId", "camera_id", "sensorId", "sensor_id")))


def _candidate_ocr_variants(candidate: ProtectBackfillCandidate) -> list[str]:
    values = {
        candidate.registration_number,
        str(candidate.track_candidate.get("registration_number") or ""),
        str(candidate.track_candidate.get("raw_value") or ""),
        str(candidate.track_candidate.get("plate") or ""),
    }
    normalized_values = {normalize_registration_number(value) for value in values if value}
    values.update(normalized_values)
    return sorted(value for value in values if str(value or "").strip())


def _direction_resolution_gate_state(direction_resolution: dict[str, Any]) -> str | None:
    gate_observation = direction_resolution.get("gate_observation")
    if isinstance(gate_observation, dict):
        state = gate_observation.get("state")
        if state:
            return str(state)
    state = direction_resolution.get("gate_state")
    return str(state) if state else None


def _best_track_candidate(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: tuple[float, datetime, dict[str, Any]] | None = None
    for observation in observations:
        registration_number = normalize_registration_number(
            str(observation.get("registration_number") or observation.get("raw_value") or "")
        )
        if not registration_number:
            continue
        candidate = dict(observation)
        candidate["registration_number"] = registration_number
        confidence = _confidence_ratio(candidate.get("confidence"))
        captured_at = _parse_datetime(candidate.get("captured_at")) or datetime.min.replace(
            tzinfo=UTC
        )
        score = (confidence, captured_at)
        if best is None or score > (best[0], best[1]):
            best = (confidence, captured_at, candidate)
    return best[2] if best else None


def _build_backfill_anomalies(
    event: AccessEvent,
    person: Person | None,
    vehicle: Vehicle | None,
    allowed: bool,
    *,
    visitor_pass: VisitorPass | None = None,
) -> list[Anomaly]:
    if not vehicle:
        if visitor_pass:
            return []
        return [
            Anomaly(
                event=event,
                anomaly_type=AnomalyType.UNAUTHORIZED_PLATE,
                severity=AnomalySeverity.WARNING,
                message="Unauthorised Plate, Access Denied",
                context={
                    key: value
                    for key, value in {
                        "registration_number": event.registration_number,
                        "snapshot": alert_snapshot_metadata_from_event(event),
                        "backfilled": True,
                    }.items()
                    if value is not None
                },
            )
        ]
    if not allowed:
        subject = person.display_name if person else event.registration_number
        return [
            Anomaly(
                event=event,
                anomaly_type=AnomalyType.OUTSIDE_SCHEDULE,
                severity=AnomalySeverity.WARNING,
                message=f"{subject} was denied by schedule or access policy.",
                context={
                    "person_id": str(person.id) if person else None,
                    "vehicle_id": str(vehicle.id),
                    "backfilled": True,
                },
            )
        ]
    return []


async def _gate_observation_for_backfill(
    session: AsyncSession,
    captured_at: datetime,
) -> tuple[GateStateObservation | None, datetime | None]:
    captured = _aware_utc(captured_at)
    observations = (
        await session.scalars(
            select(GateStateObservation)
            .where(
                GateStateObservation.observed_at >= captured - timedelta(minutes=5),
                GateStateObservation.observed_at <= captured + timedelta(minutes=5),
            )
            .order_by(GateStateObservation.observed_at)
        )
    ).all()
    if not observations:
        return None, None

    open_states = {"open", "opening", "closing"}
    open_observations = [row for row in observations if str(row.state or "").lower() in open_states]
    nearest = min(
        open_observations or observations,
        key=lambda row: abs((_aware_utc(row.observed_at) - captured).total_seconds()),
    )
    closed_at = next(
        (
            row.observed_at
            for row in observations
            if str(row.state or "").lower() == "closed" and _aware_utc(row.observed_at) >= captured
        ),
        None,
    )
    return nearest, closed_at


def _direction_from_gate_observation(
    observation: GateStateObservation | None,
) -> AccessDirection | None:
    if not observation:
        return None
    state = str(observation.state or "").lower()
    if state == "closed":
        return AccessDirection.ENTRY
    if state in {"open", "opening", "closing"}:
        return AccessDirection.EXIT
    return None


def _gate_state_from_observation(observation: GateStateObservation | None) -> GateState:
    if not observation:
        return GateState.UNKNOWN
    try:
        return GateState(str(observation.state or "").lower())
    except ValueError:
        return GateState.UNKNOWN


def _gate_observation_payload(
    observation: GateStateObservation | None,
    *,
    closed_at: datetime | None = None,
) -> dict[str, Any] | None:
    if not observation:
        return None
    payload = {
        "state": observation.state,
        "raw_state": observation.raw_state,
        "previous_state": observation.previous_state,
        "source": observation.source,
        "gate_name": observation.gate_name,
        "gate_entity_id": observation.gate_entity_id,
        "observed_at": _aware_utc(observation.observed_at).isoformat(),
        "state_changed_at": _aware_utc(observation.state_changed_at).isoformat()
        if observation.state_changed_at
        else None,
        "closed_at": _aware_utc(closed_at).isoformat() if closed_at else None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _visitor_pass_payload(
    visitor_pass: VisitorPass | None, mode: str | None
) -> dict[str, Any] | None:
    if not visitor_pass:
        return None
    return {
        "id": str(visitor_pass.id),
        "mode": mode,
        "visitor_name": visitor_pass.visitor_name,
        "pass_type": visitor_pass.pass_type.value,
        "status": visitor_pass.status.value,
    }


def _schedule_evaluation_payload(schedule_evaluation: ScheduleEvaluation | None) -> dict[str, Any]:
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
        "reason": schedule_evaluation.reason
        if schedule_evaluation
        else "No active vehicle identity matched.",
    }


def _event_might_have_plate(event: dict[str, Any]) -> bool:
    smart_types = [str(item or "").lower() for item in event.get("smart_detect_types") or []]
    if any("license" in item or "licence" in item or "plate" in item for item in smart_types):
        return True
    text = " ".join(
        [
            str(event.get("type") or ""),
            str(event.get("camera_name") or ""),
            str(event.get("camera_id") or ""),
            " ".join(str(item or "") for item in (event.get("smart_detect_types") or [])),
        ]
    ).lower()
    return any(token in text for token in ("license", "licence", "plate", "lpr"))


def _confidence_ratio(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.99
    if confidence > 1:
        confidence = confidence / 100
    return max(0.0, min(1.0, confidence))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _runtime_state_path() -> Path:
    return settings.data_dir / BACKEND_RUNTIME_STATE_RELATIVE_PATH


def _write_runtime_state(payload: dict[str, Any]) -> None:
    path = _runtime_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        os.close(descriptor)


async def _audit_backfill_result(
    result: MissedAccessEventBackfillResult,
    *,
    action: str = "access_event.restart_backfill_checked",
    actor: str = "System",
    target_label: str = "Startup restart reconciliation",
) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_ACCESS,
                action=action,
                actor=actor,
                target_entity="AccessEvent",
                target_label=target_label,
                metadata=asdict(result),
                outcome="success" if result.errors == 0 else "warning",
                level="info" if result.errors == 0 else "warning",
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - Background integration failure remains observable and recoverable.
        logger.warning("missed_access_event_backfill_audit_failed", extra={"error": str(exc)})
