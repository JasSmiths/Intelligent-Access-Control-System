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
    "access_device", "access_event", "movement_reconciliation", "home_assistant",
    "gate_malfunction", "unifi_protect",
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
    for name in SERVICE_NAMES:
        instance = service(name)
        monkeypatch.setattr(main, f"get_{name}_service", lambda instance=instance: instance)
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


@pytest.mark.parametrize("failure", ("realtime", *SERVICE_NAMES))
async def test_partial_startup_unwinds_every_started_service_in_reverse(owned_resources, failure):
    events, controls = owned_resources
    controls["fail_start"] = failure
    app = FastAPI()
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with main.lifespan(app):
            pytest.fail("Failed startup must not accept requests")
    started = [name for action, name in events if action == "start"]
    stopped = [name for action, name in events if action == "stop"]
    producers = {"automation", "unifi_protect", "access_event", "movement_reconciliation"}
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
    assert events[-2:] == [("stop", "realtime"), ("stop", "database")]
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
    for producer in ("automation", "unifi_protect", "access_event", "movement_reconciliation"):
        for sink in ("access_device", "notification", "database"):
            assert events.index(("stop", producer)) < events.index(("stop", sink))


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
    assert events[-2:] == [('stop', 'realtime'), ('stop', 'database')]
