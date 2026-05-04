import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
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
from app.services.alfred.executor import can_execute_parallel
from app.services.alfred.feedback import alfred_feedback_service
from app.services.alfred.memory import alfred_memory_service
from app.services.alfred.permissions import filter_tools_for_actor, validate_tool_call
from app.services.alfred.planner import PlannerSelection, plan_with_llm, tools_for_selection
from app.services.alfred.runtime import agent_mode, agent_status_payload, provider_agent_capability
from app.services.alfred.streaming import emit_agent_state
from app.services.chat_attachments import ChatAttachmentError, chat_attachment_store
from app.services.chat_routing import ChatRoutingMixin
from app.services.chat_contracts import (
    CHAT_FILE_LINK_PATTERN,
    CHAT_FILE_URL_PATTERN,
    DEFAULT_AGENT_TOOL_TIMEOUT_SECONDS,
    DEFAULT_CHAT_TIMEZONE,
    INTENT_ROUTER_PROMPT,
    MAX_AGENT_TOOL_ITERATIONS,
    MAX_RELEVANT_HISTORY_MESSAGES,
    REACT_TOOL_PROTOCOL,
    RECENT_HISTORY_LIMIT,
    RELEVANT_HISTORY_SCAN_LIMIT,
    STATE_CHANGING_TOOL_NAMES,
    SUPPORTED_INTENTS,
    SYSTEM_PROMPT,
    VISITOR_PASS_TOOL_NAMES,
    ChatTurnResult,
    IntentRoute,
    IntentRouterError,
    StatusCallback,
)
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_ALFRED, TELEMETRY_CATEGORY_INTEGRATIONS, emit_audit_log, sanitize_payload

logger = get_logger(__name__)


class ChatService(ChatRoutingMixin):
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
        user_message_id = await self._append_message(
            session_uuid,
            "user",
            self._message_with_attachments(message, attachment_refs),
            tool_payload={
                "attachments": attachment_refs,
                "user_id": user_id,
                "source": (client_context or {}).get("source") or "dashboard",
            },
        )
        await event_bus.publish(
            "ai.phrase_received",
            {
                "phrase": message,
                "message": message,
                "session_id": str(session_uuid),
                "user_id": user_id,
                "source": "alfred",
            },
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
            if agent_mode(runtime) == "v3":
                return await self._handle_message_v3(
                    provider,
                    runtime,
                    session_uuid,
                    message,
                    memory,
                    attachment_refs,
                    actor_context,
                    user_message_id=user_message_id,
                    status_callback=status_callback,
                )

            tool_results: list[dict[str, Any]] = []
            try:
                route = await self._classify_intent(provider, message, memory, attachment_refs, actor_context=actor_context)
            except IntentRouterError as exc:
                logger.warning(
                    "intent_router_failed_closed",
                    extra={"provider": provider.name, "error": str(exc)[:240]},
                )
                return await self._provider_error_response(session_uuid, provider.name, exc, user_message_id=user_message_id)
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
                return await self._provider_error_response(session_uuid, provider.name, exc, user_message_id=user_message_id)
            except Exception as exc:
                logger.warning(
                    "llm_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                return await self._provider_error_response(session_uuid, provider.name, exc, user_message_id=user_message_id)
        finally:
            set_chat_tool_context({}, token=context_token)

        response_attachments = self._attachments_from_tool_results(tool_results)
        raw_text = result.text or self._fallback_text(tool_results)
        if self._should_replace_with_diagnostic_answer(message, raw_text, tool_results):
            raw_text = self._access_diagnostic_direct_text(self._diagnostic_output(tool_results) or {})
        text = self._clean_assistant_text(raw_text, response_attachments)
        assistant_message_id = await self._append_message(
            session_uuid,
            "assistant",
            text,
            tool_payload=self._assistant_turn_payload(
                session_uuid=session_uuid,
                user_message_id=user_message_id,
                user_message=message,
                assistant_text=text,
                provider=provider.name,
                model=self._model_for_provider(runtime, provider.name),
                tool_results=tool_results,
                attachments=response_attachments,
                actor_context=actor_context,
                route=route,
            ),
        )
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
            str(user_message_id),
            str(assistant_message_id),
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
            "That confirmation is no longer available. Ask me to prepare the action again and I'll lay out a fresh button.",
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
                "That confirmation has expired or was already handled. Sensible safety paperwork; ask me to prepare it again.",
            )

        tool_name = str(pending.get("tool_name") or "")
        if decision.strip().lower() != "confirm":
            await self._clear_pending_agent_action(session_uuid)
            user_message_id = await self._append_message(session_uuid, "user", f"Cancelled action {confirmation_id}")
            return await self._direct_response(
                session_uuid,
                "Okay, I cancelled that action. Nothing changed.",
                user_message_id=user_message_id,
            )

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
            user_message_id = await self._append_message(
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
        assistant_message_id = await self._append_message(
            session_uuid,
            "assistant",
            text,
            tool_payload=self._assistant_turn_payload(
                session_uuid=session_uuid,
                user_message_id=user_message_id,
                user_message=self._confirmation_user_message(tool_name, {"confirmation_id": confirmation_id}),
                assistant_text=text,
                provider=provider.name,
                model=self._model_for_provider(runtime, provider.name),
                tool_results=tool_results,
                attachments=attachments,
                actor_context=actor_context,
                route=self._route_from_pending(pending),
            ),
        )
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
            None,
            str(user_message_id),
            str(assistant_message_id),
        )

    def _confirmed_tool_finishes_without_resume(self, tool_name: str) -> bool:
        return tool_name in {
            "create_schedule",
            "update_schedule",
            "delete_schedule",
            "create_visitor_pass",
            "update_visitor_pass",
            "cancel_visitor_pass",
            "test_notification_workflow",
            "trigger_icloud_sync",
        }

    async def list_tools(self) -> list[dict[str, Any]]:
        return [tool.as_llm_tool() for tool in self._tools.values()]

    async def agent_status(self) -> dict[str, Any]:
        runtime = await get_runtime_config()
        return agent_status_payload(runtime, memory_status=await alfred_memory_service.status())

    async def list_memories(self, *, user_id: str, user_role: str) -> list[dict[str, Any]]:
        return await alfred_memory_service.list_user_memories(user_id=user_id, user_role=user_role)

    async def delete_memory(self, *, memory_id: str, user_id: str, user_role: str) -> bool:
        return await alfred_memory_service.delete_memory(
            memory_id=memory_id,
            user_id=user_id,
            user_role=user_role,
        )

    async def _build_actor_context(
        self,
        *,
        user_id: str | None,
        user_role: str | None,
        client_context: dict[str, Any],
    ) -> dict[str, Any]:
        runtime = await get_runtime_config()
        site_timezone = getattr(runtime, "site_timezone", DEFAULT_CHAT_TIMEZONE) or DEFAULT_CHAT_TIMEZONE
        now = datetime.now(tz=ZoneInfo(site_timezone))
        context: dict[str, Any] = {
            "site": {
                "label": getattr(runtime, "app_name", "IACS"),
                "timezone": site_timezone,
                "local_time": now.isoformat(),
                "location": "IACS private site",
            },
            "source_channel": str(client_context.get("source") or "dashboard"),
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
        if str(client_context.get("source") or "") == "messaging":
            context["messaging"] = {
                key: value
                for key, value in {
                    "provider": client_context.get("messaging_provider"),
                    "channel_id": client_context.get("provider_channel_id"),
                    "guild_id": client_context.get("provider_guild_id"),
                    "is_direct_message": client_context.get("is_direct_message"),
                }.items()
                if value not in {None, ""}
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

    async def _handle_message_v3(
        self,
        provider: Any,
        runtime: Any,
        session_uuid: uuid.UUID,
        message: str,
        memory: dict[str, Any],
        attachment_refs: list[dict[str, Any]],
        actor_context: dict[str, Any],
        *,
        user_message_id: uuid.UUID | None = None,
        status_callback: StatusCallback | None,
    ) -> ChatTurnResult:
        await emit_agent_state(status_callback, "understanding", "Understanding request")
        capability = provider_agent_capability(runtime, provider.name)
        if not capability["agent_capable"]:
            reason = capability.get("reason") or "provider_not_agent_capable"
            return await self._provider_error_response(
                session_uuid,
                provider.name,
                IntentRouterError(
                    "Alfred 3.0 requires a configured hosted LLM provider. "
                    f"Current provider is not agent-capable ({reason})."
                ),
                user_message_id=user_message_id,
            )

        user = actor_context.get("user") if isinstance(actor_context.get("user"), dict) else {}
        durable_memory = await alfred_memory_service.recall(
            user_id=str(user.get("id") or "") or None,
            user_role=str(user.get("role") or "") or None,
            session_id=str(session_uuid),
        )
        active_lessons = await alfred_feedback_service.recall_active_lessons(
            user_id=str(user.get("id") or "") or None,
            user_role=str(user.get("role") or "") or None,
        )
        planning_context = {**actor_context, "alfred_lessons": active_lessons}
        visible_tools = filter_tools_for_actor(self._tools.values(), actor_context)

        await emit_agent_state(status_callback, "selecting_tools", "Selecting tools")
        try:
            selection = await plan_with_llm(
                provider,
                message=message,
                actor_context=planning_context,
                memories=durable_memory,
                session_memory=memory,
                tools=visible_tools,
                attachments=attachment_refs,
            )
        except Exception as exc:
            logger.warning(
                "alfred_v3_planner_failed_closed",
                extra={"provider": provider.name, "error": str(exc)[:240]},
            )
            return await self._provider_error_response(session_uuid, provider.name, exc, user_message_id=user_message_id)

        if selection.needs_clarification:
            text = self._clean_assistant_text(
                selection.clarification_question or "I need one detail before I can do that safely. What should I focus on?",
                [],
            )
            assistant_message_id = await self._append_message(
                session_uuid,
                "assistant",
                text,
                tool_payload=self._assistant_turn_payload(
                    session_uuid=session_uuid,
                    user_message_id=user_message_id,
                    user_message=message,
                    assistant_text=text,
                    provider=provider.name,
                    model=self._model_for_provider(runtime, provider.name),
                    tool_results=[],
                    attachments=[],
                    actor_context=actor_context,
                    route=None,
                ),
            )
            await event_bus.publish(
                "chat.message",
                {"session_id": str(session_uuid), "provider": provider.name, "text": text, "attachments": []},
            )
            return ChatTurnResult(
                str(session_uuid),
                provider.name,
                text,
                [],
                [],
                None,
                str(user_message_id) if user_message_id else None,
                str(assistant_message_id),
            )

        selected_tools = tools_for_selection(selection, visible_tools)
        tool_labels = ", ".join(self._tool_status(tool.name).get("label", tool.name) for tool in selected_tools[:3])
        await emit_agent_state(
            status_callback,
            "tools_selected",
            "Working with selected IACS tools",
            detail=tool_labels,
        )
        route = IntentRoute(
            intents=tuple(selection.selected_domains or ("General",)),
            confidence=selection.confidence,
            requires_entity_resolution=self._selection_requires_entity_resolution(selection, selected_tools),
            reason=selection.reason,
            source="alfred_v3_planner",
        )
        tool_results: list[dict[str, Any]] = []
        prompt_context = {
            **actor_context,
            "alfred_memory": durable_memory,
            "alfred_lessons": active_lessons,
            "alfred_plan": {
                "selected_domains": list(selection.selected_domains),
                "selected_tool_names": [tool.name for tool in selected_tools],
                "safety_posture": selection.safety_posture,
                "reason": selection.reason,
            },
        }
        messages = await self._build_agent_messages(
            session_uuid,
            tool_results,
            selected_tools,
            route,
            actor_context=prompt_context,
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
            return await self._provider_error_response(session_uuid, provider.name, exc, user_message_id=user_message_id)
        except Exception as exc:
            logger.warning(
                "llm_provider_failed",
                extra={"provider": provider.name, "error": str(exc)},
            )
            return await self._provider_error_response(session_uuid, provider.name, exc, user_message_id=user_message_id)

        await emit_agent_state(status_callback, "composing", "Composing answer")
        response_attachments = self._attachments_from_tool_results(tool_results)
        raw_text = result.text or self._fallback_text(tool_results)
        if self._should_replace_with_diagnostic_answer(message, raw_text, tool_results):
            raw_text = self._access_diagnostic_direct_text(self._diagnostic_output(tool_results) or {})
        text = self._clean_assistant_text(raw_text, response_attachments)
        assistant_message_id = await self._append_message(
            session_uuid,
            "assistant",
            text,
            tool_payload=self._assistant_turn_payload(
                session_uuid=session_uuid,
                user_message_id=user_message_id,
                user_message=message,
                assistant_text=text,
                provider=provider.name,
                model=self._model_for_provider(runtime, provider.name),
                tool_results=tool_results,
                attachments=response_attachments,
                actor_context=actor_context,
                route=route,
            ),
        )
        await self._update_memory(session_uuid, message, tool_results)
        pending_action = await self._pending_action_for_response(
            session_uuid,
            user_id=str(user.get("id") or "") or None,
        )
        if not pending_action:
            await alfred_memory_service.remember_from_turn(
                provider,
                user_message=message,
                assistant_text=text,
                tool_results=tool_results,
                actor_context=actor_context,
                session_id=str(session_uuid),
            )
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
            pending_action,
            str(user_message_id) if user_message_id else None,
            str(assistant_message_id),
        )

    def _selection_requires_entity_resolution(
        self,
        selection: PlannerSelection,
        selected_tools: list[AgentTool],
    ) -> bool:
        if "resolve_human_entity" in {tool.name for tool in selected_tools}:
            return True
        return bool(selection.raw.get("requires_entity_resolution"))

    async def _classify_intent(
        self,
        provider: Any,
        message: str,
        memory: dict[str, Any],
        attachments: list[dict[str, Any]],
        *,
        actor_context: dict[str, Any] | None = None,
    ) -> IntentRoute:
        if provider.name == "local":
            raise IntentRouterError("The local provider cannot classify free-form chat intent.")

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
                "intent_router_request_failed",
                extra={"provider": getattr(provider, "name", "unknown"), "error": str(exc)[:240]},
            )
            raise IntentRouterError(f"Intent router request failed: {self._safe_provider_error(exc)}") from exc

        payload = self._extract_tool_call_payload(result.text)
        if not isinstance(payload, dict):
            raise IntentRouterError("Intent router returned an invalid response.")
        raw_intents = payload.get("intents")
        intents = tuple(
            intent
            for intent in (str(item).strip() for item in raw_intents or [])
            if intent in SUPPORTED_INTENTS
        )
        if not intents:
            raise IntentRouterError("Intent router returned no supported intents.")
        if "requires_entity_resolution" not in payload:
            raise IntentRouterError("Intent router omitted entity-resolution guidance.")
        try:
            confidence = max(0.0, min(1.0, float(payload.get("confidence"))))
        except (TypeError, ValueError):
            confidence = 0.5
        return IntentRoute(
            intents=intents,
            confidence=confidence,
            requires_entity_resolution=bool(payload.get("requires_entity_resolution")),
            reason=str(payload.get("reason") or "LLM intent route")[:240],
            source=f"{provider.name}_classifier",
        )



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
            tool_by_name = {tool.name: tool for tool in selected_tools}
            for call in calls:
                denial = validate_tool_call(
                    call.name,
                    selected_tool_names=allowed_tool_names,
                    tools_by_name=self._tools,
                    actor_context=actor_context,
                )
                if denial:
                    return LlmResult(
                        text=(
                            f"I could not safely use {call.name or 'that tool'} for this request. "
                            "Please rephrase the request or ask an Admin to handle restricted actions."
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
                parallel_preview_calls = action_calls if can_execute_parallel(action_calls, tool_by_name) else []
                if parallel_preview_calls:
                    native_results.extend(
                        await self._execute_tool_batch(
                            session_id,
                            parallel_preview_calls,
                            selected_tools,
                            status_callback=status_callback,
                            force_parallel=True,
                        )
                    )
                    action_calls = []
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
                "I hit my five-step safety limit while checking this, so I'm stopping before I start inventing plot twists. "
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
        return await self._build_messages(
            session_id,
            tool_results,
            selected_tools,
            route=route,
            actor_context=actor_context,
        )

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

        return None

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
    ) -> ChatTurnResult | None:
        lower = message.lower().strip()
        if self._is_pending_visitor_pass_create_cancel_message(lower):
            memory.pop("pending_visitor_pass_create", None)
            await self._save_memory(session_id, memory)
            return await self._direct_response(session_id, "No problem - I cancelled that Visitor Pass setup.")
        if self._looks_like_visitor_pass_cancel_request(lower) or self._should_abandon_pending_visitor_pass_create(lower):
            memory.pop("pending_visitor_pass_create", None)
            await self._save_memory(session_id, memory)
            return None

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

        return None

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
        user_message_id: uuid.UUID | None = None,
    ) -> ChatTurnResult:
        tool_results = tool_results or []
        attachments = self._attachments_from_tool_results(tool_results)
        text = self._clean_assistant_text(text, attachments)
        assistant_message_id = await self._append_message(
            session_id,
            "assistant",
            text,
            tool_payload=self._assistant_turn_payload(
                session_uuid=session_id,
                user_message_id=user_message_id,
                user_message="",
                assistant_text=text,
                provider=provider,
                model=None,
                tool_results=tool_results,
                attachments=attachments,
                actor_context={},
                route=None,
            ),
        )
        await event_bus.publish(
            "chat.message",
            {
                "session_id": str(session_id),
                "provider": provider,
                "text": text,
                "attachments": attachments,
            },
        )
        return ChatTurnResult(
            str(session_id),
            provider,
            text,
            tool_results,
            attachments,
            pending_action,
            str(user_message_id) if user_message_id else None,
            str(assistant_message_id),
        )

    async def _provider_error_response(
        self,
        session_id: uuid.UUID,
        provider_name: str,
        exc: Exception,
        *,
        user_message_id: uuid.UUID | None = None,
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
            user_message_id=user_message_id,
        )

    def _assistant_turn_payload(
        self,
        *,
        session_uuid: uuid.UUID,
        user_message_id: uuid.UUID | None,
        user_message: str,
        assistant_text: str,
        provider: str,
        model: str | None,
        tool_results: list[dict[str, Any]],
        attachments: list[dict[str, Any]],
        actor_context: dict[str, Any],
        route: IntentRoute | None,
    ) -> dict[str, Any]:
        route_payload: dict[str, Any] | None = None
        if route:
            route_payload = {
                "intents": list(route.intents),
                "confidence": route.confidence,
                "requires_entity_resolution": route.requires_entity_resolution,
                "reason": route.reason,
                "source": route.source,
            }
        snapshot = {
            "session_id": str(session_uuid),
            "user_message_id": str(user_message_id) if user_message_id else None,
            "user_message": user_message,
            "assistant_response": assistant_text,
            "provider": provider,
            "model": model,
            "tool_results": tool_results,
            "attachments": attachments,
            "actor_context": actor_context,
            "route": route_payload,
            "captured_at": datetime.now(tz=UTC).isoformat(),
        }
        sanitized = sanitize_payload(snapshot)
        return {
            "provider": provider,
            "model": model,
            "user_message_id": str(user_message_id) if user_message_id else None,
            "turn_snapshot": sanitized,
        }

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
        cleaned = self._strip_redundant_precise_time(cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if not cleaned and any(attachment.get("kind") == "image" for attachment in attachments):
            return "Here's the latest snapshot."
        return cleaned

    def _strip_local_time_labels(self, text: str) -> str:
        cleaned = re.sub(r"\s*\((?:Europe/London)\)", "", text)
        cleaned = re.sub(r"\s+Europe/London\b", "", cleaned)
        return cleaned

    def _strip_redundant_precise_time(self, text: str) -> str:
        return re.sub(r"\b(\d{1,2}:\d{2})\s*\(\1:\d{2}(?:\.\d+)?\)", r"\1", text)

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
            return f"{past} {name}. Logged, tidy, and pleasingly uneventful." if success else f"I could not {action} {name}."
        if tool_name == "override_schedule":
            if output.get("created"):
                return f"Created the temporary access override for {output.get('person') or 'that person'} until {output.get('ends_at_display') or output.get('ends_at')}."
            return str(output.get("detail") or "I did not create the schedule override.")
        if tool_name == "create_schedule":
            schedule = output.get("schedule") if isinstance(output.get("schedule"), dict) else {}
            name = schedule.get("name") or output.get("schedule_name") or "the schedule"
            summary = schedule.get("summary")
            return f"Created {name}{f' with {summary}' if summary else ''}." if output.get("created") else str(output.get("detail") or f"I did not create {name}.")
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
        if tool_name == "trigger_icloud_sync":
            if output.get("synced"):
                return (
                    "iCloud Calendar sync finished: "
                    f"{output.get('events_matched', 0)} Open Gate events matched, "
                    f"{output.get('passes_created', 0)} passes created, "
                    f"{output.get('passes_updated', 0)} updated, "
                    f"{output.get('passes_cancelled', 0)} cancelled."
                )
            return str(output.get("detail") or output.get("error") or "I did not sync iCloud Calendar.")
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
            return f"Created notification workflow {workflow.get('name') or output.get('workflow_name') or ''}. Neatly filed.".strip()
        if tool_name == "update_notification_workflow":
            workflow = output.get("workflow") if isinstance(output.get("workflow"), dict) else {}
            return f"Updated notification workflow {workflow.get('name') or output.get('workflow_name') or ''}.".strip()
        if tool_name == "delete_notification_workflow":
            workflow = output.get("workflow") if isinstance(output.get("workflow"), dict) else {}
            return f"Deleted notification workflow {workflow.get('name') or output.get('workflow_name') or ''}.".strip()
        if tool_name == "test_notification_workflow":
            if output.get("sent"):
                return "Sent the notification workflow test. Tiny paper plane launched."
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
    ) -> uuid.UUID:
        async with AsyncSessionLocal() as session:
            row = ChatMessage(
                session_id=session_id,
                role=role,
                content=content,
                tool_name=tool_name,
                tool_payload=tool_payload,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id

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



























    async def _execute_tool_batch(
        self,
        session_id: uuid.UUID,
        calls: list[ToolCall],
        selected_tools: list[AgentTool],
        *,
        status_callback: StatusCallback | None = None,
        force_parallel: bool = False,
    ) -> list[dict[str, Any]]:
        tool_by_name = {tool.name: tool for tool in selected_tools}
        batch_id = f"batch-{uuid.uuid4().hex[:10]}"
        parallel = force_parallel or (
            len(calls) > 1 and all(tool_by_name.get(call.name) and tool_by_name[call.name].read_only for call in calls)
        )
        runtime = await get_runtime_config()
        tool_timeout = max(0.1, float(getattr(runtime, "llm_timeout_seconds", DEFAULT_AGENT_TOOL_TIMEOUT_SECONDS) or DEFAULT_AGENT_TOOL_TIMEOUT_SECONDS))
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
                return await asyncio.wait_for(
                    self._execute_tool_call(
                        session_id,
                        call,
                        status_callback=status_callback,
                        batch_id=batch_id,
                    ),
                    timeout=tool_timeout,
                )
            except asyncio.TimeoutError:
                message = f"Timed out after {tool_timeout:g} seconds."
                logger.warning("agent_tool_timed_out", extra={"tool": call.name, "timeout_seconds": tool_timeout})
                if status_callback:
                    await status_callback(
                        {
                            "batch_id": batch_id,
                            "call_id": call.id,
                            "tool": call.name,
                            "label": self._tool_status(call.name).get("label"),
                            "status": "failed",
                            "error": message,
                        }
                    )
                return {
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "output": {"error": message},
                }
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
        if tool_name == "create_schedule":
            return f"Create schedule {target or ''}?".strip()
        if tool_name == "create_visitor_pass":
            return f"Create Visitor Pass for {target or 'visitor'}?"
        if tool_name == "update_visitor_pass":
            return f"Update Visitor Pass for {target or 'visitor'}?"
        if tool_name == "cancel_visitor_pass":
            return f"Cancel Visitor Pass for {target or 'visitor'}?"
        if tool_name == "trigger_icloud_sync":
            return "Sync iCloud Calendar?"
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
        if tool_name == "create_schedule":
            return "Create schedule"
        if tool_name == "create_visitor_pass":
            return "Create pass"
        if tool_name == "update_visitor_pass":
            return "Update pass"
        if tool_name == "cancel_visitor_pass":
            return "Cancel pass"
        if tool_name == "trigger_icloud_sync":
            return "Sync calendars"
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
            "query_integration_health": "Checking integration health...",
            "test_integration_connection": "Preparing integration test...",
            "query_system_settings": "Reading redacted settings...",
            "update_system_settings": "Preparing settings update...",
            "query_auth_secret_status": "Checking auth-secret status...",
            "rotate_auth_secret": "Preparing auth-secret rotation...",
            "query_dependency_updates": "Checking dependency update state...",
            "check_dependency_updates": "Preparing dependency update check...",
            "analyze_dependency_update": "Preparing dependency analysis...",
            "apply_dependency_update": "Preparing dependency apply job...",
            "query_dependency_backups": "Checking dependency backups...",
            "restore_dependency_backup": "Preparing dependency restore job...",
            "query_dependency_update_job": "Checking dependency job...",
            "configure_dependency_backup_storage": "Preparing backup storage update...",
            "validate_dependency_backup_storage": "Preparing backup storage validation...",
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
            "trigger_icloud_sync": "Preparing iCloud Calendar sync...",
        }
        return {"tool": tool_name, "label": labels.get(tool_name, "Running system tool...")}

















































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
            return "I don't have live system context for that yet. Point me at a person, vehicle, gate, or schedule and I'll take it from there."
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
                return str(output.get("error") or "I couldn't find any matching Visitor Passes. The visitor ledger is politely blank.")
            visitor_pass = passes[0]
            name = visitor_pass.get("visitor_name") or "The visitor"
            arrival_time = self._chat_time_from_iso(str(visitor_pass.get("arrival_time") or ""))
            if arrival_time:
                return f"{name} arrived at {arrival_time}."
            departure_time = self._chat_time_from_iso(str(visitor_pass.get("departure_time") or ""))
            if departure_time:
                return f"{name} left at {departure_time}."
            if visitor_pass.get("vehicle_summary"):
                duration = f" {visitor_pass.get('visit_summary')}." if visitor_pass.get("visit_summary") else ""
                return f"{name} arrived in {visitor_pass.get('vehicle_summary')}.{duration}".strip()
            if visitor_pass.get("duration_human"):
                return f"{name} was on site for {visitor_pass.get('duration_human')}."
            return f"{name} has a {visitor_pass.get('status') or 'visitor'} pass for {visitor_pass.get('expected_time_display') or visitor_pass.get('expected_time')}."
        if tool_name in {"create_visitor_pass", "update_visitor_pass", "cancel_visitor_pass"}:
            if output.get("requires_confirmation"):
                return str(output.get("detail") or "That Visitor Pass change needs confirmation first. Sensible paperwork, not theatrics.")
            return self._confirmation_result_text(tool_name, output)
        if tool_name == "trigger_icloud_sync":
            if output.get("requires_confirmation"):
                return str(output.get("detail") or "That calendar sync needs confirmation first. Calendars are small chaos engines; I prefer a button press.")
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
                return "I couldn't find any matching recent access events. The logbook is politely blank."
            event = events[0]
            person_name = event.get("person") or event.get("registration_number") or "The matched subject"
            verb = "left" if event.get("direction") == "exit" else "arrived"
            occurred_at = self._chat_time_from_iso(str(event.get("occurred_at") or ""))
            return f"{person_name} {verb} at {occurred_at}." if occurred_at else f"{person_name} {verb} recently."
        if tool_name == "diagnose_access_event":
            if not output.get("found"):
                return str(output.get("error") or "I could not find a matching access event to diagnose. I checked the usual cupboards; next stop is incident investigation.")
            hints = output.get("answer_hints") if isinstance(output.get("answer_hints"), list) else []
            return " ".join(str(hint) for hint in hints[:3] if hint) or "I found the diagnostic record."
        if tool_name in {"investigate_access_incident", "backfill_access_event_from_protect", "test_unifi_alarm_webhook"}:
            if output.get("requires_confirmation"):
                return str(output.get("detail") or "This needs confirmation before I change anything. Safety first; dramatic button second.")
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
                return "I couldn't find a matching configured device. No labelled lever for that one, alas."
            return "; ".join(
                f"{device.get('name') or 'Device'} is {device.get('state') or 'unknown'}"
                for device in devices[:5]
            )
        if tool_name in {"open_device", "command_device", "open_gate"}:
            return self._device_open_direct_text(output)
        if tool_name in {"toggle_maintenance_mode", "enable_maintenance_mode", "disable_maintenance_mode"} and output.get("requires_confirmation"):
            return str(output.get("detail") or "Maintenance Mode needs confirmation before I change it. That switch deserves a proper nod.")
        if tool_name == "override_schedule":
            if output.get("requires_confirmation"):
                return str(output.get("detail") or "The temporary schedule override needs confirmation. A tiny calendar exception, properly witnessed.")
            if output.get("created"):
                return f"Created the temporary schedule override until {output.get('ends_at_display') or output.get('ends_at')}."
        if tool_name == "query_leaderboard":
            top = output.get("top_known") if isinstance(output.get("top_known"), dict) else None
            known = output.get("known") if isinstance(output.get("known"), list) else []
            unknown = output.get("unknown") if isinstance(output.get("unknown"), list) else []
            if top:
                return (
                    f"{top.get('display_name') or top.get('registration_number')} is leading Top Charts "
                    f"with {top.get('read_count')} detections. Very much the driveway headliner."
                )
            if known:
                first = known[0]
                return f"{first.get('display_name') or first.get('registration_number')} leads the VIP Lounge."
            if unknown:
                first = unknown[0]
                return f"{first.get('registration_number')} leads the Mystery Guests list."
            return "I found no leaderboard entries yet. The podium is spotless."
        if tool_name == "delete_schedule":
            return self._schedule_delete_direct_text(output)
        return json.dumps([result["output"] for result in tool_results], default=str)

    def _entity_resolution_direct_text(self, output: dict[str, Any]) -> str:
        if output.get("status") == "unique" and isinstance(output.get("match"), dict):
            match = output["match"]
            if match.get("type") == "visitor_pass":
                name = match.get("visitor_name") or match.get("display_name") or "that visitor"
                arrival_time = self._chat_time_from_iso(str(match.get("arrival_time") or ""))
                if arrival_time:
                    return f"{name}'s Visitor Pass shows arrival at {arrival_time}."
                departure_time = self._chat_time_from_iso(str(match.get("departure_time") or ""))
                if departure_time:
                    return f"{name}'s Visitor Pass shows departure at {departure_time}."
                status = match.get("status") or "visitor"
                expected = match.get("expected_time_display") or match.get("expected_time")
                if expected:
                    return f"{name} has a {status} Visitor Pass for {expected}."
                return f"I found {name}'s Visitor Pass, but no arrival is recorded yet."
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
