from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from app.models import (
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
from app.services.investigations.outcomes import EpisodeAssessment, assess_episode
from app.services.telemetry import sanitize_payload


ROUTINE_TRACE_CATEGORIES = {"dependency_updates"}
ROUTINE_AUDIT_ACTIONS = {
    "access_event.reconciliation_checked",
    "dependency_updates.package.check",
}


def trace_payload(trace: TelemetryTrace) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "trace_id": trace.trace_id,
            "name": trace.name,
            "category": trace.category,
            "status": trace.status,
            "level": trace.level,
            "started_at": _iso(trace.started_at),
            "ended_at": _iso(trace.ended_at),
            "duration_ms": trace.duration_ms,
            "actor": trace.actor,
            "source": trace.source,
            "registration_number": trace.registration_number,
            "access_event_id": str(trace.access_event_id) if trace.access_event_id else None,
            "summary": trace.summary,
            "context": trace.context or {},
            "error": trace.error,
        }
    )


def audit_payload(row: AuditLog) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "id": str(row.id),
            "timestamp": _iso(row.timestamp),
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
    )


def span_payload(span: TelemetrySpan) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "id": str(span.id),
            "span_id": span.span_id,
            "trace_id": span.trace_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "category": span.category,
            "step_order": span.step_order,
            "started_at": _iso(span.started_at),
            "ended_at": _iso(span.ended_at),
            "duration_ms": span.duration_ms,
            "status": span.status,
            "attributes": span.attributes or {},
            "input_payload": span.input_payload or {},
            "output_payload": span.output_payload or {},
            "error": span.error,
        }
    )


def automation_payload(run: AutomationRun, rule: AutomationRule | None) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "run_id": str(run.id),
            "rule_id": str(run.rule_id) if run.rule_id else None,
            "name": rule.name if rule else "Deleted automation",
            "description": rule.description if rule else None,
            "status": run.status,
            "trigger": run.trigger_key,
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
            "actor": run.actor,
            "source": run.source,
            "trigger_payload": run.trigger_payload or {},
            "context": run.context or {},
            "condition_results": run.condition_results or [],
            "action_results": run.action_results or [],
            "error": run.error,
            "current_configuration": (
                {
                    "is_historical_snapshot": False,
                    "is_active": rule.is_active,
                    "triggers": rule.triggers or [],
                    "conditions": rule.conditions or [],
                    "actions": rule.actions or [],
                    "updated_at": _iso(rule.updated_at),
                }
                if rule
                else None
            ),
        }
    )


def gate_command_payload(row: GateCommandRecord) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "id": str(row.id),
            "movement_saga_id": str(row.movement_saga_id) if row.movement_saga_id else None,
            "access_event_id": str(row.access_event_id) if row.access_event_id else None,
            "state": _enum(row.state),
            "action": row.action,
            "source": row.source,
            "gate_key": row.gate_key,
            "controller": row.controller,
            "reason": row.reason,
            "actor": row.actor,
            "registration_number": row.registration_number,
            "bypass_schedule": row.bypass_schedule,
            "started_at": _iso(row.started_at),
            "completed_at": _iso(row.completed_at),
            "accepted": row.accepted,
            "gate_state": row.gate_state,
            "detail": row.detail,
            "mechanically_confirmed": row.mechanically_confirmed,
            "requires_reconciliation": row.requires_reconciliation,
            "exception_class": row.exception_class,
            "metadata": row.command_metadata or {},
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }
    )


def access_event_payload(row: AccessEvent) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "id": str(row.id),
            "vehicle_id": str(row.vehicle_id) if row.vehicle_id else None,
            "person_id": str(row.person_id) if row.person_id else None,
            "registration_number": row.registration_number,
            "direction": _enum(row.direction),
            "decision": _enum(row.decision),
            "confidence": row.confidence,
            "source": row.source,
            "occurred_at": _iso(row.occurred_at),
            "timing_classification": _enum(row.timing_classification),
            "raw_payload": row.raw_payload or {},
            "created_at": _iso(row.created_at),
        }
    )


def movement_saga_payload(row: MovementSagaRecord) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "id": str(row.id),
            "state": _enum(row.state),
            "source": row.source,
            "access_event_id": str(row.access_event_id) if row.access_event_id else None,
            "registration_number": row.registration_number,
            "direction": _enum(row.direction),
            "decision": _enum(row.decision),
            "occurred_at": _iso(row.occurred_at),
            "gate_command_required": row.gate_command_required,
            "presence_committed": row.presence_committed,
            "reconciliation_required": row.reconciliation_required,
            "failure_detail": row.failure_detail,
            "intent_payload": row.intent_payload or {},
            "decision_payload": row.decision_payload or {},
            "state_history": row.state_history or [],
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }
    )


def schedule_payload(row: Schedule) -> dict[str, Any]:
    return _sanitized_dict(
        {
            "id": str(row.id),
            "name": row.name,
            "description": row.description,
            "time_blocks": row.time_blocks or {},
            "updated_at": _iso(row.updated_at),
            "is_historical_snapshot": False,
        }
    )


def build_trace_episode(
    trace: TelemetryTrace,
    *,
    automation: tuple[AutomationRun, AutomationRule | None] | None = None,
    audits: Sequence[AuditLog] = (),
    gate_commands: Sequence[GateCommandRecord] = (),
) -> dict[str, Any]:
    trace_data = trace_payload(trace)
    audit_data = [audit_payload(row) for row in audits]
    automation_data = automation_payload(*automation) if automation else None
    command_data = [gate_command_payload(row) for row in gate_commands]
    assessment = assess_episode(
        trace=trace_data,
        audits=audit_data,
        automation=automation_data,
        gate_commands=command_data,
    )
    compact_automation = _compact_automation(automation_data)
    title = str(compact_automation.get("name")) if compact_automation else trace.name
    summary = _episode_summary(assessment, trace.summary, title)
    return {
        "episode_id": f"trace:{trace.trace_id}",
        "kind": "trace",
        "occurred_at": _iso(trace.started_at),
        "ended_at": _iso(trace.ended_at),
        "duration_ms": trace.duration_ms,
        "title": title,
        "summary": summary,
        "outcome": assessment.outcome,
        "dispatch_state": assessment.dispatch_state,
        "reason_code": assessment.reason_code,
        "severity": trace.level,
        "category": trace.category,
        "actor": trace.actor or (automation[0].actor if automation else None),
        "source": trace.source or (automation[0].source if automation else None),
        "trace_id": trace.trace_id,
        "audit_id": None,
        "correlation": {"confidence": "exact", "basis": "trace_id"},
        "automation": compact_automation,
        "entities": _episode_entities(trace_data, audit_data, automation_data, command_data),
        "evidence_count": 1 + len(audits) + len(gate_commands) + (1 if automation else 0),
        "routine": is_routine_trace(trace),
    }


def build_audit_episode(row: AuditLog) -> dict[str, Any]:
    data = audit_payload(row)
    assessment = assess_episode(audits=[data])
    title = row.target_label or row.target_entity or _humanize(row.action)
    if assessment.reason_code == "state_observed":
        metadata = _mapping(data.get("metadata"))
        diff = _mapping(data.get("diff"))
        new_state = _mapping(diff.get("new"))
        state = str(metadata.get("raw_state") or new_state.get("state") or "unknown")
        title = f"State changed to {_humanize(state)}"
    stored_summary = assessment.reason if assessment.reason_code == "state_observed" else None
    return {
        "episode_id": f"audit:{row.id}",
        "kind": "audit",
        "occurred_at": _iso(row.timestamp),
        "ended_at": _iso(row.timestamp),
        "duration_ms": None,
        "title": title,
        "summary": _episode_summary(assessment, stored_summary, title),
        "outcome": assessment.outcome,
        "dispatch_state": assessment.dispatch_state,
        "reason_code": assessment.reason_code,
        "severity": row.level,
        "category": row.category,
        "actor": row.actor,
        "source": _audit_source(data),
        "trace_id": row.trace_id,
        "audit_id": str(row.id),
        "correlation": {"confidence": "none", "basis": "standalone_audit"},
        "automation": None,
        "entities": _episode_entities(None, [data], None, []),
        "evidence_count": 1,
        "routine": is_routine_audit(row),
    }


def build_trace_detail(
    trace: TelemetryTrace,
    *,
    spans: Sequence[TelemetrySpan],
    automation: tuple[AutomationRun, AutomationRule | None] | None,
    audits: Sequence[AuditLog],
    access_event: AccessEvent | None,
    movement_saga: MovementSagaRecord | None,
    gate_commands: Sequence[GateCommandRecord],
    current_schedule: Schedule | None,
    site_timezone: str,
) -> dict[str, Any]:
    episode = build_trace_episode(
        trace,
        automation=automation,
        audits=audits,
        gate_commands=gate_commands,
    )
    raw = {
        "trace": trace_payload(trace),
        "spans": [span_payload(row) for row in spans],
        "audits": [audit_payload(row) for row in audits],
        "automation": automation_payload(*automation) if automation else None,
        "access_event": access_event_payload(access_event) if access_event else None,
        "movement_saga": movement_saga_payload(movement_saga) if movement_saga else None,
        "gate_commands": [gate_command_payload(row) for row in gate_commands],
    }
    timeline = _trace_timeline(raw, episode)
    return {
        "episode": {**episode, "evidence_count": len(timeline)},
        "timeline": timeline,
        "citations": _citations(timeline),
        "configuration_context": _configuration_context(raw, current_schedule),
        "raw": _sanitized_dict(raw),
        "site_timezone": site_timezone,
    }


def build_audit_detail(row: AuditLog, *, site_timezone: str) -> dict[str, Any]:
    episode = build_audit_episode(row)
    data = audit_payload(row)
    evidence = {
        "id": f"audit:{row.id}",
        "timestamp": _iso(row.timestamp),
        "timestamp_precision": "exact",
        "type": "audit",
        "title": _humanize(row.action),
        "description": episode["summary"],
        "outcome": episode["outcome"],
        "reason_code": episode["reason_code"],
        "source": _audit_source(data),
        "raw": data,
    }
    return {
        "episode": episode,
        "timeline": [evidence],
        "citations": _citations([evidence]),
        "configuration_context": [],
        "raw": {"audit": data},
        "site_timezone": site_timezone,
    }


def is_routine_trace(trace: TelemetryTrace) -> bool:
    return trace.category in ROUTINE_TRACE_CATEGORIES


def is_routine_audit(row: AuditLog) -> bool:
    return row.category in ROUTINE_TRACE_CATEGORIES or row.action in ROUTINE_AUDIT_ACTIONS


def _trace_timeline(raw: Mapping[str, Any], episode: Mapping[str, Any]) -> list[dict[str, Any]]:
    trace = _mapping(raw.get("trace"))
    trace_id = str(trace.get("trace_id") or "")
    timeline: list[dict[str, Any]] = [
        _evidence(
            evidence_id=f"trace:{trace_id}",
            timestamp=trace.get("started_at"),
            kind="trigger",
            title=str(trace.get("name") or "Activity started"),
            description=str(trace.get("summary") or "IACS began processing this activity."),
            source=trace.get("source"),
            raw=trace,
        )
    ]
    for span in _list_of_mappings(raw.get("spans")):
        timeline.append(
            _evidence(
                evidence_id=f"span:{span.get('span_id')}",
                timestamp=span.get("started_at"),
                kind="step",
                title=str(span.get("name") or "Processing step"),
                description=_span_description(span),
                source=span.get("category"),
                raw=span,
            )
        )

    automation = _mapping(raw.get("automation"))
    if automation:
        run_id = str(automation.get("run_id") or "")
        timeline.append(
            _evidence(
                evidence_id=f"automation-run:{run_id}",
                timestamp=automation.get("started_at"),
                kind="automation",
                title=f"{automation.get('name') or 'Automation'} was evaluated",
                description=f"Trigger: {_humanize(str(automation.get('trigger') or 'unknown'))}.",
                source=automation.get("source"),
                raw={
                    "run_id": run_id,
                    "rule_id": automation.get("rule_id"),
                    "trigger": automation.get("trigger"),
                    "trigger_payload": automation.get("trigger_payload"),
                },
            )
        )
        for index, condition in enumerate(_list_of_mappings(automation.get("condition_results"))):
            passed = condition.get("passed") is True
            timeline.append(
                _evidence(
                    evidence_id=f"automation-run:{run_id}:condition:{index}",
                    timestamp=automation.get("started_at"),
                    timestamp_precision="run",
                    kind="condition",
                    title=f"{_humanize(str(condition.get('type') or 'Condition'))} {'passed' if passed else 'failed'}",
                    description=_condition_description(condition, passed),
                    source="automation_engine",
                    raw=condition,
                    outcome="succeeded" if passed else "blocked",
                    reason_code=None if passed else "condition_failed",
                )
            )
        for index, action in enumerate(_list_of_mappings(automation.get("action_results"))):
            timeline.append(
                _evidence(
                    evidence_id=f"automation-run:{run_id}:action:{index}",
                    timestamp=automation.get("finished_at") or automation.get("started_at"),
                    timestamp_precision="run",
                    kind="action",
                    title=f"{_humanize(str(action.get('type') or 'Action'))}: {_humanize(str(action.get('status') or 'unknown'))}",
                    description=_action_description(action),
                    source="automation_engine",
                    raw=action,
                )
            )

    access_event = _mapping(raw.get("access_event"))
    if access_event:
        timeline.append(
            _evidence(
                evidence_id=f"access-event:{access_event.get('id')}",
                timestamp=access_event.get("occurred_at"),
                kind="access_event",
                title=f"Access decision: {_humanize(str(access_event.get('decision') or 'unknown'))}",
                description=(
                    f"{access_event.get('registration_number') or 'Vehicle'} was recorded as "
                    f"{_humanize(str(access_event.get('direction') or 'unknown'))}."
                ),
                source=access_event.get("source"),
                raw=access_event,
            )
        )

    saga = _mapping(raw.get("movement_saga"))
    if saga:
        saga_id = str(saga.get("id") or "")
        for index, state in enumerate(_list_of_mappings(saga.get("state_history"))):
            timeline.append(
                _evidence(
                    evidence_id=f"movement-saga:{saga_id}:state:{index}",
                    timestamp=state.get("at") or state.get("timestamp") or saga.get("occurred_at"),
                    timestamp_precision="exact" if state.get("at") or state.get("timestamp") else "episode",
                    kind="decision",
                    title=f"Movement: {_humanize(str(state.get('state') or 'state changed'))}",
                    description=str(state.get("detail") or state.get("reason") or "The movement workflow advanced."),
                    source=saga.get("source"),
                    raw=state,
                )
            )

    for command in _list_of_mappings(raw.get("gate_commands")):
        command_id = str(command.get("id") or "")
        timeline.append(
            _evidence(
                evidence_id=f"gate-command:{command_id}",
                timestamp=command.get("started_at") or command.get("created_at"),
                kind="command",
                title=f"{_humanize(str(command.get('action') or 'Command'))} command",
                description=_command_description(command),
                source=command.get("controller") or command.get("source"),
                raw=command,
            )
        )

    for audit in _list_of_mappings(raw.get("audits")):
        timeline.append(
            _evidence(
                evidence_id=f"audit:{audit.get('id')}",
                timestamp=audit.get("timestamp"),
                kind="audit",
                title=_humanize(str(audit.get("action") or "Audit record")),
                description=str(audit.get("target_label") or audit.get("target_entity") or episode.get("summary")),
                source=_audit_source(audit),
                raw=audit,
            )
        )

    return sorted(timeline, key=lambda item: (_sort_timestamp(item.get("timestamp")), str(item.get("id"))))


def _configuration_context(raw: Mapping[str, Any], current_schedule: Schedule | None) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    event = _mapping(raw.get("access_event"))
    event_raw = _mapping(event.get("raw_payload"))
    recorded_schedule = _mapping(event_raw.get("schedule"))
    if recorded_schedule:
        contexts.append(
            {
                "type": "schedule_evaluation",
                "recorded_at_decision_time": True,
                "label": recorded_schedule.get("schedule_name") or "Recorded schedule evaluation",
                "value": _sanitized_dict(recorded_schedule),
            }
        )
    automation = _mapping(raw.get("automation"))
    recorded_rule = _mapping(_mapping(automation.get("context")).get("configuration_snapshot"))
    if recorded_rule:
        contexts.append(
            {
                "type": "automation",
                "recorded_at_decision_time": True,
                "label": f"Recorded configuration for {automation.get('name') or 'automation'}",
                "value": _sanitized_dict(recorded_rule),
            }
        )
    current_rule = _mapping(automation.get("current_configuration"))
    if current_rule:
        contexts.append(
            {
                "type": "automation",
                "recorded_at_decision_time": False,
                "label": f"Current configuration for {automation.get('name') or 'automation'}",
                "value": _sanitized_dict(current_rule),
                "warning": "This is the current configuration and may differ from the configuration at the time.",
            }
        )
    if current_schedule:
        contexts.append(
            {
                "type": "schedule",
                "recorded_at_decision_time": False,
                "label": f"Current configuration for {current_schedule.name}",
                "value": schedule_payload(current_schedule),
                "warning": "This is the current schedule and may differ from the schedule at the time.",
            }
        )
    return contexts


def _episode_summary(assessment: EpisodeAssessment, stored_summary: str | None, title: str) -> str:
    if assessment.outcome in {"blocked", "failed", "pending", "skipped", "unknown"}:
        return assessment.reason
    if stored_summary and stored_summary.strip():
        return stored_summary.strip()
    return f"{title} completed successfully."


def _compact_automation(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        "run_id": value.get("run_id"),
        "rule_id": value.get("rule_id"),
        "name": value.get("name"),
        "status": value.get("status"),
        "trigger": value.get("trigger"),
    }


def _episode_entities(
    trace: Mapping[str, Any] | None,
    audits: Sequence[Mapping[str, Any]],
    automation: Mapping[str, Any] | None,
    commands: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    if automation:
        entities.append(
            {
                "type": "automation",
                "id": automation.get("rule_id"),
                "label": automation.get("name"),
            }
        )
    if trace and trace.get("registration_number"):
        entities.append(
            {
                "type": "vehicle",
                "id": trace.get("registration_number"),
                "label": trace.get("registration_number"),
            }
        )
    for audit in audits:
        if audit.get("target_entity") or audit.get("target_label"):
            entities.append(
                {
                    "type": str(audit.get("target_entity") or "entity").lower(),
                    "id": audit.get("target_id"),
                    "label": audit.get("target_label") or audit.get("target_id"),
                }
            )
    for command in commands:
        entities.append(
            {
                "type": "device",
                "id": command.get("gate_key"),
                "label": command.get("gate_key"),
            }
        )
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for entity in entities:
        if not entity.get("label"):
            continue
        key = (str(entity.get("type")), str(entity.get("id") or entity.get("label")))
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result[:12]


def _evidence(
    *,
    evidence_id: str,
    timestamp: Any,
    kind: str,
    title: str,
    description: str,
    source: Any,
    raw: Mapping[str, Any],
    timestamp_precision: str = "exact",
    outcome: str | None = None,
    reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        "id": evidence_id,
        "timestamp": str(timestamp) if timestamp else None,
        "timestamp_precision": timestamp_precision,
        "type": kind,
        "title": title,
        "description": description,
        "outcome": outcome,
        "reason_code": reason_code,
        "source": str(source) if source else None,
        "raw": _sanitized_dict(raw),
    }


def _citations(timeline: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "label": item.get("title"),
            "timestamp": item.get("timestamp"),
        }
        for item in timeline
    ]


def _condition_description(condition: Mapping[str, Any], passed: bool) -> str:
    detail = _first_text(condition, ("reason", "detail", "message", "description"))
    if not detail:
        details = _mapping(condition.get("details"))
        detail = _first_text(details, ("reason", "detail", "message", "state"))
    return detail or ("The recorded condition passed." if passed else "The recorded condition did not pass.")


def _action_description(action: Mapping[str, Any]) -> str:
    return _first_text(action, ("detail", "reason", "error", "message")) or (
        f"Recorded action status: {action.get('status') or 'unknown'}."
    )


def _command_description(command: Mapping[str, Any]) -> str:
    if command.get("mechanically_confirmed") is True:
        return "The command was accepted and the resulting device state was confirmed."
    if command.get("accepted") is True:
        return str(command.get("detail") or "The command was accepted but the resulting state was not confirmed.")
    return str(command.get("detail") or "The command was not accepted.")


def _span_description(span: Mapping[str, Any]) -> str:
    if span.get("error"):
        return str(span["error"])
    status = _humanize(str(span.get("status") or "unknown"))
    duration = span.get("duration_ms")
    return f"Status: {status}." + (f" Duration: {duration} ms." if duration is not None else "")


def _audit_source(audit: Mapping[str, Any]) -> str | None:
    metadata = _mapping(audit.get("metadata"))
    source = metadata.get("source") or metadata.get("controller") or metadata.get("provider")
    return str(source) if source else None


def _first_text(value: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _humanize(value: str) -> str:
    return value.replace(".", " ").replace("_", " ").replace("-", " ").strip().capitalize()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _sanitized_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_payload(dict(value))
    return sanitized if isinstance(sanitized, dict) else {}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _enum(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _sort_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=UTC)
