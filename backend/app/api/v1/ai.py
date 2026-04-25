from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.db.session import AsyncSessionLocal
from app.services.auth import authenticate_websocket
from app.services.chat import chat_service
from app.services.settings import get_runtime_config

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    provider: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    provider: str
    text: str
    tool_results: list[dict[str, Any]]


@router.get("/providers")
async def list_providers() -> dict[str, Any]:
    config = await get_runtime_config()
    return {
        "active": config.llm_provider,
        "available": ["local", "openai", "gemini", "claude", "ollama"],
        "models": {
            "openai": config.openai_model,
            "gemini": config.gemini_model,
            "claude": config.anthropic_model,
            "ollama": config.ollama_model,
        },
    }


@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    return await chat_service.list_tools()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    result = await chat_service.handle_message(
        request.message,
        session_id=request.session_id,
        provider_name=request.provider,
    )
    return ChatResponse(
        session_id=result.session_id,
        provider=result.provider,
        text=result.text,
        tool_results=result.tool_results,
    )


@router.websocket("/chat/ws")
async def chat_websocket(websocket: WebSocket) -> None:
    async with AsyncSessionLocal() as session:
        user = await authenticate_websocket(session, websocket)
    if not user:
        await websocket.close(code=1008, reason="Authentication required")
        return

    await websocket.accept()
    config = await get_runtime_config()
    await websocket.send_json(
        {
            "type": "connection.ready",
            "payload": {"provider": config.llm_provider},
        }
    )

    try:
        while True:
            payload = await websocket.receive_json()
            message = str(payload.get("message") or "").strip()
            if not message:
                await websocket.send_json(
                    {"type": "chat.error", "payload": {"message": "Message is required."}}
                )
                continue

            await websocket.send_json({"type": "chat.thinking", "payload": {}})
            result = await chat_service.handle_message(
                message,
                session_id=payload.get("session_id"),
                provider_name=payload.get("provider"),
            )
            await websocket.send_json(
                {
                    "type": "chat.response",
                    "payload": {
                        "session_id": result.session_id,
                        "provider": result.provider,
                        "text": result.text,
                        "tool_results": result.tool_results,
                    },
                }
            )
    except WebSocketDisconnect:
        return
