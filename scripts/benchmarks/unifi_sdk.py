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
        list_unifi_protect_events,
        serialize_unifi_camera,
        serialize_unifi_event,
    )
    from uiprotect import ProtectApiClient
    from uiprotect.data import (
        Camera,
        Event,
        ModelType,
        RecordingMode,
        SmartDetectObjectType,
    )
    from uiprotect.data.devices import (
        CameraFeatureFlags,
        RecordingSettings,
        SmartDetectSettings,
    )

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
    for size, limit in [(30, 10), (20000, 100)]:
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
                    "smartDetectTypes": ["vehicle"],
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
        reads.append(
            {
                "source_rows": size,
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
    api = client()
    kinds = [SmartDetectObjectType.VEHICLE, SmartDetectObjectType.LICENSE_PLATE]
    camera = Camera.model_construct(
        model=ModelType.CAMERA,
        id="cam-1",
        name="Gate LPR",
        type="UVC G6",
        is_adopted=True,
        is_recording=True,
        is_motion_detected=False,
        is_smart_detected=True,
        use_global=False,
        channels=[],
        smart_detect_zones=[],
        feature_flags=CameraFeatureFlags.model_construct(
            has_mic=True,
            has_package_camera=False,
            has_smart_detect=True,
            smart_detect_types=kinds,
            smart_detect_audio_types=[],
        ),
        recording_settings=RecordingSettings.model_construct(mode=RecordingMode.ALWAYS),
        smart_detect_settings=SmartDetectSettings.model_construct(
            object_types=kinds, audio_types=[]
        ),
    )
    camera._api = api
    api._bootstrap.cameras[camera.id] = camera
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
        "public_camera_projections": public_projections,
    }


if __name__ == "__main__":
    main()
