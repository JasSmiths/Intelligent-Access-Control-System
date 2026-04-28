from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import MaintenanceModeState
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.notifications.base import NotificationContext
from app.services.event_bus import event_bus
from app.services.notifications import get_notification_service
from app.services.telemetry import (
    TELEMETRY_CATEGORY_MAINTENANCE,
    audit_log_event_payload,
    emit_audit_log,
    write_audit_log,
)

logger = get_logger(__name__)

MAINTENANCE_STATE_ID = 1
MAINTENANCE_HA_ENTITY_ID = "input_boolean.top_gate_maintenance_mode"
MAINTENANCE_ENABLED_TRIGGER = "maintenance_mode_enabled"
MAINTENANCE_DISABLED_TRIGGER = "maintenance_mode_disabled"


async def get_status() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        row = await _get_or_create_state(session)
        await session.commit()
        return _status_payload(row)


async def is_maintenance_mode_active() -> bool:
    async with AsyncSessionLocal() as session:
        row = await session.get(MaintenanceModeState, MAINTENANCE_STATE_ID)
        return bool(row and row.is_active)


async def set_mode(
    active: bool,
    *,
    actor: str,
    source: str,
    reason: str | None = None,
    actor_user_id: str | None = None,
    sync_ha: bool = True,
) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    reason_text = _clean_reason(reason) or _default_reason(active, actor=actor, source=source)
    async with AsyncSessionLocal() as session:
        row = await _get_or_create_state(session)
        previous = _status_payload(row, now=now)
        was_active = bool(row.is_active)
        previous_enabled_at = row.enabled_at
        if was_active == active:
            return {**previous, "changed": False}

        if active:
            row.is_active = True
            row.enabled_by = actor
            row.enabled_at = now
            row.source = source
            row.reason = reason_text
        else:
            row.is_active = False
            row.enabled_by = None
            row.enabled_at = None
            row.source = source
            row.reason = None

        await session.commit()
        next_status = _status_payload(row, now=now)

    duration_seconds = (
        max(0, int((now - previous_enabled_at).total_seconds()))
        if previous_enabled_at and not active
        else None
    )
    duration_label = format_duration(duration_seconds) if duration_seconds is not None else None
    event_payload = {
        **next_status,
        "changed": True,
        "actor": actor,
        "source": source,
        "reason": reason_text,
        "maintenance_mode_reason": reason_text,
        "duration_seconds": duration_seconds if duration_seconds is not None else next_status["duration_seconds"],
        "duration_label": duration_label or next_status["duration_label"],
    }
    await _write_mode_audit(
        active,
        actor=actor,
        actor_user_id=actor_user_id,
        source=source,
        reason=reason_text,
        previous=previous,
        current=next_status,
        duration_seconds=duration_seconds,
        duration_label=duration_label,
    )
    await event_bus.publish("maintenance_mode.changed", event_payload)
    await _notify_maintenance_changed(active, event_payload)
    if sync_ha:
        await _sync_home_assistant(active, actor=actor, source=source, reason=reason_text)
    return event_payload


async def _get_or_create_state(session: AsyncSession) -> MaintenanceModeState:
    row = await session.get(MaintenanceModeState, MAINTENANCE_STATE_ID)
    if row:
        return row
    row = MaintenanceModeState(id=MAINTENANCE_STATE_ID, is_active=False)
    session.add(row)
    await session.flush()
    return row


def _status_payload(row: MaintenanceModeState, *, now: datetime | None = None) -> dict[str, Any]:
    current_time = now or datetime.now(tz=UTC)
    duration_seconds = 0
    duration_label = None
    if row.is_active and row.enabled_at:
        duration_seconds = max(0, int((current_time - row.enabled_at).total_seconds()))
        duration_label = format_duration(duration_seconds)
    return {
        "is_active": bool(row.is_active),
        "enabled_by": row.enabled_by,
        "enabled_at": row.enabled_at.isoformat() if row.enabled_at else None,
        "source": row.source,
        "reason": row.reason,
        "duration_seconds": duration_seconds,
        "duration_label": duration_label,
        "ha_entity_id": MAINTENANCE_HA_ENTITY_ID,
    }


def format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    remaining = max(0, int(seconds))
    days, remaining = divmod(remaining, 86_400)
    hours, remaining = divmod(remaining, 3_600)
    minutes, seconds = divmod(remaining, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return " and ".join(parts[:2])


def _clean_reason(value: str | None) -> str:
    return str(value or "").strip()[:500]


def _default_reason(active: bool, *, actor: str, source: str) -> str:
    verb = "Enabled" if active else "Disabled"
    source_label = _source_label(source)
    if source_label and source_label.lower() not in actor.lower():
        return f"{verb} by {actor} from {source_label}"
    return f"{verb} by {actor}"


def _source_label(source: str) -> str:
    normalized = source.strip().lower().replace("_", " ")
    labels = {
        "ui": "UI",
        "dashboard": "Dashboard",
        "settings": "Settings",
        "alfred": "Alfred",
        "home assistant": "Home Assistant",
        "home assistant sync": "Home Assistant Sync",
    }
    return labels.get(normalized, source.strip())


async def _write_mode_audit(
    active: bool,
    *,
    actor: str,
    actor_user_id: str | None,
    source: str,
    reason: str,
    previous: dict[str, Any],
    current: dict[str, Any],
    duration_seconds: int | None,
    duration_label: str | None,
) -> None:
    action = "maintenance_mode.enabled" if active else "maintenance_mode.disabled"
    summary = reason
    if not active and duration_label:
        summary = f"{reason}. System was in Maintenance Mode for {duration_label}"
    async with AsyncSessionLocal() as session:
        row = await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_MAINTENANCE,
            action=action,
            actor=actor,
            actor_user_id=actor_user_id,
            target_entity="MaintenanceMode",
            target_id=str(MAINTENANCE_STATE_ID),
            target_label="Global Maintenance Mode",
            diff={"old": previous, "new": current},
            metadata={
                "source": source,
                "reason": reason,
                "summary": summary,
                "duration_seconds": duration_seconds,
                "duration_label": duration_label,
                "ha_entity_id": MAINTENANCE_HA_ENTITY_ID,
            },
        )
        await session.commit()
        await session.refresh(row)
        await event_bus.publish("audit.log.created", audit_log_event_payload(row))


async def _notify_maintenance_changed(active: bool, payload: dict[str, Any]) -> None:
    trigger = MAINTENANCE_ENABLED_TRIGGER if active else MAINTENANCE_DISABLED_TRIGGER
    subject = "Maintenance Mode Enabled" if active else "Maintenance Mode Disabled"
    facts = {
        "message": payload.get("maintenance_mode_reason") or payload.get("reason") or subject,
        "maintenance_mode_reason": payload.get("maintenance_mode_reason") or payload.get("reason") or "",
        "maintenance_mode_duration": payload.get("duration_label") or "",
        "maintenance_mode_actor": payload.get("actor") or "",
        "maintenance_mode_source": payload.get("source") or "",
        "occurred_at": datetime.now(tz=UTC).isoformat(),
    }
    await get_notification_service().notify(
        NotificationContext(
            event_type=trigger,
            subject=subject,
            severity="warning" if active else "info",
            facts=facts,
        )
    )


async def _sync_home_assistant(active: bool, *, actor: str, source: str, reason: str) -> None:
    service = "input_boolean.turn_on" if active else "input_boolean.turn_off"
    try:
        await HomeAssistantClient().call_service(service, {"entity_id": MAINTENANCE_HA_ENTITY_ID})
    except Exception as exc:
        emit_audit_log(
            category=TELEMETRY_CATEGORY_MAINTENANCE,
            action="maintenance_mode.ha_sync_failed",
            actor=actor,
            target_entity="HomeAssistant",
            target_id=MAINTENANCE_HA_ENTITY_ID,
            target_label="Top Gate Maintenance Mode",
            outcome="failed",
            level="error",
            metadata={
                "source": source,
                "reason": reason,
                "requested_state": "on" if active else "off",
                "error": str(exc),
            },
        )
        logger.warning(
            "maintenance_mode_ha_sync_failed",
            extra={"entity_id": MAINTENANCE_HA_ENTITY_ID, "active": active, "error": str(exc)},
        )
