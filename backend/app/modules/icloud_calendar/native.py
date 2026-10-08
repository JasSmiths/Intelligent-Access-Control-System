"""One admitted calendar SDK operation's actual bridge socket abort handles."""
from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from threading import Event, Lock
from time import monotonic

from app.modules.icloud_calendar.contracts import CalendarWork


@dataclass(eq=False)
class CalendarSendGuard:
    registration: CalendarSocketRegistration
    deadline: float
    expired: Event
    disarmed: bool = False
    handle: asyncio.TimerHandle | None = None

    def cancel(self) -> None:
        self.registration.owner.disarm(self)


@dataclass(eq=False)
class CalendarSocketRegistration:
    owner: CalendarNativeOwner
    socket: socket.socket
    active: bool = True
    guard: CalendarSendGuard | None = None

    def send_guard(self, maximum_seconds: float = 30.0) -> CalendarSendGuard:
        return self.owner.arm_send(self, maximum_seconds)

    def release(self) -> None:
        self.owner.unregister(self)


class CalendarNativeOwner:
    """No task/timer is retained by a handshake waiting for user input.

    Synchronous SDK calls register acquired sockets with their current admitted
    operation. Shutdown interrupts native I/O; it never substitutes for the
    actual worker's socket close and receiver-thread join.
    """

    def __init__(self, work: CalendarWork, loop: asyncio.AbstractEventLoop) -> None:
        self.work, self.loop = work, loop
        self._lock = Lock()
        self._registrations: set[CalendarSocketRegistration] = set()
        self._closed = False

    def register(self, sock: socket.socket) -> CalendarSocketRegistration:
        self.work.check()
        registration = CalendarSocketRegistration(self, sock)
        with self._lock:
            if self._closed:
                raise RuntimeError("Calendar native operation is already closed.")
            self._registrations.add(registration)
        # Stop can race acquisition/registration; the actual handle must still
        # become interruptible before another SDK stage starts.
        try:
            self.work.check()
        except BaseException:
            _shutdown(sock)
            self.unregister(registration)
            raise
        return registration

    def request_stop(self) -> None:
        self.work.request_stop()
        with self._lock:
            for item in self._registrations:
                if item.active:
                    _shutdown(item.socket)

    def arm_send(
        self, registration: CalendarSocketRegistration, maximum_seconds: float
    ) -> CalendarSendGuard:
        remaining = self.work.remaining()
        guard = CalendarSendGuard(registration, monotonic() + min(maximum_seconds, remaining), Event())
        with self._lock:
            if self._closed or not registration.active or registration not in self._registrations:
                raise RuntimeError("Calendar bridge no longer belongs to this operation.")
            if registration.guard is not None:
                raise RuntimeError("Calendar bridge already owns an active send.")
            registration.guard = guard
        self.loop.call_soon_threadsafe(self._install, guard)
        return guard

    def _install(self, guard: CalendarSendGuard) -> None:
        with self._lock:
            if not self._current(guard):
                return
            guard.handle = self.loop.call_later(max(0.0, guard.deadline - monotonic()),
                                               self._expire, guard)

    def _current(self, guard: CalendarSendGuard) -> bool:
        registration = guard.registration
        return (not self._closed and not guard.disarmed and registration.active
                and registration in self._registrations and registration.guard is guard)

    def _expire(self, guard: CalendarSendGuard) -> None:
        # Keep the identity lock through shutdown. A simultaneous disarm/rebind
        # cannot turn this old callback into an abort of a later handshake use.
        with self._lock:
            if not self._current(guard):
                return
            guard.expired.set()
            guard.disarmed = True
            guard.registration.guard = None
            if monotonic() >= self.work.deadline:
                self.work.request_stop()
            _shutdown(guard.registration.socket)

    def disarm(self, guard: CalendarSendGuard) -> None:
        with self._lock:
            guard.disarmed = True
            if guard.registration.guard is guard:
                guard.registration.guard = None
            handle = guard.handle
        if handle is not None:
            self.loop.call_soon_threadsafe(handle.cancel)

    def unregister(self, registration: CalendarSocketRegistration) -> None:
        with self._lock:
            registration.active = False
            self._registrations.discard(registration)
            guard, registration.guard = registration.guard, None
        if guard is not None:
            self.disarm(guard)

    def close(self) -> None:
        # The async SDK owner invokes this only after actual worker/client
        # cleanup, or after explicitly detaching a retained pending client.
        with self._lock:
            self._closed = True
            registrations = list(self._registrations)
        for registration in registrations:
            self.unregister(registration)


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
