import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.ai.providers import (
    ChatMessageInput,
    LlmResult,
    ProviderNotConfiguredError,
    ToolCall,
    get_llm_provider,
)
from app.ai.tools import AgentTool, build_agent_tools, get_chat_tool_context, set_chat_tool_context
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import ChatMessage, ChatSession
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, TELEMETRY_CATEGORY_INTEGRATIONS, emit_audit_log

logger = get_logger(__name__)

MAX_AGENT_TOOL_ITERATIONS = 5
RELEVANT_HISTORY_SCAN_LIMIT = 24
RECENT_HISTORY_LIMIT = 6
MAX_RELEVANT_HISTORY_MESSAGES = 8

DEFAULT_AGENT_TOOL_NAMES = (
    "query_presence",
    "query_access_events",
    "query_anomalies",
    "query_schedules",
    "query_device_states",
)

EVENT_TOOL_NAMES = (
    "query_presence",
    "query_access_events",
    "diagnose_access_event",
    "query_lpr_timing",
    "query_vehicle_detection_history",
    "query_anomalies",
    "summarize_access_rhythm",
    "calculate_visit_duration",
    "trigger_anomaly_alert",
)
SCHEDULE_TOOL_NAMES = (
    "query_schedules",
    "get_schedule",
    "create_schedule",
    "update_schedule",
    "delete_schedule",
    "query_schedule_targets",
    "assign_schedule_to_entity",
    "verify_schedule_access",
)
NOTIFICATION_TOOL_NAMES = (
    "query_notification_catalog",
    "query_notification_workflows",
    "get_notification_workflow",
    "create_notification_workflow",
    "update_notification_workflow",
    "delete_notification_workflow",
    "preview_notification_workflow",
    "test_notification_workflow",
)
LEADERBOARD_TOOL_NAMES = ("query_leaderboard",)
DEVICE_TOOL_NAMES = ("query_device_states", "open_device")
MAINTENANCE_TOOL_NAMES = ("get_maintenance_status", "enable_maintenance_mode", "disable_maintenance_mode")
MALFUNCTION_TOOL_NAMES = (
    "get_active_malfunctions",
    "get_malfunction_history",
    "trigger_manual_malfunction_override",
)
CAMERA_TOOL_NAMES = ("analyze_camera_snapshot", "get_camera_snapshot")
FILE_TOOL_NAMES = (
    "read_chat_attachment",
    "export_presence_report_csv",
    "generate_contractor_invoice_pdf",
)
STATE_CHANGING_TOOL_NAMES = {
    "assign_schedule_to_entity",
    "create_notification_workflow",
    "create_schedule",
    "delete_notification_workflow",
    "delete_schedule",
    "disable_maintenance_mode",
    "enable_maintenance_mode",
    "open_device",
    "trigger_manual_malfunction_override",
    "test_notification_workflow",
    "trigger_anomaly_alert",
    "update_notification_workflow",
    "update_schedule",
}


SYSTEM_PROMPT = """You are the Intelligent Access Control System assistant.
Answer concisely and use tool results as the source of truth for presence,
events, anomalies, schedules, access rhythm, leaderboards, device states, DVLA
vehicle lookups, LPR/access diagnostics, gate malfunctions, and camera snapshot analysis. If the
user asks a follow-up with pronouns like they, he, she, or it, use the session
memory context. Never invent access events, people, or DVLA vehicle records
that are not present in tool results. When a DVLA vehicle lookup succeeds,
format the result as a short human-readable vehicle details summary rather than
raw JSON. When a tool result says a device open requires confirmation, do not
ask the user to type a confirmation phrase; tell them to use the on-screen
confirmation button. For any state-changing workflow tool that requires
confirmation, call the tool with the proposed arguments and confirm=false so the
UI can render a confirmation button; never merely claim a button exists. For
questions about why an access event was slow, why the gate did or did not open,
or why a notification did or did not send, use diagnose_access_event and include
the slowest telemetry spans or workflow conclusion in the answer. For questions
about how long plate recognition took, distinguish raw LPR captured-to-received
timing from total access-event pipeline duration when both are available. For
device opens, use the user's natural device name as target, for example "main
garage door"; never ask the user for internal integration names or entity IDs.
Do not mention Home Assistant, entity IDs, cover IDs, or internal integration
implementation details unless the user explicitly asks about integration
configuration. For gate malfunctions, explain that attempt counts follow the
fixed schedule T+5m, T+5m45s, T+10m45s, T+70m45s, and T+190m45s from the gate
open time. Maintenance Mode pauses automated attempts without clearing the due
timestamp. FUBAR means automated recovery has stopped until manual intervention
or the gate is physically resolved. For
schedule creation or edits, understand natural language day/time descriptions,
ask concise follow-up questions for missing name or allowed time blocks, and
only call schedule mutation tools once the required details are known. Schedule
tools accept either strict time_blocks JSON or a natural-language
time_description such as "Wednesdays and Fridays 6am to 7pm"; use
time_description when that is the most reliable representation. If a schedule
already exists, ask whether to update the existing schedule rather than giving
up. When explaining notification workflow options, use friendly labels and short
plain-language bullets; do not dump raw JSON, internal action IDs, or decorative
Markdown. Camera snapshots are ephemeral and are not retained by default. When
returning a camera snapshot, say that the image is attached; do not mention the
generated filename or chat file URL."""

AGENT_TOOL_PROTOCOL = """Agent tool protocol:
- You are a tool-using AI agent for IACS. Use tools whenever the user asks about live system state, records, schedules, devices, cameras, users, reports, file contents, or any state-changing operation.
- Do not invent IACS facts. If a tool can answer it, call the tool first.
- For provider-neutral tool calls, respond with exactly this marker and JSON object, with no prose:
IACS_TOOL_CALLS:
{"tool_calls":[{"id":"call_1","name":"tool_name","arguments":{}}]}
- You may request multiple independent tools in one tool_calls array.
- After tool results are provided, answer the user naturally and concisely. If diagnose_access_event is present, base causality answers on its recognition, gate, notifications, lpr_timing_observations, history, and trace fields before considering shallower access event summaries. Do not say latency or notification diagnostics are unavailable until diagnose_access_event and query_lpr_timing have been checked.
- Call another tool only if the result proves more tool work is required.
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
CHAT_FILE_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((/api/chat/files/[^)]+)\)")
CHAT_FILE_URL_PATTERN = re.compile(r"\s*/api/chat/files/[A-Za-z0-9_-]+\b")
DEFAULT_CHAT_TIMEZONE = "Europe/London"


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
        user_role: str | None = None,
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
            {
                "user_id": user_id,
                "user_role": user_role,
                "session_id": str(session_uuid),
                "provider": provider.name,
                "model": self._model_for_provider(runtime, provider.name),
                "trigger": "user_requested",
            }
        )
        try:
            tool_results: list[dict[str, Any]] = []
            if self._looks_like_schedule_delete_request(message.lower()):
                tool_result = await self._execute_tool_call(
                    session_uuid,
                    self._planned_schedule_delete_call(message),
                    status_callback=status_callback,
                )
                return await self._direct_response(
                    session_uuid,
                    self._schedule_delete_direct_text(tool_result.get("output", {})),
                    tool_results=[tool_result],
                    provider="guided",
                )

            if self._looks_like_device_open_request(message.lower()):
                tool_result = await self._execute_tool_call(
                    session_uuid,
                    self._planned_device_open_call(message),
                    status_callback=status_callback,
                )
                return await self._direct_response(
                    session_uuid,
                    self._device_open_direct_text(tool_result.get("output", {})),
                    tool_results=[tool_result],
                    provider="guided",
                )

            if self._looks_like_camera_snapshot_request(message.lower()):
                tool_result = await self._execute_tool_call(
                    session_uuid,
                    self._planned_camera_snapshot_call(message),
                    status_callback=status_callback,
                )
                return await self._direct_response(
                    session_uuid,
                    self._camera_snapshot_direct_text(tool_result.get("output", {})),
                    tool_results=[tool_result],
                    provider="guided",
                )

            if self._looks_like_access_event_time_request(message.lower()):
                tool_result = await self._execute_tool_call(
                    session_uuid,
                    self._planned_access_event_time_call(message, memory),
                    status_callback=status_callback,
                )
                return await self._direct_response(
                    session_uuid,
                    self._access_event_time_direct_text(message, tool_result.get("output", {})),
                    tool_results=[tool_result],
                    provider="guided",
                )

            if not use_local_router:
                preplanned_calls = self._preplanned_context_calls(message, memory, attachment_refs)
                tool_results = [
                    await self._execute_tool_call(session_uuid, call, status_callback=status_callback)
                    for call in preplanned_calls
                ]

            if use_local_router:
                planned_calls = self._plan_tool_calls(message, memory, attachment_refs)
                tool_results = [
                    await self._execute_tool_call(session_uuid, call, status_callback=status_callback)
                    for call in planned_calls
                ]

            selected_tools = self._select_tools_for_request(
                message,
                memory,
                attachment_refs,
                tool_results,
            )
            messages = await self._build_messages(session_uuid, tool_results, selected_tools)

            try:
                result = await self._run_provider_agent_loop(
                    provider,
                    session_uuid,
                    messages,
                    tool_results,
                    selected_tools,
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

        response_attachments = self._attachments_from_tool_results(tool_results)
        raw_text = result.text or self._fallback_text(tool_results)
        if self._should_replace_with_diagnostic_answer(message, raw_text, tool_results):
            raw_text = self._access_diagnostic_direct_text(self._diagnostic_output(tool_results) or {})
        text = self._clean_assistant_text(raw_text, response_attachments)
        await self._append_message(session_uuid, "assistant", text)
        await self._update_memory(session_uuid, message, tool_results)
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

    async def handle_tool_confirmation(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        status_callback: StatusCallback | None = None,
    ) -> ChatTurnResult:
        session_uuid = await self._ensure_session(session_id)
        context_token = set_chat_tool_context(
            {
                "user_id": user_id,
                "user_role": user_role,
                "session_id": str(session_uuid),
                "provider": "tool_confirmation",
                "model": None,
                "trigger": "user_confirmed",
            }
        )
        try:
            call = ToolCall(
                id=f"confirmed-{tool_name}-{uuid.uuid4().hex[:8]}",
                name=tool_name,
                arguments=arguments,
            )
            await self._append_message(
                session_uuid,
                "user",
                self._confirmation_user_message(tool_name, arguments),
            )
            tool_result = await self._execute_tool_call(
                session_uuid,
                call,
                status_callback=status_callback,
            )
        finally:
            set_chat_tool_context({}, token=context_token)

        attachments = self._attachments_from_tool_results([tool_result])
        text = self._clean_assistant_text(
            self._confirmation_result_text(tool_name, tool_result.get("output", {})),
            attachments,
        )
        await self._append_message(session_uuid, "assistant", text)
        await self._update_memory(session_uuid, text, [tool_result])
        await event_bus.publish(
            "chat.message",
            {
                "session_id": str(session_uuid),
                "provider": "tool_confirmation",
                "text": text,
                "attachments": attachments,
            },
        )
        return ChatTurnResult(
            str(session_uuid),
            "tool_confirmation",
            text,
            [tool_result],
            attachments,
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        return [tool.as_llm_tool() for tool in self._tools.values()]

    async def _run_provider_agent_loop(
        self,
        provider: Any,
        session_id: uuid.UUID,
        messages: list[ChatMessageInput],
        tool_results: list[dict[str, Any]],
        selected_tools: list[AgentTool],
        memory: dict[str, Any],
        *,
        status_callback: StatusCallback | None,
    ) -> LlmResult | ChatTurnResult:
        tool_schemas = [tool.as_llm_tool() for tool in selected_tools]
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
                    text=self._fallback_text(tool_results),
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
            messages = await self._build_messages(session_id, tool_results, selected_tools)

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
        attachments = self._attachments_from_tool_results(tool_results)
        text = self._clean_assistant_text(text, attachments)
        await self._append_message(session_id, "assistant", text)
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

    def _model_for_provider(self, runtime: Any, provider_name: str) -> str | None:
        return {
            "openai": getattr(runtime, "openai_model", None),
            "gemini": getattr(runtime, "gemini_model", None),
            "claude": getattr(runtime, "anthropic_model", None),
            "anthropic": getattr(runtime, "anthropic_model", None),
            "ollama": getattr(runtime, "ollama_model", None),
            "local": "local",
        }.get(provider_name)

    def _clean_assistant_text(self, text: str, attachments: list[dict[str, Any]]) -> str:
        file_link_replacement = ""
        if attachments:
            file_link_replacement = (
                "the snapshot"
                if any(attachment.get("kind") == "image" for attachment in attachments)
                else "the attached file"
            )
        else:
            file_link_replacement = r"\1"
        cleaned = CHAT_FILE_LINK_PATTERN.sub(file_link_replacement, text)
        cleaned = CHAT_FILE_URL_PATTERN.sub("", cleaned)
        for attachment in attachments:
            filename = str(attachment.get("filename") or "")
            if filename and attachment.get("source") == "system_media":
                cleaned = cleaned.replace(filename, "the snapshot")
        cleaned = re.sub(r"\bHome Assistant cover entity ID\b", "device name", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bHome Assistant entity ID\b", "device name", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bHome Assistant\b", "the system", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bcover entity ID\b", "device name", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bentity ID\b", "device name", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        cleaned = cleaned.replace("***", "")
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if not cleaned and any(attachment.get("kind") == "image" for attachment in attachments):
            return "Here's the latest snapshot."
        return cleaned

    def _confirmation_user_message(self, tool_name: str, arguments: dict[str, Any]) -> str:
        target = (
            arguments.get("target")
            or arguments.get("schedule_name")
            or arguments.get("rule_name")
            or arguments.get("name")
            or tool_name.replace("_", " ")
        )
        return f"Confirmed {tool_name.replace('_', ' ')} for {target}."

    def _confirmation_result_text(self, tool_name: str, output: dict[str, Any]) -> str:
        if output.get("error"):
            return str(output.get("detail") or output.get("error") or "I could not complete that action.")
        if tool_name == "open_device":
            device = output.get("device") if isinstance(output.get("device"), dict) else {}
            name = device.get("name") or output.get("target") or "the device"
            return f"Opened {name}. This was logged as an Alfred action." if output.get("opened") else f"I could not open {name}."
        if tool_name == "update_schedule":
            schedule = output.get("schedule") if isinstance(output.get("schedule"), dict) else {}
            name = schedule.get("name") or output.get("schedule_name") or "the schedule"
            summary = schedule.get("summary")
            return f"Updated {name}{f' to {summary}' if summary else ''}."
        if tool_name == "delete_schedule":
            schedule = output.get("schedule") if isinstance(output.get("schedule"), dict) else {}
            name = schedule.get("name") or output.get("schedule_name") or "the schedule"
            return f"Deleted {name}." if output.get("deleted") else str(output.get("detail") or f"I did not delete {name}.")
        if tool_name == "create_notification_workflow":
            workflow = output.get("workflow") if isinstance(output.get("workflow"), dict) else {}
            return f"Created notification workflow {workflow.get('name') or output.get('workflow_name') or ''}.".strip()
        if tool_name == "update_notification_workflow":
            workflow = output.get("workflow") if isinstance(output.get("workflow"), dict) else {}
            return f"Updated notification workflow {workflow.get('name') or output.get('workflow_name') or ''}.".strip()
        if tool_name == "delete_notification_workflow":
            workflow = output.get("workflow") if isinstance(output.get("workflow"), dict) else {}
            return f"Deleted notification workflow {workflow.get('name') or output.get('workflow_name') or ''}.".strip()
        if tool_name == "test_notification_workflow":
            if output.get("sent"):
                return "Sent the notification workflow test."
            return str(output.get("detail") or "I did not send the notification workflow test.")
        return str(output.get("detail") or "Action completed.")

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
        selected_tools: list[AgentTool],
    ) -> list[ChatMessageInput]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .where(ChatMessage.role.in_(("user", "assistant")))
                    .order_by(ChatMessage.created_at.desc())
                    .limit(RELEVANT_HISTORY_SCAN_LIMIT)
                )
            ).all()
            chat_session = await session.get(ChatSession, session_id)
            memory = chat_session.context if chat_session and chat_session.context else {}

        runtime = await get_runtime_config()
        site_timezone = runtime.site_timezone or DEFAULT_CHAT_TIMEZONE
        history_rows = self._select_relevant_history(list(reversed(rows)), memory)
        tool_protocol = AGENT_TOOL_PROTOCOL.replace(
            "{tool_catalog}",
            json.dumps(
                [tool.as_llm_tool() for tool in selected_tools],
                separators=(",", ":"),
            ),
        )
        messages = [
            ChatMessageInput(
                "system",
                (
                    f"{SYSTEM_PROMPT}\nSite timezone: {site_timezone}. "
                    "All user-facing dates and times must use this timezone; "
                    "do not present UTC timestamps unless the user explicitly asks for UTC.\n"
                    f"Session memory: {json.dumps(memory, default=str)}\n\n{tool_protocol}"
                ),
            )
        ]
        for row in history_rows:
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

    def _select_relevant_history(
        self,
        rows: list[ChatMessage],
        memory: dict[str, Any],
    ) -> list[ChatMessage]:
        if len(rows) <= MAX_RELEVANT_HISTORY_MESSAGES:
            return rows

        latest_user = next((row for row in reversed(rows) if row.role == "user"), None)
        latest_content = latest_user.content if latest_user else ""
        if memory.get("pending_schedule_create"):
            return rows[-MAX_RELEVANT_HISTORY_MESSAGES:]

        relevant_terms = self._history_terms(latest_content)
        for key in ("last_subject", "last_person", "last_group"):
            relevant_terms.update(self._history_terms(str(memory.get(key) or "")))

        selected_ids = {id(row) for row in rows[-RECENT_HISTORY_LIMIT:]}
        if relevant_terms:
            for row in rows[:-RECENT_HISTORY_LIMIT]:
                if row.role not in {"user", "assistant"}:
                    continue
                if relevant_terms.intersection(self._history_terms(row.content)):
                    selected_ids.add(id(row))

        selected = [row for row in rows if id(row) in selected_ids]
        if len(selected) > MAX_RELEVANT_HISTORY_MESSAGES:
            protected_ids = {id(row) for row in rows[-RECENT_HISTORY_LIMIT:]}
            older = [row for row in selected if id(row) not in protected_ids]
            recent = [row for row in selected if id(row) in protected_ids]
            selected = older[-(MAX_RELEVANT_HISTORY_MESSAGES - len(recent)):] + recent
        return selected

    def _history_terms(self, text: str) -> set[str]:
        stop_words = {
            "about",
            "after",
            "again",
            "already",
            "before",
            "could",
            "should",
            "their",
            "there",
            "these",
            "those",
            "would",
        }
        return {
            token
            for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower())
            if len(token) >= 4 and token not in stop_words
        }

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
            event = output.get("event") if isinstance(output.get("event"), dict) else {}
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

    def _select_tools_for_request(
        self,
        message: str,
        memory: dict[str, Any],
        attachments: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
    ) -> list[AgentTool]:
        lower = message.lower()
        names: set[str] = set()
        leaderboard_request = self._looks_like_leaderboard_request(lower)

        if attachments:
            names.add("read_chat_attachment")

        if self._looks_like_device_open_request(lower):
            names.update(DEVICE_TOOL_NAMES)
        elif self._looks_like_device_state_request(lower):
            names.add("query_device_states")

        if any(word in lower for word in ["gate", "garage", "door", "cover", "device"]):
            names.add("query_device_states")

        if any(word in lower for word in ["malfunction", "fubar", "stuck", "recovery", "retry", "attempt"]):
            names.update(MALFUNCTION_TOOL_NAMES)

        if "what is the gate doing" in lower or "gate doing right now" in lower:
            names.update(("query_device_states", "get_active_malfunctions"))

        if any(word in lower for word in ["maintenance", "kill-switch", "kill switch", "disable automation", "resume automation"]):
            names.update(MAINTENANCE_TOOL_NAMES)

        if not leaderboard_request and any(word in lower for word in ["present", "presence", "onsite", "on site", "here", "who is", "who's"]):
            names.add("query_presence")

        if any(word in lower for word in ["arrive", "arrival", "arrived", "came", "leave", "left", "exit", "exited", "event", "denied", "access log"]):
            names.update(("query_access_events", "query_anomalies"))

        if self._looks_like_access_diagnostic_request(lower):
            names.update(("diagnose_access_event", "query_access_events", "query_lpr_timing"))

        if self._looks_like_vehicle_detection_count_request(lower):
            names.update(("query_vehicle_detection_history", "query_access_events", "query_leaderboard"))

        if any(phrase in lower for phrase in ["how long", "duration", "stay", "stayed"]):
            names.update(("calculate_visit_duration", "query_access_events"))

        if any(word in lower for word in ["anomaly", "anomalies", "unauthorized", "unauthorised", "alert"]):
            names.update(("query_anomalies", "trigger_anomaly_alert"))

        if any(word in lower for word in ["summary", "summarize", "summarise", "rhythm", "report"]):
            names.update(("summarize_access_rhythm", "query_access_events"))

        if leaderboard_request:
            names.update(LEADERBOARD_TOOL_NAMES)

        if any(word in lower for word in ["schedule", "schedules", "timeframe", "allowed", "access window"]):
            names.update(SCHEDULE_TOOL_NAMES)

        if memory.get("pending_schedule_create"):
            names.update(SCHEDULE_TOOL_NAMES)

        if any(word in lower for word in ["notification", "notifications", "workflow", "workflows", "template", "apprise"]):
            names.update(NOTIFICATION_TOOL_NAMES)

        if any(word in lower for word in ["vehicle", "registration", "reg", "plate", "dvla", "mot", "tax"]):
            names.update(("lookup_dvla_vehicle", "query_access_events"))

        if any(word in lower for word in ["camera", "snapshot", "image", "photo", "picture", "visible", "see"]):
            names.update(CAMERA_TOOL_NAMES)

        if any(word in lower for word in ["file", "attachment", "download", "csv", "pdf", "export", "invoice"]):
            names.update(FILE_TOOL_NAMES)

        if any(word in lower for word in ["user", "users", "account", "accounts", "admin"]):
            names.add("get_system_users")

        for result in tool_results:
            name = str(result.get("name") or "")
            if name:
                names.add(name)
            output = result.get("output")
            if isinstance(output, dict) and output.get("requires_confirmation"):
                if name == "open_device":
                    names.update(DEVICE_TOOL_NAMES)
                elif name == "trigger_manual_malfunction_override":
                    names.update(MALFUNCTION_TOOL_NAMES)
                elif name in MAINTENANCE_TOOL_NAMES:
                    names.update(MAINTENANCE_TOOL_NAMES)
                elif name in SCHEDULE_TOOL_NAMES:
                    names.update(SCHEDULE_TOOL_NAMES)
                elif name in NOTIFICATION_TOOL_NAMES:
                    names.update(NOTIFICATION_TOOL_NAMES)

        if not names:
            names.update(DEFAULT_AGENT_TOOL_NAMES)

        return [tool for name, tool in self._tools.items() if name in names]

    def _plan_tool_calls(
        self,
        message: str,
        memory: dict[str, Any],
        attachments: list[dict[str, Any]],
    ) -> list[ToolCall]:
        lower = message.lower()
        subject = self._subject_from_message(lower, memory)
        leaderboard_request = self._looks_like_leaderboard_request(lower)
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

        if any(word in lower for word in ["maintenance", "kill-switch", "kill switch", "disable automation", "resume automation"]):
            if any(word in lower for word in ["enable", "turn on", "activate", "start", "disable automation"]):
                calls.append(
                    ToolCall(
                        "planned-enable-maintenance",
                        "enable_maintenance_mode",
                        {"reason": message, "confirm": self._is_confirmation_message(lower)},
                    )
                )
            elif any(word in lower for word in ["disable", "turn off", "deactivate", "stop", "resume automation"]):
                calls.append(
                    ToolCall(
                        "planned-disable-maintenance",
                        "disable_maintenance_mode",
                        {"confirm": self._is_confirmation_message(lower)},
                    )
                )
            else:
                calls.append(ToolCall("planned-maintenance-status", "get_maintenance_status", {}))

        if any(word in lower for word in ["malfunction", "fubar", "stuck", "recovery", "retry", "attempt"]) or "gate doing right now" in lower:
            calls.append(
                ToolCall(
                    "planned-active-malfunctions",
                    "get_active_malfunctions",
                    {"include_timeline": any(word in lower for word in ["timeline", "history", "trace", "why"])},
                )
            )

        if self._looks_like_access_diagnostic_request(lower):
            diagnostic_args = self._access_diagnostic_args_from_message(message, memory)
            calls.append(ToolCall("planned-access-diagnostics", "diagnose_access_event", diagnostic_args))
            if self._looks_like_lpr_timing_request(lower):
                lpr_args = {
                    key: value
                    for key, value in {
                        "registration_number": diagnostic_args.get("registration_number"),
                        "limit": 50,
                    }.items()
                    if value
                }
                calls.append(ToolCall("planned-lpr-timing", "query_lpr_timing", lpr_args))

        if self._looks_like_vehicle_detection_count_request(lower):
            args: dict[str, Any] = {
                "period": "all",
                "limit": 10,
            }
            registration_number = self._registration_from_message(message)
            if registration_number:
                args["registration_number"] = registration_number
            else:
                args["latest_unknown"] = self._refers_to_latest_unknown_vehicle(lower)
            calls.append(ToolCall("planned-detection-history", "query_vehicle_detection_history", args))

        if self._looks_like_schedule_delete_request(lower):
            calls.append(self._planned_schedule_delete_call(message))

        if not leaderboard_request and any(word in lower for word in ["present", "here", "onsite", "on site", "who is"]):
            calls.append(ToolCall("planned-query-presence", "query_presence", self._subject_args(subject)))

        if any(word in lower for word in ["arrive", "arrival", "arrived", "came", "left", "leave", "exit", "exited", "event", "denied"]):
            args = self._subject_args(subject)
            person_name = self._person_name_from_event_time_message(lower)
            if person_name:
                args["person"] = person_name
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

        if leaderboard_request:
            calls.append(self._planned_leaderboard_call(message))

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
            word in lower for word in ["attach", "fetch", "get", "send", "show", "latest"]
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

    def _preplanned_context_calls(
        self,
        message: str,
        memory: dict[str, Any],
        _attachments: list[dict[str, Any]],
    ) -> list[ToolCall]:
        """Run safe read-only context tools before hosted providers answer.

        Native function calling is still available, but questions that are
        clearly about access-event causality need the deep diagnostic record
        loaded deterministically. This avoids a provider taking the shallow
        access-log path and claiming latency or notification data is missing.
        """

        lower = message.lower()
        calls: list[ToolCall] = []
        if self._looks_like_access_diagnostic_request(lower):
            diagnostic_args = self._access_diagnostic_args_from_message(message, memory)
            calls.append(ToolCall("preplanned-access-diagnostics", "diagnose_access_event", diagnostic_args))
            if self._looks_like_lpr_timing_request(lower):
                lpr_args = {
                    key: value
                    for key, value in {
                        "registration_number": diagnostic_args.get("registration_number"),
                        "limit": 50,
                    }.items()
                    if value
                }
                calls.append(ToolCall("preplanned-lpr-timing", "query_lpr_timing", lpr_args))

        if self._looks_like_vehicle_detection_count_request(lower):
            args: dict[str, Any] = {"period": "all", "limit": 10}
            registration_number = self._registration_from_message(message)
            if registration_number:
                args["registration_number"] = registration_number
            else:
                args["latest_unknown"] = self._refers_to_latest_unknown_vehicle(lower)
            calls.append(ToolCall("preplanned-detection-history", "query_vehicle_detection_history", args))

        return calls

    def _planned_device_open_call(self, message: str) -> ToolCall:
        lower = message.lower()
        target = self._device_target_from_message(lower) or ""
        return ToolCall(
            "planned-open-device",
            "open_device",
            {
                "target": target,
                "kind": "all",
                "reason": message,
                "confirm": self._explicitly_confirmed_device_open(lower),
            },
        )

    def _planned_schedule_delete_call(self, message: str) -> ToolCall:
        return ToolCall(
            "planned-delete-schedule",
            "delete_schedule",
            {
                "schedule_name": self._schedule_delete_name_from_message(message) or self._schedule_name_from_message(message) or "",
                "confirm": False,
            },
        )

    def _planned_camera_snapshot_call(self, message: str) -> ToolCall:
        return ToolCall(
            "planned-camera-snapshot",
            "get_camera_snapshot",
            {"camera_name": self._camera_name_from_message(message) or ""},
        )

    def _planned_access_event_time_call(self, message: str, memory: dict[str, Any]) -> ToolCall:
        lower = message.lower()
        args: dict[str, Any] = {"limit": 50, "day": self._day_from_message(lower)}
        person_name = self._person_name_from_event_time_message(lower)
        if person_name:
            args["person"] = person_name
        elif memory.get("last_person"):
            args["person"] = memory["last_person"]
        subject = self._subject_from_message(lower, memory)
        args.update(self._subject_args(subject))
        return ToolCall("planned-access-event-time", "query_access_events", args)

    def _planned_leaderboard_call(self, message: str) -> ToolCall:
        lower = message.lower()
        scope = "all"
        if any(phrase in lower for phrase in ["mystery", "unknown", "stranger", "denied"]):
            scope = "unknown"
        elif any(phrase in lower for phrase in ["vip", "known", "family", "leader", "winner", "winning", "top spot", "number one", "#1"]):
            scope = "top_known" if any(phrase in lower for phrase in ["leader", "winner", "winning", "top spot", "number one", "#1"]) else "known"

        args: dict[str, Any] = {
            "scope": scope,
            "limit": self._leaderboard_limit_from_message(lower),
            "enrich_unknowns": scope in {"all", "unknown"},
        }
        registration_number = self._registration_from_message(message)
        if registration_number:
            args["registration_number"] = registration_number
        return ToolCall("planned-query-leaderboard", "query_leaderboard", args)

    def _device_open_direct_text(self, output: dict[str, Any]) -> str:
        device = output.get("device") if isinstance(output.get("device"), dict) else {}
        name = str(device.get("name") or output.get("target") or "that device").strip()
        if output.get("requires_details"):
            return str(output.get("detail") or "Which gate or garage door should I open?")
        if output.get("requires_confirmation"):
            return f"Please confirm before I open {name}."
        if output.get("opened"):
            return f"Opened {name}. This was logged as an Alfred action."
        return str(output.get("detail") or output.get("error") or f"I could not open {name}.")

    def _schedule_delete_direct_text(self, output: dict[str, Any]) -> str:
        schedule = output.get("schedule") if isinstance(output.get("schedule"), dict) else {}
        name = str(schedule.get("name") or output.get("schedule_name") or "that schedule").strip()
        if output.get("requires_confirmation"):
            return str(output.get("detail") or f"Delete the {name} schedule? Use the confirmation button to continue.")
        if output.get("deleted"):
            return f"Deleted {name}."
        if output.get("dependencies"):
            return f"I cannot delete {name} because it is still assigned. Remove its assignments first, then try again."
        return str(output.get("detail") or output.get("error") or f"I could not delete {name}.")

    def _camera_snapshot_direct_text(self, output: dict[str, Any]) -> str:
        if output.get("fetched"):
            return "Here's the latest snapshot."
        camera = output.get("camera") or "that camera"
        detail = str(output.get("error") or "I could not fetch the snapshot.")
        return f"I couldn't fetch {camera}: {detail}"

    def _access_event_time_direct_text(self, message: str, output: dict[str, Any]) -> str:
        events = output.get("events") if isinstance(output.get("events"), list) else []
        lower = message.lower()
        direction = "exit" if any(word in lower for word in ["leave", "left", "exit", "exited"]) else "entry"
        matching = [event for event in events if event.get("direction") == direction]
        event = matching[0] if matching else (events[0] if events else None)
        if not event:
            person = self._person_name_from_event_time_message(lower)
            subject = f" for {person.title()}" if person else ""
            action = "leave" if direction == "exit" else "arrive"
            return f"I couldn't find a recent {action} event{subject}."
        person_name = event.get("person") or event.get("registration_number") or "They"
        verb = "left" if event.get("direction") == "exit" else "arrived"
        occurred_at = self._chat_time_from_iso(str(event.get("occurred_at") or ""))
        return f"{person_name} {verb} at {occurred_at}." if occurred_at else f"{person_name} {verb} recently."

    def _chat_time_from_iso(self, value: str) -> str | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(ZoneInfo(DEFAULT_CHAT_TIMEZONE)).strftime("%H:%M")

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
            if re.fullmatch(r"\d+(?:MS|S|SEC|SECS|SECOND|SECONDS|MILLISECOND|MILLISECONDS)", candidate):
                continue
            if 2 <= len(candidate) <= 8 and any(char.isalpha() for char in candidate) and any(char.isdigit() for char in candidate):
                return candidate
        return None

    def _camera_name_from_message(self, message: str) -> str | None:
        patterns = (
            r"(?:show|get|fetch|send)\s+(?:me\s+)?(?:the\s+)?([A-Za-z0-9 _.-]{2,80}?\s+camera)\b",
            r"(?:camera|snapshot|image|photo|picture)\s+(?:called|named|from|of)?\s*([A-Za-z0-9 _.-]{2,80})",
            r"(?:latest\s+)?(?:snapshot|image|photo|picture)\s+(?:from|of)\s+(?:the\s+)?([A-Za-z0-9 _.-]{2,80})",
            r"(?:show|get|fetch|send)\s+(?:me\s+)?(?:the\s+)?([A-Za-z0-9 _.-]{2,80})",
        )
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if not match:
                continue
            camera_name = self._clean_camera_name(match.group(1))
            if camera_name:
                return camera_name
        return None

    def _clean_camera_name(self, value: str) -> str | None:
        cleaned = value.strip(" .")
        cleaned = re.sub(r"\b(?:please|thanks|thank you)\b.*$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        cleaned = re.sub(r"^(?:latest|current|live)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .")
        cleaned = re.sub(r"^(?:snapshot|image|photo|picture)\s+(?:from|of)\s+", "", cleaned, flags=re.IGNORECASE).strip(" .")
        cleaned = re.sub(r"\s+(?:camera|cam|snapshot|image|photo|picture)$", "", cleaned, flags=re.IGNORECASE).strip(" .")
        if not cleaned:
            return None
        if self._is_non_camera_show_target(cleaned.lower()):
            return None
        return cleaned

    def _is_non_camera_show_target(self, lower: str) -> bool:
        blocked_terms = {
            "schedule",
            "schedules",
            "notification",
            "notifications",
            "workflow",
            "workflows",
            "presence",
            "people",
            "person",
            "users",
            "events",
            "logs",
            "report",
            "reports",
            "settings",
        }
        return any(term in lower.split() for term in blocked_terms)

    def _day_from_message(self, lower: str) -> str:
        if "yesterday" in lower:
            return "yesterday"
        if "today" in lower:
            return "today"
        return "recent"

    def _person_name_from_event_time_message(self, lower: str) -> str | None:
        patterns = [
            r"(?:what time did|when did|did|has|have)\s+([a-z][a-z .'-]{1,40}?)\s+(?:leave|left|exit|exited|arrive|arrived|come|came)\b",
            r"\b([a-z][a-z .'-]{1,40}?)\s+(?:left|exited|arrived|came)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower)
            if not match:
                continue
            name = re.sub(r"\b(the|a|an|person|user|resident|visitor|contractor)\b", "", match.group(1))
            name = re.sub(r"\s+", " ", name).strip(" ?.")
            if name:
                return name
        return None

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

    def _looks_like_leaderboard_request(self, lower: str) -> bool:
        terms = [
            "leaderboard",
            "leader board",
            "top charts",
            "top chart",
            "vip lounge",
            "mystery guests",
            "mystery guest",
            "read count",
            "detectiion",
            "detectiions",
            "detection",
            "detections",
            "most detected",
            "most detections",
            "most reads",
            "top spot",
            "number one",
            "#1",
            "winner",
            "overtake",
            "overtaken",
        ]
        if any(term in lower for term in terms):
            return True
        return bool(
            re.search(r"\b(?:who|what|which)\b.*\b(?:leading|lead|leader|top)\b", lower)
            and re.search(r"\b(?:plate|plates|car|vehicle|vehicles|vip|known|unknown)\b", lower)
        )

    def _leaderboard_limit_from_message(self, lower: str) -> int:
        match = re.search(r"\btop\s+(\d{1,3})\b", lower)
        if match:
            return max(1, min(int(match.group(1)), 100))
        return 25

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
        self._audit_agent_tool_call(call, output)
        return {"call_id": call.id, "name": call.name, "arguments": call.arguments, "output": output}

    def _audit_agent_tool_call(self, call: ToolCall, output: dict[str, Any]) -> None:
        context = get_chat_tool_context()
        failed = bool(output.get("error")) or output.get("accepted") is False or (
            output.get("opened") is False and not output.get("requires_confirmation")
        )
        requires_confirmation = bool(output.get("requires_confirmation"))
        state_changing = call.name in STATE_CHANGING_TOOL_NAMES
        emit_audit_log(
            category=TELEMETRY_CATEGORY_ALFRED,
            action=f"alfred.tool.{call.name}",
            actor="Alfred_AI",
            actor_user_id=context.get("user_id"),
            target_entity="AgentTool",
            target_id=call.name,
            target_label=call.name.replace("_", " ").title(),
            outcome="pending_confirmation" if requires_confirmation else "failed" if failed else "success",
            level="purple" if not failed else "error",
            metadata={
                "trigger": context.get("trigger") or "user_requested",
                "provider": context.get("provider"),
                "model": context.get("model"),
                "session_id": context.get("session_id"),
                "tool": call.name,
                "tool_call_id": call.id,
                "state_changing": state_changing,
                "arguments": call.arguments,
                "requires_confirmation": requires_confirmation,
                "outcome": output,
            },
        )
        if call.name == "lookup_dvla_vehicle":
            registration_number = str(output.get("registration_number") or call.arguments.get("registration_number") or "").strip()
            emit_audit_log(
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="dvla.lookup",
                actor="Alfred_AI",
                actor_user_id=context.get("user_id"),
                target_entity="DVLA",
                target_id=registration_number or None,
                target_label=str(output.get("display_vehicle") or registration_number or "DVLA lookup"),
                outcome="failed" if failed else "success",
                level="error" if failed else "info",
                metadata={
                    "source": "alfred",
                    "trigger": context.get("trigger") or "user_requested",
                    "provider": context.get("provider"),
                    "model": context.get("model"),
                    "session_id": context.get("session_id"),
                    "tool": call.name,
                    "tool_call_id": call.id,
                    "registration_number": registration_number,
                    "display_vehicle": output.get("display_vehicle"),
                    "error": output.get("error"),
                },
            )

    def _tool_status(self, tool_name: str) -> dict[str, Any]:
        labels = {
            "query_presence": "Checking presence logs...",
            "query_device_states": "Checking device states...",
            "open_device": "Preparing device open command...",
            "get_maintenance_status": "Checking Maintenance Mode...",
            "enable_maintenance_mode": "Preparing Maintenance Mode...",
            "disable_maintenance_mode": "Preparing Maintenance Mode...",
            "get_active_malfunctions": "Checking gate malfunction state...",
            "get_malfunction_history": "Reviewing gate malfunction history...",
            "trigger_manual_malfunction_override": "Preparing gate malfunction override...",
            "query_access_events": "Reviewing access events...",
            "diagnose_access_event": "Diagnosing access event...",
            "query_lpr_timing": "Checking LPR timing...",
            "query_vehicle_detection_history": "Counting vehicle detections...",
            "query_anomalies": "Checking anomaly records...",
            "summarize_access_rhythm": "Summarizing site rhythm...",
            "calculate_visit_duration": "Calculating visit duration...",
            "query_leaderboard": "Checking Top Charts...",
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
            "query_notification_catalog": "Checking notification options...",
            "query_notification_workflows": "Checking notification workflows...",
            "get_notification_workflow": "Checking notification workflow...",
            "create_notification_workflow": "Preparing notification workflow...",
            "update_notification_workflow": "Preparing notification workflow update...",
            "delete_notification_workflow": "Preparing notification workflow deletion...",
            "preview_notification_workflow": "Previewing notification workflow...",
            "test_notification_workflow": "Preparing notification test...",
        }
        return {"tool": tool_name, "label": labels.get(tool_name, "Running system tool...")}

    def _looks_like_schedule_create_request(self, lower: str) -> bool:
        return "schedule" in lower and any(word in lower for word in ["create", "new", "add", "make"])

    def _looks_like_schedule_delete_request(self, lower: str) -> bool:
        return "schedule" in lower and bool(re.search(r"\b(delete|remove)\b", lower))

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

    def _schedule_delete_name_from_message(self, message: str) -> str | None:
        patterns = (
            r"(?:delete|remove)\s+(?:the\s+)?schedule\s+(?:called|named)\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
            r"(?:delete|remove)\s+(?:the\s+)?schedule\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?",
            r"(?:delete|remove)\s+['\"]?([A-Za-z0-9 _.-]{2,80})['\"]?\s+schedule\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if not match:
                continue
            name = self._clean_schedule_name(match.group(1))
            name = re.sub(r"^(?:called|named)\s+", "", name, flags=re.IGNORECASE).strip()
            if name:
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
        device_words = ["gate", "door", "garage", "cover"]
        if not any(word in lower for word in device_words):
            return False
        return bool(
            re.search(r"\b(?:is|are|was|were)\b[^?]*\b(?:open|closed|opening|closing|locked|unlocked)\b", lower)
            or re.search(r"\b(?:state|status)\b", lower)
            or re.search(r"\b(?:what(?:'s| is)|check)\b", lower)
        )

    def _looks_like_device_open_request(self, lower: str) -> bool:
        if not any(word in lower for word in ["gate", "garage", "door", "cover"]):
            return False
        if re.search(r"\b(?:is|are|was|were)\b[^?]*\bopen\b", lower):
            return False
        return bool(
            re.search(r"^\s*(?:please\s+)?(?:confirm(?:ed)?\s+)?open\s+(?:the\s+|my\s+)?", lower)
            or re.search(r"\b(?:can|could|would)\s+you\s+open\s+(?:the\s+|my\s+)?", lower)
        )

    def _looks_like_access_diagnostic_request(self, lower: str) -> bool:
        diagnostic_terms = [
            "why",
            "why didn't",
            "why didnt",
            "did not",
            "didn't",
            "didnt",
            "slow",
            "slower",
            "longer",
            "latency",
            "timing",
            "took",
            "recognise",
            "recognize",
            "debug",
            "diagnose",
            "diagnostic",
            "notification",
            "notify",
            "notified",
            "alert",
            "failed",
            "failure",
            "problem",
            "issue",
            "reason",
            "cause",
            "explain",
        ]
        access_terms = [
            "lpr",
            "number plate",
            "numberplate",
            "plate",
            "scan",
            "read",
            "recognition",
            "process",
            "processing",
            "detection",
            "detected",
            "arrival",
            "arrivals",
            "entry",
            "entries",
            "event",
            "gate open",
            "gate",
            "vehicle",
            "car",
            "unknown",
            "stranger",
            "visitor",
            "access event",
            "access log",
        ]
        return any(term in lower for term in diagnostic_terms) and any(
            term in lower for term in access_terms
        )

    def _looks_like_vehicle_detection_count_request(self, lower: str) -> bool:
        if not any(phrase in lower for phrase in ["how many times", "how often", "count"]):
            return False
        return any(word in lower for word in ["car", "vehicle", "plate", "gate", "detected", "detection"])

    def _looks_like_lpr_timing_request(self, lower: str) -> bool:
        return any(
            term in lower
            for term in [
                "lpr",
                "plate",
                "number plate",
                "scan",
                "recognise",
                "recognize",
                "recognition",
                "process",
                "processing",
                "slow",
                "slower",
                "longer",
                "latency",
                "timing",
                "took",
                "ms",
                "millisecond",
                "milliseconds",
            ]
        )

    def _refers_to_latest_unknown_vehicle(self, lower: str) -> bool:
        return any(
            phrase in lower
            for phrase in [
                "unknown",
                "mystery",
                "stranger",
                "that car",
                "that vehicle",
                "that plate",
                "the car",
                "the vehicle",
                "last car",
                "latest car",
                "last vehicle",
                "latest vehicle",
                "last detection",
                "latest detection",
            ]
        )

    def _access_diagnostic_args_from_message(self, message: str, memory: dict[str, Any]) -> dict[str, Any]:
        lower = message.lower()
        args: dict[str, Any] = {"day": self._day_from_message(lower)}
        registration_number = self._registration_from_message(message)
        if registration_number:
            args["registration_number"] = registration_number
        person_name = self._person_name_from_diagnostic_message(lower)
        if person_name:
            args["person"] = person_name
        elif any(token in lower.split() for token in ["they", "them", "he", "she", "their"]) and memory.get("last_person"):
            args["person"] = memory["last_person"]
        if any(word in lower for word in ["unknown", "mystery", "stranger", "unauthorized", "unauthorised"]):
            args["unknown_only"] = True
            args["decision"] = "denied"
        if any(word in lower for word in ["exit", "exited", "leave", "left", "leaving"]):
            args["direction"] = "exit"
        elif any(word in lower for word in ["entry", "entries", "enter", "arrival", "arrivals", "arrive", "arrived", "arriving"]):
            args["direction"] = "entry"
        return args

    def _person_name_from_diagnostic_message(self, lower: str) -> str | None:
        patterns = [
            r"\b(?:why\s+(?:did|does)\s+)?([a-z][a-z .'-]{1,40}?)(?:'s|s)\s+(?:latest|last)\s+(?:lpr|plate|detection|event|arrival|entry|scan|read)\b",
            r"\bwhy\s+(?:didn'?t|did not)\s+(?:the\s+)?gate\s+open\s+for\s+([a-z][a-z .'-]{1,40})",
            r"\bfor\s+([a-z][a-z .'-]{1,40})\b",
            r"\b([a-z][a-z .'-]{1,40}?)(?:'s|s)\s+(?:latest|last)\s+(?:lpr|plate|detection|event|arrival|entry|scan|read)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower)
            if not match:
                continue
            name = re.sub(
                r"\b(why|did|does|the|a|an|latest|last|unknown|vehicle|car|gate|notification|detection|plate|lpr)\b",
                "",
                match.group(1),
            )
            name = re.sub(r"\s+", " ", name).strip(" ?.'")
            if name:
                return name
        return None

    def _looks_like_access_event_time_request(self, lower: str) -> bool:
        return bool(
            re.search(
                r"\b(?:what time did|when did|did|has|have)\s+[a-z][a-z .'-]{1,40}?\s+"
                r"(?:leave|left|exit|exited|arrive|arrived|come|came)\b",
                lower,
            )
            or re.search(r"\b[a-z][a-z .'-]{1,40}?\s+(?:left|exited|arrived|came)\b", lower)
        )

    def _looks_like_camera_snapshot_request(self, lower: str) -> bool:
        if any(word in lower for word in ["analyze", "analyse", "describe", "visible", "see if", "look for"]):
            return False
        if any(word in lower for word in ["camera", "snapshot", "image", "photo", "picture"]):
            return bool(re.search(r"\b(?:show|get|fetch|send|latest)\b", lower))
        if not re.search(r"\b(?:show|get|fetch|send)\s+(?:me\s+)?(?:the\s+)?[a-z0-9 _.-]{2,80}\b", lower):
            return False
        camera_name = self._camera_name_from_message(lower)
        if not camera_name:
            return False
        camera_like_terms = {
            "back",
            "front",
            "side",
            "garden",
            "drive",
            "driveway",
            "yard",
            "patio",
            "gate",
            "garage",
            "entrance",
            "door",
            "parking",
            "courtyard",
        }
        return bool(camera_like_terms.intersection(camera_name.lower().split()))

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
        latest = tool_results[-1]
        tool_name = str(latest.get("name") or "")
        output = latest.get("output") if isinstance(latest.get("output"), dict) else {}
        if tool_name == "get_camera_snapshot":
            return self._camera_snapshot_direct_text(output)
        diagnostic_result = next(
            (
                result.get("output")
                for result in tool_results
                if result.get("name") == "diagnose_access_event" and isinstance(result.get("output"), dict)
            ),
            None,
        )
        if isinstance(diagnostic_result, dict):
            return self._access_diagnostic_direct_text(diagnostic_result)
        if tool_name == "query_access_events":
            events = output.get("events") if isinstance(output.get("events"), list) else []
            if not events:
                return "I couldn't find any matching recent access events."
            event = events[0]
            person_name = event.get("person") or event.get("registration_number") or "The matched subject"
            verb = "left" if event.get("direction") == "exit" else "arrived"
            occurred_at = self._chat_time_from_iso(str(event.get("occurred_at") or ""))
            return f"{person_name} {verb} at {occurred_at}." if occurred_at else f"{person_name} {verb} recently."
        if tool_name == "diagnose_access_event":
            if not output.get("found"):
                return str(output.get("error") or "I could not find a matching access event to diagnose.")
            hints = output.get("answer_hints") if isinstance(output.get("answer_hints"), list) else []
            return " ".join(str(hint) for hint in hints[:3] if hint) or "I found the diagnostic record."
        if tool_name == "query_vehicle_detection_history":
            if not output.get("found"):
                return str(output.get("error") or "I could not find that vehicle in the access events.")
            registration_number = output.get("registration_number") or "That vehicle"
            count = output.get("total_count")
            return f"{registration_number} has been detected at the gate {count} time{'s' if count != 1 else ''}."
        if tool_name == "query_device_states":
            devices = output.get("devices") if isinstance(output.get("devices"), list) else []
            if not devices:
                return "I couldn't find a matching configured device."
            return "; ".join(
                f"{device.get('name') or 'Device'} is {device.get('state') or 'unknown'}"
                for device in devices[:5]
            )
        if tool_name == "open_device":
            return self._device_open_direct_text(output)
        if tool_name == "query_leaderboard":
            top = output.get("top_known") if isinstance(output.get("top_known"), dict) else None
            known = output.get("known") if isinstance(output.get("known"), list) else []
            unknown = output.get("unknown") if isinstance(output.get("unknown"), list) else []
            if top:
                return (
                    f"{top.get('display_name') or top.get('registration_number')} is leading Top Charts "
                    f"with {top.get('read_count')} Detectiions."
                )
            if known:
                first = known[0]
                return f"{first.get('display_name') or first.get('registration_number')} leads the VIP Lounge."
            if unknown:
                first = unknown[0]
                return f"{first.get('registration_number')} leads the Mystery Guests list."
            return "I found no leaderboard entries yet."
        if tool_name == "delete_schedule":
            return self._schedule_delete_direct_text(output)
        return json.dumps([result["output"] for result in tool_results], default=str)

    def _access_diagnostic_direct_text(self, output: dict[str, Any]) -> str:
        if not output.get("found"):
            return str(output.get("error") or "I could not find a matching access event to diagnose.")
        event = output.get("event") if isinstance(output.get("event"), dict) else {}
        recognition = output.get("recognition") if isinstance(output.get("recognition"), dict) else {}
        gate = output.get("gate") if isinstance(output.get("gate"), dict) else {}
        notifications = output.get("notifications") if isinstance(output.get("notifications"), dict) else {}
        subject = event.get("person") or event.get("registration_number") or "That event"
        occurred_at = event.get("occurred_at_display") or event.get("occurred_at") or "the matched time"
        total_ms = recognition.get("total_pipeline_ms")
        debounce_ms = recognition.get("debounce_or_recognition_ms")
        slowest = recognition.get("slowest_steps") if isinstance(recognition.get("slowest_steps"), list) else []
        slowest_text = ""
        if slowest:
            step = slowest[0]
            slowest_text = f" Slowest step: {step.get('name')} at {step.get('duration_ms')}ms."
        timing_text = ""
        if total_ms is not None:
            timing_text = f" Total pipeline time was {round(float(total_ms), 1)}ms."
        if debounce_ms is not None:
            timing_text += f" Debounce/recognition accounted for {round(float(debounce_ms), 1)}ms."
        return (
            f"{subject}'s matched event was at {occurred_at}."
            f"{timing_text}{slowest_text} "
            f"{recognition.get('likely_delay_reason') or ''} "
            f"{gate.get('outcome_reason') or ''} "
            f"{notifications.get('summary') or ''}"
        ).strip()

    def _diagnostic_output(self, tool_results: list[dict[str, Any]]) -> dict[str, Any] | None:
        for result in tool_results:
            if result.get("name") != "diagnose_access_event":
                continue
            output = result.get("output")
            if isinstance(output, dict):
                return output
        return None

    def _should_replace_with_diagnostic_answer(
        self,
        message: str,
        text: str,
        tool_results: list[dict[str, Any]],
    ) -> bool:
        diagnostic = self._diagnostic_output(tool_results)
        if not diagnostic or not diagnostic.get("found"):
            return False
        lower_message = message.lower()
        if not self._looks_like_access_diagnostic_request(lower_message):
            return False
        lower_text = text.lower()
        unhelpful_markers = [
            "doesn't include",
            "doesn’t include",
            "does not include",
            "not include",
            "can't determine",
            "can’t determine",
            "cannot determine",
            "couldn't determine",
            "could not determine",
            "share the timestamp",
            "specific timestamp",
            "per-scan",
            "per scan",
            "latency metrics",
            "processing/latency metrics",
            "underlying signals",
        ]
        if any(marker in lower_text for marker in unhelpful_markers):
            return True
        recognition = diagnostic.get("recognition") if isinstance(diagnostic.get("recognition"), dict) else {}
        has_timing = (
            recognition.get("total_pipeline_ms") is not None
            or recognition.get("debounce_or_recognition_ms") is not None
        )
        timing_question = self._looks_like_lpr_timing_request(lower_message)
        return bool(timing_question and has_timing and "ms" not in lower_text and "millisecond" not in lower_text)

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
