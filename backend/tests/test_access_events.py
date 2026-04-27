from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.models.enums import AccessDirection, PresenceState
from app.modules.lpr.base import PlateRead
from app.services.access_events import (
    AccessEventService,
    GATE_OBSERVATION_PAYLOAD_KEY,
    KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY,
)


class FakePresenceSession:
    def __init__(self, state: PresenceState | None = None) -> None:
        self._state = state

    async def get(self, _model, _person_id):
        if not self._state:
            return None
        return SimpleNamespace(state=self._state)


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

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)

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

    monkeypatch.setattr(service, "_active_vehicle_registrations", fake_active_vehicle_registrations)
    monkeypatch.setattr(service, "_finalize_window", fake_finalize_window)

    first_seen = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
    await service._handle_queued_read(plate_read("ND25VN0", first_seen))
    await service._handle_queued_read(plate_read("MD25VNO", first_seen + timedelta(seconds=1)))

    assert finalized == [{"candidate_count": 2, "best_registration_number": "MD25VNO"}]
    assert service._pending == []
