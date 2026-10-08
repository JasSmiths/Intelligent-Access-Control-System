"""Inert stream fixtures: no provider, hardware, database or network operation."""

import asyncio
import gc
import json
import weakref
from dataclasses import asdict, replace
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace

import pytest

import app.services.unifi_protect as service_module
import app.services.unifi_protect_operations as operations_module
import app.services.unifi_protect_runtime as runtime_module
from app.modules.unifi_protect.client import UnifiProtectError
from app.services.lpr_timing import (
    LprTimingRecorder,
    extract_unifi_protect_lpr_observations,
    extract_unifi_protect_track_observations,
    unifi_protect_timing_fields,
)
from app.services.unifi_protect import UnifiProtectIntegrationService
from app.services.unifi_protect_contracts import (
    ProtectEvidenceGeneration,
    ProtectProbeContext,
    ProtectWorkValidity,
)
from app.services.unifi_protect_operations import ProtectOperations, ProtectOperationUnavailable
from app.services.unifi_protect_runtime import (
    CALLBACK_RECORD_CAPACITY,
    ENTRY_CONTAINER_ALLOWANCE,
    ProtectCallbackWork,
    ProtectRetention,
    ProtectRuntimeCounters,
    UnifiProtectRuntime,
    retained_size,
)
from app.services.vehicle_visual_detections import (
    VehiclePresenceTracker,
    VehicleVisualDetectionRecorder,
    _presence_from_track,
    extract_unifi_protect_track_vehicle_visual_observations,
    extract_unifi_protect_vehicle_visual_observations,
    extract_unifi_realtime_presence_observations,
    extract_unifi_track_presence_observations,
    unifi_protect_visual_fields,
)

NOW = datetime(2026, 10, 8, 10, tzinfo=UTC)
TRACK = {
    "payload": [
        {
            "licensePlate": "AB12CDE",
            "objectType": "vehicle",
            "attributes": {
                "color": {"val": "gray", "confidence": 91},
                "vehicleType": {"val": "sedan", "confidence": 82},
            },
            "timestamp": int(NOW.timestamp() * 1000),
            "confidence": 89,
        }
    ]
}


class ProbePort:
    async def fetch_track(self, event_id, deadline):
        return TRACK


@pytest.fixture(autouse=True)
def inert_recorders(monkeypatch):
    timing, visual, presence = (
        LprTimingRecorder(),
        VehicleVisualDetectionRecorder(),
        VehiclePresenceTracker(),
    )
    monkeypatch.setattr(runtime_module, "get_lpr_timing_recorder", lambda: timing)
    monkeypatch.setattr(runtime_module, "get_vehicle_visual_detection_recorder", lambda: visual)
    monkeypatch.setattr(runtime_module, "get_vehicle_presence_tracker", lambda: presence)

    async def publish(*args, **kwargs):
        pass

    monkeypatch.setattr(runtime_module.event_bus, "publish", publish)
    return timing, visual, presence


def make_runtime(port=None):
    return UnifiProtectRuntime(
        port or ProbePort(), ProtectEvidenceGeneration(), ProtectRuntimeCounters()
    )


def context(event_id="event-1", *, admitted_at=None, deadline=None, event=None):
    admitted_at = monotonic() if admitted_at is None else admitted_at
    event = event or SimpleNamespace(
        id=event_id,
        camera_id="direct",
        camera=SimpleNamespace(id="linked", name="Gate"),
        smart_detect_types=["licensePlate", "vehicle"],
    )
    return ProtectProbeContext(
        event_id,
        NOW,
        admitted_at,
        deadline or admitted_at + 90,
        unifi_protect_timing_fields(event, NOW),
        unifi_protect_visual_fields(event, NOW),
    )


def work(runtime, *, probe=None, payload=b"{}", timing=(), visual=(), presence=()):
    return ProtectCallbackWork(
        monotonic(),
        ProtectWorkValidity(runtime.evidence.stamp()),
        timing,
        visual,
        presence,
        "protect.event.detected",
        payload,
        probe,
    )


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def normalized(rows):
    return [{key: value for key, value in asdict(row).items() if key != "id"} for row in rows]


@pytest.mark.parametrize(
    "direct,linked,channels,display,name",
    [
        ("direct", "linked", False, "Display", "Name"),
        (None, "linked", False, "", "Name"),
        ("direct", None, True, "Display", "Name"),
        (None, "linked", True, "", "Name"),
    ],
)
@pytest.mark.parametrize(
    "event_id,track_id,track_camera",
    [(None, None, None), ("explicit", "track", "override"), (None, "track", "override")],
)
def test_complete_track_context_parity(
    direct, linked, channels, display, name, event_id, track_id, track_camera
):
    event = SimpleNamespace(
        id="original",
        camera_id=direct,
        camera=SimpleNamespace(id=linked, display_name=display, name=name),
        smart_detect_types=["licensePlate", "vehicle"],
    )
    if channels:
        event.channels = []
    track = {**TRACK, "eventId": track_id, "cameraId": track_camera}
    kwargs = {"event_id": event_id, "received_at": NOW, "probe_attempt": 3}
    assert normalized(
        extract_unifi_protect_track_observations(track, event=event, **kwargs)
    ) == normalized(
        extract_unifi_protect_track_observations(
            track, context=unifi_protect_timing_fields(event, NOW), **kwargs
        )
    )
    assert normalized(
        extract_unifi_protect_track_vehicle_visual_observations(track, event=event, **kwargs)
    ) == normalized(
        extract_unifi_protect_track_vehicle_visual_observations(
            track, context=unifi_protect_visual_fields(event, NOW), **kwargs
        )
    )
    assert normalized(_presence_from_track(track, event=event, **kwargs)) == normalized(
        extract_unifi_track_presence_observations(
            track, context=unifi_protect_visual_fields(event, NOW), **kwargs
        )
    )


def test_callback_extracts_original_sdk_before_queue_and_retains_no_sdk():
    service = UnifiProtectIntegrationService()
    service._runtime = make_runtime()

    class Event:
        id = "event"
        model = "event"
        camera_id = "direct"
        camera = SimpleNamespace(id="linked", display_name="Gate LPR")
        smart_detect_types = ("licensePlate", "vehicle")
        license_plate = "AB12 CDE"
        confidence = 92
        metadata = SimpleNamespace(detected_thumbnails=[])

        def model_dump(self, **kwargs):
            return {"licensePlate": "ZZ99ZZZ", "attributes": {"color": "red", "vehicleType": "van"}}

    event = Event()
    reference = weakref.ref(event)
    message = SimpleNamespace(
        new_obj=event,
        old_obj=None,
        action="update",
        changed_data={
            "modelKey": "event",
            "metadata": {
                "licensePlate": "XY11XYZ",
                "attributes": {"color": "gray", "vehicleType": "sedan"},
            },
        },
    )
    service._handle_websocket_message(message)
    queued = service._runtime._callbacks[0][0]
    receipt = datetime.fromisoformat(queued.timing[0].received_at)
    assert normalized(queued.timing) == normalized(
        extract_unifi_protect_lpr_observations(message, received_at=receipt)
    )
    assert normalized(queued.visual) == normalized(
        extract_unifi_protect_vehicle_visual_observations(message, received_at=receipt)
    )
    assert normalized(queued.presence) == normalized(
        extract_unifi_realtime_presence_observations(
            json.loads(queued.payload), received_at=receipt
        )
    )
    del message, event
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize("count", [20, 2000])
async def test_burst_has_fixed_workers_and_bounded_records(count):
    runtime = make_runtime()
    for index in range(count):
        runtime.admit(work(runtime, payload=json.dumps({"object_id": str(index)}).encode()))
    status = runtime.status()
    assert status["callback_records"] == min(count, CALLBACK_RECORD_CAPACITY)
    assert status["callback_bytes"] <= status["callback_byte_capacity"]
    assert status["callback_drops"] == max(0, count - CALLBACK_RECORD_CAPACITY)
    runtime.start()
    assert len(runtime._tasks) == 5
    await eventually(lambda: runtime.status()["callback_records"] == 0)
    await runtime.stop()
    assert runtime.status()["callback_bytes"] == 0


async def test_active_callback_remains_charged_and_overflow_immediately_invalidates(
    monkeypatch, inert_recorders
):
    runtime = make_runtime()
    started, release = asyncio.Event(), asyncio.Event()
    timing, visual, presence = inert_recorders
    stamp = runtime.evidence.stamp()
    rows = extract_unifi_protect_track_vehicle_visual_observations(
        TRACK, event_id="event", received_at=NOW
    )
    await visual.record_observations(tuple(rows), ProtectWorkValidity(stamp))
    await presence.record_observations(
        extract_unifi_track_presence_observations(
            TRACK, context=context().visual, event_id="event", received_at=NOW, probe_attempt=1
        ),
        ProtectWorkValidity(stamp),
    )
    await presence.record_unifi_payload({"cameraId": "webhook"}, received_at=NOW)

    async def blocked(*args):
        started.set()
        await release.wait()

    monkeypatch.setattr(timing, "record_observations", blocked)
    runtime.start()
    first = work(runtime)
    assert runtime.admit(first)
    first_charge = retained_size(first) + ENTRY_CONTAINER_ALLOWANCE
    await started.wait()
    assert runtime.status()["callback_bytes"] == first_charge
    for _ in range(CALLBACK_RECORD_CAPACITY - 1):
        assert runtime.admit(work(runtime))
    assert not runtime.admit(work(runtime))
    assert not stamp.current()
    assert await visual.recent_match("AB12CDE", occurred_at=NOW) is None
    assert await presence.recent_evidence(camera_id="direct", observed_at=NOW) is None
    assert await presence.recent_evidence(camera_id="webhook", observed_at=NOW) is not None
    assert runtime.status()["stream_gaps"] == 1
    release.set()
    await runtime.stop()


def test_callback_oversize_is_whole_record_and_connection_updates_coalesce(monkeypatch):
    runtime = make_runtime()
    small = work(runtime)
    monkeypatch.setattr(
        runtime_module,
        "CALLBACK_BYTE_CAPACITY",
        retained_size(small) + ENTRY_CONTAINER_ALLOWANCE + 20,
    )
    assert not runtime.admit(replace(small, payload=b"x" * 1000))
    assert runtime.status()["callback_records"] == 0
    assert runtime.status()["callback_oversize"] == 1
    for state in ("connected", "disconnected", "connected"):
        runtime.connection_changed({"state": state})
    assert json.loads(runtime._connection_update)["state"] == "connected"
    assert runtime.status()["coalesced"] == 2


def test_retention_exact_cap_and_original_timestamp_expiry():
    retention = ProtectRetention()
    for index in range(5000):
        retention.put(str(index), 10.0)
    assert retention.size(10.0) == 4096
    assert retention.get("0", 10.0) is None
    assert retention.get("4999", 609.9) == 10.0
    assert retention.size(610.0) == 0


async def test_probe_pending_active_bytes_and_exact_release(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    class BlockedPort:
        async def fetch_track(self, event_id, deadline):
            calls.append((event_id, deadline))
            if len(calls) == 4:
                started.set()
            await release.wait()
            return TRACK

    runtime = make_runtime(BlockedPort())
    runtime.start()
    for index in range(4):
        ctx = context(str(index))
        runtime._admit_probe(ctx, ProtectWorkValidity(runtime.evidence.stamp(), ctx.deadline))
    await started.wait()
    active_charge = runtime.status()["probe_bytes"]
    assert active_charge > 0 and runtime.status()["probe_active"] == 4
    for index in range(64):
        ctx = context(f"pending-{index}")
        runtime._admit_probe(ctx, ProtectWorkValidity(runtime.evidence.stamp(), ctx.deadline))
    assert runtime.status()["probe_pending"] == 64
    charge = runtime.status()["probe_bytes"]
    ctx = context("overflow")
    runtime._admit_probe(ctx, ProtectWorkValidity(runtime.evidence.stamp(), ctx.deadline))
    assert runtime.status()["probe_bytes"] == charge
    assert runtime.status()["probe_drops"] == 1
    release.set()
    await eventually(lambda: runtime.status()["probe_bytes"] == 0)
    assert runtime.status()["probe_active"] == runtime.status()["probe_pending"] == 0
    await runtime.stop()


async def test_probe_variable_context_byte_cap_and_duplicate_deadline(monkeypatch):
    runtime = make_runtime()
    first = context("same")
    validity = ProtectWorkValidity(runtime.evidence.stamp(), first.deadline)
    runtime._admit_probe(first, validity)
    charge = runtime.status()["probe_bytes"]
    runtime._admit_probe(replace(first, deadline=first.deadline + 80), validity)
    assert runtime.status()["probe_bytes"] == charge
    assert runtime._probes[0][0].deadline == first.deadline
    huge = context("huge" + "z" * 1000)
    monkeypatch.setattr(runtime_module, "PROBE_BYTE_CAPACITY", charge + 100)
    runtime._admit_probe(huge, validity)
    assert runtime.status()["probe_bytes"] == charge
    assert runtime.status()["probe_oversize"] == 1
    assert not validity.current()
    await runtime.stop()
    assert runtime.status()["probe_bytes"] == 0


async def test_late_probe_cannot_revive_evidence_or_reset_receipt(inert_recorders):
    runtime = make_runtime()
    started, release = asyncio.Event(), asyncio.Event()

    class LatePort:
        async def fetch_track(self, event_id, deadline):
            started.set()
            await release.wait()
            return TRACK

    runtime._probe_port = LatePort()
    ctx = context(deadline=monotonic() + 90)
    validity = ProtectWorkValidity(runtime.evidence.stamp(), ctx.deadline)
    task = asyncio.create_task(runtime._probe(ctx, validity))
    await started.wait()
    runtime.gap("fixture")
    release.set()
    await task
    for recorder in inert_recorders:
        assert not await recorder.recent()
    assert runtime.status()["stale_probes"] == 1
    assert ctx.received_at == NOW
    await runtime._probe(
        context("fresh"), ProtectWorkValidity(runtime.evidence.stamp(), monotonic() + 90)
    )
    rows = await inert_recorders[0].recent()
    assert rows[0]["received_at"] == NOW.isoformat()


def installed_service():
    service = UnifiProtectIntegrationService()
    api = SimpleNamespace()
    service._api = api
    operations = ProtectOperations(lambda: service._api is api and not service._stopping)
    service._operations = operations
    service._runtime = UnifiProtectRuntime(ProbePort(), service._evidence, service._counters)
    return service, api, operations


async def test_operation_reentrant_lifecycle_rejected_before_state_and_external_drain(monkeypatch):
    service, api, operations = installed_service()
    closed = []

    async def close(client):
        assert not service._lock.locked()
        closed.append(client)

    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    before = (
        service._stopped,
        service._provider_generation,
        service._evidence.value,
        service._runtime,
        service._operations,
    )

    async def operation():
        for method in (service.stop, service.restart):
            with pytest.raises(UnifiProtectError, match="own operation"):
                await method()
            assert (
                service._stopped,
                service._provider_generation,
                service._evidence.value,
                service._runtime,
                service._operations,
            ) == before
        return "done"

    assert await operations.run(operation) == "done"
    await service.stop()
    assert closed == [api] and operations.active == 0


async def test_caller_outlives_completed_operation_but_does_not_hold_shutdown(monkeypatch):
    service, api, operations = installed_service()
    finished, caller_release = asyncio.Event(), asyncio.Event()

    async def close(client):
        assert operations.active == 0

    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)

    async def caller():
        await operations.run(lambda: asyncio.sleep(0))
        finished.set()
        await caller_release.wait()

    task = asyncio.create_task(caller())
    await finished.wait()
    await asyncio.wait_for(service.stop(), 1)
    assert not task.done()
    caller_release.set()
    await task


async def test_cancelled_operation_and_stop_wait_for_actual_cleanup(monkeypatch):
    service, api, operations = installed_service()
    started, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        return "late"

    async def close(client):
        assert operations.active == 0
        closed.set()

    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    caller = asyncio.create_task(operations.run(operation))
    await started.wait()
    caller.cancel()
    stopper = asyncio.create_task(service.stop())
    await eventually(lambda: service._stopping)
    assert operations.active == 1 and not closed.is_set()
    stopper.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    with pytest.raises(asyncio.CancelledError):
        await stopper
    assert closed.is_set() and not service._stopping


async def test_media_wait_rechecks_provider_after_admission(monkeypatch):
    service, api, operations = installed_service()
    service._media_semaphore = asyncio.Semaphore(0)
    started = asyncio.Event()

    async def ensure(**kwargs):
        started.set()
        return api

    async def unexpected(client):
        raise AssertionError("stale provider executed")

    async def close(client):
        pass

    monkeypatch.setattr(service, "_ensure_api", ensure)
    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    waiter = asyncio.create_task(service._run_current(unexpected, media=True))
    await started.wait()
    await service.stop()
    service._media_semaphore.release()
    with pytest.raises(UnifiProtectError, match="changed"):
        await waiter
    assert operations.active == 0


async def test_runtime_worker_reentrant_lifecycle_and_reentrant_read(monkeypatch):
    service, api, operations = installed_service()
    runtime = service._runtime
    observed = asyncio.Event()
    before = (service._stopped, service._provider_generation, service._evidence.value)

    async def config():
        return SimpleNamespace(
            unifi_protect_host="synthetic.invalid",
            unifi_protect_username="synthetic",
            unifi_protect_password="synthetic",
            unifi_protect_api_key="synthetic",
            unifi_protect_port=443,
            unifi_protect_verify_ssl=True,
            unifi_protect_snapshot_width=640,
            unifi_protect_snapshot_height=360,
        )

    monkeypatch.setattr(service_module, "get_runtime_config", config)

    async def publish(event_type, payload):
        assert not service._lock.locked()
        status = await service.status()
        assert (
            status["camera_count"] == 0
            and status["provider_generation"] == service._provider_generation
        )
        with pytest.raises(UnifiProtectError, match="own worker"):
            await service.stop()
        assert (service._stopped, service._provider_generation, service._evidence.value) == before
        observed.set()

    async def close(client):
        pass

    monkeypatch.setattr(runtime_module.event_bus, "publish", publish)
    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    runtime.start()
    runtime.admit(work(runtime))
    await observed.wait()
    await service.stop()
    assert not runtime._tasks


async def test_old_provider_callback_rejected_and_post_gap_callback_eligible():
    service, api, operations = installed_service()
    message = SimpleNamespace(
        new_obj=None,
        old_obj=None,
        action="update",
        changed_data={"modelKey": "camera", "id": "camera"},
    )
    service._provider_generation = 2
    service._handle_websocket_message(message, provider_generation=1)
    assert service._runtime.status()["callback_records"] == 0
    service._runtime.gap("fixture")
    service._handle_websocket_message(message, provider_generation=2)
    assert service._runtime._callbacks[0][0].validity.current()


async def test_retained_evidence_stamp_does_not_retain_old_runtime_or_client(monkeypatch):
    class Client:
        pass

    service, _, _ = installed_service()
    client = Client()
    service._api = client
    runtime = service._runtime
    stamp = runtime.evidence.stamp()
    client_ref, runtime_ref = weakref.ref(client), weakref.ref(runtime)

    async def close(client):
        pass

    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    await service.stop()
    del client, runtime, service
    gc.collect()
    assert client_ref() is None and runtime_ref() is None
    assert not stamp.current()


async def test_cancel_suppressing_sdk_deadline_does_not_release_early():
    operations = ProtectOperations(lambda: True)
    cancelled, release = asyncio.Event(), asyncio.Event()
    deadline = asyncio.get_running_loop().time() + 0.01

    async def operation():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return "expired"

    caller = asyncio.create_task(operations.run(operation, deadline=deadline))
    await asyncio.wait_for(cancelled.wait(), 2)
    assert operations.active == 1
    drainer = asyncio.create_task(operations.drain())
    assert not drainer.done()
    release.set()
    with pytest.raises(TimeoutError, match="original deadline"):
        await caller
    await drainer
    assert operations.active == 0


@pytest.mark.parametrize("transition", ["closed", "replaced", "expired"])
async def test_delayed_actual_operation_rechecks_admission_provider_and_deadline(
    monkeypatch, transition
):
    current = True
    operations = ProtectOperations(lambda: current)
    entered, release = asyncio.Event(), asyncio.Event()
    drain_owned = operations_module.drain_owned
    calls = []

    async def delayed_child(awaitable):
        entered.set()
        await release.wait()
        return await drain_owned(awaitable)

    async def operation():
        calls.append("SDK operation started")
        return "obsolete"

    monkeypatch.setattr(operations_module, "drain_owned", delayed_child)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (0.02 if transition == "expired" else 60)
    caller = asyncio.create_task(operations.run(operation, deadline=deadline))
    await entered.wait()
    assert operations.active == 1 and not calls
    drainer = asyncio.create_task(operations.drain())
    if transition == "closed":
        operations.close_admission()
    elif transition == "replaced":
        current = False
    else:
        expired = asyncio.Event()
        loop.call_at(deadline, expired.set)
        await expired.wait()
    assert not drainer.done()
    release.set()
    expected = TimeoutError if transition == "expired" else ProtectOperationUnavailable
    with pytest.raises(expected):
        await caller
    await drainer
    assert not calls and operations.active == 0


async def test_restart_waits_for_blocked_actual_close_before_start(monkeypatch):
    service, api, operations = installed_service()
    closing, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def close(client):
        assert not service._lock.locked()
        closing.set()
        await release.wait()

    async def start():
        started.set()

    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    monkeypatch.setattr(service, "start", start)
    restart = asyncio.create_task(service.restart())
    await closing.wait()
    assert not started.is_set() and service._stopping
    with pytest.raises(UnifiProtectError, match="shutting down"):
        await service._ensure_api(subscribe=True)
    release.set()
    await restart
    assert started.is_set() and not service._stopping


async def test_gap_during_waiting_cache_lock_rejects_old_append(inert_recorders):
    runtime = make_runtime()
    visual = inert_recorders[1]
    rows = tuple(
        extract_unifi_protect_track_vehicle_visual_observations(
            TRACK, event_id="event", received_at=NOW
        )
    )
    validity = ProtectWorkValidity(runtime.evidence.stamp())
    await visual._lock.acquire()
    append = asyncio.create_task(visual.record_observations(rows, validity))
    await asyncio.sleep(0)
    runtime.gap("locked_cache")
    visual._lock.release()
    await append
    assert await visual.recent() == []


async def test_probe_bytes_remain_charged_until_cancellation_resistant_worker_ends():
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class CancellationResistantPort:
        async def fetch_track(self, event_id, deadline):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                return TRACK

    runtime = make_runtime(CancellationResistantPort())
    runtime.start()
    ctx = context()
    runtime._admit_probe(ctx, ProtectWorkValidity(runtime.evidence.stamp(), ctx.deadline))
    await started.wait()
    charge = runtime.status()["probe_bytes"]
    stop = asyncio.create_task(runtime.stop())
    await cancelled.wait()
    assert charge > 0 and runtime.status()["probe_bytes"] == charge
    assert runtime.status()["probe_active"] == 1
    release.set()
    await stop
    assert runtime.status()["probe_bytes"] == 0
    assert runtime.status()["probe_active"] == 0


async def test_connection_operation_reentrant_stop_rejected_and_reads_do_not_deadlock():
    service = UnifiProtectIntegrationService()
    before = (service._stopped, service._provider_generation, service._evidence.value)
    async with service._connection_scope():
        with pytest.raises(UnifiProtectError, match="own connection operation"):
            await service.stop()
        assert (service._stopped, service._provider_generation, service._evidence.value) == before
        with pytest.raises(UnifiProtectError, match="in progress"):
            await service._ensure_api(subscribe=True)
    await service.stop()


async def test_idle_workers_do_not_retain_released_callback_or_probe_locals():
    runtime = make_runtime()
    runtime.start()
    runtime.admit(work(runtime, probe=context()))
    await eventually(
        lambda: runtime.status()["handled"] == 1 and runtime.status()["probe_bytes"] == 0
    )
    for task in runtime._tasks:
        local = task.get_coro().cr_frame.f_locals
        assert "work" not in local and "context" not in local and "validity" not in local
        assert local.get("payload_dict") is None
    await runtime.stop()


@pytest.mark.parametrize("accepted", [True, False])
async def test_confirmed_provider_test_outcome_survives_concurrent_stop(monkeypatch, accepted):
    service, api, operations = installed_service()
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def send(trigger_id):
        entered.set()
        await release.wait()
        if not accepted:
            raise UnifiProtectError("Synthetic provider rejection before acceptance")
        return {"accepted": True, "receipt": "synthetic"}

    async def close(client):
        assert operations.active == 0
        closed.set()

    api.send_alarm_webhook_public = send
    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    test = asyncio.create_task(service.send_alarm_webhook_test("synthetic-trigger"))
    await entered.wait()
    stop = asyncio.create_task(service.stop())
    await eventually(lambda: service._stopping)
    assert not closed.is_set()
    release.set()
    if accepted:
        assert await test == {
            "trigger_id": "synthetic-trigger",
            "result": {"accepted": True, "receipt": "synthetic"},
        }
    else:
        with pytest.raises(UnifiProtectError, match="rejection before acceptance"):
            await test
    await stop
    assert closed.is_set()


@pytest.mark.parametrize("accepted", [True, False])
async def test_cancelled_confirmed_test_preserves_actual_outcome_after_cleanup(
    monkeypatch, accepted
):
    service, api, operations = installed_service()
    entered, release = asyncio.Event(), asyncio.Event()
    cleaning, cleanup_release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def send(trigger_id):
        calls.append(trigger_id)
        entered.set()
        try:
            await release.wait()
            if not accepted:
                raise UnifiProtectError("Synthetic provider failure before acceptance")
            return {"accepted": True, "receipt": "synthetic"}
        finally:
            cleaning.set()
            await cleanup_release.wait()

    async def close(client):
        assert operations.active == 0
        closed.set()

    api.send_alarm_webhook_public = send
    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    caller = asyncio.create_task(service.send_alarm_webhook_test("synthetic-trigger"))
    await entered.wait()
    for _ in range(3):
        caller.cancel()
        await asyncio.sleep(0)
    stop = asyncio.create_task(service.stop())
    await eventually(lambda: service._stopping)
    release.set()
    await cleaning.wait()
    caller.cancel()
    await asyncio.sleep(0)
    assert operations.active == 1 and not caller.done() and not closed.is_set()
    cleanup_release.set()
    if accepted:
        assert await caller == {
            "trigger_id": "synthetic-trigger",
            "result": {"accepted": True, "receipt": "synthetic"},
        }
    else:
        with pytest.raises(UnifiProtectError, match="failure before acceptance"):
            await caller
    await stop
    assert calls == ["synthetic-trigger"] and closed.is_set() and operations.active == 0


async def test_cancelled_read_still_cancels_after_known_result_and_actual_drain():
    service, api, operations = installed_service()
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(client):
        entered.set()
        await release.wait()
        return "known read"

    caller = asyncio.create_task(service._run_current(read))
    await entered.wait()
    caller.cancel()
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    assert operations.active == 1 and not caller.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert operations.active == 0


async def test_completed_read_from_retired_provider_is_fenced(monkeypatch):
    service, api, operations = installed_service()
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(client):
        entered.set()
        await release.wait()
        return "old read"

    async def close(client):
        pass

    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    read_task = asyncio.create_task(service._run_current(read))
    await entered.wait()
    stop = asyncio.create_task(service.stop())
    await eventually(lambda: service._stopping)
    release.set()
    with pytest.raises(UnifiProtectError, match="read became obsolete"):
        await read_task
    await stop


async def test_late_refresh_cannot_reconnect_detached_provider(monkeypatch):
    service, api, operations = installed_service()
    entered, release = asyncio.Event(), asyncio.Event()

    async def bootstrap(client):
        entered.set()
        await release.wait()

    async def close(client):
        pass

    monkeypatch.setattr(service_module, "load_unifi_protect_bootstrap", bootstrap)
    monkeypatch.setattr(service_module, "close_unifi_protect_client", close)
    refresh = asyncio.create_task(service._ensure_api(subscribe=True, refresh=True))
    await entered.wait()
    stop = asyncio.create_task(service.stop())
    await eventually(lambda: service._stopping)
    release.set()
    with pytest.raises(UnifiProtectError, match="refresh became obsolete"):
        await refresh
    await stop
    assert not service._connected and service._api is None and operations.active == 0
