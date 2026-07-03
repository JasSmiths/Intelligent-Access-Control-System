from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, MovementSessionRecord
from app.models.enums import AccessDecision, AccessDirection
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.movement_ledger import get_movement_ledger_repository
from app.services.vehicle_visual_detections import get_vehicle_presence_tracker

GATE_OBSERVATION_PAYLOAD_KEY = "_iacs_gate_observation"
KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY = "_iacs_known_vehicle_plate_match"
VEHICLE_SESSION_PAYLOAD_KEY = "vehicle_session"
ARRIVAL_GATE_STATES = {GateState.CLOSED}
DEPARTURE_GATE_STATES = {GateState.OPEN, GateState.OPENING, GateState.CLOSING}
EXACT_PLATE_GATE_CYCLE_SUPPRESSION_SECONDS = 60.0
ARRIVAL_OCR_NOISE_SUPPRESSION_SECONDS = 45.0
ARRIVAL_OCR_NOISE_CLOCK_SKEW_SECONDS = 2.0
MAX_SUPPRESSED_SESSION_READS = 20


@dataclass
class VehicleSessionContext:
    registration_number: str
    normalized_registration_number: str
    camera_id: str | None = None
    device_id: str | None = None
    protect_event_ids: set[str] = field(default_factory=set)


@dataclass
class VehicleSessionSuppression:
    session: Any
    reason: str
    matched_by: str
    evidence: dict[str, Any] | None = None


@dataclass
class ExternalVehicleSessionMatch:
    session: Any
    access_event: AccessEvent
    evidence: dict[str, Any]
    matched_by: str


def detected_registration_number(read: PlateRead) -> str:
    match = known_vehicle_plate_match_from_read(read)
    return str(match.get("detected_registration_number") or read.registration_number) if match else read.registration_number


def candidate_registration_numbers(read: PlateRead) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for candidate in (read.registration_number, *getattr(read, "candidate_registration_numbers", ())):
        plate = normalize_registration_number(str(candidate or ""))
        if plate and plate not in seen:
            seen.add(plate)
            normalized.append(plate)
    return tuple(normalized)


def known_vehicle_plate_match_from_read(read: PlateRead) -> dict[str, Any] | None:
    match = (read.raw_payload or {}).get(KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY)
    return match if isinstance(match, dict) else None


def normalize_registration_number(registration_number: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", registration_number).upper()


def plates_are_similar(left: str, right: str, threshold: float) -> bool:
    return left == right or SequenceMatcher(a=left, b=right).ratio() >= float(threshold)


def payload_values(value: Any, keys: tuple[str, ...]) -> list[str]:
    normalized_keys = {payload_key(key) for key in keys}
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if payload_key(str(key)) in normalized_keys:
                found.extend(scalar_payload_values(item))
            found.extend(payload_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(payload_values(item, keys))
    return dedupe_strings(found)


def scalar_payload_values(value: Any) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str | int | float):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, list):
        return [item for value_item in value for item in scalar_payload_values(value_item)]
    return []


def first_payload_value(value: Any, keys: tuple[str, ...]) -> str | None:
    values = payload_values(value, keys)
    return values[0] if values else None


def event_ids_from_paths(value: Any) -> set[str]:
    ids: set[str] = set()
    for path in payload_values(value, ("eventPath", "eventLocalLink", "event_local_link")):
        match = re.search(r"/event/([^/?#]+)", path)
        if match:
            ids.add(match.group(1))
    return ids


def payload_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return dedupe_strings(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def dedupe_strings(values: Any) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def datetime_from_payload(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def gate_observation_from_read(read: PlateRead) -> dict[str, Any]:
    value = (read.raw_payload or {}).get(GATE_OBSERVATION_PAYLOAD_KEY)
    if not isinstance(value, dict):
        return {
            "state": GateState.UNKNOWN.value,
            "observed_at": None,
            "detail": "No gate observation captured.",
        }
    state = coerce_gate_state(value.get("state")) or GateState.UNKNOWN
    return {
        "state": state.value,
        "observed_at": value.get("observed_at"),
        "controller": value.get("controller"),
        "entity_id": value.get("entity_id"),
        "detail": value.get("detail"),
    }


def explicit_direction_from_read(read: PlateRead) -> AccessDirection | None:
    for key in ("direction", "Direction", "access_direction", "movement_direction"):
        value = (read.raw_payload or {}).get(key)
        direction = coerce_access_direction(value)
        if direction:
            return direction
    return None


def coerce_access_direction(value: Any) -> AccessDirection | None:
    if isinstance(value, AccessDirection):
        return value
    text = str(value or "").strip().lower()
    aliases = {"in": AccessDirection.ENTRY, "entry": AccessDirection.ENTRY, "enter": AccessDirection.ENTRY,
               "arrival": AccessDirection.ENTRY, "arrive": AccessDirection.ENTRY, "out": AccessDirection.EXIT,
               "exit": AccessDirection.EXIT, "leave": AccessDirection.EXIT, "departure": AccessDirection.EXIT}
    return aliases.get(text)


class MovementSessionService:
    def __init__(
        self,
        *,
        ledger_provider: Callable[[], Any] | None = None,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._ledger_provider = ledger_provider or get_movement_ledger_repository
        self._session_factory = session_factory or AsyncSessionLocal

    def idle_seconds(self, runtime: Any | None = None) -> float:
        configured = (
            getattr(runtime, "lpr_vehicle_session_idle_seconds", None)
            if runtime
            else settings.lpr_vehicle_session_idle_seconds
        )
        try:
            return max(10.0, float(configured if configured is not None else settings.lpr_vehicle_session_idle_seconds))
        except (TypeError, ValueError):
            return max(10.0, float(settings.lpr_vehicle_session_idle_seconds))

    def context_from_read(self, read: PlateRead) -> VehicleSessionContext:
        payload = read.raw_payload or {}
        return VehicleSessionContext(
            registration_number=read.registration_number,
            normalized_registration_number=normalize_registration_number(read.registration_number),
            camera_id=first_payload_value(payload, ("cameraId", "camera_id", "sensorId", "sensor_id")),
            device_id=first_payload_value(payload, ("device", "deviceId", "device_id")),
            protect_event_ids=set(payload_values(payload, ("eventId", "event_id"))) | event_ids_from_paths(payload),
        )

    async def suppression_for_read(self, read: PlateRead, *, runtime: Any | None = None) -> VehicleSessionSuppression | None:
        context = self.context_from_read(read)
        if not context.normalized_registration_number:
            return None
        return await self.suppression_from_ledger(read, context, self.idle_seconds(runtime), runtime=runtime)

    async def suppression_from_ledger(
        self,
        read: PlateRead,
        context: VehicleSessionContext,
        idle_seconds: float,
        *,
        runtime: Any | None = None,
    ) -> VehicleSessionSuppression | None:
        lookup_horizon = timedelta(seconds=max(idle_seconds * 3, 3600.0))
        async with self._session_factory() as session:
            rows = await self._ledger_provider().movement_sessions_for_active_read(
                session,
                source=None,
                captured_at=read.captured_at,
                lookup_horizon=lookup_horizon,
                limit=100,
        )
        for row in rows:
            if not str(row.normalized_registration_number or "").strip():
                continue
            matched_by = self._session_match(row, context, read, runtime)
            if not matched_by:
                continue
            if matched_by != "arrival_ocr_noise" and self._read_is_departure_after_entry_session(read, row):
                continue
            if self._read_is_entry_after_exit_idle_expired(read, row, idle_seconds):
                continue
            evidence = await self._presence_evidence(row, context, read, idle_seconds)
            if read.captured_at <= row.last_seen_at + timedelta(seconds=idle_seconds) or evidence:
                return VehicleSessionSuppression(
                    session=row,
                    reason="vehicle_session_already_active",
                    matched_by=f"movement_session_{matched_by}",
                    evidence=evidence,
                )
        return None

    async def annotate_suppressed_read(
        self,
        read: PlateRead,
        suppression: VehicleSessionSuppression,
        *,
        runtime: Any | None = None,
    ) -> None:
        context = self.context_from_read(read)
        session_state = suppression.session
        suppressed_payload = self.suppressed_read_payload(read, suppression)
        movement_session_id = _uuid_or_none(getattr(session_state, "id", None) or getattr(session_state, "movement_session_id", None))
        event_id = _uuid_or_none(getattr(session_state, "access_event_id", None) or getattr(session_state, "event_id", None))
        if not movement_session_id and not event_id:
            return

        async with self._session_factory() as db:
            if movement_session_id:
                row = await db.get(MovementSessionRecord, movement_session_id)
                if row:
                    await self._ledger_provider().record_movement_session_suppression(
                        db,
                        row,
                        read_captured_at=read.captured_at,
                        idle_expires_at=read.captured_at + timedelta(seconds=self.idle_seconds(runtime)),
                        protect_event_ids=context.protect_event_ids,
                        ocr_variants=ocr_variants_for_reads((read,)),
                        last_gate_state=gate_observation_from_read(read).get("state"),
                        reason=suppression.reason,
                        matched_by=suppression.matched_by,
                        presence_evidence=presence_evidence_payload(suppression.evidence) if suppression.evidence else None,
                        suppressed_read_payload=suppressed_payload,
                    )
            if event_id:
                event = await db.get(AccessEvent, event_id)
                if event:
                    event.raw_payload = self.raw_payload_with_suppressed_read(
                        event.raw_payload,
                        event_id=str(event.id),
                        occurred_at=event.occurred_at,
                        registration_number=event.registration_number,
                        read=read,
                        suppression=suppression,
                        context=context,
                        suppressed_read_payload=suppressed_payload,
                    )
            await db.commit()

    async def external_admission_candidate_for_gate_open(
        self,
        session: Any,
        *,
        opened_at: datetime,
        runtime: Any | None = None,
    ) -> ExternalVehicleSessionMatch | None:
        rows = await self._active_unknown_denied_sessions(session, opened_at=opened_at, runtime=runtime)
        return await self._external_presence_match(rows, opened_at=opened_at, runtime=runtime)

    async def external_admission_candidate_for_read(
        self,
        session: Any,
        read: PlateRead,
        *,
        runtime: Any | None = None,
    ) -> ExternalVehicleSessionMatch | None:
        rows = [
            (row, event)
            for row, event in await self._active_unknown_denied_sessions(
                session,
                opened_at=read.captured_at,
                runtime=runtime,
            )
            if self._session_matches_read(row, read)
        ]
        return await self._external_presence_match(rows, opened_at=read.captured_at, runtime=runtime)

    async def external_departure_candidate_for_read(
        self,
        session: Any,
        read: PlateRead,
        *,
        runtime: Any | None = None,
    ) -> ExternalVehicleSessionMatch | None:
        if read_direction_hint(read) != AccessDirection.EXIT:
            return None
        rows = await self._active_external_admission_sessions(session, read.captured_at, runtime=runtime)
        candidates: list[tuple[Any, AccessEvent]] = []
        for row, event in rows:
            if not self._session_matches_plate_or_event(row, read):
                continue
            if self._read_is_departure_after_entry_session(read, row):
                candidates.append((row, event))
        if not candidates:
            return None
        row, event = max(candidates, key=lambda item: getattr(item[0], "last_seen_at", read.captured_at))
        return ExternalVehicleSessionMatch(
            session=row,
            access_event=event,
            evidence={
                "source": "lpr_read",
                "source_detail": "external_admission_departure_read",
                "observed_at": read.captured_at.isoformat(),
                "registration_number": read.registration_number,
                "event_ids": sorted(self.context_from_read(read).protect_event_ids),
                "gate_state": gate_observation_from_read(read).get("state"),
            },
            matched_by="external_admission_session",
        )

    async def mark_external_session_superseded(
        self,
        session: Any,
        row: Any,
        *,
        reason: str,
        matched_by: str,
        evidence: dict[str, Any] | None = None,
        event_id: uuid.UUID | str | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        row.is_active = False
        row.last_suppressed_reason = reason
        row.last_matched_by = matched_by
        row.last_presence_evidence = presence_evidence_payload(evidence) if evidence else None
        if observed_at:
            row.last_seen_at = max(row.last_seen_at, observed_at)
        payload = {
            "reason": reason,
            "matched_by": matched_by,
            "event_id": str(event_id) if event_id else None,
            "observed_at": observed_at.isoformat() if observed_at else None,
            "presence_evidence": presence_evidence_payload(evidence) if evidence else None,
        }
        row.suppressed_reads = [*(row.suppressed_reads or []), payload][-MAX_SUPPRESSED_SESSION_READS:]
        await session.flush()

    def initial_payload(
        self,
        reads: Sequence[PlateRead],
        *,
        first_seen: datetime,
        updated_at: datetime,
        read: PlateRead,
        event: Any,
    ) -> dict[str, Any]:
        context = self.context_from_read(read)
        return {
            "id": str(event.id),
            "source": read.source,
            "registration_number": read.registration_number,
            "normalized_registration_number": context.normalized_registration_number,
            "started_at": first_seen.isoformat(),
            "last_seen_at": updated_at.isoformat(),
            "direction": event.direction.value,
            "decision": event.decision.value,
            "camera_id": context.camera_id,
            "device_id": context.device_id,
            "protect_event_ids": sorted(context.protect_event_ids),
            "ocr_variants": ocr_variants_for_reads(reads),
            "last_gate_state": gate_observation_from_read(read).get("state"),
            "suppressed_read_count": 0,
            "suppressed_reads": [],
        }

    async def remember_session(
        self,
        event: Any,
        reads: Sequence[PlateRead],
        *,
        first_seen: datetime,
        updated_at: datetime,
        read: PlateRead,
        movement_saga_id: uuid.UUID | str | None = None,
        runtime: Any | None = None,
    ) -> None:
        async with self._session_factory() as session:
            await self.remember_session_in_db(
                session,
                event,
                reads,
                first_seen=first_seen,
                updated_at=updated_at,
                read=read,
                movement_saga_id=movement_saga_id,
                runtime=runtime,
            )
            await session.commit()

    async def remember_session_in_db(
        self,
        session: Any,
        event: Any,
        reads: Sequence[PlateRead],
        *,
        first_seen: datetime,
        updated_at: datetime,
        read: PlateRead,
        movement_saga_id: uuid.UUID | str | None = None,
        runtime: Any | None = None,
    ) -> None:
        context = self.context_from_read(read)
        if not context.normalized_registration_number:
            return
        max_seconds = getattr(runtime, "lpr_debounce_max_seconds", None) if runtime else None
        max_seconds = max_seconds if max_seconds is not None else settings.lpr_debounce_max_seconds
        debounce_expires_at = first_seen + timedelta(seconds=float(max_seconds))
        gate_cycle_expires_at = debounce_expires_at
        if event.decision == AccessDecision.GRANTED:
            gate_cycle_expires_at = max(
                gate_cycle_expires_at,
                first_seen + timedelta(seconds=EXACT_PLATE_GATE_CYCLE_SUPPRESSION_SECONDS),
            )
        await self._ledger_provider().upsert_movement_session(
            session,
            session_key=f"movement-session:{event.id}",
            source=read.source,
            access_event_id=event.id,
            movement_saga_id=movement_saga_id,
            registration_number=read.registration_number,
            normalized_registration_number=context.normalized_registration_number,
            started_at=first_seen,
            last_seen_at=updated_at,
            direction=event.direction,
            decision=event.decision,
            debounce_expires_at=debounce_expires_at,
            gate_cycle_expires_at=gate_cycle_expires_at,
            idle_expires_at=updated_at + timedelta(seconds=self.idle_seconds(runtime)),
            camera_id=context.camera_id,
            device_id=context.device_id,
            protect_event_ids=context.protect_event_ids,
            ocr_variants=ocr_variants_for_reads(reads),
            last_gate_state=gate_observation_from_read(read).get("state"),
        )

    def raw_payload_with_suppressed_read(
        self,
        raw_payload: dict[str, Any] | None,
        *,
        event_id: str,
        occurred_at: datetime,
        registration_number: str,
        read: PlateRead,
        suppression: VehicleSessionSuppression,
        context: VehicleSessionContext,
        suppressed_read_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(raw_payload or {})
        vehicle_session = dict(payload.get(VEHICLE_SESSION_PAYLOAD_KEY) or {})
        vehicle_session.setdefault("id", event_id)
        vehicle_session.setdefault("started_at", occurred_at.isoformat())
        vehicle_session.setdefault("registration_number", registration_number)
        vehicle_session.setdefault("normalized_registration_number", normalize_registration_number(registration_number))
        vehicle_session["last_seen_at"] = read.captured_at.isoformat()
        vehicle_session["last_gate_state"] = gate_observation_from_read(read).get("state")
        vehicle_session["suppressed_read_count"] = int(vehicle_session.get("suppressed_read_count") or 0) + 1
        vehicle_session["last_suppressed_reason"] = suppression.reason
        vehicle_session["last_matched_by"] = suppression.matched_by
        if suppression.evidence:
            vehicle_session["last_presence_evidence"] = presence_evidence_payload(suppression.evidence)
        protect_event_ids = set(string_list(vehicle_session.get("protect_event_ids"))) | context.protect_event_ids
        vehicle_session["protect_event_ids"] = sorted(protect_event_ids)
        variants = set(string_list(vehicle_session.get("ocr_variants"))) | set(ocr_variants_for_reads((read,)))
        vehicle_session["ocr_variants"] = sorted(value for value in variants if value)
        suppressed_reads = list(vehicle_session.get("suppressed_reads") or [])
        suppressed_reads.append(suppressed_read_payload)
        vehicle_session["suppressed_reads"] = suppressed_reads[-MAX_SUPPRESSED_SESSION_READS:]
        payload[VEHICLE_SESSION_PAYLOAD_KEY] = vehicle_session
        return payload

    def suppressed_read_payload(self, read: PlateRead, suppression: VehicleSessionSuppression) -> dict[str, Any]:
        context = self.context_from_read(read)
        return {
            "registration_number": read.registration_number,
            "detected_registration_number": detected_registration_number(read),
            "captured_at": read.captured_at.isoformat(),
            "confidence": read.confidence,
            "source": read.source,
            "gate_state": gate_observation_from_read(read).get("state"),
            "reason": suppression.reason,
            "matched_by": suppression.matched_by,
            "protect_event_ids": sorted(context.protect_event_ids),
            "presence_evidence": presence_evidence_payload(suppression.evidence) if suppression.evidence else None,
        }

    def _session_match(
        self,
        session: Any,
        context: VehicleSessionContext,
        read: PlateRead,
        runtime: Any | None,
    ) -> str | None:
        if self._read_matches_different_known_vehicle(session, read):
            return None
        same_source = read.source == getattr(session, "source", None)
        session_registration = str(getattr(session, "normalized_registration_number", "") or "").strip()
        session_event_ids = set(string_list(getattr(session, "protect_event_ids", None)))
        same_plate = (
            context.normalized_registration_number == session_registration
            or self._is_similar_plate(context.normalized_registration_number, session_registration, runtime)
        )
        if same_plate:
            return "registration_number" if same_source else "cross_source_registration_number"
        if context.protect_event_ids & session_event_ids:
            return "protect_event_id" if same_source else "cross_source_protect_event_id"
        if self._read_looks_like_arrival_ocr_noise(session, context, read):
            return "arrival_ocr_noise"
        return None

    def _read_matches_different_known_vehicle(self, session: Any, read: PlateRead) -> bool:
        match = known_vehicle_plate_match_from_read(read)
        if not match:
            return False
        matched = normalize_registration_number(str(match.get("registration_number") or match.get("normalized_registration_number") or ""))
        return bool(matched and matched != str(getattr(session, "normalized_registration_number", "") or ""))

    def _read_looks_like_arrival_ocr_noise(
        self,
        session: Any,
        context: VehicleSessionContext,
        read: PlateRead,
    ) -> bool:
        started_at = getattr(session, "started_at", None)
        if not isinstance(started_at, datetime):
            return False
        return (
            getattr(session, "direction", None) == AccessDirection.ENTRY
            and getattr(session, "decision", None) == AccessDecision.GRANTED
            and explicit_direction_from_read(read) != AccessDirection.EXIT
            and self._same_camera_or_device(session, context)
            and started_at - timedelta(seconds=ARRIVAL_OCR_NOISE_CLOCK_SKEW_SECONDS)
            <= read.captured_at
            <= started_at + timedelta(seconds=ARRIVAL_OCR_NOISE_SUPPRESSION_SECONDS)
        )

    def _same_camera_or_device(self, session: Any, context: VehicleSessionContext) -> bool:
        camera_id = getattr(session, "camera_id", None)
        device_id = getattr(session, "device_id", None)
        return bool(
            (context.camera_id and camera_id and context.camera_id == camera_id)
            or (context.device_id and device_id and context.device_id == device_id)
        )

    async def _presence_evidence(
        self,
        session: Any,
        context: VehicleSessionContext,
        read: PlateRead,
        idle_seconds: float,
    ) -> dict[str, Any] | None:
        evidence = await get_vehicle_presence_tracker().recent_evidence(
            registration_number=context.registration_number,
            event_ids=context.protect_event_ids | set(string_list(getattr(session, "protect_event_ids", None))),
            camera_id=context.camera_id or getattr(session, "camera_id", None),
            device_id=context.device_id or getattr(session, "device_id", None),
            observed_at=read.captured_at,
            max_age_seconds=idle_seconds,
        )
        return None if evidence and self._presence_evidence_is_current_lpr_read(evidence, context, read) else evidence

    def _presence_evidence_is_current_lpr_read(
        self,
        evidence: dict[str, Any],
        context: VehicleSessionContext,
        read: PlateRead,
    ) -> bool:
        if evidence.get("source") != "webhook" or evidence.get("source_detail") != "ubiquiti_lpr_webhook":
            return False
        observed_at = datetime_from_payload(evidence.get("observed_at"))
        if not observed_at:
            return False
        if abs((read.captured_at.astimezone(UTC) - observed_at.astimezone(UTC)).total_seconds()) > 1.0:
            return False
        evidence_event_id = str(evidence.get("event_id") or "").strip()
        evidence_registration = normalize_registration_number(str(evidence.get("registration_number") or ""))
        return bool(
            (evidence_event_id and evidence_event_id in context.protect_event_ids)
            or (evidence_registration and evidence_registration == context.normalized_registration_number)
        )

    def _read_is_departure_after_entry_session(self, read: PlateRead, session: Any) -> bool:
        last_seen_at = getattr(session, "last_seen_at", read.captured_at)
        gate_cycle_expires_at = last_seen_at + timedelta(seconds=EXACT_PLATE_GATE_CYCLE_SUPPRESSION_SECONDS)
        return (
            getattr(session, "direction", None) == AccessDirection.ENTRY
            and read_direction_hint(read) == AccessDirection.EXIT
            and read.captured_at > (getattr(session, "gate_cycle_expires_at", None) or gate_cycle_expires_at)
        )

    def _read_is_entry_after_exit_idle_expired(
        self,
        read: PlateRead,
        session: Any,
        idle_seconds: float,
    ) -> bool:
        last_seen_at = getattr(session, "last_seen_at", read.captured_at)
        return (
            getattr(session, "direction", None) == AccessDirection.EXIT
            and read_direction_hint(read) == AccessDirection.ENTRY
            and read.captured_at > (getattr(session, "idle_expires_at", None) or last_seen_at + timedelta(seconds=idle_seconds))
        )

    def _is_similar_plate(self, left: str, right: str, runtime: Any | None = None) -> bool:
        threshold = getattr(runtime, "lpr_similarity_threshold", None) if runtime else None
        threshold = threshold if threshold is not None else settings.lpr_similarity_threshold
        return plates_are_similar(left, right, threshold)

    async def _active_unknown_denied_sessions(
        self,
        session: Any,
        *,
        opened_at: datetime,
        runtime: Any | None = None,
    ) -> list[tuple[Any, AccessEvent]]:
        rows = await self._active_sessions(session, captured_at=opened_at, runtime=runtime)
        candidates: list[tuple[Any, AccessEvent]] = []
        for row in rows:
            if getattr(row, "decision", None) != AccessDecision.DENIED:
                continue
            if getattr(row, "direction", None) != AccessDirection.DENIED:
                continue
            if coerce_gate_state(getattr(row, "last_gate_state", None)) not in ARRIVAL_GATE_STATES:
                continue
            event = await self._event_for_session(session, row)
            if not event or getattr(event, "vehicle_id", None) or getattr(event, "person_id", None):
                continue
            if getattr(event, "decision", None) != AccessDecision.DENIED:
                continue
            candidates.append((row, event))
        return candidates

    async def _active_external_admission_sessions(
        self,
        session: Any,
        captured_at: datetime,
        *,
        runtime: Any | None = None,
    ) -> list[tuple[Any, AccessEvent]]:
        rows = await self._active_sessions(session, captured_at=captured_at, runtime=runtime)
        candidates: list[tuple[Any, AccessEvent]] = []
        for row in rows:
            if getattr(row, "decision", None) != AccessDecision.GRANTED:
                continue
            if getattr(row, "direction", None) != AccessDirection.ENTRY:
                continue
            event = await self._event_for_session(session, row)
            raw_payload = getattr(event, "raw_payload", None) if event else None
            external = raw_payload.get("external_admission") if isinstance(raw_payload, dict) else None
            if isinstance(external, dict) and external.get("mode") == "arrival":
                candidates.append((row, event))
        return candidates

    async def _active_sessions(
        self,
        session: Any,
        *,
        captured_at: datetime,
        runtime: Any | None = None,
    ) -> list[Any]:
        idle_seconds = self.idle_seconds(runtime)
        return await self._ledger_provider().movement_sessions_for_active_read(
            session,
            source=None,
            captured_at=captured_at,
            lookup_horizon=timedelta(seconds=max(idle_seconds * 3, 3600.0)),
            limit=100,
        )

    async def _event_for_session(self, session: Any, row: Any) -> AccessEvent | None:
        event = getattr(row, "access_event", None) or getattr(row, "event", None)
        if event is not None:
            return event
        event_id = _uuid_or_none(getattr(row, "access_event_id", None) or getattr(row, "event_id", None))
        if not event_id:
            return None
        return await session.get(AccessEvent, event_id)

    async def _external_presence_match(
        self,
        rows: Sequence[tuple[Any, AccessEvent]],
        *,
        opened_at: datetime,
        runtime: Any | None = None,
    ) -> ExternalVehicleSessionMatch | None:
        idle_seconds = self.idle_seconds(runtime)
        tracker = get_vehicle_presence_tracker()
        scored: list[tuple[int, float, Any, AccessEvent, dict[str, Any], str]] = []
        for row, event in rows:
            event_ids = set(string_list(getattr(row, "protect_event_ids", None)))
            evidence = await tracker.recent_evidence(
                registration_number=getattr(row, "registration_number", None),
                event_ids=event_ids,
                camera_id=getattr(row, "camera_id", None),
                device_id=getattr(row, "device_id", None),
                observed_at=opened_at,
                max_age_seconds=idle_seconds,
            )
            if not evidence or not self._external_presence_evidence_is_current(
                evidence,
                observed_at=opened_at,
                max_age_seconds=idle_seconds,
            ):
                continue
            priority, matched_by = self._external_presence_match_strength(row, evidence)
            if not priority:
                continue
            age_seconds = _float_or_none(evidence.get("age_seconds")) or 999999.0
            scored.append((priority, -age_seconds, row, event, evidence, matched_by))

        if not scored:
            return None
        strong = [item for item in scored if item[0] >= 2]
        if strong:
            _priority, _age, row, event, evidence, matched_by = max(
                strong,
                key=lambda item: (item[0], item[1], getattr(item[2], "last_seen_at", opened_at)),
            )
            return ExternalVehicleSessionMatch(row, event, evidence, matched_by)
        if len(scored) == 1:
            _priority, _age, row, event, evidence, matched_by = scored[0]
            return ExternalVehicleSessionMatch(row, event, evidence, matched_by)
        return None

    def _external_presence_evidence_is_current(
        self,
        evidence: dict[str, Any],
        *,
        observed_at: datetime,
        max_age_seconds: float,
    ) -> bool:
        if evidence.get("active") is False:
            return False
        age_seconds = _float_or_none(evidence.get("age_seconds"))
        if age_seconds is not None and age_seconds > max_age_seconds:
            return False
        evidence_observed_at = datetime_from_payload(evidence.get("observed_at"))
        if evidence_observed_at:
            if (observed_at.astimezone(UTC) - evidence_observed_at.astimezone(UTC)).total_seconds() > max_age_seconds:
                return False
        return not (
            evidence.get("source") == "webhook"
            and evidence.get("source_detail") == "ubiquiti_lpr_webhook"
        )

    def _external_presence_match_strength(self, row: Any, evidence: dict[str, Any]) -> tuple[int, str]:
        row_plate = normalize_registration_number(str(getattr(row, "registration_number", "") or ""))
        evidence_plate = normalize_registration_number(str(evidence.get("registration_number") or ""))
        if row_plate and evidence_plate and row_plate == evidence_plate:
            return 3, "external_presence_registration_number"
        event_id = str(evidence.get("event_id") or "").strip()
        if event_id and event_id in set(string_list(getattr(row, "protect_event_ids", None))):
            return 2, "external_presence_protect_event_id"
        if getattr(row, "camera_id", None) and evidence.get("camera_id") == getattr(row, "camera_id", None):
            return 1, "external_presence_camera"
        if getattr(row, "device_id", None) and evidence.get("device_id") == getattr(row, "device_id", None):
            return 1, "external_presence_device"
        return 0, ""

    def _session_matches_read(self, row: Any, read: PlateRead) -> bool:
        if self._session_matches_plate_or_event(row, read):
            return True
        context = self.context_from_read(read)
        return self._same_camera_or_device(row, context)

    def _session_matches_plate_or_event(self, row: Any, read: PlateRead) -> bool:
        context = self.context_from_read(read)
        if context.normalized_registration_number and (
            context.normalized_registration_number == str(getattr(row, "normalized_registration_number", "") or "")
            or self._is_similar_plate(
                context.normalized_registration_number,
                str(getattr(row, "normalized_registration_number", "") or ""),
            )
        ):
            return True
        if context.protect_event_ids & set(string_list(getattr(row, "protect_event_ids", None))):
            return True
        return False


def read_direction_hint(read: PlateRead) -> AccessDirection | None:
    gate_state = coerce_gate_state(gate_observation_from_read(read).get("state"))
    if gate_state in ARRIVAL_GATE_STATES:
        return AccessDirection.ENTRY
    if gate_state in DEPARTURE_GATE_STATES:
        return AccessDirection.EXIT
    return explicit_direction_from_read(read)


def coerce_gate_state(value: Any) -> GateState | None:
    if isinstance(value, GateState):
        return value
    try:
        return GateState(str(value or "").lower())
    except ValueError:
        return None


def ocr_variants_for_reads(reads: Sequence[PlateRead]) -> list[str]:
    variants: set[str] = set()
    for read in reads:
        variants.add(detected_registration_number(read))
        variants.add(read.registration_number)
        variants.update(candidate_registration_numbers(read))
    return sorted(value for value in variants if value)


def presence_evidence_payload(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence:
        return {}
    keys = ("source", "source_detail", "active", "observed_at", "registration_number", "event_id", "camera_id", "device_id", "age_seconds")
    return {key: evidence.get(key) for key in keys if evidence.get(key) is not None}


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
