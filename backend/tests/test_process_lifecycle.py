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
    monkeypatch.setattr(main, "durable_work_listener", service("durable_work"))
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=service("database").stop))
    monkeypatch.setattr(main, "get_home_assistant_client",
        lambda: SimpleNamespace(close=service("home_assistant_client").stop))
    monkeypatch.setattr(main, "event_bus", service("realtime"))
    monkeypatch.setattr(main, "telemetry", service("telemetry"))
    monkeypatch.setattr(main, "report_exports", service("reports"))
    monkeypatch.setattr(main, "AccessCompletionOwner", lambda *args: service("access_completion"))
    monkeypatch.setattr(main, "HaPresenceWorker", lambda *args: service("ha_presence"))
    monkeypatch.setattr(main, "vehicle_information_resources", service("vehicle_information_clients"))
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


@pytest.mark.parametrize("failure", ("durable_work", "realtime", "vehicle_information_clients", "telemetry", "reports", "access_completion", "ha_presence", *SERVICE_NAMES))
async def test_partial_startup_unwinds_every_started_service_in_reverse(owned_resources, failure):
    events, controls = owned_resources
    controls["fail_start"] = failure
    app = FastAPI()
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with main.lifespan(app):
            pytest.fail("Failed startup must not accept requests")
    started = [name for action, name in events if action == "start"]
    stopped = [name for action, name in events if action == "stop"]
    producers = {*SERVICE_NAMES, "reports", "access_completion", "ha_presence"} - {"notification"}
    assert stopped == [
        *[name for name in reversed(started) if name in producers],
        *[name for name in reversed(started) if name not in producers],
        "home_assistant_client", "database",
    ]
    assert not app.state.startup_complete
    assert not any(action == "task" for action, _ in events)


async def test_cleanup_failure_preserves_startup_error_and_continues(owned_resources):
    events, controls = owned_resources
    controls.update(fail_start="access_event", fail_stop="access_device")
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Startup failed")
    assert events[-5:] == [("stop", "realtime"), ("stop", "vehicle_information_clients"),
        ("stop", "durable_work"), ("stop", "home_assistant_client"), ("stop", "database")]
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
    for caller, dependency in (("access_event", "access_device"),
                               ("access_completion", "unifi_protect"), ("ha_presence", "home_assistant")):
        assert events.index(("stop", caller)) < events.index(("stop", dependency))
    for producer in ({*SERVICE_NAMES, "reports", "access_completion", "ha_presence"} - {"notification"}):
        for sink in ("notification", "telemetry", "realtime", "vehicle_information_clients", "home_assistant_client", "database"):
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
    assert events == [("stop", "home_assistant_client"), ("stop", "database")]




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
    assert events[-5:] == [('stop', 'realtime'), ('stop', 'vehicle_information_clients'),
        ('stop', 'durable_work'), ('stop', 'home_assistant_client'), ('stop', 'database')]




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
            ("notification", "telemetry", "realtime", "vehicle_information_clients", "home_assistant_client", "database"))
        publication.stop.assert_not_awaited()
    finally:
        release.set()
        await shutdown
    assert operation.done() and not actual._operations
    publication.stop.assert_awaited_once()
    assert events[-1] == ("stop", "database")


async def test_shared_ha_client_outlives_blocked_notification_drain_and_private_client_is_owned(owned_resources, monkeypatch):
    from app.services import home_assistant
    events, _ = owned_resources
    shared = SimpleNamespace(close=AsyncMock())
    private = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(home_assistant, "get_home_assistant_client", lambda: shared)
    shared_listener = home_assistant.HomeAssistantIntegrationService()
    private_listener = home_assistant.HomeAssistantIntegrationService(private)
    await shared_listener.stop()
    shared.close.assert_not_awaited()
    await private_listener.stop()
    private.close.assert_awaited_once()
    cleaning, release = asyncio.Event(), asyncio.Event()
    async def notification_stop():
        cleaning.set()
        await release.wait()
        shared.close.assert_not_awaited()
        events.append(("stop", "notification"))
    async def close_shared():
        shared.close.assert_not_awaited()
        await shared.close()
        events.append(("stop", "home_assistant_client"))
    monkeypatch.setattr(main, "get_home_assistant_client", lambda: SimpleNamespace(close=close_shared))
    monkeypatch.setattr(main, "get_notification_service", lambda: SimpleNamespace(start=AsyncMock(), stop=notification_stop))
    lifetime = main.lifespan(FastAPI())
    await lifetime.__aenter__()
    shutdown = asyncio.create_task(lifetime.__aexit__(None, None, None))
    try:
        await cleaning.wait()
        shutdown.cancel(); await asyncio.sleep(.01)
        shutdown.cancel(); await asyncio.sleep(.01)
        assert not shutdown.done()
        shared.close.assert_not_awaited()
        assert ("stop", "database") not in events
    finally:
        release.set()
        await shutdown
    shared.close.assert_awaited_once()
    assert events.index(("stop", "notification")) < events.index(("stop", "home_assistant_client"))
    assert events[-2:] == [("stop", "home_assistant_client"), ("stop", "database")]
