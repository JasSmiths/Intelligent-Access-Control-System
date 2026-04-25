from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.session import AsyncSessionLocal
from app.services.auth import authenticate_websocket
from app.services.event_bus import event_bus

router = APIRouter()


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket) -> None:
    """Stream system events to dashboards.

    This endpoint is intentionally generic in Phase 1. Later phases will publish
    gate state, presence state, logs, anomalies, and chat tokens through the
    same event bus so the UI can update without polling.
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
            await websocket.receive_text()
    except WebSocketDisconnect:
        event_bus.disconnect(websocket)
