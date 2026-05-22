import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.services.auth import authenticate_websocket
from app.services.event_bus import event_bus

router = APIRouter()
logger = get_logger(__name__)


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket) -> None:
    """Stream system events to dashboards.

    The shared event bus carries access, gate, notification, log, and chat
    events so the dashboard can refresh without polling every view.
    """

    async with AsyncSessionLocal() as session:
        user = await authenticate_websocket(session, websocket)
    if not user:
        await websocket.close(code=1008, reason="Authentication required")
        return

    await event_bus.connect(websocket)
    try:
        while True:
            # Keep the socket open and allow client pings or lightweight commands.
            message = await websocket.receive_text()
            await _handle_client_realtime_message(websocket, message)
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.disconnect(websocket)


async def _handle_client_realtime_message(websocket: WebSocket, message: str) -> None:
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError as exc:
        logger.warning("realtime_client_message_malformed_json", extra={"error": str(exc), "bytes": len(message)})
        try:
            await websocket.send_json(
                {
                    "type": "connection.error",
                    "payload": {"detail": "Malformed realtime control message ignored."},
                    "created_at": datetime.now(tz=UTC).isoformat(),
                }
            )
        except Exception:
            logger.debug("realtime_client_error_send_failed", exc_info=True)
        return
    if not isinstance(parsed, dict) or parsed.get("type") != "client.ping":
        return
    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        payload = parsed
    await websocket.send_json(
        {
            "type": "connection.pong",
            "payload": {
                "id": _control_string(payload.get("id")),
                "reason": _control_string(payload.get("reason")),
                "client_sent_at": _control_string(payload.get("at")),
                "received_at": datetime.now(tz=UTC).isoformat(),
            },
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def _control_string(value: Any, max_length: int = 100) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:max_length]
