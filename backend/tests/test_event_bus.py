import asyncio
from typing import Any

import pytest

from app.services.event_bus import EventBus


class FakeWebSocket:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.fail_after = fail_after
        self.accepted = False
        self.closed = False
        self.sent: list[Any] = []
        self._send_count = 0

    async def accept(self) -> None:
        self.accepted = True

    async def close(self) -> None:
        self.closed = True

    async def send_json(self, payload: Any) -> None:
        if self.fail_after is not None and self._send_count >= self.fail_after:
            raise ValueError("socket closed")
        self._send_count += 1
        self.sent.append(payload)


class FakeRedisStreamServer:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []
        self.next_id = 1
        self.condition = asyncio.Condition()

    async def xadd(
        self,
        stream_key: str,
        fields: dict[str, Any],
        *,
        maxlen: int | None,
        approximate: bool,
    ) -> str:
        del approximate
        async with self.condition:
            record_id = f"{self.next_id}-0"
            self.next_id += 1
            self.records.append((stream_key, record_id, dict(fields)))
            if maxlen is not None and len(self.records) > maxlen:
                self.records = self.records[-maxlen:]
            self.condition.notify_all()
            return record_id

    async def xread(
        self,
        streams: dict[str, str],
        *,
        block: int,
        count: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, Any]]]]]:
        stream_key, last_id = next(iter(streams.items()))
        timeout_seconds = block / 1000 if block else 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            async with self.condition:
                records = [
                    (record_id, fields)
                    for key, record_id, fields in self.records
                    if key == stream_key and _redis_id_after(record_id, last_id)
                ]
                if records:
                    return [(stream_key, records[:count])]
                remaining = deadline - loop.time()
                if timeout_seconds <= 0 or remaining <= 0:
                    return []
                try:
                    await asyncio.wait_for(self.condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return []

    async def xinfo_stream(self, stream_key: str) -> dict[str, str]:
        matching = [record_id for key, record_id, _fields in self.records if key == stream_key]
        if not matching:
            raise ValueError("no such stream")
        return {"last-generated-id": matching[-1]}


class FakeRedisClient:
    def __init__(
        self,
        server: FakeRedisStreamServer,
        *,
        fail_xadd: bool = False,
        fail_ping: bool = False,
        fail_xread: bool = False,
    ) -> None:
        self.server = server
        self.fail_xadd = fail_xadd
        self.fail_ping = fail_ping
        self.fail_xread = fail_xread
        self.closed = False

    async def ping(self) -> bool:
        if self.fail_ping:
            raise ValueError("redis unavailable")
        return True

    async def xadd(
        self,
        stream_key: str,
        fields: dict[str, Any],
        *,
        maxlen: int | None,
        approximate: bool,
    ) -> str:
        if self.closed:
            raise ValueError("redis closed")
        if self.fail_xadd:
            raise ValueError("redis write failed")
        return await self.server.xadd(stream_key, fields, maxlen=maxlen, approximate=approximate)

    async def xread(
        self,
        streams: dict[str, str],
        *,
        block: int,
        count: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, Any]]]]]:
        if self.closed:
            raise ValueError("redis closed")
        if self.fail_xread:
            raise ValueError("redis read failed")
        return await self.server.xread(streams, block=block, count=count)

    async def xinfo_stream(self, stream_key: str) -> dict[str, str]:
        return await self.server.xinfo_stream(stream_key)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_publish_discards_failed_websocket_and_still_notifies_others() -> None:
    bus = EventBus(enable_redis=False)
    good_socket = FakeWebSocket()
    failed_socket = FakeWebSocket(fail_after=1)
    seen: list[Any] = []
    listener_called = asyncio.Event()

    async def listener(event):
        seen.append(event)
        listener_called.set()

    await bus.connect(good_socket)
    await bus.connect(failed_socket)
    bus.subscribe(listener)

    await bus.publish("access_event.finalized", {"registration_number": "AGS7X"})
    await asyncio.wait_for(listener_called.wait(), timeout=1)
    await _wait_until(lambda: failed_socket not in bus._connections)
    await _wait_until(lambda: len(good_socket.sent) == 2)

    assert failed_socket not in bus._connections
    assert good_socket in bus._connections
    assert good_socket.sent[1]["type"] == "access_event.finalized"
    assert good_socket.sent[1]["payload"] == {"registration_number": "AGS7X"}
    assert seen[0].type == "access_event.finalized"

    await bus.stop()


@pytest.mark.asyncio
async def test_redis_stream_fans_out_remote_websockets_and_all_worker_listeners() -> None:
    server = FakeRedisStreamServer()

    def factory(_url: str) -> FakeRedisClient:
        return FakeRedisClient(server)

    publisher = EventBus(redis_url="redis://test", redis_client_factory=factory, stream_key="test:events")
    subscriber = EventBus(redis_url="redis://test", redis_client_factory=factory, stream_key="test:events")
    remote_socket = FakeWebSocket()
    origin_seen: list[Any] = []
    remote_default_seen: list[Any] = []
    remote_all_worker_seen: list[Any] = []

    async def origin_listener(event):
        origin_seen.append(event)

    async def remote_default_listener(event):
        remote_default_seen.append(event)

    async def remote_all_worker_listener(event):
        remote_all_worker_seen.append(event)

    try:
        await subscriber.start()
        await publisher.start()
        await subscriber.connect(remote_socket)
        publisher.subscribe(origin_listener, scope="all_workers")
        subscriber.subscribe(remote_default_listener)
        subscriber.subscribe(remote_all_worker_listener, scope="all_workers")

        await publisher.publish("access_event.finalized", {"registration_number": "AGS7X"})

        await _wait_until(lambda: len(origin_seen) == 1)
        await _wait_until(lambda: len(remote_socket.sent) == 2)
        await _wait_until(lambda: len(remote_all_worker_seen) == 1)
        await asyncio.sleep(0.05)

        assert origin_seen[0].type == "access_event.finalized"
        assert remote_socket.sent[1]["payload"] == {"registration_number": "AGS7X"}
        assert remote_all_worker_seen[0].created_at == origin_seen[0].created_at
        assert remote_default_seen == []
        assert publisher.status()["redis_events_published"] == 1
        assert subscriber.status()["redis_events_received"] == 1
    finally:
        await publisher.stop()
        await subscriber.stop()


@pytest.mark.asyncio
async def test_redis_publish_failure_keeps_local_delivery_and_marks_degraded() -> None:
    server = FakeRedisStreamServer()

    def factory(_url: str) -> FakeRedisClient:
        return FakeRedisClient(server, fail_xadd=True)

    bus = EventBus(redis_url="redis://test", redis_client_factory=factory, stream_key="test:events")
    seen: list[Any] = []
    listener_called = asyncio.Event()

    async def listener(event):
        seen.append(event)
        listener_called.set()

    try:
        await bus.start()
        bus.subscribe(listener)

        await bus.publish("gate.state_changed", {"state": "open"})
        await asyncio.wait_for(listener_called.wait(), timeout=1)

        status = bus.status()
        assert seen[0].type == "gate.state_changed"
        assert status["transport"] == "redis_stream"
        assert status["redis_publish_failures"] == 1
        assert "redis write failed" in status["redis_last_publish_error"]
    finally:
        await bus.stop()


def _redis_id_after(candidate: str, last_id: str) -> bool:
    return _redis_id_tuple(candidate) > _redis_id_tuple(last_id)


def _redis_id_tuple(value: str) -> tuple[int, int]:
    head, _, tail = value.partition("-")
    return int(head or 0), int(tail or 0)


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    assert predicate()
