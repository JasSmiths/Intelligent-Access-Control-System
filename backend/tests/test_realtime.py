import pytest

from app.api.v1.realtime import _handle_client_realtime_message


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_realtime_websocket_replies_to_client_ping() -> None:
    websocket = FakeWebSocket()
    await _handle_client_realtime_message(
        websocket,
        '{"type":"client.ping","payload":{"id":"probe-1","reason":"focus","at":"2026-05-22T10:00:00Z"}}',
    )

    assert len(websocket.sent) == 1
    response = websocket.sent[0]
    assert response["type"] == "connection.pong"
    assert response["payload"]["id"] == "probe-1"
    assert response["payload"]["reason"] == "focus"
    assert response["payload"]["client_sent_at"] == "2026-05-22T10:00:00Z"
    assert response["payload"]["received_at"]
    assert response["created_at"]


@pytest.mark.asyncio
async def test_realtime_websocket_ignores_non_ping_control_messages() -> None:
    websocket = FakeWebSocket()

    await _handle_client_realtime_message(websocket, "not-json")
    await _handle_client_realtime_message(websocket, '{"type":"client.unknown"}')
    await _handle_client_realtime_message(websocket, "[]")

    assert websocket.sent == []
