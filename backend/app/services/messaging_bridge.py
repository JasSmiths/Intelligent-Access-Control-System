from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import MessagingIdentity, Person, User
from app.models.enums import UserRole
from app.modules.messaging.base import IncomingChatMessage, MessagingActor, MessagingBridgeResult
from app.services.chat import chat_service

logger = get_logger(__name__)

MESSAGING_SESSION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "iacs.messaging.sessions")


class MessagingBridgeService:
    async def handle_message(
        self,
        message: IncomingChatMessage,
        *,
        is_admin_hint: bool = False,
    ) -> MessagingBridgeResult:
        actor = await self.resolve_actor(message, is_admin_hint=is_admin_hint)
        session_id = deterministic_session_id(message)
        result = await chat_service.handle_message(
            message.text,
            session_id=session_id,
            user_id=actor.user_id,
            user_role=actor.user_role,
            client_context={
                "source": "messaging",
                "messaging_provider": message.provider,
                "provider_channel_id": message.provider_channel_id,
                "provider_guild_id": message.provider_guild_id,
                "is_direct_message": message.is_direct_message,
                "author_display_name": message.author_display_name,
            },
        )
        response_text = naturalize_messaging_response(result.text, result.tool_results, message.text)
        logger.info(
            "messaging_message_routed",
            extra={
                "provider": message.provider,
                "provider_channel_id": message.provider_channel_id,
                "author_provider_id": message.author_provider_id,
                "session_id": result.session_id,
                "pending_confirmation": bool(result.pending_action),
            },
        )
        return MessagingBridgeResult(
            session_id=result.session_id,
            response_text=response_text,
            pending_action=result.pending_action,
            actor=actor,
        )

    async def resolve_actor(
        self,
        message: IncomingChatMessage,
        *,
        is_admin_hint: bool = False,
    ) -> MessagingActor:
        now = datetime.now(tz=UTC)
        async with AsyncSessionLocal() as session:
            identity = await session.scalar(
                select(MessagingIdentity)
                .options(
                    selectinload(MessagingIdentity.user),
                    selectinload(MessagingIdentity.person),
                )
                .where(MessagingIdentity.provider == message.provider)
                .where(MessagingIdentity.provider_user_id == message.author_provider_id)
            )
            if not identity:
                identity = MessagingIdentity(
                    provider=message.provider,
                    provider_user_id=message.author_provider_id,
                    provider_display_name=message.author_display_name,
                    last_seen_at=now,
                    metadata_={},
                )
                session.add(identity)
            identity.provider_display_name = message.author_display_name
            identity.last_seen_at = now
            identity.metadata_ = {
                **(identity.metadata_ or {}),
                "last_channel_id": message.provider_channel_id,
                "last_guild_id": message.provider_guild_id,
                "last_role_ids": message.author_role_ids,
                "last_provider_admin": message.author_is_provider_admin,
            }
            await session.commit()

            user: User | None = identity.user
            person: Person | None = identity.person
            user_role = user.role.value if user and user.is_active else ("admin" if is_admin_hint else "standard")
            return MessagingActor(
                provider=message.provider,
                provider_user_id=message.author_provider_id,
                display_name=message.author_display_name,
                user_id=str(user.id) if user and user.is_active else None,
                user_role=user_role,
                person_id=str(person.id) if person else str(user.person_id) if user and user.person_id else None,
                is_admin=bool(is_admin_hint or (user and user.is_active and user.role == UserRole.ADMIN)),
            )


def deterministic_session_id(message: IncomingChatMessage) -> str:
    conversation_key = (
        f"dm:{message.author_provider_id}"
        if message.is_direct_message
        else f"guild:{message.provider_guild_id or 'unknown'}:channel:{message.provider_channel_id}"
    )
    return str(uuid.uuid5(MESSAGING_SESSION_NAMESPACE, f"{message.provider}:{conversation_key}"))


def naturalize_messaging_response(
    text: str,
    tool_results: list[dict[str, Any]] | None,
    prompt: str | None = None,
) -> str:
    """Keep provider chat replies from leaking raw tool JSON."""

    stripped = str(text or "").strip()
    parsed_payload = _parse_json_payload(stripped)
    if stripped and parsed_payload is None:
        return stripped

    outputs = _tool_outputs(tool_results or [], parsed_payload)
    summary = _summarize_messaging_outputs(outputs, prompt or "")
    if summary:
        logger.info(
            "messaging_response_naturalized",
            extra={"tool_count": len(outputs), "raw_json_response": bool(parsed_payload is not None)},
        )
        return summary
    if not stripped:
        return "I checked IACS, but there was no displayable response. The tray came back empty."
    return "I checked IACS, but Alfred returned structured data instead of a chat-ready answer. Too many cogs showing."


def _parse_json_payload(text: str) -> Any | None:
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _tool_outputs(tool_results: list[dict[str, Any]], parsed_payload: Any | None) -> list[tuple[str, dict[str, Any]]]:
    outputs: list[tuple[str, dict[str, Any]]] = []
    for result in tool_results:
        output = result.get("output")
        if isinstance(output, dict):
            outputs.append((str(result.get("name") or ""), output))
    if outputs or parsed_payload is None:
        return outputs
    if isinstance(parsed_payload, dict):
        return [("", parsed_payload)]
    if isinstance(parsed_payload, list):
        return [("", item) for item in parsed_payload if isinstance(item, dict)]
    return outputs


def _summarize_messaging_outputs(outputs: list[tuple[str, dict[str, Any]]], prompt: str) -> str | None:
    parts: list[str] = []
    presence = _presence_summary(outputs)
    gate = _gate_summary(outputs)
    maintenance = _maintenance_summary(outputs)
    malfunctions = _malfunction_summary(outputs)
    alerts = _alert_summary(outputs)

    prompt_lower = prompt.lower()
    status_like = any(word in prompt_lower for word in ("status", "presence", "occupancy", "alert", "gate state"))
    if status_like:
        for item in (presence, gate, maintenance, malfunctions, alerts):
            if item:
                parts.append(item)
        if parts:
            return " ".join(parts)

    for item in (presence, gate, maintenance, malfunctions, alerts):
        if item:
            parts.append(item)
    if parts:
        return " ".join(parts)

    generic = _generic_output_summary(outputs)
    if generic:
        return generic
    return None


def _presence_summary(outputs: list[tuple[str, dict[str, Any]]]) -> str | None:
    output = _first_output(outputs, "query_presence", "presence")
    if not output:
        return None
    records = output.get("presence") if isinstance(output.get("presence"), list) else []
    present = [
        str(record.get("person"))
        for record in records
        if isinstance(record, dict) and str(record.get("state") or "").lower() == "present" and record.get("person")
    ]
    if present:
        return f"Home now: {', '.join(present[:6])}. Occupancy ledger has spoken."
    if records:
        return "No one is currently marked home. The house is looking politely empty."
    return "No presence records are available yet. The ledger is still sharpening its pencil."


def _gate_summary(outputs: list[tuple[str, dict[str, Any]]]) -> str | None:
    output = _first_output(outputs, "query_device_states", "devices")
    if not output:
        return None
    devices = output.get("devices") if isinstance(output.get("devices"), list) else []
    gates = [
        device
        for device in devices
        if isinstance(device, dict) and str(device.get("kind") or "").lower() == "gate"
    ]
    if not gates and output.get("target"):
        return f"Gate state is unavailable for {output.get('target')}. I will not guess at machinery."
    if not gates:
        return "Gate state is unavailable. I will not guess at machinery."
    return "; ".join(
        f"{gate.get('name') or 'Gate'} is {gate.get('state') or 'unknown'}. Sensor says so; I merely wear the metaphorical waistcoat."
        for gate in gates[:3]
    )


def _maintenance_summary(outputs: list[tuple[str, dict[str, Any]]]) -> str | None:
    output = _first_output(outputs, "get_maintenance_status", "maintenance_mode")
    if not output:
        return None
    status = output.get("maintenance_mode") if isinstance(output.get("maintenance_mode"), dict) else output
    active = bool(status.get("is_active"))
    if not active:
        return "Maintenance Mode is off. Machinery may proceed with dignity."
    duration = status.get("duration_label")
    actor = status.get("enabled_by") or "System"
    suffix = f" for {duration}" if duration else ""
    return f"Maintenance Mode is on, enabled by {actor}{suffix}."


def _malfunction_summary(outputs: list[tuple[str, dict[str, Any]]]) -> str | None:
    output = _first_output(outputs, "get_active_malfunctions", "malfunctions")
    if not output:
        return None
    malfunctions = output.get("malfunctions") if isinstance(output.get("malfunctions"), list) else []
    count = int(output.get("count") or len(malfunctions))
    if count <= 0:
        return "No active gate malfunctions. The gate is behaving itself."
    labels = [
        str(item.get("title") or item.get("status") or item.get("malfunction_type") or "gate malfunction")
        for item in malfunctions[:3]
        if isinstance(item, dict)
    ]
    detail = f": {', '.join(labels)}" if labels else ""
    return f"{count} active gate malfunction{'s' if count != 1 else ''}{detail}."


def _alert_summary(outputs: list[tuple[str, dict[str, Any]]]) -> str | None:
    output = _first_output(outputs, "query_anomalies", "anomalies")
    if not output:
        return None
    anomalies = output.get("anomalies") if isinstance(output.get("anomalies"), list) else []
    count = int(output.get("count") or len(anomalies))
    if count <= 0:
        return "No active alerts. Lovely lack of drama."
    labels = []
    for anomaly in anomalies[:3]:
        if not isinstance(anomaly, dict):
            continue
        severity = str(anomaly.get("severity") or "").strip()
        message = str(anomaly.get("message") or anomaly.get("type") or "alert").strip()
        labels.append(f"{severity} {message}".strip())
    detail = f": {'; '.join(labels)}" if labels else ""
    return f"{count} active alert{'s' if count != 1 else ''}{detail}."


def _first_output(outputs: list[tuple[str, dict[str, Any]]], tool_name: str, key: str) -> dict[str, Any] | None:
    for name, output in outputs:
        if name == tool_name or key in output:
            return output
    return None


def _generic_output_summary(outputs: list[tuple[str, dict[str, Any]]]) -> str | None:
    safe_fields: list[str] = []
    blocked_fragments = ("id", "token", "secret", "raw", "payload", "metadata")
    for name, output in outputs[:3]:
        for key, value in output.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in blocked_fragments):
                continue
            if isinstance(value, (str, int, float, bool)) and value not in {None, ""}:
                safe_fields.append(f"{key}: {value}")
            if len(safe_fields) >= 4:
                break
        if len(safe_fields) >= 4:
            break
    if not safe_fields:
        return None
    prefix = "I checked IACS"
    first_tool = next((name for name, _ in outputs if name), "")
    if first_tool:
        prefix = f"I checked {first_tool.replace('_', ' ')}"
    return f"{prefix}. " + "; ".join(safe_fields[:4]) + ". Clipboard satisfied."


messaging_bridge_service = MessagingBridgeService()
