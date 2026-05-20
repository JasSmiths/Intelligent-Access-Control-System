import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import current_user
from app.db.session import AsyncSessionLocal
from app.db.session import get_db_session
from app.models import AccessEvent, Anomaly, MovementSagaRecord, Presence, User
from app.models.enums import AnomalySeverity, AnomalyType
from app.services.alert_snapshots import alert_snapshot_metadata, alert_snapshot_path
from app.services.event_bus import event_bus
from app.services.expected_presence import expected_presence_today
from app.services.settings import get_runtime_config
from app.services.snapshots import access_event_snapshot_payload, get_snapshot_manager
from app.services.telemetry import TELEMETRY_CATEGORY_ACCESS, actor_from_user, write_audit_log

router = APIRouter()


class AlertActionRequest(BaseModel):
    alert_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    action: Literal["resolve", "reopen"]
    note: str | None = Field(default=None, max_length=1000)


@router.get("/events")
async def list_events(limit: int = Query(default=50, ge=1, le=250)) -> list[dict]:
    async with AsyncSessionLocal() as session:
        events = (
            await session.scalars(
                select(AccessEvent)
                .options(selectinload(AccessEvent.anomalies))
                .order_by(AccessEvent.occurred_at.desc())
                .limit(limit)
            )
        ).all()

        movement_rows = (
            await session.scalars(
                select(MovementSagaRecord).where(
                    MovementSagaRecord.access_event_id.in_([event.id for event in events])
                )
            )
        ).all() if events else []
    movement_by_event_id = {row.access_event_id: row for row in movement_rows}

    return [_serialize_event(event, movement_by_event_id.get(event.id)) for event in events]


def _serialize_event(event: AccessEvent, movement_saga: MovementSagaRecord | None = None) -> dict:
    visitor_pass = _event_visitor_pass_payload(event)
    payload = {
        "id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "confidence": event.confidence,
        "source": event.source,
        "occurred_at": event.occurred_at.isoformat(),
        "timing_classification": event.timing_classification.value,
        "anomaly_count": len(event.anomalies),
        "visitor_pass_id": _optional_text(visitor_pass.get("id")),
        "visitor_name": _optional_text(visitor_pass.get("visitor_name")),
        "visitor_pass_mode": _optional_text(visitor_pass.get("mode")),
        "movement_saga": _event_movement_saga_payload(event, movement_saga),
    }
    payload.update(access_event_snapshot_payload(event))
    return payload


@router.get("/events/{event_id}/snapshot")
async def event_snapshot(
    event_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    row = await session.get(AccessEvent, event_id)
    if not row or not row.snapshot_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event snapshot was not found.")
    try:
        path = get_snapshot_manager().resolve_path(row.snapshot_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Event snapshot was not found.",
        ) from exc
    return FileResponse(
        path,
        media_type=row.snapshot_content_type or "image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


def _event_visitor_pass_payload(event: AccessEvent) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    payload = raw_payload.get("visitor_pass")
    return payload if isinstance(payload, dict) else {}


def _event_movement_saga_payload(
    event: AccessEvent,
    movement_saga: MovementSagaRecord | None,
) -> dict[str, Any] | None:
    if movement_saga:
        return {
            "id": str(movement_saga.id),
            "state": movement_saga.state.value,
            "reconciliation_required": movement_saga.reconciliation_required,
            "gate_command_required": movement_saga.gate_command_required,
            "presence_committed": movement_saga.presence_committed,
            "failure_detail": movement_saga.failure_detail,
            "updated_at": movement_saga.updated_at.isoformat() if movement_saga.updated_at else None,
        }
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    fallback = raw_payload.get("movement_saga")
    return fallback if isinstance(fallback, dict) else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@router.get("/presence")
async def list_presence() -> list[dict]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(Presence).options(selectinload(Presence.person)).order_by(Presence.updated_at.desc())
            )
        ).all()

    return [
        {
            "person_id": str(row.person_id),
            "display_name": row.person.display_name,
            "state": row.state.value,
            "last_changed_at": row.last_changed_at.isoformat() if row.last_changed_at else None,
        }
        for row in rows
    ]


@router.get("/presence/expected-today")
async def expected_presence_for_today(
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    config = await get_runtime_config()
    return await expected_presence_today(session, timezone_name=config.site_timezone)


@router.get("/alerts")
async def list_alerts(
    status_filter: Literal["open", "resolved", "all"] = Query(default="open", alias="status"),
    severity: AnomalySeverity | None = Query(default=None),
    type_filter: AnomalyType | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=250),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    config = await get_runtime_config()
    timezone = _alert_timezone(config.site_timezone)
    fetch_limit = min(limit * 5, 1000)
    query = (
        select(Anomaly)
        .options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
        .order_by(Anomaly.created_at.desc())
        .limit(fetch_limit)
    )
    if status_filter == "open":
        query = query.where(Anomaly.resolved_at.is_(None))
    elif status_filter == "resolved":
        query = query.where(Anomaly.resolved_at.is_not(None))
    if severity:
        query = query.where(Anomaly.severity == severity)
    if type_filter:
        query = query.where(Anomaly.anomaly_type == type_filter)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(or_(Anomaly.message.ilike(pattern), cast(Anomaly.context, String).ilike(pattern)))

    rows = (await session.scalars(query)).all()
    items = _serialize_alerts(rows, timezone)
    return items[:limit]


@router.patch("/alerts/action")
async def action_alerts(
    request: AlertActionRequest,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    alert_ids = list(dict.fromkeys(request.alert_ids))
    rows = (
        await session.scalars(
            select(Anomaly)
            .options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
            .where(Anomaly.id.in_(alert_ids))
            .order_by(Anomaly.created_at.desc())
        )
    ).all()
    if len(rows) != len(alert_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more alerts were not found.")

    before = [_alert_audit_snapshot(row) for row in rows]
    note = request.note.strip() if request.note else None
    now = datetime.now(tz=UTC)
    _apply_alert_action(rows, request.action, actor.id, note, now)

    await session.flush()
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_ACCESS,
        action=f"alert.{request.action}",
        actor=actor_from_user(actor),
        actor_user_id=actor.id,
        target_entity="Alert",
        target_id=str(rows[0].id) if len(rows) == 1 else "bulk",
        target_label=f"{len(rows)} alert{'s' if len(rows) != 1 else ''}",
        diff={"old": before, "new": [_alert_audit_snapshot(row) for row in rows]},
        metadata={"alert_ids": [str(row.id) for row in rows], "note": note},
    )
    await session.commit()
    await event_bus.publish(
        "alerts.updated",
        {
            "action": request.action,
            "alert_ids": [str(row.id) for row in rows],
            "count": len(rows),
        },
    )
    return {"updated": len(rows), "alert_ids": [str(row.id) for row in rows]}


@router.get("/alerts/{alert_id}/snapshot")
async def alert_snapshot(
    alert_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    row = await session.get(Anomaly, alert_id)
    if not row or row.anomaly_type != AnomalyType.UNAUTHORIZED_PLATE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert snapshot was not found.")
    metadata = alert_snapshot_metadata(row)
    path = alert_snapshot_path(alert_id)
    if not metadata or not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert snapshot was not found.")
    return FileResponse(
        path,
        media_type=str(metadata.get("content_type") or "image/jpeg"),
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/anomalies")
async def list_anomalies(limit: int = Query(default=50, ge=1, le=250)) -> list[dict]:
    async with AsyncSessionLocal() as session:
        anomalies = (
            await session.scalars(
                select(Anomaly)
                .options(selectinload(Anomaly.resolved_by))
                .order_by(Anomaly.created_at.desc())
                .limit(limit)
            )
        ).all()

    return [
        _serialize_alert(anomaly)
        for anomaly in anomalies
    ]


def _alert_timezone(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(value or "Europe/London")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _serialize_alerts(rows: list[Anomaly], timezone: ZoneInfo) -> list[dict]:
    groups: dict[tuple[str, str], list[Anomaly]] = {}
    items: list[dict] = []
    for row in rows:
        if _should_group_unknown_plate(row):
            registration = _alert_registration_number(row)
            local_date = row.created_at.astimezone(timezone).date().isoformat()
            groups.setdefault((registration, local_date), []).append(row)
        else:
            items.append(_serialize_alert(row))

    for (registration, local_date), group_rows in groups.items():
        ordered = sorted(group_rows, key=lambda item: item.created_at)
        first_seen = ordered[0].created_at
        last_seen = ordered[-1].created_at
        count = len(ordered)
        snapshot = _latest_alert_snapshot(ordered)
        items.append(
            {
                "id": f"group:unauthorized_plate:{local_date}:{registration}",
                "alert_ids": [str(item.id) for item in ordered],
                "grouped": True,
                "type": AnomalyType.UNAUTHORIZED_PLATE.value,
                "severity": AnomalySeverity.WARNING.value,
                "status": "open",
                "message": "Unauthorised Plate, Access Denied",
                "registration_number": registration,
                "event_id": str(ordered[-1].event_id) if ordered[-1].event_id else None,
                "count": count,
                "local_date": local_date,
                "created_at": last_seen.isoformat(),
                "first_seen_at": first_seen.isoformat(),
                "last_seen_at": last_seen.isoformat(),
                "resolved_at": None,
                "resolved_by_user_id": None,
                "resolved_by": None,
                "resolution_note": None,
                "snapshot_url": snapshot.get("url") if snapshot else None,
                "snapshot_captured_at": snapshot.get("captured_at") if snapshot else None,
                "snapshot_bytes": snapshot.get("bytes") if snapshot else None,
            }
        )

    return sorted(items, key=lambda item: item["last_seen_at"] or item["created_at"], reverse=True)


def _serialize_alert(row: Anomaly) -> dict:
    return {
        "id": str(row.id),
        "alert_ids": [str(row.id)],
        "grouped": False,
        "event_id": str(row.event_id) if row.event_id else None,
        "type": row.anomaly_type.value,
        "severity": _alert_display_severity(row).value,
        "status": "resolved" if row.resolved_at else "open",
        "message": _alert_display_message(row),
        "registration_number": _alert_registration_number(row),
        "count": 1,
        "local_date": None,
        "created_at": row.created_at.isoformat(),
        "first_seen_at": row.created_at.isoformat(),
        "last_seen_at": row.created_at.isoformat(),
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolved_by_user_id": str(row.resolved_by_user_id) if row.resolved_by_user_id else None,
        "resolved_by": _alert_resolver(row),
        "resolution_note": row.resolution_note,
        "snapshot_url": _alert_snapshot_value(row, "url"),
        "snapshot_captured_at": _alert_snapshot_value(row, "captured_at"),
        "snapshot_bytes": _alert_snapshot_value(row, "bytes"),
    }


def _should_group_unknown_plate(row: Anomaly) -> bool:
    return (
        row.resolved_at is None
        and row.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE
    )


def _alert_display_severity(row: Anomaly) -> AnomalySeverity:
    if row.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE:
        return AnomalySeverity.WARNING
    return row.severity


def _alert_display_message(row: Anomaly) -> str:
    if row.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE:
        return "Unauthorised Plate, Access Denied"
    return row.message


def _alert_registration_number(row: Anomaly) -> str:
    context = row.context or {}
    value = context.get("registration_number")
    if isinstance(value, str) and value:
        return value
    if row.event:
        return row.event.registration_number
    return ""


def _alert_resolver(row: Anomaly) -> dict | None:
    user = row.resolved_by
    if not user:
        return None
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.full_name or user.username,
    }


def _latest_alert_snapshot(rows: list[Anomaly]) -> dict | None:
    for row in reversed(rows):
        snapshot = alert_snapshot_metadata(row)
        if snapshot:
            return snapshot
    return None


def _alert_snapshot_value(row: Anomaly, key: str) -> str | int | None:
    snapshot = alert_snapshot_metadata(row)
    value = snapshot.get(key) if snapshot else None
    return value if isinstance(value, (str, int)) else None


def _alert_audit_snapshot(row: Anomaly) -> dict:
    return {
        "id": str(row.id),
        "type": row.anomaly_type.value,
        "severity": row.severity.value,
        "status": "resolved" if row.resolved_at else "open",
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolved_by_user_id": str(row.resolved_by_user_id) if row.resolved_by_user_id else None,
        "resolution_note": row.resolution_note,
    }


def _apply_alert_action(
    rows: list[Anomaly],
    action: Literal["resolve", "reopen"],
    actor_user_id: uuid.UUID,
    note: str | None,
    resolved_at: datetime,
) -> None:
    for row in rows:
        if action == "resolve":
            row.resolved_at = resolved_at
            row.resolved_by_user_id = actor_user_id
            row.resolution_note = note
        else:
            row.resolved_at = None
            row.resolved_by_user_id = None
            row.resolution_note = None
