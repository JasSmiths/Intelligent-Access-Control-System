from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from app.models.enums import AccessDirection
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.movement.sessions import (
    ARRIVAL_GATE_STATES,
    DEPARTURE_GATE_STATES,
    ExternalVehicleSessionMatch,
    coerce_gate_state,
    gate_observation_from_read,
    presence_evidence_payload,
)
from app.services.movement.sessions import (
    known_vehicle_plate_match_from_read as _known_vehicle_plate_match_from_read,
)

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


def _external_admission_direction_resolution(
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


def _external_admission_payload(
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


def _external_gate_open_read(
    match: ExternalVehicleSessionMatch,
    *,
    observed_at: datetime,
    gate_payload: dict[str, Any],
    external_admission: dict[str, Any],
) -> PlateRead:
    gate_observation = _gate_observation_from_gate_event(gate_payload, observed_at)
    raw_payload = {
        GATE_OBSERVATION_PAYLOAD_KEY: gate_observation,
        EXTERNAL_ADMISSION_PAYLOAD_KEY: external_admission,
        WEBHOOK_TRACE_PAYLOAD_KEY: {
            "source": EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
            "registration_number": str(
                getattr(match.session, "registration_number", "")
                or match.access_event.registration_number
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
            getattr(match.session, "registration_number", "")
            or match.access_event.registration_number
        ),
        confidence=float(getattr(match.access_event, "confidence", None) or 1.0),
        source=EXTERNAL_ADMISSION_SOURCE_GATE_STATE_CHANGED,
        captured_at=observed_at,
        raw_payload=raw_payload,
    )


def _float_from_payload(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gate_observation_from_gate_event(
    payload: dict[str, Any], observed_at: datetime
) -> dict[str, Any]:
    state = coerce_gate_state(payload.get("state")) or GateState.UNKNOWN
    return {
        "state": state.value,
        "observed_at": observed_at.isoformat(),
        "controller": payload.get("source") or "gate_state_changed",
        "entity_id": payload.get("entity_id") or payload.get("device_key"),
        "detail": "Gate opened outside IACS while an unknown vehicle was still detected at the gate.",
    }


def _movement_saga_idempotency_key(read: PlateRead) -> str:
    return f"movement:{read.source}:{read.registration_number}:{read.captured_at.isoformat()}"


def _visitor_pass_candidate_kind(read: PlateRead) -> str:
    visitor_pass_match = _visitor_pass_plate_match_from_read(read)
    if isinstance(visitor_pass_match, dict) and visitor_pass_match.get("kind") == "departure":
        return "departure"
    gate_observation = gate_observation_from_read(read)
    gate_state = coerce_gate_state(gate_observation.get("state"))
    if gate_state in DEPARTURE_GATE_STATES:
        return "departure"
    if gate_state in ARRIVAL_GATE_STATES:
        return "arrival"
    explicit = str(
        (read.raw_payload or {}).get("direction") or (read.raw_payload or {}).get("Direction") or ""
    ).lower()
    if explicit in {"exit", "leave", "departure", "out"}:
        return "departure"
    return "arrival"


def _webhook_trace_for_window(window: DebounceWindow) -> dict[str, Any]:
    first_read = getattr(window, "first_read", None)
    if first_read is None:
        first_read = min(window.reads, key=lambda read: read.captured_at)
    trace = _webhook_trace_from_read(first_read)
    trace["candidate_count"] = len(window.reads)
    trace["window_first_seen"] = window.first_seen.isoformat()
    trace["window_updated_at"] = window.updated_at.isoformat()
    return trace


def _webhook_trace_from_read(read: PlateRead) -> dict[str, Any]:
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


def lpr_ingest_id_from_read(read: PlateRead) -> uuid.UUID | None:
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
