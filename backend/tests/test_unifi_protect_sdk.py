"""Installed Protect SDK contracts with only its HTTP transport replaced."""

import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from uiprotect.data import (
    Camera,
    Event,
    ModelType,
    PermissionNode,
    RecordingMode,
    SmartDetectObjectType,
)
from uiprotect.data.devices import (
    CameraChannel,
    CameraFeatureFlags,
    RecordingSettings,
    SmartDetectSettings,
)

from app.modules.unifi_protect.client import (
    UnifiProtectError,
    build_unifi_protect_client,
    close_unifi_protect_client,
    get_camera_by_identifier,
    get_unifi_protect_event_thumbnail,
    get_unifi_protect_event_video,
    get_unifi_protect_snapshot,
    list_unifi_protect_events,
    serialize_unifi_camera,
    serialize_unifi_event,
    subscribe_unifi_protect,
    websocket_message_payload,
)
from app.services.lpr_timing import extract_unifi_protect_lpr_observations
from app.services.vehicle_visual_detections import extract_unifi_protect_vehicle_visual_observations

NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def event_payload(identity="event-1", **changes):
    return {
        "id": identity,
        "modelKey": "event",
        "type": "smartDetectZone",
        "start": int(NOW.timestamp() * 1000),
        "end": int((NOW + timedelta(seconds=10)).timestamp() * 1000),
        "camera": "cam-1",
        "score": 92,
        "smartDetectTypes": ["vehicle", "licensePlate"],
        "thumbnail": "e-event-1",
        **changes,
    }


@pytest.fixture(autouse=True)
def deny_provider_sockets(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("SDK fixture attempted a provider connection")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
async def sdk(tmp_path):
    config = SimpleNamespace(
        unifi_protect_host="example.invalid",
        unifi_protect_port=443,
        unifi_protect_username="synthetic-user",
        unifi_protect_password="synthetic-password",
        unifi_protect_api_key="synthetic-api-key",
        unifi_protect_verify_ssl=True,
    )
    api = await build_unifi_protect_client(config)
    flags = CameraFeatureFlags.model_construct(
        has_mic=True,
        has_package_camera=True,
        has_smart_detect=True,
        smart_detect_types=[SmartDetectObjectType.VEHICLE, SmartDetectObjectType.LICENSE_PLATE],
        smart_detect_audio_types=[],
    )
    camera = Camera.model_construct(
        id="cam-1",
        model=ModelType.CAMERA,
        name="Gate LPR",
        type="UVC G6",
        is_adopted=True,
        is_motion_detected=False,
        is_smart_detected=True,
        is_recording=True,
        use_global=False,
        feature_flags=flags,
        smart_detect_zones=[],
        recording_settings=RecordingSettings.model_construct(mode=RecordingMode.ALWAYS),
        smart_detect_settings=SmartDetectSettings.model_construct(
            object_types=flags.smart_detect_types, audio_types=[]
        ),
        channels=[
            CameraChannel.model_construct(
                id=0,
                name="High",
                width=1920,
                height=1080,
                fps=30,
                bitrate=6000,
                is_rtsp_enabled=False,
                is_package=False,
            )
        ],
    )
    camera._api = api
    api._bootstrap = SimpleNamespace(
        cameras={camera.id: camera},
        events={},
        auth_user=SimpleNamespace(can=Mock(return_value=True)),
    )
    api.api_request_list = AsyncMock(return_value=[])
    api.api_request_raw = AsyncMock(return_value=b"synthetic-media")
    try:
        yield api, camera
    finally:
        await close_unifi_protect_client(api)


async def test_client_retains_private_and_public_credentials_without_legacy_apis(sdk):
    api, _ = sdk
    assert not api._public_only
    assert api._subscribed_models == {ModelType.CAMERA, ModelType.EVENT}
    assert not hasattr(api, "get_events") and not hasattr(api, "get_camera")
    assert not hasattr(Event, "get_video") and not hasattr(Camera, "get_package_snapshot")


async def test_raw_events_keep_typed_contract_and_one_bounded_request(sdk):
    api, _ = sdk
    api.api_request_list.return_value = [
        event_payload(type="futureEvent"),
        event_payload(type="userLogin"),
        event_payload("other", camera="cam-2"),
        event_payload("negative", score=-1),
        event_payload("valid"),
        event_payload("missing-model", modelKey="event"),
    ]
    del api.api_request_list.return_value[-1]["modelKey"]
    events = await list_unifi_protect_events(
        api, camera_id="cam-1", limit=2, since=NOW, until=NOW + timedelta(hours=1)
    )
    assert [event.id for event in events] == ["valid", "missing-model"]
    assert all(isinstance(event, Event) for event in events)
    payload = serialize_unifi_event(events[0])
    assert payload["camera_name"] == "Gate LPR" and payload["score"] == 92
    assert payload["start"] == NOW.isoformat()
    assert payload["smart_detect_types"] == ["vehicle", "licensePlate"]
    assert payload["video_url"].endswith("/valid/video")
    api.api_request_list.assert_awaited_once()
    args, kwargs = api.api_request_list.await_args
    assert args == ("events",)
    params = kwargs["params"]
    assert params["limit"] == 6 and params["orderDirection"] == "DESC"
    assert params["start"] == int(NOW.timestamp() * 1000)
    assert params["end"] == int((NOW + timedelta(hours=1)).timestamp() * 1000)
    assert set(params["types"]) == EventTypeValues


EventTypeValues = {
    "motion",
    "nfcCardScanned",
    "fingerprintIdentified",
    "ring",
    "smartDetectZone",
    "smartAudioDetect",
    "smartDetectLine",
}


async def test_requested_event_type_is_checked_even_if_provider_ignores_filter(sdk):
    api, _ = sdk
    api.api_request_list.return_value = [
        event_payload(),
        event_payload("motion", type="motion", end=None),
    ]
    events = await list_unifi_protect_events(api, event_type="motion")
    assert [event.id for event in events] == ["motion"]
    assert serialize_unifi_event(events[0])["video_url"] is None
    assert api.api_request_list.await_args.kwargs["params"]["types"] == ["motion"]


@pytest.mark.parametrize("limit", [0, 251])
async def test_invalid_event_limit_fails_before_sdk_request(sdk, limit):
    api, _ = sdk
    with pytest.raises(UnifiProtectError, match="between 1 and 250"):
        await list_unifi_protect_events(api, limit=limit)
    api.api_request_list.assert_not_awaited()


async def test_unknown_event_type_fails_before_sdk_request(sdk):
    api, _ = sdk
    with pytest.raises(UnifiProtectError, match="Unsupported"):
        await list_unifi_protect_events(api, event_type="unsupported")
    api.api_request_list.assert_not_awaited()


async def test_malformed_known_event_has_safe_failure(sdk):
    api, _ = sdk
    api.api_request_list.return_value = [event_payload(start="provider-secret-must-not-appear")]
    with pytest.raises(UnifiProtectError, match="invalid event response") as error:
        await list_unifi_protect_events(api)
    assert "provider-secret" not in str(error.value)


@pytest.mark.parametrize("limit", [100, 250])
async def test_sdk_response_and_result_processing_are_bounded(sdk, limit):
    api, _ = sdk
    api.api_request_list.return_value = [
        event_payload(str(i), camera="other") for i in range(limit * 3)
    ] + [event_payload("outside-budget")]
    assert await list_unifi_protect_events(api, camera_id="cam-1", limit=limit) == []
    assert api.api_request_list.await_args.kwargs["params"]["limit"] == limit * 3


async def test_restart_backfill_event_read_through_service_and_installed_sdk(sdk, monkeypatch):
    limit = 250  # Public browsing remains independently bounded.
    from app.services.unifi_protect import UnifiProtectIntegrationService
    from app.services.unifi_protect_operations import ProtectOperations

    api, _ = sdk
    service = UnifiProtectIntegrationService()
    service._api = api
    service._operations = ProtectOperations(lambda: True)
    monkeypatch.setattr(service, "_ensure_api", AsyncMock(return_value=api))
    api.api_request_list.return_value = [
        event_payload(str(i)) for i in range(limit * 3)
    ]
    events = await service.list_events(
        limit=limit,
        event_type="smartDetectZone",
        since=NOW - timedelta(hours=24),
        until=NOW,
    )
    assert len(events) == limit
    assert [event["id"] for event in events] == [
        str(i) for i in range(limit)
    ]
    api.api_request_list.assert_awaited_once()
    assert api.api_request_list.await_args.kwargs["params"]["limit"] == (
        limit * 3
    )


async def test_clip_uses_exact_event_camera_and_time_range_through_sdk_export(sdk):
    api, camera = sdk
    event = Event.from_unifi_dict(**event_payload(), api=api)
    api._bootstrap.events[event.id] = event
    media = await get_unifi_protect_event_video(api, event.id)
    assert media.content == b"synthetic-media" and media.content_type == "video/mp4"
    api.bootstrap.auth_user.can.assert_called_once_with(
        ModelType.CAMERA, PermissionNode.READ_MEDIA, camera
    )
    api.api_request_raw.assert_awaited_once_with(
        "video/export",
        params={
            "camera": "cam-1",
            "start": int(NOW.timestamp() * 1000),
            "end": int((NOW + timedelta(seconds=10)).timestamp() * 1000),
            "channel": 0,
        },
        raise_exception=False,
    )


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"end": None}, "ongoing"),
        ({"camera": "missing"}, "available camera"),
        ({"end": 0}, "invalid video time range"),
    ],
)
async def test_invalid_clip_context_never_requests_video(sdk, changes, message):
    api, _ = sdk
    event = Event.from_unifi_dict(**event_payload(**changes), api=api)
    api._bootstrap.events[event.id] = event
    with pytest.raises(UnifiProtectError, match=message):
        await get_unifi_protect_event_video(api, event.id)
    api.api_request_raw.assert_not_awaited()


async def test_uncached_event_keeps_sdk_lookup_and_permission_rejection(sdk):
    api, _ = sdk
    api.api_request_obj = AsyncMock(return_value=event_payload())
    api.bootstrap.auth_user.can.return_value = False
    with pytest.raises(UnifiProtectError, match="permission"):
        await get_unifi_protect_event_video(api, "event-1")
    api.api_request_obj.assert_awaited_once_with("events/event-1")
    api.api_request_raw.assert_not_awaited()


async def test_empty_video_export_remains_failure(sdk):
    api, _ = sdk
    api._bootstrap.events["event-1"] = Event.from_unifi_dict(**event_payload(), api=api)
    api.api_request_raw.return_value = None
    with pytest.raises(UnifiProtectError, match="did not return an event video"):
        await get_unifi_protect_event_video(api, "event-1")


async def test_camera_cache_miss_uses_generic_sdk_lookup(sdk):
    api, _ = sdk
    api.get_device = AsyncMock(return_value="looked-up-camera")
    assert await get_camera_by_identifier(api, "unseen-id") == "looked-up-camera"
    api.get_device.assert_awaited_once_with(ModelType.CAMERA, "unseen-id", Camera)


async def test_snapshot_and_thumbnail_keep_existing_sdk_media_requests(sdk):
    api, camera = sdk
    await get_unifi_protect_snapshot(api, camera.id, width=640, height=360)
    assert api.api_request_raw.await_args.args == ("cameras/cam-1/snapshot",)
    params = api.api_request_raw.await_args.kwargs["params"]
    assert isinstance(params["ts"], int) and params["ts"] > 0
    assert {k: v for k, v in params.items() if k != "ts"} == {"w": 640, "h": 360, "force": "true"}
    api._bootstrap.events["event-1"] = Event.from_unifi_dict(**event_payload(), api=api)
    api.api_request_raw.reset_mock()
    await get_unifi_protect_event_thumbnail(api, "event-1", width=320)
    assert api.api_request_raw.await_args.args == ("events/event-1/thumbnail",)
    assert api.api_request_raw.await_args.kwargs["params"] == {"w": 320}


async def test_package_snapshot_uses_supported_endpoint_with_permissions(sdk):
    api, camera = sdk
    await get_unifi_protect_snapshot(api, camera.id, channel="package", width=2048)
    api.api_request_raw.assert_awaited_once_with(
        public_api=True,
        raise_exception=False,
        url="/v1/cameras/cam-1/snapshot",
        params={"highQuality": "true", "channel": "package"},
    )
    api.api_request_raw.reset_mock()
    api.bootstrap.auth_user.can.return_value = False
    with pytest.raises(UnifiProtectError, match="permission"):
        await get_unifi_protect_snapshot(api, camera.id, channel="package")
    api.api_request_raw.assert_not_awaited()


async def test_current_detection_projection_uses_active_typed_events(sdk):
    api, camera = sdk
    event = Event.from_unifi_dict(**event_payload(end=None), api=api)
    api._bootstrap.events[event.id] = event
    camera.last_smart_detect_event_ids = {kind: event.id for kind in event.smart_detect_types}
    assert serialize_unifi_camera(camera)["detections"]["active"] == ["vehicle", "license_plate"]
    event.end = NOW
    assert serialize_unifi_camera(camera)["detections"]["active"] == []
    event.end = None
    camera.recording_settings.mode = RecordingMode.NEVER
    assert serialize_unifi_camera(camera)["detections"]["active"] == []
    api.api_request_list.assert_not_awaited()
    api.api_request_raw.assert_not_awaited()


async def test_installed_event_metadata_reaches_lpr_and_visual_extractors(sdk):
    api, _ = sdk
    event = Event.from_unifi_dict(
        **event_payload(
            end=None,
            metadata={
                "detectedThumbnails": [
                    {
                        "type": "licensePlate",
                        "croppedId": "synthetic-crop",
                        "name": "AB12 CDE",
                        "confidence": 92,
                    },
                    {
                        "type": "vehicle",
                        "croppedId": "synthetic-vehicle",
                        "confidence": 92,
                        "attributes": {
                            "color": {"val": "gray", "confidence": 91},
                            "vehicleType": {"val": "sedan", "confidence": 82},
                        },
                    },
                ]
            },
        ),
        api=api,
    )
    message = SimpleNamespace(new_obj=event, old_obj=None, action="add", changed_data={})
    payload = websocket_message_payload(message)
    assert payload["event"]["metadata"]["detectedThumbnails"][0]["name"] == "AB12 CDE"
    lpr = extract_unifi_protect_lpr_observations(message)
    assert lpr and lpr[0].registration_number == "AB12CDE"
    assert extract_unifi_protect_vehicle_visual_observations(message)


async def test_real_sdk_subscription_cleanup_retains_three_channel_contract(sdk, monkeypatch):
    from uiprotect.websocket import Websocket

    api, _ = sdk
    start = Mock()
    monkeypatch.setattr(Websocket, "start", start)
    unsubs = subscribe_unifi_protect(api, lambda _: None, lambda *args: None)
    assert len(unsubs) == 6 and start.call_count == 3
    for unsubscribe in unsubs:
        unsubscribe()
    assert (
        not api._ws_subscriptions
        and not api._events_ws_subscriptions
        and not api._devices_ws_subscriptions
    )


async def test_audio_detection_uses_audio_settings_and_ongoing_event_index(sdk):
    api, camera = sdk
    kinds = [SmartDetectObjectType.SMOKE, SmartDetectObjectType.SPEAK]
    event = Event.from_unifi_dict(
        **event_payload(
            type="smartAudioDetect", end=None, smartDetectTypes=[kind.value for kind in kinds]
        ),
        api=api,
    )
    api._bootstrap.events[event.id] = event
    camera.is_smart_detected = False
    camera.smart_detect_settings.audio_types = [kind.audio_type for kind in kinds]
    camera.last_smart_audio_detect_event_ids = {kind.audio_type: event.id for kind in kinds}
    assert serialize_unifi_camera(camera)["detections"]["active"] == ["speaking", "smoke"]
    event.end = NOW
    assert serialize_unifi_camera(camera)["detections"]["active"] == []
    event.end = None
    camera.smart_detect_settings.audio_types = []
    assert serialize_unifi_camera(camera)["detections"]["active"] == []


async def test_real_public_websocket_frames_preserve_vehicle_presence_and_end(sdk, monkeypatch):
    import json

    from aiohttp import WSMessage, WSMsgType
    from uiprotect.data.public_bootstrap import PublicBootstrap
    from uiprotect.data.public_devices import PublicCamera, PublicCameraFeatureFlags
    from uiprotect.websocket import Websocket

    from app.services.unifi_protect import UnifiProtectIntegrationService
    from app.services.unifi_protect_runtime import UnifiProtectRuntime

    api, _ = sdk
    camera = PublicCamera.model_construct(
        id="cam-1",
        name="Gate LPR",
        has_package_camera=False,
        feature_flags=PublicCameraFeatureFlags.model_construct(
            has_mic=True,
            smart_detect_types=[SmartDetectObjectType.VEHICLE],
            smart_detect_audio_types=[],
        ),
    )
    camera._api = api
    api._public_bootstrap = PublicBootstrap(cameras={camera.id: camera})
    monkeypatch.setattr(Websocket, "start", Mock())
    messages = []
    unsubs = subscribe_unifi_protect(
        api, messages.append, lambda channel, state: owner._handle_websocket_state(channel, state)
    )
    owner = UnifiProtectIntegrationService()
    owner._runtime = UnifiProtectRuntime(SimpleNamespace(), owner._evidence, owner._counters)

    def frame(action, **item):
        api._process_events_ws_message(
            WSMessage(
                WSMsgType.TEXT,
                json.dumps(
                    {"type": action, "item": {"id": "public-event", "modelKey": "event", **item}}
                ),
                "",
            )
        )
        update = next(
            message for message in reversed(messages) if isinstance(message.new_obj, PublicCamera)
        )
        owner._handle_websocket_message(update)
        return websocket_message_payload(update)

    payload = frame(
        "add",
        type="smartDetectZone",
        device=camera.id,
        start=int(NOW.timestamp() * 1000),
        smartDetectTypes=["vehicle"],
        end=None,
    )
    assert payload["camera"]["detections"]["active"] == ["vehicle"]
    assert owner._runtime._callbacks[-1][0].presence[0].active is True
    payload = frame("update", end=int((NOW + timedelta(seconds=10)).timestamp() * 1000))
    assert payload["camera"]["detections"]["active"] == []
    # An empty active list is not explicit proof of absence.
    assert owner._runtime._callbacks[-1][0].presence == ()
    from uiprotect.websocket import WebsocketState

    api._on_events_websocket_state_change(WebsocketState.CONNECTED)
    stamp = owner._evidence.stamp()
    api._on_events_websocket_state_change(WebsocketState.DISCONNECTED)
    assert not stamp.current()
    for unsubscribe in unsubs:
        unsubscribe()
    api.api_request_raw.assert_not_awaited()
    api.api_request_list.assert_not_awaited()
