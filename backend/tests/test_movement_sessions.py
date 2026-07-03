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
    def __init__(self, events=None):
        self.events = events or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, row_id):
        return self.events.get(row_id)

    async def flush(self):
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
    raw_payload: dict | None = None,
    last_gate_state: str = "closed",
    idle_seconds: float = 180.0,
):
    access_event_id = uuid.uuid4()
    access_event = SimpleNamespace(
        id=access_event_id,
        registration_number=registration_number,
        direction=direction,
        decision=decision,
        confidence=1.0,
        vehicle_id=None,
        person_id=None,
        raw_payload=raw_payload or {},
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        access_event_id=access_event_id,
        event_id=access_event_id,
        access_event=access_event,
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
        last_gate_state=last_gate_state,
        suppressed_reads=[],
        suppressed_read_count=0,
        last_suppressed_reason=None,
        last_matched_by=None,
        last_presence_evidence=None,
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


@pytest.mark.asyncio
async def test_external_admission_candidate_requires_current_protect_vehicle_evidence(monkeypatch) -> None:
    denied_at = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    opened_at = denied_at + timedelta(seconds=30)
    row = session_row("UNK123", denied_at, event_id="protect-1", camera_id="gate-camera")
    service = service_for([row])

    class Tracker:
        def __init__(self, evidence):
            self.evidence = evidence

        async def recent_evidence(self, **_kwargs):
            return self.evidence

    monkeypatch.setattr(
        movement_sessions_module,
        "get_vehicle_presence_tracker",
        lambda: Tracker({
            "source": "uiprotect_event",
            "source_detail": "vehicle_detection",
            "active": True,
            "observed_at": opened_at.isoformat(),
            "event_id": "protect-1",
            "registration_number": "UNK123",
            "age_seconds": 2.0,
        }),
    )
    match = await service.external_admission_candidate_for_gate_open(SessionContext(), opened_at=opened_at, runtime=runtime())
    assert match is not None
    assert match.session is row
    assert match.matched_by == "external_presence_registration_number"

    for evidence in (
        {
            "source": "uiprotect_event",
            "source_detail": "vehicle_detection",
            "active": False,
            "observed_at": opened_at.isoformat(),
            "event_id": "protect-1",
            "registration_number": "UNK123",
        },
        {
            "source": "uiprotect_event",
            "source_detail": "vehicle_detection",
            "active": True,
            "observed_at": (opened_at - timedelta(minutes=10)).isoformat(),
            "event_id": "protect-1",
            "registration_number": "UNK123",
        },
        {
            "source": "webhook",
            "source_detail": "ubiquiti_lpr_webhook",
            "active": True,
            "observed_at": opened_at.isoformat(),
            "event_id": "protect-1",
            "registration_number": "UNK123",
        },
    ):
        monkeypatch.setattr(movement_sessions_module, "get_vehicle_presence_tracker", lambda evidence=evidence: Tracker(evidence))
        assert await service.external_admission_candidate_for_gate_open(
            SessionContext(),
            opened_at=opened_at,
            runtime=runtime(lpr_vehicle_session_idle_seconds=60.0),
        ) is None


@pytest.mark.asyncio
async def test_external_admission_camera_only_evidence_must_be_unambiguous(monkeypatch) -> None:
    denied_at = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    opened_at = denied_at + timedelta(seconds=20)
    first = session_row("UNK123", denied_at, camera_id="gate-camera")
    second = session_row("UNK456", denied_at + timedelta(seconds=2), camera_id="gate-camera")

    class CameraOnlyTracker:
        async def recent_evidence(self, **_kwargs):
            return {
                "source": "uiprotect_camera",
                "source_detail": "vehicle_detected",
                "active": True,
                "observed_at": opened_at.isoformat(),
                "camera_id": "gate-camera",
                "age_seconds": 1.0,
            }

    monkeypatch.setattr(movement_sessions_module, "get_vehicle_presence_tracker", lambda: CameraOnlyTracker())

    ambiguous = await service_for([first, second]).external_admission_candidate_for_gate_open(
        SessionContext(),
        opened_at=opened_at,
        runtime=runtime(),
    )
    single = await service_for([first]).external_admission_candidate_for_gate_open(
        SessionContext(),
        opened_at=opened_at,
        runtime=runtime(),
    )

    assert ambiguous is None
    assert single is not None
    assert single.matched_by == "external_presence_camera"


@pytest.mark.asyncio
async def test_external_departure_links_active_external_admission_and_closes_session() -> None:
    admitted_at = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    row = session_row(
        "UNK123",
        admitted_at,
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        raw_payload={"external_admission": {"mode": "arrival", "source": "gate_state_changed"}},
        last_gate_state="open",
    )
    service = service_for([row])
    departure_read = read("UNK123", admitted_at + timedelta(seconds=70), state="open", direction="exit")

    match = await service.external_departure_candidate_for_read(SessionContext(), departure_read, runtime=runtime())

    assert match is not None
    assert match.session is row
    assert match.matched_by == "external_admission_session"

    await service.mark_external_session_superseded(
        SessionContext(),
        row,
        reason="external_departure_recorded",
        matched_by=match.matched_by,
        evidence=match.evidence,
        event_id=uuid.uuid4(),
        observed_at=departure_read.captured_at,
    )

    assert row.is_active is False
    assert row.last_suppressed_reason == "external_departure_recorded"


@pytest.mark.asyncio
async def test_immediate_post_external_admission_read_is_session_noise_not_departure() -> None:
    admitted_at = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    row = session_row(
        "UNK123",
        admitted_at,
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        raw_payload={"external_admission": {"mode": "arrival", "source": "gate_state_changed"}},
        last_gate_state="open",
    )
    service = service_for([row])
    immediate_read = read("UNK123", admitted_at + timedelta(seconds=20), state="open", direction="exit")

    departure = await service.external_departure_candidate_for_read(SessionContext(), immediate_read, runtime=runtime())
    suppression = await service.suppression_for_read(immediate_read, runtime=runtime())

    assert departure is None
    assert suppression is not None
    assert suppression.reason == "vehicle_session_already_active"
