import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.models import AccessEvent, MovementSessionRecord
from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification
from app.modules.gate.base import GateState
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError
from app.modules.lpr.base import PlateRead
from app.services import access_events as access_events_module
from app.services.access_events import (
    AccessEventService,
    ActiveVehicleSession,
    FinalizedPlateEvent,
    GATE_MALFUNCTION_PAYLOAD_KEY,
    GATE_OBSERVATION_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    MAX_PLATE_READ_PROCESSING_ATTEMPTS,
    PROCESSING_ATTEMPT_PAYLOAD_KEY,
    VEHICLE_SESSION_PAYLOAD_KEY,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
    dvla_mot_alert_required,
    dvla_tax_alert_required,
)
from app.services.dvla import NormalizedDvlaVehicle
from app.services.gate_commands import GateCommandIntent, GateCommandOutcome
from app.services.snapshots import access_event_snapshot_relative_path
from app.services.vehicle_visual_detections import VehiclePresenceTracker


class FakeMovementLedger:
    def __init__(self) -> None:
        self.rows: list[MovementSessionRecord] = []

    async def create_movement_saga(self, _session, **kwargs):
        return SimpleNamespace(
            id=uuid.uuid4(),
            state=kwargs.get("state"),
            state_history=[],
            **{key: value for key, value in kwargs.items() if key != "state"},
        )

    async def transition_movement_saga(self, _session, saga, state, **kwargs):
        saga.state = state
        for key, value in kwargs.items():
            setattr(saga, key, value)
        return True

    async def movement_sessions_for_exact_suppression(self, _session, *, source, captured_at, limit=100):
        rows = [
            row
            for row in self.rows
            if row.source == source
            and row.started_at <= captured_at
            and (
                (row.debounce_expires_at and row.debounce_expires_at >= captured_at)
                or (row.gate_cycle_expires_at and row.gate_cycle_expires_at >= captured_at)
            )
        ]
        return sorted(rows, key=lambda row: row.started_at, reverse=True)[:limit]

    async def movement_sessions_for_active_read(self, _session, *, source, captured_at, lookup_horizon, limit=100):
        rows = [
            row
            for row in self.rows
            if row.source == source
            and row.is_active
            and row.started_at <= captured_at
            and row.last_seen_at >= captured_at - lookup_horizon
        ]
        return sorted(rows, key=lambda row: row.last_seen_at, reverse=True)[:limit]

    async def record_movement_session_suppression(
        self,
        _session,
        row,
        *,
        read_captured_at,
        idle_expires_at,
        protect_event_ids,
        ocr_variants,
        last_gate_state,
        reason,
        matched_by,
        presence_evidence,
        suppressed_read_payload,
    ):
        row.last_seen_at = max(row.last_seen_at, read_captured_at)
        row.idle_expires_at = idle_expires_at
        row.protect_event_ids = sorted(set(row.protect_event_ids or []) | set(protect_event_ids or []))
        row.ocr_variants = sorted(set(row.ocr_variants or []) | set(ocr_variants or []))
        row.last_gate_state = last_gate_state
        row.suppressed_read_count = int(row.suppressed_read_count or 0) + 1
        row.last_suppressed_reason = reason
        row.last_matched_by = matched_by
        row.last_presence_evidence = presence_evidence
        row.suppressed_reads = [*(row.suppressed_reads or []), suppressed_read_payload]


def fake_movement_ledger(service: AccessEventService) -> FakeMovementLedger:
    ledger = getattr(service, "_movement_ledger", None)
    if isinstance(ledger, FakeMovementLedger):
        return ledger
    ledger = FakeMovementLedger()
    service._movement_ledger = ledger
    return ledger


class FakePresenceSession:
    def __init__(self, state: PresenceState | None = None) -> None:
        self._state = state

    async def get(self, _model, _person_id):
        if not self._state:
            return None
        return SimpleNamespace(state=self._state)


class FakeScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeVehicleHistorySession(FakePresenceSession):
    def __init__(self, rows, state: PresenceState | None = None) -> None:
        super().__init__(state)
        self._rows = rows

    async def scalars(self, _statement):
        return FakeScalarRows(self._rows)


class FakeVisitorPassLookupResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class FakeVisitorPassLookupSession:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def execute(self, _statement):
        return FakeVisitorPassLookupResult(self._row)


class FakeNotificationService:
    def __init__(self) -> None:
        self.contexts = []

    async def notify(self, context):
        self.contexts.append(context)


def hardware_audit_subjects():
    occurred_at = datetime(2026, 5, 3, 9, 15, tzinfo=UTC)
    person = SimpleNamespace(
        id=uuid.uuid4(),
        first_name="Jason",
        last_name="Smith",
        display_name="Jason Smith",
        group=None,
        garage_door_entity_ids=["cover.main_garage_door"],
    )
    vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="PE70DHX",
        make="Tesla",
        model="Model Y",
        color="Blue",
    )
    event = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="PE70DHX",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        person_id=person.id,
        vehicle_id=vehicle.id,
        vehicle=vehicle,
        source="ubiquiti",
        timing_classification=TimingClassification.NORMAL,
        occurred_at=occurred_at,
        raw_payload={"telemetry": {"trace_id": "trace-1"}},
    )
    return event, person, vehicle


def gate_command_outcome(
    intent: GateCommandIntent,
    *,
    accepted: bool,
    state: GateState,
    detail: str | None = None,
    exception_class: str | None = None,
) -> GateCommandOutcome:
    occurred_at = datetime(2026, 5, 3, 9, 15, tzinfo=UTC)
    return GateCommandOutcome(
        intent=intent,
        accepted=accepted,
        state=state,
        detail=detail,
        mechanically_confirmed=accepted and state in {GateState.OPEN, GateState.OPENING},
        exception_class=exception_class,
        started_at=occurred_at,
        completed_at=occurred_at,
    )


def install_gate_command_outcome(monkeypatch, outcome_factory):
    class FakeCoordinator:
        async def execute_open(self, intent):
            return outcome_factory(intent)

    monkeypatch.setattr(
        access_events_module,
        "get_gate_command_coordinator",
        lambda: FakeCoordinator(),
    )


def capture_hardware_audits(monkeypatch):
    audits = []
    published = []
    timestamp = datetime(2026, 5, 3, 9, 16, tzinfo=UTC)

    class FakeAuditSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

    async def fake_write_audit_log(_session, **kwargs):
        audits.append(kwargs)
        return SimpleNamespace(
            id=uuid.uuid4(),
            timestamp=timestamp,
            category=kwargs["category"],
            action=kwargs["action"],
            actor=kwargs["actor"],
            actor_user_id=None,
            target_entity=kwargs["target_entity"],
            target_id=kwargs.get("target_id"),
            target_label=kwargs.get("target_label"),
            diff=None,
            metadata_=kwargs["metadata"],
            outcome=kwargs["outcome"],
            level=kwargs["level"],
            trace_id=None,
            request_id=None,
        )

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(access_events_module, "AsyncSessionLocal", lambda: FakeAuditSession())
    monkeypatch.setattr(access_events_module, "write_audit_log", fake_write_audit_log)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)
    return audits, published


def plate_read_with_gate_state(state: str) -> PlateRead:
    return PlateRead(
        registration_number="TEST123",
        confidence=0.98,
        source="test",
        captured_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        raw_payload={
            GATE_OBSERVATION_PAYLOAD_KEY: {
                "state": state,
                "observed_at": "2026-04-27T12:00:00+00:00",
                "controller": "home_assistant",
            }
        },
    )


def plate_read(registration_number: str, captured_at: datetime) -> PlateRead:
    return PlateRead(
        registration_number=registration_number,
        confidence=0.9,
        source="test",
        captured_at=captured_at,
        raw_payload={},
    )


def plate_read_with_gate_state_at(registration_number: str, captured_at: datetime, state: str) -> PlateRead:
    return PlateRead(
        registration_number=registration_number,
        confidence=1.0,
        source="test",
        captured_at=captured_at,
        raw_payload={
            GATE_OBSERVATION_PAYLOAD_KEY: {
                "state": state,
                "observed_at": captured_at.isoformat(),
                "controller": "home_assistant",
            }
        },
    )


def plate_read_with_gate_malfunction(
    registration_number: str,
    captured_at: datetime,
    *,
    state: str = "open",
    malfunction_id: uuid.UUID | None = None,
) -> PlateRead:
    malfunction_id = malfunction_id or uuid.uuid4()
    return PlateRead(
        registration_number=registration_number,
        confidence=1.0,
        source="test",
        captured_at=captured_at,
        raw_payload={
            GATE_OBSERVATION_PAYLOAD_KEY: {
                "state": state,
                "observed_at": captured_at.isoformat(),
                "controller": "home_assistant",
            },
            GATE_MALFUNCTION_PAYLOAD_KEY: {
                "id": str(malfunction_id),
                "gate_entity_id": "cover.top_gate",
                "gate_name": "Top Gate",
                "status": "active",
                "opened_at": (captured_at - timedelta(minutes=8)).isoformat(),
                "declared_at": (captured_at - timedelta(minutes=3)).isoformat(),
                "resolved_at": None,
                "last_gate_state": state,
            },
        },
    )


def plate_read_with_context(
    registration_number: str,
    captured_at: datetime,
    *,
    state: str = "closed",
    event_id: str | None = None,
    camera_id: str | None = None,
    device_id: str | None = None,
) -> PlateRead:
    raw_payload = {
        GATE_OBSERVATION_PAYLOAD_KEY: {
            "state": state,
            "observed_at": captured_at.isoformat(),
            "controller": "home_assistant",
        }
    }
    if event_id or camera_id or device_id:
        raw_payload["alarm"] = {
            "eventPath": f"/protect/events/event/{event_id}" if event_id else "",
            "triggers": [
                {
                    "eventId": event_id,
                    "device": device_id,
                    "value": registration_number,
                }
            ],
            "sources": [{"device": device_id}] if device_id else [],
        }
        if camera_id:
            raw_payload["cameraId"] = camera_id
    return PlateRead(
        registration_number=registration_number,
        confidence=1.0,
        source="test",
        captured_at=captured_at,
        raw_payload=raw_payload,
    )


def remember_session(
    service: AccessEventService,
    read: PlateRead,
    *,
    direction: AccessDirection = AccessDirection.DENIED,
    decision: AccessDecision = AccessDecision.DENIED,
):
    event = SimpleNamespace(
        id=uuid.uuid4(),
        direction=direction,
        decision=decision,
        source=read.source,
        occurred_at=read.captured_at,
        registration_number=read.registration_number,
    )
    remember_movement_session(service, read, event=event, direction=direction, decision=decision)
    return event


def remember_movement_session(
    service: AccessEventService,
    read: PlateRead,
    *,
    event=None,
    direction: AccessDirection = AccessDirection.DENIED,
    decision: AccessDecision = AccessDecision.DENIED,
) -> MovementSessionRecord:
    context = service._vehicle_session_context_from_read(read)
    max_seconds = service._runtime.lpr_debounce_max_seconds if service._runtime else 6.0
    idle_seconds = (
        getattr(service._runtime, "lpr_vehicle_session_idle_seconds", 180.0)
        if service._runtime
        else 180.0
    )
    if event is None:
        event = SimpleNamespace(id=uuid.uuid4())
    row = MovementSessionRecord(
        session_key=f"test-session:{event.id}",
        source=read.source,
        access_event_id=event.id,
        movement_saga_id=None,
        registration_number=read.registration_number,
        normalized_registration_number=context.normalized_registration_number,
        direction=direction,
        decision=decision,
        started_at=read.captured_at,
        last_seen_at=read.captured_at,
        debounce_expires_at=read.captured_at + timedelta(seconds=max_seconds),
        gate_cycle_expires_at=read.captured_at + timedelta(seconds=access_events_module.EXACT_PLATE_GATE_CYCLE_SUPPRESSION_SECONDS),
        idle_expires_at=read.captured_at + timedelta(seconds=idle_seconds),
        camera_id=context.camera_id,
        device_id=context.device_id,
        protect_event_ids=sorted(context.protect_event_ids),
        ocr_variants=[read.registration_number],
        last_gate_state=service._gate_observation_from_read(read).get("state"),
        suppressed_read_count=0,
        suppressed_reads=[],
        is_active=True,
    )
    fake_movement_ledger(service).rows.append(row)
    return row


def visitor_pass_departure_read(read: PlateRead) -> PlateRead:
    raw_payload = dict(read.raw_payload or {})
    raw_payload[VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY] = {
        "kind": "departure",
        "visitor_pass_id": str(uuid.uuid4()),
        "registration_number": read.registration_number,
    }
    return PlateRead(
        registration_number=read.registration_number,
        confidence=read.confidence,
        source=read.source,
        captured_at=read.captured_at,
        raw_payload=raw_payload,
    )


@pytest.mark.asyncio
async def test_worker_iteration_failure_requeues_pulled_read_and_marks_degraded(monkeypatch) -> None:
    service = AccessEventService()
    read = plate_read("AGS7X", datetime(2026, 5, 10, 8, 0, tzinfo=UTC))
    published = []

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)
    monkeypatch.setattr(service, "_sleep_until_retry", no_sleep)
    service._worker = asyncio.create_task(asyncio.sleep(30))

    try:
        await service._handle_worker_iteration_failure(
            RuntimeError("database connection reset"),
            read_for_retry=read,
        )

        retried = service._queue.get_nowait()
        assert retried.registration_number == "AGS7X"
        assert retried.raw_payload[PROCESSING_ATTEMPT_PAYLOAD_KEY] == 1
        assert service.status()["status"] == "degraded"
        assert [event_type for event_type, _payload in published] == [
            "access_event.worker_degraded",
            "plate_read.retrying",
        ]
        retry_payload = published[1][1]
        assert retry_payload["attempt"] == 1
        assert retry_payload["max_attempts"] == MAX_PLATE_READ_PROCESSING_ATTEMPTS
        assert "database connection reset" in retry_payload["error"]
    finally:
        service._worker.cancel()
        await asyncio.gather(service._worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_retry_gives_up_loudly_after_bounded_attempts(monkeypatch) -> None:
    service = AccessEventService()
    raw_payload = {PROCESSING_ATTEMPT_PAYLOAD_KEY: MAX_PLATE_READ_PROCESSING_ATTEMPTS - 1}
    read = PlateRead(
        registration_number="AGS7X",
        confidence=0.9,
        source="test",
        captured_at=datetime(2026, 5, 10, 8, 0, tzinfo=UTC),
        raw_payload=raw_payload,
    )
    published = []

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    retried = await service._retry_or_fail_read(read, RuntimeError("database unavailable"), stage="finalize")

    assert retried is False
    assert service._queue.empty()
    assert published == [
        (
            "plate_read.failed",
            {
                "registration_number": "AGS7X",
                "detected_registration_number": "AGS7X",
                "source": "test",
                "captured_at": "2026-05-10T08:00:00+00:00",
                "attempt": MAX_PLATE_READ_PROCESSING_ATTEMPTS,
                "max_attempts": MAX_PLATE_READ_PROCESSING_ATTEMPTS,
                "stage": "finalize",
                "error": "RuntimeError: database unavailable",
            },
        )
    ]


@pytest.mark.asyncio
async def test_on_site_visitor_closed_gate_reread_is_not_forced_to_departure(monkeypatch) -> None:
    service = AccessEventService()
    visitor_pass_id = uuid.uuid4()
    arrival_time = datetime(2026, 5, 2, 9, 0, tzinfo=UTC)
    read = plate_read_with_gate_state_at(
        "DP25 MOU",
        arrival_time + timedelta(minutes=5),
        "closed",
    )

    monkeypatch.setattr(
        access_events_module,
        "AsyncSessionLocal",
        lambda: FakeVisitorPassLookupSession((visitor_pass_id, arrival_time)),
    )

    matched = await service._read_with_visitor_pass_departure_match(read)

    assert matched.registration_number == "DP25 MOU"
    assert VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY not in matched.raw_payload


@pytest.mark.asyncio
async def test_on_site_visitor_departure_gate_state_marks_departure(monkeypatch) -> None:
    service = AccessEventService()
    visitor_pass_id = uuid.uuid4()
    arrival_time = datetime(2026, 5, 2, 9, 0, tzinfo=UTC)
    read = plate_read_with_gate_state_at(
        "DP25 MOU",
        arrival_time + timedelta(minutes=5),
        "open",
    )

    monkeypatch.setattr(
        access_events_module,
        "AsyncSessionLocal",
        lambda: FakeVisitorPassLookupSession((visitor_pass_id, arrival_time)),
    )

    matched = await service._read_with_visitor_pass_departure_match(read)

    assert matched.registration_number == "DP25MOU"
    assert matched.raw_payload[VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY] == {
        "kind": "departure",
        "visitor_pass_id": str(visitor_pass_id),
        "registration_number": "DP25MOU",
    }


def test_access_event_realtime_payload_includes_snapshot_metadata(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    service = AccessEventService()
    event_id = uuid.uuid4()
    captured_at = datetime(2026, 4, 30, 20, 30, tzinfo=UTC)
    event = AccessEvent(
        id=event_id,
        registration_number="BK26MKF",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        confidence=0.91,
        source="test",
        occurred_at=captured_at,
        timing_classification=TimingClassification.NORMAL,
        snapshot_path=access_event_snapshot_relative_path(event_id),
        snapshot_content_type="image/jpeg",
        snapshot_bytes=2048,
        snapshot_width=320,
        snapshot_height=180,
        snapshot_captured_at=captured_at,
        snapshot_camera="camera.gate",
    )
    snapshot_path = tmp_path / access_event_snapshot_relative_path(event_id)
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"jpeg")

    payload = service._access_event_realtime_payload(
        event,
        anomaly_count=0,
        visitor_pass=None,
        visitor_pass_mode=None,
    )

    assert payload["event_id"] == str(event_id)
    assert payload["snapshot_url"] == f"/api/v1/events/{event_id}/snapshot"
    assert payload["snapshot_captured_at"] == captured_at.isoformat()
    assert payload["snapshot_bytes"] == 2048
    assert payload["snapshot_width"] == 320
    assert payload["snapshot_height"] == 180
    assert payload["snapshot_camera"] == "camera.gate"


@pytest.mark.asyncio
async def test_capture_event_snapshot_falls_back_to_protect_thumbnail(monkeypatch: pytest.MonkeyPatch) -> None:
    service = AccessEventService()
    event_id = uuid.uuid4()
    occurred_at = datetime(2026, 5, 2, 18, 9, 22, tzinfo=UTC)
    calls: dict[str, object] = {}

    class FakeSnapshotManager:
        async def capture_access_event_snapshot(self, *_args, **_kwargs):
            raise RuntimeError("camera snapshot unavailable")

        async def store_image(self, content, *, relative_path, url, camera, captured_at):
            calls["content"] = content
            calls["relative_path"] = relative_path
            calls["url"] = url
            calls["camera"] = camera
            calls["captured_at"] = captured_at
            return SimpleNamespace(
                relative_path=relative_path,
                content_type="image/jpeg",
                bytes=1234,
                width=320,
                height=180,
                captured_at=captured_at,
                camera=camera,
            )

    class FakeProtect:
        async def event_thumbnail(self, event_id, *, width, height):
            calls["event_thumbnail"] = (event_id, width, height)
            return SimpleNamespace(content=b"jpeg", content_type="image/jpeg")

    monkeypatch.setattr(access_events_module, "get_snapshot_manager", lambda: FakeSnapshotManager())
    monkeypatch.setattr(access_events_module, "get_unifi_protect_service", lambda: FakeProtect())

    event = AccessEvent(
        id=event_id,
        registration_number="SVA673",
        direction=AccessDirection.EXIT,
        decision=AccessDecision.GRANTED,
        confidence=0.88,
        source="ubiquiti",
        occurred_at=occurred_at,
        timing_classification=TimingClassification.UNKNOWN,
        raw_payload={"best": {"alarm": {"triggers": [{"eventId": "protect-event-1"}]}}},
    )

    await service._capture_event_snapshot(event)

    assert calls["event_thumbnail"] == ("protect-event-1", 320, 180)
    assert calls["content"] == b"jpeg"
    assert calls["relative_path"] == access_event_snapshot_relative_path(event_id)
    assert event.snapshot_path == access_event_snapshot_relative_path(event_id)
    assert event.snapshot_bytes == 1234
    assert event.snapshot_camera == "camera.gate"


@pytest.mark.asyncio
async def test_closed_gate_state_resolves_allowed_read_as_entry() -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Steph")

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(),
        plate_read_with_gate_state("closed"),
        person,
        allowed=True,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "gate_state"
    assert service._automatic_open_allowed(resolution)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["open", "opening", "closing"])
async def test_non_closed_gate_state_resolves_present_person_as_exit(state: str) -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Steph")

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.PRESENT),
        plate_read_with_gate_state(state),
        person,
        allowed=True,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "gate_state"
    assert not service._automatic_open_allowed(resolution)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["open", "opening", "closing"])
async def test_non_closed_gate_state_resolves_absent_known_person_as_entry(state: str) -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Cora")

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.EXITED),
        plate_read_with_gate_state(state),
        person,
        allowed=True,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "presence_over_gate_state"
    assert resolution["gate_state_direction"] == "exit"
    assert not service._automatic_open_allowed(resolution)


@pytest.mark.asyncio
async def test_gate_malfunction_known_vehicle_last_live_exit_resolves_as_entry() -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Sylvia")
    vehicle = SimpleNamespace(id=uuid.uuid4())
    captured_at = datetime(2026, 5, 2, 18, 17, 5, tzinfo=UTC)
    previous_event = SimpleNamespace(
        id=uuid.uuid4(),
        direction=AccessDirection.EXIT,
        occurred_at=datetime(2026, 5, 2, 16, 11, 27, tzinfo=UTC),
        source="ubiquiti",
        raw_payload={},
    )

    direction, resolution = await service._resolve_direction(
        FakeVehicleHistorySession([previous_event]),
        plate_read_with_gate_malfunction("SVA673", captured_at),
        person,
        allowed=True,
        vehicle=vehicle,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "gate_malfunction_vehicle_history"
    assert resolution["previous_live_direction"] == "exit"
    assert resolution["previous_live_event_id"] == str(previous_event.id)
    assert not service._automatic_open_allowed(resolution)


@pytest.mark.asyncio
async def test_gate_malfunction_known_vehicle_last_live_entry_resolves_as_exit() -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Steph")
    vehicle = SimpleNamespace(id=uuid.uuid4())
    captured_at = datetime(2026, 5, 2, 18, 17, 5, tzinfo=UTC)
    previous_event = SimpleNamespace(
        id=uuid.uuid4(),
        direction=AccessDirection.ENTRY,
        occurred_at=captured_at - timedelta(minutes=20),
        source="ubiquiti",
        raw_payload={},
    )

    direction, resolution = await service._resolve_direction(
        FakeVehicleHistorySession([previous_event]),
        plate_read_with_gate_malfunction("PE70DHX", captured_at),
        person,
        allowed=True,
        vehicle=vehicle,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "gate_malfunction_vehicle_history"
    assert resolution["previous_live_direction"] == "entry"


@pytest.mark.asyncio
async def test_gate_malfunction_uses_latest_person_or_vehicle_history() -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Steph")
    vehicle = SimpleNamespace(id=uuid.uuid4())
    captured_at = datetime(2026, 5, 3, 15, 58, 3, tzinfo=UTC)
    older_vehicle_entry = SimpleNamespace(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        vehicle_id=vehicle.id,
        direction=AccessDirection.ENTRY,
        occurred_at=captured_at - timedelta(days=1),
        source="ubiquiti",
        raw_payload={},
    )
    latest_person_exit = SimpleNamespace(
        id=uuid.uuid4(),
        person_id=person.id,
        vehicle_id=uuid.uuid4(),
        direction=AccessDirection.EXIT,
        occurred_at=captured_at - timedelta(hours=3),
        source="ubiquiti",
        raw_payload={},
    )

    direction, resolution = await service._resolve_direction(
        FakeVehicleHistorySession([latest_person_exit, older_vehicle_entry]),
        plate_read_with_gate_malfunction("PE70DHX", captured_at),
        person,
        allowed=True,
        vehicle=vehicle,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "gate_malfunction_vehicle_history"
    assert resolution["history_lookup"] == "person_or_vehicle"
    assert resolution["previous_live_match_scope"] == "person"
    assert resolution["previous_live_event_id"] == str(latest_person_exit.id)
    assert resolution["previous_live_direction"] == "exit"


@pytest.mark.asyncio
async def test_gate_malfunction_vehicle_history_excludes_backfills_for_sylvia_case() -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Sylvia Smith")
    vehicle = SimpleNamespace(id=uuid.uuid4())
    captured_at = datetime(2026, 5, 2, 18, 17, 5, 796000, tzinfo=UTC)
    restart_backfill_entry = SimpleNamespace(
        id=uuid.uuid4(),
        direction=AccessDirection.ENTRY,
        occurred_at=datetime(2026, 5, 2, 16, 59, 23, 963000, tzinfo=UTC),
        source="unifi_protect_restart_backfill",
        raw_payload={"backfill": {"source": "startup_reconciliation"}},
    )
    live_exit = SimpleNamespace(
        id=uuid.uuid4(),
        direction=AccessDirection.EXIT,
        occurred_at=datetime(2026, 5, 2, 16, 11, 27, 297000, tzinfo=UTC),
        source="ubiquiti",
        raw_payload={},
    )

    direction, resolution = await service._resolve_direction(
        FakeVehicleHistorySession([restart_backfill_entry, live_exit]),
        plate_read_with_gate_malfunction("SVA673", captured_at),
        person,
        allowed=True,
        vehicle=vehicle,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["previous_live_event_id"] == str(live_exit.id)
    assert resolution["previous_live_direction"] == "exit"


@pytest.mark.asyncio
async def test_gate_malfunction_without_prior_live_vehicle_event_defaults_to_entry() -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Sylvia")
    vehicle = SimpleNamespace(id=uuid.uuid4())
    captured_at = datetime(2026, 5, 2, 18, 17, 5, tzinfo=UTC)

    direction, resolution = await service._resolve_direction(
        FakeVehicleHistorySession([]),
        plate_read_with_gate_malfunction("SVA673", captured_at),
        person,
        allowed=True,
        vehicle=vehicle,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "gate_malfunction_vehicle_history"
    assert resolution["previous_live_event_id"] is None


@pytest.mark.asyncio
async def test_visitor_pass_departure_match_resolves_allowed_unknown_gate_read_as_exit() -> None:
    service = AccessEventService()

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(),
        visitor_pass_departure_read(plate_read_with_gate_state("unknown")),
        person=None,
        allowed=True,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "visitor_pass_presence"


@pytest.mark.asyncio
async def test_duplicate_arrival_uses_camera_tiebreaker_as_source_of_truth(monkeypatch) -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Steph")

    async def fake_camera_tiebreaker(_read, _person):
        return {"direction": "exit", "confidence": 0.91, "reason": "Vehicle is facing away."}

    monkeypatch.setattr(service, "_resolve_duplicate_arrival_with_camera", fake_camera_tiebreaker)

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.PRESENT),
        plate_read_with_gate_state("closed"),
        person,
        allowed=True,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "camera_tiebreaker"
    assert resolution["camera_tiebreaker"]["confidence"] == 0.91


@pytest.mark.asyncio
async def test_duplicate_arrival_accepts_moderate_exit_tiebreaker(monkeypatch) -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Jason")

    async def fake_camera_tiebreaker(_read, _person):
        return {"direction": "exit", "confidence": 0.60, "reason": "Rear of vehicle is more visible."}

    monkeypatch.setattr(service, "_resolve_duplicate_arrival_with_camera", fake_camera_tiebreaker)

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.PRESENT),
        plate_read_with_gate_state("closed"),
        person,
        allowed=True,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "camera_tiebreaker"
    assert resolution["camera_tiebreaker"]["confidence"] == 0.60
    assert "camera_tiebreaker_ignored_reason" not in resolution


@pytest.mark.asyncio
async def test_duplicate_arrival_keeps_closed_gate_entry_when_camera_tiebreaker_is_uncertain(monkeypatch) -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ash")

    async def fake_camera_tiebreaker(_read, _person):
        return {"direction": "exit", "confidence": 0.54, "reason": "Vehicle might be facing away."}

    monkeypatch.setattr(service, "_resolve_duplicate_arrival_with_camera", fake_camera_tiebreaker)

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.PRESENT),
        plate_read_with_gate_state("closed"),
        person,
        allowed=True,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "gate_state"
    assert resolution["camera_tiebreaker"]["confidence"] == 0.54
    assert resolution["camera_tiebreaker_ignored_reason"] == "low_confidence"
    assert service._automatic_open_allowed(resolution)


def test_camera_direction_parser_accepts_json_and_text() -> None:
    service = AccessEventService()

    assert service._parse_camera_direction_analysis(
        '{"direction":"exit","confidence":0.87,"reason":"rear of vehicle visible"}'
    ) == ("exit", 0.87, "rear of vehicle visible")
    assert service._parse_camera_direction_analysis("The vehicle is facing towards the camera.")[0] == "entry"
    assert service._parse_camera_direction_analysis("The vehicle is away from the camera.")[0] == "exit"


def test_known_vehicle_plate_match_canonicalizes_likely_misreads() -> None:
    service = AccessEventService()
    stored = ["MD25VNO"]

    assert service._known_vehicle_plate_match("MD25VMO", stored, 0.78)["registration_number"] == "MD25VNO"
    assert service._known_vehicle_plate_match("MO25VNO", stored, 0.78)["registration_number"] == "MD25VNO"
    assert service._known_vehicle_plate_match("MD2SVNO", stored, 0.78)["registration_number"] == "MD25VNO"
    assert service._known_vehicle_plate_match("ND25VN0", stored, 0.78) is None

    exact = service._known_vehicle_plate_match("MD25VNO", stored, 0.78)
    assert exact["registration_number"] == "MD25VNO"
    assert exact["exact"] is True


@pytest.mark.asyncio
async def test_exact_known_plate_finalizes_burst_and_suppresses_trailing_noise(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=10.0,
    )
    finalized = []

    async def fake_active_vehicle_registrations():
        return ["MD25VNO"]

    async def fake_finalize_window(window):
        best_match = window.best_read.raw_payload[KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY]
        finalized.append(
            {
                "candidate_count": len(window.reads),
                "best_registration_number": window.best_read.registration_number,
                "best_detected_registration_number": best_match["detected_registration_number"],
                "best_exact": best_match["exact"],
            }
        )
        remember_movement_session(
            service,
            window.best_read,
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
        )

    async def fake_no_visitor_pass_departure_match(read):
        return read

    async def fake_no_gate_malfunction_context(read):
        return read

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_no_visitor_pass_departure_match)
    fake_movement_ledger(service)

    first_seen = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
    for offset, registration_number in enumerate(["MD25VMO", "MO25VNO"]):
        await service._handle_queued_read(plate_read(registration_number, first_seen + timedelta(seconds=offset)))

    assert finalized == []
    assert len(service._pending) == 1
    assert [read.registration_number for read in service._pending[0].reads] == ["MD25VNO", "MD25VNO"]

    await service._handle_queued_read(plate_read("MD25VNO", first_seen + timedelta(seconds=2)))

    assert finalized == [
        {
            "candidate_count": 3,
            "best_registration_number": "MD25VNO",
            "best_detected_registration_number": "MD25VNO",
            "best_exact": True,
        }
    ]
    assert service._pending == []

    await service._handle_queued_read(plate_read("MD2SVNO", first_seen + timedelta(seconds=3)))
    await service._handle_queued_read(plate_read("ND25VN0", first_seen + timedelta(seconds=4)))

    assert len(finalized) == 1
    assert service._pending == []


@pytest.mark.asyncio
async def test_exact_known_plate_candidate_inside_single_unifi_alarm_finalizes(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=10.0,
    )
    finalized = []

    async def fake_active_vehicle_registrations():
        return ["MD25VNO"]

    async def fake_finalize_window(window):
        best_match = window.best_read.raw_payload[KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY]
        finalized.append(
            {
                "candidate_count": len(window.reads),
                "best_registration_number": window.best_read.registration_number,
                "best_detected_registration_number": best_match["detected_registration_number"],
                "best_exact": best_match["exact"],
            }
        )

    async def fake_no_visitor_pass_departure_match(read):
        return read

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_no_visitor_pass_departure_match)
    fake_movement_ledger(service)

    captured_at = datetime(2026, 5, 12, 13, 31, 31, tzinfo=UTC)
    await service._handle_queued_read(
        PlateRead(
            registration_number="DX66TUA",
            confidence=1.0,
            source="ubiquiti",
            captured_at=captured_at,
            raw_payload={},
            candidate_registration_numbers=("DX66TUA", "MD25VNO"),
        )
    )

    assert finalized == [
        {
            "candidate_count": 1,
            "best_registration_number": "MD25VNO",
            "best_detected_registration_number": "MD25VNO",
            "best_exact": True,
        }
    ]
    assert service._pending == []


@pytest.mark.asyncio
async def test_exact_known_plate_suppresses_same_exit_gate_cycle_echo_after_debounce_window(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
    )
    finalized = []
    published = []

    async def fake_active_vehicle_registrations():
        return ["PE70DHX"]

    async def fake_finalize_window(window):
        finalized.append(
            {
                "candidate_count": len(window.reads),
                "best_registration_number": window.best_read.registration_number,
            }
        )
        remember_movement_session(
            service,
            window.best_read,
            direction=AccessDirection.EXIT,
            decision=AccessDecision.GRANTED,
        )
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=AccessDirection.EXIT,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_no_visitor_pass_departure_match(read):
        return read

    async def fake_no_gate_malfunction_context(read):
        return read

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_no_visitor_pass_departure_match)
    monkeypatch.setattr(service, "_read_with_gate_malfunction_context", fake_no_gate_malfunction_context)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)
    fake_movement_ledger(service)

    first_seen = datetime(2026, 5, 1, 23, 29, 41, tzinfo=UTC)
    await service._handle_queued_read(plate_read_with_gate_state_at("PE70DHX", first_seen, "open"))
    await service._handle_queued_read(plate_read_with_gate_state_at("PE70DHX", first_seen + timedelta(seconds=9), "open"))

    assert finalized == [{"candidate_count": 1, "best_registration_number": "PE70DHX"}]
    assert service._pending == []
    assert published == [
        (
            "plate_read.suppressed",
            {
                "registration_number": "PE70DHX",
                "detected_registration_number": "PE70DHX",
                "source": "test",
                "reason": "exact_known_vehicle_plate_already_resolved_in_gate_cycle",
            },
        )
    ]


@pytest.mark.asyncio
async def test_exact_known_plate_suppresses_immediate_open_gate_echo_after_entry(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
    )
    finalized = []
    published = []

    async def fake_active_vehicle_registrations():
        return ["PE70DHX"]

    async def fake_finalize_window(window):
        direction = AccessDirection.ENTRY if len(finalized) == 0 else AccessDirection.EXIT
        finalized.append(
            {
                "candidate_count": len(window.reads),
                "best_registration_number": window.best_read.registration_number,
                "gate_state": window.best_read.raw_payload[GATE_OBSERVATION_PAYLOAD_KEY]["state"],
                "direction": direction.value,
            }
        )
        remember_movement_session(
            service,
            window.best_read,
            direction=direction,
            decision=AccessDecision.GRANTED,
        )
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=direction,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    async def fake_no_gate_malfunction_context(read):
        return read

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_gate_malfunction_context", fake_no_gate_malfunction_context)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)
    fake_movement_ledger(service)

    first_seen = datetime(2026, 5, 1, 23, 29, 41, tzinfo=UTC)
    await service._handle_queued_read(plate_read_with_gate_state_at("PE70DHX", first_seen, "closed"))
    await service._handle_queued_read(plate_read_with_gate_state_at("PE70DHX", first_seen + timedelta(seconds=9), "open"))

    assert finalized == [
        {
            "candidate_count": 1,
            "best_registration_number": "PE70DHX",
            "gate_state": "closed",
            "direction": "entry",
        },
    ]
    assert service._pending == []
    assert published == [
        (
            "plate_read.suppressed",
            {
                "registration_number": "PE70DHX",
                "detected_registration_number": "PE70DHX",
                "source": "test",
                "reason": "exact_known_vehicle_plate_already_resolved_in_gate_cycle",
            },
        )
    ]


@pytest.mark.asyncio
async def test_exact_known_plate_allows_departure_state_after_entry_gate_cycle(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
    )
    finalized = []
    published = []

    async def fake_active_vehicle_registrations():
        return ["PE70DHX"]

    async def fake_finalize_window(window):
        direction = AccessDirection.ENTRY if len(finalized) == 0 else AccessDirection.EXIT
        finalized.append(
            {
                "candidate_count": len(window.reads),
                "best_registration_number": window.best_read.registration_number,
                "gate_state": window.best_read.raw_payload[GATE_OBSERVATION_PAYLOAD_KEY]["state"],
                "direction": direction.value,
            }
        )
        remember_movement_session(
            service,
            window.best_read,
            direction=direction,
            decision=AccessDecision.GRANTED,
        )
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=direction,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    async def fake_no_gate_malfunction_context(read):
        return read

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_gate_malfunction_context", fake_no_gate_malfunction_context)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)
    fake_movement_ledger(service)

    first_seen = datetime(2026, 5, 1, 23, 29, 41, tzinfo=UTC)
    await service._handle_queued_read(plate_read_with_gate_state_at("PE70DHX", first_seen, "closed"))
    await service._handle_queued_read(plate_read_with_gate_state_at("PE70DHX", first_seen + timedelta(seconds=65), "open"))

    assert finalized == [
        {
            "candidate_count": 1,
            "best_registration_number": "PE70DHX",
            "gate_state": "closed",
            "direction": "entry",
        },
        {
            "candidate_count": 1,
            "best_registration_number": "PE70DHX",
            "gate_state": "open",
            "direction": "exit",
        },
    ]
    assert service._pending == []
    assert published == []


@pytest.mark.asyncio
async def test_gate_malfunction_known_read_bypasses_recent_suppression(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
    )
    captured_at = datetime(2026, 5, 2, 18, 17, 5, tzinfo=UTC)
    finalized = []
    published = []

    async def fake_active_vehicle_registrations():
        return ["SVA673"]

    async def fake_gate_malfunction_context(read):
        raw_payload = dict(read.raw_payload or {})
        raw_payload[GATE_MALFUNCTION_PAYLOAD_KEY] = {
            "id": str(uuid.uuid4()),
            "gate_entity_id": "cover.top_gate",
            "gate_name": "Top Gate",
            "status": "active",
            "opened_at": (captured_at - timedelta(minutes=8)).isoformat(),
            "declared_at": (captured_at - timedelta(minutes=3)).isoformat(),
            "resolved_at": None,
            "last_gate_state": "open",
        }
        return PlateRead(
            registration_number=read.registration_number,
            confidence=read.confidence,
            source=read.source,
            captured_at=read.captured_at,
            raw_payload=raw_payload,
        )

    async def fake_finalize_window(window):
        finalized.append(window.best_read.registration_number)
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_gate_malfunction_context", fake_gate_malfunction_context)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)
    remember_session(
        service,
        plate_read_with_context("SVA673", captured_at - timedelta(seconds=30), state="open"),
        direction=AccessDirection.EXIT,
        decision=AccessDecision.GRANTED,
    )

    await service._handle_queued_read(plate_read_with_gate_state_at("SVA673", captured_at, "open"))

    assert finalized == ["SVA673"]
    assert [event_type for event_type, _payload in published] == []


@pytest.mark.asyncio
async def test_gate_malfunction_unknown_read_is_ignored_before_finalize(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
    )
    captured_at = datetime(2026, 5, 2, 18, 17, 5, tzinfo=UTC)
    ignored = []

    async def fake_active_vehicle_registrations():
        return []

    async def fake_gate_malfunction_context(_read):
        return plate_read_with_gate_malfunction("UNKNOWN1", captured_at)

    async def fake_ignore(read):
        ignored.append(read)

    async def fail_finalize(_window):
        raise AssertionError("Unknown malfunction reads must not finalize access events.")

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_gate_malfunction_context", fake_gate_malfunction_context)
    monkeypatch.setattr(service, "_ignore_unknown_gate_malfunction_read", fake_ignore)
    monkeypatch.setattr(service, "_finalize_window", fail_finalize)

    await service._handle_queued_read(plate_read_with_gate_state_at("UNKNOWN1", captured_at, "open"))

    assert len(ignored) == 1
    assert service._pending == []


@pytest.mark.asyncio
async def test_ignored_gate_malfunction_unknown_read_emits_realtime_and_audit(monkeypatch) -> None:
    service = AccessEventService()
    captured_at = datetime(2026, 5, 2, 18, 17, 5, tzinfo=UTC)
    read = plate_read_with_gate_malfunction("UNKNOWN1", captured_at)
    published = []
    audits = []

    class FakeAuditSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def commit(self):
            return None

        async def refresh(self, _row):
            return None

    async def fake_write_audit_log(_session, **kwargs):
        audits.append(kwargs)
        return SimpleNamespace(
            id=uuid.uuid4(),
            timestamp=captured_at,
            category=kwargs["category"],
            action=kwargs["action"],
            actor=kwargs["actor"],
            actor_user_id=None,
            target_entity=kwargs["target_entity"],
            target_id=None,
            target_label=kwargs["target_label"],
            diff=None,
            metadata_=kwargs["metadata"],
            outcome="success",
            level="info",
            trace_id=None,
            request_id=None,
        )

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(access_events_module, "AsyncSessionLocal", lambda: FakeAuditSession())
    monkeypatch.setattr(access_events_module, "write_audit_log", fake_write_audit_log)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    await service._ignore_unknown_gate_malfunction_read(read)

    assert published[0][0] == "plate_read.ignored"
    assert published[0][1]["reason"] == "gate_malfunction_unknown_vehicle"
    assert published[0][1]["malfunction_id"] == read.raw_payload[GATE_MALFUNCTION_PAYLOAD_KEY]["id"]
    assert audits[0]["action"] == "plate_read.gate_malfunction_ignored"
    assert audits[0]["target_entity"] == "PlateRead"
    assert audits[0]["target_label"] == "UNKNOWN1"
    assert published[1][0] == "audit.log.created"


@pytest.mark.asyncio
async def test_automatic_gate_open_writes_accepted_audit(monkeypatch) -> None:
    service = AccessEventService()
    event, person, vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)

    install_gate_command_outcome(
        monkeypatch,
        lambda intent: gate_command_outcome(
            intent,
            accepted=True,
            state=GateState.OPENING,
            detail=intent.reason,
        ),
    )

    outcome = await service._open_gate_for_event(event, person, open_garage_doors=False)

    assert outcome.accepted is True
    gate_audit = next(audit for audit in audits if audit["action"] == "gate.open.automatic")
    assert gate_audit["outcome"] == "accepted"
    assert gate_audit["target_entity"] == "Gate"
    assert gate_audit["metadata"]["source"] == "automatic_lpr_grant"
    assert gate_audit["metadata"]["access_event_id"] == str(event.id)
    assert gate_audit["metadata"]["registration_number"] == "PE70DHX"
    assert gate_audit["metadata"]["person_id"] == str(person.id)
    assert gate_audit["metadata"]["vehicle_id"] == str(vehicle.id)
    assert gate_audit["metadata"]["controller"] == access_events_module.settings.gate_controller
    assert gate_audit["metadata"]["accepted"] is True
    assert any(event_type == "audit.log.created" for event_type, _payload in published)


@pytest.mark.asyncio
async def test_automatic_gate_skip_writes_skipped_audit(monkeypatch) -> None:
    service = AccessEventService()
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)

    await service._publish_gate_open_skipped(
        event,
        {"gate_observation": {"state": "open", "observed_at": event.occurred_at.isoformat()}},
        person,
    )

    gate_audit = next(audit for audit in audits if audit["action"] == "gate.open.automatic")
    assert gate_audit["outcome"] == "skipped"
    assert gate_audit["level"] == "warning"
    assert gate_audit["metadata"]["state"] == "open"
    assert gate_audit["metadata"]["garage_doors_skipped"] is True
    assert any(event_type == "gate.open_skipped" for event_type, _payload in published)
    assert any(event_type == "audit.log.created" for event_type, _payload in published)


@pytest.mark.asyncio
async def test_automatic_gate_controller_error_writes_failed_audit(monkeypatch) -> None:
    service = AccessEventService()
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)
    notifications = FakeNotificationService()

    install_gate_command_outcome(
        monkeypatch,
        lambda intent: gate_command_outcome(
            intent,
            accepted=False,
            state=GateState.UNKNOWN,
            detail="Unsupported gate controller: missing",
            exception_class="UnsupportedModuleError",
        ),
    )
    monkeypatch.setattr(access_events_module, "get_notification_service", lambda: notifications)

    outcome = await service._open_gate_for_event(event, person, open_garage_doors=False)

    assert outcome.accepted is False
    gate_audit = next(audit for audit in audits if audit["action"] == "gate.open.automatic")
    assert gate_audit["outcome"] == "failed"
    assert gate_audit["level"] == "error"
    assert gate_audit["metadata"]["detail"] == "Unsupported gate controller: missing"
    assert any(event_type == "gate.open_failed" for event_type, _payload in published)
    assert len(notifications.contexts) == 1


@pytest.mark.asyncio
async def test_automatic_garage_door_open_writes_accepted_audit(monkeypatch) -> None:
    service = AccessEventService()
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)

    async def fake_runtime_config():
        return SimpleNamespace(
            home_assistant_garage_door_entities=[
                {"entity_id": "cover.main_garage_door", "name": "Main Garage", "enabled": True}
            ],
            home_assistant_gate_open_service="cover.open_cover",
            site_timezone="Europe/London",
            schedule_default_policy="allow",
        )

    async def fake_schedule_evaluation(*_args, **_kwargs):
        return access_events_module.ScheduleEvaluation(allowed=True, source="garage_door")

    async def fake_command_cover(_client, entity, action, reason):
        return SimpleNamespace(
            entity_id=str(entity["entity_id"]),
            name=str(entity["name"]),
            action=action,
            accepted=True,
            state="opening",
            detail=reason,
        )

    monkeypatch.setattr(access_events_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(access_events_module, "evaluate_schedule_id", fake_schedule_evaluation)
    monkeypatch.setattr(access_events_module, "command_cover", fake_command_cover)

    await service._open_garage_doors_for_event(event, person, "Automatic LPR grant")

    garage_audit = next(audit for audit in audits if audit["action"] == "garage_door.open.automatic")
    assert garage_audit["outcome"] == "accepted"
    assert garage_audit["target_id"] == "cover.main_garage_door"
    assert garage_audit["target_label"] == "Main Garage"
    assert garage_audit["metadata"]["accepted"] is True
    assert garage_audit["metadata"]["state"] == "opening"
    assert any(event_type == "garage_door.open_requested" for event_type, _payload in published)


@pytest.mark.asyncio
async def test_automatic_garage_door_schedule_denial_writes_rejected_audit(monkeypatch) -> None:
    service = AccessEventService()
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)
    notifications = FakeNotificationService()

    async def fake_runtime_config():
        return SimpleNamespace(
            home_assistant_garage_door_entities=[
                {
                    "entity_id": "cover.main_garage_door",
                    "name": "Main Garage",
                    "enabled": True,
                    "schedule_id": str(uuid.uuid4()),
                }
            ],
            home_assistant_gate_open_service="cover.open_cover",
            site_timezone="Europe/London",
            schedule_default_policy="deny",
        )

    async def fake_schedule_evaluation(*_args, **_kwargs):
        return access_events_module.ScheduleEvaluation(
            allowed=False,
            source="garage_door",
            reason="Main Garage is outside schedule.",
        )

    async def fail_command_cover(*_args, **_kwargs):
        raise AssertionError("Schedule-denied garage doors must not call Home Assistant.")

    monkeypatch.setattr(access_events_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(access_events_module, "evaluate_schedule_id", fake_schedule_evaluation)
    monkeypatch.setattr(access_events_module, "command_cover", fail_command_cover)
    monkeypatch.setattr(access_events_module, "get_notification_service", lambda: notifications)

    await service._open_garage_doors_for_event(event, person, "Automatic LPR grant")

    garage_audit = next(audit for audit in audits if audit["action"] == "garage_door.open.automatic")
    assert garage_audit["outcome"] == "rejected"
    assert garage_audit["level"] == "warning"
    assert garage_audit["metadata"]["state"] == "schedule_denied"
    assert garage_audit["metadata"]["detail"] == "Main Garage is outside schedule."
    assert garage_audit["metadata"]["schedule"]["allowed"] is False
    assert any(event_type == "garage_door.open_failed" for event_type, _payload in published)
    assert len(notifications.contexts) == 1


@pytest.mark.asyncio
async def test_automatic_garage_door_command_failure_writes_failed_audit(monkeypatch) -> None:
    service = AccessEventService()
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)
    notifications = FakeNotificationService()

    async def fake_runtime_config():
        return SimpleNamespace(
            home_assistant_garage_door_entities=[
                {"entity_id": "cover.main_garage_door", "name": "Main Garage", "enabled": True}
            ],
            home_assistant_gate_open_service="cover.open_cover",
            site_timezone="Europe/London",
            schedule_default_policy="allow",
        )

    async def fake_schedule_evaluation(*_args, **_kwargs):
        return access_events_module.ScheduleEvaluation(allowed=True, source="garage_door")

    async def fake_command_cover(_client, entity, action, _reason):
        return SimpleNamespace(
            entity_id=str(entity["entity_id"]),
            name=str(entity["name"]),
            action=action,
            accepted=False,
            state="fault",
            detail="Home Assistant rejected the command.",
        )

    monkeypatch.setattr(access_events_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(access_events_module, "evaluate_schedule_id", fake_schedule_evaluation)
    monkeypatch.setattr(access_events_module, "command_cover", fake_command_cover)
    monkeypatch.setattr(access_events_module, "get_notification_service", lambda: notifications)

    await service._open_garage_doors_for_event(event, person, "Automatic LPR grant")

    garage_audit = next(audit for audit in audits if audit["action"] == "garage_door.open.automatic")
    assert garage_audit["outcome"] == "failed"
    assert garage_audit["level"] == "error"
    assert garage_audit["metadata"]["accepted"] is False
    assert garage_audit["metadata"]["detail"] == "Home Assistant rejected the command."
    assert any(event_type == "garage_door.open_failed" for event_type, _payload in published)
    assert len(notifications.contexts) == 1


@pytest.mark.asyncio
async def test_exact_known_plate_absorbs_prior_unmatched_reads_from_same_window(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=10.0,
    )
    finalized = []

    async def fake_active_vehicle_registrations():
        return ["MD25VNO"]

    async def fake_finalize_window(window):
        finalized.append(
            {
                "candidate_count": len(window.reads),
                "best_registration_number": window.best_read.registration_number,
            }
        )

    async def fake_no_visitor_pass_departure_match(read):
        return read

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_no_visitor_pass_departure_match)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    fake_movement_ledger(service)

    first_seen = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
    await service._handle_queued_read(plate_read("ND25VN0", first_seen))
    await service._handle_queued_read(plate_read("MD25VNO", first_seen + timedelta(seconds=1)))

    assert finalized == [{"candidate_count": 2, "best_registration_number": "MD25VNO"}]
    assert service._pending == []


@pytest.mark.asyncio
async def test_on_site_visitor_departure_absorbs_prior_unmatched_reads_from_same_window(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=10.0,
    )
    finalized = []
    published = []

    async def fake_active_vehicle_registrations():
        return []

    async def fake_read_with_visitor_pass_departure_match(read):
        if read.registration_number == "DP25MOU":
            return visitor_pass_departure_read(read)
        return read

    async def fake_finalize_window(window):
        finalized.append(
            {
                "candidate_count": len(window.reads),
                "best_registration_number": window.best_read.registration_number,
                "registrations": [read.registration_number for read in window.reads],
            }
        )

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_read_with_visitor_pass_departure_match)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 4, 30, 12, 23, 36, tzinfo=UTC)
    await service._handle_queued_read(plate_read("AN08OFB", first_seen))
    await service._handle_queued_read(plate_read("DP25MOU", first_seen + timedelta(seconds=2)))

    assert finalized == [
        {
            "candidate_count": 2,
            "best_registration_number": "DP25MOU",
            "registrations": ["DP25MOU", "AN08OFB"],
        }
    ]
    assert service._pending == []

    await service._handle_queued_read(plate_read("FJ73XST", first_seen + timedelta(seconds=3)))

    assert len(finalized) == 1
    assert service._pending == []
    assert published == [
        (
            "plate_read.suppressed",
            {
                "registration_number": "FJ73XST",
                "detected_registration_number": "FJ73XST",
                "source": "test",
                "reason": "visitor_pass_plate_already_resolved_in_debounce_window",
            },
        )
    ]


@pytest.mark.asyncio
async def test_vehicle_session_suppresses_stationary_denied_plate(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
    )
    published = []

    async def fake_active_vehicle_registrations():
        return []

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_read_with_visitor_pass_departure_match(read):
        return read

    async def fake_annotate_suppressed_session_read(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_read_with_visitor_pass_departure_match)
    monkeypatch.setattr(service, "_annotate_suppressed_session_read", fake_annotate_suppressed_session_read)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    remember_session(
        service,
        plate_read_with_context("MJ17MDZ", first_seen, event_id="event-1", device_id="camera-device"),
    )

    await service._handle_queued_read(
        plate_read_with_context("MJ17MDZ", first_seen + timedelta(seconds=55), event_id="event-2", device_id="camera-device")
    )

    assert service._pending == []
    assert published == [
        (
            "plate_read.suppressed",
            {
                "registration_number": "MJ17MDZ",
                "detected_registration_number": "MJ17MDZ",
                "source": "test",
                "reason": "vehicle_session_already_active",
            },
        )
    ]


@pytest.mark.asyncio
async def test_vehicle_session_suppresses_same_protect_event_ocr_variant(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
    )
    published = []

    async def fake_active_vehicle_registrations():
        return []

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_read_with_visitor_pass_departure_match(read):
        return read

    async def fake_annotate_suppressed_session_read(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_read_with_visitor_pass_departure_match)
    monkeypatch.setattr(service, "_annotate_suppressed_session_read", fake_annotate_suppressed_session_read)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    remember_session(service, plate_read_with_context("MJ17MDZ", first_seen, event_id="shared-event"))

    await service._handle_queued_read(
        plate_read_with_context("SA73YVL", first_seen + timedelta(seconds=20), event_id="shared-event")
    )

    assert service._pending == []
    suppressed = [payload for event_type, payload in published if event_type == "plate_read.suppressed"]
    assert suppressed[0]["reason"] == "vehicle_session_already_active"
    assert published[0][1]["registration_number"] == "SA73YVL"


@pytest.mark.asyncio
async def test_vehicle_session_suppresses_post_exit_closed_gate_linger(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
    )
    published = []

    async def fake_active_vehicle_registrations():
        return ["MD25VNO"]

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_annotate_suppressed_session_read(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_annotate_suppressed_session_read", fake_annotate_suppressed_session_read)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 5, 2, 12, 14, 40, tzinfo=UTC)
    remember_session(
        service,
        plate_read_with_context("MD25VNO", first_seen, state="open", event_id="exit-event"),
        direction=AccessDirection.EXIT,
        decision=AccessDecision.GRANTED,
    )

    await service._handle_queued_read(
        plate_read_with_context("MD25VNO", first_seen + timedelta(seconds=70), state="closed", event_id="later-event")
    )

    assert service._pending == []
    assert published[0][0] == "plate_read.suppressed"
    assert published[0][1]["reason"] == "vehicle_session_already_active"


@pytest.mark.asyncio
async def test_vehicle_session_allows_return_entry_after_exit_idle_expired(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=30.0,
    )
    tracker = VehiclePresenceTracker()
    finalized = []

    async def fake_active_vehicle_registrations():
        return ["AGS7X"]

    async def fake_finalize_window(window):
        finalized.append(window.best_read.registration_number)
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(access_events_module, "get_vehicle_presence_tracker", lambda: tracker)

    first_seen = datetime(2026, 5, 2, 12, 14, 40, tzinfo=UTC)
    remember_session(
        service,
        plate_read_with_context(
            "AGS7X",
            first_seen,
            state="open",
            event_id="exit-event",
            device_id="942A6FD09D64",
        ),
        direction=AccessDirection.EXIT,
        decision=AccessDecision.GRANTED,
    )

    return_read = plate_read_with_context(
        "AGS7X",
        first_seen + timedelta(minutes=52),
        state="closed",
        event_id="return-event",
        device_id="942A6FD09D64",
    )
    await tracker.record_unifi_payload(
        return_read.raw_payload,
        registration_number=return_read.registration_number,
        received_at=return_read.captured_at,
    )

    await service._handle_queued_read(return_read)

    assert finalized == ["AGS7X"]
    assert service._pending == []


@pytest.mark.asyncio
async def test_vehicle_session_ignores_current_lpr_webhook_presence_evidence_after_idle(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=30.0,
    )
    tracker = VehiclePresenceTracker()
    published = []

    async def fake_active_vehicle_registrations():
        return []

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_read_with_visitor_pass_departure_match(read):
        return read

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_read_with_visitor_pass_departure_match)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(access_events_module, "get_vehicle_presence_tracker", lambda: tracker)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 5, 2, 12, 14, 40, tzinfo=UTC)
    remember_session(
        service,
        plate_read_with_context("MJ17MDZ", first_seen, event_id="previous-event"),
    )

    read = plate_read_with_context(
        "MJ17MDZ",
        first_seen + timedelta(seconds=31),
        event_id="current-event",
    )
    await tracker.record_unifi_payload(
        read.raw_payload,
        registration_number=read.registration_number,
        received_at=read.captured_at,
    )
    published.clear()

    await service._handle_queued_read(read)

    assert [event_type for event_type, _payload in published] == []
    assert len(service._pending) == 1


@pytest.mark.asyncio
async def test_vehicle_session_allows_departure_state_after_entry(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
    )
    finalized = []
    published = []

    async def fake_active_vehicle_registrations():
        return ["MD25VNO"]

    async def fake_finalize_window(window):
        finalized.append(window.best_read.registration_number)
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=AccessDirection.EXIT,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    async def fake_no_gate_malfunction_context(read):
        return read

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_gate_malfunction_context", fake_no_gate_malfunction_context)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 5, 2, 12, 14, 40, tzinfo=UTC)
    remember_session(
        service,
        plate_read_with_context("MD25VNO", first_seen, state="closed", event_id="entry-event"),
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
    )

    await service._handle_queued_read(
        plate_read_with_context("MD25VNO", first_seen + timedelta(seconds=70), state="open", event_id="exit-event")
    )

    assert finalized == ["MD25VNO"]
    assert published == []


@pytest.mark.asyncio
async def test_vehicle_session_presence_evidence_extends_suppression_after_idle(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=30.0,
    )
    tracker = VehiclePresenceTracker()
    published = []

    async def fake_active_vehicle_registrations():
        return []

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_annotate_suppressed_session_read(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_annotate_suppressed_session_read", fake_annotate_suppressed_session_read)
    monkeypatch.setattr(access_events_module, "get_vehicle_presence_tracker", lambda: tracker)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    remember_session(
        service,
        plate_read_with_context("MJ17MDZ", first_seen, camera_id="camera-1"),
    )
    await tracker.record_unifi_realtime_payload(
        {"camera": {"id": "camera-1", "detections": {"active": ["vehicle"]}}},
        received_at=first_seen + timedelta(seconds=31),
    )

    await service._handle_queued_read(
        plate_read_with_context("MJ17MDZ", first_seen + timedelta(seconds=31), camera_id="camera-1")
    )

    assert service._pending == []
    suppressed = [payload for event_type, payload in published if event_type == "plate_read.suppressed"]
    assert suppressed[0]["reason"] == "vehicle_session_already_active"


@pytest.mark.asyncio
async def test_vehicle_session_does_not_suppress_different_exact_known_vehicle(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
    )
    finalized = []

    async def fake_active_vehicle_registrations():
        return ["MD25VNO"]

    async def fake_finalize_window(window):
        finalized.append(window.best_read.registration_number)
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)

    first_seen = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    remember_session(service, plate_read_with_context("MJ17MDZ", first_seen, event_id="shared-event", camera_id="camera-1"))

    await service._handle_queued_read(
        plate_read_with_context("MD25VNO", first_seen + timedelta(seconds=10), event_id="shared-event", camera_id="camera-1")
    )

    assert finalized == ["MD25VNO"]


@pytest.mark.asyncio
async def test_vehicle_session_idle_expiry_allows_new_event(monkeypatch) -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_quiet_seconds=2.5,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=30.0,
    )
    published = []

    async def fake_active_vehicle_registrations():
        return []

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    async def fake_read_with_visitor_pass_departure_match(read):
        return read

    async def fake_vehicle_session_db_fallback(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_read_with_visitor_pass_departure_match)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)
    monkeypatch.setattr(service, "_vehicle_session_db_fallback", fake_vehicle_session_db_fallback)

    first_seen = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    remember_session(service, plate_read_with_context("MJ17MDZ", first_seen))

    await service._handle_queued_read(
        plate_read_with_context("MJ17MDZ", first_seen + timedelta(seconds=31))
    )

    assert published == []
    assert len(service._pending) == 1


@pytest.mark.asyncio
async def test_suppressed_vehicle_session_read_updates_event_payload(monkeypatch) -> None:
    service = AccessEventService()
    event_id = uuid.uuid4()
    occurred_at = datetime(2026, 5, 2, 12, 36, 16, tzinfo=UTC)
    event = SimpleNamespace(
        id=event_id,
        occurred_at=occurred_at,
        registration_number="MJ17MDZ",
        raw_payload={
            VEHICLE_SESSION_PAYLOAD_KEY: {
                "id": str(event_id),
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
    )
    fake_db = SimpleNamespace(committed=False)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _id):
            return event

        async def commit(self):
            fake_db.committed = True

    monkeypatch.setattr(access_events_module, "AsyncSessionLocal", lambda: FakeSession())
    read = plate_read_with_context(
        "MJ17MDZ",
        occurred_at + timedelta(seconds=20),
        event_id="event-2",
        state="closed",
    )
    session = ActiveVehicleSession(
        event_id=str(event_id),
        source="test",
        registration_number="MJ17MDZ",
        normalized_registration_number="MJ17MDZ",
        started_at=occurred_at,
        last_seen_at=occurred_at,
        direction=AccessDirection.DENIED,
        decision=AccessDecision.DENIED,
        protect_event_ids={"event-1"},
    )

    await service._annotate_suppressed_session_read(
        read,
        access_events_module.VehicleSessionSuppression(
            session=session,
            reason="vehicle_session_already_active",
            matched_by="registration_number",
            evidence={"source": "webhook", "active": True, "observed_at": read.captured_at.isoformat()},
        ),
    )

    payload = event.raw_payload[VEHICLE_SESSION_PAYLOAD_KEY]
    assert fake_db.committed
    assert payload["suppressed_read_count"] == 1
    assert payload["last_seen_at"] == read.captured_at.isoformat()
    assert payload["protect_event_ids"] == ["event-1", "event-2"]
    assert payload["suppressed_reads"][0]["matched_by"] == "registration_number"


@pytest.mark.asyncio
async def test_known_arrival_uses_same_day_dvla_cache(monkeypatch) -> None:
    service = AccessEventService()
    vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="PE70DHX",
        make="Peugeot",
        color="White",
        mot_status="Valid",
        tax_status="Taxed",
        fuel_type="Electric",
        mot_expiry=date(2026, 10, 14),
        tax_expiry=date(2027, 1, 1),
        last_dvla_lookup_date=date(2026, 4, 27),
    )

    async def fail_lookup(_registration_number, **_kwargs):
        raise AssertionError("same-day cache should skip DVLA")

    monkeypatch.setattr(service, "_dvla_cache_date", lambda _timezone_name: date(2026, 4, 27))
    monkeypatch.setattr(access_events_module, "lookup_normalized_vehicle_registration", fail_lookup)

    result = await service._dvla_enrichment_for_event(
        vehicle=vehicle,
        registration_number="PE70DHX",
        direction=AccessDirection.ENTRY,
        direction_resolution={},
        runtime=SimpleNamespace(site_timezone="Europe/London"),
    )

    assert result == {
        "registration_number": "PE70DHX",
        "make": "Peugeot",
        "colour": "White",
        "fuel_type": "Electric",
        "mot_status": "Valid",
        "tax_status": "Taxed",
        "mot_expiry": "2026-10-14",
        "tax_expiry": "2027-01-01",
    }


@pytest.mark.asyncio
async def test_known_arrival_refreshes_stale_dvla_cache(monkeypatch) -> None:
    service = AccessEventService()
    vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="MD25VNO",
        make="Old",
        color="Black",
        mot_status=None,
        tax_status=None,
        mot_expiry=None,
        tax_expiry=None,
        last_dvla_lookup_date=date(2026, 4, 26),
    )
    calls = []

    async def fake_lookup(registration_number, **_kwargs):
        calls.append(registration_number)
        return NormalizedDvlaVehicle(
            registration_number=registration_number,
            make="Tesla",
            colour="Blue",
            mot_status="Expired",
            tax_status="Untaxed",
            mot_expiry=date(2026, 1, 1),
            tax_expiry=date(2026, 2, 1),
        )

    monkeypatch.setattr(service, "_dvla_cache_date", lambda _timezone_name: date(2026, 4, 27))
    monkeypatch.setattr(access_events_module, "lookup_normalized_vehicle_registration", fake_lookup)

    result = await service._dvla_enrichment_for_event(
        vehicle=vehicle,
        registration_number="MD25VNO",
        direction=AccessDirection.ENTRY,
        direction_resolution={},
        runtime=SimpleNamespace(site_timezone="Europe/London"),
    )

    assert calls == ["MD25VNO"]
    assert vehicle.make == "Tesla"
    assert vehicle.color == "Blue"
    assert vehicle.mot_status == "Expired"
    assert vehicle.tax_status == "Untaxed"
    assert vehicle.last_dvla_lookup_date == date(2026, 4, 27)
    assert result["mot_expiry"] == "2026-01-01"


@pytest.mark.asyncio
async def test_unknown_closed_gate_arrival_gets_ephemeral_dvla_payload(monkeypatch) -> None:
    service = AccessEventService()

    async def fake_lookup(registration_number, **_kwargs):
        return NormalizedDvlaVehicle(
            registration_number=registration_number,
            make="Ford",
            colour="Silver",
            mot_status="Valid",
            tax_status="Taxed",
            mot_expiry=None,
            tax_expiry=None,
        )

    monkeypatch.setattr(access_events_module, "lookup_normalized_vehicle_registration", fake_lookup)

    result = await service._dvla_enrichment_for_event(
        vehicle=None,
        registration_number="UNKNOWN1",
        direction=AccessDirection.DENIED,
        direction_resolution={"gate_observation": {"state": "closed"}},
        runtime=SimpleNamespace(site_timezone="Europe/London"),
    )

    assert result["registration_number"] == "UNKNOWN1"
    assert result["make"] == "Ford"
    assert result["colour"] == "Silver"


@pytest.mark.asyncio
async def test_exit_events_skip_dvla_lookup(monkeypatch) -> None:
    service = AccessEventService()

    async def fail_lookup(_registration_number, **_kwargs):
        raise AssertionError("exits should not call DVLA")

    monkeypatch.setattr(access_events_module, "lookup_normalized_vehicle_registration", fail_lookup)

    result = await service._dvla_enrichment_for_event(
        vehicle=None,
        registration_number="PE70DHX",
        direction=AccessDirection.EXIT,
        direction_resolution={"gate_observation": {"state": "open"}},
        runtime=SimpleNamespace(site_timezone="Europe/London"),
    )

    assert result is None


@pytest.mark.asyncio
async def test_dvla_failure_does_not_block_event_enrichment(monkeypatch) -> None:
    service = AccessEventService()
    published = []

    async def fake_lookup(_registration_number, **_kwargs):
        raise DvlaVehicleEnquiryError("DVLA API key is not configured.", status_code=400)

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(access_events_module, "lookup_normalized_vehicle_registration", fake_lookup)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    result = await service._dvla_enrichment_for_event(
        vehicle=None,
        registration_number="PE70DHX",
        direction=AccessDirection.ENTRY,
        direction_resolution={},
        runtime=SimpleNamespace(site_timezone="Europe/London"),
    )

    assert result is None
    assert published == [
        (
            "dvla.enrichment_failed",
            {
                "registration_number": "PE70DHX",
                "status_code": 400,
                "error": "DVLA API key is not configured.",
            },
        )
    ]


def test_dvla_compliance_alert_helpers() -> None:
    assert not dvla_mot_alert_required("Valid")
    assert not dvla_mot_alert_required("Not Required")
    assert dvla_mot_alert_required("Expired")
    assert not dvla_tax_alert_required("Taxed")
    assert not dvla_tax_alert_required("SORN")
    assert dvla_tax_alert_required("Untaxed")


def test_unknown_notification_facts_prefer_visual_detection_colour_over_dvla() -> None:
    service = AccessEventService()
    event = SimpleNamespace(
        id=uuid.uuid4(),
        raw_payload={
            "vehicle_visual_detection": {
                "observed_vehicle_color": "Grey",
                "observed_vehicle_type": "Car",
                "source": "uiprotect_event",
            },
            "telemetry": {"trace_id": "trace-1"},
        },
        registration_number="AB12CDE",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.DENIED,
        source="ubiquiti",
        timing_classification=TimingClassification.UNKNOWN,
        occurred_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    )

    facts = service._notification_facts(
        event,
        person=None,
        vehicle=None,
        message="Unauthorised Plate, Access Denied",
        dvla_enrichment={"make": "Tesla", "colour": "White"},
    )

    assert facts["vehicle_make"] == "Tesla"
    assert facts["vehicle_colour"] == "Grey"
    assert facts["vehicle_type"] == "Car"
    assert facts["detected_vehicle_colour"] == "Grey"


@pytest.mark.parametrize(
    ("first_name", "pronouns", "object_pronoun", "possessive_determiner"),
    [
        ("Jason", "he/him", "him", "his"),
        ("Jason", "she/her", "her", "her"),
        ("Jason", None, "him", "his"),
        ("Taylor", None, "them", "their"),
    ],
)
def test_notification_facts_use_person_pronouns(
    first_name: str,
    pronouns: str | None,
    object_pronoun: str,
    possessive_determiner: str,
) -> None:
    service = AccessEventService()
    event = SimpleNamespace(
        id=uuid.uuid4(),
        raw_payload={"telemetry": {"trace_id": "trace-1"}},
        registration_number="PE70DHX",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        source="ubiquiti",
        timing_classification=TimingClassification.NORMAL,
        occurred_at=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
    )
    person = SimpleNamespace(
        first_name=first_name,
        last_name="Smith",
        display_name=f"{first_name} Smith",
        pronouns=pronouns,
        group=SimpleNamespace(name="Family"),
    )

    facts = service._notification_facts(event, person=person, vehicle=None, message="Granted")

    assert facts["object_pronoun"] == object_pronoun
    assert facts["possessive_determiner"] == possessive_determiner


@pytest.mark.parametrize(
    ("pronouns", "expected"),
    [
        (
            "he/him",
            "Jason's Tesla Model Y has been detected at the gate. I've let him in.",
        ),
        (
            "she/her",
            "Jason's Tesla Model Y has been detected at the gate. I've let her in.",
        ),
        (
            None,
            "Jason's Tesla Model Y has been detected at the gate. I've let him in.",
        ),
    ],
)
def test_authorized_entry_message_uses_person_pronouns(pronouns: str | None, expected: str) -> None:
    service = AccessEventService()
    person = SimpleNamespace(first_name="Jason", display_name="Jason Smith", pronouns=pronouns)
    vehicle = SimpleNamespace(
        make="Tesla",
        model="Model Y",
        description=None,
        registration_number="PE70DHX",
    )

    assert service._authorized_entry_message(person, vehicle) == expected


def test_authorized_entry_message_uses_them_for_unknown_unset_pronouns() -> None:
    service = AccessEventService()
    person = SimpleNamespace(first_name="Taylor", display_name="Taylor Smith", pronouns=None)
    vehicle = SimpleNamespace(
        make="Tesla",
        model="Model Y",
        description=None,
        registration_number="PE70DHX",
    )

    assert (
        service._authorized_entry_message(person, vehicle)
        == "Taylor's Tesla Model Y has been detected at the gate. I've let them in."
    )
