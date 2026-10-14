"""One supervised lifetime for authenticated dashboard transports.

Authority is checked before every send and at most every two idle seconds.
Expiry has its own timer, independent of a blocked database or transport. A
successful check is a send checkpoint, not atomic revocation of network bytes.
Internal bus listeners never pass through this audience projection.
"""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.models.enums import UserRole

logger = get_logger(__name__)

Authorize = Callable[[], Awaitable[UserRole | None]]
AUTHORITY_INTERVAL_SECONDS = 2.0
AUTHORITY_TIMEOUT_SECONDS = 1.0
SEND_TIMEOUT_SECONDS = 5.0
CLOSE_TIMEOUT_SECONDS = 1.0
CHILD_DRAIN_SECONDS = 1.0


def common_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Explicit common UI fields; everything else is an invalidation hint."""
    if event_type == "audit.log.created":
        log = payload.get("log", payload)
        action = log.get("action") if isinstance(log, dict) else None
        return {"action": action} if isinstance(action, str) and len(action) <= 100 else {}
    if event_type == "notification.in_app":
        result = {key: payload[key] for key in ("title", "body", "event_type", "severity")
                  if isinstance(payload.get(key), str)}
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict):
            url = snapshot.get("image_url")
            # Only the application's authenticated snapshot endpoint, never
            # arbitrary vendor URLs/credentials or action instructions.
            if isinstance(url, str) and url.startswith("/api/v1/"):
                result["snapshot"] = {"image_url": url}
        return result
    fields = {
        "gate.state_changed": ("state", "entity_id"),
        "door.state_changed": ("state", "entity_id", "door"),
        "maintenance_mode.changed": ("is_active",),
    }.get(event_type, ())
    candidate = payload.get("status", payload)
    if not isinstance(candidate, dict):
        return {}
    return {key: candidate[key] for key in fields if isinstance(candidate.get(key), (str, bool))}


def client_control(message: str) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "connection.error", "payload": {"detail": "Malformed realtime control message ignored."}, "created_at": now}
    if not isinstance(parsed, dict) or parsed.get("type") != "client.ping":
        return None
    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        payload = parsed
    def text(key: str) -> str | None:
        value = payload.get(key)
        return value[:100] if isinstance(value, str) and value else None
    return {"type": "connection.pong", "payload": {
        "id": text("id"), "reason": text("reason"), "client_sent_at": text("at"), "received_at": now,
    }, "created_at": now}


class RealtimeConnection:
    def __init__(self, websocket: WebSocket, authorize: Authorize, role: UserRole,
                 expires_at: int, queue_size: int) -> None:
        self.websocket = websocket
        self.authorize = authorize
        self.role = role
        self.expires_at = expires_at
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self.closing = asyncio.Event()
        self.done = asyncio.Event()
        self.close_code = 1000
        self.cleanup_pending = False

    def close(self, code: int = 1000) -> None:
        if not self.closing.is_set():
            self.close_code = code
            self.closing.set()

    def enqueue(self, message: dict[str, Any], *, control: bool = False) -> bool:
        if self.closing.is_set():
            return False
        if self.role != UserRole.ADMIN and not control:
            message = {**message, "payload": common_payload(message["type"], message["payload"])}
        try:
            self.queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            self.close(1013)
            return False

    async def _authorized(self) -> bool:
        try:
            async with asyncio.timeout(AUTHORITY_TIMEOUT_SECONDS):
                role = await self.authorize()
            valid = role == self.role and time.time() < self.expires_at
        except Exception:  # noqa: BLE001 - Any authority-store failure must close the connection.
            valid = False
        if not valid:
            self.close(1008)
        return valid and not self.closing.is_set()

    async def _sender(self) -> None:
        while not self.closing.is_set():
            message = await self.queue.get()
            try:
                if not await self._authorized():
                    return
                async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                    await self.websocket.send_json(message)
            finally:
                self.queue.task_done()

    async def _receiver(self) -> None:
        while not self.closing.is_set():
            message = await self.websocket.receive_text()
            control = client_control(message)
            if control is not None:
                self.enqueue(control, control=True)

    async def _authority_watch(self) -> None:
        while not self.closing.is_set():
            await asyncio.sleep(AUTHORITY_INTERVAL_SECONDS)
            if not await self._authorized():
                return

    async def _expiry(self) -> None:
        await asyncio.sleep(max(0, self.expires_at - time.time()))
        self.close(1008)

    async def _wait_closing(self) -> None:
        await self.closing.wait()

    async def run(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        try:
            await self.websocket.accept()
            self.enqueue({"type": "connection.ready", "payload": {}}, control=True)
            tasks = [asyncio.create_task(work(), name="realtime-" + name) for name, work in (
                ("sender", self._sender), ("receiver", self._receiver),
                ("authority", self._authority_watch), ("expiry", self._expiry),
                ("closing", self._wait_closing),
            )]
            completed, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                if not task.cancelled():
                    error = task.exception()
                    if error is not None and not isinstance(error, WebSocketDisconnect):
                        self.close(1011)
            self.close()
        finally:
            self.close()
            await drain_owned(asyncio.create_task(self._cleanup(tasks), name="realtime-cleanup"))

    async def _cleanup(self, tasks: list[asyncio.Task[None]]) -> None:
        self.cleanup_pending = True
        try:
            for task in tasks:
                task.cancel()
            if tasks:
                # Close can release stuck transport I/O. If a child ignores
                # cancellation, retain this complete cleanup owner until it
                # actually finishes; shutdown reports a bounded failure.
                await asyncio.wait(tasks, timeout=CHILD_DRAIN_SECONDS)
            try:
                async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                    await self.websocket.close(code=self.close_code)
            except Exception:  # noqa: BLE001 - Bounded close after intake stopped; never log transport data.
                logger.debug("realtime_transport_close_failed")
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if all(task.done() for task in tasks):
                self.cleanup_pending = False
                self.done.set()
