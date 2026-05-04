import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from app.api.dependencies import admin_user, current_user as require_current_user
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import User
from app.services.auth import authenticate_websocket
from app.services.alfred.feedback import AlfredFeedbackError, alfred_feedback_service
from app.services.chat import chat_service
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, emit_audit_log, sanitize_payload

router = APIRouter()
logger = get_logger(__name__)

MIN_CHAT_TYPING_SECONDS = 0.75
CHAT_CHUNK_DELAY_SECONDS = 0.045
CHAT_RUNTIME_ERROR_MESSAGE = "Alfred hit an internal chat error. I logged it for review; please try again."


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
    client_context: dict[str, Any] = Field(default_factory=dict)


class ChatConfirmationRequest(BaseModel):
    session_id: str
    confirmation_id: str
    decision: str = "confirm"
    client_context: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    provider: str
    text: str
    tool_results: list[dict[str, Any]]
    attachments: list[ChatAttachmentRef] = Field(default_factory=list)
    pending_action: dict[str, Any] | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None


class AlfredFeedbackRequest(BaseModel):
    assistant_message_id: str
    rating: str
    reason: str | None = None
    ideal_answer: str | None = None
    source_channel: str = "dashboard"


class AlfredLessonReviewRequest(BaseModel):
    decision: str
    title: str | None = None
    lesson: str | None = None


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


@router.get("/agent/status")
async def agent_status(_: User = Depends(require_current_user)) -> dict[str, Any]:
    return await chat_service.agent_status()


@router.get("/memories")
async def list_memories(current_user: User = Depends(require_current_user)) -> dict[str, Any]:
    return {
        "memories": await chat_service.list_memories(
            user_id=str(current_user.id),
            user_role=current_user.role.value,
        )
    }


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, current_user: User = Depends(require_current_user)) -> dict[str, Any]:
    deleted = await chat_service.delete_memory(
        memory_id=memory_id,
        user_id=str(current_user.id),
        user_role=current_user.role.value,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"deleted": True}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: User = Depends(require_current_user),
) -> ChatResponse:
    if not request.message.strip() and not request.attachments:
        raise HTTPException(status_code=422, detail="Message or attachment is required.")
    try:
        result = await chat_service.handle_message(
            request.message,
            session_id=request.session_id,
            provider_name=request.provider,
            attachments=[attachment.model_dump() for attachment in request.attachments],
            user_id=str(current_user.id),
            user_role=current_user.role.value,
            client_context=request.client_context,
        )
    except Exception as exc:
        _record_chat_runtime_error("http", current_user, request.model_dump(), exc)
        raise HTTPException(status_code=500, detail=CHAT_RUNTIME_ERROR_MESSAGE) from exc
    return ChatResponse(
        session_id=result.session_id,
        provider=result.provider,
        text=result.text,
        tool_results=result.tool_results,
        attachments=[ChatAttachmentRef(**attachment) for attachment in result.attachments],
        pending_action=result.pending_action,
        user_message_id=result.user_message_id,
        assistant_message_id=result.assistant_message_id,
    )


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: User = Depends(require_current_user),
) -> StreamingResponse:
    if not request.message.strip() and not request.attachments:
        raise HTTPException(status_code=422, detail="Message or attachment is required.")

    async def stream_events():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def publish_status(status: dict[str, Any]) -> None:
            item = dict(status)
            event_type = str(item.pop("event", "chat.tool_status"))
            await queue.put({"type": event_type, "payload": item})

        async def run_turn() -> None:
            try:
                result = await chat_service.handle_message(
                    request.message,
                    session_id=request.session_id,
                    provider_name=request.provider,
                    attachments=[attachment.model_dump() for attachment in request.attachments],
                    user_id=str(current_user.id),
                    user_role=current_user.role.value,
                    client_context=request.client_context,
                    status_callback=publish_status,
                )
                for chunk in _response_chunks(result.text):
                    await queue.put(
                        {
                            "type": "chat.response.delta",
                            "payload": {
                                "session_id": result.session_id,
                                "provider": result.provider,
                                "chunk": chunk,
                            },
                        }
                    )
                await queue.put(
                    {
                        "type": "chat.response",
                        "payload": {
                            "session_id": result.session_id,
                            "provider": result.provider,
                            "text": result.text,
                            "tool_results": result.tool_results,
                            "attachments": result.attachments,
                            "pending_action": result.pending_action,
                            "user_message_id": result.user_message_id,
                            "assistant_message_id": result.assistant_message_id,
                        },
                    }
                )
            except Exception as exc:
                _record_chat_runtime_error("sse", current_user, request.model_dump(), exc)
                await queue.put({"type": "chat.error", "payload": {"message": CHAT_RUNTIME_ERROR_MESSAGE}})
            finally:
                await queue.put({"type": "stream.done", "payload": {}})

        task = asyncio.create_task(run_turn())
        yield _sse_event("chat.thinking", {})
        try:
            while True:
                event = await queue.get()
                yield _sse_event(event["type"], event.get("payload") or {})
                if event["type"] == "stream.done":
                    break
        finally:
            await task

    return StreamingResponse(stream_events(), media_type="text/event-stream")


@router.post("/chat/confirm", response_model=ChatResponse)
async def confirm_chat_action(
    request: ChatConfirmationRequest,
    current_user: User = Depends(require_current_user),
) -> ChatResponse:
    try:
        result = await chat_service.handle_tool_confirmation(
            confirmation_id=request.confirmation_id,
            decision=request.decision,
            session_id=request.session_id,
            user_id=str(current_user.id),
            user_role=current_user.role.value,
            client_context=request.client_context,
        )
    except Exception as exc:
        _record_chat_runtime_error("http_confirm", current_user, request.model_dump(), exc)
        raise HTTPException(status_code=500, detail=CHAT_RUNTIME_ERROR_MESSAGE) from exc
    return ChatResponse(
        session_id=result.session_id,
        provider=result.provider,
        text=result.text,
        tool_results=result.tool_results,
        attachments=[ChatAttachmentRef(**attachment) for attachment in result.attachments],
        pending_action=result.pending_action,
        user_message_id=result.user_message_id,
        assistant_message_id=result.assistant_message_id,
    )


@router.post("/feedback")
async def submit_feedback(
    request: AlfredFeedbackRequest,
    current_user: User = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        return await alfred_feedback_service.submit_feedback(
            assistant_message_id=request.assistant_message_id,
            rating=request.rating,
            reason=request.reason,
            ideal_answer=request.ideal_answer,
            source_channel=request.source_channel or "dashboard",
            user=current_user,
        )
    except AlfredFeedbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/training/feedback")
async def list_training_feedback(_: User = Depends(admin_user), limit: int = 100) -> dict[str, Any]:
    return {"feedback": await alfred_feedback_service.list_feedback(limit=limit)}


@router.get("/training/lessons")
async def list_training_lessons(
    _: User = Depends(admin_user),
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return {"lessons": await alfred_feedback_service.list_lessons(status=status, limit=limit)}


@router.post("/training/lessons/{lesson_id}/review")
async def review_training_lesson(
    lesson_id: str,
    request: AlfredLessonReviewRequest,
    current_user: User = Depends(admin_user),
) -> dict[str, Any]:
    try:
        return {
            "lesson": await alfred_feedback_service.review_lesson(
                lesson_id=lesson_id,
                decision=request.decision,
                reviewer=current_user,
                lesson_text=request.lesson,
                title=request.title,
            )
        }
    except AlfredFeedbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/training/eval-examples")
async def list_training_eval_examples(_: User = Depends(admin_user), limit: int = 100) -> dict[str, Any]:
    return {"examples": await alfred_feedback_service.list_eval_examples(limit=limit)}


@router.get("/training/eval-export")
async def export_training_eval(_: User = Depends(admin_user)) -> PlainTextResponse:
    return PlainTextResponse(
        await alfred_feedback_service.export_eval_jsonl(),
        media_type="application/jsonl",
        headers={"Content-Disposition": 'attachment; filename="alfred-eval-examples.jsonl"'},
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
            try:
                payload = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                _record_chat_runtime_error("websocket_receive", user, {}, exc)
                await _send_runtime_chat_error(websocket, "Alfred could not read that chat message. Please try again.")
                continue

            try:
                message = str(payload.get("message") or "").strip()
                attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else []
                tool_confirmation = payload.get("tool_confirmation")
                client_context = payload.get("client_context") if isinstance(payload.get("client_context"), dict) else {}
                if not message and not attachments and not isinstance(tool_confirmation, dict):
                    await websocket.send_json(
                        {"type": "chat.error", "payload": {"message": "Message or attachment is required."}}
                    )
                    continue

                await websocket.send_json({"type": "chat.thinking", "payload": {}})
                thinking_started_at = time.monotonic()

                async def publish_status(status: dict[str, Any]) -> None:
                    event_type = str(status.pop("event", "chat.tool_status"))
                    await websocket.send_json({"type": event_type, "payload": status})

                if isinstance(tool_confirmation, dict):
                    confirmation_id = str(tool_confirmation.get("id") or tool_confirmation.get("confirmation_id") or "").strip()
                    decision = str(tool_confirmation.get("decision") or "confirm").strip() or "confirm"
                    if not confirmation_id:
                        await websocket.send_json(
                            {"type": "chat.error", "payload": {"message": "Confirmation action is missing its ID."}}
                        )
                        continue
                    result = await chat_service.handle_tool_confirmation(
                        confirmation_id=confirmation_id,
                        decision=decision,
                        session_id=payload.get("session_id"),
                        user_id=str(user.id),
                        user_role=user.role.value,
                        client_context=client_context,
                        status_callback=publish_status,
                    )
                else:
                    result = await chat_service.handle_message(
                        message,
                        session_id=payload.get("session_id"),
                        provider_name=payload.get("provider"),
                        attachments=attachments,
                        user_id=str(user.id),
                        user_role=user.role.value,
                        client_context=client_context,
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
                            "pending_action": result.pending_action,
                            "user_message_id": result.user_message_id,
                            "assistant_message_id": result.assistant_message_id,
                        },
                    }
                )
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                _record_chat_runtime_error("websocket", user, payload, exc)
                await _send_runtime_chat_error(websocket, CHAT_RUNTIME_ERROR_MESSAGE)
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


def _sse_event(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, default=str, separators=(',', ':'))}\n\n"


async def _send_runtime_chat_error(websocket: WebSocket, message: str) -> None:
    try:
        await websocket.send_json({"type": "chat.error", "payload": {"message": message}})
    except WebSocketDisconnect:
        raise
    except Exception:
        logger.debug("alfred_chat_error_send_failed")


def _record_chat_runtime_error(channel: str, user: User, payload: Mapping[str, Any], exc: Exception) -> None:
    metadata = {
        "channel": channel,
        "error_type": exc.__class__.__name__,
        "error": str(exc)[:500],
        "session_id": payload.get("session_id"),
        "provider": payload.get("provider"),
        "message_preview": str(payload.get("message") or "")[:240],
        "attachment_count": len(payload.get("attachments") or []) if isinstance(payload.get("attachments"), list) else 0,
        "has_confirmation": isinstance(payload.get("tool_confirmation"), dict) or bool(payload.get("confirmation_id")),
        "client_context": payload.get("client_context") if isinstance(payload.get("client_context"), dict) else {},
    }
    logger.exception(
        "alfred_chat_runtime_error",
        extra={"channel": channel, "user_id": str(user.id), "error_type": exc.__class__.__name__},
    )
    emit_audit_log(
        category=TELEMETRY_CATEGORY_ALFRED,
        action=f"alfred.chat.{channel}_error",
        actor="Alfred_Runtime",
        actor_user_id=user.id,
        target_entity="ChatSession",
        target_id=str(payload.get("session_id") or "") or None,
        metadata=sanitize_payload(metadata),
        outcome="failure",
        level="error",
    )
