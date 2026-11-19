"""Single-camera recovery must keep the SDK camera and its wire fields together."""

import asyncio
from unittest.mock import call

import pytest
from uiprotect.data import ModelType

from test_unifi_camera_wire import bootstrap_payload, camera_payload, client as client, sdk_frame


async def initial_bootstrap(api):
    api.api_request_obj.return_value = bootstrap_payload(camera_payload("camera-1", "Old"))
    bootstrap = await api.update()
    api.api_request_obj.reset_mock()
    return bootstrap


async def test_refresh_replaces_camera_and_all_wire_fields_in_one_read(client):
    bootstrap = await initial_bootstrap(client)
    previous = bootstrap.cameras["camera-1"]
    raw = camera_payload("camera-1", "New")
    del raw["isMotionDetected"]
    raw["name"] = "Renamed"
    client.api_request_obj.return_value = raw

    await bootstrap.refresh_device(ModelType.CAMERA, "camera-1")

    camera = bootstrap.cameras["camera-1"]
    metadata = bootstrap.camera_catalog.get(camera.id)
    assert camera is not previous and camera.name == "Renamed"
    assert metadata.zones[0].name == "New" and metadata.is_motion_detected is None
    client.api_request_obj.assert_awaited_once_with("cameras/camera-1")


@pytest.mark.parametrize("change", [{"id": "other"}, {"modelKey": "light"}, {"isAdopted": False}])
async def test_refresh_ignores_rejected_device(client, change):
    bootstrap = await initial_bootstrap(client)
    previous, metadata = bootstrap.cameras["camera-1"], bootstrap.camera_catalog.get("camera-1")
    client.api_request_obj.return_value = camera_payload("camera-1", "Unexpected") | change

    await bootstrap.refresh_device(ModelType.CAMERA, "camera-1")

    assert bootstrap.cameras["camera-1"] is previous
    assert bootstrap.camera_catalog.get("camera-1") is metadata
    client.api_request_obj.assert_awaited_once_with("cameras/camera-1")


@pytest.mark.parametrize("replacement", ["websocket", "bootstrap", "refresh"])
async def test_in_flight_refresh_yields_to_newer_camera_data(client, replacement):
    bootstrap = await initial_bootstrap(client)
    started, release = asyncio.Event(), asyncio.Event()

    async def read(path):
        if path == "bootstrap":
            return bootstrap_payload(camera_payload("camera-1", "Current"))
        assert path == "cameras/camera-1"
        if started.is_set():
            return camera_payload("camera-1", "Current")
        started.set()
        await release.wait()
        return camera_payload("camera-1", "Obsolete")

    client.api_request_obj.side_effect = read
    pending = asyncio.create_task(bootstrap.refresh_device(ModelType.CAMERA, "camera-1"))
    await asyncio.wait_for(started.wait(), 2)
    try:
        if replacement == "websocket":
            client._process_ws_message(sdk_frame("update", "camera-1", {
                "smartDetectZones": [{"id": 2, "name": "Current"}],
            }))
        elif replacement == "bootstrap":
            await client.update()
        else:
            await bootstrap.refresh_device(ModelType.CAMERA, "camera-1")
    finally:
        release.set()
        await pending

    assert client.bootstrap.camera_catalog.get("camera-1").zones[0].name == "Current"
    expected = [call("cameras/camera-1")]
    if replacement != "websocket":
        expected.append(call("bootstrap" if replacement == "bootstrap" else "cameras/camera-1"))
    assert client.api_request_obj.await_args_list == expected
