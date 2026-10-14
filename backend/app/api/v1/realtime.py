from fastapi import APIRouter, WebSocket

from app.db.session import AsyncSessionLocal
from app.models.enums import UserRole
from app.services.auth import authenticated_token, extract_websocket_token
from app.services.event_bus import event_bus

router = APIRouter()


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket) -> None:
    """The route binds private credentials; the connection owns their lifetime."""
    try:
        token = await extract_websocket_token(websocket)
        async with AsyncSessionLocal() as session:
            verified = await authenticated_token(session, token)
            role = verified.user.role if verified else None
            expires_at = verified.expires_at if verified else 0
    except Exception:  # noqa: BLE001 - Auth/store failure must reject the handshake without leaking credentials.
        role = None
        expires_at = 0
    if role is None:
        await websocket.close(code=1008, reason="Authentication required")
        return

    async def current_role() -> UserRole | None:
        async with AsyncSessionLocal() as session:
            current = await authenticated_token(session, token)
            return current.user.role if current else None

    await event_bus.serve(websocket, authorize=current_role, role=role, expires_at=expires_at)
