import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import redis.asyncio as redis_asyncio
from fastapi import WebSocket

from app.core.config import settings
from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.models.enums import UserRole
from app.services.realtime_connections import Authorize, RealtimeConnection
from app.services.realtime_contracts import RealtimePublishReceipt

logger = get_logger(__name__)

REALTIME_STREAM_KEY = "iacs:realtime:events:v1"
REALTIME_STREAM_MAXLEN = 10_000
REALTIME_STREAM_READ_COUNT = 100
REALTIME_STREAM_BLOCK_MS = 1000
REALTIME_STREAM_RECONNECT_SECONDS = 1.0
REALTIME_REDIS_CONNECT_TIMEOUT_SECONDS = 0.5
REALTIME_REDIS_SOCKET_TIMEOUT_SECONDS = 2.0
REALTIME_WEBSOCKET_QUEUE_SIZE = 250
REALTIME_SHUTDOWN_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class RealtimeEvent:
    type: str
    payload: dict[str, Any]
    created_at: str


EventListener = Callable[[RealtimeEvent], Awaitable[None]]
ListenerScope = Literal["local", "all_workers"]
RedisClientFactory = Callable[[str], redis_asyncio.Redis]


@dataclass(frozen=True)
class _StreamRecord:
    event: RealtimeEvent
    origin_id: str | None


class EventBus:
    """Realtime dashboard and workflow event bus.

    Redis Streams are the cross-process fanout boundary. Every backend worker
    publishes events to the same bounded stream, and each worker reads that
    stream into its own WebSocket clients. Mutation-capable service listeners
    remain local by default so multi-worker fanout does not duplicate workflow
    side effects; explicitly safe cache/state listeners can opt into
    ``scope="all_workers"``.
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        redis_client_factory: RedisClientFactory | None = None,
        stream_key: str = REALTIME_STREAM_KEY,
        stream_maxlen: int = REALTIME_STREAM_MAXLEN,
        enable_redis: bool = True,
        websocket_queue_size: int = REALTIME_WEBSOCKET_QUEUE_SIZE,
    ) -> None:
        self._connections: dict[WebSocket, RealtimeConnection] = {}
        self._listeners: dict[EventListener, tuple[ListenerScope, frozenset[str] | None]] = {}
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._redis_lock = asyncio.Lock()
        self._started = False
        self._transport = "in_process"
        self._origin_id = uuid.uuid4().hex
        self._redis_url = redis_url or settings.redis_url
        self._redis_enabled = enable_redis and bool(self._redis_url)
        self._redis_client_factory = redis_client_factory or _redis_client_from_url
        self._redis: redis_asyncio.Redis | None = None
        self._stream_key = stream_key
        self._stream_maxlen = stream_maxlen
        self._stream_task: asyncio.Task | None = None
        self._stream_last_id = "0-0"
        self._stream_id_initialized = False
        self._redis_connected = False
        self._redis_last_error: str | None = None
        self._redis_last_publish_error: str | None = None
        self._redis_last_read_error: str | None = None
        self._redis_publish_failures = 0
        self._redis_read_failures = 0
        self._redis_events_received = 0
        self._redis_events_published = 0
        self._websocket_queue_size = websocket_queue_size
        self._websocket_queue_drops = 0

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            if self._connections or (self._stream_task is not None and not self._stream_task.done()):
                await drain_owned(self._stop())
            try:
                if self._redis_enabled:
                    self._transport = "redis_stream"
                    await self._ensure_redis()
                self._started = True
                if self._redis_enabled:
                    self._stream_task = asyncio.create_task(
                        self._redis_stream_reader(), name="event-bus-redis-stream-reader")
                logger.info("event_bus_started", extra={"transport": self._transport})
            except BaseException:
                await drain_owned(self._stop())
                raise

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            # Cancellation of a waiter cannot abandon the shutdown transition.
            await drain_owned(self._stop())

    async def _stop(self) -> None:
        self._started = False
        async with self._lock:
            connections = list(self._connections.values())
            self._listeners.clear()
            for connection in connections:
                connection.close(1001)
        stream_task = self._stream_task
        if stream_task:
            stream_task.cancel()
        waiters = [asyncio.create_task(connection.done.wait()) for connection in connections]
        owned = [*waiters, *([stream_task] if stream_task else [])]
        try:
            if owned:
                _, pending = await asyncio.wait(owned, timeout=REALTIME_SHUTDOWN_TIMEOUT_SECONDS)
                if pending:
                    raise RuntimeError("Realtime shutdown incomplete; cleanup remains owned.")
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            if stream_task is not None and stream_task.done():
                await asyncio.gather(stream_task, return_exceptions=True)
                self._stream_task = None
            await self._close_redis()
        # Incomplete connections remain visible/owned for a subsequent drain.
        async with self._lock:
            for connection in connections:
                if connection.done.is_set():
                    self._connections.pop(connection.websocket, None)
        logger.info("event_bus_stopped")

    async def serve(self, websocket: WebSocket, *, authorize: Authorize,
                    role: UserRole, expires_at: int) -> None:
        connection = RealtimeConnection(websocket, authorize, role, expires_at, self._websocket_queue_size)
        async with self._lock:
            if self._started:
                self._connections[websocket] = connection
            else:
                connection.close(1001)
        try:
            await connection.run()
        finally:
            if connection.done.is_set():
                self._connections.pop(websocket, None)

    def subscribe(
        self, listener: EventListener, *, scope: ListenerScope = "local",
        event_types: frozenset[str] | None = None,
    ) -> None:
        if scope not in {"local", "all_workers"}:
            raise ValueError(f"unsupported event listener scope: {scope}")
        self._listeners[listener] = (scope, event_types)

    def unsubscribe(self, listener: EventListener) -> None:
        self._listeners.pop(listener, None)

    def status(self) -> dict[str, Any]:
        return {
            "started": self._started,
            "transport": self._transport,
            "connections": len(self._connections),
            "listeners": len(self._listeners),
            "websocket_cleanup_pending": sum(connection.cleanup_pending for connection in self._connections.values()),
            "websocket_queue_size": self._websocket_queue_size,
            "websocket_queued_events": sum(
                connection.queue.qsize() for connection in self._connections.values()
            ),
            "websocket_queue_drops": self._websocket_queue_drops,
            "redis_connected": self._redis_connected,
            "redis_stream": self._stream_key if self._redis_enabled else None,
            "redis_last_id": self._stream_last_id if self._redis_enabled else None,
            "redis_last_error": self._redis_last_publish_error
            or self._redis_last_read_error
            or self._redis_last_error,
            "redis_last_publish_error": self._redis_last_publish_error,
            "redis_last_read_error": self._redis_last_read_error,
            "redis_publish_failures": self._redis_publish_failures,
            "redis_read_failures": self._redis_read_failures,
            "redis_events_published": self._redis_events_published,
            "redis_events_received": self._redis_events_received,
            "stream_maxlen": self._stream_maxlen if self._redis_enabled else None,
        }

    async def publish(self, event_type: str, payload: dict[str, Any]) -> RealtimePublishReceipt:
        event = RealtimeEvent(
            type=event_type,
            payload=payload,
            created_at=datetime.now(tz=UTC).isoformat(),
        )
        await self._dispatch(event, listener_scopes={"local", "all_workers"})
        # Existing observational callers may ignore the receipt. Durable owners
        # must inspect it: local delivery is never configured-Redis acceptance.
        transport: Literal["redis_stream", "in_process"] = (
            "redis_stream" if self._redis_enabled else "in_process"
        )
        if not self._started:
            return RealtimePublishReceipt(transport, False, reason="bus_closed")
        if self._redis_enabled:
            return await self._publish_to_redis(event)
        return RealtimePublishReceipt("in_process", True)

    async def _publish_to_redis(self, event: RealtimeEvent) -> RealtimePublishReceipt:
        redis = await self._ensure_redis()
        if redis is None:
            return RealtimePublishReceipt("redis_stream", False, reason="redis_unavailable")
        try:
            stream_id = await redis.xadd(
                self._stream_key,
                {"event": _event_json(event), "origin": self._origin_id},
                maxlen=self._stream_maxlen,
                approximate=True,
            )
            if not isinstance(stream_id, str) or not stream_id:
                raise ValueError("Redis XADD returned no stream identity")
            self._redis_events_published += 1
            self._redis_connected = True
            self._redis_last_publish_error = None
            return RealtimePublishReceipt("redis_stream", True, stream_id=stream_id)
        except Exception as exc:  # noqa: BLE001 - Transport failures must preserve reconnection and client isolation.
            self._redis_publish_failures += 1
            self._redis_connected = False
            self._redis_last_publish_error = str(exc)[:500]
            logger.warning(
                "event_bus_redis_publish_failed",
                extra={"event_type": event.type, "error": str(exc)},
            )
            await self._close_redis()
            return RealtimePublishReceipt("redis_stream", False, reason="redis_rejected")

    async def _ensure_redis(self) -> redis_asyncio.Redis | None:
        if not self._redis_enabled or not self._redis_url:
            return None
        async with self._redis_lock:
            if self._redis is not None and self._redis_connected:
                return self._redis
            redis = self._redis_client_factory(self._redis_url)
            try:
                await redis.ping()
                if not self._stream_id_initialized:
                    self._stream_last_id = await self._initial_stream_id(redis)
                    self._stream_id_initialized = True
            except Exception as exc:  # noqa: BLE001 - Transport failures must preserve reconnection and client isolation.
                await _close_redis_client(redis)
                self._redis = None
                self._redis_connected = False
                self._redis_last_error = str(exc)[:500]
                self._redis_last_read_error = str(exc)[:500]
                logger.warning("event_bus_redis_unavailable", extra={"error": str(exc)})
                return None
            if self._redis is not None and self._redis is not redis:
                await _close_redis_client(self._redis)
            self._redis = redis
            self._redis_connected = True
            self._redis_last_error = None
            return self._redis

    async def _close_redis(self) -> None:
        async with self._redis_lock:
            redis = self._redis
            self._redis = None
            self._redis_connected = False
        if redis is not None:
            await _close_redis_client(redis)

    async def _initial_stream_id(self, redis: redis_asyncio.Redis) -> str:
        try:
            info = await redis.xinfo_stream(self._stream_key)
        except Exception as exc:  # noqa: BLE001 - Transport failures must preserve reconnection and client isolation.
            logger.warning(
                "event_bus_redis_stream_info_failed",
                extra={"stream": self._stream_key, "error": str(exc)},
            )
            return "0-0"
        stream_id = _dict_get(info, "last-generated-id") or _dict_get(info, b"last-generated-id")
        return _decode_text(stream_id) or "0-0"

    async def _redis_stream_reader(self) -> None:
        while self._started and self._redis_enabled:
            redis = await self._ensure_redis()
            if redis is None:
                await asyncio.sleep(REALTIME_STREAM_RECONNECT_SECONDS)
                continue
            try:
                records = await redis.xread(
                    {self._stream_key: self._stream_last_id},
                    block=REALTIME_STREAM_BLOCK_MS,
                    count=REALTIME_STREAM_READ_COUNT,
                )
                self._redis_connected = True
                self._redis_last_read_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Transport failures must preserve reconnection and client isolation.
                self._redis_connected = False
                self._redis_read_failures += 1
                self._redis_last_read_error = str(exc)[:500]
                logger.warning("event_bus_redis_read_failed", extra={"error": str(exc)})
                await self._close_redis()
                await asyncio.sleep(REALTIME_STREAM_RECONNECT_SECONDS)
                continue

            for _stream_name, stream_records in records or []:
                for record_id, fields in stream_records:
                    stream_id = _decode_text(record_id)
                    stream_record = _stream_record_from_fields(fields)
                    if stream_id:
                        self._stream_last_id = stream_id
                    if stream_record is None or stream_record.origin_id == self._origin_id:
                        continue
                    self._redis_events_received += 1
                    await self._dispatch(stream_record.event, listener_scopes={"all_workers"})

    async def _dispatch(self, event: RealtimeEvent, *, listener_scopes: set[ListenerScope]) -> None:
        async with self._lock:
            connections = list(self._connections.values())
            listeners = [
                listener for listener, (scope, event_types) in self._listeners.items()
                if scope in listener_scopes and (event_types is None or event.type in event_types)
            ]

        message = event.__dict__
        for connection in connections:
            was_closing = connection.closing.is_set()
            if not connection.enqueue(message) and not was_closing and connection.close_code == 1013:
                self._websocket_queue_drops += 1

        # Snapshot/filter under the bus lock; invoke inline outside it. Source
        # owners publish hints after releasing DB/domain locks. No task per event.
        for listener in listeners:
            try:
                await listener(event)
            except Exception:  # noqa: BLE001 - One observational listener must not prevent other listeners.
                logger.warning("event_bus_listener_failed", extra={"event_type": event.type})


def _redis_client_from_url(redis_url: str) -> redis_asyncio.Redis:
    return redis_asyncio.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=REALTIME_REDIS_CONNECT_TIMEOUT_SECONDS,
        socket_timeout=REALTIME_REDIS_SOCKET_TIMEOUT_SECONDS,
        health_check_interval=30,
    )


async def _close_redis_client(redis: redis_asyncio.Redis) -> None:
    try:
        await redis.aclose()
    except Exception:
        logger.debug("event_bus_redis_close_failed", exc_info=True)


def _event_json(event: RealtimeEvent) -> str:
    return json.dumps(event.__dict__, separators=(",", ":"), sort_keys=True)


def _stream_record_from_fields(fields: dict[Any, Any]) -> _StreamRecord | None:
    raw = _dict_get(fields, "event") or _dict_get(fields, b"event")
    if raw is None:
        logger.warning("event_bus_redis_record_missing_event_field")
        return None
    try:
        raw_text = _decode_text(raw)
        payload = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning(
            "event_bus_redis_record_malformed_json",
            extra={"error": str(exc), "raw_bytes": len(_decode_text(raw))},
        )
        return None
    if not isinstance(payload, dict):
        logger.warning(
            "event_bus_redis_record_invalid_payload", extra={"payload_type": type(payload).__name__}
        )
        return None
    event_type = payload.get("type")
    event_payload = payload.get("payload")
    created_at = payload.get("created_at")
    if (
        not isinstance(event_type, str)
        or not isinstance(event_payload, dict)
        or not isinstance(created_at, str)
    ):
        logger.warning(
            "event_bus_redis_record_invalid_shape",
            extra={
                "event_type_type": type(event_type).__name__,
                "payload_type": type(event_payload).__name__,
                "created_at_type": type(created_at).__name__,
            },
        )
        return None
    origin_id = _dict_get(fields, "origin") or _dict_get(fields, b"origin")
    return _StreamRecord(
        event=RealtimeEvent(type=event_type, payload=event_payload, created_at=created_at),
        origin_id=_decode_optional_text(origin_id),
    )


def _dict_get(value: Any, key: Any) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _decode_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value or "")


def _decode_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _decode_text(value)


event_bus = EventBus()
