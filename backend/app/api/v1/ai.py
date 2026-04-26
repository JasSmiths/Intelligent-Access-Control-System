import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from app.api.dependencies import current_user as require_current_user
from app.db.session import AsyncSessionLocal
from app.models import User
from app.services.auth import authenticate_websocket
from app.services.chat import chat_service
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.settings import get_runtime_config

router = APIRouter()

MIN_CHAT_TYPING_SECONDS = 0.75
CHAT_CHUNK_DELAY_SECONDS = 0.045


class ChatAttachmentRef(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    kind: str
    url: str
    download_url: str | None = None
    source: str | None = None
    created_at: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    session_id: str | None = None
    provider: str | None = None
    attachments: list[ChatAttachmentRef] = Field(default_factory=list)


class ChatResponse(BaseModel):
    session_id: str
    provider: str
    text: str
    tool_results: list[dict[str, Any]]
    attachments: list[ChatAttachmentRef] = Field(default_factory=list)


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
async def chat(
    request: ChatRequest,
    current_user: User = Depends(require_current_user),
) -> ChatResponse:
    if not request.message.strip() and not request.attachments:
        raise HTTPException(status_code=422, detail="Message or attachment is required.")
    result = await chat_service.handle_message(
        request.message,
        session_id=request.session_id,
        provider_name=request.provider,
        attachments=[attachment.model_dump() for attachment in request.attachments],
        user_id=str(current_user.id),
    )
    return ChatResponse(
        session_id=result.session_id,
        provider=result.provider,
        text=result.text,
        tool_results=result.tool_results,
        attachments=[ChatAttachmentRef(**attachment) for attachment in result.attachments],
    )


@router.post("/chat/upload", response_model=ChatAttachmentRef)
async def upload_chat_attachment(
    file: UploadFile = File(...),
    session_id: str | None = None,
    current_user: User = Depends(require_current_user),
) -> ChatAttachmentRef:
    try:
        attachment = await chat_attachment_store.save_upload(
            file,
            owner_user_id=str(current_user.id),
            session_id=session_id,
        )
    except ChatAttachmentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatAttachmentRef(**attachment.to_public_dict())


@router.get("/chat/files/{file_id}")
async def download_chat_attachment(
    file_id: str,
    current_user: User = Depends(require_current_user),
) -> FileResponse:
    try:
        attachment = chat_attachment_store.get(file_id)
        chat_attachment_store.require_access(attachment, str(current_user.id))
        path = chat_attachment_store.data_path(attachment)
    except ChatAttachmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    disposition = "inline" if attachment.kind == "image" else "attachment"
    return FileResponse(
        path,
        media_type=attachment.content_type,
        filename=attachment.filename,
        content_disposition_type=disposition,
        headers={"Cache-Control": "private, max-age=0"},
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
            attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
            if not message and not attachments:
                await websocket.send_json(
                    {"type": "chat.error", "payload": {"message": "Message or attachment is required."}}
                )
                continue

            await websocket.send_json({"type": "chat.thinking", "payload": {}})
            thinking_started_at = time.monotonic()

            async def publish_status(status: dict[str, Any]) -> None:
                await websocket.send_json({"type": "chat.tool_status", "payload": status})

            result = await chat_service.handle_message(
                message,
                session_id=payload.get("session_id"),
                provider_name=payload.get("provider"),
                attachments=attachments,
                user_id=str(user.id),
                status_callback=publish_status,
            )
            remaining_typing = MIN_CHAT_TYPING_SECONDS - (time.monotonic() - thinking_started_at)
            if remaining_typing > 0:
                await asyncio.sleep(remaining_typing)
            for chunk in _response_chunks(result.text):
                await websocket.send_json(
                    {
                        "type": "chat.response.delta",
                        "payload": {
                            "session_id": result.session_id,
                            "provider": result.provider,
                            "chunk": chunk,
                        },
                    }
                )
                await asyncio.sleep(CHAT_CHUNK_DELAY_SECONDS)
            await websocket.send_json(
                {
                    "type": "chat.response",
                    "payload": {
                        "session_id": result.session_id,
                        "provider": result.provider,
                        "text": result.text,
                        "tool_results": result.tool_results,
                        "attachments": result.attachments,
                    },
                }
            )
    except WebSocketDisconnect:
        return


def _response_chunks(text: str, chunk_size: int = 120) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            boundary = max(text.rfind(" ", start, end), text.rfind("\n", start, end))
            if boundary > start + 24:
                end = boundary + 1
        chunks.append(text[start:end])
        start = end
    return chunks
