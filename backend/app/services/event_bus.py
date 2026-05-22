import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Literal

from fastapi import WebSocket
import redis.asyncio as redis_asyncio

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

REALTIME_STREAM_KEY = "iacs:realtime:events:v1"
REALTIME_STREAM_MAXLEN = 10_000
REALTIME_STREAM_READ_COUNT = 100
REALTIME_STREAM_BLOCK_MS = 1000
REALTIME_STREAM_RECONNECT_SECONDS = 1.0
REALTIME_REDIS_CONNECT_TIMEOUT_SECONDS = 0.5
REALTIME_REDIS_SOCKET_TIMEOUT_SECONDS = 2.0
REALTIME_WEBSOCKET_QUEUE_SIZE = 250


@dataclass(frozen=True)
class RealtimeEvent:
    type: str
    payload: dict[str, Any]
    created_at: str


EventListener = Callable[[RealtimeEvent], Awaitable[None]]
ListenerScope = Literal["local", "all_workers"]
RedisClientFactory = Callable[[str], redis_asyncio.Redis]


@dataclass
class _WebSocketConnection:
    websocket: WebSocket
    queue: asyncio.Queue[dict[str, Any]]
    sender_task: asyncio.Task | None


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
        self._connections: dict[WebSocket, _WebSocketConnection] = {}
        self._listeners: dict[EventListener, ListenerScope] = {}
        self._listener_tasks: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
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
        if self._started:
            return
        self._started = True
        if self._redis_enabled:
            self._transport = "redis_stream"
            await self._ensure_redis()
            self._stream_task = asyncio.create_task(
                self._redis_stream_reader(),
                name="event-bus-redis-stream-reader",
            )
        logger.info("event_bus_started", extra={"transport": self._transport})

    async def stop(self) -> None:
        self._started = False
        stream_task = self._stream_task
        self._stream_task = None
        if stream_task:
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)

        async with self._lock:
            connections = list(self._connections.values())
            listener_tasks = list(self._listener_tasks)
            self._connections.clear()
            self._listener_tasks.clear()
            self._listeners.clear()

        for connection in connections:
            if connection.sender_task is not None:
                connection.sender_task.cancel()
        if connections:
            await asyncio.gather(
                *(connection.sender_task for connection in connections if connection.sender_task is not None),
                return_exceptions=True,
            )
        for connection in connections:
            try:
                await connection.websocket.close()
            except Exception:
                logger.debug("event_bus_websocket_close_failed", exc_info=True)

        for task in listener_tasks:
            task.cancel()
        if listener_tasks:
            await asyncio.gather(*listener_tasks, return_exceptions=True)

        await self._close_redis()
        logger.info("event_bus_stopped")

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        connection = _WebSocketConnection(
            websocket=websocket,
            queue=asyncio.Queue(maxsize=self._websocket_queue_size),
            sender_task=None,
        )
        async with self._lock:
            self._connections[websocket] = connection
        try:
            await websocket.send_json({"type": "connection.ready", "payload": {}})
            connection.sender_task = asyncio.create_task(
                self._websocket_sender(websocket),
                name="event-bus-websocket-sender",
            )
        except Exception:
            self.disconnect(websocket)
            raise

    def disconnect(self, websocket: WebSocket) -> None:
        connection = self._connections.pop(websocket, None)
        if connection is not None and connection.sender_task is not None:
            connection.sender_task.cancel()

    def subscribe(self, listener: EventListener, *, scope: ListenerScope = "local") -> None:
        if scope not in {"local", "all_workers"}:
            raise ValueError(f"unsupported event listener scope: {scope}")
        self._listeners[listener] = scope

    def unsubscribe(self, listener: EventListener) -> None:
        self._listeners.pop(listener, None)

    def status(self) -> dict[str, Any]:
        return {
            "started": self._started,
            "transport": self._transport,
            "connections": len(self._connections),
            "listeners": len(self._listeners),
            "listener_tasks": len(self._listener_tasks),
            "websocket_queue_size": self._websocket_queue_size,
            "websocket_queued_events": sum(connection.queue.qsize() for connection in self._connections.values()),
            "websocket_queue_drops": self._websocket_queue_drops,
            "redis_connected": self._redis_connected,
            "redis_stream": self._stream_key if self._redis_enabled else None,
            "redis_last_id": self._stream_last_id if self._redis_enabled else None,
            "redis_last_error": self._redis_last_publish_error or self._redis_last_read_error or self._redis_last_error,
            "redis_last_publish_error": self._redis_last_publish_error,
            "redis_last_read_error": self._redis_last_read_error,
            "redis_publish_failures": self._redis_publish_failures,
            "redis_read_failures": self._redis_read_failures,
            "redis_events_published": self._redis_events_published,
            "redis_events_received": self._redis_events_received,
            "stream_maxlen": self._stream_maxlen if self._redis_enabled else None,
        }

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = RealtimeEvent(
            type=event_type,
            payload=payload,
            created_at=datetime.now(tz=UTC).isoformat(),
        )
        await self._dispatch(event, listener_scopes={"local", "all_workers"})
        if self._started and self._redis_enabled:
            await self._publish_to_redis(event)

    async def _publish_to_redis(self, event: RealtimeEvent) -> None:
        redis = await self._ensure_redis()
        if redis is None:
            return
        try:
            await redis.xadd(
                self._stream_key,
                {"event": _event_json(event), "origin": self._origin_id},
                maxlen=self._stream_maxlen,
                approximate=True,
            )
            self._redis_events_published += 1
            self._redis_connected = True
            self._redis_last_publish_error = None
        except Exception as exc:
            self._redis_publish_failures += 1
            self._redis_connected = False
            self._redis_last_publish_error = str(exc)[:500]
            logger.warning(
                "event_bus_redis_publish_failed",
                extra={"event_type": event.type, "error": str(exc)},
            )
            await self._close_redis()

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
            except Exception as exc:
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
        except Exception:
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
            except Exception as exc:
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
                listener
                for listener, scope in self._listeners.items()
                if scope in listener_scopes
            ]

        message = event.__dict__
        for connection in connections:
            try:
                connection.queue.put_nowait(message)
            except asyncio.QueueFull:
                self._websocket_queue_drops += 1
                self.disconnect(connection.websocket)
                logger.warning(
                    "event_bus_websocket_backpressure_disconnected",
                    extra={"event_type": event.type},
                )

        for listener in listeners:
            task = asyncio.create_task(listener(event), name=f"event-bus-listener:{event.type}")
            self._listener_tasks.add(task)
            task.add_done_callback(self._listener_tasks.discard)
            task.add_done_callback(_log_listener_error)

    async def _websocket_sender(self, websocket: WebSocket) -> None:
        while True:
            try:
                connection = self._connections.get(websocket)
                if connection is None:
                    return
                message = await connection.queue.get()
                try:
                    await websocket.send_json(message)
                finally:
                    connection.queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connections.pop(websocket, None)
                logger.warning(
                    "event_bus_websocket_send_failed",
                    extra={"error": str(exc)},
                )
                return


def _log_listener_error(task: asyncio.Future) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("event_bus_listener_failed")


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
        return None
    try:
        payload = json.loads(_decode_text(raw))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    event_payload = payload.get("payload")
    created_at = payload.get("created_at")
    if not isinstance(event_type, str) or not isinstance(event_payload, dict) or not isinstance(created_at, str):
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
