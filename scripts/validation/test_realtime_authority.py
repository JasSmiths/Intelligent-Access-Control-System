"""Actual ASGI WebSocket connections with real isolated auth/PostgreSQL owners."""
from test_account_authority import api as api, approved, headers
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.api.v1 import realtime
from app.db.session import AsyncSessionLocal
from app.models import User
from app.services import auth
from app.services.event_bus import EventBus, REALTIME_WEBSOCKET_QUEUE_SIZE

pytestmark = pytest.mark.asyncio


class Socket:
    """ASGI frames traverse the real FastAPI route and Starlette WebSocket."""
    def __init__(self, app, token, *, block_type=None, fail_type=None, block_close=False, hold_cancellation=False):
        self.app, self.token = app, token
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.block_type, self.fail_type = block_type, fail_type
        self.blocked, self.release, self.failed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.block_close, self.hold_cancellation = block_close, hold_cancellation
        self.close_entered, self.close_release = asyncio.Event(), asyncio.Event()
        self.cleanup_entered, self.cleanup_release = asyncio.Event(), asyncio.Event()
        self.task = None

    async def start(self):
        scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws", "path": "/api/v1/ws", "raw_path": b"/api/v1/ws", "query_string": b"",
            "root_path": "", "headers": [(b"authorization", ("Bearer " + self.token).encode())],
            "client": ("synthetic", 1), "server": ("synthetic", 80), "subprotocols": []}
        await self.incoming.put({"type": "websocket.connect"})
        self.task = asyncio.create_task(self.app(scope, self.incoming.get, self.send_from_app))
        frame = await asyncio.wait_for(self.outgoing.get(), 3)
        if frame["type"] == "websocket.close":
            return {"type": "__closed__", "code": frame["code"]}
        assert frame["type"] == "websocket.accept"
        return await self.next()

    async def send_from_app(self, frame):
        if frame["type"] == "websocket.send":
            message = json.loads(frame.get("text") or frame["bytes"])
            if message["type"] == self.block_type:
                self.blocked.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    if self.hold_cancellation:
                        self.cleanup_entered.set()
                        await self.cleanup_release.wait()
                    raise
            if message["type"] == self.fail_type:
                self.failed.set()
                raise RuntimeError("synthetic transport failure")
        if frame["type"] == "websocket.close" and self.block_close:
            self.close_entered.set()
            await self.close_release.wait()
        await self.outgoing.put(frame)

    async def next(self, timeout=3):
        frame = await asyncio.wait_for(self.outgoing.get(), timeout)
        if frame["type"] == "websocket.close":
            return {"type": "__closed__", "code": frame["code"]}
        assert frame["type"] == "websocket.send"
        return json.loads(frame.get("text") or frame["bytes"])

    async def ping(self):
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps({"type": "client.ping", "payload": {"id": "synthetic-probe"}})})

    async def stop(self):
        self.release.set()
        self.cleanup_release.set()
        self.close_release.set()
        await self.incoming.put({"type": "websocket.disconnect", "code": 1000})
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, 3)
            except asyncio.CancelledError:
                pass


@pytest_asyncio.fixture
async def stream(api, monkeypatch):
    bus = EventBus(enable_redis=False)
    monkeypatch.setattr(realtime, "event_bus", bus)
    api.app.include_router(realtime.router, prefix="/api/v1")
    await bus.start()
    sockets = []
    async def open_socket(name="beta", token=None, **kwargs):
        sock = Socket(api.app, token or api.tokens[name], **kwargs)
        sockets.append(sock)
        sock.initial = await sock.start()
        return sock
    try:
        yield bus, open_socket
    finally:
        for sock in sockets:
            await sock.stop()
        await bus.stop()


async def test_standard_socket_receives_safe_hint_and_internal_listener_keeps_full_audit(api, stream):
    bus, connect = stream
    admin, standard = await connect(), await connect("standard")
    assert admin.initial["type"] == standard.initial["type"] == "connection.ready"
    observed = []
    async def listener(event):
        observed.append(event.payload)
    bus.subscribe(listener)
    payload = {"log": {"action": "user.update", "diff": {"private": "synthetic-admin-detail"}, "metadata": {"private": True}}}
    await bus.publish("audit.log.created", payload)
    privileged, common = await admin.next(), await standard.next()
    assert privileged["payload"] == payload and observed == [payload]
    assert common["type"] == "audit.log.created" and common["payload"].get("action") == "user.update"
    assert "log" not in common["payload"] and "synthetic-admin-detail" not in json.dumps(common)


async def test_idle_socket_closes_at_actual_jwt_expiry(api, stream):
    _, connect = stream
    now = int(datetime.now(UTC).timestamp())
    token = auth._encode_jwt({"sub": str(api.ids["beta"]), "role": "admin", "ver": 0,
        "iat": now, "exp": now + 2, "jti": "synthetic-" + uuid.uuid4().hex})
    sock = await connect(token=token)
    assert sock.initial["type"] == "connection.ready"
    assert (await sock.next(timeout=4))["type"] == "__closed__"


@pytest.mark.parametrize("change", ["logout", "reset", "disable", "delete", "downgrade"])
async def test_idle_socket_loses_authority_after_real_account_or_logout_mutation(api, stream, change):
    _, connect = stream
    sock = await connect()
    assert sock.initial["type"] == "connection.ready"
    path = f"/api/v1/users/{api.ids['beta']}"
    if change == "logout":
        result = await api.client.post("/api/v1/auth/logout", headers=headers(api, "beta"))
    elif change == "reset":
        token = await approved(api, "user.reset_password", "beta")
        result = await api.client.post(path + "/reset-password", headers=headers(api),
            json={"temporary_password": "synthetic-realtime-password", "confirmation_token": token})
    elif change == "delete":
        token = await approved(api, "user.delete", "beta")
        result = await api.client.request("DELETE", path, headers=headers(api), json={"confirmation_token": token})
    else:
        changes = {"is_active": False} if change == "disable" else {"role": "standard"}
        token = await approved(api, "user.update", "beta", changes=changes)
        result = await api.client.patch(path, headers=headers(api), json={**changes, "confirmation_token": token})
    assert result.status_code in (200, 204)
    assert (await sock.next(timeout=4))["type"] == "__closed__"


async def test_default_queue_overflow_closes_transport_and_never_returns_healthy_pong(api, stream):
    bus, connect = stream
    sock = await connect(block_type="synthetic.blocked")
    await bus.publish("synthetic.blocked", {})
    await asyncio.wait_for(sock.blocked.wait(), 2)
    for number in range(REALTIME_WEBSOCKET_QUEUE_SIZE + 1):
        await bus.publish("synthetic.event", {"number": number})
    await sock.ping()
    assert (await sock.next(timeout=2))["type"] == "__closed__"


async def test_sender_failure_closes_transport_instead_of_answering_pings(api, stream):
    bus, connect = stream
    sock = await connect(fail_type="synthetic.failure")
    await bus.publish("synthetic.failure", {})
    await asyncio.wait_for(sock.failed.wait(), 2)
    await sock.ping()
    assert (await sock.next(timeout=2))["type"] == "__closed__"


async def test_queued_privileged_payload_is_not_sent_after_role_loss(api, stream):
    bus, connect = stream
    sock = await connect(block_type="synthetic.blocked")
    await bus.publish("synthetic.blocked", {})
    await asyncio.wait_for(sock.blocked.wait(), 2)
    await bus.publish("audit.log.created", {"log": {"action": "user.update", "diff": {"private": "synthetic-queued-admin"}}})
    token = await approved(api, "user.update", "beta", changes={"role": "standard"})
    result = await api.client.patch(f"/api/v1/users/{api.ids['beta']}", headers=headers(api),
        json={"role": "standard", "confirmation_token": token})
    assert result.status_code == 200
    sock.release.set()
    message = await sock.next()
    if message["type"] == "synthetic.blocked":
        message = await sock.next()
    assert message["type"] == "__closed__" and "synthetic-queued-admin" not in json.dumps(message)


async def test_valid_socket_control_and_initial_rejection(api, stream):
    _, connect = stream
    rejected = await connect(token="synthetic-invalid-token")
    assert rejected.initial["type"] == "__closed__"
    valid = await connect("standard")
    assert valid.initial["type"] == "connection.ready"
    await valid.ping()
    assert (await valid.next())["type"] == "connection.pong"


async def test_authorization_store_failure_closes_idle_and_never_sends_restricted_data(api, stream, monkeypatch):
    bus, connect = stream
    sock = await connect()
    async def failed_store(*args, **kwargs):
        raise RuntimeError("synthetic authorization store unavailable")
    monkeypatch.setattr(realtime, "authenticated_token" if hasattr(realtime, "authenticated_token") else "authenticate_websocket", failed_store)
    await bus.publish("audit.log.created", {"log": {"diff": {"private": "must-not-leak"}}})
    assert (await sock.next())["type"] == "__closed__"


@pytest.mark.parametrize("blocked", ["authority", "transport"])
async def test_expiry_interrupts_blocked_authority_or_transport(api, stream, monkeypatch, blocked):
    bus, connect = stream
    now = int(datetime.now(UTC).timestamp())
    token = auth._encode_jwt({"sub": str(api.ids["beta"]), "role": "admin", "ver": 0,
        "iat": now, "exp": now + 2, "jti": "synthetic-" + uuid.uuid4().hex})
    sock = await connect(token=token, block_type="synthetic.blocked" if blocked == "transport" else None)
    entered = asyncio.Event()
    if blocked == "authority":
        import importlib.util
        if importlib.util.find_spec("app.services.realtime_connections") is not None:
            from app.services import realtime_connections
            monkeypatch.setattr(realtime_connections, "AUTHORITY_TIMEOUT_SECONDS", 10.0)
        async def blocked_store(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(realtime, "authenticated_token" if hasattr(realtime, "authenticated_token") else "authenticate_websocket", blocked_store)
    await bus.publish("synthetic.blocked", {})
    await asyncio.wait_for(entered.wait() if blocked == "authority" else sock.blocked.wait(), 2)
    assert (await sock.next(timeout=4))["type"] == "__closed__"


async def test_slow_client_does_not_block_healthy_client_or_shutdown_cleanup(api, stream):
    bus, connect = stream
    slow, healthy = await connect(block_type="synthetic.blocked"), await connect()
    await bus.publish("synthetic.blocked", {})
    await asyncio.wait_for(slow.blocked.wait(), 2)
    assert (await healthy.next())["type"] == "synthetic.blocked"
    await bus.stop()
    assert (await slow.next())["type"] == (await healthy.next())["type"] == "__closed__"
    await asyncio.wait_for(asyncio.gather(slow.task, healthy.task), 3)
    assert bus.status()["connections"] == 0
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("realtime-")]


async def test_repeated_connections_and_request_cancellation_drain_children(api, stream):
    bus, connect = stream
    for _ in range(5):
        sock = await connect()
        await sock.stop()
        assert bus.status()["connections"] == 0
    cancelled = await connect(block_type="synthetic.blocked")
    await bus.publish("synthetic.blocked", {})
    await asyncio.wait_for(cancelled.blocked.wait(), 2)
    cancelled.task.cancel()
    await asyncio.gather(cancelled.task, return_exceptions=True)
    assert bus.status()["connections"] == 0
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("realtime-")]


async def test_common_refresh_categories_and_console_copy_are_explicit(api, stream):
    bus, connect = stream
    sock = await connect("standard")
    for kind in ["access_event.finalized", "access_event.updated", "telemetry.trace.completed", "home_assistant.status", "access_device.status", "visitor_pass.updated", "schedule.updated"]:
        await bus.publish(kind, {"private": "synthetic-privileged", "metadata": {"private": True}})
        message = await sock.next()
        assert message["type"] == kind and message["payload"] == {}
    await bus.publish("notification.in_app", {"title": "Visitor arrived", "body": "Synthetic console copy",
        "event_type": "arrival", "severity": "info", "rule_id": "private-rule", "metadata": {"secret": True},
        "actions": [{"path": "/api/v1/private"}], "snapshot": {"image_url": "/api/v1/snapshots/synthetic", "credentials": "private"}})
    message = await sock.next()
    assert message["payload"] == {"title": "Visitor arrived", "body": "Synthetic console copy",
        "event_type": "arrival", "severity": "info", "snapshot": {"image_url": "/api/v1/snapshots/synthetic"}}


# Captured before fixture patching. The extra endpoint is the harness's
# disposable Redis inside the already verified loopback-only namespace.
import os
import socket
from pathlib import Path
from sqlalchemy import event
from app.db.session import engine

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex


@pytest.fixture
def redis_boundary(isolated_resources, monkeypatch):
    from urllib.parse import urlsplit
    url = urlsplit(os.environ["IACS_REDIS_URL"])
    # validate.py assigns each serial suite its own numeric Redis DB.
    assert url.scheme == "redis" and url.hostname == "127.0.0.1" and url.port == 6379
    assert url.path[1:].isdigit() and not url.username and not url.password and not url.query and not url.fragment
    def checked(original):
        def connect(sock, address):
            assert isinstance(address, tuple) and address[:2] in (("127.0.0.1", 5432), ("127.0.0.1", 6379))
            return original(sock, address)
        return connect
    monkeypatch.setattr(socket.socket, "connect", checked(_original_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", checked(_original_connect_ex))


async def test_real_redis_fanout_preserves_internal_scopes_and_revocation_across_workers(api, stream, redis_boundary, monkeypatch):
    _, connect = stream
    key = "synthetic:security-realtime:" + uuid.uuid4().hex
    publisher = EventBus(redis_url=os.environ["IACS_REDIS_URL"], stream_key=key)
    subscriber = EventBus(redis_url=os.environ["IACS_REDIS_URL"], stream_key=key)
    monkeypatch.setattr(realtime, "event_bus", subscriber)
    local, remote, mutation = [], [], []
    async def on_local(event):
        local.append(event.payload)
    async def on_remote(event):
        remote.append(event.payload)
    async def on_mutation(event):
        mutation.append(event.payload)
    publisher.subscribe(on_local)
    subscriber.subscribe(on_remote, scope="all_workers")
    subscriber.subscribe(on_mutation)
    try:
        await publisher.start()
        await subscriber.start()
        admin, standard = await connect(), await connect("standard")
        payload = {"log": {"action": "settings.update", "diff": {"private": "synthetic-redis"}}}
        receipt = await publisher.publish("audit.log.created", payload)
        assert receipt.accepted and receipt.transport == "redis_stream"
        assert (await admin.next())["payload"] == payload
        assert (await standard.next())["payload"] == {"action": "settings.update"}
        assert local == remote == [payload] and mutation == []
        result = await api.client.post("/api/v1/auth/logout", headers=headers(api, "beta"))
        assert result.status_code == 200
        await publisher.publish("audit.log.created", payload)
        assert (await admin.next())["type"] == "__closed__"
        assert (await standard.next())["payload"] == {"action": "settings.update"}
    finally:
        await publisher.stop()
        await subscriber.stop()


@pytest.mark.parametrize("connections", [1, 10])
async def test_synthetic_authorization_query_and_send_latency_evidence(api, stream, tmp_path, connections):
    bus, connect = stream
    sockets = [await connect() for _ in range(connections)]
    for _ in range(5):
        await bus.publish("synthetic.measurement", {})
        await asyncio.gather(*(sock.next() for sock in sockets))
    statements, samples = [], []
    def count(*args):
        statements.append(1)  # Count only; never retain SQL parameters.
    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        for number in range(50):
            started = asyncio.get_running_loop().time()
            await bus.publish("synthetic.measurement", {"number": number})
            messages = await asyncio.gather(*(sock.next() for sock in sockets))
            assert all(message["payload"]["number"] == number for message in messages)
            samples.append((asyncio.get_running_loop().time() - started) * 1000)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    ordered = sorted(samples)
    result = {"environment": "isolated PostgreSQL / ASGI transport, no network wire measurement",
        "warmup": 5, "samples": 50, "users": 3, "connections": connections, "sql_statements": len(statements),
        "p50_ms": ordered[24], "p95_ms": ordered[47], "max_ms": ordered[-1],
        "expected_current_authority_queries_per_send": 2}
    (tmp_path / "realtime-authorization.json").write_text(json.dumps(result, indent=2))


@pytest.mark.parametrize("boundary", ["children", "transport"])
async def test_repeated_route_cancellation_drains_complete_cleanup_and_shutdown(api, stream, boundary):
    bus, connect = stream
    sock = await connect(block_type="synthetic.blocked" if boundary == "children" else None,
        hold_cancellation=boundary == "children", block_close=boundary == "transport")
    connection = next(iter(bus._connections.values()))
    if boundary == "children":
        await bus.publish("synthetic.blocked", {})
        await asyncio.wait_for(sock.blocked.wait(), 2)
    sock.task.cancel()
    await asyncio.wait_for(sock.cleanup_entered.wait() if boundary == "children" else sock.close_entered.wait(), 2)
    sock.task.cancel()
    shutdown = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    sock.cleanup_release.set()
    sock.close_release.set()
    await asyncio.gather(sock.task, return_exceptions=True)
    try:
        await asyncio.wait_for(shutdown, 3)
    finally:
        shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)
    assert connection.done.is_set()
    assert (await sock.next())["type"] == "__closed__"
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("realtime-")]


async def test_concurrent_stop_waiters_own_cleanup_even_if_first_waiter_cancelled(api, stream):
    bus, connect = stream
    sock = await connect(block_close=True)
    first = asyncio.create_task(bus.stop())
    await asyncio.wait_for(sock.close_entered.wait(), 2)
    second = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    try:
        assert not second.done(), "Concurrent shutdown returned while the connection was still owned"
        first.cancel()
    finally:
        sock.close_release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert (await sock.next())["type"] == "__closed__"
    assert bus.status()["connections"] == 0


async def test_start_and_stop_serialize_redis_initialization_and_clean_restart(api, monkeypatch):
    bus = EventBus(redis_url="redis://127.0.0.1:6379/0")
    entered, release = asyncio.Event(), asyncio.Event()
    readers = []
    async def initialize():
        entered.set()
        await release.wait()
        return None
    async def reader():
        readers.append(asyncio.current_task())
        await asyncio.Event().wait()
    monkeypatch.setattr(bus, "_ensure_redis", initialize)
    monkeypatch.setattr(bus, "_redis_stream_reader", reader)
    start = asyncio.create_task(bus.start())
    await asyncio.wait_for(entered.wait(), 2)
    stop = asyncio.create_task(bus.stop())
    await asyncio.sleep(0)
    try:
        assert not stop.done(), "Shutdown raced past unfinished initialization"
    finally:
        release.set()
        await asyncio.gather(start, stop)
        await bus.stop()
    assert not bus.status()["started"] and bus._stream_task is None
    await bus.start()
    await asyncio.sleep(0)
    assert sum(not task.done() for task in readers) == 1
    await bus.stop()
    assert all(task.done() for task in readers)


async def test_cancellation_resistant_child_keeps_owned_cleanup_visible_and_blocks_restart(api, stream, monkeypatch):
    import importlib
    bus_owner = importlib.import_module("app.services.event_bus")
    from app.services import realtime_connections
    monkeypatch.setattr(bus_owner, "REALTIME_SHUTDOWN_TIMEOUT_SECONDS", .08)
    monkeypatch.setattr(realtime_connections, "CHILD_DRAIN_SECONDS", .01)
    bus, connect = stream
    sock = await connect(block_type="synthetic.blocked", hold_cancellation=True)
    await bus.publish("synthetic.blocked", {})
    await asyncio.wait_for(sock.blocked.wait(), 2)
    try:
        with pytest.raises(RuntimeError, match="shutdown incomplete"):
            await asyncio.wait_for(bus.stop(), 2)
        assert sock.cleanup_entered.is_set()
        assert bus.status()["connections"] == bus.status()["websocket_cleanup_pending"] == 1
        with pytest.raises(RuntimeError, match="shutdown incomplete"):
            await asyncio.wait_for(bus.start(), 2)
    finally:
        sock.cleanup_release.set()
        await asyncio.wait_for(sock.task, 2)
        await bus.stop()
    assert bus.status()["connections"] == 0
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("realtime-")]
