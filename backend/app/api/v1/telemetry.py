import shutil
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.core.config import settings
from app.db.session import get_db_session
from app.models import AuditLog, TelemetrySpan, TelemetryTrace, User
from app.services.telemetry import TELEMETRY_CATEGORIES, telemetry

router = APIRouter()


@router.get("/categories")
async def telemetry_categories(_: User = Depends(admin_user)) -> dict[str, Any]:
    return {"categories": TELEMETRY_CATEGORIES}


@router.get("/traces")
async def list_traces(
    category: str | None = None,
    status: str | None = None,
    level: str | None = None,
    registration_number: str | None = None,
    q: str | None = None,
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    query = select(TelemetryTrace).order_by(
        TelemetryTrace.started_at.desc(),
        TelemetryTrace.trace_id.desc(),
    )
    if category:
        query = query.where(TelemetryTrace.category == category)
    if status:
        query = query.where(TelemetryTrace.status == status)
    if level:
        query = query.where(TelemetryTrace.level == level)
    if registration_number:
        query = query.where(TelemetryTrace.registration_number.ilike(f"%{registration_number.strip()}%"))
    if from_at:
        query = query.where(TelemetryTrace.started_at >= from_at)
    if to_at:
        query = query.where(TelemetryTrace.started_at <= to_at)
    if q:
        needle = f"%{q.strip()}%"
        query = query.where(
            or_(
                TelemetryTrace.name.ilike(needle),
                TelemetryTrace.summary.ilike(needle),
                TelemetryTrace.actor.ilike(needle),
                TelemetryTrace.source.ilike(needle),
                TelemetryTrace.registration_number.ilike(needle),
                cast(TelemetryTrace.context, String).ilike(needle),
            )
        )
    cursor_started_at, cursor_id = _parse_cursor(cursor)
    if cursor_started_at and cursor_id:
        query = query.where(
            or_(
                TelemetryTrace.started_at < cursor_started_at,
                and_(
                    TelemetryTrace.started_at == cursor_started_at,
                    TelemetryTrace.trace_id < cursor_id,
                ),
            )
        )

    traces = (await session.scalars(query.limit(limit + 1))).all()
    items = traces[:limit]
    next_cursor = _trace_cursor(items[-1]) if len(traces) > limit and items else None
    return {"items": [serialize_trace(trace) for trace in items], "next_cursor": next_cursor}


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    trace = await session.get(TelemetryTrace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Telemetry trace not found.")
    spans = (
        await session.scalars(
            select(TelemetrySpan)
            .where(TelemetrySpan.trace_id == trace_id)
            .order_by(TelemetrySpan.step_order, TelemetrySpan.started_at)
        )
    ).all()
    return {**serialize_trace(trace), "spans": [serialize_span(span) for span in spans]}


@router.get("/audit")
async def list_audit_logs(
    category: str | None = None,
    actor: str | None = None,
    target_entity: str | None = None,
    q: str | None = None,
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    query = select(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    if category:
        query = query.where(AuditLog.category == category)
    if actor:
        query = query.where(AuditLog.actor.ilike(f"%{actor.strip()}%"))
    if target_entity:
        query = query.where(AuditLog.target_entity == target_entity)
    if from_at:
        query = query.where(AuditLog.timestamp >= from_at)
    if to_at:
        query = query.where(AuditLog.timestamp <= to_at)
    if q:
        needle = f"%{q.strip()}%"
        query = query.where(
            or_(
                AuditLog.action.ilike(needle),
                AuditLog.actor.ilike(needle),
                AuditLog.target_entity.ilike(needle),
                AuditLog.target_id.ilike(needle),
                AuditLog.target_label.ilike(needle),
                cast(AuditLog.diff, String).ilike(needle),
                cast(AuditLog.metadata_, String).ilike(needle),
            )
        )
    cursor_timestamp, cursor_id = _parse_cursor(cursor)
    if cursor_timestamp and cursor_id:
        query = query.where(
            or_(
                AuditLog.timestamp < cursor_timestamp,
                and_(
                    AuditLog.timestamp == cursor_timestamp,
                    cast(AuditLog.id, String) < cursor_id,
                ),
            )
        )
    logs = (await session.scalars(query.limit(limit + 1))).all()
    items = logs[:limit]
    next_cursor = _audit_cursor(items[-1]) if len(logs) > limit and items else None
    return {"items": [serialize_audit_log(row) for row in items], "next_cursor": next_cursor}


@router.delete("/purge")
async def purge_telemetry(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await telemetry.flush()
    counts = {
        "traces": await _row_count(session, TelemetryTrace),
        "spans": await _row_count(session, TelemetrySpan),
        "audit_logs": await _row_count(session, AuditLog),
    }

    await session.execute(text("TRUNCATE TABLE telemetry_spans, telemetry_traces, audit_logs RESTART IDENTITY"))
    await session.commit()

    artifact_dir = settings.data_dir / "telemetry-artifacts"
    artifact_files = 0
    if artifact_dir.exists():
        artifact_files = sum(1 for path in artifact_dir.rglob("*") if path.is_file())
        shutil.rmtree(artifact_dir)

    return {
        "status": "purged",
        "deleted": {**counts, "artifact_files": artifact_files},
    }


@router.get("/artifacts/{artifact_id}")
async def telemetry_artifact(
    artifact_id: str,
    _: User = Depends(admin_user),
) -> FileResponse:
    try:
        path, metadata = telemetry.artifact_path(artifact_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Telemetry artifact not found.") from exc
    return FileResponse(
        path,
        media_type=str(metadata.get("content_type") or "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=60"},
    )


def serialize_trace(trace: TelemetryTrace) -> dict[str, Any]:
    return {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "category": trace.category,
        "status": trace.status,
        "level": trace.level,
        "started_at": trace.started_at.isoformat(),
        "ended_at": trace.ended_at.isoformat() if trace.ended_at else None,
        "duration_ms": trace.duration_ms,
        "actor": trace.actor,
        "source": trace.source,
        "registration_number": trace.registration_number,
        "access_event_id": str(trace.access_event_id) if trace.access_event_id else None,
        "summary": trace.summary,
        "context": trace.context or {},
        "error": trace.error,
    }


def serialize_span(span: TelemetrySpan) -> dict[str, Any]:
    return {
        "id": str(span.id),
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "category": span.category,
        "step_order": span.step_order,
        "started_at": span.started_at.isoformat(),
        "ended_at": span.ended_at.isoformat() if span.ended_at else None,
        "duration_ms": span.duration_ms,
        "status": span.status,
        "attributes": span.attributes or {},
        "input_payload": span.input_payload or {},
        "output_payload": span.output_payload or {},
        "error": span.error,
    }


def serialize_audit_log(row: AuditLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "timestamp": row.timestamp.isoformat(),
        "category": row.category,
        "action": row.action,
        "actor": row.actor,
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "target_entity": row.target_entity,
        "target_id": row.target_id,
        "target_label": row.target_label,
        "diff": row.diff or {},
        "metadata": row.metadata_ or {},
        "outcome": row.outcome,
        "level": row.level,
        "trace_id": row.trace_id,
        "request_id": row.request_id,
    }


async def _row_count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def _parse_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor or "|" not in cursor:
        return None, None
    timestamp, row_id = cursor.split("|", 1)
    try:
        return datetime.fromisoformat(timestamp), row_id
    except ValueError:
        return None, None


def _trace_cursor(trace: TelemetryTrace) -> str:
    return f"{trace.started_at.isoformat()}|{trace.trace_id}"


def _audit_cursor(row: AuditLog) -> str:
    return f"{row.timestamp.isoformat()}|{row.id}"
