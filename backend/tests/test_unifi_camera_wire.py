"""Run private Protect bootstrap and WebSocket camera data through the installed SDK."""

import json
import socket
import struct
from unittest.mock import AsyncMock, Mock, call

from aiohttp import WSMessage, WSMsgType
import pytest
from uiprotect.data import ModelType
from uiprotect.websocket import Websocket

from app.modules.unifi_protect.client import camera_wire_metadata, websocket_message_payload
from app.modules.unifi_protect.wire_client import IacsProtectClient
from app.services.unifi_protect import UnifiProtectIntegrationService


def camera_payload(camera_id, zone_name, *, adopted=True):
    return {
        "id": camera_id, "modelKey": "camera", "name": camera_id,
        "mac": f"00000000000{camera_id[-1]}", "isAdopted": adopted, "isAdoptedByOther": False,
        "isDark": False, "useGlobal": False, "isMotionDetected": False,
        "recordingSettings": {"mode": "never"},
        "smartDetectSettings": {"objectTypes": ["vehicle"]},
        "featureFlags": {"hasMic": True, "hasPackageCamera": False, "smartDetectTypes": ["vehicle"]},
        "smartDetectZones": [{"id": 2, "name": zone_name, "objectTypes": ["vehicle"]}],
        "channels": [{"id": 0, "name": "Main", "isRtspEnabled": True, "width": 1920, "height": 1080}],
    }


def bootstrap_payload(*cameras):
    return {
        **{model.devices_key: [] for model in ModelType.bootstrap_models_types_set},
        "cameras": list(cameras), "authUserId": "fixture-user", "lastUpdateId": "initial",
        "nvr": {"version": "7.2.0", "hosts": ["192.0.2.1"]},
    }


def sdk_frame(action, camera_id, data):
    header = {"action": action, "id": camera_id, "modelKey": "camera", "newUpdateId": "next"}
    frames = []
    for kind, value in ((1, header), (2, data)):
        body = json.dumps(value).encode()
        # Protect's private websocket sends two consecutive length-prefixed JSON frames.
        frames.append(struct.pack("!BBBBI", kind, 1, 0, 0, len(body)) + body)
    return WSMessage(WSMsgType.BINARY, b"".join(frames), "")


@pytest.fixture
def client(monkeypatch, tmp_path):
    def deny_socket(*args):
        raise AssertionError("camera fixtures must never contact the provider")

    monkeypatch.setattr(socket.socket, "connect", deny_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_socket)
    monkeypatch.setattr(socket, "create_connection", deny_socket)
    monkeypatch.setattr(Websocket, "start", Mock())
    api = IacsProtectClient(
        host="192.0.2.1", port=443, username="fixture", password="fixture",
        api_key="fixture", store_sessions=False, override_connection_host=True,
        config_dir=tmp_path, cache_dir=tmp_path, subscribed_models={ModelType.CAMERA},
    )
    api.api_request_raw = AsyncMock(side_effect=AssertionError("unexpected media or provider read"))
    api.api_request_list = AsyncMock(return_value=[])
    api.api_request_obj = AsyncMock()
    return api


async def test_private_bootstrap_and_live_camera_catalog_through_normal_sdk_dispatch(client, monkeypatch):
    client.api_request_obj.return_value = bootstrap_payload(
        camera_payload("camera-1", "Driveway"), camera_payload("camera-2", "Road"),
        camera_payload("camera-3", "Unadopted", adopted=False),
    )
    bootstrap = await client.update()
    assert set(bootstrap.cameras) == {"camera-1", "camera-2"}
    assert bootstrap.camera_catalog.get("camera-3") is None
    client.api_request_obj.assert_awaited_once_with("bootstrap")
    client.api_request_obj.reset_mock()
    client.api_request_list.reset_mock()
    service = UnifiProtectIntegrationService()
    monkeypatch.setattr(service, "_ensure_api", AsyncMock(return_value=client))

    cameras = await service.list_cameras()
    assert [camera["smart_detect_zones"][0]["name"] for camera in cameras] == ["Driveway", "Road"]
    assert cameras[0]["channels"][0]["name"] == "Main"
    assert cameras[0]["channels"][0]["is_rtsp_enabled"] is True
    assert cameras[0]["feature_flags"]["has_package_camera"] is False
    assert cameras[0]["is_motion_detected"] is False

    callbacks = []
    unsubscribe = client.subscribe_websocket(callbacks.append)
    try:
        client._process_ws_message(sdk_frame("update", "camera-1", {
            "smartDetectZones": [{"id": 2, "name": "Renamed", "objectTypes": ["vehicle"]}],
            "futureFirmwareField": "ignored",
        }))
        assert callbacks[-1].changed_data["smart_detect_zones"][0]["name"] == "Renamed"
        assert "futureFirmwareField" not in callbacks[-1].changed_data
        assert websocket_message_payload(callbacks[-1])["camera"]["smart_detect_zones"] == [
            {"id": 2, "name": "Renamed", "object_types": ["vehicle"]},
        ]
        resolution = await service.resolve_lpr_smart_zone_names(["2"], camera_identifier="camera-1")
        assert resolution["smart_zone_matches"] == [{"input": "2", "id": "2", "name": "Renamed"}]

        client._process_ws_message(sdk_frame("update", "camera-2", {"isMotionDetected": True}))
        assert websocket_message_payload(callbacks[-1])["camera"]["is_motion_detected"] is True
        assert (await service.list_cameras())[0]["is_motion_detected"] is False
        client._process_ws_message(sdk_frame("add", "camera-4", camera_payload("camera-4", "Side")))
        assert websocket_message_payload(callbacks[-1])["camera"]["smart_detect_zones"][0]["name"] == "Side"
        client._process_ws_message(sdk_frame("remove", "camera-4", {}))
        assert bootstrap.camera_catalog.get("camera-4") is None
        assert "camera-4" not in bootstrap.cameras
    finally:
        unsubscribe()
    client.api_request_obj.assert_not_awaited()
    client.api_request_list.assert_not_awaited()
    client.api_request_raw.assert_not_awaited()


async def test_private_bootstrap_refresh_replaces_catalog_atomically(client):
    client.api_request_obj.side_effect = [
        bootstrap_payload(camera_payload("camera-1", "Old"), camera_payload("camera-2", "Removed")),
        bootstrap_payload(camera_payload("camera-1", "New")),
    ]
    previous = await client.update()
    current = await client.update()
    assert previous.camera_catalog is not current.camera_catalog
    assert previous.camera_catalog.get("camera-2") is not None
    assert current.camera_catalog.get("camera-2") is None
    value = camera_wire_metadata(current.cameras["camera-1"])
    assert value is not None and value.zones is not None
    assert [zone.name for zone in value.zones] == ["New"]
    assert client.api_request_obj.await_args_list == [call("bootstrap"), call("bootstrap")]
