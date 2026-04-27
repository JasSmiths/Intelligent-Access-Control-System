from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest

from app.models.enums import AccessDirection, PresenceState
from app.modules.lpr.base import PlateRead
from app.services.access_events import AccessEventService, GATE_OBSERVATION_PAYLOAD_KEY


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
