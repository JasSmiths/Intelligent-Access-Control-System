"""Gate and maintenance Alfred tool handlers."""
# ruff: noqa: F403, F405

from __future__ import annotations

from typing import Any

from app.ai.tool_groups._shared import *
from app.services.access_devices import get_access_device_service, normalize_access_device_key
from app.services.gate_commands import GateCommandIntent, get_gate_command_coordinator


def _device_state_record(entity: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": str(entity.get("name") or entity.get("entity_id") or ""),
        "entity_id": str(entity.get("entity_id") or ""),
        "state": entity.get("state") or "unknown",
        "enabled": bool(entity.get("enabled", True)),
        "schedule_id": entity.get("schedule_id"),
    }


def _device_matches_target(device: dict[str, Any], target: str) -> bool:
    aliases = {
        "main gate": "top gate",
        "top gate": "main gate",
        "mums garage": "mums garage door",
        "mum garage": "mums garage door",
    }
    haystack = f"{device.get('name', '')} {device.get('entity_id', '')} {device.get('kind', '')}".lower()
    candidates = {target, aliases.get(target, "")}
    return any(candidate and candidate in haystack for candidate in candidates)


async def _resolve_openable_device(
    arguments: dict[str, Any],
    *,
    kind_filter: str,
) -> dict[str, Any] | None:
    target_text = str(arguments.get("target") or arguments.get("entity_id") or "").strip()
    resolve_args = {
        **arguments,
        "entity_id": arguments.get("entity_id") or target_text,
        "entity_name": arguments.get("entity_name") or arguments.get("target") or arguments.get("name") or target_text,
        "target": target_text,
    }
    if kind_filter in {"gate", "garage_door"}:
        return await _resolve_cover_target(resolve_args, entity_type=kind_filter)

    preferred_order = ["garage_door", "gate"] if "garage" in target_text.lower() else ["gate", "garage_door"]
    for entity_type in preferred_order:
        target = await _resolve_cover_target(resolve_args, entity_type=entity_type)
        if target:
            return target
    return None


def _agent_device_payload(target: dict[str, Any]) -> dict[str, Any]:
    entity = target["entity"]
    return {
        "kind": target["kind"],
        "entity_id": str(entity["entity_id"]),
        "name": str(entity.get("name") or entity["entity_id"]),
    }


def _agent_device_audit_payload(
    target: dict[str, Any],
    *,
    action: str,
    accepted: bool,
    state: str,
    detail: str | None,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    device = _agent_device_payload(target)
    payload = {
        "source": "alfred",
        "requested_by": "agent",
        "user_id": user_id or None,
        "session_id": session_id or None,
        "kind": device["kind"],
        "entity_id": device["entity_id"],
        "name": device["name"],
        "action": action,
        "accepted": accepted,
        "state": state,
        "detail": detail,
    }
    if action == "open":
        payload["opened_by"] = "agent"
    elif action == "close":
        payload["closed_by"] = "agent"
    return payload


def _log_extra(payload: dict[str, Any]) -> dict[str, Any]:
    extra = dict(payload)
    if "name" in extra:
        extra["device_name"] = extra.pop("name")
    return extra


def _gate_state_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "unknown")


async def _execute_agent_device_command(
    target: dict[str, Any],
    *,
    action: str,
    audit_reason: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    entity = target["entity"]
    entity_id = str(entity["entity_id"])
    reason = f"Alfred agent: {audit_reason}"
    if target["kind"] == "gate":
        outcome = await get_gate_command_coordinator().execute_open(
            GateCommandIntent(
                reason=reason,
                source="alfred",
                actor="Alfred_AI",
                metadata={
                    "actor_user_id": user_id or None,
                    "session_id": session_id or None,
                    "target_entity_id": entity_id,
                    "target_name": str(entity.get("name") or entity_id),
                },
            )
        )
        return {
            "accepted": outcome.accepted,
            "state": outcome.state.value,
            "detail": outcome.detail,
            "intent_id": outcome.intent.intent_id,
            "command_id": outcome.command_id,
            "mechanically_confirmed": outcome.mechanically_confirmed,
            "requires_reconciliation": outcome.requires_reconciliation,
        }

    outcome = await get_access_device_service().command_device(
        normalize_access_device_key(entity_id),
        action,
        reason,
        schedule_source=str(target["kind"]),
    )
    return {
        "accepted": outcome.accepted,
        "state": _gate_state_value(outcome.state),
        "detail": outcome.detail,
        "verified": outcome.verified,
        "used_provider": outcome.used_provider,
        "failover_used": outcome.failover_used,
    }


async def query_device_states(arguments: dict[str, Any]) -> dict[str, Any]:
    target = _normalize(arguments.get("target"))
    kind_filter = _normalize(arguments.get("kind") or "all")
    if kind_filter not in {"", "all", "gate", "door", "garage_door"}:
        return {"configured": False, "devices": [], "count": 0, "error": "kind must be all, gate, door, or garage_door."}

    status = await get_home_assistant_service().status()
    devices: list[dict[str, Any]] = []
    for entity in status.get("gate_entities") or []:
        devices.append(_device_state_record(entity, "gate"))
    for entity in status.get("garage_door_entities") or []:
        devices.append(_device_state_record(entity, "garage_door"))

    door_sensors = [
        {
            "kind": "door",
            "name": "Front Door",
            "entity_id": "binary_sensor.front_door",
            "state": status.get("front_door_state"),
            "enabled": True,
        },
        {
            "kind": "door",
            "name": "Back Door",
            "entity_id": "binary_sensor.back_door",
            "state": status.get("back_door_state"),
            "enabled": True,
        },
    ]
    devices.extend(row for row in door_sensors if row.get("state") is not None)

    filtered = [
        device
        for device in devices
        if kind_filter in {"", "all", device["kind"]}
        and (not target or _device_matches_target(device, target))
    ]
    return {
        "configured": bool(status.get("configured")),
        "devices": filtered,
        "count": len(filtered),
        "target": arguments.get("target") or None,
        "kind": kind_filter or "all",
    }


async def get_maintenance_status(arguments: dict[str, Any]) -> dict[str, Any]:
    status = await get_maintenance_mode_status()
    return {"maintenance_mode": status, **status}


async def get_active_malfunctions(arguments: dict[str, Any]) -> dict[str, Any]:
    include_timeline = bool(arguments.get("include_timeline"))
    items = await get_gate_malfunction_service().active(include_timeline=include_timeline)
    return {"count": len(items), "malfunctions": items}


async def get_malfunction_history(arguments: dict[str, Any]) -> dict[str, Any]:
    status = str(arguments.get("status") or "").strip().lower() or None
    limit = max(1, min(int(arguments.get("limit") or 25), 100))
    include_timeline = bool(arguments.get("include_timeline"))
    items = await get_gate_malfunction_service().history(
        status=status,
        limit=limit,
        include_timeline=include_timeline,
    )
    return {"count": len(items), "malfunctions": items}


async def trigger_manual_malfunction_override(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    if str(context.get("user_role") or "").lower() != "admin":
        return {
            "changed": False,
            "error": "Admin access is required for gate malfunction overrides.",
        }
    malfunction_id = str(arguments.get("malfunction_id") or "").strip()
    action = str(arguments.get("action") or "").strip()
    reason = str(arguments.get("reason") or "Manual Alfred gate malfunction override").strip()
    confirm = bool(arguments.get("confirm"))
    if not malfunction_id:
        return {"changed": False, "error": "malfunction_id is required."}
    if action not in {"recheck_live_state", "run_attempt_now", "mark_resolved", "mark_fubar"}:
        return {"changed": False, "error": "action must be recheck_live_state, run_attempt_now, mark_resolved, or mark_fubar."}
    return await get_gate_malfunction_service().override(
        malfunction_id,
        action=action,
        reason=reason,
        actor="Alfred",
        confirm=confirm,
    )


async def enable_maintenance_mode(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "enabled": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Maintenance Mode",
            "detail": "Maintenance Mode disables automated access actions until it is turned off.",
        }
    context = get_chat_tool_context()
    status = await set_maintenance_mode(
        True,
        actor="Alfred",
        actor_user_id=str(context.get("user_id") or "") or None,
        source="Alfred",
        reason=str(arguments.get("reason") or "Enabled by Alfred").strip(),
        sync_ha=True,
    )
    return {"enabled": bool(status.get("is_active")), "maintenance_mode": status, **status}


async def disable_maintenance_mode(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "disabled": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Maintenance Mode",
            "detail": "Disabling Maintenance Mode resumes automated access actions.",
        }
    context = get_chat_tool_context()
    status = await set_maintenance_mode(
        False,
        actor="Alfred",
        actor_user_id=str(context.get("user_id") or "") or None,
        source="Alfred",
        reason="Disabled by Alfred",
        sync_ha=True,
    )
    return {"disabled": not bool(status.get("is_active")), "maintenance_mode": status, **status}


async def open_device(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    user_id = str(context.get("user_id") or "")
    session_id = str(context.get("session_id") or "")
    target_text = str(arguments.get("target") or arguments.get("entity_id") or "").strip()
    action = _normalize(arguments.get("action") or "open")
    if action not in {"open", "close"}:
        return {"accepted": False, "error": "action must be open or close."}
    kind_filter = _normalize(arguments.get("kind") or "all")
    if kind_filter not in {"", "all", "gate", "garage_door"}:
        return {"accepted": False, "error": "kind must be all, gate, or garage_door."}
    if action == "close" and kind_filter == "gate":
        return {
            "closed": False,
            "accepted": False,
            "action": action,
            "error": "Alfred can close configured garage doors, not gates.",
        }
    if not target_text:
        return {
            "accepted": False,
            "action": action,
            "requires_details": True,
            "detail": f"Which gate or garage door should I {action}?",
        }

    target = await _resolve_openable_device(arguments, kind_filter=kind_filter or "all")
    if not target:
        return {
            "accepted": False,
            "action": action,
            "target": target_text,
            "error": f"I could not find a configured gate or garage door called {target_text}.",
        }
    if action == "close" and target["kind"] != "garage_door":
        return {
            "closed": False,
            "accepted": False,
            "action": action,
            "device": _agent_device_payload(target),
            "detail": "Alfred can close configured garage doors. Gate close commands are not enabled.",
        }

    if not bool(arguments.get("confirm")):
        device = _agent_device_payload(target)
        return {
            "opened": False,
            "closed": False,
            "accepted": False,
            "action": action,
            "requires_confirmation": True,
            "target": device["name"],
            "device": device,
            "confirmation_field": "confirm",
            "detail": (
                f"{'Opening gates and garage doors' if action == 'open' else 'Closing garage doors'} "
                "is a real-world action. Use the chat confirmation action before I continue."
            ),
        }

    if action == "open" and await is_maintenance_mode_active():
        return {
            "opened": False,
            "accepted": False,
            "action": action,
            "device": _agent_device_payload(target),
            "state": "maintenance_mode",
            "detail": "Maintenance Mode is active. Automated actions are disabled.",
            "opened_by": "agent",
        }

    config = await get_runtime_config()
    now = datetime.now(tz=UTC)
    if action == "open":
        async with AsyncSessionLocal() as session:
            schedule_evaluation = await evaluate_schedule_id(
                session,
                target["entity"].get("schedule_id"),
                now,
                timezone_name=config.site_timezone,
                default_policy=config.schedule_default_policy,
                source=str(target["kind"]),
            )
    else:
        schedule_evaluation = None
    if schedule_evaluation and not schedule_evaluation.allowed:
        detail = schedule_evaluation.reason or "Device is outside its assigned schedule."
        payload = _agent_device_audit_payload(
            target,
            action=action,
            accepted=False,
            state="schedule_denied",
            detail=detail,
            user_id=user_id,
            session_id=session_id,
        )
        await event_bus.publish("agent.device_open_failed", payload)
        logger.warning("agent_device_open_schedule_denied", extra=_log_extra(payload))
        return {
            "opened": False,
            "accepted": False,
            "device": _agent_device_payload(target),
            "action": action,
            "state": "schedule_denied",
            "detail": detail,
            "opened_by": "agent",
        }

    reason = str(arguments.get("reason") or "").strip()
    action_label = "opening" if action == "open" else "closing"
    audit_reason = reason or f"Alfred agent requested {action_label} {target['entity'].get('name') or target['entity']['entity_id']}"
    outcome = await _execute_agent_device_command(
        target,
        action=action,
        audit_reason=audit_reason,
        user_id=user_id,
        session_id=session_id,
    )
    audit_payload = _agent_device_audit_payload(
        target,
        action=action,
        accepted=bool(outcome["accepted"]),
        state=str(outcome["state"]),
        detail=outcome.get("detail"),
        user_id=user_id,
        session_id=session_id,
    )
    audit_payload["reason"] = audit_reason
    audit_payload.update(
        {
            key: value
            for key, value in outcome.items()
            if key not in {"accepted", "state", "detail"} and value is not None
        }
    )
    agent_event = f"agent.device_{action}_requested" if outcome["accepted"] else f"agent.device_{action}_failed"
    device_event = f"{target['kind']}.{action}_requested" if outcome["accepted"] else f"{target['kind']}.{action}_failed"
    await event_bus.publish(
        agent_event,
        audit_payload,
    )
    await event_bus.publish(
        device_event,
        {
            **audit_payload,
            "source": "alfred",
        },
    )
    if outcome["accepted"]:
        logger.info(f"agent_device_{action}_requested", extra=_log_extra(audit_payload))
    else:
        logger.error(f"agent_device_{action}_failed", extra=_log_extra(audit_payload))

    return {
        "opened": bool(outcome["accepted"]) if action == "open" else False,
        "closed": bool(outcome["accepted"]) if action == "close" else False,
        "accepted": bool(outcome["accepted"]),
        "device": _agent_device_payload(target),
        "action": action,
        "state": outcome["state"],
        "detail": outcome.get("detail"),
        f"{'opened' if action == 'open' else 'closed'}_by": "agent",
        "audit_event": agent_event,
        **{
            key: value
            for key, value in outcome.items()
            if key not in {"accepted", "state", "detail"} and value is not None
        },
    }


async def open_gate(arguments: dict[str, Any]) -> dict[str, Any]:
    target = str(arguments.get("target") or "").strip()
    if not target:
        config = await get_runtime_config()
        gates = [entity for entity in _cover_entities_by_kind(config).get("gate", []) if entity.get("enabled", True)]
        if len(gates) == 1:
            target = str(gates[0].get("name") or gates[0].get("entity_id") or "")
    if not target:
        return {
            "opened": False,
            "requires_details": True,
            "detail": "Which gate should I open?",
        }
    output = await open_device(
        {
            "target": target,
            "kind": "gate",
            "reason": arguments.get("reason"),
            "confirm": bool(arguments.get("confirm")),
        }
    )
    output.setdefault("target", target)
    return output


async def toggle_maintenance_mode(arguments: dict[str, Any]) -> dict[str, Any]:
    state = _normalize(arguments.get("state"))
    enable = state in {"enabled", "enable", "on", "true", "yes", "active"}
    disable = state in {"disabled", "disable", "off", "false", "no", "inactive"}
    if not enable and not disable:
        return {
            "changed": False,
            "error": "state must be enabled or disabled.",
        }
    if not bool(arguments.get("confirm")):
        return {
            "changed": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "Maintenance Mode",
            "state": "enabled" if enable else "disabled",
            "detail": (
                "Enable Maintenance Mode and stop automated access actions?"
                if enable
                else "Disable Maintenance Mode and resume automated access actions?"
            ),
        }
    if enable:
        result = await enable_maintenance_mode(
            {"reason": arguments.get("reason") or "Enabled by Alfred", "confirm": True}
        )
        return {"changed": bool(result.get("enabled")), "state": "enabled", **result}
    result = await disable_maintenance_mode({"confirm": True})
    return {"changed": bool(result.get("disabled")), "state": "disabled", **result}
