"""Process ownership contracts: every resource is inert and explicitly injected."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app import main
from app.api.v1 import health


SERVICE_NAMES = (
    "notification", "automation", "visitor_pass",
    "access_device", "vehicle_information", "access_event", "movement_reconciliation", "home_assistant",
    "gate_malfunction", "unifi_protect", "leaderboard",
)


@pytest.fixture
def owned_resources(monkeypatch):
    events = []
    controls = {"fail_start": None, "fail_stop": None}

    def service(name):
        async def start():
            events.append(("start", name))
            if controls["fail_start"] == name:
                raise RuntimeError("synthetic startup failure")

        async def stop():
            events.append(("stop", name))
            if controls["fail_stop"] == name:
                raise ValueError("synthetic cleanup failure")

        return SimpleNamespace(start=start, stop=stop)

    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "validate_startup_security_config", lambda: None)
    monkeypatch.setattr(main, "init_database", AsyncMock())
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=service("database").stop))
    monkeypatch.setattr(main, "event_bus", service("realtime"))
    monkeypatch.setattr(main, "telemetry", service("telemetry"))
    monkeypatch.setattr(main, "report_exports", service("reports"))
    monkeypatch.setattr(main, "AccessCompletionOwner", lambda *args: service("access_completion"))
    monkeypatch.setattr(main, "HaPresenceWorker", lambda *args: service("ha_presence"))
    monkeypatch.setattr(main, "vehicle_information_resources", service("vehicle_information_clients"))
    monkeypatch.setattr(main, "get_icloud_calendar_service", lambda: service("calendar"))
    for name in SERVICE_NAMES:
        instance = service(name)
        monkeypatch.setattr(main, f"get_{name}_worker" if name == "vehicle_information" else f"get_{name}_service", lambda instance=instance: instance)
    monkeypatch.setattr(main, "read_backend_runtime_state", lambda: {})

    async def task(**_kwargs):
        events.append(("task", "started"))
        try:
            await asyncio.Event().wait()
        finally:
            events.append(("task", "stopped"))

    for name in ("run_backend_runtime_heartbeat", "backfill_missed_access_events_safely",
                 "run_missed_access_event_reconciliation", "recover_missing_access_event_snapshots_safely"):
        monkeypatch.setattr(main, name, task)
    return events, controls


@pytest.mark.parametrize("failure", ("realtime", "vehicle_information_clients", "telemetry", "reports", "calendar", "access_completion", "ha_presence", *SERVICE_NAMES))
async def test_partial_startup_unwinds_every_started_service_in_reverse(owned_resources, failure):
    events, controls = owned_resources
    controls["fail_start"] = failure
    app = FastAPI()
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with main.lifespan(app):
            pytest.fail("Failed startup must not accept requests")
    started = [name for action, name in events if action == "start"]
    stopped = [name for action, name in events if action == "stop"]
    producers = {*SERVICE_NAMES, "calendar", "reports", "access_completion", "ha_presence"} - {"notification"}
    assert stopped == [
        *[name for name in reversed(started) if name in producers],
        *[name for name in reversed(started) if name not in producers],
        "database",
    ]
    assert not app.state.startup_complete
    assert not any(action == "task" for action, _ in events)


async def test_cleanup_failure_preserves_startup_error_and_continues(owned_resources):
    events, controls = owned_resources
    controls.update(fail_start="access_event", fail_stop="access_device")
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Startup failed")
    assert events[-3:] == [("stop", "realtime"), ("stop", "vehicle_information_clients"), ("stop", "database")]
    assert ("stop", "access_device") in events


async def test_normal_shutdown_drains_owned_tasks_before_services_and_database(owned_resources):
    events, controls = owned_resources
    controls["fail_stop"] = "home_assistant"
    app = FastAPI()
    async with main.lifespan(app):
        await asyncio.sleep(0)
        assert app.state.startup_complete
        assert events.count(("task", "started")) == 4
    assert not app.state.startup_complete
    assert events.count(("task", "stopped")) == 4
    first_stop = next(i for i, event in enumerate(events) if event[0] == "stop")
    assert all(i < first_stop for i, event in enumerate(events) if event == ("task", "stopped"))
    assert events[-1] == ("stop", "database")
    for caller, dependency in (("automation", "calendar"), ("access_event", "access_device"),
                               ("access_completion", "unifi_protect"), ("ha_presence", "home_assistant")):
        assert events.index(("stop", caller)) < events.index(("stop", dependency))
    for producer in ({*SERVICE_NAMES, "calendar", "reports", "access_completion", "ha_presence"} - {"notification"}):
        for sink in ("notification", "telemetry", "realtime", "vehicle_information_clients", "database"):
            assert events.index(("stop", producer)) < events.index(("stop", sink))
    for before, after in (("notification", "telemetry"), ("telemetry", "realtime"),
                          ("realtime", "vehicle_information_clients")):
        assert events.index(("stop", before)) < events.index(("stop", after))



async def test_database_initialization_failure_disposes_pool(owned_resources, monkeypatch):
    events, _ = owned_resources
    monkeypatch.setattr(main, "init_database", AsyncMock(side_effect=RuntimeError("synthetic database failure")))
    with pytest.raises(RuntimeError, match="synthetic database failure"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Database initialization failed")
    assert events == [("stop", "database")]




@pytest.mark.parametrize("started,database,worker,expected", [
    (True, "ok", True, 200), (False, "ok", True, 503),
    (True, "down", True, 503), (True, "ok", False, 503),
])
async def test_readiness_uses_core_owners_and_never_queries_optional_vendors(monkeypatch, started, database, worker, expected):
    app = FastAPI()
    app.state.startup_complete = started
    monkeypatch.setattr(health, "_database_check", AsyncMock(return_value={"status": database}))
    monkeypatch.setattr(health, "_realtime_check", lambda: {"started": True})
    monkeypatch.setattr(health, "_access_events_check", lambda: {"worker_running": worker})
    for name in ("_home_assistant_check",):
        monkeypatch.setattr(health, name, AsyncMock(side_effect=AssertionError("No optional vendor I/O")))
    response = await health.readiness(Request({"type": "http", "app": app}))
    assert response.status_code == expected


async def test_timed_out_cooperative_cleanup_is_cancelled_before_later_resources_close(owned_resources, monkeypatch):
    events, controls = owned_resources
    controls['fail_start'] = 'access_event'
    real_wait_for = asyncio.wait_for
    async def short_wait(awaitable, *, timeout):
        return await real_wait_for(awaitable, timeout=0.02)
    monkeypatch.setattr(main.asyncio, 'wait_for', short_wait)
    async def start():
        events.append(('start', 'access_device'))
    async def stop():
        events.append(('stop', 'access_device'))
        try:
            await asyncio.Event().wait()
        finally:
            events.append(('cancelled', 'access_device'))
    monkeypatch.setattr(main, 'get_access_device_service', lambda: SimpleNamespace(start=start, stop=stop))
    with pytest.raises(RuntimeError, match='synthetic startup failure'):
        async with main.lifespan(FastAPI()):
            pytest.fail('Startup failed')
    assert ('cancelled', 'access_device') in events
    assert events.index(('cancelled', 'access_device')) < events.index(('stop', 'notification'))
    assert events[-3:] == [('stop', 'realtime'), ('stop', 'vehicle_information_clients'), ('stop', 'database')]


async def test_full_lifespan_retains_calendar_observation_cleanup_before_database_disposal(owned_resources, monkeypatch):
    from app.services.icloud_calendar import ICloudCalendarService
    events, _ = owned_resources
    client = SimpleNamespace(cleanup_auth_session=lambda _: None, detach=lambda _: None)
    service = ICloudCalendarService(client)
    monkeypatch.setattr(main, "get_icloud_calendar_service", lambda: service)
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancelled = []
    async def actual_observer():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.append(asyncio.current_task().cancelling())
            cleanup.set()
            await release.wait()
    async def accepted(native):
        return {"status": "connected", "id": "inert-known-receipt"}
    async def after(receipt, native):
        return await service._observe_accepted(receipt, actual_observer, work=native.work)
    monkeypatch.setattr("app.services.icloud_calendar.CALENDAR_OBSERVATION_SECONDS", .02)
    original_wait_for = asyncio.wait_for
    async def short_stop(awaitable, *, timeout):
        return await original_wait_for(awaitable, timeout=.01)
    monkeypatch.setattr(main.asyncio, "wait_for", short_stop)
    lifetime = main.lifespan(FastAPI())
    await lifetime.__aenter__()
    request = asyncio.create_task(service.sdk.run(accepted, after_result=after))
    await entered.wait()
    shutdown = asyncio.create_task(lifetime.__aexit__(None, None, None))
    await cleanup.wait(); await asyncio.sleep(.03)
    assert not shutdown.done() and not request.done() and service.sdk._active is not None
    assert ("stop", "database") not in events and cancelled == [1]
    release.set()
    assert (await request)["id"] == "inert-known-receipt"
    await shutdown
    assert events[-1] == ("stop", "database") and service.sdk._active is None


async def test_repeated_lifespan_cancellation_retains_actual_completion_cleanup_before_sinks(owned_resources, monkeypatch):
    from app.services.access.completion_worker import AccessCompletionOwner, CompletionStageOwners
    events, _ = owned_resources
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = []
    publication = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), wake=lambda: None)
    actual = AccessCompletionOwner(SimpleNamespace(claim=AsyncMock(return_value=[])),
        CompletionStageOwners(*[AsyncMock() for _ in range(4)]), publication)
    monkeypatch.setattr(main, "AccessCompletionOwner", lambda *args: actual)
    lifetime = main.lifespan(FastAPI())
    await lifetime.__aenter__()
    async def owned_operation():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancellations.append(asyncio.current_task().cancelling())
            cleaning.set()
            await release.wait()
    operation = asyncio.create_task(owned_operation())
    actual._operations.add(operation)
    await entered.wait()
    original_wait = asyncio.wait_for
    async def short_stop(awaitable, *, timeout):
        return await original_wait(awaitable, timeout=.01)
    monkeypatch.setattr(main.asyncio, "wait_for", short_stop)
    shutdown = asyncio.create_task(lifetime.__aexit__(None, None, None))
    try:
        await cleaning.wait()
        shutdown.cancel(); await asyncio.sleep(.02)
        shutdown.cancel(); await asyncio.sleep(.02)
        assert not shutdown.done() and not operation.done()
        assert cancellations == [1]
        assert not any(("stop", sink) in events for sink in
            ("notification", "telemetry", "realtime", "vehicle_information_clients", "database"))
        publication.stop.assert_not_awaited()
    finally:
        release.set()
        await shutdown
    assert operation.done() and not actual._operations
    publication.stop.assert_awaited_once()
    assert events[-1] == ("stop", "database")
