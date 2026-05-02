from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.models import AccessEvent
from app.models.enums import AccessDecision, AccessDirection, PresenceState, TimingClassification
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError
from app.modules.lpr.base import PlateRead
from app.services import access_events as access_events_module
from app.services.access_events import (
    AccessEventService,
    FinalizedPlateEvent,
    GATE_OBSERVATION_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
    VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY,
    dvla_mot_alert_required,
    dvla_tax_alert_required,
)
from app.services.dvla import NormalizedDvlaVehicle
from app.services.snapshots import access_event_snapshot_relative_path


class FakePresenceSession:
    def __init__(self, state: PresenceState | None = None) -> None:
        self._state = state

    async def get(self, _model, _person_id):
        if not self._state:
            return None
        return SimpleNamespace(state=self._state)


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


def test_access_event_realtime_payload_includes_snapshot_metadata() -> None:
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
async def test_non_closed_gate_state_resolves_allowed_read_as_exit(state: str) -> None:
    service = AccessEventService()
    person = SimpleNamespace(id=uuid.uuid4(), display_name="Steph")

    direction, resolution = await service._resolve_direction(
        FakePresenceSession(PresenceState.EXITED),
        plate_read_with_gate_state(state),
        person,
        allowed=True,
    )

    assert direction == AccessDirection.EXIT
    assert resolution["source"] == "gate_state"
    assert not service._automatic_open_allowed(resolution)


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

    async def fake_no_visitor_pass_departure_match(read):
        return read

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_no_visitor_pass_departure_match)

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
async def test_exact_known_plate_suppresses_same_gate_cycle_echo_after_debounce_window(monkeypatch) -> None:
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
        return FinalizedPlateEvent(
            event_id=str(uuid.uuid4()),
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
            occurred_at=window.best_read.captured_at,
        )

    async def fake_no_visitor_pass_departure_match(read):
        return read

    async def fake_publish(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_no_visitor_pass_departure_match)
    monkeypatch.setattr(access_events_module.event_bus, "publish", fake_publish)

    first_seen = datetime(2026, 5, 1, 23, 29, 41, tzinfo=UTC)
    await service._handle_queued_read(plate_read_with_gate_state_at("PE70DHX", first_seen, "closed"))
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

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_no_visitor_pass_departure_match)

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

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_read_with_visitor_pass_departure_match", fake_read_with_visitor_pass_departure_match)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)
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
async def test_known_arrival_uses_same_day_dvla_cache(monkeypatch) -> None:
    service = AccessEventService()
    vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="PE70DHX",
        make="Peugeot",
        color="White",
        mot_status="Valid",
        tax_status="Taxed",
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
