import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.ai.providers import (
    ChatMessageInput,
    ProviderNotConfiguredError,
    ToolCall,
    get_llm_provider,
)
from app.ai.tools import AgentTool, build_agent_tools
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import ChatMessage, ChatSession
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config

logger = get_logger(__name__)


SYSTEM_PROMPT = """You are the Intelligent Access Control System assistant.
Answer concisely and use tool results as the source of truth for presence,
events, anomalies, schedules, access rhythm, DVLA vehicle lookups, and camera
snapshot analysis. If the
user asks a follow-up with pronouns like they, he, she, or it, use the session
memory context. Never invent access events, people, or DVLA vehicle records
that are not present in tool results. When a DVLA vehicle lookup succeeds,
format the result as a short human-readable vehicle details summary rather than
raw JSON. Camera snapshots are ephemeral and are not retained by default."""


@dataclass(frozen=True)
class ChatTurnResult:
    session_id: str
    provider: str
    text: str
    tool_results: list[dict[str, Any]]


class ChatService:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = build_agent_tools()

    async def handle_message(
        self,
        message: str,
        *,
        session_id: str | None = None,
        provider_name: str | None = None,
    ) -> ChatTurnResult:
        session_uuid = await self._ensure_session(session_id)
        await self._append_message(session_uuid, "user", message)

        memory = await self._load_memory(session_uuid)
        planned_calls = self._plan_tool_calls(message, memory)
        tool_results = [await self._execute_tool_call(session_uuid, call) for call in planned_calls]

        messages = await self._build_messages(session_uuid, tool_results)
        runtime = await get_runtime_config()
        provider = get_llm_provider(provider_name or runtime.llm_provider)

        try:
            result = await provider.complete(
                messages,
                tools=[tool.as_llm_tool() for tool in self._tools.values()],
                tool_results=tool_results if provider.name == "local" else None,
            )
            if result.tool_calls:
                native_results = [
                    await self._execute_tool_call(session_uuid, call)
                    for call in result.tool_calls
                ]
                tool_results.extend(native_results)
                messages = await self._build_messages(session_uuid, tool_results)
                result = await provider.complete(messages, tool_results=native_results)
        except ProviderNotConfiguredError as exc:
            logger.info(
                "llm_provider_not_configured_falling_back",
                extra={"provider": provider.name, "error": str(exc)},
            )
            provider = get_llm_provider("local")
            result = await provider.complete(messages, tool_results=tool_results)
        except Exception as exc:
            logger.warning(
                "llm_provider_failed_falling_back",
                extra={"provider": provider.name, "error": str(exc)},
            )
            provider = get_llm_provider("local")
            result = await provider.complete(messages, tool_results=tool_results)

        text = result.text or self._fallback_text(tool_results)
        await self._append_message(session_uuid, "assistant", text)
        await self._update_memory(session_uuid, message, tool_results)
        await event_bus.publish(
            "chat.message",
            {"session_id": str(session_uuid), "provider": provider.name, "text": text},
        )
        return ChatTurnResult(str(session_uuid), provider.name, text, tool_results)

    async def list_tools(self) -> list[dict[str, Any]]:
        return [tool.as_llm_tool() for tool in self._tools.values()]

    async def _ensure_session(self, session_id: str | None) -> uuid.UUID:
        async with AsyncSessionLocal() as session:
            if session_id:
                session_uuid = uuid.UUID(session_id)
                existing = await session.get(ChatSession, session_uuid)
                if existing:
                    return session_uuid
            else:
                session_uuid = uuid.uuid4()

            chat_session = ChatSession(id=session_uuid, context={})
            session.add(chat_session)
            await session.commit()
            return session_uuid

    async def _append_message(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        *,
        tool_name: str | None = None,
        tool_payload: dict[str, Any] | None = None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                ChatMessage(
                    session_id=session_id,
                    role=role,
                    content=content,
                    tool_name=tool_name,
                    tool_payload=tool_payload,
                )
            )
            await session.commit()

    async def _build_messages(
        self,
        session_id: uuid.UUID,
        tool_results: list[dict[str, Any]],
    ) -> list[ChatMessageInput]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .where(ChatMessage.role.in_(("user", "assistant")))
                    .order_by(ChatMessage.created_at.desc())
                    .limit(12)
                )
            ).all()
            chat_session = await session.get(ChatSession, session_id)
            memory = chat_session.context if chat_session and chat_session.context else {}

        messages = [ChatMessageInput("system", f"{SYSTEM_PROMPT}\nSession memory: {json.dumps(memory)}")]
        for row in reversed(rows):
            if row.role in {"user", "assistant"}:
                messages.append(ChatMessageInput(row.role, row.content))
        if tool_results:
            messages.append(
                ChatMessageInput(
                    "user",
                    "Tool results for the current user request: "
                    f"{json.dumps(tool_results, default=str)}",
                )
            )
        return messages

    async def _load_memory(self, session_id: uuid.UUID) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            chat_session = await session.get(ChatSession, session_id)
            return chat_session.context if chat_session and chat_session.context else {}

    async def _update_memory(
        self,
        session_id: uuid.UUID,
        user_message: str,
        tool_results: list[dict[str, Any]],
    ) -> None:
        memory = await self._load_memory(session_id)
        lower = user_message.lower()

        if "gardener" in lower:
            memory["last_group"] = "gardener"
            memory["last_subject"] = "gardener"

        for result in tool_results:
            output = result.get("output", {})
            for event in output.get("events", []):
                if event.get("person"):
                    memory["last_person"] = event["person"]
                    memory["last_subject"] = event["person"]
                if event.get("group"):
                    memory["last_group"] = event["group"]
            for presence in output.get("presence", []):
                if presence.get("person"):
                    memory["last_person"] = presence["person"]
                    memory["last_subject"] = presence["person"]

        async with AsyncSessionLocal() as session:
            chat_session = await session.get(ChatSession, session_id)
            if chat_session:
                chat_session.context = memory
                await session.commit()

    def _plan_tool_calls(self, message: str, memory: dict[str, Any]) -> list[ToolCall]:
        lower = message.lower()
        subject = self._subject_from_message(lower, memory)
        calls: list[ToolCall] = []

        if any(word in lower for word in ["present", "here", "onsite", "on site", "who is"]):
            calls.append(ToolCall("planned-query-presence", "query_presence", self._subject_args(subject)))

        if any(word in lower for word in ["arrive", "arrival", "came", "event", "denied", "gate"]):
            args = self._subject_args(subject)
            args["day"] = "today" if "today" in lower else "recent"
            calls.append(ToolCall("planned-query-events", "query_access_events", args))

        if any(word in lower for word in ["how long", "duration", "stay", "stayed"]):
            args = self._subject_args(subject)
            args["day"] = "today" if "today" in lower or memory else "recent"
            calls.append(ToolCall("planned-duration", "calculate_visit_duration", args))

        if any(word in lower for word in ["anomaly", "anomalies", "alert", "unauthorized"]):
            calls.append(ToolCall("planned-query-anomalies", "query_anomalies", {"limit": 25}))

        if ("send" in lower or "trigger" in lower) and "alert" in lower:
            calls.append(
                ToolCall(
                    "planned-trigger-alert",
                    "trigger_anomaly_alert",
                    {
                        "subject": memory.get("last_subject") or "Manual AI alert",
                        "severity": "warning",
                        "message": message,
                    },
                )
            )

        registration_number = self._registration_from_message(message)
        if registration_number and self._is_vehicle_lookup_request(lower):
            calls.append(
                ToolCall(
                    "planned-dvla-lookup",
                    "lookup_dvla_vehicle",
                    {"registration_number": registration_number},
                )
            )

        if any(word in lower for word in ["camera", "snapshot", "image"]) and any(
            word in lower for word in ["analyze", "analyse", "look", "see", "visible", "describe"]
        ):
            camera_name = self._camera_name_from_message(message)
            if camera_name:
                calls.append(
                    ToolCall(
                        "planned-camera-analysis",
                        "analyze_camera_snapshot",
                        {"camera_name": camera_name, "prompt": message},
                    )
                )

        if any(word in lower for word in ["summary", "summarize", "rhythm", "report"]):
            calls.append(
                ToolCall(
                    "planned-summary",
                    "summarize_access_rhythm",
                    {"day": "today" if "today" in lower else "recent"},
                )
            )

        if not calls:
            calls.append(ToolCall("planned-query-presence", "query_presence", {}))
        return calls

    def _subject_from_message(self, lower: str, memory: dict[str, Any]) -> dict[str, str]:
        if "gardener" in lower:
            return {"group": "gardener"}
        if "contractor" in lower:
            return {"group": "contractor"}
        if any(token in lower.split() for token in ["they", "them", "he", "she", "their"]):
            if memory.get("last_group"):
                return {"group": memory["last_group"]}
            if memory.get("last_person"):
                return {"person": memory["last_person"]}
        return {}

    def _subject_args(self, subject: dict[str, str]) -> dict[str, str]:
        if "group" in subject:
            return {"group": subject["group"]}
        if "person" in subject:
            return {"person": subject["person"]}
        return {}

    def _registration_from_message(self, message: str) -> str | None:
        for match in re.finditer(r"\b[A-Z0-9][A-Z0-9 -]{1,10}[A-Z0-9]\b", message.upper()):
            candidate = re.sub(r"[^A-Z0-9]", "", match.group(0))
            if 2 <= len(candidate) <= 8 and any(char.isalpha() for char in candidate) and any(char.isdigit() for char in candidate):
                return candidate
        return None

    def _camera_name_from_message(self, message: str) -> str | None:
        match = re.search(
            r"(?:camera|snapshot|image)\s+(?:called|named|from|of)?\s*([A-Za-z0-9 _.-]{2,80})",
            message,
            re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(1).strip(" .")

    def _is_vehicle_lookup_request(self, lower: str) -> bool:
        lookup_phrases = [
            "lookup details",
            "look up details",
            "lookup vehicle",
            "look up vehicle",
            "vehicle details",
            "details on",
            "details for",
            "check vehicle",
            "check registration",
            "dvla",
            "vehicle enquiry",
            "mot",
            "tax status",
            "taxed",
        ]
        return any(phrase in lower for phrase in lookup_phrases)

    async def _execute_tool_call(self, session_id: uuid.UUID, call: ToolCall) -> dict[str, Any]:
        tool = self._tools.get(call.name)
        if not tool:
            return {
                "call_id": call.id,
                "name": call.name,
                "output": {"error": f"Unknown tool: {call.name}"},
            }
        output = await tool.handler(call.arguments)
        await self._append_tool_message(session_id, call, output)
        return {"call_id": call.id, "name": call.name, "arguments": call.arguments, "output": output}

    async def _append_tool_message(
        self,
        session_id: uuid.UUID,
        call: ToolCall,
        output: dict[str, Any],
    ) -> None:
        await self._append_message(
            session_id,
            "tool",
            json.dumps(output, default=str, separators=(",", ":")),
            tool_name=call.name,
            tool_payload=output,
        )

    def _fallback_text(self, tool_results: list[dict[str, Any]]) -> str:
        if not tool_results:
            return "I could not find any live system context for that request."
        return json.dumps([result["output"] for result in tool_results], default=str)


chat_service = ChatService()
