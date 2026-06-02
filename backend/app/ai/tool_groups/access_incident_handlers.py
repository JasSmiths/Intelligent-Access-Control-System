"""Access incident and backfill Alfred tool handlers."""
# ruff: noqa: F403, F405

from app.ai.tool_groups._shared import *
from app.ai.tool_groups.access_diagnostics_handlers import _access_event_core_fields, _access_event_load_options, _agent_time_fields, _datetime_from_agent_value, _gate_observation_from_event, _period_bounds, _serialize_lpr_timing_observation, _trace_id_from_access_event, _vehicle_agent_payload, diagnose_access_event
from app.services.movement.sessions import VEHICLE_SESSION_PAYLOAD_KEY

SUPPRESSED_READ_ROOT_CAUSES = {
    "iacs_read_suppressed_as_active_vehicle_session",
    "iacs_read_suppressed_before_access_event",
}

def _incident_window(arguments: dict[str, Any], timezone_name: str) -> tuple[datetime, datetime, datetime | None]:
    day = str(arguments.get("day") or "today")
    expected_at = _parse_incident_datetime(
        arguments.get("expected_time") or arguments.get("captured_at") or arguments.get("at"),
        timezone_name,
        day,
    )
    window_minutes = _bounded_int(arguments.get("window_minutes"), default=20, minimum=1, maximum=720)
    if expected_at:
        start = (expected_at - timedelta(minutes=window_minutes)).astimezone(UTC)
        end = (expected_at + timedelta(minutes=window_minutes)).astimezone(UTC)
        return start, end, expected_at.astimezone(UTC)
    start, end = _period_bounds(day, timezone_name)
    return start, end, None
def _parse_incident_datetime(value: Any, timezone_name: str, day: str = "today") -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _parse_agent_datetime(text, timezone_name).astimezone(UTC)
    except ValueError:
        pass

    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\b", text.lower())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridian = match.group(3)
    if meridian == "pm" and hour < 12:
        hour += 12
    if meridian == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    start, _end = _period_bounds(day, timezone_name)
    local_day = start.astimezone(_agent_timezone(timezone_name))
    return local_day.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(UTC)
async def _resolve_incident_subject(session, arguments: dict[str, Any]) -> dict[str, Any]:
    person: Person | None = None
    vehicle: Vehicle | None = None
    vehicle_id = _uuid_from_value(arguments.get("vehicle_id"))
    if vehicle_id:
        vehicle = await session.scalar(
            select(Vehicle)
            .options(
                selectinload(Vehicle.owner).selectinload(Person.group),
                selectinload(Vehicle.owner).selectinload(Person.schedule),
                selectinload(Vehicle.schedule),
            )
            .where(Vehicle.id == vehicle_id)
        )
        person = vehicle.owner if vehicle else None

    person_id = _uuid_from_value(arguments.get("person_id"))
    if not person and person_id:
        person = await session.scalar(
            select(Person)
            .options(
                selectinload(Person.group),
                selectinload(Person.schedule),
                selectinload(Person.vehicles).selectinload(Vehicle.schedule),
            )
            .where(Person.id == person_id)
        )

    person_text = _normalize(arguments.get("person") or arguments.get("person_name") or arguments.get("name"))
    if not person and person_text:
        people = (
            await session.scalars(
                select(Person)
                .options(
                    selectinload(Person.group),
                    selectinload(Person.schedule),
                    selectinload(Person.vehicles).selectinload(Vehicle.schedule),
                )
                .order_by(Person.display_name)
            )
        ).all()
        matches = [
            item
            for item in people
            if _person_record_matches(
                {"display_name": item.display_name, "group": item.group.name if item.group else ""},
                person_text,
            )
        ]
        if len(matches) == 1:
            person = matches[0]

    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number and not vehicle:
        vehicle = await session.scalar(
            select(Vehicle)
            .options(
                selectinload(Vehicle.owner).selectinload(Person.group),
                selectinload(Vehicle.owner).selectinload(Person.schedule),
                selectinload(Vehicle.schedule),
            )
            .where(Vehicle.registration_number == registration_number)
        )
        if vehicle and not person:
            person = vehicle.owner

    vehicles = [vehicle] if vehicle else list(person.vehicles or []) if person else []
    vehicle_payloads = [_vehicle_agent_payload(item) for item in vehicles]
    plates = [
        normalize_registration_number(str(item.registration_number or ""))
        for item in vehicles
        if str(item.registration_number or "").strip()
    ]
    if registration_number and registration_number not in plates:
        plates.append(registration_number)
    label = (
        person.display_name
        if person
        else vehicle.registration_number
        if vehicle
        else registration_number
        if registration_number
        else "Unresolved subject"
    )
    return {
        "person": person,
        "vehicle": vehicle,
        "vehicles": vehicles,
        "summary": _compact_observation(
            {
                "label": label,
                "person_id": str(person.id) if person else None,
                "person": person.display_name if person else None,
                "group": person.group.name if person and person.group else None,
                "vehicle_id": str(vehicle.id) if vehicle else None,
                "vehicles": vehicle_payloads,
                "plates": plates,
                "resolved": bool(person or vehicle or registration_number),
            }
        ),
    }
def _incident_candidate_plates(subject: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    plates = list((subject.get("summary") or {}).get("plates") or [])
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number and registration_number not in plates:
        plates.append(registration_number)
    return [plate for plate in plates if plate]
async def _incident_iacs_events(
    session,
    *,
    subject: dict[str, Any],
    plates: list[str],
    start: datetime,
    end: datetime,
    direction: str,
) -> list[dict[str, Any]]:
    summary = as_dict(subject.get("summary"))
    query = (
        select(AccessEvent)
        .options(*_access_event_load_options())
        .where(AccessEvent.occurred_at >= start, AccessEvent.occurred_at <= end)
        .order_by(AccessEvent.occurred_at.desc())
        .limit(50)
    )
    person_id = _uuid_from_value(summary.get("person_id"))
    vehicle_id = _uuid_from_value(summary.get("vehicle_id"))
    if vehicle_id:
        query = query.where(AccessEvent.vehicle_id == vehicle_id)
    elif person_id:
        query = query.where(AccessEvent.person_id == person_id)
    elif plates:
        query = query.where(AccessEvent.registration_number.in_(plates))
    if direction:
        query = query.where(AccessEvent.direction == AccessDirection(direction))
    events = (await session.scalars(query)).all()
    return [_incident_access_event_payload(event) for event in events]
def _incident_access_event_payload(event: AccessEvent) -> dict[str, Any]:
    person = event.vehicle.owner if event.vehicle and event.vehicle.owner else None
    person_payload = {"display_name": person.display_name, "group": ""} if person else None
    return _compact_observation(
        {
            **_access_event_core_fields(event, person_payload, DEFAULT_AGENT_TIMEZONE, include_vehicle=False),
            "occurred_at": event.occurred_at.isoformat(),
            "telemetry_trace_id": _trace_id_from_access_event(event),
            "gate_observation": _gate_observation_from_event(event),
        }
    )
async def _incident_telemetry_traces(
    session,
    *,
    start: datetime,
    end: datetime,
    plates: list[str],
    timezone_name: str,
) -> list[dict[str, Any]]:
    traces = (
        await session.scalars(
            select(TelemetryTrace)
            .where(
                TelemetryTrace.started_at >= start,
                TelemetryTrace.started_at <= end,
                TelemetryTrace.category.in_(
                    [TELEMETRY_CATEGORY_WEBHOOKS_API, "lpr_telemetry", TELEMETRY_CATEGORY_ACCESS, TELEMETRY_CATEGORY_ALFRED]
                ),
            )
            .order_by(TelemetryTrace.started_at.desc())
            .limit(60)
        )
    ).all()
    records = []
    for trace in traces:
        if plates and trace.registration_number and trace.registration_number not in plates:
            continue
        records.append(_incident_trace_payload(trace, timezone_name))
    return records
def _incident_trace_payload(trace: TelemetryTrace, timezone_name: str) -> dict[str, Any]:
    context = trace.context if isinstance(trace.context, dict) else {}
    return _compact_observation(
        {
            "trace_id": trace.trace_id,
            "name": trace.name,
            "category": trace.category,
            "status": trace.status,
        "level": trace.level,
        **_agent_time_fields("started_at", trace.started_at, timezone_name),
        "summary": trace.summary,
            "registration_number": trace.registration_number,
            "path": context.get("path"),
            "status_code": context.get("status_code"),
            "user_agent": context.get("user_agent"),
            "request_id": context.get("request_id"),
        }
    )
async def _incident_audit_logs(session, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    logs = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.timestamp >= start, AuditLog.timestamp <= end)
            .order_by(AuditLog.timestamp.desc())
            .limit(30)
        )
    ).all()
    return [
        _compact_observation(
            {
                "id": str(row.id),
                "timestamp": _agent_datetime_iso(row.timestamp, DEFAULT_AGENT_TIMEZONE),
                "action": row.action,
                "category": row.category,
                "actor": row.actor,
                "target_entity": row.target_entity,
                "target_label": row.target_label,
                "outcome": row.outcome,
                "trace_id": row.trace_id,
            }
        )
        for row in logs
    ]
async def _incident_gate_observations(session, *, start: datetime, end: datetime, timezone_name: str) -> list[dict[str, Any]]:
    observations = (
        await session.scalars(
            select(GateStateObservation)
            .where(GateStateObservation.observed_at >= start, GateStateObservation.observed_at <= end)
            .order_by(GateStateObservation.observed_at)
            .limit(40)
        )
    ).all()
    return [_gate_observation_payload(observation, timezone_name) for observation in observations]
def _gate_observation_payload(observation: GateStateObservation, timezone_name: str) -> dict[str, Any]:
    return _compact_observation(
        {
            "id": str(observation.id),
            "gate_entity_id": observation.gate_entity_id,
            "gate_name": observation.gate_name,
            "state": observation.state,
            "raw_state": observation.raw_state,
            "previous_state": observation.previous_state,
            **_agent_time_fields("observed_at", observation.observed_at, timezone_name),
            "source": observation.source,
        }
    )
async def _incident_anomalies(
    session,
    *,
    start: datetime,
    end: datetime,
    plates: list[str],
    timezone_name: str,
) -> list[dict[str, Any]]:
    anomalies = (
        await session.scalars(
            select(Anomaly)
            .options(selectinload(Anomaly.event))
            .where(Anomaly.created_at >= start, Anomaly.created_at <= end)
            .order_by(Anomaly.created_at.desc())
            .limit(30)
        )
    ).all()
    records = []
    for anomaly in anomalies:
        event = anomaly.event
        if plates and event and event.registration_number not in plates:
            continue
        records.append(
            _compact_observation(
                {
                    "id": str(anomaly.id),
                    "type": anomaly.anomaly_type.value,
                    "severity": anomaly.severity.value,
                    "message": anomaly.message,
                    "created_at": _agent_datetime_iso(anomaly.created_at, timezone_name),
                    "event_id": str(anomaly.event_id) if anomaly.event_id else None,
                    "registration_number": event.registration_number if event else None,
                    "resolved": bool(anomaly.resolved_at),
                }
            )
        )
    return records
async def _incident_schedule_diagnostics(
    session,
    *,
    subject: dict[str, Any],
    checked_at: datetime,
    timezone_name: str,
    default_policy: str,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    vehicles = [item for item in subject.get("vehicles") or [] if isinstance(item, Vehicle)]
    person = subject.get("person") if isinstance(subject.get("person"), Person) else None
    for vehicle in vehicles:
        evaluation = await evaluate_vehicle_schedule(
            session,
            vehicle,
            checked_at,
            timezone_name=timezone_name,
            default_policy=default_policy,
        )
        diagnostics.append(_schedule_evaluation_payload(evaluation, f"vehicle {vehicle.registration_number}", timezone_name))
    if person and not vehicles:
        evaluation = await evaluate_person_schedule(
            session,
            person,
            checked_at,
            timezone_name=timezone_name,
            default_policy=default_policy,
        )
        diagnostics.append(_schedule_evaluation_payload(evaluation, person.display_name, timezone_name))
    return diagnostics
def _schedule_evaluation_payload(evaluation: Any, label: str, timezone_name: str) -> dict[str, Any]:
    return _compact_observation(
        {
            "target": label,
            "allowed": bool(evaluation.allowed),
            "source": evaluation.source,
            "schedule_id": str(evaluation.schedule_id) if evaluation.schedule_id else None,
            "schedule_name": evaluation.schedule_name,
            "reason": evaluation.reason,
            "override_id": str(evaluation.override_id) if evaluation.override_id else None,
            "override_ends_at": _agent_datetime_iso(evaluation.override_ends_at, timezone_name) if evaluation.override_ends_at else None,
        }
    )
async def _incident_notification_summary(session, *, incident_type: str) -> dict[str, Any]:
    rules = (
        await session.scalars(
            select(NotificationRule)
            .where(NotificationRule.is_active.is_(True))
            .order_by(NotificationRule.trigger_event, NotificationRule.name)
            .limit(100)
        )
    ).all()
    relevant_triggers = {
        "authorized_entry",
        "unauthorized_plate",
        "outside_schedule",
        "duplicate_entry",
        "duplicate_exit",
        "gate_open_failure",
        "garage_door_open_failure",
    }
    relevant = rules if incident_type == "notification_failure" else [rule for rule in rules if rule.trigger_event in relevant_triggers]
    return _compact_observation(
        {
            "active_rule_count": len(rules),
            "relevant_rule_count": len(relevant),
            "relevant_rules": [
                {
                    "id": str(rule.id),
                    "name": rule.name,
                    "trigger_event": rule.trigger_event,
                    "action_count": len(rule.actions or []),
                    "condition_count": len(rule.conditions or []),
                    "last_fired_at": rule.last_fired_at.isoformat() if rule.last_fired_at else None,
                }
                for rule in relevant[:12]
            ],
        }
    )
async def _query_protect_events_for_incident(
    *,
    start: datetime,
    end: datetime,
    timezone_name: str,
    plates: list[str],
    camera_id: str | None = None,
    camera_name: str | None = None,
    smart_detect_type: str | None = None,
    include_tracks: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    try:
        events = await get_unifi_protect_service().list_events(
            camera_id=camera_id,
            limit=limit,
            since=start,
            until=end,
        )
    except UnifiProtectError as exc:
        return {"available": False, "error": str(exc), "events": [], "count": 0}

    records: list[dict[str, Any]] = []
    matched_event: dict[str, Any] | None = None
    track_errors: list[dict[str, str]] = []
    for event in events:
        if not _protect_event_matches(event, camera_name=camera_name, smart_detect_type=smart_detect_type):
            continue
        record = _protect_event_payload(event, timezone_name)
        if include_tracks and event.get("id") and (plates or len(records) < 25):
            try:
                track = await get_unifi_protect_service().event_lpr_track(str(event["id"]))
                observations = [
                    _serialize_lpr_timing_observation(observation, timezone_name, include_payload_path=True)
                    for observation in track.get("observations", [])
                    if isinstance(observation, dict)
                ]
                record["track_observations"] = observations
                record["track_observation_count"] = len(observations)
                candidate = _best_track_candidate(observations, plates)
                if candidate:
                    record["matched_candidate"] = candidate
                    matched_event = matched_event or record
            except UnifiProtectError as exc:
                track_errors.append({"event_id": str(event.get("id") or ""), "error": str(exc)})
        if not matched_event and not plates and _event_looks_like_lpr(record):
            matched_event = record
        records.append(record)

    return _compact_observation(
        {
            "available": True,
            "events": records,
            "count": len(records),
            "matched_event": matched_event,
            "matched": bool(matched_event),
            "track_errors": track_errors,
            "window": {
                "start": _agent_datetime_iso(start, timezone_name),
                "end": _agent_datetime_iso(end, timezone_name),
            },
            "filters": {
                "camera_id": camera_id,
                "camera_name": camera_name,
                "smart_detect_type": smart_detect_type,
                "plates": plates,
            },
        }
    )
def _protect_event_matches(event: dict[str, Any], *, camera_name: str | None, smart_detect_type: str | None) -> bool:
    if camera_name:
        haystack = f"{event.get('camera_name') or ''} {event.get('camera_id') or ''}".lower()
        if camera_name.lower() not in haystack:
            return False
    if smart_detect_type:
        requested = re.sub(r"[^a-z0-9]+", "", smart_detect_type.lower())
        event_types = [
            re.sub(r"[^a-z0-9]+", "", str(item or "").lower())
            for item in event.get("smart_detect_types", [])
        ]
        if requested not in event_types:
            return False
    return True
def _protect_event_payload(event: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    started = _datetime_from_agent_value(event.get("start"))
    ended = _datetime_from_agent_value(event.get("end"))
    return _compact_observation(
        {
            "id": event.get("id"),
            "type": event.get("type"),
            "camera_id": event.get("camera_id"),
            "camera_name": event.get("camera_name"),
            **_agent_time_fields("start", started, timezone_name, raw_value=event.get("start")),
            **_agent_time_fields("end", ended, timezone_name, display=False, raw_value=event.get("end")),
            "score": event.get("score"),
            "smart_detect_types": event.get("smart_detect_types"),
            "metadata": _payload_summary(event.get("metadata")),
        }
    )
def _best_track_candidate(observations: list[dict[str, Any]], plates: list[str]) -> dict[str, Any] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for observation in observations:
        candidate_plate = normalize_registration_number(str(observation.get("registration_number") or observation.get("raw_value") or ""))
        if not candidate_plate:
            continue
        score = max((_plate_match_score(candidate_plate, plate) for plate in plates), default=0.0) if plates else 1.0
        if score < 0.78:
            continue
        candidate = {
            "registration_number": candidate_plate,
            "raw_value": observation.get("raw_value"),
            "captured_at": observation.get("captured_at"),
            "confidence": observation.get("confidence"),
            "confidence_scale": observation.get("confidence_scale"),
            "score": round(score, 3),
            "source_detail": observation.get("source_detail"),
            "payload_path": observation.get("payload_path"),
        }
        if best is None or score > best[0]:
            best = (score, candidate)
    return best[1] if best else None
def _plate_match_score(candidate: str, expected: str) -> float:
    candidate = normalize_registration_number(candidate)
    expected = normalize_registration_number(expected)
    if not candidate or not expected:
        return 0.0
    if candidate == expected:
        return 1.0
    if candidate in expected or expected in candidate:
        return 0.9
    return SequenceMatcher(None, candidate, expected).ratio()
def _event_looks_like_lpr(event: dict[str, Any]) -> bool:
    types = " ".join(str(item or "").lower() for item in event.get("smart_detect_types", []))
    camera = f"{event.get('camera_name') or ''} {event.get('camera_id') or ''}".lower()
    return "license" in types or "plate" in types or "lpr" in camera or "license" in camera
def _runtime_vehicle_session_idle_seconds(config: Any) -> float:
    try:
        value = float(getattr(config, "lpr_vehicle_session_idle_seconds", 0) or 0)
    except (TypeError, ValueError):
        value = 0.0
    return max(10.0, value or 180.0)
async def _incident_suppressed_reads(
    session,
    *,
    subject: dict[str, Any],
    plates: list[str],
    start: datetime,
    end: datetime,
    direction: str,
    timezone_name: str,
    idle_seconds: float,
) -> list[dict[str, Any]]:
    search_start = start - timedelta(seconds=max(idle_seconds * 3, 3600.0))
    events = (
        await session.scalars(
            select(AccessEvent)
            .options(*_access_event_load_options())
            .where(AccessEvent.occurred_at >= search_start, AccessEvent.occurred_at <= end)
            .order_by(AccessEvent.occurred_at.desc())
            .limit(250)
        )
    ).all()
    summary = as_dict(subject.get("summary"))
    records: list[dict[str, Any]] = []
    for event in events:
        records.extend(
            _incident_suppressed_read_payloads_from_event(
                event,
                subject_summary=summary,
                plates=plates,
                start=start,
                end=end,
                direction=direction,
                timezone_name=timezone_name,
            )
        )

    def sort_key(item: dict[str, Any]) -> datetime:
        return _datetime_from_agent_value(item.get("captured_at")) or datetime.min.replace(tzinfo=UTC)

    return sorted(records, key=sort_key, reverse=True)[:50]
def _incident_suppressed_read_payloads_from_event(
    event: Any,
    *,
    subject_summary: dict[str, Any],
    plates: list[str],
    start: datetime,
    end: datetime,
    direction: str,
    timezone_name: str,
) -> list[dict[str, Any]]:
    raw_payload = as_dict(getattr(event, "raw_payload", None))
    vehicle_session = as_dict(raw_payload.get(VEHICLE_SESSION_PAYLOAD_KEY))
    suppressed_reads = as_dict_list(vehicle_session.get("suppressed_reads"))
    if not suppressed_reads:
        return []

    source_person_id = str(getattr(event, "person_id", "") or "")
    source_vehicle_id = str(getattr(event, "vehicle_id", "") or "")
    source_registration = normalize_registration_number(str(getattr(event, "registration_number", "") or ""))
    source_direction = _enum_value(getattr(event, "direction", ""))
    source_decision = _enum_value(getattr(event, "decision", ""))
    source_event_id = str(getattr(event, "id", "") or vehicle_session.get("id") or "")
    source_occurred_at = _datetime_from_agent_value(getattr(event, "occurred_at", None))
    person = None
    vehicle = getattr(event, "vehicle", None)
    if vehicle is not None:
        person = getattr(vehicle, "owner", None)
    person_name = getattr(person, "display_name", None)
    records: list[dict[str, Any]] = []
    for read in suppressed_reads:
        if not isinstance(read, dict):
            continue
        captured_at = _datetime_from_agent_value(read.get("captured_at"))
        if not captured_at or captured_at < start or captured_at > end:
            continue
        if not _suppressed_read_matches_subject(
            event_person_id=source_person_id,
            event_vehicle_id=source_vehicle_id,
            event_registration=source_registration,
            read=read,
            subject_summary=subject_summary,
            plates=plates,
        ):
            continue
        inferred_direction = _direction_from_suppressed_read(read)
        if direction and inferred_direction and inferred_direction != direction:
            continue
        registration_number = _registration_from_suppressed_read(read) or source_registration
        detected_registration_number = normalize_registration_number(str(read.get("detected_registration_number") or ""))
        records.append(
            _compact_observation(
                {
                    "source_access_event_id": source_event_id,
                    "source_event_occurred_at": _agent_time_fields("occurred_at", source_occurred_at, timezone_name, display=False)["occurred_at"],
                    "source_event_direction": source_direction,
                    "source_event_decision": source_decision,
                    "source_event_registration_number": source_registration,
                    "person_id": source_person_id or subject_summary.get("person_id"),
                    "vehicle_id": source_vehicle_id or subject_summary.get("vehicle_id"),
                    "person": person_name or subject_summary.get("person"),
                    "registration_number": registration_number,
                    "detected_registration_number": detected_registration_number,
                    **_agent_time_fields("captured_at", captured_at, timezone_name),
                    "confidence": read.get("confidence"),
                    "source": read.get("source"),
                    "gate_state": read.get("gate_state"),
                    "inferred_direction": inferred_direction,
                    "reason": read.get("reason") or vehicle_session.get("last_suppressed_reason"),
                    "matched_by": read.get("matched_by") or vehicle_session.get("last_matched_by"),
                    "protect_event_ids": read.get("protect_event_ids") if isinstance(read.get("protect_event_ids"), list) else [],
                    "presence_evidence": _payload_summary(read.get("presence_evidence")),
                    "source_event": {
                        "id": source_event_id,
                        **_agent_time_fields("occurred_at", source_occurred_at, timezone_name, display=False),
                        "direction": source_direction,
                        "decision": source_decision,
                        "registration_number": source_registration,
                    },
                    "backfill_repairable": bool(
                        registration_number
                        and captured_at
                        and (subject_summary.get("person_id") or subject_summary.get("vehicle_id") or source_person_id or source_vehicle_id)
                    ),
                }
            )
        )
    return records
def _suppressed_read_matches_subject(
    *,
    event_person_id: str,
    event_vehicle_id: str,
    event_registration: str,
    read: dict[str, Any],
    subject_summary: dict[str, Any],
    plates: list[str],
) -> bool:
    subject_person_id = str(subject_summary.get("person_id") or "")
    subject_vehicle_id = str(subject_summary.get("vehicle_id") or "")
    if subject_vehicle_id and event_vehicle_id == subject_vehicle_id:
        return True
    if subject_person_id and event_person_id == subject_person_id:
        return True

    read_registration = _registration_from_suppressed_read(read)
    detected_registration = normalize_registration_number(str(read.get("detected_registration_number") or ""))
    candidates = [value for value in [read_registration, detected_registration, event_registration] if value]
    if plates:
        return any(
            _plate_match_score(candidate, expected) >= 0.78
            for candidate in candidates
            for expected in plates
        )
    return not (subject_person_id or subject_vehicle_id)
def _registration_from_suppressed_read(read: dict[str, Any]) -> str:
    return normalize_registration_number(
        str(read.get("registration_number") or read.get("detected_registration_number") or read.get("raw_value") or "")
    )
def _direction_from_suppressed_read(read: dict[str, Any]) -> str | None:
    state = str(read.get("gate_state") or "").strip().lower()
    if state == "closed":
        return "entry"
    if state in {"open", "opening", "closing"}:
        return "exit"
    return None
def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw) if str(raw) else None
def _incident_root_cause(
    *,
    found_iacs: bool,
    protect: dict[str, Any],
    traces: list[dict[str, Any]],
    suppressed_reads: list[dict[str, Any]],
    incident_type: str,
) -> dict[str, str]:
    if found_iacs:
        if incident_type == "notification_failure":
            return {"root_cause": "iacs_event_found_check_notification_diagnostics", "confidence": "high"}
        if incident_type in {"gate_failure", "garage_failure"}:
            return {"root_cause": "iacs_event_found_check_hardware_diagnostics", "confidence": "high"}
        if incident_type == "schedule_denial":
            return {"root_cause": "iacs_event_found_check_schedule_diagnostics", "confidence": "high"}
        return {"root_cause": "iacs_event_found", "confidence": "high"}
    if suppressed_reads:
        reason = str(suppressed_reads[0].get("reason") or "")
        if reason == "vehicle_session_already_active":
            return {"root_cause": "iacs_read_suppressed_as_active_vehicle_session", "confidence": "high"}
        return {"root_cause": "iacs_read_suppressed_before_access_event", "confidence": "high"}
    if not protect.get("available"):
        return {"root_cause": "protect_unavailable_partial_diagnosis", "confidence": "low"}
    if protect.get("matched_event"):
        webhook_traces = [trace for trace in traces if _trace_is_lpr_webhook(trace)]
        if not webhook_traces:
            return {"root_cause": "protect_lpr_detected_but_iacs_webhook_missing", "confidence": "high"}
        if any(int(trace.get("status_code") or 0) >= 400 for trace in webhook_traces):
            return {"root_cause": "iacs_webhook_received_error", "confidence": "high"}
        return {"root_cause": "iacs_webhook_seen_but_access_event_missing", "confidence": "medium"}
    if protect.get("events"):
        return {"root_cause": "protect_event_found_without_matching_lpr_candidate", "confidence": "medium"}
    return {"root_cause": "no_iacs_or_protect_event_found", "confidence": "low"}
def _trace_is_lpr_webhook(trace: dict[str, Any]) -> bool:
    path = str(trace.get("path") or "")
    name = str(trace.get("name") or "")
    return "/webhooks/ubiquiti/lpr" in path or "Webhook" in name
def _backfill_args_from_incident(
    *,
    subject: dict[str, Any],
    protect: dict[str, Any],
    suppressed_reads: list[dict[str, Any]],
    arguments: dict[str, Any],
    root_cause: str,
) -> dict[str, Any] | None:
    if root_cause in SUPPRESSED_READ_ROOT_CAUSES:
        return _backfill_args_from_suppressed_read(
            subject=subject,
            suppressed_reads=suppressed_reads,
            arguments=arguments,
            root_cause=root_cause,
        )
    if root_cause not in {
        "protect_lpr_detected_but_iacs_webhook_missing",
        "iacs_webhook_received_error",
        "iacs_webhook_seen_but_access_event_missing",
    }:
        return None
    event = as_dict(protect.get("matched_event"))
    if not event:
        return None
    candidate = as_dict(event.get("matched_candidate"))
    summary = as_dict(subject.get("summary"))
    summary_plates = as_list(summary.get("plates"))
    registration_number = normalize_registration_number(
        str(candidate.get("registration_number") or (summary_plates[0] if summary_plates else None) or arguments.get("registration_number") or "")
    )
    captured_at = candidate.get("captured_at") or event.get("start") or arguments.get("expected_time")
    if not registration_number or not captured_at:
        return None
    direction = _normalize(arguments.get("direction"))
    if direction not in {"entry", "exit", "denied"}:
        direction = "entry"
    decision = "denied" if direction == "denied" or not (summary.get("person_id") or summary.get("vehicle_id")) else "granted"
    return _compact_observation(
        {
            "evidence_kind": "protect",
            "protect_event_id": event.get("id"),
            "person_id": summary.get("person_id"),
            "vehicle_id": summary.get("vehicle_id"),
            "registration_number": registration_number,
            "captured_at": captured_at,
            "direction": direction,
            "decision": decision,
            "confidence": candidate.get("confidence"),
            "reason": f"Alfred incident remediation: {root_cause}",
        }
    )
def _backfill_args_from_suppressed_read(
    *,
    subject: dict[str, Any],
    suppressed_reads: list[dict[str, Any]],
    arguments: dict[str, Any],
    root_cause: str,
) -> dict[str, Any] | None:
    if not suppressed_reads:
        return None
    read = next((item for item in suppressed_reads if item.get("backfill_repairable")), suppressed_reads[0])
    summary = as_dict(subject.get("summary"))
    summary_plates = as_list(summary.get("plates"))
    registration_number = normalize_registration_number(
        str(read.get("registration_number") or (summary_plates[0] if summary_plates else None) or arguments.get("registration_number") or "")
    )
    captured_at = read.get("captured_at") or arguments.get("expected_time")
    source_access_event_id = read.get("source_access_event_id")
    if not registration_number or not captured_at or not source_access_event_id:
        return None
    direction = _normalize(arguments.get("direction"))
    if direction not in {"entry", "exit", "denied"}:
        direction = str(read.get("inferred_direction") or "entry")
    decision = "denied" if direction == "denied" or not (summary.get("person_id") or summary.get("vehicle_id") or read.get("person_id") or read.get("vehicle_id")) else "granted"
    return _compact_observation(
        {
            "evidence_kind": "suppressed_read",
            "source_access_event_id": source_access_event_id,
            "suppressed_read_captured_at": captured_at,
            "suppression_reason": read.get("reason"),
            "person_id": summary.get("person_id") or read.get("person_id"),
            "vehicle_id": summary.get("vehicle_id") or read.get("vehicle_id"),
            "registration_number": registration_number,
            "captured_at": captured_at,
            "direction": direction,
            "decision": decision,
            "confidence": read.get("confidence"),
            "reason": f"Alfred incident remediation: {root_cause}",
        }
    )
def _iacs_vs_protect_summary(
    found_iacs: bool,
    protect: dict[str, Any],
    traces: list[dict[str, Any]],
    *,
    suppressed_reads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    webhook_traces = [trace for trace in traces if _trace_is_lpr_webhook(trace)]
    found_suppressed = bool(suppressed_reads)
    return _compact_observation(
        {
            "iacs_access_event": "found" if found_iacs else "missing",
            "iacs_suppressed_read": "found" if found_suppressed else "not_found",
            "protect_event": "found" if protect.get("matched_event") else "not_found",
            "iacs_webhook_trace_count": len(webhook_traces),
            "comparison": (
                "IACS received a matching LPR read but suppressed it before finalizing an access event."
                if found_suppressed and not found_iacs
                else
                "Protect saw a matching LPR candidate but IACS has no access event."
                if protect.get("matched_event") and not found_iacs
                else "IACS has a matching access event."
                if found_iacs
                else "Neither IACS nor Protect produced matching durable evidence in the requested window."
            ),
        }
    )
def _incident_recommended_action(root: dict[str, Any], backfill_available: bool, protect: dict[str, Any]) -> dict[str, Any]:
    root_cause = str(root.get("root_cause") or "")
    if backfill_available:
        return {"type": "confirmed_backfill_available", "summary": "Confirm the prepared backfill to repair IACS history and presence."}
    if root_cause in SUPPRESSED_READ_ROOT_CAUSES:
        return {
            "type": "suppressed_read_review",
            "summary": "Review the suppressed-read evidence and only backfill if the resolved person, vehicle, and schedule allow it.",
        }
    if root_cause == "protect_lpr_detected_but_iacs_webhook_missing":
        return {
            "type": "external_alarm_manager_fix",
            "summary": "Fix UniFi Protect Alarm Manager delivery, then send a test.",
            "steps": [
                "Open UniFi Protect Alarm Manager and check the LPR alarm action webhook URL.",
                "Use the current IACS endpoint: /api/v1/webhooks/ubiquiti/lpr.",
                "Do not use retired /api/webhooks, /api/chat, or other non-versioned paths.",
                "Send a Protect Alarm Manager test and verify IACS Webhooks & API telemetry shows HTTP 202.",
            ],
        }
    if root_cause == "protect_unavailable_partial_diagnosis":
        return {"type": "restore_protect_diagnostics", "summary": str(protect.get("error") or "UniFi Protect was unavailable.")}
    if root_cause == "no_iacs_or_protect_event_found":
        return {"type": "camera_or_timing_check", "summary": "No durable IACS or Protect evidence was found; widen the time window or check camera/LPR zones."}
    return {"type": "diagnostic_follow_up", "summary": "Use the linked event diagnostics or external repair steps above."}
def _incident_diagnostic_chain(
    *,
    iacs_events: list[dict[str, Any]],
    suppressed_reads: list[dict[str, Any]],
    protect: dict[str, Any],
    traces: list[dict[str, Any]],
    audit_logs: list[dict[str, Any]],
    root: dict[str, Any],
    diagnostic: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    webhook_traces = [trace for trace in traces if _trace_is_lpr_webhook(trace)]
    matched_protect = protect.get("matched_event") if isinstance(protect.get("matched_event"), dict) else None
    first_suppressed = suppressed_reads[0] if suppressed_reads else {}
    access_detail = "A finalized IACS access event exists." if iacs_events else "No finalized IACS access event exists in the requested window."
    if first_suppressed:
        access_detail = (
            f"IACS received the read at {first_suppressed.get('captured_at_display') or first_suppressed.get('captured_at')} "
            f"but suppressed it as {first_suppressed.get('reason') or 'a vehicle-session duplicate'}."
        )
    gate_detail = _incident_gate_chain_detail(iacs_events, diagnostic, audit_logs)
    notification_detail = _incident_notification_chain_detail(iacs_events, diagnostic, first_suppressed)
    return [
        _compact_observation(
            {
                "stage": "camera_webhook",
                "status": "found" if first_suppressed or webhook_traces or matched_protect else "not_found",
                "detail": (
                    "IACS suppressed-read history contains the matching LPR read."
                    if first_suppressed
                    else "IACS webhook telemetry contains matching LPR traffic."
                    if webhook_traces
                    else "UniFi Protect has matching LPR evidence."
                    if matched_protect
                    else "No matching camera/webhook evidence was found."
                ),
            }
        ),
        _compact_observation(
            {
                "stage": "access_event",
                "status": "finalized" if iacs_events else "suppressed" if first_suppressed else "missing",
                "detail": access_detail,
            }
        ),
        _compact_observation(
            {
                "stage": "gate_command",
                "status": "not_attempted" if not iacs_events else gate_detail.get("status"),
                "detail": "No gate command ran because no access event was finalized." if not iacs_events else gate_detail.get("detail"),
            }
        ),
        _compact_observation(
            {
                "stage": "notification",
                "status": "not_triggered" if not iacs_events else notification_detail.get("status"),
                "detail": (
                    "Notifications never ran because notification workflows are evaluated after finalized access events or explicit notification triggers."
                    if not iacs_events
                    else notification_detail.get("detail")
                ),
            }
        ),
        _compact_observation(
            {
                "stage": "root_cause",
                "status": root.get("confidence"),
                "detail": root.get("root_cause"),
            }
        ),
    ]
def _incident_gate_chain_detail(
    iacs_events: list[dict[str, Any]],
    diagnostic: dict[str, Any] | None,
    audit_logs: list[dict[str, Any]],
) -> dict[str, str]:
    gate = diagnostic.get("gate") if isinstance(diagnostic, dict) and isinstance(diagnostic.get("gate"), dict) else {}
    if gate:
        command = gate.get("gate_command")
        if isinstance(command, dict):
            return {"status": str(command.get("status") or "recorded"), "detail": str(gate.get("outcome_reason") or "Gate command diagnostics were recorded.")}
        return {"status": "not_attempted", "detail": str(gate.get("outcome_reason") or "No gate command span was recorded.")}
    gate_audits = [row for row in audit_logs if "gate" in str(row.get("action") or "").lower()]
    if gate_audits:
        return {"status": str(gate_audits[0].get("outcome") or "recorded"), "detail": str(gate_audits[0].get("action") or "Gate audit record found.")}
    if iacs_events:
        return {"status": "not_found", "detail": "No gate command evidence was found for the finalized event."}
    return {"status": "not_attempted", "detail": "No access event was finalized."}
def _incident_notification_chain_detail(
    iacs_events: list[dict[str, Any]],
    diagnostic: dict[str, Any] | None,
    first_suppressed: dict[str, Any],
) -> dict[str, str]:
    notifications = as_dict(diagnostic.get("notifications") if diagnostic else None)
    summary = str(notifications.get("summary") or "").strip()
    deliveries = as_list(notifications.get("delivery_records"))
    if deliveries:
        return {"status": "recorded", "detail": summary or "Notification delivery records were found."}
    if summary:
        return {"status": "not_recorded", "detail": summary}
    if iacs_events:
        return {"status": "unknown", "detail": "No notification diagnostic summary was available."}
    if first_suppressed:
        return {"status": "not_triggered", "detail": "The read stopped at vehicle-session suppression, before notification context creation."}
    return {"status": "not_triggered", "detail": "No matching event reached notification evaluation."}
def _incident_timeline(
    *,
    iacs_events: list[dict[str, Any]],
    protect: dict[str, Any],
    traces: list[dict[str, Any]],
    gate_observations: list[dict[str, Any]],
    audit_logs: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    suppressed_reads: list[dict[str, Any]],
    timezone_name: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in iacs_events:
        items.append({"time": event.get("occurred_at"), "source": "iacs_access_event", "summary": f"{event.get('direction')} {event.get('decision')} {event.get('registration_number')}"})
    for read in suppressed_reads:
        items.append(
            {
                "time": read.get("captured_at"),
                "source": "iacs_suppressed_read",
                "summary": f"suppressed {read.get('registration_number')} as {read.get('reason') or 'suppressed_read'}",
                "source_access_event_id": read.get("source_access_event_id"),
            }
        )
    for event in protect.get("events") or []:
        items.append({"time": event.get("start"), "source": "unifi_protect", "summary": f"{event.get('camera_name')} {event.get('smart_detect_types')}"})
    for trace in traces:
        items.append({"time": trace.get("started_at"), "source": f"telemetry:{trace.get('category')}", "summary": trace.get("summary") or trace.get("name")})
    for observation in gate_observations:
        items.append({"time": observation.get("observed_at"), "source": "home_assistant_gate", "summary": f"{observation.get('gate_name') or observation.get('gate_entity_id')} {observation.get('state')}"})
    for anomaly in anomalies:
        items.append({"time": anomaly.get("created_at"), "source": "anomaly", "summary": anomaly.get("message")})
    for row in audit_logs:
        items.append({"time": row.get("timestamp"), "source": f"audit:{row.get('category')}", "summary": row.get("action")})

    def sort_key(item: dict[str, Any]) -> datetime:
        parsed = _datetime_from_agent_value(item.get("time"))
        return parsed or datetime.max.replace(tzinfo=UTC)

    return [
        _compact_observation(
            {
                **item,
                "time_display": _agent_datetime_display(sort_key(item), timezone_name) if sort_key(item).year < 9999 else None,
            }
        )
        for item in sorted(items, key=sort_key)[:80]
    ]
async def _backfill_candidate(session, arguments: dict[str, Any], config: Any) -> dict[str, Any]:
    timezone_name = config.site_timezone
    subject = await _resolve_incident_subject(session, arguments)
    summary = as_dict(subject.get("summary"))
    plates = _incident_candidate_plates(subject, arguments)
    evidence_kind = _normalize(arguments.get("evidence_kind") or "protect")
    if evidence_kind not in {"protect", "suppressed_read"}:
        return {"error": "evidence_kind must be protect or suppressed_read."}
    protect_event_id = str(arguments.get("protect_event_id") or "").strip()
    track_candidate: dict[str, Any] | None = None
    protect_event: dict[str, Any] | None = None
    source_event: AccessEvent | None = None
    suppressed_read: dict[str, Any] | None = None
    if evidence_kind == "suppressed_read":
        source_event_id = _uuid_from_value(arguments.get("source_access_event_id"))
        if not source_event_id:
            return {"error": "source_access_event_id is required for suppressed-read backfill evidence."}
        source_event = await session.scalar(
            select(AccessEvent)
            .options(*_access_event_load_options())
            .where(AccessEvent.id == source_event_id)
        )
        if not source_event:
            return {"error": "Source access event containing suppressed-read evidence was not found."}
        suppressed_read = _suppressed_read_from_source_event(source_event, arguments, timezone_name)
        if not suppressed_read:
            return {"error": "No matching suppressed read was found on the source access event."}
        if source_event.vehicle and str(source_event.vehicle_id) != str(summary.get("vehicle_id") or ""):
            plates = list(dict.fromkeys([*plates, source_event.vehicle.registration_number]))
    elif protect_event_id:
        try:
            track = await get_unifi_protect_service().event_lpr_track(protect_event_id)
            protect_event = _protect_event_payload(track.get("event") or {"id": protect_event_id}, timezone_name)
            observations = [
                _serialize_lpr_timing_observation(observation, timezone_name, include_payload_path=True)
                for observation in track.get("observations", [])
                if isinstance(observation, dict)
            ]
            track_candidate = _best_track_candidate(observations, plates) or (observations[0] if observations else None)
        except UnifiProtectError as exc:
            return {"error": str(exc)}

    registration_number = normalize_registration_number(
        str(
            arguments.get("registration_number")
            or (suppressed_read or {}).get("registration_number")
            or (suppressed_read or {}).get("detected_registration_number")
            or (track_candidate or {}).get("registration_number")
            or (plates[0] if plates else "")
        )
    )
    if not registration_number:
        return {"error": "registration_number is required for an access event backfill."}

    captured_at = (
        _parse_incident_datetime(arguments.get("captured_at") or arguments.get("expected_time"), timezone_name, str(arguments.get("day") or "today"))
        or _datetime_from_agent_value(arguments.get("suppressed_read_captured_at"))
        or _datetime_from_agent_value((suppressed_read or {}).get("captured_at"))
        or _datetime_from_agent_value((track_candidate or {}).get("captured_at"))
        or _datetime_from_agent_value((protect_event or {}).get("start"))
    )
    if not captured_at:
        return {"error": "captured_at or durable Protect track time is required for an access event backfill."}
    captured_at = captured_at.astimezone(UTC)
    gate_observation = await _nearest_gate_observation(session, captured_at, timezone_name)
    if evidence_kind == "suppressed_read" and not gate_observation and suppressed_read:
        gate_state = str(suppressed_read.get("gate_state") or "").strip()
        if gate_state:
            gate_observation = {"state": gate_state, "source": "suppressed_read", "observed_at": _agent_datetime_iso(captured_at, timezone_name)}
    direction = _normalize(arguments.get("direction"))
    if direction not in {"entry", "exit", "denied"}:
        direction = _direction_from_suppressed_read(suppressed_read or {}) or _direction_from_gate_observation(gate_observation) or "entry"
    source_vehicle = source_event.vehicle if source_event and source_event.vehicle else None
    source_person = source_vehicle.owner if source_vehicle and source_vehicle.owner else None
    vehicle_obj = subject.get("vehicle") if isinstance(subject.get("vehicle"), Vehicle) else source_vehicle
    person_obj = subject.get("person") if isinstance(subject.get("person"), Person) else source_person
    person_id = _uuid_from_value(summary.get("person_id")) or (person_obj.id if person_obj else None)
    vehicle_id = _uuid_from_value(summary.get("vehicle_id")) or (vehicle_obj.id if vehicle_obj else None)
    decision = _normalize(arguments.get("decision"))
    if decision not in {"granted", "denied"}:
        decision = "denied" if direction == "denied" or not (person_id or vehicle_id) else "granted"
    if decision == "granted" and not (summary.get("person_id") or summary.get("vehicle_id")):
        if not (person_id or vehicle_id):
            return {"error": "A granted backfill requires a resolved active person or vehicle."}
    schedule_evaluation = None
    downgraded_reason = None
    if decision == "granted":
        if person_obj is not None and not person_obj.is_active:
            decision = "denied"
            downgraded_reason = f"{person_obj.display_name} is inactive, so the repair was prepared as denied."
        elif vehicle_obj is not None and not vehicle_obj.is_active:
            decision = "denied"
            downgraded_reason = f"{vehicle_obj.registration_number} is inactive, so the repair was prepared as denied."
        elif vehicle_obj is not None:
            evaluation = await evaluate_vehicle_schedule(
                session,
                vehicle_obj,
                captured_at,
                timezone_name=timezone_name,
                default_policy=getattr(config, "schedule_default_policy", "deny"),
            )
            schedule_evaluation = _schedule_evaluation_payload(evaluation, f"vehicle {vehicle_obj.registration_number}", timezone_name)
            if not evaluation.allowed:
                decision = "denied"
                downgraded_reason = evaluation.reason or "The resolved vehicle was outside schedule, so the repair was prepared as denied."
        elif person_obj is not None:
            evaluation = await evaluate_person_schedule(
                session,
                person_obj,
                captured_at,
                timezone_name=timezone_name,
                default_policy=getattr(config, "schedule_default_policy", "deny"),
            )
            schedule_evaluation = _schedule_evaluation_payload(evaluation, person_obj.display_name, timezone_name)
            if not evaluation.allowed:
                decision = "denied"
                downgraded_reason = evaluation.reason or "The resolved person was outside schedule, so the repair was prepared as denied."
    return {
        "label": summary.get("label") or registration_number,
        "person_id": person_id,
        "vehicle_id": vehicle_id,
        "registration_number": registration_number,
        "captured_at": captured_at,
        "direction": direction,
        "decision": decision,
        "confidence": _confidence_ratio(arguments.get("confidence") or (suppressed_read or {}).get("confidence") or (track_candidate or {}).get("confidence") or 0.99),
        "source": "iacs_suppressed_read_backfill" if evidence_kind == "suppressed_read" else "unifi_protect_backfill" if protect_event_id else "alfred_backfill",
        "evidence_kind": evidence_kind,
        "protect_event_id": protect_event_id or None,
        "source_access_event_id": source_event.id if source_event else None,
        "suppression_reason": (suppressed_read or {}).get("reason") or arguments.get("suppression_reason"),
        "camera_id": (protect_event or {}).get("camera_id") or (track_candidate or {}).get("camera_id"),
        "camera_name": (protect_event or {}).get("camera_name") or (track_candidate or {}).get("camera_name"),
        "track_candidate": track_candidate,
        "suppressed_read_evidence": _compact_observation(suppressed_read) if suppressed_read else None,
        "schedule_evaluation": schedule_evaluation,
        "gate_observation": gate_observation,
        "reason": str(downgraded_reason or arguments.get("reason") or "Backfilled by Alfred from incident investigation").strip(),
    }
async def _nearest_gate_observation(session, captured_at: datetime, timezone_name: str) -> dict[str, Any] | None:
    observations = (
        await session.scalars(
            select(GateStateObservation)
            .where(
                GateStateObservation.observed_at >= captured_at - timedelta(minutes=5),
                GateStateObservation.observed_at <= captured_at + timedelta(minutes=5),
            )
            .order_by(GateStateObservation.observed_at)
            .limit(30)
        )
    ).all()
    if not observations:
        return None
    nearest = min(
        observations,
        key=lambda observation: abs((observation.observed_at.astimezone(UTC) - captured_at).total_seconds()),
    )
    return _gate_observation_payload(nearest, timezone_name)
def _suppressed_read_from_source_event(event: AccessEvent, arguments: dict[str, Any], timezone_name: str) -> dict[str, Any] | None:
    raw_payload = as_dict(event.raw_payload)
    vehicle_session = as_dict(raw_payload.get(VEHICLE_SESSION_PAYLOAD_KEY))
    suppressed_reads = as_dict_list(vehicle_session.get("suppressed_reads"))
    if not suppressed_reads:
        return None
    target_time = (
        _parse_incident_datetime(
            arguments.get("suppressed_read_captured_at") or arguments.get("captured_at") or arguments.get("expected_time"),
            timezone_name,
            str(arguments.get("day") or "today"),
        )
        or _datetime_from_agent_value(arguments.get("suppressed_read_captured_at"))
        or _datetime_from_agent_value(arguments.get("captured_at"))
    )
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    suppression_reason = str(arguments.get("suppression_reason") or "").strip()
    best: tuple[float, dict[str, Any]] | None = None
    for read in suppressed_reads:
        if not isinstance(read, dict):
            continue
        read_at = _datetime_from_agent_value(read.get("captured_at"))
        if target_time and read_at and abs((read_at - target_time).total_seconds()) > 5:
            continue
        if suppression_reason and str(read.get("reason") or "") != suppression_reason:
            continue
        read_registration = _registration_from_suppressed_read(read)
        if registration_number and _plate_match_score(read_registration, registration_number) < 0.78:
            continue
        time_score = 1.0
        if target_time and read_at:
            time_score = max(0.0, 1.0 - abs((read_at - target_time).total_seconds()) / 5)
        plate_score = _plate_match_score(read_registration, registration_number) if registration_number else 1.0
        score = (plate_score * 0.7) + (time_score * 0.3)
        if best is None or score > best[0]:
            best = (score, read)
    return dict(best[1]) if best else None
def _direction_from_gate_observation(observation: dict[str, Any] | None) -> str | None:
    if not observation:
        return None
    state = str(observation.get("state") or "").lower()
    if state == "closed":
        return "entry"
    if state in {"open", "opening", "closing"}:
        return "exit"
    return None
def _confidence_ratio(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.99
    if confidence > 1:
        confidence = confidence / 100
    return max(0.0, min(1.0, confidence))
def _backfill_candidate_payload(candidate: dict[str, Any], timezone_name: str) -> dict[str, Any]:
    return _compact_observation(
        {
            "label": candidate.get("label"),
            "person_id": str(candidate.get("person_id")) if candidate.get("person_id") else None,
            "vehicle_id": str(candidate.get("vehicle_id")) if candidate.get("vehicle_id") else None,
            "registration_number": candidate.get("registration_number"),
            **_agent_time_fields("captured_at", candidate["captured_at"], timezone_name),
            "direction": candidate.get("direction"),
            "decision": candidate.get("decision"),
            "confidence": candidate.get("confidence"),
            "source": candidate.get("source"),
            "evidence_kind": candidate.get("evidence_kind"),
            "protect_event_id": candidate.get("protect_event_id"),
            "source_access_event_id": str(candidate.get("source_access_event_id")) if candidate.get("source_access_event_id") else None,
            "suppression_reason": candidate.get("suppression_reason"),
            "camera_name": candidate.get("camera_name"),
            "suppressed_read_evidence": candidate.get("suppressed_read_evidence"),
            "schedule_evaluation": candidate.get("schedule_evaluation"),
            "gate_observation": candidate.get("gate_observation"),
            "reason": candidate.get("reason"),
        }
    )
async def investigate_access_incident(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    incident_type = _normalize(arguments.get("incident_type") or "auto") or "auto"
    start, end, expected_at = _incident_window(arguments, config.site_timezone)
    direction_filter = _normalize(arguments.get("direction"))
    if direction_filter not in {"entry", "exit", "denied"}:
        direction_filter = ""

    async with AsyncSessionLocal() as session:
        subject = await _resolve_incident_subject(session, arguments)
        plates = _incident_candidate_plates(subject, arguments)
        iacs_events = await _incident_iacs_events(
            session,
            subject=subject,
            plates=plates,
            start=start,
            end=end,
            direction=direction_filter,
        )
        traces = await _incident_telemetry_traces(
            session,
            start=start,
            end=end,
            plates=plates,
            timezone_name=config.site_timezone,
        )
        audit_logs = await _incident_audit_logs(session, start=start, end=end)
        gate_observations = await _incident_gate_observations(session, start=start, end=end, timezone_name=config.site_timezone)
        anomalies = await _incident_anomalies(session, start=start, end=end, plates=plates, timezone_name=config.site_timezone)
        schedules = await _incident_schedule_diagnostics(
            session,
            subject=subject,
            checked_at=(expected_at or start + ((end - start) / 2)).astimezone(UTC),
            timezone_name=config.site_timezone,
            default_policy=config.schedule_default_policy,
        )
        notification_summary = await _incident_notification_summary(session, incident_type=incident_type)
        suppressed_reads = await _incident_suppressed_reads(
            session,
            subject=subject,
            plates=plates,
            start=start,
            end=end,
            direction=direction_filter,
            timezone_name=config.site_timezone,
            idle_seconds=_runtime_vehicle_session_idle_seconds(config),
        )

    diagnostic = None
    if iacs_events:
        diagnostic = await diagnose_access_event(
            {
                "access_event_id": iacs_events[0]["id"],
                "span_limit": 20,
                "summarize_payload": True,
            }
        )

    protect = await _query_protect_events_for_incident(
        start=start,
        end=end,
        timezone_name=config.site_timezone,
        plates=plates,
        camera_id=str(arguments.get("camera_id") or "").strip() or None,
        camera_name=str(arguments.get("camera_name") or "").strip() or None,
        smart_detect_type=str(arguments.get("smart_detect_type") or "").strip() or None,
        include_tracks=True,
    )
    root = _incident_root_cause(
        found_iacs=bool(iacs_events),
        protect=protect,
        traces=traces,
        suppressed_reads=suppressed_reads,
        incident_type=incident_type,
    )
    backfill_args = _backfill_args_from_incident(
        subject=subject,
        protect=protect,
        suppressed_reads=suppressed_reads,
        arguments=arguments,
        root_cause=str(root.get("root_cause") or ""),
    )

    if bool(arguments.get("confirm")) and backfill_args:
        confirmed_args = {**backfill_args, "confirm": True}
        return await backfill_access_event_from_protect(confirmed_args)

    timeline = _incident_timeline(
        iacs_events=iacs_events,
        protect=protect,
        traces=traces,
        gate_observations=gate_observations,
        audit_logs=audit_logs,
        anomalies=anomalies,
        suppressed_reads=suppressed_reads,
        timezone_name=config.site_timezone,
    )
    diagnostic_chain = _incident_diagnostic_chain(
        iacs_events=iacs_events,
        suppressed_reads=suppressed_reads,
        protect=protect,
        traces=traces,
        audit_logs=audit_logs,
        root=root,
        diagnostic=diagnostic,
    )
    result = {
        "found_iacs_event": bool(iacs_events),
        "found_iacs_suppressed_read": bool(suppressed_reads),
        "found_protect_event": bool(protect.get("matched_event") or protect.get("events")),
        "root_cause": root.get("root_cause"),
        "confidence": root.get("confidence"),
        "timeline": timeline,
        "diagnostic_chain": diagnostic_chain,
        "subject": subject.get("summary"),
        "window": {
            "start": _agent_datetime_iso(start, config.site_timezone),
            "end": _agent_datetime_iso(end, config.site_timezone),
            "expected_time": _agent_datetime_iso(expected_at, config.site_timezone) if expected_at else None,
            "timezone": config.site_timezone,
        },
        "iacs": {
            "events": iacs_events,
            "event_count": len(iacs_events),
            "suppressed_reads": suppressed_reads,
            "suppressed_read_count": len(suppressed_reads),
            "telemetry_traces": traces,
            "gate_observations": gate_observations,
            "anomalies": anomalies,
            "audit_logs": audit_logs,
            "schedules": schedules,
            "notifications": notification_summary,
            "diagnostic": diagnostic,
        },
        "protect": protect,
        "iacs_vs_protect": _iacs_vs_protect_summary(bool(iacs_events), protect, traces, suppressed_reads=suppressed_reads),
        "recommended_action": _incident_recommended_action(root, bool(backfill_args), protect),
        "requires_confirmation": bool(backfill_args),
        "confirmation_field": "confirm" if backfill_args else None,
        "backfill_arguments": backfill_args,
    }
    if backfill_args:
        result["target"] = subject.get("summary", {}).get("label") or backfill_args.get("registration_number") or "missing access event"
        if backfill_args.get("evidence_kind") == "suppressed_read":
            result["detail"] = (
                "I found durable IACS suppressed-read evidence without a finalized access event. "
                "Confirm to backfill the access event and update presence only; no gate, garage, automation, or normal arrival notifications will be fired."
            )
        else:
            result["detail"] = (
                "I found durable UniFi Protect LPR evidence without a matching IACS access event. "
                "Confirm to backfill the access event and update presence only; no gate, garage, automation, or normal arrival notifications will be fired."
            )
    return _compact_observation(result)
async def query_unifi_protect_events(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    limit = _bounded_int(arguments.get("limit"), default=25, minimum=1, maximum=100)
    if arguments.get("start") or arguments.get("end"):
        start = _parse_incident_datetime(arguments.get("start"), config.site_timezone, str(arguments.get("day") or "today"))
        end = _parse_incident_datetime(arguments.get("end"), config.site_timezone, str(arguments.get("day") or "today"))
        if not start or not end:
            return {"available": False, "error": "start and end must be ISO datetimes or local times."}
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
    else:
        start_utc, end_utc, _ = _incident_window(arguments, config.site_timezone)

    plates = []
    registration_number = normalize_registration_number(str(arguments.get("registration_number") or ""))
    if registration_number:
        plates.append(registration_number)
    result = await _query_protect_events_for_incident(
        start=start_utc,
        end=end_utc,
        timezone_name=config.site_timezone,
        plates=plates,
        camera_id=str(arguments.get("camera_id") or "").strip() or None,
        camera_name=str(arguments.get("camera_name") or "").strip() or None,
        smart_detect_type=str(arguments.get("smart_detect_type") or "").strip() or None,
        include_tracks=bool(arguments.get("include_tracks") or registration_number),
        limit=limit,
    )
    return _compact_observation(result)
async def backfill_access_event_from_protect(arguments: dict[str, Any]) -> dict[str, Any]:
    context = get_chat_tool_context()
    if str(context.get("user_role") or "").lower() != "admin":
        return {"backfilled": False, "error": "Admin access is required to backfill access events."}

    config = await get_runtime_config()
    async with AsyncSessionLocal() as session:
        candidate = await _backfill_candidate(session, arguments, config)
        if isinstance(candidate, dict) and candidate.get("error"):
            return {"backfilled": False, **candidate}
        if not bool(arguments.get("confirm")):
            return {
                "backfilled": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": candidate.get("label") or candidate.get("registration_number") or "access event",
                "detail": (
                    f"Backfill {candidate.get('direction')} {candidate.get('decision')} event for "
                    f"{candidate.get('label') or candidate.get('registration_number')} at "
                    f"{_agent_datetime_display(candidate['captured_at'], config.site_timezone)}? "
                    "This updates IACS event history and presence only. It will not replay gate, garage, automation, or notification actions."
                ),
                "candidate": _backfill_candidate_payload(candidate, config.site_timezone),
            }

        duplicate = await session.scalar(
            select(AccessEvent)
            .where(
                AccessEvent.registration_number == candidate["registration_number"],
                AccessEvent.occurred_at >= candidate["captured_at"] - timedelta(seconds=60),
                AccessEvent.occurred_at <= candidate["captured_at"] + timedelta(seconds=60),
            )
            .order_by(AccessEvent.occurred_at.desc())
            .limit(1)
        )
        if duplicate:
            return {
                "backfilled": False,
                "already_exists": True,
                "access_event_id": str(duplicate.id),
                "detail": "A matching IACS access event already exists within 60 seconds, so I did not create a duplicate.",
            }

        trace = telemetry.start_trace(
            "Alfred Access Event Backfill",
            category=TELEMETRY_CATEGORY_ALFRED,
            actor="Alfred_AI",
            source=candidate.get("source"),
            registration_number=candidate["registration_number"],
            context={
                "protect_event_id": candidate.get("protect_event_id"),
                "evidence_kind": candidate.get("evidence_kind"),
                "source_access_event_id": candidate.get("source_access_event_id"),
                "suppression_reason": candidate.get("suppression_reason"),
                "reason": candidate.get("reason"),
                "direction": candidate["direction"],
                "decision": candidate["decision"],
            },
        )
        trace.record_span(
            "Backfill evidence selected",
            started_at=datetime.now(tz=UTC),
            category=TELEMETRY_CATEGORY_ALFRED,
            attributes={
                "source": candidate.get("source"),
                "evidence_kind": candidate.get("evidence_kind"),
                "protect_event_id": candidate.get("protect_event_id"),
                "source_access_event_id": candidate.get("source_access_event_id"),
            },
            output_payload=_backfill_candidate_payload(candidate, config.site_timezone),
        )
        event = AccessEvent(
            vehicle_id=candidate.get("vehicle_id"),
            person_id=candidate.get("person_id"),
            registration_number=candidate["registration_number"],
            direction=AccessDirection(candidate["direction"]),
            decision=AccessDecision(candidate["decision"]),
            confidence=candidate["confidence"],
            source=candidate.get("source") or "alfred_backfill",
            occurred_at=candidate["captured_at"],
            timing_classification=TimingClassification.UNKNOWN,
            raw_payload={
                "backfill": {
                    "source": candidate.get("source"),
                    "evidence_kind": candidate.get("evidence_kind"),
                    "reason": candidate.get("reason"),
                    "source_access_event_id": candidate.get("source_access_event_id"),
                    "suppression_reason": candidate.get("suppression_reason"),
                    "created_by": "Alfred_AI",
                    "created_by_user_id": str(context.get("user_id") or "") or None,
                    "actions_replayed": False,
                },
                "protect_evidence": {
                    "event_id": candidate.get("protect_event_id"),
                    "camera_id": candidate.get("camera_id"),
                    "camera_name": candidate.get("camera_name"),
                    "captured_at": candidate["captured_at"].isoformat(),
                    "confidence": candidate["confidence"],
                    "track_candidate": candidate.get("track_candidate"),
                },
                "suppressed_read_evidence": candidate.get("suppressed_read_evidence"),
                "direction_resolution": {
                    "source": "alfred_backfill",
                    "gate_observation": candidate.get("gate_observation"),
                },
                "schedule": candidate.get("schedule_evaluation"),
                "telemetry": {"trace_id": trace.trace_id},
            },
        )
        session.add(event)
        await session.flush()

        presence_updated = False
        if event.decision == AccessDecision.GRANTED and event.person_id and event.direction in {AccessDirection.ENTRY, AccessDirection.EXIT}:
            presence = await session.get(Presence, event.person_id)
            if not presence:
                presence = Presence(person_id=event.person_id)
                session.add(presence)
            presence.state = PresenceState.PRESENT if event.direction == AccessDirection.ENTRY else PresenceState.EXITED
            presence.last_event_id = event.id
            presence.last_changed_at = event.occurred_at
            presence_updated = True

        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_ALFRED,
            action="access_event.backfilled",
            actor="Alfred_AI",
            actor_user_id=context.get("user_id"),
            target_entity="AccessEvent",
            target_id=event.id,
            target_label=event.registration_number,
            metadata={
                "protect_event_id": candidate.get("protect_event_id"),
                "evidence_kind": candidate.get("evidence_kind"),
                "source_access_event_id": candidate.get("source_access_event_id"),
                "suppression_reason": candidate.get("suppression_reason"),
                "camera_name": candidate.get("camera_name"),
                "direction": event.direction.value,
                "decision": event.decision.value,
                "presence_updated": presence_updated,
                "reason": candidate.get("reason"),
            },
            trace_id=trace.trace_id,
        )
        await session.commit()
        await session.refresh(event)

    trace.finish(
        status="ok",
        summary=f"Backfilled {event.direction.value} for plate {event.registration_number}",
        access_event_id=event.id,
        context={"event_id": str(event.id), "presence_updated": presence_updated},
    )
    await telemetry.flush()
    await event_bus.publish(
        "access_event.finalized",
        {
            "event_id": str(event.id),
            "access_event_id": str(event.id),
            "person_id": str(event.person_id) if event.person_id else None,
            "vehicle_id": str(event.vehicle_id) if event.vehicle_id else None,
            "registration_number": event.registration_number,
            "direction": event.direction.value,
            "decision": event.decision.value,
            "confidence": event.confidence,
            "source": event.source,
            "occurred_at": event.occurred_at.isoformat(),
            "event_type": "access_event.finalized",
            "timing_classification": event.timing_classification.value,
            "anomaly_count": 0,
            "backfilled": True,
            "skip_automation_actions": True,
            "skip_notification_actions": True,
        },
    )
    return {
        "backfilled": True,
        "access_event_id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value,
        "occurred_at": _agent_datetime_iso(event.occurred_at, config.site_timezone),
        "occurred_at_display": _agent_datetime_display(event.occurred_at, config.site_timezone),
        "presence_updated": presence_updated,
        "telemetry_trace_id": trace.trace_id,
        "evidence_kind": candidate.get("evidence_kind"),
        "source_access_event_id": str(candidate.get("source_access_event_id")) if candidate.get("source_access_event_id") else None,
    }
async def test_unifi_alarm_webhook(arguments: dict[str, Any]) -> dict[str, Any]:
    trigger_id = str(arguments.get("trigger_id") or "").strip()
    if not trigger_id:
        return {"sent": False, "error": "trigger_id is required."}
    if not bool(arguments.get("confirm")):
        return {
            "sent": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": trigger_id,
            "detail": "Send a UniFi Protect Alarm Manager webhook test for this trigger?",
        }

    before = datetime.now(tz=UTC)
    try:
        sent = await get_unifi_protect_service().send_alarm_webhook_test(trigger_id)
    except UnifiProtectError as exc:
        return {"sent": False, "error": str(exc)}

    await asyncio.sleep(1)
    await telemetry.flush()
    async with AsyncSessionLocal() as session:
        traces = (
            await session.scalars(
                select(TelemetryTrace)
                .where(
                    TelemetryTrace.category == TELEMETRY_CATEGORY_WEBHOOKS_API,
                    TelemetryTrace.started_at >= before,
                )
                .order_by(TelemetryTrace.started_at.desc())
                .limit(5)
            )
        ).all()
    return {
        "sent": True,
        "trigger_id": trigger_id,
        "protect_result": _payload_summary(sent),
        "verified_iacs_webhook_trace": bool(traces),
        "recent_webhook_traces": [_incident_trace_payload(trace, DEFAULT_AGENT_TIMEZONE) for trace in traces],
    }
