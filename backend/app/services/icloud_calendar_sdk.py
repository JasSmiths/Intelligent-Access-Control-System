"""One nonwaiting actual SDK operation and bounded retained 2FA clients."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol

from app.core.task_lifecycle import drain_owned
from app.modules.icloud_calendar.client import ICloudAuthSession
from app.modules.icloud_calendar.contracts import CALENDAR_ATTEMPT_SECONDS, CalendarWork
from app.modules.icloud_calendar.native import CalendarNativeOwner

CALENDAR_PENDING_AUTH_LIMIT = 8
CALENDAR_PENDING_AUTH_TTL = timedelta(minutes=10)


class CalendarSDKBusy(RuntimeError):
    """No client/queued work is created for a rejected admission."""


class CalendarAuthCleanup(Protocol):
    def cleanup_auth_session(self, session: ICloudAuthSession) -> None: ...
    def detach(self, session: ICloudAuthSession) -> None: ...


@dataclass(frozen=True)
class PendingCalendarAuth:
    handshake_id: str
    session: ICloudAuthSession
    expires_at: datetime
    # Account identity/generation or explicit absence captured before SDK work.
    account_id: str | None
    account_generation: int | None
    actor_user_id: str

    def expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class CalendarSDKOwner:
    def __init__(
        self, client: CalendarAuthCleanup,
        *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client, self._clock = client, clock
        self._active: asyncio.Task[object] | None = None
        self._native: CalendarNativeOwner | None = None
        self._closing = False
        self._stopping: asyncio.Task[None] | None = None
        self._reserved_pending = False
        self._pending: dict[str, PendingCalendarAuth] = {}
        self._retiring: set[str] = set()

    async def start(self) -> None:
        """Inert process admission; never replace retained or failed cleanup."""
        if self._active is not None or self._pending:
            raise CalendarSDKBusy("iCloud cleanup must finish before this process owner can start.")
        if self._stopping is not None:
            # A failed stop stays failed rather than discarding its resources.
            await drain_owned(self._stopping)
        self._closing = False
        self._stopping = None

    async def run[T](
        self, action: Callable[[CalendarNativeOwner], Awaitable[T]],
        *, work: CalendarWork | None = None,
        disconnect: Callable[[], Awaitable[None]] | None = None,
        after_result: Callable[[T, CalendarNativeOwner], Awaitable[T]] | None = None,
    ) -> T:
        if self._closing or self._active is not None:
            raise CalendarSDKBusy("An iCloud operation is already running. Try again when it finishes.")
        work = work or CalendarWork(monotonic() + CALENDAR_ATTEMPT_SECONDS)
        native = CalendarNativeOwner(work, asyncio.get_running_loop())
        self._native = native
        child = asyncio.create_task(self._owned(action, native, disconnect, after_result))
        self._active = child
        try:
            return await asyncio.shield(child)
        except asyncio.CancelledError:
            native.request_stop()
            try:
                await drain_owned(child)
            except asyncio.CancelledError:
                # drain_owned has retrieved the actual child even when another
                # caller cancellation arrived during cleanup. Retrieve its known
                # successful receipt instead of discarding it for that signal.
                pass
            except Exception as exc:
                raise asyncio.CancelledError from exc
            return child.result()

    async def _owned[T](
        self, action: Callable[[CalendarNativeOwner], Awaitable[T]],
        native: CalendarNativeOwner, disconnect: Callable[[], Awaitable[None]] | None,
        after_result: Callable[[T, CalendarNativeOwner], Awaitable[T]] | None,
    ) -> T:
        deadline: asyncio.TimerHandle | None = None
        watcher: asyncio.Task[None] | None = None
        try:
            deadline = asyncio.get_running_loop().call_later(native.work.remaining(), native.request_stop)
            if disconnect is not None:
                watcher = asyncio.create_task(self._watch_disconnect(disconnect, native))
            await self._prune_expired()
            native.work.check()
            # Action owns every acquired session through cleanup or explicit
            # pending retention, including DB/SDK acquisition that completes late.
            result = await action(native)
            # Accepted observation remains in this admitted child/slot through
            # actual cleanup; stop cannot dispose its DB/provider dependencies.
            return await after_result(result, native) if after_result is not None else result
        finally:
            if deadline is not None:
                deadline.cancel()
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            native.close()
            self._reserved_pending = False
            self._native = None
            self._active = None

    @staticmethod
    async def _watch_disconnect(
        disconnect: Callable[[], Awaitable[None]], native: CalendarNativeOwner,
    ) -> None:
        try:
            await disconnect()
        finally:
            native.request_stop()

    def reserve_pending_capacity(self) -> None:
        """Call before start_auth construction; one active owner makes it exact."""
        if self._native is None:
            raise RuntimeError("Apple authentication needs an admitted SDK owner.")
        self._native.work.check()
        if self._reserved_pending or len(self._pending) >= CALENDAR_PENDING_AUTH_LIMIT:
            raise CalendarSDKBusy("Eight Apple verification sessions are pending. Finish one or wait for an expired session to be cleaned up.")
        self._reserved_pending = True

    async def retain(self, pending: PendingCalendarAuth) -> None:
        if self._native is None or not self._reserved_pending:
            raise RuntimeError("Apple authentication did not reserve pending-client capacity.")
        self._native.work.check()
        await asyncio.to_thread(self._client.detach, pending.session)
        self._native.work.check()
        if pending.handshake_id in self._pending:
            raise RuntimeError("Apple verification handshake identity was already retained.")
        self._pending[pending.handshake_id] = pending
        self._reserved_pending = False

    def pending(self, handshake_id: str) -> PendingCalendarAuth | None:
        pending = self._pending.get(handshake_id)
        return None if pending is None or handshake_id in self._retiring or pending.expired(self._clock()) else pending

    async def drop(self, handshake_id: str) -> None:
        pending = self._pending.get(handshake_id)
        if pending is not None:
            # Retire before cleanup starts. A partially closed handle can never
            # be reactivated, but remains counted/owned until cleanup succeeds.
            self._retiring.add(handshake_id)
            await asyncio.to_thread(self._client.cleanup_auth_session, pending.session)
            del self._pending[handshake_id]
            self._retiring.discard(handshake_id)

    async def _prune_expired(self) -> None:
        now = self._clock()
        for handshake_id, pending in list(self._pending.items()):
            if handshake_id in self._retiring or pending.expired(now):
                await self.drop(handshake_id)

    async def stop(self) -> None:
        self._closing = True
        if self._native is not None:
            self._native.request_stop()
        if self._stopping is None:
            self._stopping = asyncio.create_task(self._stop_owned())
        await drain_owned(self._stopping)

    async def _stop_owned(self) -> None:
        active = self._active
        if active is not None:
            await asyncio.gather(active, return_exceptions=True)
        failure: BaseException | None = None
        for handshake_id in list(self._pending):
            try:
                await self.drop(handshake_id)
            except BaseException as exc:  # noqa: BLE001 - Drain every retained resource before reporting stop failure.
                failure = failure or exc
        if failure is not None:
            raise failure
