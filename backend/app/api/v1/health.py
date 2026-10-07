import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.access_events import get_access_event_service
from app.services.event_bus import event_bus
from app.services.home_assistant import get_home_assistant_service
from app.services.maintenance import get_status as get_maintenance_status

router = APIRouter()


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    """Core readiness; optional vendor outages stay in the detailed health view."""
    try:
        database = await asyncio.wait_for(_database_check(), timeout=2)
    except TimeoutError:
        database = {"status": "down"}
    ready = (
        bool(getattr(request.app.state, "startup_complete", False))
        and database.get("status") == "ok"
        and bool(_realtime_check().get("started"))
        and bool(_access_events_check().get("worker_running"))
    )
    return JSONResponse({"ready": ready}, status_code=200 if ready else 503)


@router.get("/health")
async def health() -> dict[str, object]:
    database = await _database_check()
    realtime = _realtime_check()
    access_events = _access_events_check()
    maintenance = await _maintenance_check()
    home_assistant = await _home_assistant_check()
    checks = {
        "database": database,
        "realtime": realtime,
        "access_events": access_events,
        "maintenance": maintenance,
        "home_assistant": home_assistant,
    }
    return {
        "status": _overall_status(checks),
        "checks": checks,
    }


async def _database_check() -> dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - Readiness reports any subsystem failure without interrupting other probes.
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
    except Exception as exc:  # noqa: BLE001 - Readiness reports any subsystem failure without interrupting other probes.
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
    except Exception as exc:  # noqa: BLE001 - Readiness reports any subsystem failure without interrupting other probes.
        return {"status": "degraded", "detail": _safe_error(exc)}


async def _home_assistant_check() -> dict[str, Any]:
    try:
        status = await get_home_assistant_service().status(refresh=False)
    except Exception as exc:  # noqa: BLE001 - Readiness reports any subsystem failure without interrupting other probes.
        return {
            "status": "degraded",
            "configured": None,
            "connected": False,
            "detail": _safe_error(exc),
        }
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


def _overall_status(checks: dict[str, dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "") for check in checks.values()}
    if "down" in statuses:
        return "down"
    if "degraded" in statuses:
        return "degraded"
    return "ok"


def _safe_error(exc: Exception) -> str:
    return str(exc)[:500]
