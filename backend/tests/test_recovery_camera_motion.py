import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.unifi_protect import client
from app.modules.unifi_protect.client import ProtectMedia
from app.services.access import recovery_camera as camera


@pytest.mark.parametrize("motion,earlier,later,last_plate,expected", [
    ("exit", [.1, .1, .8, .8], [.2, .2, .6, .6], "ABC123", "exit"),
    ("entry", [.2, .2, .6, .6], [.1, .1, .8, .8], "ABC123", "entry"),
    ("exit", [.1, .1, .8, .8], [.1, .1, .8, .8], "ABC123", "unknown"),
    ("exit", [.1, .1, .8, .8], [.2, .2, .6, .6], "ABC128", "unknown"),
    ("exit", [.2, .2, .6, .6], [.1, .1, .8, .8], "ABC123", "unknown"),
    ("exit", [0, .1, .8, .8], [.2, .2, .6, .6], "ABC123", "unknown"),
    ("unknown", [.1, .1, .8, .8], [.2, .2, .6, .6], "ABC123", "unknown"),
])
def test_position_and_exact_identity_must_support_observed_motion(motion, earlier, later, last_plate, expected):
    response = {"motion": motion, "confidence": .99, "first": {"plate": "ABC 123", "box": earlier},
                "last": {"plate": last_plate, "box": later}}
    assert camera.motion_result(json.dumps(response), "ABC123")["direction"] == expected


@pytest.mark.parametrize("text", ['{"direction":"exit","confidence":1}', 'facing away', 'null'])
def test_single_frame_verdict_never_establishes_travel(text):
    assert camera.motion_result(text, "ABC123")["direction"] == "unknown"


@pytest.mark.asyncio
async def test_recordings_requested_in_order_from_one_camera(monkeypatch):
    recorded = AsyncMock(side_effect=[b"first", b"last"])
    lookup = AsyncMock(return_value=SimpleNamespace(get_snapshot=recorded))
    monkeypatch.setattr(client, "get_camera_by_identifier", lookup)
    captured = datetime.now(UTC) - timedelta(seconds=20)
    frames = await client.get_direction_frames(object(), "camera.gate", captured)
    assert [call.kwargs["dt"] for call in recorded.await_args_list] == [captured - timedelta(seconds=6), captured]
    assert [frame.content for frame in frames] == [b"first", b"last"]
    lookup.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_recording_never_calls_vision(monkeypatch):
    frame = ProtectMedia(b"same frame", "image/jpeg")
    monkeypatch.setattr(camera, "get_unifi_protect_service", lambda: SimpleNamespace(
        direction_frames=AsyncMock(return_value=(frame, frame))))
    model = AsyncMock()
    monkeypatch.setattr(camera.OpenAIResponsesProvider, "analyze_images", model)
    result = await camera.recovery_camera(SimpleNamespace(captured_at=datetime.now(UTC)), None)
    assert result["reason"] == "identical_recorded_frames"
    model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,calls", [("recording_frame_empty", 2), ("recording_frame_read_failed", 1)])
async def test_failed_recording_identifies_frame_and_retries_only_empty(monkeypatch, reason, calls):
    frames = AsyncMock(side_effect=client.DirectionFrameError(reason, 2))
    monkeypatch.setattr(camera, "get_unifi_protect_service", lambda: SimpleNamespace(direction_frames=frames))
    result = await camera.recovery_camera(SimpleNamespace(captured_at=datetime.now(UTC)), None)
    assert (result["reason"], result["frame"], result["retried"]) == (reason, 2, calls == 2)
    assert frames.await_count == calls
