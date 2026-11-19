"""Compare bounded SDK event reads and camera projections in exact-lock snapshots.

Run this same file against baseline/candidate validation snapshots and their own
locked environments. Provider transport is synthetic; all socket connects are
denied. Counts and timings describe this fixture, never a live console.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import sys
import tracemalloc
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for key in tuple(os.environ):
        if key.startswith("IACS_"):
            del os.environ[key]
    os.environ.update(
        IACS_ENVIRONMENT="testing",
        IACS_AUTO_CREATE_SCHEMA="false",
        IACS_AUTH_SECRET_KEY="validation-synthetic-unifi-benchmark-only",
        IACS_DATABASE_URL="postgresql+asyncpg://validation:synthetic-only@127.0.0.1:1/unifi_sdk",
        IACS_REDIS_URL="redis://127.0.0.1:1/1",
        IACS_DATA_DIR="/tmp/unifi-sdk-data",
        IACS_LOG_DIR="/tmp/unifi-sdk-logs",
    )

    def deny(*args, **kwargs):
        raise AssertionError("SDK benchmark denies all socket connections")

    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    sys.dont_write_bytecode = True
    result = asyncio.run(measure())
    result.update(
        python=sys.version,
        uiprotect=version("uiprotect"),
        limits="Synthetic HTTP transport and Python allocations only. No database queries/plans, provider, hardware or production latency claim. SDK response allocation precedes adapter processing limits.",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "uiprotect": result["uiprotect"]}))


async def measure():
    from datetime import UTC, datetime, timedelta

    from app.modules.unifi_protect.client import (
        build_unifi_protect_client,
        list_unifi_protect_events,
        serialize_unifi_camera,
        serialize_unifi_event,
    )
    from uiprotect import ProtectApiClient
    from uiprotect.data import Event, ModelType, SmartDetectObjectType

    now = datetime(2026, 10, 13, 12, tzinfo=UTC)

    def client():
        api = ProtectApiClient(
            "example.invalid",
            443,
            "synthetic",
            "synthetic",
            api_key="synthetic",
            store_sessions=False,
        )
        api._bootstrap = SimpleNamespace(
            cameras={
                "cam-1": SimpleNamespace(name="Gate LPR", display_name="Gate LPR")
            },
            events={},
        )
        return api

    def output(values):
        data = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return {
            "payload_bytes": len(data),
            "output_sha256": hashlib.sha256(data).hexdigest(),
        }

    reads = []
    for size, limit in [(30, 10), (20000, 100), (30, 250), (20000, 250)]:
        api = client()
        requests = []

        async def request(url, *, params, size=size, requests=requests):
            assert url == "events"
            start = params.get("offset", 0)
            stop = min(start + params["limit"], size)
            requests.append(
                {
                    "limit": params["limit"],
                    "offset": start,
                    "rows": max(0, stop - start),
                }
            )
            return [
                {
                    "id": f"event-{i}",
                    "modelKey": "event",
                    "type": "smartDetectZone",
                    "score": 92,
                    "camera": "cam-1" if i % 2 == 0 else "cam-other",
                    "start": int((now - timedelta(seconds=i + 20)).timestamp() * 1000),
                    "end": int((now - timedelta(seconds=i + 10)).timestamp() * 1000),
                    "smartDetectTypes": ["vehicle", "licensePlate"],
                    "metadata": {"detectedThumbnails": [{
                        "type": "licensePlate",
                        "croppedId": f"crop-{i}",
                        "objectId": f"object-{i}",
                        "coord": [1, 2, 10, 12],
                        "confidence": 99,
                        "group": {
                            "id": f"group-{i}",
                            "name": "AB12 CDE",
                            "confidence": 0 if i % 4 == 0 else 85,
                        },
                        "attributes": {"color": {"val": "silver", "confidence": 88}},
                    }]},
                }
                for i in range(start, stop)
            ]

        api.api_request_list = AsyncMock(side_effect=request)
        tracemalloc.start()
        started = perf_counter()
        events = await list_unifi_protect_events(
            api,
            camera_id="cam-1",
            limit=limit,
            since=now - timedelta(hours=24),
            until=now,
        )
        public = [serialize_unifi_event(event) for event in events]
        elapsed = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        for event in public:
            source_index = int(event["id"].removeprefix("event-"))
            thumbnail, = event["metadata"]["detectedThumbnails"]
            assert thumbnail.get("name") is None
            assert thumbnail["confidence"] == 99
            assert thumbnail["group"] == {
                "id": f"group-{source_index}", "name": "AB12 CDE",
                "confidence": 0 if source_index % 4 == 0 else 85,
            }
            assert thumbnail["croppedId"] == f"crop-{source_index}"
            assert thumbnail["coord"] == [1, 2, 10, 12]
            assert thumbnail["objectId"] == f"object-{source_index}"
            assert thumbnail["attributes"]["color"] == {"val": "silver", "confidence": 88}
        reads.append(
            {
                "source_rows": size,
                "requested_limit": limit,
                "returned_rows": len(public),
                "requests": requests,
                "request_count": len(requests),
                "response_rows": sum(r["rows"] for r in requests),
                "peak_python_bytes": peak,
                "seconds": elapsed,
                **output(public),
            }
        )
        await api.close_session()
    projections = []
    config = SimpleNamespace(
        unifi_protect_host="example.invalid",
        unifi_protect_port=443,
        unifi_protect_username="synthetic",
        unifi_protect_password="synthetic",
        unifi_protect_api_key="synthetic",
        unifi_protect_verify_ssl=True,
    )
    api = await build_unifi_protect_client(config)
    kinds = [SmartDetectObjectType.VEHICLE, SmartDetectObjectType.LICENSE_PLATE]
    wire_types = [kind.value for kind in kinds]
    # Feed identical console fields through each release's normal bootstrap
    # parser, before timing repeated reads from the resulting camera.
    raw = {model.devices_key: [] for model in ModelType.bootstrap_models_types_set}
    raw["nvr"] = {"id": "synthetic-nvr", "timezone": "UTC"}
    raw["cameras"] = [{
        "modelKey": "camera",
        "id": "cam-1",
        "name": "Gate LPR",
        "type": "UVC G6",
        "isAdopted": True,
        "isRecording": True,
        "isMotionDetected": True,
        "isSmartDetected": True,
        "useGlobal": False,
        "channels": [{
            "id": 0,
            "name": "High",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "bitrate": 6000,
            "isRtspEnabled": True,
        }],
        "smartDetectZones": [{"id": 1, "name": "Driveway", "objectTypes": ["vehicle"]}],
        "featureFlags": {
            "hasMic": True,
            "hasPackageCamera": True,
            "hasSmartDetect": True,
            "smartDetectTypes": wire_types,
            "smartDetectAudioTypes": [],
        },
        "recordingSettings": {"mode": "always"},
        "smartDetectSettings": {"objectTypes": wire_types, "audioTypes": []},
    }]
    api.api_request_obj = AsyncMock(return_value=raw)
    api._bootstrap = await api.get_bootstrap()
    api.api_request_obj.assert_awaited_once_with("bootstrap")
    camera = api.bootstrap.cameras["cam-1"]
    event = Event.from_unifi_dict(
        modelKey="event",
        id="ongoing",
        type="smartDetectZone",
        camera=camera.id,
        start=int(now.timestamp() * 1000),
        end=None,
        score=92,
        smartDetectTypes=[kind.value for kind in kinds],
        api=api,
    )
    api._bootstrap.events[event.id] = event
    camera.last_smart_detect_event_ids = {kind: event.id for kind in kinds}
    projected = serialize_unifi_camera(camera)
    assert projected["is_motion_detected"] is True
    assert projected["feature_flags"]["has_package_camera"] is True
    assert projected["detections"]["active"] == ["vehicle", "license_plate"]
    assert projected["smart_detect_zones"] == [
        {"id": 1, "name": "Driveway", "object_types": ["vehicle"]}
    ]
    channel, = projected["channels"]
    assert channel["name"] == "High" and channel["is_rtsp_enabled"] is True
    for count in [20, 2000]:
        tracemalloc.start()
        started = perf_counter()
        values = [serialize_unifi_camera(camera) for _ in range(count)]
        elapsed = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        projections.append(
            {
                "camera_updates": count,
                "peak_python_bytes": peak,
                "seconds": elapsed,
                **output(values),
            }
        )
    from uiprotect.data.public_bootstrap import PublicBootstrap
    from uiprotect.data.public_devices import PublicCamera, PublicCameraFeatureFlags

    public_camera = PublicCamera.model_construct(
        id="cam-1",
        name="Gate LPR",
        has_package_camera=False,
        feature_flags=PublicCameraFeatureFlags.model_construct(
            has_mic=True,
            smart_detect_types=[SmartDetectObjectType.VEHICLE],
            smart_detect_audio_types=[],
        ),
    )
    public_camera._api = api
    api._public_bootstrap = PublicBootstrap(cameras={public_camera.id: public_camera})
    api._public_bootstrap.process_events_ws_message(
        api,
        {
            "type": "add",
            "item": {
                "id": "public-ongoing",
                "modelKey": "event",
                "type": "smartDetectZone",
                "device": public_camera.id,
                "start": int(now.timestamp() * 1000),
                "smartDetectTypes": ["vehicle"],
                "end": None,
            },
        },
    )
    public_projections = []
    public_sample = serialize_unifi_camera(public_camera)
    for count in [20, 2000]:
        tracemalloc.start()
        started = perf_counter()
        values = [serialize_unifi_camera(public_camera) for _ in range(count)]
        elapsed = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        public_projections.append(
            {
                "camera_updates": count,
                "peak_python_bytes": peak,
                "seconds": elapsed,
                **output(values),
            }
        )
    await api.close_session()
    return {
        "event_reads": reads,
        "camera_projections": projections,
        "camera_sample": projected,
        "public_camera_projections": public_projections,
        "public_camera_sample": public_sample,
    }


if __name__ == "__main__":
    main()
