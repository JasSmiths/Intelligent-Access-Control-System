import asyncio

import pytest

from app.services.event_bus import EventBus


class FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent = []

    async def send_json(self, payload):
        if self.fail:
            raise ValueError("socket closed")
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_publish_discards_failed_websocket_and_still_notifies_others() -> None:
    bus = EventBus()
    good_socket = FakeWebSocket()
    failed_socket = FakeWebSocket(fail=True)
    seen = []
    listener_called = asyncio.Event()

    async def listener(event):
        seen.append(event)
        listener_called.set()

    bus._connections.update({good_socket, failed_socket})
    bus.subscribe(listener)

    await bus.publish("access_event.finalized", {"registration_number": "AGS7X"})
    await asyncio.wait_for(listener_called.wait(), timeout=1)

    assert failed_socket not in bus._connections
    assert good_socket in bus._connections
    assert good_socket.sent[0]["type"] == "access_event.finalized"
    assert good_socket.sent[0]["payload"] == {"registration_number": "AGS7X"}
    assert seen[0].type == "access_event.finalized"
