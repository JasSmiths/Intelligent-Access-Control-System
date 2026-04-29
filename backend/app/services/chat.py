import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

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
from app.models import ChatMessage, ChatSession, Person, User, Vehicle
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
    "query_visitor_passes",
    "query_device_states",
)

EVENT_TOOL_NAMES = (
    "query_presence",
    "query_access_events",
    "diagnose_access_event",
    "investigate_access_incident",
    "query_unifi_protect_events",
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
    "override_schedule",
    "verify_schedule_access",
)
VISITOR_PASS_TOOL_NAMES = (
    "query_visitor_passes",
    "get_visitor_pass",
    "create_visitor_pass",
    "update_visitor_pass",
    "cancel_visitor_pass",
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
DEVICE_TOOL_NAMES = ("query_device_states", "command_device", "open_device", "open_gate")
MAINTENANCE_TOOL_NAMES = ("get_maintenance_status", "enable_maintenance_mode", "disable_maintenance_mode", "toggle_maintenance_mode")
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
    "backfill_access_event_from_protect",
    "cancel_visitor_pass",
    "create_notification_workflow",
    "create_schedule",
    "create_visitor_pass",
    "delete_notification_workflow",
    "delete_schedule",
    "disable_maintenance_mode",
    "enable_maintenance_mode",
    "open_gate",
    "open_device",
    "command_device",
    "investigate_access_incident",
    "override_schedule",
    "test_unifi_alarm_webhook",
    "trigger_manual_malfunction_override",
    "test_notification_workflow",
    "toggle_maintenance_mode",
    "trigger_anomaly_alert",
    "update_notification_workflow",
    "update_schedule",
    "update_visitor_pass",
}


SYSTEM_PROMPT = """You are Alfred, the AI operations agent for the Intelligent Access Control System (IACS).

System context:
IACS is a localized, high-security access and presence system for a private site. It coordinates LPR cameras, Home Assistant gates and garage doors, DVLA vehicle compliance lookups, notification workflows, UniFi Protect camera media, schedules, presence, anomaly detection, telemetry, and dashboard users. Tool results are the source of truth.

Rules of engagement:
- Be conversational, concise, calm, and professional.
- Never invent people, vehicles, schedules, events, device states, database IDs, telemetry, or DVLA records.
- Never guess database IDs. Use resolve_human_entity or an appropriate search/query tool first.
- Use tools for live system state, records, schedules, devices, cameras, notifications, reports, uploaded files, and all state-changing requests.
- For gate or garage-door failures, check Maintenance Mode and schedules before assuming a hardware malfunction.
- For "why did/didn't the gate open" questions, inspect the matching access event, schedule decision, captured gate state, Maintenance Mode, gate command result, and relevant telemetry.
- For access-event causality, prefer diagnose_access_event over shallow event lists.
- If an access event is missing, nothing was logged, a departure/arrival was expected but not recorded, or no notification was sent, use investigate_access_incident. Do not stop at "no event found"; compare IACS with UniFi Protect durable event history and smartDetectTrack candidates.
- If diagnose_access_event finds no matching event, fall through to investigate_access_incident before answering.
- For Visitor Pass requests, do not create a pass until both visitor name and expected time are known; ask a short follow-up for missing details.
- Visitor Passes are for expected unknown visitors. Do not look up or require a matching Person record before creating one.
- For Visitor Pass requests, always use local site time silently. Never ask the user to confirm local-time details unless the date or clock time is missing, and never mention local-time names or labels.
- If no Visitor Pass time window is specified, use the default +/- 30 minute window.
- Do not ask for vehicle plate, make, or colour when creating a Visitor Pass. The LPR/DVLA pipeline fills those details on arrival.
- Use Visitor Pass tools for expected unknown visitors and for follow-ups such as what car a visitor arrived in or how long they stayed.
- For MOT, tax, or vehicle identity questions, use DVLA/vehicle tools and report compliance as advisory unless a tool says access was denied for another reason.
- For state-changing tools, call the tool with confirmation set to false when confirmation is required so the UI can render a confirmation button. Do not claim an action has happened until a confirmed tool result says it happened.
- Do not expose internal entity IDs, Home Assistant entity IDs, raw JSON, tool protocol, or hidden reasoning unless the user explicitly asks for diagnostics.
- If a tool fails, explain the failure plainly and continue with any safe checks that can still help.
- Stop after the configured tool iteration limit and summarize what you found so far."""

INTENT_ROUTER_PROMPT = """Classify the user's IACS request into intent categories.
Return only compact JSON with this exact shape:
{"intents":["Access_Diagnostics"],"confidence":0.0,"requires_entity_resolution":true,"reason":"short routing note"}

Allowed categories:
Gate_Hardware, Access_Logs, Access_Diagnostics, Schedules, Maintenance,
Visitor_Passes, Compliance_DVLA, Notifications, Cameras, Reports_Files, Users_Settings, General.

Use Access_Diagnostics for why/didn't/failed/slow/latency/root-cause questions, missing access events, "nothing logged", and notification failures.
Use Visitor_Passes for expected visitors, guest passes, visitor pass CRUD, and visitor telemetry follow-ups.
Use General only when no operational category is clear."""

REACT_TOOL_PROTOCOL = """Hidden ReAct protocol:
- Think silently before each tool call.
- Reply with exactly one JSON object and no prose while acting:
{"thought":"hidden reason","tool_name":"tool_name","arguments":{}}
- When ready to answer, reply with exactly:
{"final":"human-facing answer"}
- Never expose the thought field to the user.
- Use only tools in the scoped catalog below.
- Use resolve_human_entity before using a guessed person, vehicle, group, device, or database ID.
- Exception: never use resolve_human_entity to create a Visitor Pass. Visitor Pass names are free-text expected unknown visitors, not directory People.
- If a tool returns requires_confirmation, stop and tell the user to use the confirmation button.
- If you cannot finish within {max_iterations} tool calls, return a concise final answer summarizing what you checked.

Routing result:
{routing}

Scoped tools JSON:
{tool_catalog}"""

SUPPORTED_INTENTS = {
    "Gate_Hardware",
    "Access_Logs",
    "Access_Diagnostics",
    "Schedules",
    "Visitor_Passes",
    "Maintenance",
    "Compliance_DVLA",
    "Notifications",
    "Cameras",
    "Reports_Files",
    "Users_Settings",
    "General",
}

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
CHAT_FILE_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((/api/v1/ai/chat/files/[^)]+)\)")
CHAT_FILE_URL_PATTERN = re.compile(r"\s*/api/v1/ai/chat/files/[A-Za-z0-9_-]+\b")
DEFAULT_CHAT_TIMEZONE = "Europe/London"


@dataclass(frozen=True)
class ChatTurnResult:
    session_id: str
    provider: str
    text: str
    tool_results: list[dict[str, Any]]
    attachments: list[dict[str, Any]]
    pending_action: dict[str, Any] | None = None


@dataclass(frozen=True)
class IntentRoute:
    intents: tuple[str, ...]
    confidence: float
    requires_entity_resolution: bool
    reason: str
    source: str = "deterministic"


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
        client_context: dict[str, Any] | None = None,
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
        actor_context = await self._build_actor_context(
            user_id=user_id,
            user_role=user_role,
            client_context=client_context or {},
        )

        context_token = set_chat_tool_context(
            {
                "user_id": user_id,
                "user_role": user_role,
                "actor_context": actor_context,
                "session_id": str(session_uuid),
                "provider": provider.name,
                "model": self._model_for_provider(runtime, provider.name),
                "trigger": "user_requested",
            }
        )
        try:
            tool_results: list[dict[str, Any]] = []
            guided_visitor_pass = await self._handle_guided_visitor_pass_flow(
                session_uuid,
                message,
                memory,
                actor_context=actor_context,
                provider_name=provider.name,
                status_callback=status_callback,
            )
            if guided_visitor_pass:
                return guided_visitor_pass
            guided_schedule = await self._handle_guided_schedule_flow(
                session_uuid,
                message,
                memory,
                status_callback=status_callback,
            )
            if guided_schedule:
                return guided_schedule
            route = await self._classify_intent(provider, message, memory, attachment_refs, actor_context=actor_context)
            selected_tools = self._select_tools_for_route(route, attachment_refs)
            messages = await self._build_agent_messages(
                session_uuid,
                tool_results,
                selected_tools,
                route,
                actor_context=actor_context,
            )

            try:
                result = await self._run_provider_agent_loop(
                    provider,
                    session_uuid,
                    messages,
                    tool_results,
                    selected_tools,
                    memory,
                    route=route,
                    user_message=message,
                    attachments=attachment_refs,
                    actor_context=actor_context,
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
            await self._pending_action_for_response(session_uuid, user_id=user_id),
        )

    async def handle_tool_confirmation(
        self,
        *,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        confirmation_id: str | None = None,
        decision: str = "confirm",
        session_id: str | None = None,
        user_id: str | None = None,
        user_role: str | None = None,
        client_context: dict[str, Any] | None = None,
        status_callback: StatusCallback | None = None,
    ) -> ChatTurnResult:
        session_uuid = await self._ensure_session(session_id)
        if confirmation_id:
            return await self._handle_pending_action_decision(
                session_uuid,
                confirmation_id=confirmation_id,
                decision=decision,
                user_id=user_id,
                user_role=user_role,
                client_context=client_context or {},
                status_callback=status_callback,
            )

        return await self._direct_response(
            session_uuid,
            "That confirmation is no longer available. Please ask Alfred to prepare the action again.",
        )

    async def _handle_pending_action_decision(
        self,
        session_uuid: uuid.UUID,
        *,
        confirmation_id: str,
        decision: str,
        user_id: str | None,
        user_role: str | None,
        client_context: dict[str, Any],
        status_callback: StatusCallback | None,
    ) -> ChatTurnResult:
        pending = await self._load_pending_agent_action(
            session_uuid,
            confirmation_id=confirmation_id,
            user_id=user_id,
        )
        if not pending:
            return await self._direct_response(
                session_uuid,
                "That confirmation has expired or was already handled. Please ask me to prepare it again.",
            )

        tool_name = str(pending.get("tool_name") or "")
        if decision.strip().lower() != "confirm":
            await self._clear_pending_agent_action(session_uuid)
            await self._append_message(session_uuid, "user", f"Cancelled action {confirmation_id}")
            return await self._direct_response(session_uuid, "Okay, I cancelled that action. Nothing was changed.")

        runtime = await get_runtime_config()
        provider = get_llm_provider(str(pending.get("provider") or runtime.llm_provider))
        actor_context = pending.get("actor_context") if isinstance(pending.get("actor_context"), dict) else await self._build_actor_context(
            user_id=user_id,
            user_role=user_role,
            client_context=client_context,
        )
        tool_results: list[dict[str, Any]] = []
        context_token = set_chat_tool_context(
            {
                "user_id": user_id,
                "user_role": user_role,
                "session_id": str(session_uuid),
                "actor_context": actor_context,
                "provider": provider.name,
                "model": self._model_for_provider(runtime, provider.name),
                "trigger": "user_confirmed",
            }
        )
        try:
            result_text = ""
            arguments = self._confirmed_arguments_for_pending(pending)
            call = ToolCall(
                id=f"confirmed-{tool_name}-{uuid.uuid4().hex[:8]}",
                name=tool_name,
                arguments=arguments,
            )
            await self._append_message(
                session_uuid,
                "user",
                self._confirmation_user_message(tool_name, {"confirmation_id": confirmation_id}),
            )
            try:
                tool_result = await self._execute_tool_call(
                    session_uuid,
                    call,
                    status_callback=status_callback,
                )
            except Exception as exc:
                logger.warning(
                    "agent_confirmation_execution_failed",
                    extra={"tool": tool_name, "error": str(exc)[:240]},
                )
                tool_result = {
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "output": {"error": str(exc)[:500], "status": "failed"},
                }
            finally:
                await self._clear_pending_agent_action(session_uuid)

            tool_results = list(pending.get("tool_results") if isinstance(pending.get("tool_results"), list) else [])
            tool_results.append(tool_result)
            if self._confirmed_tool_finishes_without_resume(tool_name):
                tool_results = [tool_result]
                result_text = self._confirmation_result_text(tool_name, tool_result.get("output", {}))
            else:
                route = self._route_from_pending(pending)
                selected_tools = [
                    self._tools[name]
                    for name in pending.get("selected_tools", [])
                    if isinstance(name, str) and name in self._tools
                ] or self._select_tools_for_route(route, [])
                memory = await self._load_memory(session_uuid)
                messages = await self._build_agent_messages(
                    session_uuid,
                    tool_results,
                    selected_tools,
                    route,
                    actor_context=actor_context,
                )
                try:
                    resumed = await self._run_provider_agent_loop(
                        provider,
                        session_uuid,
                        messages,
                        tool_results,
                        selected_tools,
                        memory,
                        route=route,
                        user_message=str(pending.get("user_message") or ""),
                        attachments=[],
                        actor_context=actor_context,
                        status_callback=status_callback,
                    )
                    result_text = resumed.text if isinstance(resumed, LlmResult) else resumed.text
                except Exception as exc:
                    logger.info("agent_confirmation_resume_failed", extra={"tool": tool_name, "error": str(exc)[:240]})
                    result_text = self._confirmation_result_text(tool_name, tool_result.get("output", {}))
        finally:
            set_chat_tool_context({}, token=context_token)

        attachments = self._attachments_from_tool_results(tool_results)
        text = self._clean_assistant_text(
            result_text or self._confirmation_result_text(tool_name, tool_result.get("output", {})),
            attachments,
        )
        await self._append_message(session_uuid, "assistant", text)
        await self._update_memory(session_uuid, text, tool_results)
        await event_bus.publish(
            "chat.message",
            {
                "session_id": str(session_uuid),
                "provider": provider.name,
                "text": text,
                "attachments": attachments,
            },
        )
        return ChatTurnResult(
            str(session_uuid),
            provider.name,
            text,
            tool_results,
            attachments,
        )

    def _confirmed_tool_finishes_without_resume(self, tool_name: str) -> bool:
        return tool_name in {"create_visitor_pass", "update_visitor_pass", "cancel_visitor_pass"}

    async def list_tools(self) -> list[dict[str, Any]]:
        return [tool.as_llm_tool() for tool in self._tools.values()]

    async def _build_actor_context(
        self,
        *,
        user_id: str | None,
        user_role: str | None,
        client_context: dict[str, Any],
    ) -> dict[str, Any]:
        runtime = await get_runtime_config()
        site_timezone = runtime.site_timezone or DEFAULT_CHAT_TIMEZONE
        now = datetime.now(tz=ZoneInfo(site_timezone))
        context: dict[str, Any] = {
            "site": {
                "timezone": site_timezone,
                "local_time": now.isoformat(),
                "location": "IACS private site",
            },
            "client": {
                key: value
                for key, value in {
                    "timezone": client_context.get("timezone"),
                    "locale": client_context.get("locale"),
                }.items()
                if value
            },
            "user": {
                "id": user_id,
                "role": user_role,
            },
            "person": None,
            "vehicles": [],
        }
        if not user_id:
            return context
        try:
            parsed_user_id = uuid.UUID(str(user_id))
        except (TypeError, ValueError):
            return context

        async with AsyncSessionLocal() as session:
            user = await session.get(User, parsed_user_id)
            if not user:
                return context
            first_name = user.first_name or ""
            last_name = user.last_name or ""
            display_name = " ".join(part for part in [first_name, last_name] if part).strip() or user.full_name
            context["user"] = {
                "id": str(user.id),
                "role": user.role.value,
                "username": user.username,
                "display_name": display_name,
                "person_id": str(user.person_id) if user.person_id else None,
            }
            if not user.person_id:
                return context
            person = await session.scalar(
                select(Person)
                .options(
                    selectinload(Person.group),
                    selectinload(Person.schedule),
                    selectinload(Person.presence),
                    selectinload(Person.vehicles).selectinload(Vehicle.schedule),
                )
                .where(Person.id == user.person_id)
            )
            if not person:
                return context
            presence = person.presence
            context["person"] = {
                "id": str(person.id),
                "display_name": person.display_name,
                "first_name": person.first_name,
                "last_name": person.last_name,
                "group_id": str(person.group_id) if person.group_id else None,
                "group": person.group.name if person.group else None,
                "schedule_id": str(person.schedule_id) if person.schedule_id else None,
                "schedule": person.schedule.name if person.schedule else None,
                "presence": presence.state.value if presence else None,
                "presence_last_changed_at": (
                    presence.last_changed_at.isoformat() if presence and presence.last_changed_at else None
                ),
                "is_active": person.is_active,
            }
            context["vehicles"] = [
                {
                    "id": str(vehicle.id),
                    "registration_number": vehicle.registration_number,
                    "make": vehicle.make,
                    "model": vehicle.model,
                    "color": vehicle.color,
                    "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
                    "schedule": vehicle.schedule.name if vehicle.schedule else None,
                    "is_active": vehicle.is_active,
                }
                for vehicle in person.vehicles
                if vehicle.is_active
            ]
        return context

    async def _classify_intent(
        self,
        provider: Any,
        message: str,
        memory: dict[str, Any],
        attachments: list[dict[str, Any]],
        *,
        actor_context: dict[str, Any] | None = None,
    ) -> IntentRoute:
        fallback = self._deterministic_intent_route(message, memory, attachments, actor_context=actor_context)
        if provider.name == "local":
            return fallback

        try:
            result = await provider.complete(
                [
                    ChatMessageInput("system", INTENT_ROUTER_PROMPT),
                    ChatMessageInput(
                        "user",
                        json.dumps(
                            {
                                "message": message,
                                "has_attachments": bool(attachments),
                                "session_memory": memory,
                                "actor_context": actor_context or {},
                            },
                            default=str,
                            separators=(",", ":"),
                        ),
                    ),
                ]
            )
        except Exception as exc:
            logger.info(
                "intent_router_fallback",
                extra={"provider": getattr(provider, "name", "unknown"), "error": str(exc)[:240]},
            )
            return fallback

        payload = self._extract_tool_call_payload(result.text)
        if not isinstance(payload, dict):
            return fallback
        raw_intents = payload.get("intents")
        intents = tuple(
            intent
            for intent in (str(item).strip() for item in raw_intents or [])
            if intent in SUPPORTED_INTENTS
        )
        if not intents:
            intents = fallback.intents
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence"))))
        except (TypeError, ValueError):
            confidence = fallback.confidence
        return IntentRoute(
            intents=intents,
            confidence=confidence,
            requires_entity_resolution=bool(
                payload.get("requires_entity_resolution", fallback.requires_entity_resolution)
            ),
            reason=str(payload.get("reason") or fallback.reason)[:240],
            source=f"{provider.name}_classifier",
        )

    def _deterministic_intent_route(
        self,
        message: str,
        memory: dict[str, Any],
        attachments: list[dict[str, Any]],
        *,
        actor_context: dict[str, Any] | None = None,
    ) -> IntentRoute:
        lower = message.lower()
        intents: list[str] = []
        if attachments or any(word in lower for word in ["file", "attachment", "download", "csv", "pdf", "export", "invoice"]):
            intents.append("Reports_Files")
        if any(word in lower for word in ["camera", "snapshot", "image", "photo", "picture", "visible", "see"]):
            intents.append("Cameras")
        if any(word in lower for word in ["notification", "notifications", "workflow", "workflows", "template", "apprise"]):
            intents.append("Notifications")
        if any(word in lower for word in ["maintenance", "kill-switch", "kill switch", "disable automation", "resume automation"]):
            intents.append("Maintenance")
        if self._looks_like_visitor_pass_request(lower) or memory.get("pending_visitor_pass_create"):
            intents.append("Visitor_Passes")
        if any(word in lower for word in ["schedule", "schedules", "timeframe", "allowed", "access window"]):
            intents.append("Schedules")
        if any(word in lower for word in ["dvla", "mot", "tax", "compliance", "registration", "plate", "vehicle", "tesla", "car"]):
            intents.append("Compliance_DVLA")
        if any(word in lower for word in ["gate", "garage", "door", "cover", "device", "malfunction", "fubar", "stuck", "open"]):
            intents.append("Gate_Hardware")
        if self._looks_like_missing_access_incident(lower) or self._looks_like_access_diagnostic_request(lower) or any(
            word in lower for word in ["why", "failed", "failure", "didn't", "didnt", "slow", "latency", "delay", "malfunction"]
        ):
            intents.append("Access_Diagnostics")
        if any(
            word in lower
            for word in ["present", "presence", "onsite", "on site", "arrive", "arrival", "arrived", "left", "leave", "exit", "event", "denied", "anomaly", "how long", "duration", "leaderboard", "top charts"]
        ):
            intents.append("Access_Logs")
        if any(word in lower for word in ["user", "users", "account", "accounts", "admin", "setting", "settings", "telemetry", "trace"]):
            intents.append("Users_Settings")
        if memory.get("pending_schedule_create"):
            intents.append("Schedules")
        if memory.get("last_visitor_name") and any(phrase in lower for phrase in ["what car", "which car", "how long", "duration", "stayed", "arrived in"]):
            intents.append("Visitor_Passes")
        if not intents:
            intents.append("General")
        deduped = tuple(dict.fromkeys(intent for intent in intents if intent in SUPPORTED_INTENTS))
        actor_has_vehicle = bool(((actor_context or {}).get("vehicles") or []))
        actor_has_person = bool(((actor_context or {}).get("person") or {}).get("id"))
        pronoun_reference = bool(re.search(r"\b(he|she|they|them|their|it|that|steph|wife|husband|tesla|car|vehicle)\b", lower))
        exact_actor_reference = bool(
            actor_has_person
            and re.search(r"\b(me|myself|mine)\b", lower)
            or actor_has_vehicle
            and re.search(r"\b(my car|my vehicle|my tesla)\b", lower)
        )
        needs_entity = bool(
            pronoun_reference and not exact_actor_reference
            or self._person_name_from_event_time_message(lower)
            or self._registration_from_message(message)
        )
        return IntentRoute(
            intents=deduped,
            confidence=0.72 if deduped != ("General",) else 0.45,
            requires_entity_resolution=needs_entity,
            reason="deterministic keyword and session-memory route",
            source="deterministic",
        )

    def _select_tools_for_route(
        self,
        route: IntentRoute,
        attachments: list[dict[str, Any]],
    ) -> list[AgentTool]:
        intents = set(route.intents or ("General",))
        pure_visitor_pass_route = intents == {"Visitor_Passes"}
        names: set[str] = set() if pure_visitor_pass_route else {"resolve_human_entity"}
        if attachments:
            names.add("read_chat_attachment")
        for name, tool in self._tools.items():
            if pure_visitor_pass_route and name == "resolve_human_entity":
                continue
            if intents.intersection(tool.categories):
                if intents == {"General"} and not tool.read_only:
                    continue
                names.add(name)
        if "Access_Diagnostics" in intents:
            names.update(
                {
                    "backfill_access_event_from_protect",
                    "diagnose_access_event",
                    "get_maintenance_status",
                    "get_telemetry_trace",
                    "investigate_access_incident",
                    "query_access_events",
                    "query_lpr_timing",
                    "query_unifi_protect_events",
                    "resolve_human_entity",
                    "test_unifi_alarm_webhook",
                    "verify_schedule_access",
                }
            )
        if "Gate_Hardware" in intents:
            names.update({"get_maintenance_status", "query_device_states"})
        if "Compliance_DVLA" in intents:
            names.update({"lookup_dvla_vehicle", "query_vehicle_detection_history"})
        if "Schedules" in intents:
            names.update({"query_schedules", "query_schedule_targets", "verify_schedule_access"})
        if "Visitor_Passes" in intents:
            names.update(VISITOR_PASS_TOOL_NAMES)
        if "Reports_Files" in intents and attachments:
            names.add("read_chat_attachment")
        return [tool for name, tool in self._tools.items() if name in names]

    async def _run_provider_agent_loop(
        self,
        provider: Any,
        session_id: uuid.UUID,
        messages: list[ChatMessageInput],
        tool_results: list[dict[str, Any]],
        selected_tools: list[AgentTool],
        memory: dict[str, Any],
        *,
        route: IntentRoute | None = None,
        user_message: str = "",
        attachments: list[dict[str, Any]] | None = None,
        actor_context: dict[str, Any] | None = None,
        status_callback: StatusCallback | None,
    ) -> LlmResult | ChatTurnResult:
        tool_schemas = [tool.as_llm_tool() for tool in selected_tools]
        allowed_tool_names = {tool.name for tool in selected_tools}
        executed: set[str] = set()
        result = LlmResult(text="")
        route = route or IntentRoute(("General",), 0.5, True, "default route")

        for iteration in range(MAX_AGENT_TOOL_ITERATIONS):
            if provider.name == "local":
                calls = self._deterministic_react_calls(
                    user_message,
                    route,
                    memory,
                    attachments or [],
                    tool_results,
                    selected_tools,
                    iteration=iteration,
                    actor_context=actor_context,
                )
                if not calls:
                    result = await provider.complete(messages, tools=tool_schemas, tool_results=tool_results)
                    return LlmResult(text=self._clean_agent_text(result.text), raw=result.raw)
            else:
                result = await provider.complete(messages, tools=tool_schemas)
                final_text = self._react_final_from_result(result)
                if final_text:
                    return LlmResult(text=final_text, raw=result.raw)
                calls = self._tool_calls_from_result(result, iteration=iteration)
            if not calls:
                return LlmResult(text=self._clean_agent_text(result.text), raw=result.raw)

            fresh_calls: list[ToolCall] = []
            for call in calls:
                if call.name not in allowed_tool_names:
                    return LlmResult(
                        text=(
                            f"I could not safely use {call.name or 'that tool'} for this request. "
                            "Please rephrase the request or specify the system area you want me to inspect."
                        ),
                        raw=result.raw,
                    )
                call = self._safe_state_changing_call(call)
                fingerprint = self._tool_call_fingerprint(call)
                if fingerprint not in executed:
                    executed.add(fingerprint)
                    fresh_calls.append(call)
            if not fresh_calls:
                return LlmResult(
                    text=self._fallback_text(tool_results),
                    raw=result.raw,
                )

            tool_by_name = {tool.name: tool for tool in selected_tools}
            read_calls = [call for call in fresh_calls if tool_by_name.get(call.name) and tool_by_name[call.name].read_only]
            action_calls = [call for call in fresh_calls if call not in read_calls]
            native_results: list[dict[str, Any]] = []
            if read_calls:
                native_results.extend(
                    await self._execute_tool_batch(
                        session_id,
                        read_calls,
                        selected_tools,
                        status_callback=status_callback,
                    )
                )
            if action_calls:
                native_results.extend(
                    await self._execute_tool_batch(
                        session_id,
                        action_calls[:1],
                        selected_tools,
                        status_callback=status_callback,
                    )
                )
            tool_results.extend(native_results)
            conflict_result = await self._schedule_conflict_response(session_id, memory, tool_results)
            if conflict_result:
                return conflict_result
            if any(
                isinstance(result.get("output"), dict) and result["output"].get("requires_confirmation")
                for result in native_results
            ):
                pending_result = next(
                    result
                    for result in native_results
                    if isinstance(result.get("output"), dict) and result["output"].get("requires_confirmation")
                )
                pending_action = await self._store_pending_agent_action(
                    session_id,
                    pending_result,
                    tool_results,
                    route,
                    selected_tools,
                    provider_name=provider.name,
                    user_message=user_message,
                    user_id=str((actor_context or {}).get("user", {}).get("id") or ""),
                    actor_context=actor_context or {},
                    iteration=iteration,
                )
                if status_callback:
                    await status_callback({"event": "chat.confirmation_required", **pending_action})
                return LlmResult(text=self._fallback_text(tool_results), raw=result.raw)
            messages = await self._build_agent_messages(
                session_id,
                tool_results,
                selected_tools,
                route,
                actor_context=actor_context,
            )

        return LlmResult(
            text=(
                "I hit my five-step safety limit while checking this. "
                f"{self._fallback_text(tool_results)}"
            ),
            raw=result.raw,
        )

    def _tool_calls_from_result(self, result: LlmResult, *, iteration: int) -> list[ToolCall]:
        if result.tool_calls:
            return result.tool_calls
        return self._tool_calls_from_text(result.text, iteration=iteration)

    def _react_final_from_result(self, result: LlmResult) -> str | None:
        if result.tool_calls:
            return None
        payload = self._extract_tool_call_payload(result.text)
        if isinstance(payload, dict) and isinstance(payload.get("final"), str):
            return payload["final"].strip()
        return None

    def _tool_calls_from_text(self, text: str, *, iteration: int) -> list[ToolCall]:
        payload = self._extract_tool_call_payload(text)
        if payload is None:
            return []

        raw_calls: Any
        if isinstance(payload, dict):
            raw_calls = payload.get("tool_calls") or payload.get("tools") or payload.get("calls")
            if raw_calls is None and (payload.get("name") or payload.get("tool")):
                raw_calls = [payload]
            if raw_calls is None and payload.get("tool_name"):
                raw_calls = [
                    {
                        "id": payload.get("id") or f"react-{iteration}",
                        "name": payload.get("tool_name"),
                        "arguments": payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {},
                    }
                ]
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
        payload = self._extract_tool_call_payload(cleaned)
        if isinstance(payload, dict):
            if isinstance(payload.get("final"), str):
                return payload["final"].strip()
            if payload.get("thought") or payload.get("tool_name"):
                return "I could not safely complete that tool step. Please rephrase that and I will try again."
        if cleaned.startswith("IACS_FINAL:"):
            return cleaned.removeprefix("IACS_FINAL:").strip()
        return cleaned

    def _tool_call_fingerprint(self, call: ToolCall) -> str:
        return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"

    def _safe_state_changing_call(self, call: ToolCall) -> ToolCall:
        if call.name not in STATE_CHANGING_TOOL_NAMES:
            return call
        arguments = dict(call.arguments)
        if "confirm" in arguments or call.name != "test_notification_workflow":
            arguments["confirm"] = False
        if "confirm_send" in arguments or call.name == "test_notification_workflow":
            arguments["confirm_send"] = False
        return ToolCall(call.id, call.name, arguments)

    def _deterministic_react_calls(
        self,
        message: str,
        route: IntentRoute,
        memory: dict[str, Any],
        attachments: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        selected_tools: list[AgentTool],
        *,
        iteration: int,
        actor_context: dict[str, Any] | None = None,
    ) -> list[ToolCall]:
        allowed = {tool.name for tool in selected_tools}
        intents = set(route.intents)
        lower = message.lower()
        calls: list[ToolCall] = []

        if iteration == 0 and route.requires_entity_resolution and "resolve_human_entity" in allowed:
            entity_types = self._entity_types_for_route(route)
            query = self._entity_query_from_message(message, memory)
            if query:
                calls.append(
                    ToolCall(
                        "react-resolve-entity",
                        "resolve_human_entity",
                        {"query": query, "entity_types": entity_types},
                    )
                )

        if iteration == 0:
            for index, attachment in enumerate(attachments[:4]):
                if "read_chat_attachment" in allowed:
                    calls.append(
                        ToolCall(
                            f"react-read-attachment-{index}",
                            "read_chat_attachment",
                            {
                                "file_id": attachment["id"],
                                "prompt": message or "Summarize this attachment for the user.",
                            },
                        )
                    )

        if iteration > 0 or not calls:
            if (
                "Access_Diagnostics" in intents
                and "investigate_access_incident" in allowed
                and (
                    self._looks_like_missing_access_incident(lower)
                    or self._latest_diagnostic_result_not_found(tool_results)
                )
            ):
                args = self._access_incident_args_from_message(message, memory, actor_context=actor_context)
                calls.append(ToolCall("react-investigate-access-incident", "investigate_access_incident", args))
            elif "Access_Diagnostics" in intents and "diagnose_access_event" in allowed:
                args = self._access_diagnostic_args_from_message(message, memory, actor_context=actor_context)
                args.setdefault("summarize_payload", True)
                args.setdefault("span_limit", 20)
                calls.append(ToolCall("react-diagnose-access", "diagnose_access_event", args))
                if self._looks_like_lpr_timing_request(lower) and "query_lpr_timing" in allowed:
                    lpr_args = {
                        key: value
                        for key, value in {
                            "registration_number": args.get("registration_number"),
                            "limit": 25,
                        }.items()
                        if value
                    }
                    calls.append(ToolCall("react-lpr-timing", "query_lpr_timing", lpr_args))
            elif "Gate_Hardware" in intents:
                if self._looks_like_device_action_request(lower):
                    action = self._device_action_from_message(lower)
                    if action == "open" and "garage" not in lower and "open_gate" in allowed:
                        calls.append(
                            ToolCall(
                                "react-open-gate",
                                "open_gate",
                                {
                                    "target": self._device_target_from_message(lower) or "",
                                    "reason": message,
                                    "confirm": self._explicitly_confirmed_device_action(lower),
                                },
                            )
                        )
                    elif action == "close" and "command_device" in allowed:
                        calls.append(self._planned_device_action_call(message))
                    elif "command_device" in allowed:
                        calls.append(self._planned_device_action_call(message))
                    elif "open_device" in allowed:
                        calls.append(self._planned_device_open_call(message))
                elif "query_device_states" in allowed:
                    calls.append(
                        ToolCall(
                            "react-query-device-states",
                            "query_device_states",
                            {"target": self._device_target_from_message(lower) or "", "kind": "all"},
                        )
                    )
                if any(word in lower for word in ["malfunction", "fubar", "stuck", "recovery", "attempt"]) and "get_active_malfunctions" in allowed:
                    calls.append(ToolCall("react-active-malfunctions", "get_active_malfunctions", {"include_timeline": True}))
            elif "Maintenance" in intents:
                if "toggle_maintenance_mode" in allowed and any(word in lower for word in ["enable", "turn on", "activate", "start", "disable automation"]):
                    calls.append(
                        ToolCall(
                            "react-enable-maintenance",
                            "toggle_maintenance_mode",
                            {"state": "enabled", "reason": message, "confirm": self._is_confirmation_message(lower)},
                        )
                    )
                elif "toggle_maintenance_mode" in allowed and any(word in lower for word in ["disable", "turn off", "deactivate", "stop", "resume automation"]):
                    calls.append(
                        ToolCall(
                            "react-disable-maintenance",
                            "toggle_maintenance_mode",
                            {"state": "disabled", "confirm": self._is_confirmation_message(lower)},
                        )
                    )
                elif "get_maintenance_status" in allowed:
                    calls.append(ToolCall("react-maintenance-status", "get_maintenance_status", {}))
            elif "Visitor_Passes" in intents:
                visitor_name = self._visitor_name_from_message(message) or str(memory.get("last_visitor_name") or "")
                expected_time = self._visitor_expected_time_from_message(message)
                if self._looks_like_visitor_pass_cancel_request(lower) and "cancel_visitor_pass" in allowed:
                    calls.append(
                        ToolCall(
                            "react-cancel-visitor-pass",
                            "cancel_visitor_pass",
                            {
                                "visitor_name": visitor_name,
                                "reason": message,
                                "confirm": self._is_confirmation_message(lower),
                            },
                        )
                    )
                elif self._looks_like_visitor_pass_create_request(lower) and "create_visitor_pass" in allowed and visitor_name and expected_time:
                    calls.append(
                        ToolCall(
                            "react-create-visitor-pass",
                            "create_visitor_pass",
                            {
                                "visitor_name": visitor_name,
                                "expected_time": expected_time,
                                "window_minutes": self._visitor_window_from_message(lower) or 30,
                                "confirm": self._is_confirmation_message(lower),
                            },
                        )
                    )
                elif "query_visitor_passes" in allowed:
                    query_args: dict[str, Any] = {"limit": 10}
                    if visitor_name:
                        query_args["search"] = visitor_name
                    elif not any(phrase in lower for phrase in ["what car", "which car", "how long", "duration", "stayed", "arrived in"]):
                        query_args["statuses"] = ["active", "scheduled"]
                    calls.append(ToolCall("react-query-visitor-passes", "query_visitor_passes", query_args))
            elif "Compliance_DVLA" in intents:
                registration_number = self._registration_from_message(message)
                if registration_number and "lookup_dvla_vehicle" in allowed:
                    calls.append(ToolCall("react-dvla", "lookup_dvla_vehicle", {"registration_number": registration_number}))
                elif "query_vehicle_detection_history" in allowed:
                    calls.append(ToolCall("react-vehicle-history", "query_vehicle_detection_history", {"period": "recent", "limit": 10}))
            elif "Access_Logs" in intents:
                if any(phrase in lower for phrase in ["how long", "duration", "stay", "stayed"]) and "calculate_visit_duration" in allowed:
                    args = self._subject_args(self._subject_from_message(lower, memory, actor_context=actor_context))
                    args["day"] = "today" if "today" in lower else "recent"
                    calls.append(ToolCall("react-duration", "calculate_visit_duration", args))
                elif "query_access_events" in allowed:
                    args = self._subject_args(self._subject_from_message(lower, memory, actor_context=actor_context))
                    args["day"] = "today" if "today" in lower else "recent"
                    args["limit"] = 10
                    args["summarize_payload"] = True
                    calls.append(ToolCall("react-query-events", "query_access_events", args))
            elif "Schedules" in intents and "query_schedules" in allowed:
                calls.append(ToolCall("react-query-schedules", "query_schedules", {"include_dependencies": True}))
            elif "Notifications" in intents and "query_notification_workflows" in allowed:
                calls.append(ToolCall("react-query-notifications", "query_notification_workflows", {"limit": 20}))
            elif "Cameras" in intents and "get_camera_snapshot" in allowed and self._looks_like_camera_snapshot_request(lower):
                calls.append(self._planned_camera_snapshot_call(message))
            elif "Users_Settings" in intents:
                if "get_telemetry_trace" in allowed and "trace" in lower:
                    calls.append(ToolCall("react-telemetry", "get_telemetry_trace", {"limit": 20}))
                elif "get_system_users" in allowed:
                    calls.append(ToolCall("react-users", "get_system_users", {}))
            elif "query_presence" in allowed:
                calls.append(ToolCall("react-presence", "query_presence", {}))

        fresh: list[ToolCall] = []
        seen = {
            self._tool_call_fingerprint(
                ToolCall(str(result.get("call_id") or ""), str(result.get("name") or ""), result.get("arguments") if isinstance(result.get("arguments"), dict) else {})
            )
            for result in tool_results
        }
        for call in calls:
            if call.name not in allowed:
                continue
            fingerprint = self._tool_call_fingerprint(call)
            if fingerprint in seen:
                continue
            fresh.append(call)
        return fresh[:2]

    def _entity_types_for_route(self, route: IntentRoute) -> list[str]:
        intents = set(route.intents)
        if "Gate_Hardware" in intents:
            return ["device", "person", "vehicle"]
        if "Compliance_DVLA" in intents:
            return ["vehicle", "person"]
        if "Schedules" in intents:
            return ["person", "vehicle", "group", "device"]
        if "Visitor_Passes" in intents:
            return ["person", "vehicle"]
        if "Access_Logs" in intents or "Access_Diagnostics" in intents:
            return ["person", "vehicle", "group"]
        return ["person", "vehicle", "group", "device"]

    def _entity_query_from_message(self, message: str, memory: dict[str, Any]) -> str:
        registration = self._registration_from_message(message)
        if registration:
            return registration
        person_name = self._person_name_from_event_time_message(message.lower())
        if person_name:
            return person_name
        subject = self._subject_from_message(message.lower(), memory)
        if subject.get("person"):
            return subject["person"]
        if subject.get("group"):
            return subject["group"]
        match = re.search(r"\b(?:for|did|didn't|didnt|was|is|has|about)\s+([A-Za-z][A-Za-z' -]{1,40})", message)
        if match:
            return match.group(1).strip(" ?.!'")
        return message.strip()[:80]

    def _tool_results_for_prompt(self, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "call_id": result.get("call_id"),
                "name": result.get("name"),
                "arguments": self._compact_prompt_value(result.get("arguments") if isinstance(result.get("arguments"), dict) else {}),
                "output": self._compact_prompt_value(result.get("output") if isinstance(result.get("output"), dict) else result.get("output")),
            }
            for result in tool_results
        ]

    def _compact_prompt_value(self, value: Any, *, depth: int = 0) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value if len(value) <= 1000 else f"{value[:1000]}... [truncated]"
        if isinstance(value, list):
            if depth >= 4:
                return {"type": "list", "count": len(value)}
            items = [self._compact_prompt_value(item, depth=depth + 1) for item in value[:12]]
            if len(value) > 12:
                items.append({"omitted_items": len(value) - 12})
            return items
        if isinstance(value, dict):
            if depth >= 4:
                return {"type": "object", "keys": list(value.keys())[:20], "key_count": len(value)}
            compacted = {}
            for key, item in list(value.items())[:50]:
                key_text = str(key)
                if key_text.lower() in {"timezone", "site_timezone"}:
                    continue
                if any(secret in key_text.lower() for secret in ("api_key", "password", "secret", "token")):
                    compacted[key_text] = "[redacted]"
                elif any(media in key_text.lower() for media in ("image", "photo", "snapshot", "thumbnail", "video")):
                    compacted[key_text] = "[omitted_large_media]"
                else:
                    compacted[key_text] = self._compact_prompt_value(item, depth=depth + 1)
            if len(value) > 50:
                compacted["omitted_keys"] = len(value) - 50
            return {key: item for key, item in compacted.items() if item not in (None, "", [], {})}
        return str(value)

    async def _build_agent_messages(
        self,
        session_id: uuid.UUID,
        tool_results: list[dict[str, Any]],
        selected_tools: list[AgentTool],
        route: IntentRoute,
        *,
        actor_context: dict[str, Any] | None = None,
    ) -> list[ChatMessageInput]:
        try:
            return await self._build_messages(
                session_id,
                tool_results,
                selected_tools,
                route=route,
                actor_context=actor_context,
            )
        except TypeError as exc:
            if "route" not in str(exc) and "actor_context" not in str(exc):
                raise
            return await self._build_messages(session_id, tool_results, selected_tools)

    async def _handle_guided_visitor_pass_flow(
        self,
        session_id: uuid.UUID,
        message: str,
        memory: dict[str, Any],
        *,
        actor_context: dict[str, Any] | None,
        provider_name: str,
        status_callback: StatusCallback | None,
    ) -> ChatTurnResult | None:
        pending = memory.get("pending_visitor_pass_create")
        if isinstance(pending, dict):
            return await self._continue_visitor_pass_create(
                session_id,
                message,
                memory,
                pending,
                actor_context=actor_context,
                provider_name=provider_name,
                status_callback=status_callback,
            )

        lower = message.lower()
        if not self._looks_like_visitor_pass_create_request(lower):
            return None

        runtime = await get_runtime_config()
        visitor_name = self._visitor_name_from_message(message)
        expected_time = self._visitor_expected_time_from_message(message, runtime.site_timezone)
        window_minutes = self._visitor_window_from_message(lower) or 30
        if not visitor_name or not expected_time:
            memory["pending_visitor_pass_create"] = {
                "visitor_name": visitor_name,
                "expected_time": expected_time,
                "window_minutes": window_minutes,
            }
            await self._save_memory(session_id, memory)
            return await self._direct_response(
                session_id,
                self._visitor_pass_missing_details_text(visitor_name, expected_time),
            )

        return await self._prepare_visitor_pass_confirmation(
            session_id,
            memory,
            visitor_name=visitor_name,
            expected_time=expected_time,
            window_minutes=window_minutes,
            actor_context=actor_context,
            provider_name=provider_name,
            user_message=message,
            status_callback=status_callback,
        )

    async def _continue_visitor_pass_create(
        self,
        session_id: uuid.UUID,
        message: str,
        memory: dict[str, Any],
        pending: dict[str, Any],
        *,
        actor_context: dict[str, Any] | None,
        provider_name: str,
        status_callback: StatusCallback | None,
    ) -> ChatTurnResult:
        lower = message.lower().strip()
        if lower in {"cancel", "stop", "never mind", "nevermind"}:
            memory.pop("pending_visitor_pass_create", None)
            await self._save_memory(session_id, memory)
            return await self._direct_response(session_id, "No problem - I cancelled that Visitor Pass setup.")

        runtime = await get_runtime_config()
        visitor_name = (
            self._visitor_name_from_message(message)
            or str(pending.get("visitor_name") or "").strip()
            or None
        )
        expected_time = (
            self._visitor_expected_time_from_message(message, runtime.site_timezone)
            or str(pending.get("expected_time") or "").strip()
            or None
        )
        window_minutes = self._visitor_window_from_message(lower) or int(pending.get("window_minutes") or 30)
        if not visitor_name or not expected_time:
            memory["pending_visitor_pass_create"] = {
                "visitor_name": visitor_name,
                "expected_time": expected_time,
                "window_minutes": window_minutes,
            }
            await self._save_memory(session_id, memory)
            return await self._direct_response(
                session_id,
                self._visitor_pass_missing_details_text(visitor_name, expected_time),
            )

        return await self._prepare_visitor_pass_confirmation(
            session_id,
            memory,
            visitor_name=visitor_name,
            expected_time=expected_time,
            window_minutes=window_minutes,
            actor_context=actor_context,
            provider_name=provider_name,
            user_message=message,
            status_callback=status_callback,
        )

    async def _prepare_visitor_pass_confirmation(
        self,
        session_id: uuid.UUID,
        memory: dict[str, Any],
        *,
        visitor_name: str,
        expected_time: str,
        window_minutes: int,
        actor_context: dict[str, Any] | None,
        provider_name: str,
        user_message: str,
        status_callback: StatusCallback | None,
    ) -> ChatTurnResult:
        call = ToolCall(
            "guided-create-visitor-pass",
            "create_visitor_pass",
            {
                "visitor_name": visitor_name,
                "expected_time": expected_time,
                "window_minutes": window_minutes,
                "confirm": False,
            },
        )
        tool_result = await self._execute_tool_call(session_id, call, status_callback=status_callback)
        output = tool_result.get("output") if isinstance(tool_result.get("output"), dict) else {}
        if not output.get("requires_confirmation"):
            memory.pop("pending_visitor_pass_create", None)
            await self._save_memory(session_id, memory)
            text = str(output.get("detail") or output.get("error") or "I could not prepare that Visitor Pass.")
            return await self._direct_response(session_id, text, tool_results=[tool_result])

        memory.pop("pending_visitor_pass_create", None)
        memory["last_visitor_name"] = visitor_name
        await self._save_memory(session_id, memory)
        route = IntentRoute(("Visitor_Passes",), 0.95, False, "guided visitor pass creation")
        selected_tools = [self._tools[name] for name in VISITOR_PASS_TOOL_NAMES if name in self._tools]
        pending_action = await self._store_pending_agent_action(
            session_id,
            tool_result,
            [tool_result],
            route,
            selected_tools,
            provider_name=provider_name,
            user_message=user_message,
            user_id=str(((actor_context or {}).get("user") or {}).get("id") or ""),
            actor_context=actor_context or {},
            iteration=0,
        )
        if status_callback:
            await status_callback({"event": "chat.confirmation_required", **pending_action})
        return await self._direct_response(
            session_id,
            str(output.get("detail") or f"Create a Visitor Pass for {visitor_name}?"),
            tool_results=[tool_result],
            pending_action=pending_action,
        )

    def _visitor_pass_missing_details_text(self, visitor_name: str | None, expected_time: str | None) -> str:
        if not visitor_name and not expected_time:
            return "Sure - who is visiting, and when should I expect them?"
        if not visitor_name:
            return "What is the visitor's name?"
        return f"What time should I expect {visitor_name}?"

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
        pending_action: dict[str, Any] | None = None,
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
        return ChatTurnResult(str(session_id), provider, text, tool_results, attachments, pending_action)

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
        cleaned = self._strip_local_time_labels(cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if not cleaned and any(attachment.get("kind") == "image" for attachment in attachments):
            return "Here's the latest snapshot."
        return cleaned

    def _strip_local_time_labels(self, text: str) -> str:
        cleaned = re.sub(r"\s*\((?:Europe/London)\)", "", text)
        cleaned = re.sub(r"\s+Europe/London\b", "", cleaned)
        return cleaned

    def _confirmation_user_message(self, tool_name: str, arguments: dict[str, Any]) -> str:
        target = (
            arguments.get("target")
            or arguments.get("visitor_name")
            or arguments.get("schedule_name")
            or arguments.get("rule_name")
            or arguments.get("name")
            or tool_name.replace("_", " ")
        )
        return f"Confirmed {tool_name.replace('_', ' ')} for {target}."

    def _confirmation_result_text(self, tool_name: str, output: dict[str, Any]) -> str:
        if output.get("error"):
            return str(output.get("detail") or output.get("error") or "I could not complete that action.")
        if tool_name in {"open_device", "command_device", "open_gate"}:
            device = output.get("device") if isinstance(output.get("device"), dict) else {}
            name = device.get("name") or output.get("target") or "the gate"
            action = "open" if tool_name == "open_gate" else str(output.get("action") or "open")
            past = "Opened" if action == "open" else "Closed"
            success = bool(output.get("opened") if action == "open" else output.get("closed"))
            return f"{past} {name}. This was logged as an Alfred action." if success else f"I could not {action} {name}."
        if tool_name == "override_schedule":
            if output.get("created"):
                return f"Created the temporary access override for {output.get('person') or 'that person'} until {output.get('ends_at_display') or output.get('ends_at')}."
            return str(output.get("detail") or "I did not create the schedule override.")
        if tool_name == "create_visitor_pass":
            if output.get("created"):
                visitor_pass = output.get("visitor_pass") if isinstance(output.get("visitor_pass"), dict) else {}
                return f"Created the Visitor Pass for {visitor_pass.get('visitor_name') or output.get('visitor_name') or 'that visitor'}."
            return str(output.get("detail") or output.get("error") or "I did not create the Visitor Pass.")
        if tool_name == "update_visitor_pass":
            if output.get("updated"):
                visitor_pass = output.get("visitor_pass") if isinstance(output.get("visitor_pass"), dict) else {}
                return f"Updated the Visitor Pass for {visitor_pass.get('visitor_name') or 'that visitor'}."
            return str(output.get("detail") or output.get("error") or "I did not update the Visitor Pass.")
        if tool_name == "cancel_visitor_pass":
            if output.get("cancelled"):
                visitor_pass = output.get("visitor_pass") if isinstance(output.get("visitor_pass"), dict) else {}
                return f"Cancelled the Visitor Pass for {visitor_pass.get('visitor_name') or 'that visitor'}."
            return str(output.get("detail") or output.get("error") or "I did not cancel the Visitor Pass.")
        if tool_name in {"toggle_maintenance_mode", "enable_maintenance_mode", "disable_maintenance_mode"}:
            if output.get("changed") or output.get("enabled") or output.get("disabled"):
                state = output.get("state") or ("enabled" if output.get("enabled") else "disabled")
                return f"Maintenance Mode is now {state}."
            return str(output.get("detail") or output.get("error") or "I did not change Maintenance Mode.")
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
        if tool_name in {"backfill_access_event_from_protect", "investigate_access_incident"}:
            if output.get("backfilled"):
                return (
                    f"Backfilled the {output.get('direction') or 'access'} event for "
                    f"{output.get('registration_number') or 'that plate'} at "
                    f"{output.get('occurred_at_display') or output.get('occurred_at')}. "
                    f"Presence {'was' if output.get('presence_updated') else 'was not'} updated."
                )
            return str(output.get("detail") or output.get("error") or "I did not backfill the access event.")
        if tool_name == "test_unifi_alarm_webhook":
            if output.get("sent"):
                return "Sent the UniFi Protect Alarm Manager webhook test and checked for a matching IACS webhook trace."
            return str(output.get("detail") or output.get("error") or "I did not send the UniFi Protect webhook test.")
        if tool_name == "trigger_anomaly_alert":
            if output.get("sent"):
                return f"Sent the anomaly alert: {output.get('title') or 'Alert'}."
            return str(output.get("detail") or output.get("error") or "I did not send the anomaly alert.")
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
        *,
        route: IntentRoute | None = None,
        actor_context: dict[str, Any] | None = None,
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

        history_rows = self._select_relevant_history(list(reversed(rows)), memory)
        route_payload = {
            "intents": list(route.intents) if route else ["General"],
            "confidence": route.confidence if route else 0.5,
            "requires_entity_resolution": route.requires_entity_resolution if route else True,
            "reason": route.reason if route else "default route",
            "source": route.source if route else "default",
        }
        tool_protocol = (
            REACT_TOOL_PROTOCOL
            .replace("{max_iterations}", str(MAX_AGENT_TOOL_ITERATIONS))
            .replace("{routing}", json.dumps(route_payload, separators=(",", ":"), default=str))
            .replace(
                "{tool_catalog}",
                json.dumps(
                    [tool.as_llm_tool() for tool in selected_tools],
                    separators=(",", ":"),
                ),
            )
        )
        prompt_actor_context = self._compact_prompt_value(actor_context or {})
        messages = [
            ChatMessageInput(
                "system",
                (
                    f"{SYSTEM_PROMPT}\n"
                    "All user-facing dates and times must use local site time; "
                    "never mention local-time names, local-time labels, UTC offsets, or UTC timestamps unless the user explicitly asks for UTC.\n"
                    f"Current authenticated user context: {json.dumps(prompt_actor_context, default=str, separators=(',', ':'))}\n"
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
                    "Observations for the current user request: "
                    f"{json.dumps(self._tool_results_for_prompt(tool_results), default=str, separators=(',', ':'))}",
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
        if memory.get("pending_schedule_create") or memory.get("pending_visitor_pass_create"):
            return rows[-MAX_RELEVANT_HISTORY_MESSAGES:]

        relevant_terms = self._history_terms(latest_content)
        for key in ("last_subject", "last_person", "last_group", "last_visitor_name"):
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
            visitor_passes = output.get("visitor_passes") if isinstance(output.get("visitor_passes"), list) else []
            visitor_pass = output.get("visitor_pass") if isinstance(output.get("visitor_pass"), dict) else None
            for pass_record in ([visitor_pass] if visitor_pass else []) + visitor_passes:
                if isinstance(pass_record, dict) and pass_record.get("visitor_name"):
                    memory["last_visitor_name"] = pass_record["visitor_name"]
                    memory["last_subject"] = pass_record["visitor_name"]
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

        if self._looks_like_device_action_request(lower):
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

        if self._looks_like_missing_access_incident(lower) or self._looks_like_access_diagnostic_request(lower):
            names.update(("diagnose_access_event", "investigate_access_incident", "query_access_events", "query_lpr_timing", "query_unifi_protect_events"))

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

        if self._looks_like_visitor_pass_request(lower) or memory.get("pending_visitor_pass_create") or memory.get("last_visitor_name"):
            names.update(VISITOR_PASS_TOOL_NAMES)

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
                if name in {"open_device", "command_device"}:
                    names.update(DEVICE_TOOL_NAMES)
                elif name == "trigger_manual_malfunction_override":
                    names.update(MALFUNCTION_TOOL_NAMES)
                elif name in MAINTENANCE_TOOL_NAMES:
                    names.update(MAINTENANCE_TOOL_NAMES)
                elif name in SCHEDULE_TOOL_NAMES:
                    names.update(SCHEDULE_TOOL_NAMES)
                elif name in VISITOR_PASS_TOOL_NAMES:
                    names.update(VISITOR_PASS_TOOL_NAMES)
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

        if self._looks_like_device_action_request(lower):
            action = self._device_action_from_message(lower)
            if action == "open" and "garage" not in lower and "open_gate" in self._tools:
                calls.append(
                    ToolCall(
                        "planned-open-gate",
                        "open_gate",
                        {
                            "target": self._device_target_from_message(lower) or "",
                            "reason": message,
                            "confirm": self._explicitly_confirmed_device_action(lower),
                        },
                    )
                )
            elif action == "close" and "command_device" in self._tools:
                calls.append(self._planned_device_action_call(message))
            else:
                calls.append(
                    ToolCall(
                        "planned-open-device",
                        "open_device",
                        {
                            "target": self._device_target_from_message(lower) or "",
                            "action": action,
                            "kind": "all",
                            "reason": message,
                            "confirm": self._explicitly_confirmed_device_action(lower),
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
                        "toggle_maintenance_mode",
                        {"state": "enabled", "reason": message, "confirm": self._is_confirmation_message(lower)},
                    )
                )
            elif any(word in lower for word in ["disable", "turn off", "deactivate", "stop", "resume automation"]):
                calls.append(
                    ToolCall(
                        "planned-disable-maintenance",
                        "toggle_maintenance_mode",
                        {"state": "disabled", "confirm": self._is_confirmation_message(lower)},
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

        if self._looks_like_missing_access_incident(lower):
            calls.append(
                ToolCall(
                    "planned-access-incident",
                    "investigate_access_incident",
                    self._access_incident_args_from_message(message, memory),
                )
            )
        elif self._looks_like_access_diagnostic_request(lower):
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
                "action": "open",
                "kind": "all",
                "reason": message,
                "confirm": self._explicitly_confirmed_device_open(lower),
            },
        )

    def _planned_device_action_call(self, message: str) -> ToolCall:
        lower = message.lower()
        action = self._device_action_from_message(lower)
        target = self._device_target_from_message(lower) or ""
        tool_name = "command_device" if "command_device" in self._tools else "open_device"
        return ToolCall(
            f"planned-{action}-device",
            tool_name,
            {
                "target": target,
                "action": action,
                "kind": "all",
                "reason": message,
                "confirm": self._explicitly_confirmed_device_action(lower),
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
        action = str(output.get("action") or "open")
        if output.get("requires_details"):
            return str(output.get("detail") or f"Which gate or garage door should I {action}?")
        if output.get("requires_confirmation"):
            return f"Please confirm before I {action} {name}."
        success = bool(output.get("opened") if action == "open" else output.get("closed"))
        if success:
            return f"{'Opened' if action == 'open' else 'Closed'} {name}. This was logged as an Alfred action."
        return str(output.get("detail") or output.get("error") or f"I could not {action} {name}.")

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

    def _subject_from_message(
        self,
        lower: str,
        memory: dict[str, Any],
        *,
        actor_context: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        actor_subject = self._actor_subject_from_message(lower, actor_context or {})
        if actor_subject:
            return actor_subject
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

    def _actor_subject_from_message(self, lower: str, actor_context: dict[str, Any]) -> dict[str, str]:
        person = actor_context.get("person") if isinstance(actor_context.get("person"), dict) else {}
        vehicles = actor_context.get("vehicles") if isinstance(actor_context.get("vehicles"), list) else []
        if re.search(r"\b(my car|my vehicle|my tesla)\b", lower) and vehicles:
            vehicle = next((item for item in vehicles if isinstance(item, dict)), None)
            if vehicle and vehicle.get("id"):
                return {"vehicle_id": str(vehicle["id"])}
            if vehicle and vehicle.get("registration_number"):
                return {"registration_number": str(vehicle["registration_number"])}
        if re.search(r"\b(me|myself|mine)\b", lower) and person.get("id"):
            return {"person_id": str(person["id"]), "person": str(person.get("display_name") or "")}
        return {}

    def _subject_args(self, subject: dict[str, str]) -> dict[str, str]:
        if "vehicle_id" in subject:
            return {"vehicle_id": subject["vehicle_id"]}
        if "person_id" in subject:
            return {"person_id": subject["person_id"]}
        if "registration_number" in subject:
            return {"registration_number": subject["registration_number"]}
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
            if re.fullmatch(r"AT\d{1,4}", candidate):
                continue
            if re.match(r"^\d{1,2}(?:AM|PM)", candidate):
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

    async def _execute_tool_batch(
        self,
        session_id: uuid.UUID,
        calls: list[ToolCall],
        selected_tools: list[AgentTool],
        *,
        status_callback: StatusCallback | None = None,
    ) -> list[dict[str, Any]]:
        tool_by_name = {tool.name: tool for tool in selected_tools}
        batch_id = f"batch-{uuid.uuid4().hex[:10]}"
        parallel = len(calls) > 1 and all(tool_by_name.get(call.name) and tool_by_name[call.name].read_only for call in calls)
        tools_payload = [
            {
                "call_id": call.id,
                "tool": call.name,
                "label": self._tool_status(call.name).get("label"),
            }
            for call in calls
        ]
        if status_callback:
            await status_callback(
                {
                    "event": "chat.tool_batch",
                    "batch_id": batch_id,
                    "status": "started",
                    "parallel": parallel,
                    "tools": tools_payload,
                }
            )
            for item in tools_payload:
                await status_callback({**item, "batch_id": batch_id, "status": "queued"})

        async def run(call: ToolCall) -> dict[str, Any]:
            try:
                try:
                    return await self._execute_tool_call(
                        session_id,
                        call,
                        status_callback=status_callback,
                        batch_id=batch_id,
                    )
                except TypeError as exc:
                    if "batch_id" not in str(exc):
                        raise
                    return await self._execute_tool_call(
                        session_id,
                        call,
                        status_callback=status_callback,
                    )
            except Exception as exc:
                logger.warning("agent_tool_failed", extra={"tool": call.name, "error": str(exc)[:240]})
                if status_callback:
                    await status_callback(
                        {
                            "batch_id": batch_id,
                            "call_id": call.id,
                            "tool": call.name,
                            "label": self._tool_status(call.name).get("label"),
                            "status": "failed",
                            "error": str(exc)[:240],
                        }
                    )
                return {
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "output": {"error": str(exc)[:500]},
                }

        if parallel:
            results = await asyncio.gather(*(run(call) for call in calls))
        else:
            results = []
            for call in calls:
                results.append(await run(call))

        if status_callback:
            await status_callback(
                {
                    "event": "chat.tool_batch",
                    "batch_id": batch_id,
                    "status": "completed",
                    "parallel": parallel,
                    "tools": tools_payload,
                }
            )
        return results

    async def _execute_tool_call(
        self,
        session_id: uuid.UUID,
        call: ToolCall,
        *,
        status_callback: StatusCallback | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        tool = self._tools.get(call.name)
        if not tool:
            return {
                "call_id": call.id,
                "name": call.name,
                "output": {"error": f"Unknown tool: {call.name}"},
            }
        if status_callback:
            await status_callback(
                {
                    **self._tool_status(call.name),
                    "batch_id": batch_id,
                    "call_id": call.id,
                    "status": "running",
                }
            )
        output = await tool.handler(call.arguments)
        await self._append_tool_message(session_id, call, output)
        self._audit_agent_tool_call(call, output)
        if status_callback:
            await status_callback(
                {
                    **self._tool_status(call.name),
                    "batch_id": batch_id,
                    "call_id": call.id,
                    "status": "requires_confirmation" if output.get("requires_confirmation") else "succeeded",
                }
            )
        return {"call_id": call.id, "name": call.name, "arguments": call.arguments, "output": output}

    async def _store_pending_agent_action(
        self,
        session_id: uuid.UUID,
        pending_result: dict[str, Any],
        tool_results: list[dict[str, Any]],
        route: IntentRoute,
        selected_tools: list[AgentTool],
        *,
        provider_name: str,
        user_message: str,
        user_id: str,
        actor_context: dict[str, Any],
        iteration: int,
    ) -> dict[str, Any]:
        confirmation_id = f"confirm-{uuid.uuid4().hex}"
        now = datetime.now(tz=UTC)
        pending = {
            "id": confirmation_id,
            "session_id": str(session_id),
            "tool_name": str(pending_result.get("name") or ""),
            "arguments": pending_result.get("arguments") if isinstance(pending_result.get("arguments"), dict) else {},
            "preview_output": pending_result.get("output") if isinstance(pending_result.get("output"), dict) else {},
            "tool_results": self._tool_results_for_prompt(tool_results),
            "route": {
                "intents": list(route.intents),
                "confidence": route.confidence,
                "requires_entity_resolution": route.requires_entity_resolution,
                "reason": route.reason,
                "source": route.source,
            },
            "selected_tools": [tool.name for tool in selected_tools],
            "provider": provider_name,
            "user_message": user_message,
            "user_id": user_id or None,
            "actor_context": actor_context,
            "iteration": iteration,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=10)).isoformat(),
        }
        memory = await self._load_memory(session_id)
        memory["pending_agent_action"] = pending
        await self._save_memory(session_id, memory)
        return self._pending_action_public_payload(pending)

    async def _pending_action_for_response(
        self,
        session_id: uuid.UUID,
        *,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        memory = await self._load_memory(session_id)
        pending = memory.get("pending_agent_action")
        if not isinstance(pending, dict):
            return None
        if user_id and pending.get("user_id") and str(pending.get("user_id")) != str(user_id):
            return None
        if self._pending_action_expired(pending):
            memory.pop("pending_agent_action", None)
            await self._save_memory(session_id, memory)
            return None
        return self._pending_action_public_payload(pending)

    async def _load_pending_agent_action(
        self,
        session_id: uuid.UUID,
        *,
        confirmation_id: str,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        memory = await self._load_memory(session_id)
        pending = memory.get("pending_agent_action")
        if not isinstance(pending, dict) or pending.get("id") != confirmation_id:
            return None
        if user_id and pending.get("user_id") and str(pending.get("user_id")) != str(user_id):
            return None
        if self._pending_action_expired(pending):
            memory.pop("pending_agent_action", None)
            await self._save_memory(session_id, memory)
            return None
        return pending

    async def _clear_pending_agent_action(self, session_id: uuid.UUID) -> None:
        memory = await self._load_memory(session_id)
        if "pending_agent_action" in memory:
            memory.pop("pending_agent_action", None)
            await self._save_memory(session_id, memory)

    def _pending_action_expired(self, pending: dict[str, Any]) -> bool:
        try:
            expires_at = datetime.fromisoformat(str(pending.get("expires_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= datetime.now(tz=UTC)

    def _pending_action_public_payload(self, pending: dict[str, Any]) -> dict[str, Any]:
        output = pending.get("preview_output") if isinstance(pending.get("preview_output"), dict) else {}
        tool_name = str(pending.get("tool_name") or "")
        target = str(
            output.get("target")
            or output.get("visitor_name")
            or output.get("schedule_name")
            or output.get("workflow_name")
            or output.get("person")
            or output.get("state")
            or tool_name.replace("_", " ")
        ).strip()
        title = self._confirmation_title(tool_name, target, output)
        description = self._strip_local_time_labels(
            str(output.get("detail") or "This action needs confirmation before Alfred continues.")
        )
        return {
            "confirmation_id": pending.get("id"),
            "session_id": pending.get("session_id"),
            "tool_name": tool_name,
            "title": title,
            "description": description,
            "confirm_label": self._confirmation_button_label(tool_name, output),
            "cancel_label": "Cancel",
            "risk_level": "high" if tool_name in {"open_device", "command_device", "open_gate"} else "medium",
            "target": target,
            "expires_at": pending.get("expires_at"),
        }

    def _confirmation_title(self, tool_name: str, target: str, output: dict[str, Any]) -> str:
        if tool_name in {"open_device", "command_device", "open_gate"}:
            action = "open" if tool_name == "open_gate" else str(output.get("action") or "open")
            return f"{'Close' if action == 'close' else 'Open'} {target or 'gate'}?"
        if tool_name == "override_schedule":
            return f"Override schedule for {target or 'person'}?"
        if tool_name == "create_visitor_pass":
            return f"Create Visitor Pass for {target or 'visitor'}?"
        if tool_name == "update_visitor_pass":
            return f"Update Visitor Pass for {target or 'visitor'}?"
        if tool_name == "cancel_visitor_pass":
            return f"Cancel Visitor Pass for {target or 'visitor'}?"
        if tool_name in {"toggle_maintenance_mode", "enable_maintenance_mode", "disable_maintenance_mode"}:
            state = str(output.get("state") or "").strip()
            return f"{'Enable' if state in {'enabled', 'on', 'true'} or tool_name == 'enable_maintenance_mode' else 'Disable'} Maintenance Mode?"
        return f"Confirm {target or tool_name.replace('_', ' ')}?"

    def _confirmation_button_label(self, tool_name: str, output: dict[str, Any] | None = None) -> str:
        if tool_name in {"open_device", "command_device", "open_gate"}:
            action = "open" if tool_name == "open_gate" else str((output or {}).get("action") or "open")
            return "Close" if action == "close" else "Open"
        if tool_name == "override_schedule":
            return "Create override"
        if tool_name == "create_visitor_pass":
            return "Create pass"
        if tool_name == "update_visitor_pass":
            return "Update pass"
        if tool_name == "cancel_visitor_pass":
            return "Cancel pass"
        if tool_name in {"backfill_access_event_from_protect", "investigate_access_incident"}:
            return "Backfill event"
        if tool_name == "test_unifi_alarm_webhook":
            return "Send test"
        if tool_name in {"toggle_maintenance_mode", "enable_maintenance_mode", "disable_maintenance_mode"}:
            return "Confirm"
        return "Confirm"

    def _confirmed_arguments_for_pending(self, pending: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(pending.get("arguments") if isinstance(pending.get("arguments"), dict) else {})
        output = pending.get("preview_output") if isinstance(pending.get("preview_output"), dict) else {}
        confirmation_field = str(output.get("confirmation_field") or "confirm")
        arguments[confirmation_field] = True
        return arguments

    def _route_from_pending(self, pending: dict[str, Any]) -> IntentRoute:
        route = pending.get("route") if isinstance(pending.get("route"), dict) else {}
        intents = tuple(
            intent
            for intent in (str(item) for item in route.get("intents", ["General"]))
            if intent in SUPPORTED_INTENTS
        ) or ("General",)
        try:
            confidence = float(route.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        return IntentRoute(
            intents=intents,
            confidence=max(0.0, min(1.0, confidence)),
            requires_entity_resolution=bool(route.get("requires_entity_resolution", False)),
            reason=str(route.get("reason") or "resumed pending action"),
            source=str(route.get("source") or "pending_action"),
        )

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
            "resolve_human_entity": "Resolving system entity...",
            "query_presence": "Checking presence logs...",
            "query_device_states": "Checking device states...",
            "open_device": "Preparing device command...",
            "command_device": "Preparing device command...",
            "open_gate": "Preparing gate open command...",
            "get_maintenance_status": "Checking Maintenance Mode...",
            "enable_maintenance_mode": "Preparing Maintenance Mode...",
            "disable_maintenance_mode": "Preparing Maintenance Mode...",
            "toggle_maintenance_mode": "Preparing Maintenance Mode...",
            "get_active_malfunctions": "Checking gate malfunction state...",
            "get_malfunction_history": "Reviewing gate malfunction history...",
            "trigger_manual_malfunction_override": "Preparing gate malfunction override...",
            "query_access_events": "Reviewing access events...",
            "diagnose_access_event": "Diagnosing access event...",
            "investigate_access_incident": "Investigating access incident...",
            "query_unifi_protect_events": "Checking UniFi Protect history...",
            "backfill_access_event_from_protect": "Preparing access event backfill...",
            "test_unifi_alarm_webhook": "Preparing Protect webhook test...",
            "query_lpr_timing": "Checking LPR timing...",
            "query_vehicle_detection_history": "Counting vehicle detections...",
            "get_telemetry_trace": "Reading telemetry trace...",
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
            "override_schedule": "Preparing schedule override...",
            "verify_schedule_access": "Verifying schedule access...",
            "query_notification_catalog": "Checking notification options...",
            "query_notification_workflows": "Checking notification workflows...",
            "get_notification_workflow": "Checking notification workflow...",
            "create_notification_workflow": "Preparing notification workflow...",
            "update_notification_workflow": "Preparing notification workflow update...",
            "delete_notification_workflow": "Preparing notification workflow deletion...",
            "preview_notification_workflow": "Previewing notification workflow...",
            "test_notification_workflow": "Preparing notification test...",
            "query_visitor_passes": "Checking Visitor Passes...",
            "get_visitor_pass": "Checking Visitor Pass...",
            "create_visitor_pass": "Preparing Visitor Pass...",
            "update_visitor_pass": "Preparing Visitor Pass update...",
            "cancel_visitor_pass": "Preparing Visitor Pass cancellation...",
        }
        return {"tool": tool_name, "label": labels.get(tool_name, "Running system tool...")}

    def _looks_like_visitor_pass_request(self, lower: str) -> bool:
        return (
            "visitor pass" in lower
            or "guest pass" in lower
            or bool(re.search(r"\b(?:create|make|add|book|set\s*up|setup)\s+(?:a\s+)?pass\b", lower))
            or bool(re.search(r"\bpass\s+(?:for|to)\b", lower))
            or bool(re.search(r"\bpass\b.*\b(?:coming|arriving|visiting|expected|tomorrow|today|tonight)\b", lower))
            or "visitor coming" in lower
            or "guest coming" in lower
            or "visitor arriving" in lower
            or "guest arriving" in lower
            or "expected visitor" in lower
            or "expected guest" in lower
            or bool(re.search(r"\b(visitor|guest)\b.*\b(pass|coming|arriving|expect|expected|cancel|delete|remove|revoke|visit)\b", lower))
        )

    def _looks_like_visitor_pass_create_request(self, lower: str) -> bool:
        if self._looks_like_visitor_pass_cancel_request(lower):
            return False
        return self._looks_like_visitor_pass_request(lower) and any(
            phrase in lower
            for phrase in [
                "coming",
                "arriving",
                "expecting",
                "expect ",
                "create",
                "new",
                "add",
                "book",
                "set up",
                "setup",
                "make",
                "coming over",
            ]
        )

    def _looks_like_visitor_pass_cancel_request(self, lower: str) -> bool:
        return self._looks_like_visitor_pass_request(lower) and bool(re.search(r"\b(cancel|delete|remove|revoke)\b", lower))

    def _visitor_name_from_message(self, message: str) -> str | None:
        patterns = [
            r"(?:called|named)\s+([A-Za-z][A-Za-z' -]{1,48})",
            r"(?:visitor|guest)\s+(?:called|named)\s+([A-Za-z][A-Za-z' -]{1,48})",
            r"(?:expecting|expect|having|have)\s+(?:a\s+)?(?:visitor|guest)?\s*(?:called|named)?\s+([A-Za-z][A-Za-z' -]{1,48})",
            r"\b([A-Za-z][A-Za-z' -]{1,48})\s+(?:is\s+|'s\s+)?(?:coming|arriving|visiting)\b",
            r"(?:visitor|guest)\s+([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight|coming|arriving)\b|$)",
            r"\bfor\s+([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight)\b|$)",
            r"^([A-Za-z][A-Za-z' -]{1,48})(?:\s+(?:at|on|tomorrow|today|tonight|in)\b|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if not match:
                continue
            cleaned = self._clean_visitor_name_candidate(match.group(1))
            if cleaned:
                return cleaned
        return None

    def _clean_visitor_name_candidate(self, value: str) -> str | None:
        name = re.split(
            r"\b(?:at|around|about|on|today|tomorrow|tonight|this|next|in|from|for|with|coming|arriving|visiting)\b",
            value,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        name = re.sub(r"\b(?:a|an|the|visitor|guest|is|will|be|called|named)\b", " ", name, flags=re.IGNORECASE)
        name = " ".join(name.strip(" .,!?'\"").split())
        if not name or name.lower() in {"coming", "arriving", "visiting", "today", "tomorrow", "tonight"}:
            return None
        return name[:80]

    def _visitor_expected_time_from_message(
        self,
        message: str,
        timezone_name: str = DEFAULT_CHAT_TIMEZONE,
    ) -> str | None:
        text = message.strip()
        iso_match = re.search(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?", text)
        if iso_match:
            raw = iso_match.group(0).replace(" ", "T")
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
                return parsed.astimezone(ZoneInfo(timezone_name)).isoformat()

        timezone = ZoneInfo(timezone_name)
        now = datetime.now(tz=timezone)
        lower = text.lower()
        relative = re.search(r"\bin\s+(\d{1,3})\s*(minute|minutes|mins|min|hour|hours|hrs|hr)\b", lower)
        if relative:
            amount = int(relative.group(1))
            unit = relative.group(2)
            delta = timedelta(hours=amount) if unit.startswith(("hour", "hr")) else timedelta(minutes=amount)
            return (now + delta).isoformat()

        time_match = re.search(
            r"\b(?:at|around|about|by)?\s*(?:approx(?:imately)?|roughly|circa|about|around)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
            lower,
        )
        if not time_match:
            time_match = re.search(
                r"\b(?:at|around|about|by)\s*(?:approx(?:imately)?|roughly|circa|about|around)?\s*(\d{1,2}):(\d{2})\b",
                lower,
            )
        if not time_match:
            if re.search(r"\b(noon|midday)\b", lower):
                hour, minute = 12, 0
            elif "midnight" in lower:
                hour, minute = 0, 0
            else:
                return None
        else:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            meridiem = time_match.group(3)
            if meridiem == "pm" and hour != 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            if hour > 23 or minute > 59:
                return None

        days_offset = 1 if "tomorrow" in lower else 0
        expected = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_offset)
        if days_offset == 0 and "today" not in lower and expected <= now:
            expected = expected + timedelta(days=1)
        return expected.isoformat()

    def _visitor_window_from_message(self, lower: str) -> int | None:
        match = re.search(r"(?:\+/-|plus/minus|window|within|for)\s*(\d{1,3})\s*(?:minutes|minute|mins|min|m)?", lower)
        if not match:
            return None
        minutes = int(match.group(1))
        if minutes in {30, 60, 90, 120, 180}:
            return minutes
        return max(1, min(minutes, 180))

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

    def _looks_like_device_action_request(self, lower: str) -> bool:
        return self._looks_like_device_open_request(lower) or self._looks_like_device_close_request(lower)

    def _looks_like_device_open_request(self, lower: str) -> bool:
        if not any(word in lower for word in ["gate", "garage", "door", "cover"]):
            return False
        if re.search(r"\b(?:is|are|was|were)\b[^?]*\bopen\b", lower):
            return False
        return bool(
            re.search(r"^\s*(?:please\s+)?(?:confirm(?:ed)?\s+)?open\s+(?:the\s+|my\s+)?", lower)
            or re.search(r"\b(?:can|could|would)\s+you\s+open\s+(?:the\s+|my\s+)?", lower)
        )

    def _looks_like_device_close_request(self, lower: str) -> bool:
        if not any(word in lower for word in ["garage", "door", "cover"]):
            return False
        if re.search(r"\b(?:is|are|was|were)\b[^?]*\bclosed?\b", lower):
            return False
        return bool(
            re.search(r"^\s*(?:please\s+)?(?:confirm(?:ed)?\s+)?(?:close|shut)\s+(?:the\s+|my\s+)?", lower)
            or re.search(r"\b(?:can|could|would)\s+you\s+(?:close|shut)\s+(?:the\s+|my\s+)?", lower)
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

    def _looks_like_missing_access_incident(self, lower: str) -> bool:
        phrases = [
            "nothing logged",
            "nothing was logged",
            "not logged",
            "didn't log",
            "didnt log",
            "did not log",
            "wasn't logged",
            "wasnt logged",
            "no event",
            "no access event",
            "no log",
            "missing event",
            "not recorded",
            "wasn't recorded",
            "wasnt recorded",
            "no notification",
            "not notified",
            "why wasn't",
            "why wasnt",
        ]
        if any(phrase in lower for phrase in phrases):
            return any(
                term in lower
                for term in ["left", "leave", "exit", "arrived", "arrival", "entry", "gate", "lpr", "plate", "car", "vehicle", "notification", "logged", "recorded"]
            )
        return False

    def _latest_diagnostic_result_not_found(self, tool_results: list[dict[str, Any]]) -> bool:
        for result in reversed(tool_results):
            if result.get("name") != "diagnose_access_event":
                continue
            output = result.get("output")
            return isinstance(output, dict) and not bool(output.get("found"))
        return False

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

    def _access_diagnostic_args_from_message(
        self,
        message: str,
        memory: dict[str, Any],
        *,
        actor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lower = message.lower()
        args: dict[str, Any] = {"day": self._day_from_message(lower)}
        actor_subject = self._actor_subject_from_message(lower, actor_context or {})
        args.update(self._subject_args(actor_subject))
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

    def _access_incident_args_from_message(
        self,
        message: str,
        memory: dict[str, Any],
        *,
        actor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lower = message.lower()
        args = self._access_diagnostic_args_from_message(message, memory, actor_context=actor_context)
        args.pop("span_limit", None)
        args.pop("summarize_payload", None)
        args["incident_type"] = self._incident_type_from_message(lower)
        if not any(key in args for key in ("person", "person_id", "vehicle_id", "registration_number")):
            person_name = self._person_name_from_event_time_message(lower)
            if person_name:
                args["person"] = person_name
        expected_time = self._expected_time_from_message(lower)
        if expected_time:
            args["expected_time"] = expected_time
            args["window_minutes"] = 20
        if "this morning" in lower or "today" in lower:
            args["day"] = "today"
        if "yesterday" in lower:
            args["day"] = "yesterday"
        return args

    def _incident_type_from_message(self, lower: str) -> str:
        if any(phrase in lower for phrase in ["nothing logged", "nothing was logged", "not logged", "didn't log", "didnt log", "no event", "missing event", "not recorded"]):
            return "missing_event"
        if "notification" in lower or "notified" in lower or "notify" in lower:
            return "notification_failure"
        if "garage" in lower:
            return "garage_failure"
        if "schedule" in lower or "denied" in lower or "outside" in lower:
            return "schedule_denial"
        if "gate" in lower and any(word in lower for word in ["open", "failed", "failure", "didn't", "didnt"]):
            return "gate_failure"
        return "auto"

    def _expected_time_from_message(self, lower: str) -> str | None:
        match = re.search(r"\b(?:at|around|about|by)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", lower)
        if not match:
            match = re.search(r"\b(\d{1,2}:\d{2}\s*(?:am|pm)?)\b", lower)
        if not match:
            return None
        return re.sub(r"\s+", "", match.group(1))

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
        return self._explicitly_confirmed_device_action(lower)

    def _explicitly_confirmed_device_action(self, lower: str) -> bool:
        return bool(
            re.search(r"\b(confirm|confirmed|authorise|authorize|approved|yes)\b", lower)
            and re.search(r"\b(open|close|shut)\b", lower)
        )

    def _device_action_from_message(self, lower: str) -> str:
        return "close" if self._looks_like_device_close_request(lower) else "open"

    def _device_target_from_message(self, lower: str) -> str | None:
        patterns = [
            r"(?:confirm|confirmed|authorise|authorize|approved|yes,?\s*)?\s*(?:open|close|shut)\s+(?:the\s+|my\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+please|\.|\?|$)",
            r"(?:state|status)\s+(?:of|for)\s+(?:the\s+)?([a-z0-9 _.-]{2,80})",
            r"(?:is|are|check)\s+(?:the\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+(?:open|closed|opening|closing|locked|unlocked)|\?|$)",
            r"(?:what(?:'s| is))\s+(?:the\s+)?([a-z0-9 _.-]*?(?:gate|door|garage)[a-z0-9 _.-]*?)(?:\s+(?:state|status|doing)|\?|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, lower)
            if match:
                target = re.sub(
                    r"\b(open|close|shut|closed|opening|closing|locked|unlocked|state|status|doing|please)\b",
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
        if tool_name == "resolve_human_entity":
            return self._entity_resolution_direct_text(output)
        if tool_name == "get_telemetry_trace":
            return self._telemetry_trace_direct_text(output)
        if tool_name in {"query_visitor_passes", "get_visitor_pass"}:
            passes = output.get("visitor_passes") if isinstance(output.get("visitor_passes"), list) else []
            if not passes and isinstance(output.get("visitor_pass"), dict):
                passes = [output["visitor_pass"]]
            if not passes:
                return str(output.get("error") or "I couldn't find any matching Visitor Passes.")
            visitor_pass = passes[0]
            name = visitor_pass.get("visitor_name") or "The visitor"
            if visitor_pass.get("vehicle_summary"):
                duration = f" {visitor_pass.get('visit_summary')}." if visitor_pass.get("visit_summary") else ""
                return f"{name} arrived in {visitor_pass.get('vehicle_summary')}.{duration}".strip()
            if visitor_pass.get("duration_human"):
                return f"{name} was on site for {visitor_pass.get('duration_human')}."
            return f"{name} has a {visitor_pass.get('status') or 'visitor'} pass for {visitor_pass.get('expected_time_display') or visitor_pass.get('expected_time')}."
        if tool_name in {"create_visitor_pass", "update_visitor_pass", "cancel_visitor_pass"}:
            if output.get("requires_confirmation"):
                return str(output.get("detail") or "That Visitor Pass change needs confirmation.")
            return self._confirmation_result_text(tool_name, output)
        incident_result = next(
            (
                result.get("output")
                for result in reversed(tool_results)
                if result.get("name") == "investigate_access_incident" and isinstance(result.get("output"), dict)
            ),
            None,
        )
        if isinstance(incident_result, dict):
            return self._access_incident_direct_text(incident_result)
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
        if tool_name in {"investigate_access_incident", "backfill_access_event_from_protect", "test_unifi_alarm_webhook"}:
            if output.get("requires_confirmation"):
                return str(output.get("detail") or "This needs confirmation before I change anything.")
            return self._confirmation_result_text(tool_name, output)
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
        if tool_name in {"open_device", "command_device", "open_gate"}:
            return self._device_open_direct_text(output)
        if tool_name in {"toggle_maintenance_mode", "enable_maintenance_mode", "disable_maintenance_mode"} and output.get("requires_confirmation"):
            return str(output.get("detail") or "Maintenance Mode needs confirmation before I change it.")
        if tool_name == "override_schedule":
            if output.get("requires_confirmation"):
                return str(output.get("detail") or "The temporary schedule override needs confirmation.")
            if output.get("created"):
                return f"Created the temporary schedule override until {output.get('ends_at_display') or output.get('ends_at')}."
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

    def _entity_resolution_direct_text(self, output: dict[str, Any]) -> str:
        if output.get("status") == "unique" and isinstance(output.get("match"), dict):
            match = output["match"]
            label = match.get("display_name") or match.get("name") or match.get("registration_number") or "that entity"
            return f"I resolved that to {label}."
        if output.get("status") == "ambiguous":
            matches = output.get("matches") if isinstance(output.get("matches"), list) else []
            labels = [
                str(match.get("display_name") or match.get("name") or match.get("registration_number") or "")
                for match in matches[:4]
                if isinstance(match, dict)
            ]
            return f"I found multiple possible matches: {', '.join(label for label in labels if label)}."
        return f"I could not resolve {output.get('query') or 'that reference'} to a known IACS entity."

    def _telemetry_trace_direct_text(self, output: dict[str, Any]) -> str:
        if not output.get("found"):
            return str(output.get("error") or "I could not find that telemetry trace.")
        trace = output.get("trace") if isinstance(output.get("trace"), dict) else {}
        trace_id = trace.get("trace_id") or "the trace"
        duration = trace.get("duration_ms")
        status = trace.get("status") or "unknown status"
        return f"{trace_id} finished with {status}{f' in {duration}ms' if duration is not None else ''}."

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

    def _access_incident_direct_text(self, output: dict[str, Any]) -> str:
        if output.get("backfilled"):
            return self._confirmation_result_text("backfill_access_event_from_protect", output)
        if output.get("error"):
            return str(output.get("detail") or output.get("error"))
        root = str(output.get("root_cause") or "unknown").replace("_", " ")
        confidence = output.get("confidence") or "unknown"
        iacs = "found an IACS access event" if output.get("found_iacs_event") else "found no matching IACS access event"
        protect = "Protect has matching evidence" if output.get("found_protect_event") else "Protect did not show a matching event"
        comparison = output.get("iacs_vs_protect") if isinstance(output.get("iacs_vs_protect"), dict) else {}
        action = output.get("recommended_action") if isinstance(output.get("recommended_action"), dict) else {}
        detail = str(output.get("detail") or action.get("summary") or "").strip()
        pieces = [
            f"I investigated the incident: IACS {iacs}; {protect}.",
            f"Likely root cause: {root} ({confidence} confidence).",
        ]
        if comparison.get("comparison"):
            pieces.append(str(comparison["comparison"]))
        if detail:
            pieces.append(detail)
        return " ".join(pieces)

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
