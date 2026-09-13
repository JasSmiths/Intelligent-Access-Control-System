"""Process ownership contracts: every resource is inert and explicitly injected."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from app import main
from app.api.v1 import health
from app.services.chat import ChatService


SERVICE_NAMES = (
    "dependency_update", "notification", "automation", "discord_messaging", "visitor_pass",
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

        return SimpleNamespace(start=start, stop=stop, configure_gateway=lambda _gateway: None)

    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "validate_startup_security_config", lambda: None)
    monkeypatch.setattr(main, "init_database", AsyncMock())
    monkeypatch.setattr(main, "engine", SimpleNamespace(dispose=service("database").stop))
    monkeypatch.setattr(main, "event_bus", service("realtime"))
    for name in SERVICE_NAMES:
        instance = service(name)
        monkeypatch.setattr(main, f"get_{name}_service", lambda instance=instance: instance)
    monkeypatch.setattr(main, "chat_service", service("alfred_approvals"))
    monkeypatch.setattr(main, "alfred_feedback_service", service("alfred_feedback"))
    monkeypatch.setattr(main, "get_whatsapp_delivery_service", lambda: service("whatsapp_delivery"))
    incoming = service("whatsapp_incoming")
    def start_incoming():
        events.append(("start", "whatsapp_incoming"))
        if controls["fail_start"] == "whatsapp_incoming":
            raise RuntimeError("synthetic startup failure")
    incoming.start = start_incoming
    monkeypatch.setattr(main, "get_whatsapp_incoming_dispatcher", lambda: incoming)
    discord_incoming = service("discord_incoming")
    def start_discord_incoming():
        events.append(("start", "discord_incoming"))
        if controls["fail_start"] == "discord_incoming":
            raise RuntimeError("synthetic startup failure")
    discord_incoming.start = start_discord_incoming
    monkeypatch.setattr(main, "DiscordIncomingGateway", lambda _service, **_handlers: discord_incoming)
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


@pytest.mark.parametrize("failure", ("realtime", "alfred_feedback", "whatsapp_incoming", "discord_incoming", *SERVICE_NAMES))
async def test_partial_startup_unwinds_every_started_service_in_reverse(owned_resources, failure):
    events, controls = owned_resources
    controls["fail_start"] = failure
    app = FastAPI()
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with main.lifespan(app):
            pytest.fail("Failed startup must not accept requests")
    started = [name for action, name in events if action == "start"]
    stopped = [name for action, name in events if action == "stop"]
    producers = {"discord_messaging", "discord_incoming", "automation", "unifi_protect",
                 "access_event", "movement_reconciliation", "whatsapp_incoming"}
    assert stopped == [
        *[name for name in reversed(started) if name in producers],
        "alfred_approvals",
        *[name for name in reversed(started) if name not in producers],
        "whatsapp_delivery", "database",
    ]
    assert not app.state.startup_complete
    assert not any(action == "task" for action, _ in events)


async def test_cleanup_failure_preserves_startup_error_and_continues(owned_resources):
    events, controls = owned_resources
    controls.update(fail_start="access_event", fail_stop="access_device")
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Startup failed")
    assert events[-3:] == [("stop", "realtime"), ("stop", "whatsapp_delivery"), ("stop", "database")]
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
    assert events.index(("stop", "discord_messaging")) < events.index(("stop", "discord_incoming"))
    assert events.index(("stop", "discord_incoming")) < events.index(("stop", "notification"))
    for producer in ("discord_incoming", "whatsapp_incoming", "automation", "unifi_protect"):
        assert events.index(("stop", producer)) < events.index(("stop", "alfred_approvals"))
    for sink in ("access_device", "notification", "database"):
        assert events.index(("stop", "alfred_approvals")) < events.index(("stop", sink))


async def test_database_initialization_failure_disposes_pool(owned_resources, monkeypatch):
    events, _ = owned_resources
    monkeypatch.setattr(main, "init_database", AsyncMock(side_effect=RuntimeError("synthetic database failure")))
    with pytest.raises(RuntimeError, match="synthetic database failure"):
        async with main.lifespan(FastAPI()):
            pytest.fail("Database initialization failed")
    assert events == [("stop", "database")]


async def test_incoming_shutdown_precedes_drain_of_late_shielded_approval(owned_resources, monkeypatch):
    events, _ = owned_resources
    chat = ChatService()
    deciding, allow_claim, invoking, allow_finish = (asyncio.Event() for _ in range(4))
    callback = None
    approval = SimpleNamespace(id=uuid.uuid4())

    async def decide(*_args, **_kwargs):
        deciding.set()
        await allow_claim.wait()
        return SimpleNamespace(status="claimed", approval=approval)

    async def invoke(*_args, **_kwargs):
        invoking.set()
        await allow_finish.wait()
        events.append(("approval", "finished"))

    async def stop_incoming():
        events.append(("stop", "whatsapp_incoming"))
        # Reproduce a provider callback that passes its durable claim after
        # shutdown begins, then loses its awaiting client.
        allow_claim.set()
        await asyncio.wait_for(invoking.wait(), timeout=2)
        callback.cancel()
        await asyncio.gather(callback, return_exceptions=True)
        assert chat._approval_tasks

    async def stop_chat():
        events.append(("stop", "alfred_approvals"))
        assert invoking.is_set()
        allow_finish.set()
        await chat.stop()

    chat._approvals = SimpleNamespace(decide=decide)
    monkeypatch.setattr(chat, "_run_claimed_approval", invoke)
    monkeypatch.setattr(main, "chat_service", SimpleNamespace(stop=stop_chat))
    monkeypatch.setattr(main, "get_whatsapp_incoming_dispatcher", lambda: SimpleNamespace(
        start=lambda: None, stop=stop_incoming))
    try:
        async with main.lifespan(FastAPI()):
            callback = asyncio.create_task(chat.handle_tool_confirmation(
                confirmation_id=str(approval.id), decision="confirm",
                session_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()), user_role="admin",
            ))
            await asyncio.wait_for(deciding.wait(), timeout=2)
    finally:
        allow_claim.set()
        allow_finish.set()
        if callback is not None:
            callback.cancel()
            await asyncio.gather(callback, return_exceptions=True)
        await chat.stop()
    assert not chat._approval_tasks
    assert events.index(("stop", "whatsapp_incoming")) < events.index(("stop", "alfred_approvals"))
    for sink in ("access_device", "notification", "database"):
        assert events.index(("approval", "finished")) < events.index(("stop", sink))


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
    for name in ("_home_assistant_check", "_discord_check", "_whatsapp_check"):
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
    assert events[-3:] == [('stop', 'realtime'), ('stop', 'whatsapp_delivery'), ('stop', 'database')]
