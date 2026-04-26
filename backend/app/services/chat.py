import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.ai.providers import (
    ChatMessageInput,
    LlmResult,
    ProviderNotConfiguredError,
    ToolCall,
    get_llm_provider,
)
from app.ai.tools import AgentTool, build_agent_tools, set_chat_tool_context
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import ChatMessage, ChatSession
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config

logger = get_logger(__name__)

MAX_AGENT_TOOL_ITERATIONS = 5


SYSTEM_PROMPT = """You are the Intelligent Access Control System assistant.
Answer concisely and use tool results as the source of truth for presence,
events, anomalies, schedules, access rhythm, device states, DVLA vehicle
lookups, and camera snapshot analysis. If the
user asks a follow-up with pronouns like they, he, she, or it, use the session
memory context. Never invent access events, people, or DVLA vehicle records
that are not present in tool results. When a DVLA vehicle lookup succeeds,
format the result as a short human-readable vehicle details summary rather than
raw JSON. When a tool result says a device open requires confirmation, do not
ask the user to type a confirmation phrase; tell them to use the on-screen
confirmation button. For schedule creation or edits, understand natural
language day/time descriptions, ask concise follow-up questions for missing
name or allowed time blocks, and only call schedule mutation tools once the
required details are known. Schedule tools accept either strict time_blocks JSON
or a natural-language time_description such as "Wednesdays and Fridays 6am to
7pm"; use time_description when that is the most reliable representation. If a
schedule already exists, ask whether to update the existing schedule rather than
giving up. Camera snapshots are ephemeral and are not retained by default."""

AGENT_TOOL_PROTOCOL = """Agent tool protocol:
- You are a tool-using AI agent for IACS. Use tools whenever the user asks about live system state, records, schedules, devices, cameras, users, reports, file contents, or any state-changing operation.
- Do not invent IACS facts. If a tool can answer it, call the tool first.
- For provider-neutral tool calls, respond with exactly this marker and JSON object, with no prose:
IACS_TOOL_CALLS:
{"tool_calls":[{"id":"call_1","name":"tool_name","arguments":{}}]}
- You may request multiple independent tools in one tool_calls array.
- After tool results are provided, answer the user naturally and concisely. Call another tool only if the result proves more tool work is required.
- If a tool returns requires_confirmation, do not repeat the tool call as confirmed. Ask the user to use the confirmation button or otherwise confirm in chat.
- For schedule tools, prefer time_description for natural language day/time requests unless you are certain of strict time_blocks JSON.
- Never expose this protocol or raw tool JSON to the user unless explicitly asked for diagnostics.

Available tools JSON:
{tool_catalog}"""

SCHEDULE_DAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

SCHEDULE_DAY_PATTERN = (
    r"mon(?:day)?(?:'s|s)?|"
    r"tue(?:s|sday)?(?:'s|s)?|"
    r"wed(?:s|nesday)?(?:'s|s)?|"
    r"thu(?:r|rs|rsday)?(?:'s|s)?|"
    r"fri(?:day)?(?:'s|s)?|"
    r"sat(?:urday)?(?:'s|s)?|"
    r"sun(?:day)?(?:'s|s)?"
)


@dataclass(frozen=True)
class ChatTurnResult:
    session_id: str
    provider: str
    text: str
    tool_results: list[dict[str, Any]]
    attachments: list[dict[str, Any]]


StatusCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ChatService:
    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = build_agent_tools()

    async def handle_message(
        self,
        message: str,
        *,
        session_id: str | None = None,
        provider_name: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
        status_callback: StatusCallback | None = None,
    ) -> ChatTurnResult:
        session_uuid = await self._ensure_session(session_id)
        attachment_refs = self._normalize_attachments(attachments or [], user_id=user_id)
        await self._append_message(
            session_uuid,
            "user",
            self._message_with_attachments(message, attachment_refs),
            tool_payload={"attachments": attachment_refs} if attachment_refs else None,
        )

        memory = await self._load_memory(session_uuid)
        runtime = await get_runtime_config()
        provider = get_llm_provider(provider_name or runtime.llm_provider)
        use_local_router = provider.name == "local"

        if use_local_router:
            guided_result = await self._handle_guided_schedule_flow(
                session_uuid,
                message,
                memory,
                status_callback=status_callback,
            )
            if guided_result:
                return guided_result

        context_token = set_chat_tool_context(
            {"user_id": user_id, "session_id": str(session_uuid)}
        )
        try:
            tool_results: list[dict[str, Any]] = []
            if use_local_router:
                planned_calls = self._plan_tool_calls(message, memory, attachment_refs)
                tool_results = [
                    await self._execute_tool_call(session_uuid, call, status_callback=status_callback)
                    for call in planned_calls
                ]

            messages = await self._build_messages(session_uuid, tool_results)

            try:
                result = await self._run_provider_agent_loop(
                    provider,
                    session_uuid,
                    messages,
                    tool_results,
                    memory,
                    status_callback=status_callback,
                )
                if isinstance(result, ChatTurnResult):
                    return result
            except ProviderNotConfiguredError as exc:
                logger.info(
                    "llm_provider_not_configured",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                return await self._provider_error_response(session_uuid, provider.name, exc)
            except Exception as exc:
                logger.warning(
                    "llm_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                return await self._provider_error_response(session_uuid, provider.name, exc)
        finally:
            set_chat_tool_context({}, token=context_token)

        text = result.text or self._fallback_text(tool_results)
        await self._append_message(session_uuid, "assistant", text)
        await self._update_memory(session_uuid, message, tool_results)
        response_attachments = self._attachments_from_tool_results(tool_results)
        await event_bus.publish(
            "chat.message",
            {
                "session_id": str(session_uuid),
                "provider": provider.name,
                "text": text,
                "attachments": response_attachments,
            },
        )
        return ChatTurnResult(
            str(session_uuid),
            provider.name,
            text,
            tool_results,
            response_attachments,
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        return [tool.as_llm_tool() for tool in self._tools.values()]

    async def _run_provider_agent_loop(
        self,
        provider: Any,
        session_id: uuid.UUID,
        messages: list[ChatMessageInput],
        tool_results: list[dict[str, Any]],
        memory: dict[str, Any],
        *,
        status_callback: StatusCallback | None,
    ) -> LlmResult | ChatTurnResult:
        tool_schemas = [tool.as_llm_tool() for tool in self._tools.values()]
        executed: set[str] = set()
        result = LlmResult(text="")

        for iteration in range(MAX_AGENT_TOOL_ITERATIONS):
            result = await provider.complete(
                messages,
                tools=tool_schemas,
                tool_results=tool_results if provider.name == "local" else None,
            )
            calls = self._tool_calls_from_result(result, iteration=iteration)
            if not calls:
                return LlmResult(text=self._clean_agent_text(result.text), raw=result.raw)

            fresh_calls: list[ToolCall] = []
            for call in calls:
                fingerprint = self._tool_call_fingerprint(call)
                if fingerprint not in executed:
                    executed.add(fingerprint)
                    fresh_calls.append(call)
            if not fresh_calls:
                return LlmResult(
                    text=(
                        "I already ran the relevant system tool and could not make further progress. "
                        "Please rephrase the request or provide the missing detail."
                    ),
                    raw=result.raw,
                )

            native_results = [
                await self._execute_tool_call(
                    session_id,
                    call,
                    status_callback=status_callback,
                )
                for call in fresh_calls
            ]
            tool_results.extend(native_results)
            conflict_result = await self._schedule_conflict_response(session_id, memory, tool_results)
            if conflict_result:
                return conflict_result
            messages = await self._build_messages(session_id, tool_results)

        return LlmResult(
            text=(
                "I ran several system checks but still need a clearer next step. "
                "Please narrow the request or confirm the specific action."
            ),
            raw=result.raw,
        )

    def _tool_calls_from_result(self, result: LlmResult, *, iteration: int) -> list[ToolCall]:
        if result.tool_calls:
            return result.tool_calls
        return self._tool_calls_from_text(result.text, iteration=iteration)

    def _tool_calls_from_text(self, text: str, *, iteration: int) -> list[ToolCall]:
        payload = self._extract_tool_call_payload(text)
        if payload is None:
            return []

        raw_calls: Any
        if isinstance(payload, dict):
            raw_calls = payload.get("tool_calls") or payload.get("tools") or payload.get("calls")
            if raw_calls is None and (payload.get("name") or payload.get("tool")):
                raw_calls = [payload]
        elif isinstance(payload, list):
            raw_calls = payload
        else:
            raw_calls = None

        if not isinstance(raw_calls, list):
            return []

        calls: list[ToolCall] = []
        for index, item in enumerate(raw_calls):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("tool") or "").strip()
            if not name:
                continue
            arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                arguments = item.get("args") if isinstance(item.get("args"), dict) else {}
            calls.append(
                ToolCall(
                    id=str(item.get("id") or f"protocol-{iteration}-{index}"),
                    name=name,
                    arguments=arguments,
                )
            )
        return calls

    def _extract_tool_call_payload(self, text: str) -> Any | None:
        if not text:
            return None
        candidates: list[str] = []
        marker = "IACS_TOOL_CALLS:"
        if marker in text:
            candidates.append(text.split(marker, 1)[1].strip())
        candidates.extend(
            match.group(1).strip()
            for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        )
        candidates.append(text.strip())

        for candidate in candidates:
            json_text = self._first_json_value(candidate)
            if not json_text:
                continue
            try:
                return json.loads(json_text)
            except json.JSONDecodeError:
                continue
        return None

    def _first_json_value(self, text: str) -> str | None:
        start_positions = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
        if not start_positions:
            return None
        start = min(start_positions)
        opener = text[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None

    def _clean_agent_text(self, text: str) -> str:
        cleaned = text.strip()
        if "IACS_TOOL_CALLS:" in cleaned:
            return "I could not safely interpret the tool request. Please rephrase that and I will try again."
        if cleaned.startswith("IACS_FINAL:"):
            return cleaned.removeprefix("IACS_FINAL:").strip()
        return cleaned

    def _tool_call_fingerprint(self, call: ToolCall) -> str:
        return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"

    async def _handle_guided_schedule_flow(
        self,
        session_id: uuid.UUID,
        message: str,
        memory: dict[str, Any],
        *,
        status_callback: StatusCallback | None,
    ) -> ChatTurnResult | None:
        pending = memory.get("pending_schedule_create")
        if isinstance(pending, dict):
            return await self._continue_schedule_create(session_id, message, memory, pending, status_callback=status_callback)

        if not self._looks_like_schedule_create_request(message.lower()):
            return None

        name = self._schedule_name_from_message(message)
        time_blocks = self._parse_schedule_time_blocks(message)
        if not name:
            memory["pending_schedule_create"] = {"stage": "name"}
            await self._save_memory(session_id, memory)
            return await self._direct_response(
                session_id,
                "Sure - what would you like to name the new schedule?",
            )

        if not time_blocks:
            memory["pending_schedule_create"] = {"stage": "time_blocks", "name": name}
            await self._save_memory(session_id, memory)
            return await self._direct_response(
                session_id,
                f"Great. What days and times should {name} allow? For example: Monday to Friday 08:00-17:00, weekends 10:00-14:00, or 24/7.",
            )

        return await self._create_schedule_from_chat(
            session_id,
            name=name,
            time_blocks=time_blocks,
            status_callback=status_callback,
            memory=memory,
        )

    async def _continue_schedule_create(
        self,
        session_id: uuid.UUID,
        message: str,
        memory: dict[str, Any],
        pending: dict[str, Any],
        *,
        status_callback: StatusCallback | None,
    ) -> ChatTurnResult:
        lower = message.lower().strip()
        if lower in {"cancel", "stop", "never mind", "nevermind"}:
            memory.pop("pending_schedule_create", None)
            await self._save_memory(session_id, memory)
            return await self._direct_response(session_id, "No problem - I cancelled that schedule setup.")

        stage = str(pending.get("stage") or "name")
        if stage == "confirm_update":
            name = str(pending.get("name") or "").strip()
            time_blocks = pending.get("time_blocks") if isinstance(pending.get("time_blocks"), dict) else None
            revised_time_blocks = self._parse_schedule_time_blocks(message)
            if revised_time_blocks:
                time_blocks = revised_time_blocks
                pending["time_blocks"] = revised_time_blocks
                memory["pending_schedule_create"] = pending
                await self._save_memory(session_id, memory)
                return await self._ask_to_update_existing_schedule(session_id, memory, name=name, time_blocks=revised_time_blocks)
            if self._is_confirmation_message(lower) and name and time_blocks:
                return await self._update_existing_schedule_from_chat(
                    session_id,
                    name=name,
                    time_blocks=time_blocks,
                    status_callback=status_callback,
                    memory=memory,
                )
            if self._is_rejection_message(lower):
                memory.pop("pending_schedule_create", None)
                await self._save_memory(session_id, memory)
                return await self._direct_response(session_id, f"Okay, I left {name or 'that schedule'} unchanged.")
            if name and time_blocks:
                return await self._ask_to_update_existing_schedule(session_id, memory, name=name, time_blocks=time_blocks)
            memory["pending_schedule_create"] = {"stage": "name"}
            await self._save_memory(session_id, memory)
            return await self._direct_response(session_id, "What should I call the schedule?")

        if stage == "name":
            name = self._clean_schedule_name(message)
            if not name:
                return await self._direct_response(session_id, "What should I call the new schedule?")
            time_blocks = self._parse_schedule_time_blocks(message)
            pending = {"stage": "time_blocks", "name": name}
            memory["pending_schedule_create"] = pending
            await self._save_memory(session_id, memory)
            if time_blocks:
                return await self._create_schedule_from_chat(
                    session_id,
                    name=name,
                    time_blocks=time_blocks,
                    status_callback=status_callback,
                    memory=memory,
                )
            return await self._direct_response(
                session_id,
                f"Got it: {name}. What days and times should it allow? For example: Monday to Friday 08:00-17:00, weekends 10:00-14:00, or 24/7.",
            )

        name = str(pending.get("name") or "").strip()
        if not name:
            memory["pending_schedule_create"] = {"stage": "name"}
            await self._save_memory(session_id, memory)
            return await self._direct_response(session_id, "What should I call the new schedule?")

        time_blocks = self._parse_schedule_time_blocks(message)
        if not time_blocks and self._refers_to_previous_timeframe(lower):
            previous_attempt = str(pending.get("last_time_attempt") or "")
            if previous_attempt:
                time_blocks = self._parse_schedule_time_blocks(previous_attempt)
        if not time_blocks:
            pending["last_time_attempt"] = message
            memory["pending_schedule_create"] = pending
            await self._save_memory(session_id, memory)
            return await self._direct_response(
                session_id,
                "I could not read that as a schedule yet. Try days plus a time range, for example: Wednesdays and Fridays 6am to 7pm, weekdays 08:00-17:00, or 24/7.",
            )

        return await self._create_schedule_from_chat(
            session_id,
            name=name,
            time_blocks=time_blocks,
            status_callback=status_callback,
            memory=memory,
        )

    async def _create_schedule_from_chat(
        self,
        session_id: uuid.UUID,
        *,
        name: str,
        time_blocks: dict[str, list[dict[str, str]]],
        status_callback: StatusCallback | None,
        memory: dict[str, Any],
    ) -> ChatTurnResult:
        call = ToolCall(
            "guided-create-schedule",
            "create_schedule",
            {"name": name, "time_blocks": time_blocks},
        )
        tool_result = await self._execute_tool_call(session_id, call, status_callback=status_callback)
        output = tool_result.get("output", {})
        if output.get("created") and isinstance(output.get("schedule"), dict):
            memory.pop("pending_schedule_create", None)
            await self._save_memory(session_id, memory)
            schedule = output["schedule"]
            text = f"Created {schedule.get('name', name)} with {schedule.get('summary', 'the requested allowed time')}."
        elif output.get("error_code") == "schedule_exists" or output.get("error") == "Schedule already exists.":
            return await self._ask_to_update_existing_schedule(session_id, memory, name=name, time_blocks=time_blocks)
        else:
            memory.pop("pending_schedule_create", None)
            await self._save_memory(session_id, memory)
            text = output.get("detail") or output.get("error") or "I could not create that schedule."
        return await self._direct_response(session_id, text, tool_results=[tool_result])

    async def _schedule_conflict_response(
        self,
        session_id: uuid.UUID,
        memory: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> ChatTurnResult | None:
        for result in tool_results:
            if result.get("name") != "create_schedule":
                continue
            output = result.get("output")
            if not isinstance(output, dict) or output.get("error_code") != "schedule_exists":
                continue
            arguments = result.get("arguments") if isinstance(result.get("arguments"), dict) else {}
            name = str(output.get("schedule_name") or arguments.get("name") or "").strip()
            time_blocks = arguments.get("time_blocks")
            if not isinstance(time_blocks, dict):
                natural_text = " ".join(
                    str(arguments.get(key) or "").strip()
                    for key in ("time_description", "description")
                    if str(arguments.get(key) or "").strip()
                )
                time_blocks = self._parse_schedule_time_blocks(natural_text) if natural_text else None
            if name and isinstance(time_blocks, dict):
                return await self._ask_to_update_existing_schedule(
                    session_id,
                    memory,
                    name=name,
                    time_blocks=time_blocks,
                )
        return None

    async def _ask_to_update_existing_schedule(
        self,
        session_id: uuid.UUID,
        memory: dict[str, Any],
        *,
        name: str,
        time_blocks: dict[str, list[dict[str, str]]],
    ) -> ChatTurnResult:
        memory["pending_schedule_create"] = {
            "stage": "confirm_update",
            "name": name,
            "time_blocks": time_blocks,
        }
        await self._save_memory(session_id, memory)
        summary = self._chat_schedule_summary(time_blocks)
        text = (
            f"{name} already exists. Do you want me to replace its allowed times with {summary}? "
            "This will update the existing schedule rather than create a duplicate."
        )
        tool_result = {
            "call_id": "guided-confirm-update-schedule",
            "name": "update_schedule",
            "arguments": {"schedule_name": name, "time_blocks": time_blocks, "confirm": False},
            "output": {
                "requires_confirmation": True,
                "schedule_name": name,
                "time_blocks": time_blocks,
                "summary": summary,
                "detail": text,
            },
        }
        return await self._direct_response(session_id, text, tool_results=[tool_result])

    async def _update_existing_schedule_from_chat(
        self,
        session_id: uuid.UUID,
        *,
        name: str,
        time_blocks: dict[str, list[dict[str, str]]],
        status_callback: StatusCallback | None,
        memory: dict[str, Any],
    ) -> ChatTurnResult:
        call = ToolCall(
            "guided-update-schedule",
            "update_schedule",
            {"schedule_name": name, "time_blocks": time_blocks},
        )
        tool_result = await self._execute_tool_call(session_id, call, status_callback=status_callback)
        memory.pop("pending_schedule_create", None)
        await self._save_memory(session_id, memory)
        output = tool_result.get("output", {})
        if output.get("updated") and isinstance(output.get("schedule"), dict):
            schedule = output["schedule"]
            text = f"Updated {schedule.get('name', name)} to {schedule.get('summary', self._chat_schedule_summary(time_blocks))}."
        else:
            text = output.get("detail") or output.get("error") or f"I could not update {name}."
        return await self._direct_response(session_id, text, tool_results=[tool_result])

    async def _direct_response(
        self,
        session_id: uuid.UUID,
        text: str,
        *,
        tool_results: list[dict[str, Any]] | None = None,
        provider: str = "guided",
    ) -> ChatTurnResult:
        tool_results = tool_results or []
        await self._append_message(session_id, "assistant", text)
        attachments = self._attachments_from_tool_results(tool_results)
        await event_bus.publish(
            "chat.message",
            {
                "session_id": str(session_id),
                "provider": provider,
                "text": text,
                "attachments": attachments,
            },
        )
        return ChatTurnResult(str(session_id), provider, text, tool_results, attachments)

    async def _provider_error_response(
        self,
        session_id: uuid.UUID,
        provider_name: str,
        exc: Exception,
    ) -> ChatTurnResult:
        detail = self._safe_provider_error(exc)
        text = (
            f"I cannot use the configured {provider_name} provider right now: {detail}. "
            "I did not run any system action. Please fix the provider settings or switch provider."
        )
        tool_result = {
            "call_id": "llm-provider-error",
            "name": "llm_provider",
            "arguments": {"provider": provider_name},
            "output": {
                "provider": provider_name,
                "error": detail,
            },
        }
        return await self._direct_response(
            session_id,
            text,
            tool_results=[tool_result],
            provider="provider_error",
        )

    def _safe_provider_error(self, exc: Exception) -> str:
        detail = str(exc).strip() or exc.__class__.__name__
        detail = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", detail)
        detail = re.sub(r"(?i)(api[_-]?key|x-api-key|key)=([^&\s]+)", r"\1=[redacted]", detail)
        return detail[:300]

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

        tool_protocol = AGENT_TOOL_PROTOCOL.replace(
            "{tool_catalog}",
            json.dumps(
                [tool.as_llm_tool() for tool in self._tools.values()],
                separators=(",", ":"),
            ),
        )
        messages = [
            ChatMessageInput(
                "system",
                f"{SYSTEM_PROMPT}\nSession memory: {json.dumps(memory, default=str)}\n\n{tool_protocol}",
            )
        ]
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

    async def _save_memory(self, session_id: uuid.UUID, memory: dict[str, Any]) -> None:
        async with AsyncSessionLocal() as session:
            chat_session = await session.get(ChatSession, session_id)
            if chat_session:
                chat_session.context = memory
                await session.commit()

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

        await self._save_memory(session_id, memory)

    def _plan_tool_calls(
        self,
        message: str,
        memory: dict[str, Any],
        attachments: list[dict[str, Any]],
    ) -> list[ToolCall]:
        lower = message.lower()
        subject = self._subject_from_message(lower, memory)
        calls: list[ToolCall] = []

        for index, attachment in enumerate(attachments[:4]):
            calls.append(
                ToolCall(
                    f"planned-read-attachment-{index}",
                    "read_chat_attachment",
                    {
                        "file_id": attachment["id"],
                        "prompt": message or "Summarize this attachment for the user.",
                    },
                )
            )

        if self._looks_like_device_open_request(lower):
            calls.append(
                ToolCall(
                    "planned-open-device",
                    "open_device",
                    {
                        "target": self._device_target_from_message(lower) or "",
                        "kind": "all",
                        "reason": message,
                        "confirm": self._explicitly_confirmed_device_open(lower),
                    },
                )
            )

        if self._looks_like_device_state_request(lower):
            calls.append(
                ToolCall(
                    "planned-query-device-states",
                    "query_device_states",
                    {"target": self._device_target_from_message(lower) or "", "kind": "all"},
                )
            )

        if any(word in lower for word in ["present", "here", "onsite", "on site", "who is"]):
            calls.append(ToolCall("planned-query-presence", "query_presence", self._subject_args(subject)))

        if any(word in lower for word in ["arrive", "arrival", "came", "event", "denied"]):
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

        if "presence" in lower and any(word in lower for word in ["csv", "export", "download", "spreadsheet"]):
            calls.append(
                ToolCall(
                    "planned-presence-csv",
                    "export_presence_report_csv",
                    {"day": "today" if "today" in lower else "recent"},
                )
            )

        if "invoice" in lower and "contractor" in lower:
            calls.append(
                ToolCall(
                    "planned-contractor-invoice",
                    "generate_contractor_invoice_pdf",
                    {"contractor_name": memory.get("last_subject") or "Contractor", "day": "today"},
                )
            )

        if any(word in lower for word in ["snapshot", "image", "camera"]) and any(
            word in lower for word in ["attach", "fetch", "get", "send", "show"]
        ):
            camera_name = self._camera_name_from_message(message)
            if camera_name:
                calls.append(
                    ToolCall(
                        "planned-camera-snapshot",
                        "get_camera_snapshot",
                        {"camera_name": camera_name},
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

    async def _execute_tool_call(
        self,
        session_id: uuid.UUID,
        call: ToolCall,
        *,
        status_callback: StatusCallback | None = None,
    ) -> dict[str, Any]:
        tool = self._tools.get(call.name)
        if not tool:
            return {
                "call_id": call.id,
                "name": call.name,
                "output": {"error": f"Unknown tool: {call.name}"},
            }
        if status_callback:
            await status_callback(self._tool_status(call.name))
        output = await tool.handler(call.arguments)
        await self._append_tool_message(session_id, call, output)
        return {"call_id": call.id, "name": call.name, "arguments": call.arguments, "output": output}

    def _tool_status(self, tool_name: str) -> dict[str, Any]:
        labels = {
            "query_presence": "Checking presence logs...",
            "query_device_states": "Checking device states...",
            "open_device": "Preparing device open command...",
            "query_access_events": "Reviewing access events...",
            "query_anomalies": "Checking anomaly records...",
            "summarize_access_rhythm": "Summarizing site rhythm...",
            "calculate_visit_duration": "Calculating visit duration...",
            "trigger_anomaly_alert": "Preparing alert notification...",
            "get_system_users": "Checking user directory...",
            "lookup_dvla_vehicle": "Looking up vehicle details...",
            "analyze_camera_snapshot": "Analyzing camera snapshot...",
            "read_chat_attachment": "Reading attachment...",
            "export_presence_report_csv": "Generating CSV report...",
            "generate_contractor_invoice_pdf": "Generating PDF invoice...",
            "get_camera_snapshot": "Fetching camera snapshot...",
            "query_schedules": "Checking schedules...",
            "get_schedule": "Checking schedule details...",
            "create_schedule": "Creating schedule...",
            "update_schedule": "Updating schedule...",
            "delete_schedule": "Deleting schedule...",
            "query_schedule_targets": "Checking schedule assignments...",
            "assign_schedule_to_entity": "Assigning schedule...",
            "verify_schedule_access": "Verifying schedule access...",
        }
        return {"tool": tool_name, "label": labels.get(tool_name, "Running system tool...")}

    def _looks_like_schedule_create_request(self, lower: str) -> bool:
        return "schedule" in lower and any(word in lower for word in ["create", "new", "add", "make"])

    def _is_confirmation_message(self, lower: str) -> bool:
        return bool(re.search(r"\b(yes|confirm|confirmed|update|replace|proceed|go ahead|do it|approved|approve)\b", lower))

    def _is_rejection_message(self, lower: str) -> bool:
        return bool(re.search(r"\b(no|cancel|stop|leave|unchanged|do not|don't)\b", lower))

    def _refers_to_previous_timeframe(self, lower: str) -> bool:
        return any(phrase in lower for phrase in ["already told", "told you", "as i said", "same as before", "previous"])

    def _schedule_name_from_message(self, message: str) -> str | None:
        patterns = [
            r"(?:called|named)\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
            r"schedule\s+(?:for\s+)?['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if not match:
                continue
            name = self._clean_schedule_name(match.group(1))
            name = re.split(
                rf"\b(?:{SCHEDULE_DAY_PATTERN}|weekday|weekend|every day|daily|24/7)\b",
                name,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()
            if name and name.lower() not in {"a new", "new", "called", "named"}:
                return name
        return None

    def _clean_schedule_name(self, message: str) -> str:
        name = re.sub(r"\s+", " ", message.strip(" .?\"'"))
        return name[:120].strip()

    def _parse_schedule_time_blocks(self, message: str) -> dict[str, list[dict[str, str]]] | None:
        lower = message.lower()
        if any(token in lower for token in ["24/7", "24-7", "24 hours", "all day every day"]):
            return {
                str(day): [{"start": "00:00", "end": "24:00"}]
                for day in range(7)
            }

        days = self._schedule_days_from_message(lower)
        time_range = self._schedule_time_range_from_message(lower)
        if not days or not time_range:
            return None

        start, end = time_range
        blocks = {str(day): [] for day in range(7)}
        for day in days:
            blocks[str(day)].append({"start": start, "end": end})
        return blocks

    def _schedule_days_from_message(self, lower: str) -> list[int]:
        if any(phrase in lower for phrase in ["weekday", "week day", "workday", "work day"]):
            return list(range(5))
        if any(phrase in lower for phrase in ["weekend", "saturday and sunday", "sat and sun"]):
            return [5, 6]
        if any(phrase in lower for phrase in ["every day", "daily", "all week", "each day", "mon-sun", "monday to sunday"]):
            return list(range(7))

        range_match = re.search(
            rf"\b({SCHEDULE_DAY_PATTERN})\b"
            r"\s*(?:-|to|through|until|thru)\s*"
            rf"\b({SCHEDULE_DAY_PATTERN})\b",
            lower,
        )
        if range_match:
            start = self._schedule_day_index(range_match.group(1))
            end = self._schedule_day_index(range_match.group(2))
            if start is not None and end is not None:
                if start <= end:
                    return list(range(start, end + 1))
                return list(range(start, 7)) + list(range(0, end + 1))

        days: list[int] = []
        for token in re.findall(rf"\b({SCHEDULE_DAY_PATTERN})\b", lower):
            day = self._schedule_day_index(token)
            if day is not None and day not in days:
                days.append(day)
        return days

    def _schedule_day_index(self, value: str) -> int | None:
        normalized = value.lower()[:3]
        return SCHEDULE_DAY_ALIASES.get(value.lower(), SCHEDULE_DAY_ALIASES.get(normalized))

    def _schedule_time_range_from_message(self, lower: str) -> tuple[str, str] | None:
        match = re.search(
            r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:-|to|until|through|thru)\s*"
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
            lower,
        )
        if not match:
            return None

        start = self._schedule_minute_from_parts(match.group(1), match.group(2), match.group(3))
        end = self._schedule_minute_from_parts(match.group(4), match.group(5), match.group(6))
        if start is None or end is None:
            return None
        if end <= start and not match.group(3) and not match.group(6) and int(match.group(4)) <= 12:
            end += 12 * 60
        if start < 0 or end > 24 * 60 or end <= start:
            return None
        if start % 30 or end % 30:
            return None
        return self._format_schedule_minute(start), self._format_schedule_minute(end)

    def _schedule_minute_from_parts(self, hour_text: str, minute_text: str | None, meridiem: str | None) -> int | None:
        hour = int(hour_text)
        minute = int(minute_text or "0")
        if minute not in {0, 30}:
            return None
        if meridiem:
            if hour < 1 or hour > 12:
                return None
            if meridiem == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        if hour < 0 or hour > 24:
            return None
        return hour * 60 + minute

    def _format_schedule_minute(self, minute: int) -> str:
        if minute == 24 * 60:
            return "24:00"
        return f"{minute // 60:02d}:{minute % 60:02d}"

    def _chat_schedule_summary(self, time_blocks: dict[str, list[dict[str, str]]]) -> str:
        selected_slots = 0
        active_days = 0
        for intervals in time_blocks.values():
            day_slots = 0
            for interval in intervals:
                start = self._parse_schedule_summary_minute(str(interval["start"]))
                end = self._parse_schedule_summary_minute(str(interval["end"]))
                day_slots += max(0, (end - start) // 30)
            if day_slots:
                active_days += 1
                selected_slots += day_slots
        if not selected_slots:
            return "no allowed time"
        if selected_slots == 48 * 7:
            return "24/7"
        hours = selected_slots / 2
        display_hours = int(hours) if hours.is_integer() else round(hours, 1)
        return f"{display_hours}h across {active_days} day{'s' if active_days != 1 else ''}"

    def _parse_schedule_summary_minute(self, value: str) -> int:
        if value in {"24:00", "23:59"}:
            return 24 * 60
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    def _looks_like_device_state_request(self, lower: str) -> bool:
        if self._looks_like_device_open_request(lower):
            return False
        device_words = ["gate", "door", "garage", "cover"]
        state_words = ["state", "status", "open", "closed", "opening", "closing", "locked", "unlocked"]
        question_words = ["is the", "is my", "are the", "what is", "what's", "check the", "show me"]
        return any(word in lower for word in device_words) and (
            any(word in lower for word in state_words)
            or any(phrase in lower for phrase in question_words)
        )

    def _looks_like_device_open_request(self, lower: str) -> bool:
        if not re.search(r"\bopen\b", lower):
            return False
        return any(word in lower for word in ["gate", "garage", "door", "cover"])

    def _explicitly_confirmed_device_open(self, lower: str) -> bool:
        return bool(
            re.search(r"\b(confirm|confirmed|authorise|authorize|approved|yes)\b", lower)
            and re.search(r"\bopen\b", lower)
        )

    def _device_target_from_message(self, lower: str) -> str | None:
        patterns = [
            r"(?:confirm|confirmed|authorise|authorize|approved|yes,?\s*)?\s*open\s+(?:the\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+please|\.|\?|$)",
            r"(?:state|status)\s+(?:of|for)\s+(?:the\s+)?([a-z0-9 _.-]{2,80})",
            r"(?:is|are|check)\s+(?:the\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+(?:open|closed|opening|closing|locked|unlocked)|\?|$)",
            r"(?:what(?:'s| is))\s+(?:the\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+(?:state|status|doing)|\?|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower)
            if match:
                target = re.sub(
                    r"\b(open|closed|opening|closing|locked|unlocked|state|status|doing|please)\b",
                    "",
                    match.group(1),
                )
                return re.sub(r"\s+", " ", target).strip(" ?.") or None
        return None

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

    def _normalize_attachments(
        self,
        attachments: list[dict[str, Any]],
        *,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        if not user_id:
            return []
        normalized: list[dict[str, Any]] = []
        for attachment in attachments:
            file_id = str(attachment.get("id") or "").strip()
            if not file_id:
                continue
            try:
                stored = chat_attachment_store.get(file_id)
                chat_attachment_store.require_access(stored, user_id)
            except ChatAttachmentError:
                continue
            normalized.append(stored.to_public_dict())
        return normalized

    def _message_with_attachments(self, message: str, attachments: list[dict[str, Any]]) -> str:
        if not attachments:
            return message
        lines = [message.strip() or "Please inspect the attached file."]
        lines.append("\nAttachments:")
        for attachment in attachments:
            lines.append(
                "- "
                f"{attachment['filename']} "
                f"({attachment['kind']}, file_id={attachment['id']})"
            )
        return "\n".join(lines)

    def _attachments_from_tool_results(self, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for result in tool_results:
            output = result.get("output")
            if not isinstance(output, dict):
                continue
            candidates = []
            if isinstance(output.get("attachment"), dict):
                candidates.append(output["attachment"])
            if isinstance(output.get("attachments"), list):
                candidates.extend(item for item in output["attachments"] if isinstance(item, dict))
            for attachment in candidates:
                attachment_id = str(attachment.get("id") or "")
                if attachment_id and attachment_id not in seen:
                    seen.add(attachment_id)
                    attachments.append(attachment)
        return attachments


chat_service = ChatService()
