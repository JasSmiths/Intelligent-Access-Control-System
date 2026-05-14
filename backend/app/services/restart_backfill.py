import asyncio
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Anomaly, GateStateObservation, Person, Presence, Vehicle, VisitorPass
from app.models.enums import (
    AccessDecision,
    AccessDirection,
    AnomalySeverity,
    AnomalyType,
    PresenceState,
    TimingClassification,
)
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.modules.unifi_protect.client import UnifiProtectError
from app.services.alert_snapshots import alert_snapshot_metadata_from_event
from app.services.event_bus import event_bus
from app.services.schedules import ScheduleEvaluation, evaluate_vehicle_schedule
from app.services.settings import get_runtime_config
from app.services.snapshots import (
    SNAPSHOT_HEIGHT,
    SNAPSHOT_WIDTH,
    SnapshotError,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
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
MISSED_EVENT_BACKFILL_PROTECT_LIMIT = 250
MISSED_EVENT_BACKFILL_SOURCE = "unifi_protect_restart_backfill"
MISSED_EVENT_RECONCILIATION_SOURCE = "unifi_protect_lpr_reconciliation"
MISSED_EVENT_RECONCILIATION_INTERVAL_SECONDS = 120.0
MISSED_EVENT_RECONCILIATION_INITIAL_LOOKBACK = timedelta(minutes=15)
MISSED_EVENT_RECONCILIATION_REASON = (
    "UniFi Protect retained an LPR event that did not arrive through the webhook while the backend was running."
)


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
        window_start = window_end
        await asyncio.sleep(interval_seconds)


class MissedAccessEventBackfillService:
    def __init__(
        self,
        *,
        source: str = MISSED_EVENT_BACKFILL_SOURCE,
        backfill_reason: str = "UniFi Protect retained an LPR event during a backend/container restart window.",
    ) -> None:
        self.source = source
        self.backfill_reason = backfill_reason

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

        events = await protect.list_events(
            limit=MISSED_EVENT_BACKFILL_PROTECT_LIMIT,
            event_type="smartDetectZone",
            since=window_start,
            until=startup,
        )
        events = sorted(events, key=lambda event: _parse_datetime(event.get("start")) or startup)
        result.scanned = len(events)

        for protect_event in events:
            candidate = await self._candidate_from_protect_event(protect, protect_event)
            if not candidate:
                result.skipped += 1
                continue
            if candidate.captured_at < window_start or candidate.captured_at > startup:
                result.skipped += 1
                continue
            result.lpr_candidates += 1

            try:
                created = await self._create_backfill_event(candidate)
            except Exception as exc:
                result.errors += 1
                logger.exception(
                    "missed_access_event_backfill_candidate_failed",
                    extra={
                        "protect_event_id": candidate.protect_event_id,
                        "registration_number": candidate.registration_number,
                        "error": str(exc),
                    },
                )
                continue
            if created:
                result.backfilled += 1
            else:
                result.duplicates += 1

        return result

    async def _candidate_from_protect_event(
        self,
        protect: Any,
        protect_event: dict[str, Any],
    ) -> ProtectBackfillCandidate | None:
        protect_event_id = str(protect_event.get("id") or "").strip()
        if not protect_event_id:
            return None
        if not _event_might_have_plate(protect_event):
            return None
        try:
            track = await protect.event_lpr_track(protect_event_id)
        except UnifiProtectError as exc:
            logger.debug(
                "missed_access_event_track_unavailable",
                extra={"protect_event_id": protect_event_id, "error": str(exc)},
            )
            return None

        observations = [row for row in track.get("observations", []) if isinstance(row, dict)]
        track_candidate = _best_track_candidate(observations)
        if not track_candidate:
            return None

        registration_number = normalize_registration_number(
            str(track_candidate.get("registration_number") or track_candidate.get("raw_value") or "")
        )
        captured_at = (
            _parse_datetime(track_candidate.get("captured_at"))
            or _parse_datetime((track.get("event") or {}).get("start"))
            or _parse_datetime(protect_event.get("start"))
        )
        if not registration_number or not captured_at:
            return None

        event_payload = track.get("event") if isinstance(track.get("event"), dict) else protect_event
        return ProtectBackfillCandidate(
            protect_event_id=protect_event_id,
            registration_number=registration_number,
            captured_at=_aware_utc(captured_at),
            confidence=_confidence_ratio(track_candidate.get("confidence")),
            camera_id=str(event_payload.get("camera_id") or protect_event.get("camera_id") or "") or None,
            camera_name=str(event_payload.get("camera_name") or protect_event.get("camera_name") or "") or None,
            protect_event=event_payload if isinstance(event_payload, dict) else protect_event,
            track_candidate=track_candidate,
        )

    async def _create_backfill_event(self, candidate: ProtectBackfillCandidate) -> bool:
        config = await get_runtime_config()
        async with AsyncSessionLocal() as session:
            duplicate = await self._matching_existing_event(session, candidate)
            if duplicate:
                return False

            vehicle = await self._lookup_vehicle(session, candidate.registration_number)
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
            allowed = bool((schedule_evaluation and schedule_evaluation.allowed and identity_active) or visitor_pass)
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

            await self._attach_protect_thumbnail(event, candidate)
            if visitor_pass:
                visitor_service = get_visitor_pass_service()
                if visitor_pass_mode == "arrival":
                    await visitor_service.record_arrival(session, visitor_pass, event=event, trace_id=trace.trace_id)
                elif visitor_pass_mode == "departure":
                    await visitor_service.record_departure(session, visitor_pass, event=event)
            anomalies = _build_backfill_anomalies(event, person, vehicle, allowed, visitor_pass=visitor_pass)
            session.add_all(anomalies)

            presence_updated = False
            if allowed and person:
                await _update_presence(session, person, event)
                presence_updated = True

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
            await session.commit()
            await session.refresh(event)

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
        await telemetry.flush()
        await event_bus.publish(
            "access_event.finalized",
            {
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
                "anomaly_count": len(anomalies),
                "backfilled": True,
                "backfill_source": self._backfill_payload_source(),
            },
        )
        return True

    async def _matching_existing_event(
        self,
        session: AsyncSession,
        candidate: ProtectBackfillCandidate,
    ) -> AccessEvent | None:
        rows = (
            await session.scalars(
                select(AccessEvent)
                .where(
                    AccessEvent.occurred_at >= candidate.captured_at - MISSED_EVENT_BACKFILL_DUPLICATE_WINDOW,
                    AccessEvent.occurred_at <= candidate.captured_at + MISSED_EVENT_BACKFILL_DUPLICATE_WINDOW,
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

    async def _lookup_vehicle(self, session: AsyncSession, registration_number: str) -> Vehicle | None:
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

    async def _match_visitor_pass(
        self,
        session: AsyncSession,
        candidate: ProtectBackfillCandidate,
    ) -> tuple[VisitorPass | None, str | None]:
        visitor_service = get_visitor_pass_service()
        departure = await visitor_service.find_departure_pass(
            session,
            occurred_at=candidate.captured_at,
            registration_number=candidate.registration_number,
        )
        if departure:
            return departure, "departure"
        arrival = await visitor_service.claim_active_pass(
            session,
            occurred_at=candidate.captured_at,
            registration_number=candidate.registration_number,
            actor="System",
        )
        if arrival:
            return arrival, "arrival"
        return None, None

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
        gate_direction = _direction_from_gate_observation(gate_observation)
        if gate_direction:
            return gate_direction, {
                "source": "protect_event_backfill_with_gate_state",
                "direction": gate_direction.value,
                "gate_observation": _gate_observation_payload(gate_observation, closed_at=closed_at),
                "restart_backfill": self.source == MISSED_EVENT_BACKFILL_SOURCE,
                "reconciliation": self.source == MISSED_EVENT_RECONCILIATION_SOURCE,
            }
        if not person:
            return AccessDirection.ENTRY, {
                "source": "restart_backfill_default",
                "direction": AccessDirection.ENTRY.value,
                "restart_backfill": True,
            }
        presence = await session.get(Presence, person.id)
        if presence and presence.state == PresenceState.PRESENT:
            return AccessDirection.EXIT, {
                "source": "presence_state_at_backfill",
                "direction": AccessDirection.EXIT.value,
                "previous_presence_state": presence.state.value,
                "restart_backfill": True,
            }
        return AccessDirection.ENTRY, {
            "source": "presence_state_at_backfill",
            "direction": AccessDirection.ENTRY.value,
            "previous_presence_state": presence.state.value if presence else None,
            "restart_backfill": True,
        }

    def _audit_backfill_action(self) -> str:
        if self.source == MISSED_EVENT_RECONCILIATION_SOURCE:
            return "access_event.reconciled"
        return "access_event.restart_backfilled"

    def _backfill_payload_source(self) -> str:
        if self.source == MISSED_EVENT_RECONCILIATION_SOURCE:
            return "protect_reconciliation"
        return "startup_reconciliation"

    async def _attach_protect_thumbnail(
        self,
        event: AccessEvent,
        candidate: ProtectBackfillCandidate,
    ) -> None:
        protect = get_unifi_protect_service()
        try:
            media = await protect.event_thumbnail(
                candidate.protect_event_id,
                width=SNAPSHOT_WIDTH,
                height=SNAPSHOT_HEIGHT,
            )
            metadata = await get_snapshot_manager().store_image(
                media.content,
                relative_path=access_event_snapshot_relative_path(event.id),
                url=access_event_snapshot_url(event.id),
                camera=candidate.camera_id or candidate.camera_name or "unifi_protect_event",
                captured_at=candidate.captured_at,
            )
            apply_snapshot_to_access_event(event, metadata)
        except (OSError, SnapshotError, UnifiProtectError) as exc:
            raw_payload = dict(event.raw_payload or {})
            raw_payload["snapshot_backfill"] = {
                "status": "unavailable",
                "protect_event_id": candidate.protect_event_id,
                "reason": str(exc),
                "attempted_at": datetime.now(tz=UTC).isoformat(),
            }
            event.raw_payload = raw_payload
            logger.info(
                "missed_access_event_thumbnail_unavailable",
                extra={
                    "event_id": str(event.id),
                    "protect_event_id": candidate.protect_event_id,
                    "error": str(exc),
                },
            )


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
    if anchor < lower_bound:
        anchor = lower_bound
    return anchor - MISSED_EVENT_BACKFILL_OVERLAP


def protect_event_ids_from_payload(payload: Any) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    candidates = {
        _nested_value(payload, ("best", "alarm", "triggers", 0, "eventId")),
        _nested_value(payload, ("best", "alarm", "triggers", 0, "event_id")),
        _nested_value(payload, ("best", "alarm", "eventId")),
        _nested_value(payload, ("best", "alarm", "event_id")),
        _nested_value(payload, ("best", "eventId")),
        _nested_value(payload, ("best", "event_id")),
        _nested_value(payload, ("protect_evidence", "event_id")),
        _nested_value(payload, ("backfill", "protect_event_id")),
        _nested_value(payload, ("snapshot_recovery", "protect_event_id")),
    }
    vehicle_session = payload.get("vehicle_session")
    if isinstance(vehicle_session, dict):
        protect_event_ids = vehicle_session.get("protect_event_ids")
        if isinstance(protect_event_ids, list):
            candidates.update(protect_event_ids)
    return {str(candidate).strip() for candidate in candidates if str(candidate or "").strip()}


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
        captured_at = _parse_datetime(candidate.get("captured_at")) or datetime.min.replace(tzinfo=UTC)
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
    open_observations = [
        row for row in observations if str(row.state or "").lower() in open_states
    ]
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


def _direction_from_gate_observation(observation: GateStateObservation | None) -> AccessDirection | None:
    if not observation:
        return None
    state = str(observation.state or "").lower()
    if state == "closed":
        return AccessDirection.ENTRY
    if state in {"open", "opening", "closing"}:
        return AccessDirection.EXIT
    return None


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


def _visitor_pass_payload(visitor_pass: VisitorPass | None, mode: str | None) -> dict[str, Any] | None:
    if not visitor_pass:
        return None
    return {
        "id": str(visitor_pass.id),
        "mode": mode,
        "visitor_name": visitor_pass.visitor_name,
        "pass_type": visitor_pass.pass_type.value,
        "status": visitor_pass.status.value,
    }


async def _update_presence(session: AsyncSession, person: Person, event: AccessEvent) -> None:
    latest_event = await session.scalar(
        select(AccessEvent)
        .where(
            AccessEvent.person_id == person.id,
            AccessEvent.decision == AccessDecision.GRANTED,
            AccessEvent.direction.in_([AccessDirection.ENTRY, AccessDirection.EXIT]),
        )
        .order_by(AccessEvent.occurred_at.desc(), AccessEvent.created_at.desc())
        .limit(1)
    )
    target_event = latest_event or event
    presence = await session.get(Presence, person.id)
    if not presence:
        presence = Presence(person_id=person.id)
        session.add(presence)
    presence.state = PresenceState.PRESENT if target_event.direction == AccessDirection.ENTRY else PresenceState.EXITED
    presence.last_event_id = target_event.id
    presence.last_changed_at = target_event.occurred_at


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
        "reason": schedule_evaluation.reason if schedule_evaluation else "No active vehicle identity matched.",
    }


def _event_might_have_plate(event: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(event.get("type") or ""),
            str(event.get("camera_name") or ""),
            str(event.get("camera_id") or ""),
            " ".join(str(item or "") for item in (event.get("smart_detect_types") or [])),
        ]
    ).lower()
    return any(token in text for token in ("license", "licence", "plate", "lpr", "vehicle", "smartdetect"))


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
        return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _nested_value(payload: Any, path: tuple[Any, ...]) -> Any:
    current = payload
    for item in path:
        if isinstance(item, int):
            if not isinstance(current, list) or len(current) <= item:
                return None
            current = current[item]
        else:
            if not isinstance(current, dict):
                return None
            current = current.get(item)
    return current


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
    except Exception as exc:
        logger.warning("missed_access_event_backfill_audit_failed", extra={"error": str(exc)})
