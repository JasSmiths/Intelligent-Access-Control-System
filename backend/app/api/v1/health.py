from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.access_events import get_access_event_service
from app.services.discord_messaging import get_discord_messaging_service
from app.services.event_bus import event_bus
from app.services.home_assistant import get_home_assistant_service
from app.services.maintenance import get_status as get_maintenance_status
from app.services.whatsapp_messaging import get_whatsapp_messaging_service

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    database = await _database_check()
    realtime = _realtime_check()
    access_events = _access_events_check()
    maintenance = await _maintenance_check()
    home_assistant = await _home_assistant_check()
    discord = await _discord_check()
    whatsapp = await _whatsapp_check()
    checks = {
        "database": database,
        "realtime": realtime,
        "access_events": access_events,
        "maintenance": maintenance,
        "home_assistant": home_assistant,
        "discord": discord,
        "whatsapp": whatsapp,
    }
    return {
        "status": _overall_status(checks),
        "checks": checks,
        "discord": _legacy_discord_payload(discord),
    }


async def _database_check() -> dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "down", "detail": _safe_error(exc)}


def _realtime_check() -> dict[str, Any]:
    status = event_bus.status()
    return {
        "status": "ok" if status["started"] else "down",
        **status,
    }


def _access_events_check() -> dict[str, Any]:
    try:
        status = get_access_event_service().status()
    except Exception as exc:
        return {"status": "degraded", "detail": _safe_error(exc)}
    return status


async def _maintenance_check() -> dict[str, Any]:
    try:
        status = await get_maintenance_status()
        return {
            "status": "maintenance" if status.get("is_active") else "ok",
            "active": bool(status.get("is_active")),
            "enabled_by": status.get("enabled_by"),
            "enabled_at": status.get("enabled_at"),
            "reason": status.get("reason"),
            "duration_seconds": status.get("duration_seconds"),
            "duration_label": status.get("duration_label"),
        }
    except Exception as exc:
        return {"status": "degraded", "detail": _safe_error(exc)}


async def _home_assistant_check() -> dict[str, Any]:
    try:
        status = await get_home_assistant_service().status(refresh=False)
    except Exception as exc:
        return {"status": "degraded", "configured": None, "connected": False, "detail": _safe_error(exc)}
    configured = bool(status.get("configured"))
    connected = bool(status.get("connected"))
    last_error = status.get("last_error")
    if not configured:
        health_status = "disabled"
    elif connected and not last_error:
        health_status = "ok"
    else:
        health_status = "degraded"
    return {
        "status": health_status,
        "configured": configured,
        "connected": connected,
        "degraded": bool(status.get("degraded")),
        "last_error": last_error,
        "state_refreshed_at": status.get("state_refreshed_at"),
        "listener_running": bool(status.get("listener_running")),
    }


async def _discord_check() -> dict[str, Any]:
    try:
        status = await get_discord_messaging_service().status()
    except Exception as exc:
        return {"status": "degraded", "configured": None, "connected": False, "detail": _safe_error(exc)}
    configured = bool(status.get("configured"))
    connected = bool(status.get("connected"))
    last_error = status.get("last_error")
    if not configured:
        health_status = "disabled"
    elif connected and not last_error:
        health_status = "ok"
    else:
        health_status = "degraded"
    return {
        "status": health_status,
        "configured": configured,
        "connected": connected,
        "guild_count": status.get("guild_count"),
        "channel_count": status.get("channel_count"),
        "last_error": last_error,
    }


async def _whatsapp_check() -> dict[str, Any]:
    try:
        status = await get_whatsapp_messaging_service().status()
    except Exception as exc:
        return {"status": "degraded", "enabled": None, "configured": None, "detail": _safe_error(exc)}
    enabled = bool(status.get("enabled"))
    configured = bool(status.get("configured"))
    last_error = status.get("last_error")
    if not enabled:
        health_status = "disabled"
    elif configured and not last_error:
        health_status = "ok"
    else:
        health_status = "degraded"
    return {
        "status": health_status,
        "enabled": enabled,
        "configured": configured,
        "webhook_configured": bool(status.get("webhook_configured")),
        "signature_configured": bool(status.get("signature_configured")),
        "admin_target_count": status.get("admin_target_count"),
        "last_error": last_error,
    }


def _overall_status(checks: dict[str, dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "") for check in checks.values()}
    if "down" in statuses:
        return "down"
    if "degraded" in statuses:
        return "degraded"
    return "ok"


def _legacy_discord_payload(discord: dict[str, Any]) -> dict[str, Any]:
    return {
        "configured": discord.get("configured"),
        "connected": discord.get("connected"),
        "guild_count": discord.get("guild_count"),
        "channel_count": discord.get("channel_count"),
    }


def _safe_error(exc: Exception) -> str:
    return str(exc)[:500]
