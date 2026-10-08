"""Synthetic UniFi callback comparison against the frozen pre-owner service.

Run in a copied validation source with exact locked tools. All socket connections
are denied before application imports. This is a Python workload probe, never a
provider/hardware test or a production sizing/latency claim.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
from time import perf_counter
import tracemalloc
from types import SimpleNamespace

BASELINE_SHA256 = "26536be8822fe84e6c80ead4b5fc5fe8d5bf9873355b3dd86709316e5d9b73b4"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-service", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert (
        hashlib.sha256(args.baseline_service.read_bytes()).hexdigest()
        == BASELINE_SHA256
    )
    root = Path(__file__).resolve().parents[2]
    assert not (root / ".env").exists() and not (root / "backend/.env").exists()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for key in tuple(os.environ):
        if key.startswith("IACS_"):
            del os.environ[key]
    os.environ.update(
        {
            "IACS_ENVIRONMENT": "testing",
            "IACS_AUTO_CREATE_SCHEMA": "false",
            "IACS_AUTH_SECRET_KEY": "validation-synthetic-auth-root-never-production",
            "IACS_DATABASE_URL": "postgresql+asyncpg://validation:synthetic-validation-only@127.0.0.1:1/unifi_probe",
            "IACS_REDIS_URL": "redis://127.0.0.1:1/1",
            "IACS_DATA_DIR": str(args.output.parent / "runtime"),
            "IACS_LOG_DIR": str(args.output.parent / "logs"),
        }
    )

    def deny(*args, **kwargs):
        raise OSError("UniFi synthetic workload denies every socket connection")

    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root / "backend"))
    result = asyncio.run(compare(args.baseline_service))
    result["baseline_service_sha256"] = BASELINE_SHA256
    result["python"] = sys.version
    result["limits"] = (
        "Python callback/retained-work comparison only; no provider, DB, Linux or production claim. "
        "Frozen baseline callback code uses the same current pure extractors/recorder behavior and locked tools. "
        "SDK/extractor transient allocations are included in peak Python allocation, separate from queue charges. "
        "Batched cases require equal observable output; pressure cases intentionally reject excess work."
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "equivalent_cases": len(result["equivalent"]),
                "pressure_cases": len(result["pressure"]),
            }
        )
    )


async def compare(baseline_path):
    from datetime import UTC, datetime
    import app.services.unifi_protect as current
    import app.services.unifi_protect_runtime as runtime
    from app.services.lpr_timing import LprTimingRecorder
    from app.services.vehicle_visual_detections import (
        VehiclePresenceTracker,
        VehicleVisualDetectionRecorder,
    )

    spec = importlib.util.spec_from_file_location(
        "unifi_frozen_baseline", baseline_path
    )
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    now = datetime(2026, 10, 8, 10, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    current.datetime = baseline.datetime = Clock

    class Event:
        model = "event"
        type = "motion"
        camera_id = "camera"
        camera = SimpleNamespace(id="camera", display_name="Driveway")
        smart_detect_types = ("vehicle",)
        license_plate = "AB12CDE"
        start = now
        end = None
        metadata = None

        def __init__(self, index):
            self.id = str(index)

        def model_dump(self, **kwargs):
            return {"attributes": {"vehicleType": "sedan", "color": "gray"}}

    async def run(kind, count, pressure):
        gate = asyncio.Event()
        if not pressure:
            gate.set()
        outputs, output_counts = {}, {}

        async def publish(event_type, payload, **kwargs):
            await gate.wait()
            if "observation" in payload:
                payload = {
                    **payload,
                    "observation": {
                        k: v for k, v in payload["observation"].items() if k != "id"
                    },
                }
            digest = outputs.setdefault(event_type, hashlib.sha256())
            digest.update(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            )
            output_counts[event_type] = output_counts.get(event_type, 0) + 1

        runtime.event_bus.publish = publish
        timing, visual, presence = (
            LprTimingRecorder(),
            VehicleVisualDetectionRecorder(),
            VehiclePresenceTracker(),
        )
        runtime.get_lpr_timing_recorder = baseline.get_lpr_timing_recorder = lambda: (
            timing
        )
        runtime.get_vehicle_visual_detection_recorder = (
            baseline.get_vehicle_visual_detection_recorder
        ) = lambda: visual
        runtime.get_vehicle_presence_tracker = baseline.get_vehicle_presence_tracker = (
            lambda: presence
        )
        service = (
            baseline.UnifiProtectIntegrationService()
            if kind == "before"
            else current.UnifiProtectIntegrationService()
        )

        async def forbid_api(**kwargs):
            raise AssertionError("Workload must not invoke a provider")

        service._ensure_api = forbid_api
        if kind == "after":
            service._runtime = runtime.UnifiProtectRuntime(
                SimpleNamespace(), service._evidence, service._counters
            )
            service._runtime.start()
        tracemalloc.start()
        started = perf_counter()
        try:
            for index in range(count):
                service._handle_websocket_message(
                    SimpleNamespace(
                        new_obj=Event(index),
                        old_obj=None,
                        action="add",
                        changed_data={"modelKey": "event"},
                    )
                )
                if not pressure and (index + 1) % 20 == 0:
                    async with asyncio.timeout(10):
                        while (
                            service._background_tasks
                            if kind == "before"
                            else service._runtime.status()["callback_records"]
                        ):
                            await asyncio.sleep(0)
            status = {} if kind == "before" else service._runtime.status()
            tasks = (
                len(service._background_tasks)
                if kind == "before"
                else len(service._runtime._tasks)
            )
            current_bytes, peak = tracemalloc.get_traced_memory()
            elapsed = perf_counter() - started
        finally:
            tracemalloc.stop()
            if kind == "before":
                await service._stop_locked()
            else:
                await service._runtime.stop()
        return {
            "count": count,
            "kind": kind,
            "seconds": elapsed,
            "peak_python_bytes": peak,
            "current_python_bytes": current_bytes,
            "owned_tasks": tasks,
            "runtime": status,
            "output_counts": output_counts,
            "output_hashes": {key: value.hexdigest() for key, value in outputs.items()},
        }

    equivalent, pressure = [], []
    for count in (20, 2000):
        for repeat in range(3):
            before, after = (
                await run("before", count, False),
                await run("after", count, False),
            )
            assert before["output_counts"] == after["output_counts"]
            assert before["output_hashes"] == after["output_hashes"]
            equivalent.append({"repeat": repeat, "before": before, "after": after})
        before, after = (
            await run("before", count, True),
            await run("after", count, True),
        )
        assert after["owned_tasks"] == 5
        assert after["runtime"]["callback_records"] <= 256
        assert after["runtime"]["callback_bytes"] <= 16 * 1024 * 1024
        assert after["runtime"]["callback_drops"] == max(0, count - 256)
        pressure.append({"before": before, "after": after})
    return {"equivalent": equivalent, "pressure": pressure}


if __name__ == "__main__":
    main()
