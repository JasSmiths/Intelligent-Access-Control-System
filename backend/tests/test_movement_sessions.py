from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.models.enums import AccessDecision, AccessDirection
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.movement import sessions as movement_sessions_module
from app.services.movement.sessions import (
    GATE_OBSERVATION_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    VEHICLE_SESSION_PAYLOAD_KEY,
    MovementSessionService,
    VehicleSessionSuppression,
)


class SessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class Ledger:
    def __init__(self, rows):
        self.rows = rows

    async def movement_sessions_for_active_read(self, *_args, **_kwargs):
        return self.rows


def service_for(rows) -> MovementSessionService:
    ledger = Ledger(rows)
    return MovementSessionService(ledger_provider=lambda: ledger, session_factory=SessionContext)


def runtime(**overrides):
    values = {
        "lpr_similarity_threshold": 0.78,
        "lpr_debounce_max_seconds": 6.0,
        "lpr_vehicle_session_idle_seconds": 180.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def read(
    registration_number: str,
    captured_at: datetime,
    *,
    state: str = "closed",
    source: str = "test",
    event_id: str | None = None,
    camera_id: str | None = None,
    device_id: str | None = None,
    direction: str | None = None,
    known_match: str | None = None,
) -> PlateRead:
    payload: dict = {
        GATE_OBSERVATION_PAYLOAD_KEY: {
            "state": state,
            "observed_at": captured_at.isoformat(),
            "controller": "home_assistant",
        }
    }
    if direction:
        payload["direction"] = direction
    if known_match:
        payload[KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY] = {
            "registration_number": known_match,
            "detected_registration_number": registration_number,
        }
    if event_id or camera_id or device_id:
        payload["alarm"] = {
            "eventPath": f"/protect/events/event/{event_id}" if event_id else "",
            "triggers": [{"eventId": event_id, "device": device_id, "value": registration_number}],
            "sources": [{"device": device_id}] if device_id else [],
        }
        if camera_id:
            payload["cameraId"] = camera_id
    return PlateRead(
        registration_number=registration_number,
        confidence=1.0,
        source=source,
        captured_at=captured_at,
        raw_payload=payload,
    )


def session_row(
    registration_number: str,
    started_at: datetime,
    *,
    direction: AccessDirection = AccessDirection.DENIED,
    decision: AccessDecision = AccessDecision.DENIED,
    source: str = "test",
    event_id: str | None = None,
    camera_id: str | None = None,
    device_id: str | None = None,
    idle_seconds: float = 180.0,
):
    access_event_id = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        access_event_id=access_event_id,
        event_id=access_event_id,
        source=source,
        registration_number=registration_number,
        normalized_registration_number=movement_sessions_module.normalize_registration_number(registration_number),
        started_at=started_at,
        last_seen_at=started_at,
        direction=direction,
        decision=decision,
        gate_cycle_expires_at=started_at
        + timedelta(seconds=movement_sessions_module.EXACT_PLATE_GATE_CYCLE_SUPPRESSION_SECONDS),
        idle_expires_at=started_at + timedelta(seconds=idle_seconds),
        camera_id=camera_id,
        device_id=device_id,
        protect_event_ids=[event_id] if event_id else [],
        is_active=True,
    )


@pytest.mark.asyncio
async def test_session_suppression_matches_plate_and_shared_protect_event() -> None:
    seen_at = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    row = session_row("MJ17MDZ", seen_at, event_id="event-1", device_id="camera-device")
    service = service_for([row])

    exact = await service.suppression_for_read(
        read("MJ17MDZ", seen_at + timedelta(seconds=55), event_id="event-2", device_id="camera-device"),
        runtime=runtime(),
    )
    shared_event = await service.suppression_for_read(
        read("SA73YVL", seen_at + timedelta(seconds=20), event_id="event-1"),
        runtime=runtime(),
    )

    assert exact is not None
    assert exact.reason == "vehicle_session_already_active"
    assert exact.matched_by == "movement_session_registration_number"
    assert shared_event is not None
    assert shared_event.matched_by == "movement_session_protect_event_id"


@pytest.mark.asyncio
async def test_arrival_ocr_noise_is_suppressed_only_inside_same_camera_arrival_window() -> None:
    seen_at = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    row = session_row(
        "MD25VNO",
        seen_at,
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        camera_id="gate-camera",
    )
    service = service_for([row])

    suppressed = await service.suppression_for_read(
        read(
            "ADZ5U",
            seen_at + timedelta(seconds=18),
            state="open",
            source="unifi_protect_lpr_reconciliation",
            camera_id="gate-camera",
        ),
        runtime=runtime(),
    )
    outside_window = await service.suppression_for_read(
        read("ADZ5U", seen_at + timedelta(seconds=60), camera_id="gate-camera"),
        runtime=runtime(),
    )
    explicit_exit = await service.suppression_for_read(
        read(
            "ADZ5U",
            seen_at + timedelta(seconds=18),
            source="unifi_protect_lpr_reconciliation",
            camera_id="gate-camera",
            direction="exit",
        ),
        runtime=runtime(),
    )

    assert suppressed is not None
    assert suppressed.matched_by == "movement_session_arrival_ocr_noise"
    assert outside_window is None
    assert explicit_exit is None


@pytest.mark.asyncio
async def test_session_boundaries_allow_real_departure_and_return_entry() -> None:
    seen_at = datetime(2026, 5, 2, 12, 14, 40, tzinfo=UTC)
    entry_row = session_row(
        "MD25VNO",
        seen_at,
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
    )
    exit_row = session_row(
        "AGS7X",
        seen_at,
        direction=AccessDirection.EXIT,
        decision=AccessDecision.GRANTED,
        idle_seconds=30.0,
    )

    departure = await service_for([entry_row]).suppression_for_read(
        read("MD25VNO", seen_at + timedelta(seconds=70), state=GateState.OPEN.value),
        runtime=runtime(),
    )
    return_entry = await service_for([exit_row]).suppression_for_read(
        read("AGS7X", seen_at + timedelta(minutes=52), state=GateState.CLOSED.value),
        runtime=runtime(lpr_vehicle_session_idle_seconds=30.0),
    )
    exit_linger = await service_for([exit_row]).suppression_for_read(
        read("AGS7X", seen_at + timedelta(seconds=20), state=GateState.CLOSED.value),
        runtime=runtime(lpr_vehicle_session_idle_seconds=30.0),
    )

    assert departure is None
    assert return_entry is None
    assert exit_linger is not None


@pytest.mark.asyncio
async def test_different_exact_known_vehicle_is_not_suppressed_by_active_session() -> None:
    seen_at = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    row = session_row("MJ17MDZ", seen_at, event_id="shared-event", camera_id="camera-1")

    suppression = await service_for([row]).suppression_for_read(
        read(
            "MD25VNO",
            seen_at + timedelta(seconds=10),
            event_id="shared-event",
            camera_id="camera-1",
            known_match="MD25VNO",
        ),
        runtime=runtime(),
    )

    assert suppression is None


@pytest.mark.asyncio
async def test_presence_evidence_extends_session_but_current_lpr_evidence_is_ignored(monkeypatch) -> None:
    seen_at = datetime(2026, 5, 2, 12, 14, 40, tzinfo=UTC)
    row = session_row("MJ17MDZ", seen_at, event_id="previous-event", camera_id="camera-1", idle_seconds=30.0)
    current_read = read("MJ17MDZ", seen_at + timedelta(seconds=31), event_id="current-event", camera_id="camera-1")

    class CurrentLprEvidence:
        async def recent_evidence(self, **_kwargs):
            return {
                "source": "webhook",
                "source_detail": "ubiquiti_lpr_webhook",
                "observed_at": current_read.captured_at.isoformat(),
                "event_id": "current-event",
                "registration_number": "MJ17MDZ",
            }

    class PriorCameraEvidence:
        async def recent_evidence(self, **_kwargs):
            return {
                "source": "unifi_realtime",
                "source_detail": "vehicle_detection",
                "active": True,
                "observed_at": current_read.captured_at.isoformat(),
                "event_id": "previous-event",
                "registration_number": "MJ17MDZ",
            }

    monkeypatch.setattr(movement_sessions_module, "get_vehicle_presence_tracker", lambda: CurrentLprEvidence())
    assert await service_for([row]).suppression_for_read(
        current_read,
        runtime=runtime(lpr_vehicle_session_idle_seconds=30.0),
    ) is None

    monkeypatch.setattr(movement_sessions_module, "get_vehicle_presence_tracker", lambda: PriorCameraEvidence())
    suppression = await service_for([row]).suppression_for_read(
        current_read,
        runtime=runtime(lpr_vehicle_session_idle_seconds=30.0),
    )

    assert suppression is not None
    assert suppression.evidence is not None
    assert suppression.evidence["source"] == "unifi_realtime"


def test_suppressed_read_payload_updates_session_summary() -> None:
    service = MovementSessionService()
    event_id = str(uuid.uuid4())
    occurred_at = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    suppressed_read = read("MJ17MDZ", occurred_at + timedelta(seconds=20), event_id="event-2", state="closed")
    context = service.context_from_read(suppressed_read)
    suppression = VehicleSessionSuppression(
        session=SimpleNamespace(event_id=event_id),
        reason="vehicle_session_already_active",
        matched_by="movement_session_registration_number",
        evidence={"source": "webhook", "active": True, "observed_at": suppressed_read.captured_at.isoformat()},
    )
    suppressed_payload = service.suppressed_read_payload(suppressed_read, suppression)

    payload = service.raw_payload_with_suppressed_read(
        {
            VEHICLE_SESSION_PAYLOAD_KEY: {
                "id": event_id,
                "registration_number": "MJ17MDZ",
                "normalized_registration_number": "MJ17MDZ",
                "started_at": occurred_at.isoformat(),
                "last_seen_at": occurred_at.isoformat(),
                "protect_event_ids": ["event-1"],
                "ocr_variants": ["MJ17MDZ"],
                "suppressed_read_count": 0,
                "suppressed_reads": [],
            }
        },
        event_id=event_id,
        occurred_at=occurred_at,
        registration_number="MJ17MDZ",
        read=suppressed_read,
        suppression=suppression,
        context=context,
        suppressed_read_payload=suppressed_payload,
    )

    session_payload = payload[VEHICLE_SESSION_PAYLOAD_KEY]
    assert session_payload["suppressed_read_count"] == 1
    assert session_payload["last_seen_at"] == suppressed_read.captured_at.isoformat()
    assert session_payload["protect_event_ids"] == ["event-1", "event-2"]
    assert session_payload["suppressed_reads"][0]["matched_by"] == "movement_session_registration_number"
