import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as _SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.models import AccessEvent
from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services import access_events as access_events_module
from app.services.access import hardware as access_hardware_module
from app.services.access import snapshots as access_snapshots_module
from app.services.access.evidence import AccessEvidenceResolver, PreparedCameraEvidence
from app.services.access.execution import AccessExecution
from app.services.access.hardware import (
    open_garage_doors_for_access_event,
    open_gate_for_access_event,
    publish_gate_open_skipped,
)
from app.services.access.payloads import (
    _access_event_raw_payload,
    access_event_realtime_payload,
    authorized_entry_message,
    notification_facts,
)
from app.services.access.pipeline_contracts import EvidenceReference, WindowSummary
from app.services.access.plate_matching import match_candidates
from app.services.access.reads import (
    EXTERNAL_ADMISSION_PAYLOAD_KEY,
    GATE_MALFUNCTION_PAYLOAD_KEY,
    GATE_OBSERVATION_PAYLOAD_KEY,
    INGEST_METADATA_PAYLOAD_KEY,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
    _webhook_trace_for_window,
)
from app.services.access.snapshots import capture_access_event_snapshot
from app.services.access.window_candidates import WindowCandidate
from app.services.access.window_store import StoredWindow
from app.services.access_events import AccessEventService
from app.services.gate_commands import GateCommandIntent, GateCommandOutcome
from app.services.movement import sessions as movement_sessions_module
from app.services.snapshots import access_event_snapshot_relative_path

SimpleNamespace = cast(Any, _SimpleNamespace)


# Durable intake/capture contracts execute against real PostgreSQL in
# scripts/validation/test_access_intake_persistence.py. An in-memory session
# substitute cannot exercise unique insert ownership, leases or ready ordering.


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
    async def close(self):
        pass

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
        self.contexts: list[Any] = []

    async def notify(self, context):
        self.contexts.append(context)


class FakeAccessDeviceService:
    def __init__(
        self,
        *,
        accepted: bool = True,
        state: GateState = GateState.OPENING,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.device = SimpleNamespace(device_id=str(uuid.uuid4()), key="cover.main_garage_door", name="Main Garage", kind="garage_door")
        self.accepted = accepted
        self.state = state
        self.detail = detail
        self.metadata = {"command_id": str(uuid.uuid4()), **(metadata or {})}
        self.commands: list[tuple[str, str, str]] = []

    async def list_devices(self, *, kind: str | None = None, enabled_only: bool = False):
        return [self.device] if kind in {None, "garage_door"} else []

    async def command_device(self, device_key: str, action: str, reason: str, **_kwargs):
        self.commands.append((device_key, action, reason))
        return SimpleNamespace(
            device=self.device,
            action=action,
            accepted=self.accepted,
            state=self.state,
            detail=self.detail or reason,
            used_provider="home_assistant",
            primary_provider="home_assistant",
            failover_used=False,
            attempts=[],
            delivery="accepted" if self.accepted else "not_sent" if self.metadata.get("schedule_denied") or self.metadata.get("verified") else "rejected",
            requires_reconciliation=False,
            verified=bool(self.metadata.get("verified")),
            metadata=self.metadata,
            as_payload=lambda: {
                "entity_id": self.device.key,
                "name": self.device.name,
                "accepted": self.accepted,
                "delivery": "accepted" if self.accepted else "not_sent" if self.metadata.get("schedule_denied") or self.metadata.get("verified") else "rejected",
                "verified": bool(self.metadata.get("verified")),
                "state": self.state.value,
                "detail": self.detail or reason,
            },
        )


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
        access_hardware_module,
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

    monkeypatch.setattr(access_hardware_module, "reserve_garage_outcome_outputs", AsyncMock(return_value=True))
    monkeypatch.setattr(access_hardware_module, "_recognition_expiry", AsyncMock(return_value=timestamp + timedelta(seconds=60)))
    FakeAuditSession.get = AsyncMock(return_value=None)
    monkeypatch.setattr(access_hardware_module, "AsyncSessionLocal", lambda: FakeAuditSession())
    monkeypatch.setattr(access_hardware_module, "write_audit_log", fake_write_audit_log)
    monkeypatch.setattr(access_hardware_module.event_bus, "publish", fake_publish)
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


def test_plate_read_webhook_trace_records_capture_to_receipt_latency() -> None:
    service = AccessEventService()
    captured_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    received_at = captured_at + timedelta(milliseconds=875)
    read = plate_read("AGS7X", captured_at)

    annotated = service._read_with_webhook_trace(read, received_at)

    webhook_trace = annotated.raw_payload["webhook_trace"]
    assert webhook_trace["source"] == "test"
    assert webhook_trace["registration_number"] == "AGS7X"
    assert webhook_trace["captured_at"] == "2026-05-10T08:00:00+00:00"
    assert webhook_trace["received_at"] == "2026-05-10T08:00:00.875000+00:00"
    assert webhook_trace["captured_to_webhook_ms"] == 875.0


def test_plate_read_webhook_trace_prefers_route_ingest_timestamp() -> None:
    service = AccessEventService()
    captured_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    route_received_at = captured_at + timedelta(milliseconds=125)
    service_received_at = captured_at + timedelta(milliseconds=875)
    read = PlateRead(
        registration_number="AGS7X",
        confidence=0.9,
        source="test",
        captured_at=captured_at,
        raw_payload={
            INGEST_METADATA_PAYLOAD_KEY: {
                "webhook_received_at": route_received_at.isoformat(),
                "request_id": "req-test",
                "webhook_trace_id": "trace-test",
                "captured_to_webhook_ms": 125.0,
            }
        },
    )

    annotated = service._read_with_webhook_trace(read, service_received_at)

    webhook_trace = annotated.raw_payload["webhook_trace"]
    assert webhook_trace["received_at"] == "2026-05-10T08:00:00.125000+00:00"
    assert webhook_trace["webhook_received_at"] == "2026-05-10T08:00:00.125000+00:00"
    assert webhook_trace["captured_to_webhook_ms"] == 125.0
    assert webhook_trace["request_id"] == "req-test"
    assert webhook_trace["webhook_trace_id"] == "trace-test"


def test_access_event_raw_payload_includes_webhook_trace_and_debounce_timestamps() -> None:
    service = AccessEventService()
    captured_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    received_at = captured_at + timedelta(milliseconds=250)
    finalized_at = captured_at + timedelta(seconds=2)
    read = service._read_with_webhook_trace(plate_read("AGS7X", captured_at), received_at)
    identity = uuid.uuid4()
    window = StoredWindow(WindowSummary(EvidenceReference(uuid.uuid4()), read.source, 1, 1,
        captured_at, captured_at, received_at, identity, identity, None), read, read, None,
        (WindowCandidate(identity, 0, read.registration_number, read.registration_number,
            read.confidence, captured_at, (read.registration_number,), None, None,
            read.raw_payload["webhook_trace"]),))
    webhook_trace = _webhook_trace_for_window(window)

    payload = _access_event_raw_payload(
        window=window,
        read=read,
        schedule_evaluation=None,
        direction_resolution={"source": "gate_state"},
        vehicle_visual_detection=None,
        visitor_pass=None,
        visitor_pass_mode=None,
        trace_id="0" * 32,
        finalize_started_at=finalized_at,
        webhook_trace=webhook_trace,
    )

    assert payload["webhook_trace"]["captured_to_webhook_ms"] == 250.0
    assert payload["debounce"]["first_seen"] == "2026-05-10T08:00:00+00:00"
    assert payload["debounce"]["updated_at"] == "2026-05-10T08:00:00+00:00"
    assert payload["debounce"]["finalize_started_at"] == "2026-05-10T08:00:02+00:00"
    assert payload["debounce"]["candidates"][0]["webhook_trace"]["received_at"] == "2026-05-10T08:00:00.250000+00:00"


def test_access_event_payloads_expose_external_admission_metadata() -> None:
    service = AccessEventService()
    captured_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    read = plate_read("UNK123", captured_at)
    external_admission = {"mode": "arrival", "source": "gate_state_changed"}
    payload = _access_event_raw_payload(
        window=None,
        read=read,
        schedule_evaluation=None,
        direction_resolution={"source": "external_gate_open", "external_admission": external_admission},
        vehicle_visual_detection=None,
        visitor_pass=None,
        visitor_pass_mode=None,
        trace_id="0" * 32,
        finalize_started_at=captured_at,
        webhook_trace={},
        external_admission=external_admission,
    )
    event = SimpleNamespace(
        id=uuid.uuid4(),
        person_id=None,
        vehicle_id=None,
        registration_number="UNK123",
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
        confidence=0.9,
        source="gate_state_changed",
        occurred_at=captured_at,
        timing_classification=TimingClassification.UNKNOWN,
        raw_payload=payload,
        snapshot_path=None,
    )

    realtime = access_event_realtime_payload(event, anomaly_count=1, visitor_pass=None, visitor_pass_mode=None)
    facts = notification_facts(event, None, None, "Unauthorised Plate, Admitted Externally")

    assert payload[EXTERNAL_ADMISSION_PAYLOAD_KEY] == external_admission
    assert realtime["external_admission_mode"] == "arrival"
    assert realtime["external_admission_source"] == "gate_state_changed"
    assert facts["external_admission_mode"] == "arrival"
    assert facts["external_admission_source"] == "gate_state_changed"


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
    raw_payload: dict[str, Any] = {
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


@pytest.mark.asyncio
async def test_open_gate_unknown_read_can_be_marked_as_external_admission() -> None:
    service = AccessEventService()
    service._runtime = SimpleNamespace(
        lpr_similarity_threshold=0.78,
        lpr_debounce_max_seconds=6.0,
        lpr_vehicle_session_idle_seconds=180.0,
        site_timezone="Europe/London",
    )
    captured_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
    denied_session = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="UNK123",
        normalized_registration_number="UNK123",
    )
    denied_event = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="UNK123",
        confidence=0.88,
    )
    match = movement_sessions_module.ExternalVehicleSessionMatch(
        session=denied_session,
        access_event=denied_event,
        evidence={
            "source": "uiprotect_event",
            "source_detail": "vehicle_detection",
            "active": True,
            "observed_at": captured_at.isoformat(),
            "event_id": "protect-1",
            "registration_number": "UNK123",
        },
        matched_by="external_presence_registration_number",
    )

    class FakeMovementSessions:
        async def external_departure_candidate_for_read(self, *_args, **_kwargs):
            return None

        async def external_admission_candidate_for_read(self, *_args, **_kwargs):
            return match

    service._movement_sessions = FakeMovementSessions()
    service._recent_iacs_gate_open_command = AsyncMock(return_value=None)

    annotated = await service._read_with_external_admission_match(
        plate_read_with_context("UNK123", captured_at, state="open", event_id="protect-1", camera_id="gate-camera")
    )

    external = annotated.raw_payload[EXTERNAL_ADMISSION_PAYLOAD_KEY]
    assert annotated.registration_number == "UNK123"
    assert external["mode"] == "arrival"
    assert external["source"] == "lpr_open_gate_read"
    assert external["original_denied_access_event_id"] == str(denied_event.id)
    assert external["presence_evidence"]["source"] == "uiprotect_event"


@pytest.mark.asyncio
async def test_external_admission_anomalies_alert_arrival_but_not_linked_departure() -> None:
    service = AccessExecution(None, None)
    occurred_at = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)

    def event_for(mode: str | None) -> AccessEvent:
        return AccessEvent(
            registration_number="UNK123",
            direction=AccessDirection.EXIT if mode == "departure" else AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED if mode else AccessDecision.DENIED,
            confidence=0.9,
            source="test",
            occurred_at=occurred_at,
            timing_classification=TimingClassification.UNKNOWN,
            raw_payload={"external_admission": {"mode": mode, "source": "gate_state_changed"}} if mode else {},
        )

    arrival = await service._build_anomalies(
        FakePresenceSession(),
        event_for("arrival"),
        None,
        None,
        True,
        external_admission={"mode": "arrival", "source": "gate_state_changed"},
    )
    departure = await service._build_anomalies(
        FakePresenceSession(),
        event_for("departure"),
        None,
        None,
        True,
        external_admission={"mode": "departure", "source": "external_vehicle_session"},
    )
    denied = await service._build_anomalies(FakePresenceSession(), event_for(None), None, None, False)

    assert [item.message for item in arrival] == ["Unauthorised Plate, Admitted Externally"]
    assert arrival[0].context["external_admission"]["mode"] == "arrival"
    assert departure == []
    assert [item.message for item in denied] == ["Unauthorised Plate, Access Denied"]


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

    payload = access_event_realtime_payload(
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

    monkeypatch.setattr(access_snapshots_module, "get_snapshot_manager", lambda: FakeSnapshotManager())
    monkeypatch.setattr(access_snapshots_module, "get_unifi_protect_service", lambda: FakeProtect())

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

    await capture_access_event_snapshot(event)

    assert calls["event_thumbnail"] == ("protect-event-1", 320, 180)
    assert calls["content"] == b"jpeg"
    assert calls["relative_path"] == access_event_snapshot_relative_path(event_id)
    assert event.snapshot_path == access_event_snapshot_relative_path(event_id)
    assert event.snapshot_bytes == 1234
    assert event.snapshot_camera == "camera.gate"


@pytest.mark.asyncio
async def test_gate_malfunction_known_vehicle_last_live_exit_resolves_as_entry() -> None:
    service = AccessEvidenceResolver()
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


@pytest.mark.asyncio
async def test_gate_malfunction_known_vehicle_last_live_entry_resolves_as_exit() -> None:
    service = AccessEvidenceResolver()
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
    service = AccessEvidenceResolver()
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
    service = AccessEvidenceResolver()
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
    service = AccessEvidenceResolver()
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
    service = AccessEvidenceResolver()

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(),
        visitor_pass_departure_read(plate_read_with_gate_state("unknown")),
        person=None,
        allowed=True,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "visitor_pass_presence"


@pytest.mark.asyncio
async def test_duplicate_arrival_uses_camera_tiebreaker_as_source_of_truth() -> None:
    service = AccessEvidenceResolver()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Steph")

    read = plate_read_with_gate_state("closed")
    prepared = PreparedCameraEvidence.for_read(person.id, read,
        {"direction": "exit", "confidence": 0.91, "reason": "Vehicle is facing away."})

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.PRESENT),
        read,
        person,
        allowed=True,
        camera_evidence=prepared,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "camera_tiebreaker"
    assert resolution["camera_tiebreaker"]["confidence"] == 0.91


@pytest.mark.asyncio
async def test_duplicate_arrival_accepts_moderate_exit_tiebreaker() -> None:
    service = AccessEvidenceResolver()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Jason")

    read = plate_read_with_gate_state("closed")
    prepared = PreparedCameraEvidence.for_read(person.id, read,
        {"direction": "exit", "confidence": 0.60, "reason": "Rear of vehicle is more visible."})

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.PRESENT),
        read,
        person,
        allowed=True,
        camera_evidence=prepared,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "camera_tiebreaker"
    assert resolution["camera_tiebreaker"]["confidence"] == 0.60
    assert "camera_tiebreaker_ignored_reason" not in resolution


@pytest.mark.asyncio
async def test_duplicate_arrival_keeps_closed_gate_entry_when_camera_tiebreaker_is_uncertain() -> None:
    service = AccessEvidenceResolver()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Ash")

    read = plate_read_with_gate_state("closed")
    prepared = PreparedCameraEvidence.for_read(person.id, read,
        {"direction": "exit", "confidence": 0.54, "reason": "Vehicle might be facing away."})

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.PRESENT),
        read,
        person,
        allowed=True,
        camera_evidence=prepared,
    )

    assert direction == AccessDirection.ENTRY
    assert resolution["source"] == "gate_state"
    assert resolution["camera_tiebreaker"]["confidence"] == 0.54
    assert resolution["camera_tiebreaker_ignored_reason"] == "low_confidence"


def test_camera_direction_parser_accepts_json_and_text() -> None:
    service = AccessEvidenceResolver()

    assert service._parse_camera_direction_analysis(
        '{"direction":"exit","confidence":0.87,"reason":"rear of vehicle visible"}'
    ) == ("exit", 0.87, "rear of vehicle visible")
    assert service._parse_camera_direction_analysis("The vehicle is facing towards the camera.")[0] == "entry"
    assert service._parse_camera_direction_analysis("The vehicle is away from the camera.")[0] == "exit"


def test_known_vehicle_plate_match_canonicalizes_likely_misreads() -> None:
    service = AccessEventService()
    stored = ["MD25VNO"]

    first_match = match_candidates(("MD25VMO",), stored, 0.78)
    second_match = match_candidates(("MO25VNO",), stored, 0.78)
    third_match = match_candidates(("MD2SVNO",), stored, 0.78)
    assert first_match is not None
    assert second_match is not None
    assert third_match is not None
    assert first_match["registration_number"] == "MD25VNO"
    assert second_match["registration_number"] == "MD25VNO"
    assert third_match["registration_number"] == "MD25VNO"
    assert match_candidates(("ND25VN0",), stored, 0.78) is None

    exact = match_candidates(("MD25VNO",), stored, 0.78)
    assert exact is not None
    assert exact["registration_number"] == "MD25VNO"
    assert exact["exact"] is True


@pytest.mark.asyncio
async def test_automatic_gate_open_projects_receipt_without_second_audit_owner(monkeypatch) -> None:
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

    outcome = await open_gate_for_access_event(event, person, open_garage_doors=False)

    assert outcome.accepted is True
    assert audits == []  # Durable finalizer owns the audit; tested with PostgreSQL.
    event_type, payload = published[-1]
    assert event_type == "gate.open_requested"
    assert payload["accepted"] is True and payload["delivery"] == "accepted"
    assert payload["event_id"] == str(event.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery,expected_status", [("not_sent", "ok"), ("partial", "error"), ("unknown", "error")])
async def test_verified_entry_does_not_hide_other_target_failures_in_trace(monkeypatch, delivery, expected_status):
    from dataclasses import replace
    from unittest.mock import Mock

    event, person, _ = hardware_audit_subjects()
    capture_hardware_audits(monkeypatch)
    install_gate_command_outcome(monkeypatch, lambda intent: replace(
        gate_command_outcome(intent, accepted=False, state=GateState.OPEN, detail="Synthetic target result"),
        delivery=delivery, admission_verified=True,
    ))
    span = Mock()
    trace = SimpleNamespace(start_span=Mock(return_value=span))
    await open_gate_for_access_event(event, person, open_garage_doors=False, trace=trace)
    assert span.finish.call_args.kwargs["status"] == expected_status
    assert span.finish.call_args.kwargs["output_payload"]["delivery"] == delivery


@pytest.mark.asyncio
async def test_automatic_gate_skip_writes_skipped_audit(monkeypatch) -> None:
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)

    await publish_gate_open_skipped(
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
async def test_automatic_gate_controller_error_projects_receipt_without_optional_notification(monkeypatch) -> None:
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

    outcome = await open_gate_for_access_event(event, person, open_garage_doors=False)

    assert outcome.accepted is False
    assert audits == [] and notifications.contexts == []
    event_type, payload = published[-1]
    assert event_type == "gate.open_failed"
    assert payload["accepted"] is False
    assert payload["detail"] == "Unsupported gate controller: missing"


@pytest.mark.asyncio
async def test_automatic_garage_open_hands_receipt_to_single_delivery_owner(monkeypatch) -> None:
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)
    devices = FakeAccessDeviceService()

    monkeypatch.setattr(access_hardware_module, "get_access_device_service", lambda: devices)

    await open_garage_doors_for_access_event(event, person, "Automatic LPR grant")

    assert audits == []
    access_hardware_module.reserve_garage_outcome_outputs.assert_awaited_once()
    assert access_hardware_module.reserve_garage_outcome_outputs.await_args.kwargs["command_id"] == uuid.UUID(devices.metadata["command_id"])
    assert published[-1][1]["delivery"] == "accepted"
    assert devices.commands == [("cover.main_garage_door", "open", "Automatic LPR grant")]
    assert any(event_type == "garage_door.open_requested" for event_type, _payload in published)


@pytest.mark.asyncio
async def test_automatic_garage_door_schedule_denial_records_not_sent(monkeypatch) -> None:
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)
    notifications = FakeNotificationService()
    devices = FakeAccessDeviceService(
        accepted=False,
        state=GateState.FAULT,
        detail="Main Garage is outside schedule.",
        metadata={"schedule_denied": True},
    )

    monkeypatch.setattr(access_hardware_module, "get_access_device_service", lambda: devices)

    await open_garage_doors_for_access_event(event, person, "Automatic LPR grant")

    assert audits == []
    access_hardware_module.reserve_garage_outcome_outputs.assert_awaited_once()
    assert access_hardware_module.reserve_garage_outcome_outputs.await_args.kwargs["command_id"] == uuid.UUID(devices.metadata["command_id"])
    assert published[-1][1]["delivery"] == "not_sent"
    assert devices.commands == [("cover.main_garage_door", "open", "Automatic LPR grant")]
    assert any(event_type == "garage_door.open_failed" for event_type, _payload in published)
    assert notifications.contexts == []


@pytest.mark.asyncio
async def test_automatic_garage_door_provider_rejection_is_recorded(monkeypatch) -> None:
    event, person, _vehicle = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)
    notifications = FakeNotificationService()
    devices = FakeAccessDeviceService(
        accepted=False,
        state=GateState.FAULT,
        detail="Home Assistant rejected the command.",
    )

    monkeypatch.setattr(access_hardware_module, "get_access_device_service", lambda: devices)

    await open_garage_doors_for_access_event(event, person, "Automatic LPR grant")

    assert audits == []
    access_hardware_module.reserve_garage_outcome_outputs.assert_awaited_once()
    assert access_hardware_module.reserve_garage_outcome_outputs.await_args.kwargs["command_id"] == uuid.UUID(devices.metadata["command_id"])
    assert published[-1][1]["delivery"] == "rejected"
    assert devices.commands == [("cover.main_garage_door", "open", "Automatic LPR grant")]
    assert any(event_type == "garage_door.open_failed" for event_type, _payload in published)
    assert notifications.contexts == []


def test_unknown_notification_facts_prefer_visual_detection_colour_over_dvla() -> None:
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

    facts = notification_facts(
        event,
        person=None,
        vehicle=None,
        message="Unauthorised Plate, Access Denied",
        vehicle_information={"make": "Tesla", "colour": "White"},
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

    facts = notification_facts(event, person=person, vehicle=None, message="Granted")

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
    person = SimpleNamespace(first_name="Jason", display_name="Jason Smith", pronouns=pronouns)
    vehicle = SimpleNamespace(
        make="Tesla",
        model="Model Y",
        description=None,
        registration_number="PE70DHX",
    )

    assert authorized_entry_message(person, vehicle) == expected


def test_authorized_entry_message_uses_them_for_unknown_unset_pronouns() -> None:
    person = SimpleNamespace(first_name="Taylor", display_name="Taylor Smith", pronouns=None)
    vehicle = SimpleNamespace(
        make="Tesla",
        model="Model Y",
        description=None,
        registration_number="PE70DHX",
    )

    assert (
        authorized_entry_message(person, vehicle)
        == "Taylor's Tesla Model Y has been detected at the gate. I've let them in."
    )


@pytest.mark.asyncio
async def test_verified_already_open_garage_preserves_no_send_truth_without_failure_notice(monkeypatch):
    event, person, _ = hardware_audit_subjects()
    audits, published = capture_hardware_audits(monkeypatch)
    notifications = FakeNotificationService()
    devices = FakeAccessDeviceService(accepted=False, state=GateState.OPEN,
        detail="Target already reports the requested state.", metadata={"verified": True})
    monkeypatch.setattr(access_hardware_module, "get_access_device_service", lambda: devices)
    await open_garage_doors_for_access_event(event, person, "Synthetic already-open garage")
    assert audits == []
    access_hardware_module.reserve_garage_outcome_outputs.assert_awaited_once()
    assert published[-1][1]["accepted"] is False and published[-1][1]["receipt"]["verified"] is True
    assert published[-1][1]["delivery"] == "not_sent"
    assert notifications.contexts == []
    assert any(kind == "garage_door.open_requested" for kind, _ in published)
    assert not any(kind == "garage_door.open_failed" for kind, _ in published)
