import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import admin_user
from app.core.config import settings
from app.db.session import get_db_session
from app.models import AccessEvent, AuditLog, AutomationRun, GateCommandRecord, MovementSagaRecord, TelemetrySpan, TelemetryTrace, User
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation
from app.services.lpr_timing import get_lpr_timing_recorder
from app.services.telemetry import (
    TELEMETRY_CATEGORIES,
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    sanitize_payload,
    telemetry,
    write_audit_log,
)

router = APIRouter()


class TelemetryPurgeRequest(BaseModel):
    scope: str = Field(default="telemetry", pattern="^(telemetry|full)$")
    confirmation_token: str | None = Field(default=None, max_length=160)


@router.get("/categories")
async def telemetry_categories(_: User = Depends(admin_user)) -> dict[str, Any]:
    return {"categories": TELEMETRY_CATEGORIES}


@router.get("/summary")
async def telemetry_summary(
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await telemetry.flush()
    trace_filters = _time_filters(TelemetryTrace.started_at, from_at, to_at)
    audit_filters = _time_filters(AuditLog.timestamp, from_at, to_at)

    trace_total = await _filtered_row_count(session, TelemetryTrace, trace_filters)
    audit_total = await _filtered_row_count(session, AuditLog, audit_filters)
    database_size_bytes = await _telemetry_database_size(session)
    log_file_size_bytes, log_file_count = _log_directory_size()
    artifact_size_bytes, artifact_file_count = _directory_size([settings.data_dir / "telemetry-artifacts"])
    storage = _telemetry_storage_payload(
        database_size_bytes=database_size_bytes,
        log_file_size_bytes=log_file_size_bytes,
        artifact_size_bytes=artifact_size_bytes,
        file_count=log_file_count + artifact_file_count,
    )

    return {
        "traces": {
            "total": trace_total,
            "by_category": await _group_counts(session, TelemetryTrace.category, trace_filters),
            "by_level": await _group_counts(session, TelemetryTrace.level, trace_filters),
            "by_status": await _group_counts(session, TelemetryTrace.status, trace_filters),
        },
        "audit": {
            "total": audit_total,
            "by_category": await _group_counts(session, AuditLog.category, audit_filters),
            "by_level": await _group_counts(session, AuditLog.level, audit_filters),
            "by_outcome": await _group_counts(session, AuditLog.outcome, audit_filters),
        },
        "storage": storage,
        "updated_at": datetime.now().isoformat(),
    }


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
    return {"items": [await serialize_trace_with_links(session, trace) for trace in items], "next_cursor": next_cursor}


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
    return {**(await serialize_trace_with_links(session, trace)), "spans": [serialize_span(span) for span in spans]}


@router.get("/lpr-waterfall")
async def get_lpr_waterfall_by_access_event(
    access_event_id: uuid.UUID = Query(...),
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await _lpr_waterfall_payload(session, trace_id="", access_event_id=access_event_id)


@router.get("/lpr-waterfall/{trace_id}")
async def get_lpr_waterfall(
    trace_id: str,
    access_event_id: uuid.UUID | None = None,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return await _lpr_waterfall_payload(session, trace_id=trace_id, access_event_id=access_event_id)


@router.get("/audit")
async def list_audit_logs(
    category: str | None = None,
    action_prefix: str | None = None,
    actor: str | None = None,
    target_entity: str | None = None,
    level: str | None = None,
    outcome: str | None = None,
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
    if action_prefix:
        query = query.where(AuditLog.action.ilike(f"{action_prefix.strip()}%"))
    if actor:
        query = query.where(AuditLog.actor.ilike(f"%{actor.strip()}%"))
    if target_entity:
        query = query.where(AuditLog.target_entity == target_entity)
    if level:
        query = query.where(AuditLog.level == level)
    if outcome:
        query = query.where(AuditLog.outcome == outcome)
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


@router.get("/storage")
async def telemetry_storage(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await telemetry.flush()
    database_size_bytes = await _telemetry_database_size(session)
    log_file_size_bytes, log_file_count = _log_directory_size()
    artifact_size_bytes, artifact_file_count = _directory_size([settings.data_dir / "telemetry-artifacts"])
    return _telemetry_storage_payload(
        database_size_bytes=database_size_bytes,
        log_file_size_bytes=log_file_size_bytes,
        artifact_size_bytes=artifact_size_bytes,
        file_count=log_file_count + artifact_file_count,
    )


@router.delete("/purge")
async def purge_telemetry(
    request: TelemetryPurgeRequest | None = Body(default=None),
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    scope = request.scope if request else "telemetry"
    confirmation_payload = {"scope": scope}
    try:
        await consume_action_confirmation(
            session,
            user=user,
            action="telemetry.purge",
            payload=confirmation_payload,
            confirmation_token=request.confirmation_token if request else None,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    await telemetry.flush()
    include_audit = scope == "full"
    include_file_logs = scope == "full"
    counts = {
        "traces": await _row_count(session, TelemetryTrace),
        "spans": await _row_count(session, TelemetrySpan),
        "audit_logs": await _row_count(session, AuditLog),
    }

    artifact_dir = settings.data_dir / "telemetry-artifacts"
    artifact_files = 0
    if artifact_dir.exists():
        artifact_files = sum(1 for path in artifact_dir.rglob("*") if path.is_file())
        shutil.rmtree(artifact_dir)

    log_file_stats = _clear_log_files() if include_file_logs else {"files": 0, "bytes": 0}
    truncate_tables = "telemetry_spans, telemetry_traces"
    if include_audit:
        truncate_tables = f"{truncate_tables}, audit_logs"
    await session.execute(text(f"TRUNCATE TABLE {truncate_tables} RESTART IDENTITY"))
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="telemetry.purge",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Telemetry",
        target_label="All logs" if scope == "full" else "Telemetry traces and artifacts",
        metadata={
            "scope": scope,
            "deleted": {
                **counts,
                "audit_logs_preserved": 0 if include_audit else counts["audit_logs"],
                "artifact_files": artifact_files,
                "log_files": log_file_stats["files"],
                "log_file_bytes": log_file_stats["bytes"],
            },
        },
    )
    await session.commit()

    return {
        "status": "purged",
        "scope": scope,
        "deleted": {
            **counts,
            "audit_logs_preserved": 0 if include_audit else counts["audit_logs"],
            "artifact_files": artifact_files,
            "log_files": log_file_stats["files"],
            "log_file_bytes": log_file_stats["bytes"],
        },
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


async def _lpr_waterfall_payload(
    session: AsyncSession,
    *,
    trace_id: str,
    access_event_id: uuid.UUID | None,
) -> dict[str, Any]:
    await telemetry.flush()
    trace, access_event = await _resolve_lpr_waterfall_subject(
        session,
        trace_id=trace_id,
        access_event_id=access_event_id,
    )
    if not trace and not access_event:
        raise HTTPException(status_code=404, detail="LPR trace or access event not found.")

    spans = await _spans_for_trace(session, trace.trace_id) if trace else []
    movement_saga = await _movement_saga_for_access_event(session, access_event) if access_event else None
    gate_commands = await _gate_commands_for_waterfall(session, access_event, movement_saga)
    webhook_trace = _webhook_trace_payload(trace, access_event)
    durable_latency = _durable_latency_payload(
        trace=trace,
        spans=spans,
        access_event=access_event,
        webhook_trace=webhook_trace,
    )
    recent_observations = await _recent_lpr_timing_observations(trace, access_event)

    return {
        "trace": serialize_trace(trace) if trace else None,
        "spans": [serialize_span(span) for span in spans],
        "access_event": _serialize_access_event(access_event) if access_event else None,
        "movement_saga": _serialize_movement_saga(movement_saga) if movement_saga else None,
        "gate_commands": [_serialize_gate_command(command) for command in gate_commands],
        "webhook_trace": webhook_trace,
        "durable_latency": durable_latency,
        "recent_lpr_timing_observations": recent_observations,
    }


async def _resolve_lpr_waterfall_subject(
    session: AsyncSession,
    *,
    trace_id: str,
    access_event_id: uuid.UUID | None,
) -> tuple[TelemetryTrace | None, AccessEvent | None]:
    event = await session.get(AccessEvent, access_event_id) if access_event_id else None
    trace = await _trace_for_access_event(session, event) if event else None
    if trace or event:
        return trace, event

    trace = await session.get(TelemetryTrace, trace_id)
    if trace:
        return trace, await _access_event_for_trace(session, trace)

    event_uuid = _parse_uuid(trace_id)
    if not event_uuid:
        return None, None
    event = await session.get(AccessEvent, event_uuid)
    if not event:
        return None, None
    return await _trace_for_access_event(session, event), event


async def _trace_for_access_event(session: AsyncSession, event: AccessEvent | None) -> TelemetryTrace | None:
    if not event:
        return None
    event_trace_id = _trace_id_from_access_event(event)
    if event_trace_id:
        trace = await session.get(TelemetryTrace, event_trace_id)
        if trace:
            return trace
    return await session.scalar(
        select(TelemetryTrace)
        .where(TelemetryTrace.access_event_id == event.id)
        .order_by(TelemetryTrace.started_at.desc(), TelemetryTrace.trace_id.desc())
        .limit(1)
    )


async def _access_event_for_trace(session: AsyncSession, trace: TelemetryTrace) -> AccessEvent | None:
    if trace.access_event_id:
        event = await session.get(AccessEvent, trace.access_event_id)
        if event:
            return event
    return await session.scalar(
        select(AccessEvent)
        .where(cast(AccessEvent.raw_payload, String).ilike(f"%{trace.trace_id}%"))
        .order_by(AccessEvent.created_at.desc())
        .limit(1)
    )


async def _spans_for_trace(session: AsyncSession, trace_id: str) -> list[TelemetrySpan]:
    return (
        await session.scalars(
            select(TelemetrySpan)
            .where(TelemetrySpan.trace_id == trace_id)
            .order_by(TelemetrySpan.step_order, TelemetrySpan.started_at)
        )
    ).all()


async def _movement_saga_for_access_event(
    session: AsyncSession,
    event: AccessEvent | None,
) -> MovementSagaRecord | None:
    if not event:
        return None
    return await session.scalar(
        select(MovementSagaRecord)
        .options(selectinload(MovementSagaRecord.gate_commands))
        .where(MovementSagaRecord.access_event_id == event.id)
        .order_by(MovementSagaRecord.created_at.desc())
        .limit(1)
    )


async def _gate_commands_for_waterfall(
    session: AsyncSession,
    event: AccessEvent | None,
    movement_saga: MovementSagaRecord | None,
) -> list[GateCommandRecord]:
    commands = list(getattr(movement_saga, "gate_commands", []) or [])
    if event and not commands:
        commands = (
            await session.scalars(
                select(GateCommandRecord)
                .where(GateCommandRecord.access_event_id == event.id)
                .order_by(GateCommandRecord.created_at.desc())
            )
        ).all()
    seen: set[str] = set()
    deduped: list[GateCommandRecord] = []
    for command in commands:
        command_id = str(command.id)
        if command_id in seen:
            continue
        seen.add(command_id)
        deduped.append(command)
    return deduped


def _trace_id_from_access_event(event: AccessEvent) -> str | None:
    raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
    telemetry_payload = raw_payload.get("telemetry")
    if isinstance(telemetry_payload, dict):
        trace_id = str(telemetry_payload.get("trace_id") or "").strip()
        if trace_id:
            return trace_id
    return None


def _webhook_trace_payload(
    trace: TelemetryTrace | None,
    event: AccessEvent | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if trace and isinstance(trace.context, dict):
        context_trace = trace.context.get("webhook_trace")
        if isinstance(context_trace, dict):
            payload.update(context_trace)
        for key in ("captured_to_webhook_ms", "webhook_received_at", "webhook_to_finalize_ms"):
            if key in trace.context and key not in payload:
                payload[key] = trace.context.get(key)
    if event and isinstance(event.raw_payload, dict):
        event_trace = event.raw_payload.get("webhook_trace")
        if isinstance(event_trace, dict):
            payload.update(event_trace)
        event_ingest = event.raw_payload.get("_iacs_ingest")
        if isinstance(event_ingest, dict):
            payload.update(_webhook_ingest_payload(event_ingest))
        best_payload = event.raw_payload.get("best")
        if isinstance(best_payload, dict):
            best_ingest = best_payload.get("_iacs_ingest")
            if isinstance(best_ingest, dict):
                payload = {**_webhook_ingest_payload(best_ingest), **payload}
            best_trace = best_payload.get("webhook_trace")
            if isinstance(best_trace, dict):
                payload = {**best_trace, **payload}
    return sanitize_payload(payload) if payload else {}


def _webhook_ingest_payload(ingest: dict[str, Any]) -> dict[str, Any]:
    payload = dict(ingest)
    webhook_received_at = ingest.get("webhook_received_at")
    if webhook_received_at is not None:
        payload.setdefault("received_at", webhook_received_at)
    return payload


def _durable_latency_payload(
    *,
    trace: TelemetryTrace | None,
    spans: list[TelemetrySpan],
    access_event: AccessEvent | None,
    webhook_trace: dict[str, Any],
) -> dict[str, Any]:
    raw_payload = access_event.raw_payload if access_event and isinstance(access_event.raw_payload, dict) else {}
    debounce_payload = raw_payload.get("debounce") if isinstance(raw_payload.get("debounce"), dict) else {}
    trace_context = trace.context if trace and isinstance(trace.context, dict) else {}

    captured_at = (
        _datetime_from_payload(webhook_trace.get("captured_at"))
        or (access_event.occurred_at if access_event else None)
        or (trace.started_at if trace else None)
    )
    webhook_received_at = (
        _datetime_from_payload(webhook_trace.get("received_at"))
        or _datetime_from_payload(trace_context.get("webhook_received_at"))
    )
    debounce_first_seen = (
        _datetime_from_payload(debounce_payload.get("first_seen"))
        or _datetime_from_payload(trace_context.get("first_seen"))
        or (trace.started_at if trace else None)
    )
    debounce_updated_at = _datetime_from_payload(debounce_payload.get("updated_at"))
    debounce_finalized_at = (
        _datetime_from_payload(debounce_payload.get("finalize_started_at"))
        or _datetime_from_payload(trace_context.get("finalize_started_at"))
        or _span_end(spans, "Webhook Receipt to Debounce Finalization")
    )
    access_event_created_at = access_event.created_at if access_event else None

    captured_to_webhook_ms = _float_from_payload(webhook_trace.get("captured_to_webhook_ms"))
    if captured_to_webhook_ms is None:
        captured_to_webhook_ms = _duration_ms(webhook_received_at, captured_at)

    return {
        "captured_at": _isoformat(captured_at),
        "webhook_received_at": _isoformat(webhook_received_at),
        "debounce_first_seen": _isoformat(debounce_first_seen),
        "debounce_updated_at": _isoformat(debounce_updated_at),
        "debounce_finalized_at": _isoformat(debounce_finalized_at),
        "access_event_occurred_at": _isoformat(access_event.occurred_at if access_event else None),
        "access_event_created_at": _isoformat(access_event_created_at),
        "trace_started_at": _isoformat(trace.started_at if trace else None),
        "trace_ended_at": _isoformat(trace.ended_at if trace else None),
        "captured_to_webhook_ms": captured_to_webhook_ms,
        "webhook_to_debounce_finalize_ms": _duration_ms(debounce_finalized_at, webhook_received_at),
        "captured_to_debounce_finalize_ms": _duration_ms(debounce_finalized_at, captured_at),
        "debounce_finalize_to_access_event_created_ms": _duration_ms(access_event_created_at, debounce_finalized_at),
        "captured_to_access_event_created_ms": _duration_ms(access_event_created_at, captured_at),
        "trace_duration_ms": trace.duration_ms if trace else None,
    }


async def _recent_lpr_timing_observations(
    trace: TelemetryTrace | None,
    access_event: AccessEvent | None,
) -> list[dict[str, Any]]:
    observations = await get_lpr_timing_recorder().recent(limit=200)
    registration_number = _normalize_plate(
        (trace.registration_number if trace else None)
        or (access_event.registration_number if access_event else None)
        or ""
    )
    raw_payload = access_event.raw_payload if access_event and isinstance(access_event.raw_payload, dict) else {}
    event_ids = set(_payload_values(raw_payload, ("eventId", "event_id")))
    camera_ids = set(_payload_values(raw_payload, ("cameraId", "camera_id", "sensorId", "sensor_id")))
    matched = [
        observation
        for observation in observations
        if _lpr_observation_matches(
            observation,
            registration_number=registration_number,
            event_ids=event_ids,
            camera_ids=camera_ids,
        )
    ]
    return matched[:25]


def _lpr_observation_matches(
    observation: dict[str, Any],
    *,
    registration_number: str,
    event_ids: set[str],
    camera_ids: set[str],
) -> bool:
    observed_plate = _normalize_plate(str(observation.get("registration_number") or observation.get("raw_value") or ""))
    if registration_number and observed_plate == registration_number:
        return True
    event_id = str(observation.get("event_id") or "").strip()
    if event_id and event_id in event_ids:
        return True
    camera_id = str(observation.get("camera_id") or "").strip()
    return bool(camera_id and camera_id in camera_ids)


def _serialize_access_event(event: AccessEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
        "person_id": str(event.person_id) if event.person_id else None,
        "registration_number": event.registration_number,
        "direction": _enum_value(event.direction),
        "decision": _enum_value(event.decision),
        "confidence": event.confidence,
        "source": event.source,
        "occurred_at": event.occurred_at.isoformat(),
        "timing_classification": _enum_value(event.timing_classification),
        "snapshot_path": event.snapshot_path,
        "snapshot_bytes": event.snapshot_bytes,
        "snapshot_captured_at": _isoformat(event.snapshot_captured_at),
        "snapshot_camera": event.snapshot_camera,
        "created_at": _isoformat(event.created_at),
        "updated_at": _isoformat(event.updated_at),
        "raw_payload": sanitize_payload(event.raw_payload or {}),
    }


def _serialize_movement_saga(row: MovementSagaRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "idempotency_key": row.idempotency_key,
        "source": row.source,
        "state": _enum_value(row.state),
        "access_event_id": str(row.access_event_id) if row.access_event_id else None,
        "person_id": str(row.person_id) if row.person_id else None,
        "vehicle_id": str(row.vehicle_id) if row.vehicle_id else None,
        "registration_number": row.registration_number,
        "direction": _enum_value(row.direction),
        "decision": _enum_value(row.decision),
        "occurred_at": row.occurred_at.isoformat(),
        "gate_command_required": row.gate_command_required,
        "presence_committed": row.presence_committed,
        "reconciliation_required": row.reconciliation_required,
        "failure_detail": row.failure_detail,
        "intent_payload": sanitize_payload(row.intent_payload or {}),
        "decision_payload": sanitize_payload(row.decision_payload or {}),
        "state_history": sanitize_payload(row.state_history or []),
        "created_at": _isoformat(row.created_at),
        "updated_at": _isoformat(row.updated_at),
    }


def _serialize_gate_command(row: GateCommandRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "idempotency_key": row.idempotency_key,
        "movement_saga_id": str(row.movement_saga_id) if row.movement_saga_id else None,
        "access_event_id": str(row.access_event_id) if row.access_event_id else None,
        "state": _enum_value(row.state),
        "action": row.action,
        "source": row.source,
        "gate_key": row.gate_key,
        "controller": row.controller,
        "reason": row.reason,
        "actor": row.actor,
        "registration_number": row.registration_number,
        "bypass_schedule": row.bypass_schedule,
        "leased_at": _isoformat(row.leased_at),
        "lease_expires_at": _isoformat(row.lease_expires_at),
        "started_at": _isoformat(row.started_at),
        "completed_at": _isoformat(row.completed_at),
        "accepted": row.accepted,
        "gate_state": row.gate_state,
        "detail": row.detail,
        "mechanically_confirmed": row.mechanically_confirmed,
        "requires_reconciliation": row.requires_reconciliation,
        "exception_class": row.exception_class,
        "metadata": sanitize_payload(row.command_metadata or {}),
        "created_at": _isoformat(row.created_at),
        "updated_at": _isoformat(row.updated_at),
    }


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


async def serialize_trace_with_links(session: AsyncSession, trace: TelemetryTrace) -> dict[str, Any]:
    payload = serialize_trace(trace)
    if trace.category == "automation_engine":
        payload["context"] = await _automation_trace_context(session, trace, payload["context"])
    return payload


async def _automation_trace_context(
    session: AsyncSession,
    trace: TelemetryTrace,
    context: dict[str, Any],
) -> dict[str, Any]:
    run_id = _parse_uuid(context.get("run_id"))
    run = await session.get(AutomationRun, run_id) if run_id else None
    if not run and trace.trace_id:
        run = await session.scalar(select(AutomationRun).where(AutomationRun.trace_id == trace.trace_id))
    if not run:
        return context

    condition_results = sanitize_payload(run.condition_results or [])
    action_results = sanitize_payload(run.action_results or [])
    skip_reason = _automation_skip_reason(
        run.status,
        condition_results if isinstance(condition_results, list) else [],
        action_results if isinstance(action_results, list) else [],
        run.error,
    )
    enriched = {
        **context,
        "run_id": str(run.id),
        "status": run.status,
        "trigger_key": run.trigger_key,
        "condition_count": len(condition_results) if isinstance(condition_results, list) else 0,
        "action_count": len(action_results) if isinstance(action_results, list) else 0,
        "condition_results": condition_results,
        "action_results": action_results,
        "run_error": run.error,
    }
    if skip_reason:
        enriched["skip_reason"] = skip_reason
    return enriched


def _automation_skip_reason(
    status: str,
    condition_results: list[Any],
    action_results: list[Any],
    error: str | None,
) -> str:
    if error:
        return error
    if status != "skipped":
        return ""
    for result in condition_results:
        if _is_falsey_result(result, "passed"):
            reason = _result_reason(result)
            if reason:
                return reason
    for result in action_results:
        if _result_matches_status(result, {"skipped", "failed"}):
            reason = _result_reason(result)
            if reason:
                return reason
    return "Automation run was skipped."


def _is_falsey_result(value: Any, key: str) -> bool:
    return isinstance(value, dict) and value.get(key) is False


def _result_matches_status(value: Any, statuses: set[str]) -> bool:
    return isinstance(value, dict) and str(value.get("status") or "").lower() in statuses


def _result_reason(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("disabled_reason", "reason", "error", "detail", "message", "description"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


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


def _parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _datetime_from_payload(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(end: datetime | None, start: datetime | None) -> float | None:
    if not end or not start:
        return None
    return round(max(0.0, (end - start).total_seconds()) * 1000.0, 3)


def _span_end(spans: list[TelemetrySpan], name: str) -> datetime | None:
    for span in spans:
        if span.name == name and span.ended_at:
            return span.ended_at
    return None


def _float_from_payload(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _normalize_plate(value: str) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _payload_values(value: Any, keys: tuple[str, ...]) -> list[str]:
    normalized_keys = {_payload_key(key) for key in keys}
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if _payload_key(str(key)) in normalized_keys:
                found.extend(_scalar_payload_values(item))
            found.extend(_payload_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.extend(_payload_values(item, keys))
    return _dedupe_strings(found)


def _scalar_payload_values(value: Any) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str | int | float):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, list):
        return [item for value_item in value for item in _scalar_payload_values(value_item)]
    return []


def _payload_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


async def _row_count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def _time_filters(timestamp_column: Any, from_at: datetime | None, to_at: datetime | None) -> list[Any]:
    filters: list[Any] = []
    if from_at:
        filters.append(timestamp_column >= from_at)
    if to_at:
        filters.append(timestamp_column <= to_at)
    return filters


async def _filtered_row_count(session: AsyncSession, model: type, filters: list[Any]) -> int:
    query = select(func.count()).select_from(model)
    if filters:
        query = query.where(*filters)
    return int(await session.scalar(query) or 0)


async def _group_counts(session: AsyncSession, column: Any, filters: list[Any]) -> dict[str, int]:
    query = select(column, func.count()).group_by(column)
    if filters:
        query = query.where(*filters)
    rows = (await session.execute(query)).all()
    return _count_rows_to_map(rows)


def _count_rows_to_map(rows: list[tuple[Any, int]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, count in rows:
        counts[str(key or "unknown")] = int(count or 0)
    return counts


def _telemetry_storage_payload(
    *,
    database_size_bytes: int,
    log_file_size_bytes: int,
    artifact_size_bytes: int,
    file_count: int,
) -> dict[str, Any]:
    total_size_bytes = database_size_bytes + log_file_size_bytes + artifact_size_bytes
    return {
        "total_size_bytes": total_size_bytes,
        "database_size_bytes": database_size_bytes,
        "log_file_size_bytes": log_file_size_bytes,
        "artifact_size_bytes": artifact_size_bytes,
        "file_count": file_count,
        "updated_at": datetime.now().isoformat(),
    }


async def _telemetry_database_size(session: AsyncSession) -> int:
    total = 0
    for table_name in ("telemetry_traces", "telemetry_spans", "audit_logs"):
        try:
            total += int(
                await session.scalar(
                    text("SELECT COALESCE(pg_total_relation_size(to_regclass(:table_name)), 0)"),
                    {"table_name": table_name},
                )
                or 0
            )
        except Exception:
            await session.rollback()
            return 0
    return total


def _log_directory_size() -> tuple[int, int]:
    workspace_logs = settings.workspace_dir / "logs"
    if workspace_logs.exists():
        return _directory_size([workspace_logs])
    return _directory_size([settings.log_dir])


def _log_directory_roots() -> list[Path]:
    workspace_logs = settings.workspace_dir / "logs"
    if workspace_logs.exists():
        return [workspace_logs]
    return [settings.log_dir]


def _clear_log_files() -> dict[str, int]:
    total_size = 0
    file_count = 0
    visited: set[Path] = set()
    for root in _log_directory_roots():
        try:
            resolved_root = root.resolve()
        except OSError:
            continue
        if resolved_root in visited or not resolved_root.exists():
            continue
        visited.add(resolved_root)
        for path in resolved_root.rglob("*"):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                total_size += path.stat().st_size
                path.write_text("")
                file_count += 1
            except OSError:
                continue
    return {"files": file_count, "bytes": total_size}


def _directory_size(paths: list[Path]) -> tuple[int, int]:
    total_size = 0
    file_count = 0
    visited: set[Path] = set()
    for root in paths:
        try:
            resolved_root = root.resolve()
        except OSError:
            continue
        if resolved_root in visited or not resolved_root.exists():
            continue
        visited.add(resolved_root)
        for path in resolved_root.rglob("*"):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                total_size += path.stat().st_size
                file_count += 1
            except OSError:
                continue
    return total_size, file_count


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
