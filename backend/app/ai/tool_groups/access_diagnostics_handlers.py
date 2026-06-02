"""Access diagnostics Alfred tool handlers."""
# ruff: noqa: F403, F405

from __future__ import annotations

from typing import Any

from app.ai.tool_groups._shared import *
from app.services.movement.sessions import GATE_OBSERVATION_PAYLOAD_KEY, datetime_from_payload as _datetime_from_agent_value

def _answer_fact(fact_id: str, label: str, value: Any, display_value: Any, kind: str, source: str, *, must_appear: bool = False, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"id": fact_id, "label": label, "value": value, "display_value": str(display_value), "kind": kind, "source": source, "must_appear": must_appear}
    if metadata:
        payload["metadata"] = metadata
    return payload

def _empty_count_artifact(*, domain: str, answer_type: str, subject_label: str, fact_id: str, fact_label: str, source: str, day: Any, canonical_text: str, display: dict[str, Any] | None = None) -> dict[str, Any]:
    primary_fact = _answer_fact(fact_id, fact_label, 0, "0", "count", source)
    return artifact_payload(
        domain=domain,
        answer_type=answer_type,
        subject_label=subject_label,
        primary_fact=primary_fact,
        time_scope={"day": day or "recent"},
        display=display,
        canonical_text=canonical_text,
    )

def _access_events_answer_artifacts(arguments: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    subject = _preferred_subject_label(arguments, records[0].get("person") if records else "the matched subject")
    if not records:
        return [
            _empty_count_artifact(
                domain="access_logs",
                answer_type="no_access_match",
                subject_label=subject,
                fact_id="access_event.match_count",
                fact_label="Matching access events",
                source="access_events",
                day=arguments.get("day"),
                canonical_text=f"I couldn't find any matching access events for {subject}.",
            )
        ]
    event = records[0]
    direction = str(event.get("direction") or "").strip()
    answer_type = "latest_departure" if direction == AccessDirection.EXIT.value else "latest_arrival"
    verb = "left" if direction == AccessDirection.EXIT.value else "arrived"
    display = str(event.get("occurred_at_display") or "").strip()
    compact_display = _compact_time_label(display)
    subject = _preferred_subject_label(arguments, event.get("person") or event.get("registration_number") or subject)
    return [
        artifact_payload(
            domain="access_logs",
            answer_type=answer_type,
            subject_label=subject,
            primary_fact=_answer_fact(
                "access_event.occurred_at",
                "Access event time",
                event.get("occurred_at"),
                compact_display or display,
                "timestamp",
                "access_events",
                must_appear=True,
            ),
            supporting_facts=[
                _answer_fact("access_event.direction", "Direction", direction, verb, "direction", "access_events")
            ],
            time_scope={"day": arguments.get("day") or "recent"},
            source_records=[
                {
                    "access_event_id": event.get("id"),
                    "direction": direction,
                    "decision": event.get("decision"),
                    "source": event.get("source"),
                    "occurred_at": event.get("occurred_at"),
                    "occurred_at_display": display,
                }
            ],
            display={"verb": verb, "voice": "natural_concise", "no_timezone_labels": True},
            canonical_text=f"{subject} {verb} at {compact_display or display}.",
        )
    ]

def _anomaly_answer_artifacts(arguments: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    search = str(arguments.get("search") or "matching alerts").strip()
    suspected_delivery = bool(arguments.get("suspected_delivery") or arguments.get("possible_delivery"))
    if not records:
        subject = search if search != "matching alerts" else "alerts"
        canonical_text = (
            f"I couldn't find any matching active or resolved alerts for {subject}."
            if subject != "alerts"
            else "I couldn't find any matching active or resolved alerts."
        )
        return [
            _empty_count_artifact(
                domain="alerts",
                answer_type="alert_activity_empty",
                subject_label=subject,
                fact_id="alerts.match_count",
                fact_label="Matching alerts",
                source="anomalies",
                day=arguments.get("day"),
                display={"voice": "natural_concise", "no_timezone_labels": True},
                canonical_text=canonical_text,
            )
        ]
    alert = records[0]
    when = str(alert.get("created_at_display") or alert.get("resolved_at_display") or "").strip()
    indicators = as_list(alert.get("delivery_indicators"))
    evidence = "; ".join(str(item) for item in indicators[:2] if item)
    message = str(alert.get("message") or alert.get("type") or "alert").strip()
    display = f"{_compact_time_label(when)}: {message}" if when else message
    if suspected_delivery and evidence:
        display = f"{display}. Evidence: {evidence}"
    return [
        artifact_payload(
            domain="alerts",
            answer_type="delivery_alert_match" if suspected_delivery else "alert_match",
            subject_label=search or "alerts",
            primary_fact=_answer_fact("alert.latest", "Latest matching alert", alert.get("id"), display, "alert", "anomalies", must_appear=True),
            source_records=[
                {
                    "alert_id": alert.get("id"),
                    "status": alert.get("status"),
                    "created_at": alert.get("created_at"),
                    "created_at_display": when,
                    "resolved_at": alert.get("resolved_at"),
                    "resolved_at_display": alert.get("resolved_at_display"),
                }
            ],
            display={"voice": "natural_concise", "no_timezone_labels": True},
            canonical_text=f"The latest matching alert was {display}.",
        )
    ]

def _alert_activity_answer_artifacts(
    arguments: dict[str, Any],
    raised: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    day = str(arguments.get("day") or "today")
    scope_label = "today" if day == "today" else day
    raised_count = len(raised)
    resolved_count = len(resolved)
    if not raised_count and not resolved_count:
        return [
            _empty_count_artifact(
                domain="alerts",
                answer_type="alert_activity_empty",
                subject_label="alerts",
                fact_id="alerts.activity_count",
                fact_label="Alert activity",
                source="anomalies",
                day=day,
                canonical_text=f"No alerts were raised or resolved {scope_label}.",
            )
        ]
    parts = [f"{raised_count} raised", f"{resolved_count} resolved"]
    latest_bits: list[str] = []
    if raised:
        latest = raised[0]
        latest_bits.append(
            f"latest raised at {_compact_time_label(latest.get('created_at_display'))}: "
            f"{latest.get('message') or latest.get('type') or 'alert'}"
        )
    if resolved:
        latest = resolved[0]
        latest_bits.append(
            f"latest resolved at {_compact_time_label(latest.get('resolved_at_display'))}: "
            f"{latest.get('message') or latest.get('type') or 'alert'}"
        )
    display = f"{scope_label}: {', '.join(parts)}"
    if latest_bits:
        display = f"{display}. {'; '.join(latest_bits)}"
    return [
        artifact_payload(
            domain="alerts",
            answer_type="alert_activity",
            subject_label="alerts",
            primary_fact=_answer_fact(
                "alerts.activity_summary",
                "Alert activity summary",
                {"raised": raised_count, "resolved": resolved_count},
                display,
                "alert_activity",
                "anomalies",
                must_appear=True,
            ),
            supporting_facts=[
                _answer_fact("alerts.raised_count", "Raised alerts", raised_count, raised_count, "count", "anomalies"),
                _answer_fact("alerts.resolved_count", "Resolved alerts", resolved_count, resolved_count, "count", "anomalies"),
            ],
            time_scope={"day": day, "label": scope_label},
            source_records=[
                *[
                    {"alert_id": item.get("id"), "activity": "raised", "created_at": item.get("created_at")}
                    for item in raised[:5]
                ],
                *[
                    {"alert_id": item.get("id"), "activity": "resolved", "resolved_at": item.get("resolved_at")}
                    for item in resolved[:5]
                ],
            ],
            display={"voice": "natural_concise", "no_timezone_labels": True, "alerts_only": True},
            canonical_text=display + ".",
        )
    ]

def _alert_status_from_argument(value: Any) -> str:
    status = _normalize(value or "open")
    if status in {"active", "unresolved"}:
        return "open"
    if status in {"resolved", "all", "open"}:
        return status
    return "open"

def _anomaly_agent_payload(anomaly: Anomaly, *, timezone_name: str) -> dict[str, Any]:
    event = getattr(anomaly, "event", None)
    snapshot = _alert_snapshot_for_agent(anomaly)
    visual_detection = _event_vehicle_visual_detection(event)
    status = "resolved" if getattr(anomaly, "resolved_at", None) else "open"
    record: dict[str, Any] = {
        "id": str(getattr(anomaly, "id", "")),
        "alert_ids": [str(getattr(anomaly, "id", ""))],
        "event_id": str(getattr(anomaly, "event_id", None) or getattr(event, "id", "") or "") or None,
        "type": _enum_text(getattr(anomaly, "anomaly_type", "")),
        "severity": _enum_text(getattr(anomaly, "severity", "")),
        "status": status,
        "message": str(getattr(anomaly, "message", "") or ""),
        "registration_number": _anomaly_registration_number(anomaly),
        **_agent_time_fields("created_at", anomaly.created_at, timezone_name),
        **_agent_time_fields("resolved_at", getattr(anomaly, "resolved_at", None), timezone_name),
        "resolution_note": getattr(anomaly, "resolution_note", None),
        "snapshot": snapshot,
        "event": _alert_event_payload(event, timezone_name=timezone_name, visual_detection=visual_detection),
    }
    delivery_indicators = _delivery_indicators_for_alert(anomaly, record, visual_detection)
    record["possible_delivery"] = bool(delivery_indicators)
    record["delivery_indicators"] = delivery_indicators
    return record

def _alert_event_payload(
    event: AccessEvent | None,
    *,
    timezone_name: str,
    visual_detection: dict[str, Any],
) -> dict[str, Any] | None:
    if not event:
        return None
    return {
        "id": str(getattr(event, "id", "")),
        "registration_number": getattr(event, "registration_number", None),
        "direction": _enum_text(getattr(event, "direction", "")),
        "decision": _enum_text(getattr(event, "decision", "")),
        "source": getattr(event, "source", None),
        **_agent_time_fields("occurred_at", event.occurred_at, timezone_name),
        "vehicle_visual_detection": visual_detection or None,
    }

def _alert_snapshot_for_agent(anomaly: Anomaly) -> dict[str, Any] | None:
    snapshot = None
    try:
        snapshot = alert_snapshot_metadata(anomaly)
    except Exception:
        snapshot = None
    if not snapshot and isinstance(getattr(anomaly, "context", None), dict):
        raw_snapshot = anomaly.context.get("snapshot")
        snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else None
    return _compact_alert_snapshot(snapshot)

def _compact_alert_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    value = {
        "url": snapshot.get("url") or snapshot.get("snapshot_url"),
        "captured_at": snapshot.get("captured_at") or snapshot.get("snapshot_captured_at"),
        "content_type": snapshot.get("content_type") or snapshot.get("snapshot_content_type"),
        "bytes": snapshot.get("bytes") or snapshot.get("snapshot_bytes"),
        "width": snapshot.get("width") or snapshot.get("snapshot_width"),
        "height": snapshot.get("height") or snapshot.get("snapshot_height"),
        "camera": snapshot.get("camera") or snapshot.get("snapshot_camera"),
    }
    return {key: item for key, item in value.items() if item not in (None, "")}

def _alert_snapshot_file(anomaly: Anomaly) -> tuple[Any, str, dict[str, Any]] | None:
    snapshot = None
    try:
        snapshot = alert_snapshot_metadata(anomaly)
    except Exception:
        snapshot = None
    if not isinstance(snapshot, dict):
        return None
    url = str(snapshot.get("url") or "")
    path = None
    if url.startswith("/api/v1/alerts/") and getattr(anomaly, "id", None):
        path = alert_snapshot_path(anomaly.id)
    elif getattr(anomaly, "event", None) and getattr(anomaly.event, "snapshot_path", None):
        try:
            path = get_snapshot_manager().resolve_path(anomaly.event.snapshot_path)
        except Exception:
            path = None
    if not path or not path.exists():
        return None
    content_type = str(
        snapshot.get("content_type")
        or snapshot.get("snapshot_content_type")
        or getattr(getattr(anomaly, "event", None), "snapshot_content_type", None)
        or "image/jpeg"
    )
    return path, content_type, snapshot

def _event_vehicle_visual_detection(event: AccessEvent | None) -> dict[str, Any]:
    payload = getattr(event, "raw_payload", None)
    if not isinstance(payload, dict):
        return {}
    visual = payload.get("vehicle_visual_detection")
    if not isinstance(visual, dict):
        return {}
    allowed = {
        "observed_vehicle_type",
        "observed_vehicle_color",
        "observed_vehicle_colour",
        "vehicle_type",
        "vehicle_color",
        "vehicle_colour",
        "detected_vehicle_type",
        "detected_vehicle_color",
        "detected_vehicle_colour",
        "vehicle_type_confidence",
        "vehicle_color_confidence",
        "source",
        "observed_at",
    }
    return {key: value for key, value in visual.items() if key in allowed and value not in (None, "")}

def _delivery_indicators_for_alert(
    anomaly: Anomaly,
    record: dict[str, Any],
    visual_detection: dict[str, Any],
) -> list[str]:
    blob = _alert_search_blob(anomaly, record)
    lowered = blob.lower()
    compacted = re.sub(r"[^a-z0-9]+", "", lowered)
    indicators: list[str] = []
    if "dove fuels" in lowered or "dove fuel" in lowered:
        indicators.append("Text evidence mentions Dove Fuels.")
    if "hello fresh" in lowered or "hellofresh" in compacted:
        indicators.append("Text evidence mentions HelloFresh.")
    if "oil delivery" in lowered or "heating oil" in lowered or "fuel delivery" in lowered:
        indicators.append("Text evidence mentions an oil/fuel delivery.")
    elif "delivery" in lowered:
        indicators.append("Text evidence mentions a delivery.")
    vehicle_type = _normalize(
        visual_detection.get("observed_vehicle_type")
        or visual_detection.get("vehicle_type")
        or visual_detection.get("detected_vehicle_type")
    )
    if vehicle_type in {"truck", "lorry", "tanker", "hgv", "van"}:
        indicators.append(f"Stored visual evidence reports vehicle type: {vehicle_type.title()}.")
    elif any(term in lowered for term in ("lorry", "truck", "tanker", "hgv")):
        indicators.append("Text evidence mentions a truck/lorry/tanker.")
    return indicators

def _anomaly_matches_search(
    record: dict[str, Any],
    anomaly: Anomaly,
    *,
    search: str,
    suspected_delivery: bool,
) -> bool:
    if suspected_delivery and record.get("delivery_indicators"):
        return True
    if not search:
        return True
    terms = [term for term in re.findall(r"[a-z0-9]+", search.lower()) if len(term) >= 2]
    if not terms:
        return True
    blob = _alert_search_blob(anomaly, record).lower()
    compact_blob = re.sub(r"[^a-z0-9]+", "", blob)
    compact_search = re.sub(r"[^a-z0-9]+", "", search.lower())
    if compact_search and compact_search in compact_blob:
        return True
    return any(term in blob or term in compact_blob for term in terms)

def _alert_search_blob(anomaly: Anomaly, record: dict[str, Any]) -> str:
    parts = [
        str(record.get("message") or ""),
        str(record.get("registration_number") or ""),
        str(record.get("resolution_note") or ""),
        _searchable_text(getattr(anomaly, "context", None)),
        _searchable_text(getattr(getattr(anomaly, "event", None), "raw_payload", None)),
    ]
    return " ".join(part for part in parts if part)

def _searchable_text(value: Any, *, depth: int = 0) -> str:
    if value is None or depth > 5:
        return ""
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            parts.append(str(key))
            parts.append(_searchable_text(item, depth=depth + 1))
        return " ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_searchable_text(item, depth=depth + 1) for item in value)
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""

def _anomaly_registration_number(anomaly: Anomaly) -> str:
    context = getattr(anomaly, "context", None)
    if isinstance(context, dict):
        value = context.get("registration_number")
        if isinstance(value, str) and value.strip():
            return value.strip()
    event = getattr(anomaly, "event", None)
    return str(getattr(event, "registration_number", "") or "")

def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")

def _agent_time_fields(name: str, value: Any, timezone_name: str, *, display: bool = True, raw_value: Any = None) -> dict[str, Any]:
    parsed = _datetime_from_agent_value(value)
    payload = {name: _agent_datetime_iso(parsed, timezone_name) if parsed else raw_value}
    if display:
        payload[f"{name}_display"] = _agent_datetime_display(parsed, timezone_name) if parsed else None
    return payload

def _absence_duration_answer_hint(
    *,
    subject: str,
    duration: str,
    primary_interval: dict[str, Any] | None,
    status: str,
    as_of: datetime | None,
    timezone_name: str,
    mode: str,
    total_human: str,
    interval_count: int,
) -> str:
    if not duration or duration == "0m":
        return ""
    latest = primary_interval or {}
    left_at = str(latest.get("exit_display") or "").strip()
    returned_at = str(latest.get("entry_display") or "").strip()
    latest_duration = str(latest.get("duration_human") or "").strip()
    if mode == "total" and interval_count > 1:
        latest_suffix = ""
        if latest_duration and left_at and returned_at:
            latest_suffix = f" The latest matched absence was {latest_duration}, from {left_at} to {returned_at}."
        return (
            f"Answer conversationally: {subject} was out for {total_human} in total across "
            f"{interval_count} matched absences.{latest_suffix} Keep it crisp, factual, and Alfred-warm."
        )
    if status == "still_away":
        as_of_display = _agent_datetime_display(as_of, timezone_name) if as_of else ""
        as_of_suffix = f". Still marked away as of {as_of_display}" if as_of_display else ". Still marked away"
        since_suffix = f" since {left_at}" if left_at else ""
        return (
            f"Answer conversationally: {subject} has been out for {duration}{since_suffix}{as_of_suffix}. "
            "Keep it crisp, factual, and Alfred-warm; avoid robotic audit-log phrasing."
        )
    if left_at and returned_at:
        return (
            f"Answer conversationally: {subject} was out for {duration}, from {left_at} to {returned_at}. "
            "Keep it crisp, factual, and Alfred-warm; avoid robotic audit-log phrasing."
        )
    return (
        f"Answer conversationally: {subject} was out for {duration}. "
        "Keep it crisp, factual, and Alfred-warm; avoid robotic audit-log phrasing."
    )

def _access_event_load_options() -> tuple[Any, ...]:
    return (
        selectinload(AccessEvent.vehicle).selectinload(Vehicle.owner).selectinload(Person.group),
        selectinload(AccessEvent.anomalies),
    )

async def _resolve_access_event_for_diagnostics(
    session,
    arguments: dict[str, Any],
    person_map: dict[str, dict[str, str]],
    timezone_name: str,
) -> AccessEvent | None:
    event_id = _uuid_from_value(arguments.get("access_event_id") or arguments.get("event_id"))
    query_options = _access_event_load_options()
    if event_id:
        return await session.scalar(
            select(AccessEvent)
            .options(*query_options)
            .where(AccessEvent.id == event_id)
        )

    start, end = _period_bounds(str(arguments.get("day") or "recent"), timezone_name)
    query = (
        select(AccessEvent)
        .options(*query_options)
        .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
        .order_by(AccessEvent.occurred_at.desc())
        .limit(250)
    )
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number:
        query = query.where(AccessEvent.registration_number == registration_number)
    person_id = _uuid_from_value(arguments.get("person_id"))
    if person_id:
        query = query.where(AccessEvent.person_id == person_id)
    vehicle_id = _uuid_from_value(arguments.get("vehicle_id"))
    if vehicle_id:
        query = query.where(AccessEvent.vehicle_id == vehicle_id)
    if bool(arguments.get("unknown_only")):
        query = query.where(AccessEvent.vehicle_id.is_(None))

    events = (await session.scalars(query)).all()
    person_filter = _normalize(arguments.get("person"))
    group_filter = _normalize(arguments.get("group"))
    decision_filter = _normalize(arguments.get("decision"))
    direction_filter = _normalize(arguments.get("direction"))
    for event in events:
        person = person_map.get(str(event.person_id)) if event.person_id else None
        if person_filter and (not person or not _person_record_matches(person, person_filter)):
            continue
        if group_filter and (not person or group_filter not in person.get("group", "").lower()):
            continue
        if decision_filter and event.decision.value != decision_filter:
            continue
        if direction_filter and event.direction.value != direction_filter:
            continue
        return event
    return None

async def _telemetry_for_access_event(session, event: AccessEvent) -> tuple[TelemetryTrace | None, list[TelemetrySpan]]:
    trace_id = _trace_id_from_access_event(event)
    trace: TelemetryTrace | None = None
    if trace_id:
        trace = await session.get(TelemetryTrace, trace_id)
    if not trace:
        trace = await session.scalar(
            select(TelemetryTrace)
            .where(TelemetryTrace.access_event_id == event.id)
            .order_by(TelemetryTrace.started_at.desc())
            .limit(1)
        )
    if not trace:
        return None, []
    spans = (
        await session.scalars(
            select(TelemetrySpan)
            .where(TelemetrySpan.trace_id == trace.trace_id)
            .order_by(TelemetrySpan.step_order, TelemetrySpan.started_at)
        )
    ).all()
    return trace, list(spans)

def _trace_id_from_access_event(event: AccessEvent) -> str | None:
    raw_payload = as_dict(event.raw_payload)
    telemetry_payload = as_dict(raw_payload.get("telemetry"))
    trace_id = str(telemetry_payload.get("trace_id") or "").strip()
    return trace_id or None

def _access_event_core_fields(event: AccessEvent, person: dict[str, str] | None, timezone_name: str, *, include_ids: bool = True, include_vehicle: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(event.id),
        "person": person.get("display_name") if person else None,
        "group": person.get("group") if person else None,
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "confidence": event.confidence,
        "source": event.source,
        **_agent_time_fields("occurred_at", event.occurred_at, timezone_name),
        "timing_classification": event.timing_classification.value,
    }
    if include_ids:
        payload["person_id"] = str(event.person_id) if event.person_id else None
        payload["vehicle_id"] = str(event.vehicle_id) if event.vehicle_id else None
    if include_vehicle:
        payload["vehicle"] = _vehicle_agent_payload(event.vehicle)
    return payload

def _access_event_diagnostic_payload(
    event: AccessEvent,
    person: dict[str, str] | None,
    timezone_name: str,
    *,
    summarize_payload: bool = True,
) -> dict[str, Any]:
    raw_payload = as_dict(event.raw_payload)
    schedule = as_dict(raw_payload.get("schedule"))
    direction_resolution = as_dict(raw_payload.get("direction_resolution"))
    debounce = as_dict(raw_payload.get("debounce"))
    return {
        **_access_event_core_fields(event, person, timezone_name, include_ids=False),
        "schedule": _payload_summary(schedule) if summarize_payload else schedule,
        "direction_resolution": _payload_summary(direction_resolution) if summarize_payload else direction_resolution,
        "gate_observation": _gate_observation_from_event(event),
        "debounce": {
            "candidate_count": debounce.get("candidate_count"),
            "candidates": _compact_value(debounce.get("candidates") or [], max_list_items=6)
            if summarize_payload
            else debounce.get("candidates") or [],
        },
        "anomalies": [
            {
                "id": str(anomaly.id),
                "type": anomaly.anomaly_type.value,
                "severity": anomaly.severity.value,
                "message": anomaly.message,
                "resolved": bool(anomaly.resolved_at),
            }
            for anomaly in event.anomalies
        ],
        "telemetry_trace_id": _trace_id_from_access_event(event),
        "payload_summary": _payload_summary(raw_payload) if summarize_payload else None,
        "raw_payload": raw_payload if not summarize_payload else None,
    }

def _vehicle_agent_payload(vehicle: Vehicle | None) -> dict[str, Any] | None:
    if not vehicle:
        return None
    owner = vehicle.owner
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "description": vehicle.description,
        "is_active": vehicle.is_active,
        "owner": owner.display_name if owner else None,
        "owner_id": str(owner.id) if owner else None,
    }

def _trace_diagnostic_payload(
    trace: TelemetryTrace | None,
    spans: list[TelemetrySpan],
    timezone_name: str,
    *,
    span_limit: int = 20,
    include_payloads: bool = False,
    summarize_payload: bool = True,
) -> dict[str, Any] | None:
    if not trace:
        return None
    limited_spans = spans[:span_limit]
    return {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "category": trace.category,
        "status": trace.status,
        "level": trace.level,
        "started_at": _agent_datetime_iso(trace.started_at, timezone_name),
        "ended_at": _agent_datetime_iso(trace.ended_at, timezone_name) if trace.ended_at else None,
        "duration_ms": trace.duration_ms,
        "summary": trace.summary,
        "error": trace.error,
        "context": _payload_summary(trace.context or {}) if summarize_payload else trace.context or {},
        "span_count": len(spans),
        "spans": [
            _span_diagnostic_payload(
                span,
                timezone_name,
                include_payloads=include_payloads,
                summarize_payload=summarize_payload,
            )
            for span in limited_spans
        ],
    }

def _span_diagnostic_payload(
    span: TelemetrySpan,
    timezone_name: str,
    *,
    include_payloads: bool = False,
    summarize_payload: bool = True,
) -> dict[str, Any]:
    payload = {
        "span_id": span.span_id,
        "name": span.name,
        "category": span.category,
        "step_order": span.step_order,
        "started_at": _agent_datetime_iso(span.started_at, timezone_name),
        "duration_ms": span.duration_ms,
        "status": span.status,
        "attributes": _payload_summary(span.attributes or {}) if summarize_payload else span.attributes or {},
        "error": span.error,
    }
    if include_payloads:
        payload["input_payload"] = span.input_payload or {}
        payload["output_payload"] = span.output_payload or {}
    else:
        payload["input_payload_summary"] = _payload_summary(span.input_payload or {})
        payload["output_payload_summary"] = _payload_summary(span.output_payload or {})
    return _compact_observation(payload)













































def _recognition_diagnostics(
    event: AccessEvent,
    trace: TelemetryTrace | None,
    spans: list[TelemetrySpan],
) -> dict[str, Any]:
    raw_payload = as_dict(event.raw_payload)
    debounce_payload = as_dict(raw_payload.get("debounce"))
    candidates = as_dict_list(debounce_payload.get("candidates"))
    debounce_span = _find_span(spans, "Debounce & Confidence Aggregation")
    slowest_spans = sorted(
        [span for span in spans if span.duration_ms is not None],
        key=lambda span: float(span.duration_ms or 0),
        reverse=True,
    )[:5]
    total_ms = trace.duration_ms if trace else None
    debounce_ms = debounce_span.duration_ms if debounce_span else None
    processing_after_debounce_ms = (
        max(0.0, float(total_ms) - float(debounce_ms))
        if total_ms is not None and debounce_ms is not None
        else None
    )
    exact_known_match = any(
        isinstance(candidate, dict)
        and isinstance(candidate.get("known_vehicle_plate_match"), dict)
        and candidate["known_vehicle_plate_match"].get("exact") is True
        for candidate in candidates
    )
    likely_reason = "Telemetry for this access event was not found."
    if trace:
        slowest = slowest_spans[0] if slowest_spans else None
        if debounce_span and slowest and slowest.span_id == debounce_span.span_id and float(debounce_ms or 0) >= 500:
            likely_reason = "Most of the time was spent waiting in the LPR debounce/confidence window."
            if exact_known_match:
                likely_reason += " An exact known-plate match was present, so the burst should have short-circuited once that read arrived."
            elif len(candidates) > 1:
                likely_reason += " Multiple candidate reads were grouped before the final plate was selected."
            else:
                likely_reason += " Only one candidate was present, so this usually means the quiet-period timer had not expired yet."
        elif slowest:
            likely_reason = f"The slowest recorded step was {slowest.name}."
        else:
            likely_reason = "The trace did not contain any timed spans."

    return {
        "total_pipeline_ms": total_ms,
        "debounce_or_recognition_ms": debounce_ms,
        "processing_after_debounce_ms": processing_after_debounce_ms,
        "candidate_count": debounce_payload.get("candidate_count") or len(candidates),
        "selected_registration_number": event.registration_number,
        "exact_known_plate_match_seen": exact_known_match,
        "slowest_steps": [
            {
                "name": span.name,
                "duration_ms": span.duration_ms,
                "status": span.status,
                "error": span.error,
            }
            for span in slowest_spans
        ],
        "likely_delay_reason": likely_reason,
    }

def _gate_diagnostics(event: AccessEvent, spans: list[TelemetrySpan], timezone_name: str) -> dict[str, Any]:
    gate_observation = _gate_observation_from_event(event)
    gate_span = _find_span(spans, "Home Assistant Gate Open Command Sent")
    garage_spans = [span for span in spans if "Garage Door Command" in span.name]
    automatic_open_considered = (
        event.decision == AccessDecision.GRANTED and event.direction == AccessDirection.ENTRY
    )

    if not automatic_open_considered:
        reason = (
            "The gate was not opened because this event was not a granted entry."
            if event.decision != AccessDecision.GRANTED
            else "The gate was not opened because this event was classified as an exit/departure."
        )
    elif str(gate_observation.get("state") or "unknown") != "closed":
        reason = (
            "Automatic gate and garage-door commands are skipped unless the top gate "
            "was closed at plate-read time."
        )
    elif gate_span is None:
        reason = "The event qualified for an automatic gate open, but no gate command span was recorded."
    elif gate_span.status == "ok" and (gate_span.output_payload or {}).get("accepted") is not False:
        reason = "The automatic gate open command was accepted."
    else:
        output = gate_span.output_payload or {}
        reason = str(gate_span.error or output.get("detail") or "The gate open command failed.")

    return {
        "automatic_open_considered": automatic_open_considered,
        "gate_observation": gate_observation,
        "gate_command": _span_diagnostic_payload(gate_span, timezone_name) if gate_span else None,
        "garage_commands": [
            _span_diagnostic_payload(span, timezone_name)
            for span in garage_spans
        ],
        "outcome_reason": reason,
    }

async def _notification_diagnostics_for_event(
    session,
    event: AccessEvent,
    person: dict[str, str] | None,
    trace_id: str | None,
    spans: list[TelemetrySpan],
    timezone_name: str,
) -> dict[str, Any]:
    triggers = _expected_notification_triggers(event, spans)
    if not triggers:
        return {
            "expected_triggers": [],
            "trigger_diagnostics": [],
            "delivery_records": [],
            "summary": "No notification trigger was expected for this event.",
        }

    rules = (
        await session.scalars(
            select(NotificationRule)
            .where(NotificationRule.trigger_event.in_([trigger["event_type"] for trigger in triggers]))
            .order_by(NotificationRule.trigger_event, NotificationRule.created_at)
        )
    ).all()
    notification_service = get_notification_service()
    delivery_records = _notification_delivery_records(spans, timezone_name)
    trigger_rows: list[dict[str, Any]] = []
    for trigger in triggers:
        trigger_rules = [rule for rule in rules if rule.trigger_event == trigger["event_type"]]
        active_rules = [rule for rule in trigger_rules if rule.is_active]
        context = _notification_context_for_access_event(event, person, trigger, trace_id, timezone_name)
        rule_rows: list[dict[str, Any]] = []
        for rule in trigger_rules:
            conditions_matched = None
            condition_error = None
            if rule.is_active:
                try:
                    conditions_matched = await notification_service.conditions_match(rule, context)
                except Exception as exc:
                    conditions_matched = False
                    condition_error = str(exc)
            rule_rows.append(
                {
                    "id": str(rule.id),
                    "name": rule.name,
                    "is_active": rule.is_active,
                    "conditions": rule.conditions or [],
                    "conditions_matched": conditions_matched,
                    "condition_error": condition_error,
                    "action_count": len(rule.actions or []),
                }
            )

        matching_delivery_records = [
            record for record in delivery_records if record.get("event_type") == trigger["event_type"]
        ]
        if matching_delivery_records:
            conclusion = "A persisted notification delivery record exists for this trigger."
        elif not active_rules:
            conclusion = "No active notification workflow currently matches this trigger."
        elif not any(row.get("conditions_matched") for row in rule_rows if row.get("is_active")):
            conclusion = "Active workflows exist, but their conditions do not currently match this event context."
        else:
            conclusion = (
                "An active workflow appears eligible, but no persisted delivery span was found. "
                "Older events may predate notification delivery telemetry, or delivery may have only appeared in realtime logs."
            )
        trigger_rows.append(
            {
                **trigger,
                "workflow_count": len(trigger_rules),
                "active_workflow_count": len(active_rules),
                "workflows": rule_rows,
                "delivery_records": matching_delivery_records,
                "conclusion": conclusion,
            }
        )

    return {
        "expected_triggers": triggers,
        "trigger_diagnostics": trigger_rows,
        "delivery_records": delivery_records,
        "summary": "; ".join(row["conclusion"] for row in trigger_rows),
    }

def _expected_notification_triggers(event: AccessEvent, spans: list[TelemetrySpan]) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []
    for anomaly in event.anomalies:
        triggers.append(
            {
                "event_type": anomaly.anomaly_type.value,
                "severity": anomaly.severity.value,
                "subject": event.registration_number,
                "reason": anomaly.message,
            }
        )

    gate_span = _find_span(spans, "Home Assistant Gate Open Command Sent")
    if gate_span and gate_span.status == "error":
        triggers.append(
            {
                "event_type": "gate_open_failed",
                "severity": "critical",
                "subject": event.registration_number,
                "reason": str(gate_span.error or (gate_span.output_payload or {}).get("detail") or "Gate command failed."),
            }
        )
    if (
        event.decision == AccessDecision.GRANTED
        and event.direction == AccessDirection.ENTRY
        and gate_span
        and gate_span.status == "ok"
        and (gate_span.output_payload or {}).get("accepted") is not False
    ):
        triggers.append(
            {
                "event_type": "authorized_entry",
                "severity": "info",
                "subject": event.registration_number,
                "reason": "Granted entry and automatic gate open was accepted.",
            }
        )
    return triggers

def _notification_context_for_access_event(
    event: AccessEvent,
    person: dict[str, str] | None,
    trigger: dict[str, Any],
    trace_id: str | None,
    timezone_name: str,
) -> NotificationContext:
    facts = {
        "message": str(trigger.get("reason") or ""),
        "display_name": person.get("display_name") if person else "",
        "group_name": person.get("group") if person else "",
        "registration_number": event.registration_number,
        "vehicle_registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "source": event.source,
        "timing_classification": event.timing_classification.value,
        "occurred_at": _agent_datetime_iso(event.occurred_at, timezone_name),
        "access_event_id": str(event.id),
        "telemetry_trace_id": trace_id or "",
    }
    return NotificationContext(
        event_type=str(trigger["event_type"]),
        subject=str(trigger.get("subject") or event.registration_number),
        severity=str(trigger.get("severity") or "info"),
        facts={key: "" if value is None else str(value) for key, value in facts.items()},
    )

def _notification_delivery_records(spans: list[TelemetrySpan], timezone_name: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for span in spans:
        if not span.name.startswith("Notification "):
            continue
        output = span.output_payload or {}
        records.append(
            {
                "name": span.name,
                "status": span.status,
                "event_type": output.get("event_type"),
                "rule_id": output.get("rule_id"),
                "rule_name": output.get("rule_name"),
                "channel": output.get("channel"),
                "reason": output.get("reason"),
                "delivered": output.get("delivered"),
                "error": span.error or output.get("error"),
                "occurred_at": _agent_datetime_iso(span.started_at, timezone_name),
            }
        )
    return records

async def _registration_history_summary(
    session,
    registration_number: str,
    *,
    timezone_name: str,
    period: str,
    limit: int,
) -> dict[str, Any]:
    normalized = normalize_registration_number(registration_number)
    conditions: list[Any] = [AccessEvent.registration_number == normalized]
    if period != "all":
        start, end = _period_bounds(period, timezone_name)
        conditions.extend([AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end])

    total_count = int(
        await session.scalar(select(func.count()).select_from(AccessEvent).where(*conditions))
        or 0
    )
    granted_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AccessEvent)
            .where(*conditions, AccessEvent.decision == AccessDecision.GRANTED)
        )
        or 0
    )
    denied_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AccessEvent)
            .where(*conditions, AccessEvent.decision == AccessDecision.DENIED)
        )
        or 0
    )
    bounds = (
        await session.execute(
            select(func.min(AccessEvent.occurred_at), func.max(AccessEvent.occurred_at))
            .where(*conditions)
        )
    ).one()
    recent_events = (
        await session.scalars(
            select(AccessEvent)
            .options(selectinload(AccessEvent.anomalies))
            .where(*conditions)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "registration_number": normalized,
        "total_count": total_count,
        "granted_count": granted_count,
        "denied_count": denied_count,
        **_agent_time_fields("first_seen_at", bounds[0], timezone_name),
        **_agent_time_fields("last_seen_at", bounds[1], timezone_name),
        "recent_events": [
            {
                "id": str(row.id),
                "direction": row.direction.value,
                "decision": row.decision.value,
                **_agent_time_fields("occurred_at", row.occurred_at, timezone_name),
                "anomaly_count": len(row.anomalies),
            }
            for row in recent_events
        ],
    }

async def _lpr_timing_near_event(event: AccessEvent, timezone_name: str) -> dict[str, Any]:
    observations = await get_lpr_timing_recorder().recent(limit=300)
    event_at = event.occurred_at if event.occurred_at.tzinfo else event.occurred_at.replace(tzinfo=UTC)
    event_at = event_at.astimezone(UTC)
    registration_number = normalize_registration_number(event.registration_number)
    nearby: list[dict[str, Any]] = []
    for observation in observations:
        observed_plate = normalize_registration_number(
            str(observation.get("registration_number") or observation.get("raw_value") or "")
        )
        if observed_plate != registration_number:
            continue
        received_at = _datetime_from_agent_value(observation.get("received_at"))
        captured_at = _datetime_from_agent_value(observation.get("captured_at"))
        comparison_at = captured_at or received_at
        if comparison_at and abs((comparison_at - event_at).total_seconds()) > 120:
            continue
        serialized = _serialize_lpr_timing_observation(observation, timezone_name)
        serialized["ms_from_access_event_time"] = (
            round((received_at - event_at).total_seconds() * 1000, 1) if received_at else None
        )
        nearby.append(serialized)
    return {
        "observations": nearby[:20],
        "count": len(nearby),
        "note": "This feed is in-memory and only covers recent observations since the backend started.",
    }

def _serialize_lpr_timing_observation(
    observation: dict[str, Any],
    timezone_name: str,
    *,
    include_payload_path: bool = False,
) -> dict[str, Any]:
    received_at = _datetime_from_agent_value(observation.get("received_at"))
    captured_at = _datetime_from_agent_value(observation.get("captured_at"))
    delay_ms = (
        round((received_at - captured_at).total_seconds() * 1000, 1)
        if received_at and captured_at
        else None
    )
    payload = {
        "id": observation.get("id"),
        "source": observation.get("source"),
        "source_detail": observation.get("source_detail"),
        "registration_number": observation.get("registration_number"),
        "raw_value": observation.get("raw_value"),
        "candidate_kind": observation.get("candidate_kind"),
        **_agent_time_fields("received_at", received_at, timezone_name, display=False, raw_value=observation.get("received_at")),
        **_agent_time_fields("captured_at", captured_at, timezone_name, display=False, raw_value=observation.get("captured_at")),
        "captured_to_received_ms": delay_ms,
        "event_id": observation.get("event_id"),
        "camera_id": observation.get("camera_id"),
        "camera_name": observation.get("camera_name"),
        "confidence": observation.get("confidence"),
        "confidence_scale": observation.get("confidence_scale"),
        "protect_action": observation.get("protect_action"),
        "protect_model": observation.get("protect_model"),
    }
    if include_payload_path:
        payload["payload_path"] = observation.get("payload_path")
    return _compact_observation(payload)

def _gate_observation_from_event(event: AccessEvent) -> dict[str, Any]:
    raw_payload = as_dict(event.raw_payload)
    direction_resolution = as_dict(raw_payload.get("direction_resolution"))
    gate_observation = direction_resolution.get("gate_observation")
    if isinstance(gate_observation, dict):
        return gate_observation
    best_payload = as_dict(raw_payload.get("best"))
    value = best_payload.get(GATE_OBSERVATION_PAYLOAD_KEY)
    return value if isinstance(value, dict) else {}

def _find_span(spans: list[TelemetrySpan], name: str) -> TelemetrySpan | None:
    return next((span for span in spans if span.name == name), None)

def _diagnostic_answer_hints(
    recognition: dict[str, Any],
    gate: dict[str, Any],
    notifications: dict[str, Any],
) -> list[str]:
    hints = [str(recognition.get("likely_delay_reason") or "").strip()]
    gate_reason = str(gate.get("outcome_reason") or "").strip()
    if gate_reason:
        hints.append(gate_reason)
    notification_summary = str(notifications.get("summary") or "").strip()
    if notification_summary:
        hints.append(notification_summary)
    return [hint for hint in hints if hint]

def _leaderboard_search_text(arguments: dict[str, Any]) -> str:
    registration = str(arguments.get("registration_number") or "").strip()
    if registration:
        return normalize_registration_number(registration).lower()
    return _person_match_key(
        " ".join(
            str(arguments.get(key) or "").strip()
            for key in ("search", "person")
            if str(arguments.get(key) or "").strip()
        )
    )

def _leaderboard_known_matches(row: dict[str, Any], requested: str) -> bool:
    if not requested:
        return True
    person = as_dict(row.get("person"))
    vehicle = as_dict(row.get("vehicle"))
    haystack = _person_match_key(
        " ".join(
            str(value or "")
            for value in [
                row.get("registration_number"),
                row.get("first_name"),
                row.get("display_name"),
                row.get("vehicle_name"),
                person.get("first_name"),
                person.get("last_name"),
                person.get("display_name"),
                vehicle.get("registration_number"),
                vehicle.get("make"),
                vehicle.get("model"),
                vehicle.get("color"),
                vehicle.get("description"),
                vehicle.get("display_name"),
            ]
        )
    )
    return requested in haystack

def _leaderboard_unknown_matches(row: dict[str, Any], requested: str) -> bool:
    if not requested:
        return True
    dvla = as_dict(row.get("dvla"))
    display_vehicle = as_dict(dvla.get("display_vehicle"))
    haystack = _person_match_key(
        " ".join(
            str(value or "")
            for value in [
                row.get("registration_number"),
                dvla.get("label"),
                display_vehicle.get("make"),
                display_vehicle.get("model"),
                display_vehicle.get("colour"),
                display_vehicle.get("color"),
            ]
        )
    )
    return requested in haystack

def _access_direction_from_argument(value: Any) -> AccessDirection | None:
    text = _normalize(value)
    if not text:
        return None
    try:
        return AccessDirection(text)
    except ValueError:
        return None

def _access_decision_from_argument(value: Any) -> AccessDecision | None:
    text = _normalize(value)
    if not text:
        return None
    try:
        return AccessDecision(text)
    except ValueError:
        return None

def _period_bounds(day: str, timezone_name: str = DEFAULT_AGENT_TIMEZONE) -> tuple[datetime, datetime]:
    now = _agent_now(timezone_name)
    if day == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.astimezone(UTC), now.astimezone(UTC)
    if day == "yesterday":
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (today - timedelta(days=1)).astimezone(UTC), today.astimezone(UTC)
    return (now - timedelta(days=14)).astimezone(UTC), now.astimezone(UTC)

def _human_duration(duration: timedelta) -> str:
    seconds = int(duration.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"

def _human_duration_natural(duration: timedelta | int | float) -> str:
    seconds = int(duration.total_seconds()) if isinstance(duration, timedelta) else int(duration)
    minutes_total = max(0, (seconds + 30) // 60)
    hours, minutes = divmod(minutes_total, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes} min" if minutes == 1 else f"{minutes} mins"

def _compact_interval_labels(left_display: Any, right_display: Any) -> tuple[str, str]:
    left = str(left_display or "").strip()
    right = str(right_display or "").strip()
    if left[:12] and left[:12] == right[:12]:
        return _compact_time_label(left), _compact_time_label(right)
    return left, right

async def query_access_events(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)
    config = await get_runtime_config()
    start, end = _period_bounds(arguments.get("day") or "recent", config.site_timezone)

    async with AsyncSessionLocal() as session:
        query = (
            select(AccessEvent)
            .options(
                selectinload(AccessEvent.vehicle).selectinload(Vehicle.owner),
                selectinload(AccessEvent.anomalies),
            )
            .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(limit)
        )
        person_id_filter = _uuid_from_value(arguments.get("person_id"))
        if person_id_filter:
            query = query.where(AccessEvent.person_id == person_id_filter)
        vehicle_id_filter = _uuid_from_value(arguments.get("vehicle_id"))
        if vehicle_id_filter:
            query = query.where(AccessEvent.vehicle_id == vehicle_id_filter)
        direction_filter = _access_direction_from_argument(arguments.get("direction"))
        if direction_filter:
            query = query.where(AccessEvent.direction == direction_filter)
        decision_filter = _access_decision_from_argument(arguments.get("decision"))
        if decision_filter:
            query = query.where(AccessEvent.decision == decision_filter)
        events = (await session.scalars(query)).all()

        person_filter = _normalize(arguments.get("person"))
        group_filter = _normalize(arguments.get("group"))
        person_map = await _person_map(session)

    records = []
    for event in events:
        person = person_map.get(str(event.person_id)) if event.person_id else None
        if person_filter and (not person or not _person_record_matches(person, person_filter)):
            continue
        if group_filter and (not person or group_filter not in person.get("group", "").lower()):
            continue
        plate_filter = _normalize(arguments.get("registration_number"))
        if plate_filter and plate_filter not in event.registration_number.lower():
            continue
        raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
        schedule_payload = raw_payload.get("schedule") if isinstance(raw_payload.get("schedule"), dict) else None
        records.append(
            _compact_observation(
                {
                    **_access_event_core_fields(event, person, config.site_timezone),
                    "anomaly_count": len(event.anomalies),
                    "schedule_summary": _payload_summary(schedule_payload) if summarize_payload else schedule_payload,
                    "gate_observation": _gate_observation_from_event(event),
                    "payload_summary": _payload_summary(raw_payload) if summarize_payload else None,
                    "raw_payload": raw_payload if not summarize_payload else None,
                }
            )
        )

    return {
        "events": records,
        "count": len(records),
        "timezone": config.site_timezone,
        "answer_artifacts": _access_events_answer_artifacts(arguments, records),
    }


async def diagnose_access_event(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    span_limit = _bounded_int(arguments.get("span_limit"), default=20, minimum=1, maximum=50)
    include_trace_payloads = bool(arguments.get("include_trace_payloads"))
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)
    await telemetry.flush()
    async with AsyncSessionLocal() as session:
        person_map = await _person_map(session)
        event = await _resolve_access_event_for_diagnostics(session, arguments, person_map, config.site_timezone)
        if not event:
            return {
                "found": False,
                "error": "No matching access event was found.",
                "timezone": config.site_timezone,
            }

        person = person_map.get(str(event.person_id)) if event.person_id else None
        trace, spans = await _telemetry_for_access_event(session, event)
        history = await _registration_history_summary(
            session,
            event.registration_number,
            timezone_name=config.site_timezone,
            period="all",
            limit=8,
        )
        notifications = await _notification_diagnostics_for_event(
            session,
            event,
            person,
            trace.trace_id if trace else _trace_id_from_access_event(event),
            spans,
            config.site_timezone,
        )

    lpr_timing = await _lpr_timing_near_event(event, config.site_timezone)
    recognition = _recognition_diagnostics(event, trace, spans)
    gate = _gate_diagnostics(event, spans, config.site_timezone)
    maintenance = await get_maintenance_mode_status()

    return _compact_observation({
        "found": True,
        "timezone": config.site_timezone,
        "event": _access_event_diagnostic_payload(
            event,
            person,
            config.site_timezone,
            summarize_payload=summarize_payload,
        ),
        "recognition": recognition,
        "gate": gate,
        "maintenance_mode": maintenance,
        "notifications": notifications,
        "history": history,
        "lpr_timing_observations": lpr_timing,
        "trace": _trace_diagnostic_payload(
            trace,
            spans,
            config.site_timezone,
            span_limit=span_limit,
            include_payloads=include_trace_payloads,
            summarize_payload=summarize_payload,
        ),
        "answer_hints": _diagnostic_answer_hints(recognition, gate, notifications),
    })










async def query_lpr_timing(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=50, minimum=1, maximum=200)
    config = await get_runtime_config()
    plate_filter = normalize_registration_number(str(arguments.get("registration_number") or ""))
    source_filter = _normalize(arguments.get("source"))
    include_possible_fields = bool(arguments.get("include_possible_fields"))
    include_payload_path = bool(arguments.get("include_payload_path"))
    raw_observations = await get_lpr_timing_recorder().recent(limit=max(limit, 200))

    observations: list[dict[str, Any]] = []
    for observation in raw_observations:
        if not include_possible_fields and observation.get("candidate_kind") == "possible_lpr_field":
            continue
        registration_number = normalize_registration_number(
            str(observation.get("registration_number") or observation.get("raw_value") or "")
        )
        if plate_filter and plate_filter not in registration_number:
            continue
        source_text = f"{observation.get('source') or ''} {observation.get('source_detail') or ''}".lower()
        if source_filter and source_filter not in source_text:
            continue
        observations.append(
            _serialize_lpr_timing_observation(
                observation,
                config.site_timezone,
                include_payload_path=include_payload_path,
            )
        )
        if len(observations) >= limit:
            break

    slowest = sorted(
        [row for row in observations if row.get("captured_to_received_ms") is not None],
        key=lambda row: float(row["captured_to_received_ms"]),
        reverse=True,
    )[:5]
    return {
        "observations": observations,
        "count": len(observations),
        "timezone": config.site_timezone,
        "filters": {
            "registration_number": plate_filter or None,
            "source": source_filter or None,
            "include_possible_fields": include_possible_fields,
        },
        "slowest_observations": slowest,
        "latest_observation": observations[0] if observations else None,
    }


async def query_vehicle_detection_history(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    period = str(arguments.get("period") or "all")
    if period not in {"all", "today", "yesterday", "recent"}:
        period = "all"
    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=50)
    latest_unknown = bool(arguments.get("latest_unknown"))
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))

    async with AsyncSessionLocal() as session:
        if not registration_number:
            query = select(AccessEvent).order_by(AccessEvent.occurred_at.desc())
            if latest_unknown:
                query = query.where(AccessEvent.vehicle_id.is_(None))
            latest = await session.scalar(query.limit(1))
            if not latest:
                return {
                    "found": False,
                    "error": "No access events were found.",
                    "timezone": config.site_timezone,
                }
            registration_number = latest.registration_number
            latest_unknown = latest.vehicle_id is None

        history = await _registration_history_summary(
            session,
            registration_number,
            timezone_name=config.site_timezone,
            period=period,
            limit=limit,
        )

    return {
        "found": bool(history.get("total_count")),
        "registration_number": registration_number,
        "resolved_from_latest_unknown": latest_unknown and not arguments.get("registration_number"),
        "period": period,
        "timezone": config.site_timezone,
        **history,
    }


async def get_telemetry_trace(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    summarize_payload = arguments.get("summarize_payload")
    summarize_payload = True if summarize_payload is None else bool(summarize_payload)
    config = await get_runtime_config()
    trace_id = str(arguments.get("trace_id") or "").strip()
    access_event_id = _uuid_from_value(arguments.get("access_event_id"))
    await telemetry.flush()
    async with AsyncSessionLocal() as session:
        trace: TelemetryTrace | None = None
        if trace_id:
            trace = await session.get(TelemetryTrace, trace_id)
        elif access_event_id:
            trace = await session.scalar(
                select(TelemetryTrace)
                .where(TelemetryTrace.access_event_id == access_event_id)
                .order_by(TelemetryTrace.started_at.desc())
                .limit(1)
            )
        if not trace:
            return {
                "found": False,
                "error": "Telemetry trace not found.",
                "trace_id": trace_id or None,
                "access_event_id": str(access_event_id) if access_event_id else None,
            }
        spans = (
            await session.scalars(
                select(TelemetrySpan)
                .where(TelemetrySpan.trace_id == trace.trace_id)
                .order_by(TelemetrySpan.step_order, TelemetrySpan.started_at)
                .limit(limit)
            )
        ).all()
    return {
        "found": True,
        "timezone": config.site_timezone,
        "trace": _trace_diagnostic_payload(
            trace,
            list(spans),
            config.site_timezone,
            span_limit=limit,
            include_payloads=not summarize_payload,
            summarize_payload=summarize_payload,
        ),
    }


async def query_leaderboard(arguments: dict[str, Any]) -> dict[str, Any]:
    scope = _normalize(arguments.get("scope") or "all")
    if scope not in {"", "all", "known", "unknown", "top_known"}:
        return {"error": "scope must be all, known, unknown, or top_known."}
    scope = scope or "all"

    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    enrich_unknowns = arguments.get("enrich_unknowns")
    if enrich_unknowns is None:
        enrich_unknowns = scope in {"all", "unknown"}

    leaderboard = await get_leaderboard_service().get_leaderboard(
        limit=limit,
        enrich_unknowns=bool(enrich_unknowns),
    )

    search = _leaderboard_search_text(arguments)
    known = [
        row
        for row in list(leaderboard.get("known") or [])
        if not search or _leaderboard_known_matches(row, search)
    ]
    unknown = [
        row
        for row in list(leaderboard.get("unknown") or [])
        if not search or _leaderboard_unknown_matches(row, search)
    ]
    top_known = leaderboard.get("top_known")
    if search and isinstance(top_known, dict) and not _leaderboard_known_matches(top_known, search):
        top_known = known[0] if known else None

    response: dict[str, Any] = {
        "scope": scope,
        "generated_at": leaderboard.get("generated_at"),
        "top_known": top_known,
        "known_count": len(known),
        "unknown_count": len(unknown),
        "search": search or None,
        "enriched_unknowns": bool(enrich_unknowns),
    }
    if scope in {"all", "known"}:
        response["known"] = known
    if scope in {"all", "unknown"}:
        response["unknown"] = unknown
    if scope == "top_known":
        response["known"] = [top_known] if top_known else []
    return response


async def query_anomalies(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=100)
    severity = _normalize(arguments.get("severity"))
    status_filter = _alert_status_from_argument(arguments.get("status"))
    search = _normalize(arguments.get("search"))
    suspected_delivery = bool(arguments.get("suspected_delivery") or arguments.get("possible_delivery"))
    config = await get_runtime_config()
    start, end = _period_bounds(arguments.get("day") or "recent", config.site_timezone)
    query_limit = 250 if search or suspected_delivery or status_filter == "all" else limit
    async with AsyncSessionLocal() as session:
        query = (
            select(Anomaly)
            .options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
            .where(Anomaly.created_at >= start, Anomaly.created_at <= end)
            .order_by(Anomaly.created_at.desc())
            .limit(query_limit)
        )
        if status_filter == "open":
            query = query.where(Anomaly.resolved_at.is_(None))
        elif status_filter == "resolved":
            query = query.where(Anomaly.resolved_at.is_not(None))
        anomalies = (await session.scalars(query)).all()

    records: list[dict[str, Any]] = []
    for anomaly in anomalies:
        if severity and severity != _enum_text(anomaly.severity):
            continue
        record = _anomaly_agent_payload(anomaly, timezone_name=config.site_timezone)
        if not _anomaly_matches_search(record, anomaly, search=search, suspected_delivery=suspected_delivery):
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return {
        "alerts": records,
        "anomalies": records,
        "count": len(records),
        "status": status_filter,
        "day": arguments.get("day") or "recent",
        "search": search or None,
        "suspected_delivery": suspected_delivery,
        "timezone": config.site_timezone,
        "answer_artifacts": _anomaly_answer_artifacts(arguments, records),
    }


async def query_alert_activity(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = _bounded_int(arguments.get("limit"), default=50, minimum=1, maximum=100)
    severity = _normalize(arguments.get("severity"))
    status_filter = _alert_status_from_argument(arguments.get("status") or "all")
    search = _normalize(arguments.get("search"))
    config = await get_runtime_config()
    day = arguments.get("day") or "today"
    start, end = _period_bounds(day, config.site_timezone)
    async with AsyncSessionLocal() as session:
        query = (
            select(Anomaly)
            .options(selectinload(Anomaly.event), selectinload(Anomaly.resolved_by))
            .where(
                or_(
                    (Anomaly.created_at >= start) & (Anomaly.created_at <= end),
                    (Anomaly.resolved_at >= start) & (Anomaly.resolved_at <= end),
                )
            )
            .order_by(Anomaly.created_at.desc())
            .limit(250)
        )
        anomalies = (await session.scalars(query)).all()

    records: list[dict[str, Any]] = []
    raised: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    for anomaly in anomalies:
        if severity and severity != _enum_text(anomaly.severity):
            continue
        record = _anomaly_agent_payload(anomaly, timezone_name=config.site_timezone)
        if not _anomaly_matches_search(record, anomaly, search=search, suspected_delivery=False):
            continue
        created_at = anomaly.created_at if anomaly.created_at.tzinfo else anomaly.created_at.replace(tzinfo=UTC)
        resolved_at = anomaly.resolved_at
        if resolved_at and resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=UTC)
        was_raised = start <= created_at <= end
        was_resolved = bool(resolved_at and start <= resolved_at <= end)
        if status_filter == "open" and record.get("status") != "open":
            continue
        if status_filter == "resolved" and not was_resolved:
            continue
        if was_raised:
            raised.append(record)
        if was_resolved:
            resolved.append(record)
        records.append(record)
        if len(records) >= limit:
            break

    raised = raised[:limit]
    resolved = resolved[:limit]
    return {
        "alerts": records[:limit],
        "raised": raised,
        "resolved": resolved,
        "raised_count": len(raised),
        "resolved_count": len(resolved),
        "count": len(records[:limit]),
        "status": status_filter,
        "day": day,
        "search": search or None,
        "timezone": config.site_timezone,
        "answer_artifacts": _alert_activity_answer_artifacts(arguments, raised, resolved),
    }


async def analyze_alert_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    alert_id = _uuid_from_value(arguments.get("alert_id"))
    if not alert_id:
        return {"error": "alert_id is required."}
    prompt = str(
        arguments.get("prompt")
        or "Inspect this retained alert snapshot. Describe visible vehicles, supplier branding, and whether it looks like a delivery."
    ).strip()
    runtime = await get_runtime_config()
    provider = str(arguments.get("provider") or runtime.llm_provider)

    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(Anomaly)
            .options(selectinload(Anomaly.event))
            .where(Anomaly.id == alert_id)
        )
    if not row:
        return {"alert_id": str(alert_id), "error": "Alert was not found."}

    media = _alert_snapshot_file(row)
    if not media:
        return {"alert_id": str(alert_id), "error": "No retained snapshot is available for this alert."}
    path, content_type, snapshot = media
    try:
        result = await analyze_image_with_provider(
            provider,
            prompt=prompt,
            image_bytes=await asyncio.to_thread(path.read_bytes),
            mime_type=content_type,
        )
    except (ImageAnalysisUnsupportedError, Exception) as exc:
        return {"alert_id": str(alert_id), "provider": provider, "error": str(exc)}

    return {
        "alert_id": str(alert_id),
        "provider": provider,
        "analysis": result.text,
        "snapshot": _compact_alert_snapshot(snapshot),
        "snapshot_retained": True,
    }


async def summarize_access_rhythm(arguments: dict[str, Any]) -> dict[str, Any]:
    result = await query_access_events({"day": arguments.get("day") or "today", "limit": 100})
    events = result["events"]
    return {
        "period": arguments.get("day") or "today",
        "total_events": len(events),
        "entries": sum(1 for event in events if event["direction"] == "entry"),
        "exits": sum(1 for event in events if event["direction"] == "exit"),
        "denials": sum(1 for event in events if event["decision"] == "denied"),
        "anomaly_events": sum(1 for event in events if event["anomaly_count"] > 0),
        "events": events[:10],
    }


def _access_event_time(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(event["occurred_at"])).astimezone(UTC)

def _duration_intervals(events: list[dict[str, Any]], *, timezone_name: str, start_direction: str, end_direction: str, start_field: str, end_field: str, open_end: str, reset_on_start: bool, include_seconds: bool) -> tuple[timedelta, list[dict[str, Any]]]:
    opened_at: datetime | None = None
    total = timedelta()
    intervals: list[dict[str, Any]] = []
    for event in events:
        if event["decision"] != AccessDecision.GRANTED.value:
            continue
        occurred = _access_event_time(event)
        if event["direction"] == start_direction and (reset_on_start or opened_at is None):
            opened_at = occurred
        elif event["direction"] == end_direction and opened_at:
            total += occurred - opened_at
            intervals.append(
                _duration_interval_payload(
                    opened_at,
                    occurred,
                    timezone_name,
                    start_field=start_field,
                    end_field=end_field,
                    include_seconds=include_seconds,
                )
            )
            opened_at = None
    if opened_at:
        now = _agent_now(timezone_name)
        total += now - opened_at
        intervals.append(
            _duration_interval_payload(
                opened_at,
                None,
                timezone_name,
                start_field=start_field,
                end_field=end_field,
                open_end=open_end,
                now=now,
                include_seconds=include_seconds,
            )
        )
    return total, intervals

def _duration_interval_payload(start: datetime, end: datetime | None, timezone_name: str, *, start_field: str, end_field: str, include_seconds: bool, open_end: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    interval: dict[str, Any] = {start_field: _agent_datetime_iso(start, timezone_name), f"{start_field}_display": _agent_datetime_display(start, timezone_name)}
    interval.update({end_field: _agent_datetime_iso(end, timezone_name) if end else open_end, f"{end_field}_display": _agent_datetime_display(end, timezone_name) if end else None})
    if include_seconds:
        seconds = max(0, int(((end or now or _agent_now(timezone_name)) - start).total_seconds()))
        interval.update({"seconds": seconds, "duration_human": _human_duration(timedelta(seconds=seconds))})
    return interval

def _duration_artifact(*, answer_type: str, subject: str, fact_id: str, label: str, seconds: int, display_value: str, compact_display: str, day: Any, source_records: list[dict[str, Any]], canonical_text: str, metadata: dict[str, Any] | None = None, supporting_facts: list[dict[str, Any]] | None = None, display: dict[str, Any] | None = None, time_scope_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    primary_fact = _answer_fact(
        fact_id,
        label,
        seconds,
        display_value,
        "duration",
        "access_events",
        must_appear=bool(seconds),
        metadata={"compact_display": compact_display, **(metadata or {})},
    )
    return artifact_payload(
        domain="access_logs",
        answer_type=answer_type,
        subject_label=subject,
        primary_fact=primary_fact,
        supporting_facts=supporting_facts or [],
        time_scope={"day": day or "today", **(time_scope_extra or {})},
        source_records=source_records,
        display=display or {"voice": "natural_concise", "no_timezone_labels": True},
        canonical_text=canonical_text,
    )


async def calculate_visit_duration(arguments: dict[str, Any]) -> dict[str, Any]:
    result = await query_access_events(
        {
            "person": arguments.get("person"),
            "person_id": arguments.get("person_id"),
            "group": arguments.get("group"),
            "day": arguments.get("day") or "today",
            "limit": 100,
        }
    )
    timezone_name = str(result.get("timezone") or DEFAULT_AGENT_TIMEZONE)
    events = sorted(result["events"], key=_access_event_time)
    total, intervals = _duration_intervals(
        events,
        timezone_name=timezone_name,
        start_direction=AccessDirection.ENTRY.value,
        end_direction=AccessDirection.EXIT.value,
        start_field="entry",
        end_field="exit",
        open_end="still_present",
        reset_on_start=True,
        include_seconds=False,
    )
    duration_seconds = int(total.total_seconds())
    duration_human = _human_duration(total)
    display_duration = _human_duration_natural(duration_seconds)
    subject = _preferred_subject_label(arguments, "The matched visit")
    latest_interval = intervals[-1] if intervals else None
    canonical_text = (
        f"{subject} has been here for {display_duration}."
        if isinstance(latest_interval, dict) and latest_interval.get("exit") == "still_present"
        else f"{subject} was here for {display_duration}."
        if duration_seconds
        else "I couldn't find enough matching access events to calculate a visit duration."
    )
    return {
        "duration_seconds": duration_seconds,
        "duration_human": duration_human,
        "duration_display": display_duration,
        "intervals": intervals,
        "matched_events": len(events),
        "timezone": timezone_name,
        "answer_artifacts": [
            _duration_artifact(
                answer_type="visit_duration",
                subject=subject,
                fact_id="visit.duration",
                label="Visit duration",
                seconds=duration_seconds,
                display_value=display_duration,
                compact_display=duration_human,
                day=arguments.get("day"),
                source_records=[
                    {"entry_at": item.get("entry_display"), "exit_at": item.get("exit_display") or item.get("exit"), "entry": item.get("entry"), "exit": item.get("exit")}
                    for item in intervals
                ],
                canonical_text=canonical_text,
            )
        ],
    }


async def calculate_absence_duration(arguments: dict[str, Any]) -> dict[str, Any]:
    mode = str(arguments.get("mode") or "latest").strip().lower()
    if mode not in {"latest", "total"}:
        mode = "latest"
    result = await query_access_events(
        {
            "person": arguments.get("person"),
            "person_id": arguments.get("person_id"),
            "vehicle_id": arguments.get("vehicle_id"),
            "group": arguments.get("group"),
            "day": arguments.get("day") or "today",
            "limit": 100,
        }
    )
    timezone_name = str(result.get("timezone") or DEFAULT_AGENT_TIMEZONE)
    events = sorted(result["events"], key=_access_event_time)
    subject = next(
        (
            str(event.get("person") or event.get("registration_number") or "").strip()
            for event in events
            if str(event.get("person") or event.get("registration_number") or "").strip()
        ),
        str(arguments.get("person") or arguments.get("group") or "The matched subject").strip(),
    )
    total, intervals = _duration_intervals(
        events,
        timezone_name=timezone_name,
        start_direction=AccessDirection.EXIT.value,
        end_direction=AccessDirection.ENTRY.value,
        start_field="exit",
        end_field="entry",
        open_end="still_away",
        reset_on_start=False,
        include_seconds=True,
    )

    total_seconds = max(0, int(total.total_seconds()))
    latest_interval = intervals[-1] if intervals else None
    status = "still_away" if latest_interval and latest_interval.get("entry") == "still_away" else "returned" if intervals else "not_found"
    as_of = _agent_now(timezone_name) if status == "still_away" else None
    primary_seconds = total_seconds
    if mode == "latest" and latest_interval:
        primary_seconds = int(latest_interval.get("seconds") or 0)
    duration_human = _human_duration(timedelta(seconds=primary_seconds))
    display_duration = _human_duration_natural(primary_seconds)
    display_subject = _preferred_subject_label(arguments, subject or "The matched subject")
    source_records: list[dict[str, Any]] = []
    canonical_text = ""
    if latest_interval:
        left_display_raw = latest_interval.get("exit_display")
        returned_display_raw = latest_interval.get("entry_display")
        left_display, returned_display = _compact_interval_labels(left_display_raw, returned_display_raw)
        if latest_interval.get("entry") == "still_away" and str(arguments.get("day") or "today") == "today":
            left_display = _compact_time_label(left_display_raw)
        source_records.append(
            {
                "left_at": left_display,
                "returned_at": returned_display or latest_interval.get("entry"),
                "left_at_full": left_display_raw,
                "returned_at_full": returned_display_raw,
                "seconds": latest_interval.get("seconds"),
                "mode": mode,
            }
        )
        if mode == "total" and len(intervals) > 1:
            canonical_text = f"{display_subject} was out for {display_duration} in total across {len(intervals)} matched absences."
        elif latest_interval.get("entry") == "still_away":
            suffix = f" since {left_display}" if left_display else ""
            canonical_text = f"{display_subject} has been out for {display_duration}{suffix}. Still away, so the clock is still running."
        elif left_display and returned_display:
            canonical_text = f"{display_subject} was out for {display_duration}, from {left_display} to {returned_display}."
        else:
            canonical_text = f"{display_subject} was out for {display_duration}."
    else:
        canonical_text = "I couldn't find enough matching access events to calculate an absence duration."
    response_hint = _absence_duration_answer_hint(
        subject=subject or "The matched subject",
        duration=duration_human,
        primary_interval=latest_interval,
        status=status,
        as_of=as_of,
        timezone_name=timezone_name,
        mode=mode,
        total_human=_human_duration(timedelta(seconds=total_seconds)),
        interval_count=len(intervals),
    )
    supporting_facts = [
        _answer_fact(
            "absence.interval_count",
            "Matched absence intervals",
            len(intervals),
            len(intervals),
            "count",
            "access_events",
            must_appear=mode == "total" and len(intervals) > 1,
        )
    ]
    return {
        "subject": subject or "The matched subject",
        "absence_seconds": primary_seconds,
        "absence_human": duration_human,
        "absence_display": display_duration,
        "total_absence_seconds": total_seconds,
        "total_absence_human": _human_duration(timedelta(seconds=total_seconds)),
        "total_absence_display": _human_duration_natural(total_seconds),
        "intervals": intervals,
        "primary_interval": latest_interval,
        "mode": mode,
        "matched_events": len(events),
        "status": status,
        "timezone": timezone_name,
        "as_of": _agent_datetime_iso(as_of, timezone_name) if as_of else None,
        "as_of_display": _agent_datetime_display(as_of, timezone_name) if as_of else None,
        "answer_hints": [response_hint] if response_hint else [],
        "answer_artifacts": [
            _duration_artifact(
                answer_type="absence_duration",
                subject=display_subject,
                fact_id="absence.duration.total" if mode == "total" else "absence.duration.latest",
                label="Total absence duration" if mode == "total" else "Latest absence duration",
                seconds=primary_seconds,
                display_value=display_duration,
                compact_display=duration_human,
                day=arguments.get("day"),
                metadata={"mode": mode, "total_seconds": total_seconds, "interval_count": len(intervals)},
                supporting_facts=supporting_facts,
                time_scope_extra={"mode": mode, "status": status},
                source_records=source_records,
                display={"voice": "natural_concise", "no_timezone_labels": True, "subject_full_name": subject},
                canonical_text=canonical_text,
            )
        ],
    }


async def trigger_anomaly_alert(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        subject = str(arguments.get("subject") or "anomaly alert").strip()
        return {
            "sent": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": subject,
            "detail": "Send this anomaly alert notification?",
        }
    try:
        notification = await get_notification_service().notify(
            NotificationContext(
                event_type="agent_anomaly_alert",
                subject=str(arguments["subject"]),
                severity=str(arguments["severity"]),
                facts={"message": str(arguments["message"])},
            ),
            raise_on_failure=True,
        )
    except NotificationDeliveryError as exc:
        return {"sent": False, "error": str(exc)}
    await event_bus.publish(
        "ai.issue_detected",
        {
            "subject": str(arguments["subject"]),
            "severity": str(arguments["severity"]),
            "message": str(arguments["message"]),
            "issue": str(arguments["message"]),
            "source": "alfred",
        },
    )
    return {"sent": True, "title": notification.title, "body": notification.body}
