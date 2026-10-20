"""Committed wake hints for closed durable lanes; never a work/authority queue."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned

logger = get_logger(__name__)
CHANNEL = "iacs_durable_work"
REPAIR_SECONDS = 60.0
HEALTH_SECONDS = 30.0
CONNECTION_TIMEOUT_SECONDS = 5.0


class WorkTopic(StrEnum):
    LPR = "lpr"
    COMPLETION = "completion"
    PUBLICATION = "publication"
    HA = "ha_presence"
    CHART = "chart"
    NOTIFICATION = "notification"
    AUTOMATION = "automation"
    INFORMATION = "information"
    AUTOMATION_CLOCK = "automation_clock"
    VISITOR = "visitor"
    MOVEMENT = "movement"


@dataclass(frozen=True)
class WorkReadiness:
    ready: bool = False
    delay_seconds: float | None = None


class WorkWake:
    """One bounded, coalescing latch. Capture generation BEFORE durable inspection."""
    def __init__(self) -> None:
        self.generation = 0
        self.connected = False
        self._event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.fallback_waits = 0

    def set(self) -> None:
        self.generation = (self.generation + 1) % (1 << 63)
        self._event.set()

    async def wait(self, generation: int, readiness: WorkReadiness, *, fallback_seconds: float) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._event = asyncio.Event()
            self._loop = loop
        self._event.clear()
        if self.generation != generation:
            return
        if not self.connected:
            self.fallback_waits += 1
        timeout = REPAIR_SECONDS if self.connected else fallback_seconds
        if readiness.ready:
            timeout = 0
        elif readiness.delay_seconds is not None:
            timeout = min(timeout, max(0.0, readiness.delay_seconds))
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            pass


_WAKES = {topic: WorkWake() for topic in WorkTopic}


def work_wake(topic: WorkTopic) -> WorkWake:
    return _WAKES[topic]


async def notify_work(session: AsyncSession, topic: WorkTopic) -> None:
    """Joins the existing transaction. PostgreSQL emits only at successful commit."""
    await session.execute(text("SELECT pg_notify(:channel, :topic)"), {"channel": CHANNEL, "topic": topic.value})


class ListenerConnection(Protocol):
    async def add_listener(self, channel: str, callback: Callable[[object, int, str, str], None]) -> None: ...
    def add_termination_listener(self, callback: Callable[[object], None]) -> None: ...
    async def execute(self, query: str) -> object: ...
    async def fetchval(self, query: str) -> object: ...
    async def close(self, *, timeout: float) -> None: ...
    def is_closed(self) -> bool: ...
    def terminate(self) -> None: ...


async def _connect() -> ListenerConnection:
    # Dedicated unpooled connection. The DSN never enters logs/diagnostics.
    return cast(ListenerConnection, await asyncpg.connect(
        settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
        timeout=CONNECTION_TIMEOUT_SECONDS, statement_cache_size=0,
        server_settings={"application_name": "iacs-durable-work"},
    ))


class DurableWorkListener:
    def __init__(self, connect: Callable[[], Awaitable[ListenerConnection]] = _connect, *,
                 wakes: Mapping[WorkTopic, WorkWake] | None = None) -> None:
        self._connect = connect
        self._wakes = dict(_WAKES if wakes is None else wakes)
        self._connection: ListenerConnection | None = None
        self._queue_usage = 0.0
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._lost = asyncio.Event()
        self._stopping = False
        self._connected = False
        self._reconnects = 0
        self._failures = 0
        self._hints = 0

    def diagnostics(self) -> dict[str, bool | int | float]:
        return {"connected": self._connected, "reconnects": self._reconnects,
                "failures": self._failures, "hints": self._hints,
                "healthy_repair_seconds": REPAIR_SECONDS,
                "fallback_waits": sum(wake.fallback_waits for wake in self._wakes.values()),
                "notification_queue_usage": self._queue_usage}

    def _state(self, connected: bool) -> None:
        self._connected = connected
        for wake in self._wakes.values():
            wake.connected = connected
            wake.set()  # Catch up on startup/reconnect; shorten waits on detected outage.

    def _notification(self, connection: object, pid: int, channel: str, payload: str) -> None:
        if connection is not self._connection or channel != CHANNEL:
            return
        try:
            topic = WorkTopic(payload)
        except ValueError:
            return  # Closed names only; no decoded records, IDs, or provider data.
        self._hints += 1
        if topic in self._wakes:
            self._wakes[topic].set()

    def _terminated(self, connection: object) -> None:
        if connection is not self._connection:
            return
        self._state(False)
        self._lost.set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._ready.clear()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="durable-work-listener")
        await self._ready.wait()

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            await drain_owned(asyncio.gather(self._task, return_exceptions=True))
            self._task = None
        self._state(False)

    async def _run(self) -> None:
        backoff = 0.5
        while not self._stopping:
            connection: ListenerConnection | None = None
            try:
                self._lost.clear()
                async with asyncio.timeout(CONNECTION_TIMEOUT_SECONDS):
                    connection = await self._connect()
                    self._connection = connection
                    connection.add_termination_listener(self._terminated)
                    # asyncpg add_listener executes LISTEN outside a transaction;
                    # it is committed before catch-up or any healthy idle wait.
                    await connection.add_listener(CHANNEL, self._notification)
                self._reconnects += 1
                self._state(True)
                self._ready.set()
                backoff = 0.5
                while not self._lost.is_set():
                    try:
                        await asyncio.wait_for(self._lost.wait(), HEALTH_SECONDS)
                    except TimeoutError:
                        async with asyncio.timeout(CONNECTION_TIMEOUT_SECONDS):
                            usage = await connection.fetchval("SELECT pg_notification_queue_usage()")
                            if isinstance(usage, (int, float)):
                                self._queue_usage = float(usage)
                raise ConnectionError("durable_listener_disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - fallback polling remains authoritative.
                self._failures += 1
                self._state(False)
                self._ready.set()
                logger.warning("durable_work_listener_unavailable", extra={"exception_class": type(exc).__name__})
            finally:
                self._connection = None
                if connection is not None:
                    try:
                        await drain_owned(connection.close(timeout=CONNECTION_TIMEOUT_SECONDS))
                    except Exception:  # noqa: BLE001 - terminate only this owned dedicated connection.
                        connection.terminate()
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)


durable_work_listener = DurableWorkListener()


async def wait_with_capacity(
    wake: WorkWake, generation: int, pending: WorkReadiness,
    operations: set[asyncio.Task[None]], *, full: bool, fallback_seconds: float,
) -> None:
    """Actual child completion releases capacity; idle waiting owns no DB session."""
    if full:
        await asyncio.wait(tuple(operations), return_when=asyncio.FIRST_COMPLETED)
        return
    waiter = asyncio.create_task(wake.wait(generation, pending, fallback_seconds=fallback_seconds))
    try:
        await asyncio.wait((*operations, waiter), return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
        await drain_owned(asyncio.gather(waiter, return_exceptions=True))
