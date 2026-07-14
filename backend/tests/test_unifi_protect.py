from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

from app.modules.unifi_protect.client import serialize_unifi_camera, serialize_unifi_event
from app.services.unifi_protect import (
    UnifiProtectIntegrationService,
    _websocket_state_name,
    gate_lpr_camera_from_bootstrap,
    resolve_camera_smart_zone_names,
)


class EnumValue:
    def __init__(self, value: str) -> None:
        self.value = value


def test_camera_serialization_exposes_metadata_not_stream_urls() -> None:
    camera = SimpleNamespace(
        id="cam-1",
        display_name="Driveway",
        type=EnumValue("camera"),
        state=EnumValue("CONNECTED"),
        is_adopted=True,
        is_recording=True,
        is_recording_enabled=True,
        is_video_ready=True,
        is_motion_detected=False,
        is_smart_detected=True,
        last_motion=datetime(2026, 4, 25, tzinfo=UTC),
        last_motion_event_id="event-1",
        last_smart_detect=None,
        last_smart_detect_event_id=None,
        last_smart_audio_detect=None,
        last_smart_audio_detect_event_id=None,
        is_person_currently_detected=True,
        channels=[
            SimpleNamespace(
                id="0",
                name="High",
                width=1920,
                height=1080,
                fps=30,
                bitrate=6000,
                is_rtsp_enabled=True,
                is_package=False,
                rtsp_url="rtsp://secret",
            )
        ],
        feature_flags=SimpleNamespace(
            has_smart_detect=True,
            has_package_camera=False,
            smart_detect_types=[EnumValue("person")],
            smart_detect_audio_types=[],
        ),
        smart_detect_zones=[
            SimpleNamespace(id=2, name="Default", object_types=[EnumValue("person"), EnumValue("vehicle")]),
        ],
        has_mic=True,
    )

    payload = serialize_unifi_camera(camera)

    assert payload["id"] == "cam-1"
    assert payload["detections"]["active"] == ["person"]
    assert payload["smart_detect_zones"] == [{"id": 2, "name": "Default", "object_types": ["person", "vehicle"]}]
    assert payload["channels"][0]["is_rtsp_enabled"] is True
    assert "rtsp_url" not in payload["channels"][0]


def test_gate_lpr_zone_id_resolves_to_zone_name() -> None:
    gate_lpr = SimpleNamespace(
        id="camera-protect-id",
        mac="942A6FD09D64",
        display_name="Gate LPR",
        smart_detect_zones=[
            SimpleNamespace(id=2, name="Default", object_types=[EnumValue("vehicle")]),
        ],
    )
    other = SimpleNamespace(id="other", mac="other", display_name="Drive", smart_detect_zones=[])

    camera = gate_lpr_camera_from_bootstrap([other, gate_lpr], camera_identifier="942A6FD09D64")

    assert camera is gate_lpr
    assert resolve_camera_smart_zone_names(camera, ["2"]) == ["2", "Default"]


def test_event_serialization_includes_proxy_urls() -> None:
    event = SimpleNamespace(
        id="event-1",
        type=EnumValue("smartDetectZone"),
        camera_id="cam-1",
        camera=SimpleNamespace(display_name="Driveway"),
        start=datetime(2026, 4, 25, tzinfo=UTC),
        end=datetime(2026, 4, 25, 0, 0, 10, tzinfo=UTC),
        score=92,
        smart_detect_types=[EnumValue("vehicle")],
        metadata=None,
    )

    payload = serialize_unifi_event(event)

    assert payload["thumbnail_url"].endswith("/events/event-1/thumbnail")
    assert payload["video_url"].endswith("/events/event-1/video")
    assert payload["smart_detect_types"] == ["vehicle"]


class FakeWebsocketState(Enum):
    CONNECTED = True
    DISCONNECTED = False
    AUTH_FAILED = "auth_failed"


def test_websocket_state_normalization_preserves_bool_backed_enum_names() -> None:
    assert _websocket_state_name(FakeWebsocketState.CONNECTED) == "connected"
    assert _websocket_state_name(FakeWebsocketState.DISCONNECTED) == "disconnected"
    assert _websocket_state_name(FakeWebsocketState.AUTH_FAILED) == "auth_failed"
    assert _websocket_state_name(True) == "connected"
    assert _websocket_state_name(False) == "disconnected"


async def test_websocket_states_are_channel_specific_and_do_not_replace_bootstrap_health() -> None:
    service = UnifiProtectIntegrationService()
    service._connected = True
    service._spawn_background = lambda coro, *, name: coro.close()

    for channel in ("private", "events", "devices"):
        service._handle_websocket_state(channel, FakeWebsocketState.CONNECTED)

    assert service._connected is True
    assert service._realtime_connected() is True
    assert service._websocket_states == {"private": "connected", "events": "connected", "devices": "connected"}

    service._handle_websocket_state("events", FakeWebsocketState.AUTH_FAILED)
    assert service._connected is True
    assert service._realtime_connected() is False
    assert service._realtime_error() == "UniFi Protect websocket authentication failed: events."

    await service._stop_locked()
    assert service._connected is False
    assert service._websocket_states == {"private": "unknown", "events": "unknown", "devices": "unknown"}


def test_stream_metadata_update_does_not_refresh_vehicle_presence() -> None:
    service = UnifiProtectIntegrationService()
    spawned: list[str] = []

    def capture(coro, *, name: str) -> None:
        spawned.append(name)
        coro.close()

    service._spawn_background = capture
    camera = SimpleNamespace(
        id="cam-1",
        model=EnumValue("camera"),
        channels=[],
        is_video_ready=True,
        is_vehicle_currently_detected=True,
    )
    service._handle_websocket_message(
        SimpleNamespace(
            action=EnumValue("update"),
            new_obj=camera,
            old_obj=None,
            changed_data={"modelKey": "camera", "id": "cam-1", "rtsps_streams": {}},
        )
    )

    assert "unifi-protect-vehicle-presence-message" not in spawned
    assert "unifi-protect-lpr-timing-message" not in spawned
    assert "unifi-protect-vehicle-visual-message" not in spawned


def test_detection_update_still_refreshes_vehicle_presence() -> None:
    service = UnifiProtectIntegrationService()
    spawned: list[str] = []

    def capture(coro, *, name: str) -> None:
        spawned.append(name)
        coro.close()

    service._spawn_background = capture
    camera = SimpleNamespace(
        id="cam-1",
        model=EnumValue("camera"),
        channels=[],
        is_video_ready=True,
        is_vehicle_currently_detected=True,
    )
    service._handle_websocket_message(
        SimpleNamespace(
            action=EnumValue("update"),
            new_obj=camera,
            old_obj=None,
            changed_data={"modelKey": "camera", "id": "cam-1", "isVehicleCurrentlyDetected": True},
        )
    )

    assert "unifi-protect-vehicle-presence-message" in spawned
