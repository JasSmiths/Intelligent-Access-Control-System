import uuid
import hashlib
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Date, String, case, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import current_user
from app.api.v1.history import HistoryPage, older_than, range_boundary, read_cursor, site_zone, write_cursor
from app.db.session import AsyncSessionLocal
from app.db.session import get_db_session
from app.models import AccessEvent, Anomaly, MovementSagaRecord, Presence, User
from app.models.enums import AccessDecision, AccessDirection, AnomalySeverity, AnomalyType
from app.services.snapshots import alert_snapshot_metadata, alert_snapshot_path
from app.services.event_bus import event_bus
from app.services.expected_presence import expected_presence_today
from app.services.profile_photos import compact_image_bytes
from app.services.settings import get_runtime_config
from app.services.snapshots import access_event_snapshot_payload, get_snapshot_manager
from app.services.telemetry import TELEMETRY_CATEGORY_ACCESS, actor_from_user, write_audit_log
from app.services.action_confirmations import (
    ActionConfirmationError, create_action_confirmation, consume_action_confirmation,
)

router = APIRouter()


@router.get("/events/history", response_model=HistoryPage[dict[str, Any]])
async def events_history(
    limit: int = Query(default=50, ge=1, le=250),
    cursor: str | None = Query(default=None, max_length=1024),
    q: str | None = Query(default=None, max_length=120),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    direction: AccessDirection | None = None,
    decision: AccessDecision | None = None,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> HistoryPage[dict[str, Any]]:
    zone = site_zone((await get_runtime_config()).site_timezone)
    start, end = range_boundary(from_, zone), range_boundary(to, zone)
    if start and end and start >= end:
        raise HTTPException(status_code=422, detail="History start must be before end.")
    filters = {"q": (q or "").strip(), "from": start, "to": end, "direction": direction, "decision": decision}
    as_of, after_stamp, after_id = read_cursor(cursor, filters)
    query = select(AccessEvent).options(selectinload(AccessEvent.anomalies)).where(AccessEvent.created_at <= as_of)
    if start:
        query = query.where(AccessEvent.occurred_at >= start)
    if end:
        query = query.where(AccessEvent.occurred_at < end)
    if direction:
        query = query.where(AccessEvent.direction == direction)
    if decision:
        query = query.where(AccessEvent.decision == decision)
    if filters["q"]:
        pattern = f"%{filters['q']}%"
        query = query.where(or_(AccessEvent.registration_number.ilike(pattern), AccessEvent.source.ilike(pattern)))
    if after_stamp and after_id:
        query = query.where(older_than(AccessEvent.occurred_at, AccessEvent.id, after_stamp, after_id))
    rows = (await session.scalars(query.order_by(AccessEvent.occurred_at.desc(), AccessEvent.id.desc()).limit(limit + 1))).all()
    page_rows = rows[:limit]
    movement_rows = (await session.scalars(select(MovementSagaRecord).where(
        MovementSagaRecord.access_event_id.in_([row.id for row in page_rows])
    ))).all() if page_rows else []
    movement_by_event = {row.access_event_id: row for row in movement_rows}
    next_cursor = write_cursor(as_of, page_rows[-1].occurred_at, page_rows[-1].id, filters) if len(rows) > limit else None
    return HistoryPage(items=[_serialize_event(row, movement_by_event.get(row.id), verify_snapshot_available=False)
                              for row in page_rows], next_cursor=next_cursor, as_of=as_of)


class AlertActionRequest(BaseModel):
    alert_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    action: Literal["resolve", "reopen"]
    note: str | None = Field(default=None, max_length=1000)
    group_id: str | None = Field(default=None, max_length=200)
    confirmation_token: str | None = Field(default=None, max_length=160)


class AlertGroupConfirmationRequest(BaseModel):
    group_id: str = Field(max_length=200)
    as_of: datetime
    member_hash: str = Field(min_length=64, max_length=64)
    count: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1000)


def _alert_member_hash(ids: list[uuid.UUID]) -> str:
    return hashlib.sha256(",".join(sorted(str(item) for item in ids)).encode()).hexdigest()


def _group_identity(group_id: str) -> tuple[str, str]:
    parts = group_id.split(":", 3)
    if len(parts) != 4 or parts[:2] != ["group", "unauthorized_plate"] or not parts[2]:
        raise HTTPException(status_code=422, detail="Invalid alert group target.")
    return parts[3], parts[2]


async def _group_rows(session: AsyncSession, group_id: str, as_of: datetime, timezone: ZoneInfo,
                      *, lock: bool = False) -> list[Anomaly]:
    registration, local_date = _group_identity(group_id)
    try:
        day = datetime.fromisoformat(local_date).date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid alert group date.") from exc
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone).astimezone(UTC)
    end = datetime.combine(day.fromordinal(day.toordinal() + 1), datetime.min.time(), tzinfo=timezone).astimezone(UTC)
    query = (select(Anomaly).options(selectinload(Anomaly.event))
             .where(Anomaly.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE,
                    Anomaly.resolved_at.is_(None), Anomaly.created_at >= start,
                    Anomaly.created_at < end, Anomaly.created_at <= as_of)
             .order_by(Anomaly.id))
    if lock:
        query = query.with_for_update(of=Anomaly)
    return [row for row in (await session.scalars(query)).all() if _alert_registration_number(row) == registration]


@router.post("/alerts/groups/confirmation")
async def confirm_alert_group(
    request: AlertGroupConfirmationRequest,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if request.as_of.tzinfo is None or request.as_of > datetime.now(UTC):
        raise HTTPException(status_code=422, detail="Invalid alert history cutoff.")
    timezone = _alert_timezone((await get_runtime_config()).site_timezone)
    rows = await _group_rows(session, request.group_id, request.as_of, timezone)
    ids = [row.id for row in rows]
    if len(ids) != request.count or _alert_member_hash(ids) != request.member_hash:
        raise HTTPException(status_code=409, detail="Alert group changed. Refresh and review it again.")
    note = request.note.strip() if request.note else None
    payload = {"group_id": request.group_id, "action": "resolve", "note": note}
    confirmation = await create_action_confirmation(
        session, user=actor, action="alert.group.resolve", payload=payload,
        target_entity="AlertGroup", target_id=request.group_id, target_label=f"{len(ids)} alerts",
        reason="Resolve reviewed alert group", metadata={"alert_ids": [str(item) for item in ids],
                                                       "member_hash": request.member_hash},
    )
    return {**confirmation, "count": len(ids), "member_hash": request.member_hash, "as_of": request.as_of.isoformat()}


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

    return [
        _serialize_event(event, movement_by_event_id.get(event.id), verify_snapshot_available=False)
        for event in events
    ]


def _serialize_event(
    event: AccessEvent,
    movement_saga: MovementSagaRecord | None = None,
    *,
    verify_snapshot_available: bool = True,
) -> dict:
    visitor_pass = _event_visitor_pass_payload(event)
    external_admission = _event_external_admission_payload(event)
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
        "external_admission_mode": _optional_text(external_admission.get("mode")),
        "external_admission_source": _optional_text(external_admission.get("source")),
        "movement_saga": _event_movement_saga_payload(event, movement_saga),
    }
    payload.update(access_event_snapshot_payload(event, verify_available=verify_snapshot_available))
    return payload


@router.get("/events/{event_id}")
async def event_detail(
    event_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    event = await session.scalar(select(AccessEvent).options(selectinload(AccessEvent.anomalies)).where(AccessEvent.id == event_id))
    if not event:
        raise HTTPException(status_code=404, detail="Event was not found.")
    movement = await session.scalar(select(MovementSagaRecord).where(MovementSagaRecord.access_event_id == event_id))
    return _serialize_event(event, movement, verify_snapshot_available=False)


@router.get("/events/{event_id}/snapshot", response_model=None)
async def event_snapshot(
    event_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
    variant: Literal["thumb", "full"] = Query(default="full"),
):
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
    if variant == "thumb":
        try:
            content_type, content = compact_image_bytes(
                path.read_bytes(),
                row.snapshot_content_type or "image/jpeg",
                max_edge_px=96,
            )
            return Response(
                content=content,
                media_type=content_type,
                headers={"Cache-Control": "private, no-store"},
            )
        except OSError:
            pass
    return FileResponse(
        path,
        media_type=row.snapshot_content_type or "image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


def _event_visitor_pass_payload(event: AccessEvent) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    payload = raw_payload.get("visitor_pass")
    return payload if isinstance(payload, dict) else {}


def _event_external_admission_payload(event: AccessEvent) -> dict[str, Any]:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    payload = raw_payload.get("external_admission")
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
    return None


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


@router.get("/alerts/history", response_model=HistoryPage[dict[str, Any]])
async def alerts_history(
    status_filter: Literal["open", "resolved", "all"] = Query(default="open", alias="status"),
    severity: AnomalySeverity | None = None,
    type_filter: AnomalyType | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None, max_length=120),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    limit: int = Query(default=50, ge=1, le=250),
    cursor: str | None = Query(default=None, max_length=1024),
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> HistoryPage[dict[str, Any]]:
    timezone = _alert_timezone((await get_runtime_config()).site_timezone)
    start, end = range_boundary(from_, timezone), range_boundary(to, timezone)
    if start and end and start >= end:
        raise HTTPException(status_code=422, detail="History start must be before end.")
    filters = {"status": status_filter, "severity": severity, "type": type_filter,
               "q": (q or "").strip().lower(), "from": start, "to": end}
    as_of, after_stamp, after_id = read_cursor(cursor, filters)
    context_plate = case(
        (func.jsonb_typeof(Anomaly.context["registration_number"]) == "string",
         func.nullif(Anomaly.context["registration_number"].astext, "")),
        else_=None,
    )
    plate = func.coalesce(context_plate, AccessEvent.registration_number, "")
    local_day = cast(func.timezone(timezone.key, Anomaly.created_at), Date)
    grouped = (Anomaly.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE) & Anomaly.resolved_at.is_(None)
    group_key = case(
        (grouped, func.concat("group:unauthorized_plate:", cast(local_day, String), ":", plate)),
        else_=cast(Anomaly.id, String),
    )
    display_severity = case((Anomaly.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE,
                             literal(AnomalySeverity.WARNING.name)), else_=cast(Anomaly.severity, String))
    member_match = None
    if filters["q"]:
        pattern = f"%{filters['q']}%"
        display_message = case((Anomaly.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE,
                                literal("Unauthorised Plate, Access Denied")), else_=Anomaly.message)
        member_match = or_(plate.ilike(pattern), display_message.ilike(pattern),
                           Anomaly.message.ilike(pattern),
                           cast(Anomaly.context, String).ilike(pattern),
                           cast(Anomaly.anomaly_type, String).ilike(pattern))
    columns = [
        Anomaly.id.label("id"), group_key.label("group_key"), Anomaly.created_at.label("stamp"),
        display_severity.label("display_severity"),
        func.row_number().over(partition_by=group_key,
                               order_by=(Anomaly.created_at.desc(), Anomaly.id.desc())).label("rank"),
    ]
    if member_match is not None:
        columns.append(func.bool_or(member_match).over(partition_by=group_key).label("matches"))
    query = select(*columns).outerjoin(AccessEvent, Anomaly.event_id == AccessEvent.id).where(Anomaly.created_at <= as_of)
    if status_filter == "open":
        query = query.where(Anomaly.resolved_at.is_(None))
    elif status_filter == "resolved":
        query = query.where(Anomaly.resolved_at.is_not(None))
    if type_filter:
        query = query.where(Anomaly.anomaly_type == type_filter)
    ranked = query.subquery()
    summary = select(ranked.c.id, ranked.c.group_key, ranked.c.stamp).where(ranked.c.rank == 1)
    if severity:
        summary = summary.where(ranked.c.display_severity == severity.name)
    if member_match is not None:
        summary = summary.where(ranked.c.matches.is_(True))
    if start:
        summary = summary.where(ranked.c.stamp >= start)
    if end:
        summary = summary.where(ranked.c.stamp < end)
    if after_stamp and after_id:
        summary = summary.where(older_than(ranked.c.stamp, ranked.c.id, after_stamp, after_id))
    page_keys = (await session.execute(summary.order_by(ranked.c.stamp.desc(), ranked.c.id.desc()).limit(limit + 1))).all()
    selected = page_keys[:limit]
    if not selected:
        return HistoryPage(items=[], next_cursor=None, as_of=as_of)
    selected_keys = [item.group_key for item in selected]
    member_query = (select(Anomaly).outerjoin(AccessEvent, Anomaly.event_id == AccessEvent.id)
                    .options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
                    .where(Anomaly.created_at <= as_of, group_key.in_(selected_keys)))
    if status_filter == "open":
        member_query = member_query.where(Anomaly.resolved_at.is_(None))
    elif status_filter == "resolved":
        member_query = member_query.where(Anomaly.resolved_at.is_not(None))
    if type_filter:
        member_query = member_query.where(Anomaly.anomaly_type == type_filter)
    rows = (await session.scalars(member_query.order_by(Anomaly.created_at.desc(), Anomaly.id.desc()))).all()
    payload_by_key = {item["id"]: item for item in _serialize_alerts(rows, timezone)}
    try:
        page_items = [payload_by_key[item.group_key] for item in selected]
    except KeyError as exc:
        raise HTTPException(status_code=409, detail="Alert history changed during this read. Refresh the page.") from exc
    next_cursor = None
    if len(page_keys) > limit:
        last = selected[-1]
        next_cursor = write_cursor(as_of, last.stamp, last.id, filters)
    return HistoryPage(items=page_items, next_cursor=next_cursor, as_of=as_of)


@router.patch("/alerts/action")
async def action_alerts(
    request: AlertActionRequest,
    actor: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    if request.group_id:
        if request.alert_ids or request.action != "resolve":
            raise HTTPException(status_code=422, detail="Invalid alert group action.")
        note = request.note.strip() if request.note else None
        try:
            confirmation = await consume_action_confirmation(
                session, user=actor, action="alert.group.resolve",
                payload={"group_id": request.group_id, "action": "resolve", "note": note},
                confirmation_token=request.confirmation_token, commit=False,
            )
        except ActionConfirmationError as exc:
            await session.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        frozen_ids = (confirmation.metadata_ or {}).get("alert_ids")
        if not isinstance(frozen_ids, list) or not frozen_ids or any(not isinstance(item, str) for item in frozen_ids):
            await session.rollback()
            raise HTTPException(status_code=409, detail="Alert group confirmation is invalid.")
        alert_ids = [uuid.UUID(item) for item in frozen_ids]
    else:
        alert_ids = list(dict.fromkeys(request.alert_ids))
        if not alert_ids or request.confirmation_token:
            raise HTTPException(status_code=422, detail="Alert IDs are required.")
    rows = (await session.scalars(
        select(Anomaly).options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
        .where(Anomaly.id.in_(alert_ids)).order_by(Anomaly.id).with_for_update(of=Anomaly)
    )).all()
    if len(rows) != len(alert_ids):
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more alerts were not found.")
    if request.group_id:
        registration, local_date = _group_identity(request.group_id)
        timezone = _alert_timezone((await get_runtime_config()).site_timezone)
        if any(row.resolved_at is not None or row.anomaly_type != AnomalyType.UNAUTHORIZED_PLATE or
               _alert_registration_number(row) != registration or
               row.created_at.astimezone(timezone).date().isoformat() != local_date for row in rows):
            await session.rollback()
            raise HTTPException(status_code=409, detail="Alert group changed. Refresh and review it again.")

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
    try:
        await event_bus.publish(
            "alerts.updated",
            {"action": request.action, "alert_ids": [str(row.id) for row in rows], "count": len(rows)},
        )
    except Exception:
        # The audited transaction has committed; a refresh will see its result.
        pass
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


@router.get("/alerts/{alert_id}")
async def alert_detail(
    alert_id: uuid.UUID,
    _: User = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    row = await session.scalar(select(Anomaly).options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
                               .where(Anomaly.id == alert_id))
    if not row:
        raise HTTPException(status_code=404, detail="Alert was not found.")
    if _should_group_unknown_plate(row):
        timezone = _alert_timezone((await get_runtime_config()).site_timezone)
        group_id = f"group:unauthorized_plate:{row.created_at.astimezone(timezone).date().isoformat()}:{_alert_registration_number(row)}"
        as_of = datetime.now(UTC)
        rows = await _group_rows(session, group_id, as_of, timezone)
        if not rows:
            raise HTTPException(status_code=409, detail="Alert changed during this read. Refresh the page.")
        return {**_serialize_alerts(rows, timezone)[0], "as_of": as_of.isoformat()}
    return _serialize_alert(row)


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
        ordered = sorted(group_rows, key=lambda item: (item.created_at, item.id))
        first_seen = ordered[0].created_at
        last_seen = ordered[-1].created_at
        count = len(ordered)
        snapshot = _latest_alert_snapshot(ordered)
        items.append(
            {
                "id": f"group:unauthorized_plate:{local_date}:{registration}",
                "alert_ids": [str(item.id) for item in ordered],
                "member_hash": _alert_member_hash([item.id for item in ordered]),
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
        "member_hash": None,
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
