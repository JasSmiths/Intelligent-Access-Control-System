from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

from app.modules.unifi_protect.camera_catalog import CameraWireCatalog
from app.modules.unifi_protect.client import serialize_unifi_camera, serialize_unifi_event
from app.services.unifi_protect import (
    UnifiProtectIntegrationService,
    _websocket_state_name,
    gate_lpr_camera_from_bootstrap,
    resolve_camera_smart_zones,
)


class EnumValue:
    def __init__(self, value: str) -> None:
        self.value = value


def test_camera_serialization_exposes_metadata_not_stream_urls(monkeypatch) -> None:
    from app.modules.unifi_protect.camera_catalog import CameraWireMetadata

    camera = SimpleNamespace(
        id="cam-1",
        display_name="Driveway",
        type=EnumValue("camera"),
        state=EnumValue("CONNECTED"),
        is_adopted=True,
        is_recording=True,
        is_recording_enabled=True,
        is_video_ready=True,
        is_smart_detected=True,
        last_motion=datetime(2026, 4, 25, tzinfo=UTC),
        last_motion_event_id="event-1",
        last_smart_detect=None,
        last_smart_detect_event_id=None,
        last_smart_audio_detect=None,
        last_smart_audio_detect_event_id=None,
        channels=[
            SimpleNamespace(
                id=0,
                width=1920,
                height=1080,
                fps=30,
                bitrate=6000,
                rtsp_url="rtsp://secret",
            )
        ],
        feature_flags=SimpleNamespace(
            has_smart_detect=True,
            smart_detect_types=[EnumValue("person")],
            smart_detect_audio_types=[],
        ),
        has_mic=True,
    )

    from uiprotect.data import SmartDetectObjectType

    wire = CameraWireMetadata.from_raw({
        "smartDetectZones": [{"id": 2, "name": "Default", "objectTypes": ["person", "vehicle"]}],
        "channels": [{"id": 0, "name": "High", "isRtspEnabled": True}],
        "isMotionDetected": False,
    })
    monkeypatch.setattr("app.modules.unifi_protect.client.camera_wire_metadata", lambda _: wire)
    camera.use_global = False
    camera.smart_detect_settings = SimpleNamespace(object_types=[SmartDetectObjectType.PERSON], audio_types=[])
    camera.get_last_smart_detect_event = lambda _: SimpleNamespace(
        end=None, smart_detect_types=[SmartDetectObjectType.PERSON])
    payload = serialize_unifi_camera(camera)

    assert payload["id"] == "cam-1"
    assert payload["detections"]["active"] == ["person"]
    assert payload["smart_detect_zones"] == [
        {"id": 2, "name": "Default", "object_types": ["person", "vehicle"]}
    ]
    assert payload["channels"][0]["is_rtsp_enabled"] is True
    assert "rtsp_url" not in payload["channels"][0]


def test_gate_lpr_zone_id_resolves_to_zone_name() -> None:
    gate_lpr = SimpleNamespace(
        id="camera-protect-id",
        mac="942A6FD09D64",
        display_name="Gate LPR",
    )
    other = SimpleNamespace(id="other", mac="other", display_name="Drive")
    catalog = CameraWireCatalog.from_bootstrap([
        {"id": other.id, "smartDetectZones": [{"id": 2, "name": "Other"}]},
        {"id": gate_lpr.id, "smartDetectZones": [{"id": 2, "name": "Default"}]},
    ])

    camera = gate_lpr_camera_from_bootstrap([other, gate_lpr], camera_identifier="942A6FD09D64")

    assert camera is gate_lpr
    metadata = catalog.get(camera.id)
    assert metadata is not None and metadata.zones is not None
    resolved, matches = resolve_camera_smart_zones(metadata.zones, ["2", "unmapped"])
    assert resolved == ["2", "Default", "unmapped"]
    assert matches == [
        {"input": "2", "id": "2", "name": "Default"},
        {"input": "unmapped", "id": "unmapped", "name": "unmapped"},
    ]


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

    for channel in ("private", "events", "devices"):
        service._handle_websocket_state(channel, FakeWebsocketState.CONNECTED)

    assert service._connected is True
    assert service._realtime_connected() is True
    assert service._websocket_states == {
        "private": "connected",
        "events": "connected",
        "devices": "connected",
    }

    service._handle_websocket_state("events", FakeWebsocketState.AUTH_FAILED)
    assert service._connected is True
    assert service._realtime_connected() is False
    assert service._realtime_error() == "UniFi Protect websocket authentication failed: events."

    await service.stop()
    assert service._connected is False
    assert service._websocket_states == {
        "private": "unknown",
        "events": "unknown",
        "devices": "unknown",
    }


def test_stream_metadata_update_does_not_refresh_vehicle_presence() -> None:
    service = UnifiProtectIntegrationService()
    from types import SimpleNamespace

    from app.services.unifi_protect_runtime import UnifiProtectRuntime

    service._runtime = UnifiProtectRuntime(SimpleNamespace(), service._evidence, service._counters)
    camera = SimpleNamespace(
        id="cam-1",
        model=EnumValue("camera"),
        channels=[],
        is_video_ready=True,
    )
    service._handle_websocket_message(
        SimpleNamespace(
            action=EnumValue("update"),
            new_obj=camera,
            old_obj=None,
            changed_data={"modelKey": "camera", "id": "cam-1", "rtsps_streams": {}},
        )
    )

    work = service._runtime._callbacks[0][0]
    assert work.presence == ()
    assert work.timing == ()
    assert work.visual == ()


def test_detection_update_still_refreshes_vehicle_presence() -> None:
    service = UnifiProtectIntegrationService()
    from types import SimpleNamespace

    from app.services.unifi_protect_runtime import UnifiProtectRuntime

    service._runtime = UnifiProtectRuntime(SimpleNamespace(), service._evidence, service._counters)
    camera = SimpleNamespace(
        id="cam-1",
        model=EnumValue("camera"),
        channels=[],
        is_video_ready=True,
    )
    from uiprotect.data import SmartDetectObjectType

    camera.is_recording_enabled = camera.is_smart_detected = True
    camera.use_global = False
    camera.smart_detect_settings = SimpleNamespace(object_types=[SmartDetectObjectType.VEHICLE], audio_types=[])
    camera.get_last_smart_detect_event = lambda _: SimpleNamespace(
        end=None, smart_detect_types=[SmartDetectObjectType.VEHICLE])
    service._handle_websocket_message(
        SimpleNamespace(
            action=EnumValue("update"),
            new_obj=camera,
            old_obj=None,
            changed_data={"modelKey": "camera", "id": "cam-1", "isSmartDetected": True},
        )
    )

    assert service._runtime._callbacks[0][0].presence[0].active is True
