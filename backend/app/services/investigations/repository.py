from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import String, and_, cast, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccessDevice,
    AccessDeviceProviderBinding,
    AccessEvent,
    AuditLog,
    AutomationRule,
    AutomationRun,
    GateCommandRecord,
    MovementSagaRecord,
    Schedule,
    TelemetrySpan,
    TelemetryTrace,
)
from app.services.investigations.contracts import ActivityFilters, UnifiedCursor
from app.services.investigations.presenter import ROUTINE_AUDIT_ACTIONS, ROUTINE_TRACE_CATEGORIES


@dataclass
class TraceEnrichment:
    automation: tuple[AutomationRun, AutomationRule | None] | None = None
    audits: list[AuditLog] = field(default_factory=list)
    gate_commands: list[GateCommandRecord] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateBatch:
    traces: list[TelemetryTrace]
    audits: list[AuditLog]
    traces_exhausted: bool
    audits_exhausted: bool


@dataclass
class TraceDetailBundle:
    trace: TelemetryTrace
    spans: list[TelemetrySpan] = field(default_factory=list)
    automation: tuple[AutomationRun, AutomationRule | None] | None = None
    audits: list[AuditLog] = field(default_factory=list)
    access_event: AccessEvent | None = None
    movement_saga: MovementSagaRecord | None = None
    gate_commands: list[GateCommandRecord] = field(default_factory=list)
    current_schedule: Schedule | None = None


async def fetch_candidate_batch(
    session: AsyncSession,
    filters: ActivityFilters,
    *,
    cursor: UnifiedCursor | None,
    batch_size: int,
) -> CandidateBatch:
    trace_query = _trace_query(filters, cursor).limit(batch_size + 1)
    audit_query = _audit_query(filters, cursor).limit(batch_size + 1)
    trace_rows = list((await session.scalars(trace_query)).all())
    audit_rows = list((await session.scalars(audit_query)).all())
    return CandidateBatch(
        traces=trace_rows[:batch_size],
        audits=audit_rows[:batch_size],
        traces_exhausted=len(trace_rows) <= batch_size,
        audits_exhausted=len(audit_rows) <= batch_size,
    )


async def enrich_traces(
    session: AsyncSession,
    traces: Sequence[TelemetryTrace],
) -> dict[str, TraceEnrichment]:
    trace_ids = [trace.trace_id for trace in traces]
    result = {trace_id: TraceEnrichment() for trace_id in trace_ids}
    if not trace_ids:
        return result

    automation_rows = (
        await session.execute(
            select(AutomationRun, AutomationRule)
            .outerjoin(AutomationRule, AutomationRule.id == AutomationRun.rule_id)
            .where(AutomationRun.trace_id.in_(trace_ids))
            .order_by(AutomationRun.started_at.desc())
        )
    ).all()
    for run, rule in automation_rows:
        if run.trace_id and result[run.trace_id].automation is None:
            result[run.trace_id].automation = (run, rule)

    linked_audits = list(
        (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.trace_id.in_(trace_ids))
                .order_by(AuditLog.timestamp, AuditLog.id)
            )
        ).all()
    )
    for audit in linked_audits:
        if audit.trace_id in result:
            result[audit.trace_id].audits.append(audit)

    access_event_trace_ids: dict[uuid.UUID, str] = {
        trace.access_event_id: trace.trace_id for trace in traces if trace.access_event_id
    }
    if access_event_trace_ids:
        commands = list(
            (
                await session.scalars(
                    select(GateCommandRecord)
                    .where(GateCommandRecord.access_event_id.in_(list(access_event_trace_ids)))
                    .order_by(GateCommandRecord.created_at, GateCommandRecord.id)
                )
            ).all()
        )
        for command in commands:
            trace_id = access_event_trace_ids.get(command.access_event_id)
            if trace_id:
                result[trace_id].gate_commands.append(command)
    return result


async def load_trace_detail(session: AsyncSession, trace_id: str) -> TraceDetailBundle | None:
    trace = await session.get(TelemetryTrace, trace_id)
    if not trace:
        return None
    spans = list(
        (
            await session.scalars(
                select(TelemetrySpan)
                .where(TelemetrySpan.trace_id == trace_id)
                .order_by(TelemetrySpan.step_order, TelemetrySpan.started_at, TelemetrySpan.id)
            )
        ).all()
    )
    enrichment = (await enrich_traces(session, [trace]))[trace_id]
    access_event = await session.get(AccessEvent, trace.access_event_id) if trace.access_event_id else None
    movement_saga = None
    if access_event:
        movement_saga = await session.scalar(
            select(MovementSagaRecord)
            .where(MovementSagaRecord.access_event_id == access_event.id)
            .order_by(MovementSagaRecord.created_at.desc())
            .limit(1)
        )
    current_schedule = await _current_schedule_for_event(session, access_event)
    return TraceDetailBundle(
        trace=trace,
        spans=spans,
        automation=enrichment.automation,
        audits=enrichment.audits,
        access_event=access_event,
        movement_saga=movement_saga,
        gate_commands=enrichment.gate_commands,
        current_schedule=current_schedule,
    )


async def load_audit_or_linked_trace(
    session: AsyncSession,
    audit_id: str,
) -> tuple[AuditLog | None, TelemetryTrace | None]:
    try:
        parsed = uuid.UUID(audit_id)
    except ValueError:
        return None, None
    audit = await session.get(AuditLog, parsed)
    if not audit:
        return None, None
    trace = await session.get(TelemetryTrace, audit.trace_id) if audit.trace_id else None
    return audit, trace


async def filter_catalog(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    devices = list((await session.scalars(select(AccessDevice).order_by(AccessDevice.name))).all())
    automations = list((await session.scalars(select(AutomationRule).order_by(AutomationRule.name))).all())
    schedules = list((await session.scalars(select(Schedule).order_by(Schedule.name))).all())
    provider_names = list(
        (
            await session.scalars(
                select(AccessDeviceProviderBinding.provider)
                .distinct()
                .order_by(AccessDeviceProviderBinding.provider)
            )
        ).all()
    )
    trace_sources = list(
        (
            await session.scalars(
                select(TelemetryTrace.source)
                .where(TelemetryTrace.source.is_not(None))
                .distinct()
                .order_by(TelemetryTrace.source)
                .limit(100)
            )
        ).all()
    )
    trace_categories = list(
        (
            await session.scalars(select(TelemetryTrace.category).distinct().order_by(TelemetryTrace.category))
        ).all()
    )
    audit_categories = list(
        (await session.scalars(select(AuditLog.category).distinct().order_by(AuditLog.category))).all()
    )
    trace_levels = list(
        (await session.scalars(select(TelemetryTrace.level).distinct().order_by(TelemetryTrace.level))).all()
    )
    audit_levels = list(
        (await session.scalars(select(AuditLog.level).distinct().order_by(AuditLog.level))).all()
    )
    actors = list(
        (
            await session.scalars(
                select(AuditLog.actor)
                .where(AuditLog.actor != "")
                .distinct()
                .order_by(AuditLog.actor)
                .limit(100)
            )
        ).all()
    )
    triggers = list(
        (
            await session.scalars(
                select(AutomationRun.trigger_key).distinct().order_by(AutomationRun.trigger_key).limit(100)
            )
        ).all()
    )
    return {
        "devices": [
            {
                "id": str(row.id),
                "value": row.key,
                "label": row.name,
                "kind": row.kind,
                "enabled": row.enabled,
            }
            for row in devices
        ],
        "automations": [
            {"id": str(row.id), "value": str(row.id), "label": row.name, "active": row.is_active}
            for row in automations
        ],
        "schedules": [
            {"id": str(row.id), "value": str(row.id), "label": row.name}
            for row in schedules
        ],
        "integrations": _value_options({*provider_names, *trace_sources}),
        "categories": _value_options({*trace_categories, *audit_categories}),
        "severities": _value_options({*trace_levels, *audit_levels}),
        "actors": _value_options(set(actors)),
        "triggers": _value_options(set(triggers)),
    }


def _trace_query(filters: ActivityFilters, cursor: UnifiedCursor | None):
    query = select(TelemetryTrace).order_by(TelemetryTrace.started_at.desc(), TelemetryTrace.trace_id.desc())
    if filters.from_at:
        query = query.where(TelemetryTrace.started_at >= filters.from_at)
    if filters.to_at:
        query = query.where(TelemetryTrace.started_at < filters.to_at)
    if filters.category:
        query = query.where(TelemetryTrace.category == filters.category)
    if filters.severity:
        query = query.where(TelemetryTrace.level == filters.severity)
    if filters.actor:
        query = query.where(
            or_(
                TelemetryTrace.actor.ilike(_needle(filters.actor)),
                _linked_actor_match(filters.actor),
                _automation_actor_match(filters.actor),
            )
        )
    if filters.trace:
        query = query.where(TelemetryTrace.trace_id.ilike(_needle(filters.trace)))
    if filters.device:
        query = query.where(
            or_(
                _trace_text_match(filters.device, include_context=True),
                _linked_audit_match(filters.device),
                _linked_gate_command_match(filters.device),
            )
        )
    if filters.schedule:
        query = query.where(
            or_(
                TelemetryTrace.name.ilike(_needle(filters.schedule)),
                TelemetryTrace.summary.ilike(_needle(filters.schedule)),
                cast(TelemetryTrace.context, String).ilike(_needle(filters.schedule)),
            )
        )
    if filters.integration:
        query = query.where(
            or_(
                TelemetryTrace.source.ilike(_needle(filters.integration)),
                TelemetryTrace.name.ilike(_needle(filters.integration)),
                cast(TelemetryTrace.context, String).ilike(_needle(filters.integration)),
            )
        )
    if filters.automation:
        query = query.where(_automation_trace_match(filters.automation))
    if filters.trigger:
        query = query.where(
            or_(
                _automation_trigger_match(filters.trigger),
                cast(TelemetryTrace.context, String).ilike(_needle(filters.trigger)),
            )
        )
    if filters.q:
        query = query.where(_trace_text_match(filters.q, include_context=True))
    if not filters.include_routine:
        query = query.where(TelemetryTrace.category.not_in(ROUTINE_TRACE_CATEGORIES))
    if cursor:
        query = query.where(_trace_after_cursor(cursor))
    return query


def _audit_query(filters: ActivityFilters, cursor: UnifiedCursor | None):
    linked_trace = exists(
        select(TelemetryTrace.trace_id).where(TelemetryTrace.trace_id == AuditLog.trace_id)
    )
    query = (
        select(AuditLog)
        .where(or_(AuditLog.trace_id.is_(None), ~linked_trace))
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    )
    if filters.from_at:
        query = query.where(AuditLog.timestamp >= filters.from_at)
    if filters.to_at:
        query = query.where(AuditLog.timestamp < filters.to_at)
    if filters.category:
        query = query.where(AuditLog.category == filters.category)
    if filters.severity:
        query = query.where(AuditLog.level == filters.severity)
    if filters.actor:
        query = query.where(
            or_(
                AuditLog.actor.ilike(_needle(filters.actor)),
                cast(AuditLog.actor_user_id, String).ilike(_needle(filters.actor)),
            )
        )
    if filters.trace:
        query = query.where(AuditLog.trace_id.ilike(_needle(filters.trace)))
    if filters.device:
        query = query.where(_audit_text_match(filters.device))
    if filters.automation:
        query = query.where(
            or_(
                AuditLog.target_id.ilike(_needle(filters.automation)),
                AuditLog.target_label.ilike(_needle(filters.automation)),
                cast(AuditLog.metadata_, String).ilike(_needle(filters.automation)),
            )
        )
    if filters.schedule:
        query = query.where(_audit_text_match(filters.schedule))
    if filters.integration:
        query = query.where(
            or_(
                AuditLog.action.ilike(_needle(filters.integration)),
                AuditLog.target_label.ilike(_needle(filters.integration)),
                cast(AuditLog.metadata_, String).ilike(_needle(filters.integration)),
            )
        )
    if filters.trigger:
        query = query.where(
            or_(
                AuditLog.action.ilike(_needle(filters.trigger)),
                cast(AuditLog.metadata_, String).ilike(_needle(filters.trigger)),
            )
        )
    if filters.q:
        query = query.where(_audit_text_match(filters.q))
    if not filters.include_routine:
        query = query.where(
            AuditLog.category.not_in(ROUTINE_TRACE_CATEGORIES),
            AuditLog.action.not_in(ROUTINE_AUDIT_ACTIONS),
        )
    if cursor:
        query = query.where(_audit_after_cursor(cursor))
    return query


def _trace_after_cursor(cursor: UnifiedCursor):
    if cursor.kind == "trace":
        return or_(
            TelemetryTrace.started_at < cursor.occurred_at,
            and_(
                TelemetryTrace.started_at == cursor.occurred_at,
                TelemetryTrace.trace_id < cursor.row_id,
            ),
        )
    return TelemetryTrace.started_at < cursor.occurred_at


def _audit_after_cursor(cursor: UnifiedCursor):
    if cursor.kind == "trace":
        return AuditLog.timestamp <= cursor.occurred_at
    return or_(
        AuditLog.timestamp < cursor.occurred_at,
        and_(
            AuditLog.timestamp == cursor.occurred_at,
            cast(AuditLog.id, String) < cursor.row_id,
        ),
    )


def _trace_text_match(value: str, *, include_context: bool):
    clauses = [
        TelemetryTrace.name.ilike(_needle(value)),
        TelemetryTrace.summary.ilike(_needle(value)),
        TelemetryTrace.actor.ilike(_needle(value)),
        TelemetryTrace.source.ilike(_needle(value)),
        TelemetryTrace.registration_number.ilike(_needle(value)),
    ]
    if include_context:
        clauses.append(cast(TelemetryTrace.context, String).ilike(_needle(value)))
    return or_(*clauses)


def _audit_text_match(value: str):
    needle = _needle(value)
    return or_(
        AuditLog.action.ilike(needle),
        AuditLog.actor.ilike(needle),
        AuditLog.target_entity.ilike(needle),
        AuditLog.target_id.ilike(needle),
        AuditLog.target_label.ilike(needle),
        cast(AuditLog.diff, String).ilike(needle),
        cast(AuditLog.metadata_, String).ilike(needle),
    )


def _automation_trace_match(value: str):
    needle = _needle(value)
    return exists(
        select(AutomationRun.id)
        .outerjoin(AutomationRule, AutomationRule.id == AutomationRun.rule_id)
        .where(
            AutomationRun.trace_id == TelemetryTrace.trace_id,
            or_(
                cast(AutomationRun.rule_id, String).ilike(needle),
                AutomationRule.name.ilike(needle),
            ),
        )
    )


def _automation_trigger_match(value: str):
    return exists(
        select(AutomationRun.id).where(
            AutomationRun.trace_id == TelemetryTrace.trace_id,
            AutomationRun.trigger_key.ilike(_needle(value)),
        )
    )


def _automation_actor_match(value: str):
    return exists(
        select(AutomationRun.id).where(
            AutomationRun.trace_id == TelemetryTrace.trace_id,
            AutomationRun.actor.ilike(_needle(value)),
        )
    )


def _linked_actor_match(value: str):
    needle = _needle(value)
    return exists(
        select(AuditLog.id).where(
            AuditLog.trace_id == TelemetryTrace.trace_id,
            or_(
                AuditLog.actor.ilike(needle),
                cast(AuditLog.actor_user_id, String).ilike(needle),
            ),
        )
    )


def _linked_audit_match(value: str):
    needle = _needle(value)
    return exists(
        select(AuditLog.id).where(
            AuditLog.trace_id == TelemetryTrace.trace_id,
            or_(
                AuditLog.target_id.ilike(needle),
                AuditLog.target_label.ilike(needle),
                cast(AuditLog.metadata_, String).ilike(needle),
            ),
        )
    )


def _linked_gate_command_match(value: str):
    needle = _needle(value)
    return exists(
        select(GateCommandRecord.id).where(
            GateCommandRecord.access_event_id == TelemetryTrace.access_event_id,
            TelemetryTrace.access_event_id.is_not(None),
            or_(
                GateCommandRecord.gate_key.ilike(needle),
                GateCommandRecord.controller.ilike(needle),
                GateCommandRecord.reason.ilike(needle),
                cast(GateCommandRecord.command_metadata, String).ilike(needle),
            ),
        )
    )


async def _current_schedule_for_event(
    session: AsyncSession,
    event: AccessEvent | None,
) -> Schedule | None:
    if not event or not isinstance(event.raw_payload, dict):
        return None
    schedule = event.raw_payload.get("schedule")
    if not isinstance(schedule, dict):
        return None
    schedule_id = schedule.get("schedule_id") or schedule.get("id")
    try:
        parsed = uuid.UUID(str(schedule_id))
    except (TypeError, ValueError):
        return None
    return await session.get(Schedule, parsed)


def _needle(value: str) -> str:
    return f"%{value.strip()}%"


def _value_options(values: set[Any]) -> list[dict[str, Any]]:
    clean = sorted({str(value).strip() for value in values if value is not None and str(value).strip()})
    return [{"value": value, "label": value.replace("_", " ").title()} for value in clean]
