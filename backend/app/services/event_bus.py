import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RealtimeEvent:
    type: str
    payload: dict[str, Any]
    created_at: str


EventListener = Callable[[RealtimeEvent], Awaitable[None]]


class EventBus:
    """Small in-process event bus for realtime dashboard and workflow updates.

    The public API stays narrow so a later worker split can move backing
    transport to Redis streams or pub/sub without changing routers or modules.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._listeners: set[EventListener] = set()
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        self._started = True
        logger.info("event_bus_started")

    async def stop(self) -> None:
        async with self._lock:
            connections = list(self._connections)
            self._connections.clear()

        for websocket in connections:
            await websocket.close()
        self._started = False
        logger.info("event_bus_stopped")

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        await websocket.send_json({"type": "connection.ready", "payload": {}})

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    def subscribe(self, listener: EventListener) -> None:
        self._listeners.add(listener)

    def unsubscribe(self, listener: EventListener) -> None:
        self._listeners.discard(listener)

    def status(self) -> dict[str, Any]:
        return {
            "started": self._started,
            "connections": len(self._connections),
            "listeners": len(self._listeners),
        }

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = RealtimeEvent(
            type=event_type,
            payload=payload,
            created_at=datetime.now(tz=UTC).isoformat(),
        )
        async with self._lock:
            connections = list(self._connections)
            listeners = list(self._listeners)

        for websocket in connections:
            try:
                await websocket.send_json(event.__dict__)
            except Exception as exc:
                self.disconnect(websocket)
                logger.warning(
                    "event_bus_websocket_send_failed",
                    extra={
                        "event_type": event_type,
                        "error": str(exc),
                    },
                )

        for listener in listeners:
            task = asyncio.create_task(listener(event), name=f"event-listener:{event_type}")
            task.add_done_callback(_log_listener_error)


def _log_listener_error(task: asyncio.Task) -> None:
    try:
        task.result()
    except Exception:
        logger.exception("event_bus_listener_failed")


event_bus = EventBus()
