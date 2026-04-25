import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RealtimeEvent:
    type: str
    payload: dict[str, Any]
    created_at: str


class EventBus:
    """Small in-process event bus used by Phase 1.

    The public API is intentionally narrow so Phase 2 can move backing transport
    to Redis streams or pub/sub without changing API routers or modules.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
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

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = RealtimeEvent(
            type=event_type,
            payload=payload,
            created_at=datetime.now(tz=UTC).isoformat(),
        )
        async with self._lock:
            connections = list(self._connections)

        for websocket in connections:
            try:
                await websocket.send_json(event.__dict__)
            except RuntimeError:
                self.disconnect(websocket)


event_bus = EventBus()
