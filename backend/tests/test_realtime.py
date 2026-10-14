import pytest

from app.services.realtime_connections import client_control


@pytest.mark.asyncio
async def test_realtime_websocket_replies_to_client_ping() -> None:
    response = client_control(
        '{"type":"client.ping","payload":{"id":"probe-1","reason":"focus","at":"2026-05-22T10:00:00Z"}}',
    )

    assert response is not None
    assert response["type"] == "connection.pong"
    assert response["payload"]["id"] == "probe-1"
    assert response["payload"]["reason"] == "focus"
    assert response["payload"]["client_sent_at"] == "2026-05-22T10:00:00Z"
    assert response["payload"]["received_at"]
    assert response["created_at"]


@pytest.mark.asyncio
async def test_realtime_websocket_reports_malformed_control_messages() -> None:
    response = client_control("not-json")
    assert client_control('{"type":"client.unknown"}') is None
    assert client_control("[]") is None
    assert response is not None
    assert response["type"] == "connection.error"
    assert response["payload"]["detail"] == "Malformed realtime control message ignored."
    assert response["created_at"]
