"""Actual maintained TLS transport, callback identity and retained receiver cleanup."""
import asyncio
import socket
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from time import monotonic

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pyicloud.exceptions import PyiCloudTrustedDevicePromptException
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from app.modules.icloud_calendar.bridge import CalendarBridgeSocket
from app.modules.icloud_calendar.contracts import CalendarWork
from app.modules.icloud_calendar.native import CalendarNativeOwner, CalendarSocketRegistration


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    import ssl

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    servers = []

    def start(*, block=False, redirect=False, block_upgrade=False):
        stop, ready = threading.Event(), threading.Event()
        requests = []

        def handler(connection):
            requests.append(dict(connection.request.headers))
            connection.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            ready.set()
            if block:
                stop.wait(10)
            else:
                with suppress(ConnectionClosed):
                    for message in connection:
                        connection.send(message)

        def process_request(connection, request):
            if block_upgrade:
                ready.set()
                stop.wait(10)
            if redirect:
                requests.append(dict(request.headers))
                response = connection.respond(HTTPStatus.FOUND, "redirect rejected")
                response.headers["Location"] = "wss://localhost:1/must-not-follow"
                return response
            return None

        server = serve(handler, "127.0.0.1", 0, ssl=context, ping_interval=None,
                       compression=None, max_queue=1, max_size=2 * 1024 * 1024,
                       process_request=process_request)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread, stop))
        return f"wss://localhost:{server.socket.getsockname()[1]}/bridge", ready, requests

    yield start
    for server, thread, stop in servers:
        stop.set()
        server.shutdown()
        thread.join(3)
        assert not thread.is_alive()


def owner(seconds=10):
    return CalendarNativeOwner(CalendarWork(monotonic() + seconds), asyncio.get_running_loop())


@pytest.mark.asyncio
async def test_actual_tls_bridge_echo_and_eight_retained_receivers_close(tls_server):
    url, _, requests = tls_server()
    retained = []
    try:
        for _ in range(8):
            operation = owner()
            bridge = await asyncio.to_thread(CalendarBridgeSocket, url, 3, "https://inert.invalid",
                                             "IACS inert fixture", operation=operation)
            await asyncio.to_thread(bridge.send_binary, b"bridge protocol bytes")
            assert await asyncio.to_thread(bridge.read_message) == b"bridge protocol bytes"
            await asyncio.to_thread(bridge.detach)
            operation.close()
            assert bridge.connection.recv_events_thread.is_alive()
            retained.append(bridge)
        assert len(retained) == 8
        assert requests[0]["origin"] == "https://inert.invalid"
        assert requests[0]["user-agent"] == "IACS inert fixture"
        assert "sec-websocket-extensions" not in requests[0]
    finally:
        for bridge in retained:
            await asyncio.to_thread(bridge.close)
    assert all(not bridge.connection.recv_events_thread.is_alive() for bridge in retained)


@pytest.mark.asyncio
async def test_actual_tls_blocked_send_is_aborted_and_drained(tls_server, monkeypatch):
    url, ready, _ = tls_server(block=True)
    operation = owner(5)
    bridge = await asyncio.to_thread(CalendarBridgeSocket, url, 3, "https://inert.invalid",
                                     "IACS inert fixture", operation=operation)
    assert await asyncio.to_thread(ready.wait, 1)
    bridge.connection.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    original = CalendarSocketRegistration.send_guard
    monkeypatch.setattr(CalendarSocketRegistration, "send_guard", lambda self: original(self, .08))

    def send_until_backpressure():
        for _ in range(8):
            bridge.send_binary(b"x" * (1024 * 1024))
        pytest.fail("fixture did not reach native TLS backpressure")

    started = monotonic()
    try:
        with pytest.raises(PyiCloudTrustedDevicePromptException, match="transport failed"):
            await asyncio.wait_for(asyncio.to_thread(send_until_backpressure), 3)
        assert monotonic() - started < 2
        # A transport timeout can still take the SDK's existing SMS fallback.
        assert operation.work.remaining() > 0
    finally:
        operation.request_stop()
        await asyncio.to_thread(bridge.close)
        operation.close()
    assert not bridge.connection.recv_events_thread.is_alive()


@pytest.mark.asyncio
async def test_disarmed_old_send_callback_cannot_abort_reused_socket():
    left, right = socket.socketpair()
    left.settimeout(1)
    first, second = owner(), owner()
    try:
        registration = first.register(left)
        guard = registration.send_guard(.01)
        guard.cancel()  # Includes disarm-before-loop-install race.
        registration.release()
        replacement = second.register(left)
        first._expire(guard)  # A callback already queued before cancellation is inert.
        await asyncio.sleep(.03)
        right.sendall(b"still current")
        assert left.recv(13) == b"still current"
        first.request_stop()
        right.sendall(b"new use")
        assert left.recv(7) == b"new use"
        replacement.release()
    finally:
        first.close()
        second.close()
        left.close()
        right.close()


@pytest.mark.asyncio
async def test_upgrade_redirect_is_rejected_without_followup(tls_server):
    url, _, requests = tls_server(redirect=True)
    operation = owner()
    try:
        with pytest.raises(PyiCloudTrustedDevicePromptException, match="connection failed"):
            await asyncio.to_thread(CalendarBridgeSocket, url, 3, "https://inert.invalid",
                                    "IACS inert fixture", operation=operation)
        assert len(requests) == 1
        assert all(not item.active for item in operation._registrations) or not operation._registrations
    finally:
        operation.request_stop()
        operation.close()


@pytest.mark.asyncio
async def test_stop_during_actual_upgrade_drains_acquired_socket(tls_server):
    from app.modules.icloud_calendar.contracts import ICloudCalendarStopped

    url, ready, _ = tls_server(block_upgrade=True)
    operation = owner()
    child = asyncio.create_task(asyncio.to_thread(
        CalendarBridgeSocket, url, 3, "https://inert.invalid", "IACS inert fixture",
        operation=operation))
    assert await asyncio.to_thread(ready.wait, 1)
    operation.request_stop()
    with pytest.raises(ICloudCalendarStopped):
        await asyncio.wait_for(asyncio.shield(child), 2)
    assert not operation._registrations
    operation.close()


@pytest.mark.asyncio
async def test_actual_tls_verification_failure_does_not_retain_receiver(tls_server, monkeypatch):
    url, _, requests = tls_server()
    monkeypatch.delenv("SSL_CERT_FILE")
    operation = owner()
    try:
        with pytest.raises(PyiCloudTrustedDevicePromptException, match="connection failed"):
            await asyncio.to_thread(CalendarBridgeSocket, url, 1, "https://inert.invalid",
                                    "IACS inert fixture", operation=operation)
        assert requests == []
        assert operation._registrations == set()
    finally:
        operation.request_stop()
        operation.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["ws://localhost:1/bridge", "https://localhost:1/bridge", "wss:///bridge"])
async def test_bridge_preserves_wss_only_validation_without_connect(url, monkeypatch):
    from app.modules.icloud_calendar.bridge import _NoRedirectConnection

    def forbidden_connect(*args, **kwargs):
        pytest.fail("unsupported URL must not acquire a connection")

    monkeypatch.setattr(_NoRedirectConnection, "connect", forbidden_connect)
    operation = owner()
    try:
        with pytest.raises(PyiCloudTrustedDevicePromptException, match="connection failed"):
            await asyncio.to_thread(CalendarBridgeSocket, url, 1, "https://inert.invalid",
                                    "IACS inert fixture", operation=operation)
        assert not operation._registrations
    finally:
        operation.close()


@pytest.mark.asyncio
async def test_detached_native_write_is_interruptible_during_activation(tls_server):
    from app.modules.icloud_calendar.contracts import ICloudCalendarStopped

    url, ready, _ = tls_server(block=True)
    previous = owner()
    bridge = await asyncio.to_thread(CalendarBridgeSocket, url, 3, "https://inert.invalid",
                                     "IACS inert fixture", operation=previous)
    assert await asyncio.to_thread(ready.wait, 1)
    bridge.connection.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    await asyncio.to_thread(bridge.detach)
    previous.close()
    current = owner()

    def pending_native_write():
        # Receiver control writes use this same detached send_data/native TLS
        # path; large bytes force genuine backpressure in this inert fixture.
        for _ in range(8):
            bridge.connection.send(b"x" * (1024 * 1024))
        pytest.fail("fixture did not reach native TLS backpressure")

    blocked = asyncio.create_task(asyncio.to_thread(pending_native_write))
    activation = None
    try:
        deadline = monotonic() + 1
        while not bridge.connection._binding_lock.locked():
            assert monotonic() < deadline
            await asyncio.sleep(.001)
        activation = asyncio.create_task(asyncio.to_thread(bridge.activate, current))
        while not current._registrations:
            assert monotonic() < deadline
            await asyncio.sleep(.001)
        assert not activation.done()
        current.request_stop()
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(asyncio.shield(blocked), 2)
        with pytest.raises(ICloudCalendarStopped):
            await asyncio.wait_for(asyncio.shield(activation), 2)
    finally:
        current.request_stop()
        await asyncio.to_thread(bridge.close)
        await asyncio.gather(blocked, *([activation] if activation else []), return_exceptions=True)
        current.close()
    assert not bridge.connection.recv_events_thread.is_alive()
