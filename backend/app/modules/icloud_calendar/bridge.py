"""Locked SDK bridge policy with maintained framing and owned native sockets."""
from __future__ import annotations

import socket
from threading import Lock
from typing import Any, cast
from urllib.parse import urlsplit

from pyicloud.exceptions import PyiCloudTrustedDevicePromptException
from websockets.client import ClientProtocol
from websockets.sync.client import ClientConnection, reconnect

from app.modules.icloud_calendar.native import CalendarNativeOwner, CalendarSocketRegistration

BRIDGE_MAX_MESSAGE_BYTES = 1024 * 1024
BRIDGE_MAX_QUEUED_FRAMES = 4


class _NoRedirectConnection(reconnect):
    def process_redirect(self, exc: Exception) -> Exception:
        # The former SDK raw upgrade rejected every non-101 response.
        return exc


class CalendarBridgeConnection(ClientConnection):
    def __init__(
        self, sock: socket.socket, protocol: ClientProtocol,
        *, operation: CalendarNativeOwner, **kwargs: Any,
    ) -> None:
        self._binding_lock = Lock()
        self._registration: CalendarSocketRegistration | None = operation.register(sock)
        try:
            super().__init__(sock, protocol, **kwargs)
            # This context spans retained 2FA input as well as active SDK calls.
            self.__enter__()
        except BaseException:
            self._registration.release()
            self._registration = None
            raise

    def activate(self, operation: CalendarNativeOwner) -> None:
        # Register before waiting on a pending protocol write's lock, so the
        # active operation can interrupt that exact socket even in this gap.
        replacement = operation.register(self.socket)
        with self._binding_lock:
            try:
                operation.work.check()
            except BaseException:
                replacement.release()
                raise
            previous, self._registration = self._registration, replacement
            if previous is not None:
                previous.release()

    def detach(self) -> None:
        with self._binding_lock:
            previous, self._registration = self._registration, None
            if previous is not None:
                previous.release()

    def send_data(self) -> None:
        with self._binding_lock:
            registration = self._registration
            if registration is None:
                # No SDK application operation is eligible while pending.
                # The receiver remains an owned bounded retained resource;
                # expiry/disposal shutdown can always interrupt control I/O.
                super().send_data()
                return
            guard = registration.send_guard()
            try:
                super().send_data()
                if guard.expired.is_set():
                    raise TimeoutError("Calendar bridge send timed out.")
            finally:
                guard.cancel()

    def close_owned(self) -> None:
        # Shutdown does not take protocol_mutex, so a blocked TLS write cannot
        # prevent actual socket close and receiver cleanup.
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.__exit__(None, None, None)
        finally:
            self.recv_events_thread.join()
            self.detach()


class CalendarBridgeSocket:
    def __init__(
        self, url: str, timeout: float, origin: str, user_agent: str,
        *, operation: CalendarNativeOwner,
    ) -> None:
        self.operation = operation
        self.closed = False
        remaining = operation.work.remaining()
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise PyiCloudTrustedDevicePromptException("Calendar trusted-device connection failed.")

        acquired: list[CalendarBridgeConnection] = []

        class BoundConnection(CalendarBridgeConnection):
            def __init__(self, sock: socket.socket, protocol: ClientProtocol, **kwargs: Any) -> None:
                super().__init__(sock, protocol, operation=operation, **kwargs)
                acquired.append(self)

        connector = _NoRedirectConnection(
            url, origin=origin, user_agent_header=user_agent, proxy=None,
            compression=None, ping_interval=None, ping_timeout=None, close_timeout=1.0,
            open_timeout=min(timeout, remaining), max_size=BRIDGE_MAX_MESSAGE_BYTES,
            max_queue=BRIDGE_MAX_QUEUED_FRAMES, create_connection=BoundConnection,
        )
        # connect() is one attempt; never iterate reconnect's retry protocol.
        try:
            self.connection = cast(CalendarBridgeConnection, connector.connect())
        except BaseException as exc:
            for connection in acquired:
                connection.close_owned()
            operation.work.check()
            if not isinstance(exc, Exception):
                raise
            raise PyiCloudTrustedDevicePromptException("Calendar trusted-device connection failed.") from exc
        try:
            operation.work.check()
        except BaseException:
            self.connection.close_owned()
            raise

    def activate(self, operation: CalendarNativeOwner) -> None:
        self.connection.activate(operation)
        self.operation = operation

    def detach(self) -> None:
        self.connection.detach()

    def send_binary(self, payload: bytes) -> None:
        self.operation.work.check()
        if len(payload) > BRIDGE_MAX_MESSAGE_BYTES:
            raise PyiCloudTrustedDevicePromptException("Calendar bridge message exceeded its supported size.")
        try:
            self.connection.send(payload)
        except Exception as exc:
            self.operation.work.check()
            raise PyiCloudTrustedDevicePromptException("Calendar trusted-device transport failed.") from exc
        self.operation.work.check()

    def read_message(self) -> bytes:
        remaining = self.operation.work.remaining()
        try:
            result = self.connection.recv(timeout=min(30.0, remaining), decode=False)
        except Exception as exc:
            self.operation.work.check()
            raise PyiCloudTrustedDevicePromptException("Calendar trusted-device transport failed.") from exc
        self.operation.work.check()
        if not isinstance(result, bytes):
            raise PyiCloudTrustedDevicePromptException("Calendar bridge returned an unsupported message.")
        return result

    def close(self) -> None:
        if self.closed:
            return
        self.connection.close_owned()
        self.closed = True
