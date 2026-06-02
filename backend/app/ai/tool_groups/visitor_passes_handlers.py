"""Visitor Pass Alfred tool handlers."""
# ruff: noqa: F403, F405

from __future__ import annotations

from typing import Any

from app.ai.tool_groups._shared import *


def _normalize_name_for_similarity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _fuzzy_visitor_pass_name_matches(search: str, candidates: list[VisitorPass]) -> list[VisitorPass]:
    needle = _normalize_name_for_similarity(search)
    if not needle:
        return []
    scored: list[tuple[float, VisitorPass]] = []
    for pass_ in candidates:
        candidate = _normalize_name_for_similarity(pass_.visitor_name)
        if not candidate:
            continue
        score = SequenceMatcher(None, needle, candidate).ratio()
        if needle in candidate or candidate in needle:
            score = max(score, 0.9)
        if score >= 0.72:
            scored.append((score, pass_))
    scored.sort(key=lambda item: (item[0], item[1].expected_time), reverse=True)
    return [pass_ for _score, pass_ in scored]


def _visitor_pass_answer_artifacts(
    arguments: dict[str, Any],
    records: list[dict[str, Any]],
    timezone_name: str,
) -> list[dict[str, Any]]:
    subject = _preferred_subject_label(arguments, records[0].get("visitor_name") if records else "the visitor")
    if not records:
        return [
            artifact_payload(
                domain="visitor_passes",
                answer_type="visitor_pass_empty",
                subject_label=subject,
                primary_fact={
                    "id": "visitor_pass.match_count",
                    "label": "Matching visitor passes",
                    "value": 0,
                    "display_value": "0",
                    "kind": "count",
                    "must_appear": False,
                },
                canonical_text=f"I couldn't find a matching visitor pass for {subject}.",
            )
        ]
    record = records[0]
    subject = _preferred_subject_label(arguments, record.get("visitor_name") or subject)
    arrival_display = _display_iso_datetime(record.get("arrival_time"), timezone_name)
    departure_display = _display_iso_datetime(record.get("departure_time"), timezone_name)
    duration = str(record.get("duration_human") or "").strip()
    if departure_display:
        answer_type = "visitor_departure"
        fact_id = "visitor.departure_time"
        label = "Visitor departure"
        display = _compact_time_label(departure_display)
        canonical_text = f"{subject} left at {display}."
    elif arrival_display:
        answer_type = "visitor_arrival"
        fact_id = "visitor.arrival_time"
        label = "Visitor arrival"
        display = _compact_time_label(arrival_display)
        canonical_text = f"{subject} arrived at {display}."
    elif duration:
        answer_type = "visitor_duration"
        fact_id = "visitor.duration"
        label = "Visitor duration"
        display = duration
        canonical_text = f"{subject} was on site for {display}."
    else:
        answer_type = "visitor_pass"
        fact_id = "visitor.status"
        label = "Visitor pass status"
        display = str(record.get("status") or "visitor pass")
        canonical_text = f"{subject} has a {display} visitor pass."
    return [
        artifact_payload(
            domain="visitor_passes",
            answer_type=answer_type,
            subject_label=subject,
            primary_fact={
                "id": fact_id,
                "label": label,
                "value": record.get("departure_time") or record.get("arrival_time") or record.get("duration_on_site_seconds") or record.get("status"),
                "display_value": display,
                "kind": "timestamp" if fact_id.endswith("_time") else "duration" if fact_id.endswith("duration") else "status",
                "source": "visitor_passes",
                "must_appear": True,
            },
            source_records=[
                {
                    "visitor_pass_id": record.get("id"),
                    "arrival_time": arrival_display,
                    "departure_time": departure_display,
                    "status": record.get("status"),
                    "creation_source": record.get("creation_source"),
                }
            ],
            display={"voice": "natural_concise", "no_timezone_labels": True},
            canonical_text=canonical_text,
        )
    ]


def _visitor_pass_statuses_from_arguments(arguments: dict[str, Any]) -> list[VisitorPassStatus] | None:
    raw = arguments.get("statuses")
    if raw is None and arguments.get("status"):
        raw = [arguments.get("status")]
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",")]
    if not isinstance(raw, list):
        return None
    statuses: list[VisitorPassStatus] = []
    for item in raw:
        normalized = str(item or "").strip().lower()
        try:
            status = VisitorPassStatus(normalized)
        except ValueError:
            continue
        if status not in statuses:
            statuses.append(status)
    return statuses or None


def _visitor_pass_type_from_arguments(value: Any) -> VisitorPassType:
    normalized = str(value or VisitorPassType.ONE_TIME.value).strip().lower().replace("_", "-")
    try:
        return VisitorPassType(normalized)
    except ValueError:
        return VisitorPassType.ONE_TIME


def _visitor_pass_agent_payload(visitor_pass: VisitorPass, timezone_name: str) -> dict[str, Any]:
    payload = serialize_visitor_pass(visitor_pass, timezone_name=timezone_name)
    vehicle_summary_parts = [
        payload.get("vehicle_colour"),
        payload.get("vehicle_make"),
    ]
    vehicle_summary = " ".join(str(part) for part in vehicle_summary_parts if part)
    if payload.get("number_plate"):
        vehicle_summary = f"{vehicle_summary} - {payload['number_plate']}".strip(" -")
    payload["expected_time_display"] = _agent_datetime_display(visitor_pass.expected_time, timezone_name)
    if payload.get("valid_from") and payload.get("valid_until"):
        payload["window_summary"] = f"{payload['window_start']} to {payload['window_end']}"
    else:
        payload["window_summary"] = f"+/- {visitor_pass.window_minutes} minutes"
    payload["vehicle_summary"] = vehicle_summary or None
    if payload.get("duration_human"):
        payload["visit_summary"] = f"On site for {payload['duration_human']}"
    elif payload.get("arrival_time") and not payload.get("departure_time"):
        payload["visit_summary"] = "Arrived, departure not recorded yet"
    else:
        payload["visit_summary"] = None
    return _compact_observation(payload)


async def _resolve_visitor_pass_for_agent(
    session,
    arguments: dict[str, Any],
    *,
    editable_only: bool = False,
) -> VisitorPass | dict[str, Any]:
    service = get_visitor_pass_service()
    await service.refresh_statuses(session=session, publish=False)
    pass_id = _uuid_from_value(arguments.get("pass_id") or arguments.get("visitor_pass_id"))
    if pass_id:
        visitor_pass = await service.get_pass(session, pass_id)
        if not visitor_pass:
            return {"found": False, "error": "Visitor Pass not found."}
        if editable_only and visitor_pass.status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
            return {
                "found": True,
                "changed": False,
                "error": f"{visitor_pass.status.value.title()} visitor passes cannot be changed.",
                "visitor_pass": _visitor_pass_agent_payload(visitor_pass, DEFAULT_AGENT_TIMEZONE),
            }
        return visitor_pass

    visitor_name = str(arguments.get("visitor_name") or arguments.get("search") or "").strip()
    if not visitor_name:
        return {
            "found": False,
            "requires_details": True,
            "detail": "Which visitor pass should I use?",
        }
    statuses = [VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED] if editable_only else None
    matches = await service.list_passes(session, statuses=statuses, search=visitor_name, limit=10)
    exact = [pass_ for pass_ in matches if pass_.visitor_name.casefold() == visitor_name.casefold()]
    candidates = exact or matches
    if not candidates:
        return {"found": False, "error": f"I could not find a Visitor Pass for {visitor_name}."}
    if len(candidates) > 1:
        return {
            "found": True,
            "ambiguous": True,
            "error": f"I found more than one Visitor Pass matching {visitor_name}.",
            "matches": [_visitor_pass_agent_payload(pass_, DEFAULT_AGENT_TIMEZONE) for pass_ in candidates[:5]],
        }
    return candidates[0]


def _display_iso_datetime(value: Any, timezone_name: str) -> str | None:
    if not value:
        return None
    try:
        return _agent_datetime_display(datetime.fromisoformat(str(value)), timezone_name)
    except (TypeError, ValueError):
        return None


async def query_visitor_passes(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    limit = _bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100)
    statuses = _visitor_pass_statuses_from_arguments(arguments)
    search = str(arguments.get("search") or arguments.get("visitor_name") or "").strip() or None
    fuzzy_name = bool(arguments.get("fuzzy_name"))
    service = get_visitor_pass_service()
    async with AsyncSessionLocal() as session:
        changed = await service.refresh_statuses(session=session, publish=False)
        if changed:
            await session.commit()
        passes = await service.list_passes(session, statuses=statuses, search=search, limit=limit)
        if fuzzy_name and search:
            broad_limit = max(limit, 100)
            candidates = await service.list_passes(session, statuses=statuses, search=None, limit=broad_limit)
            merged: list[VisitorPass] = []
            seen: set[str] = set()
            for pass_ in [*passes, *_fuzzy_visitor_pass_name_matches(search, candidates)]:
                key = str(pass_.id)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(pass_)
                if len(merged) >= limit:
                    break
            passes = merged
        records = [_visitor_pass_agent_payload(pass_, config.site_timezone) for pass_ in passes]
    return {
        "visitor_passes": records,
        "count": len(records),
        "timezone": config.site_timezone,
        "filters": {
            "statuses": [status.value for status in statuses] if statuses else None,
            "search": search,
            "fuzzy_name": fuzzy_name or None,
        },
        "answer_artifacts": _visitor_pass_answer_artifacts(arguments, records, config.site_timezone),
    }


async def get_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    async with AsyncSessionLocal() as session:
        resolved = await _resolve_visitor_pass_for_agent(session, arguments)
        if isinstance(resolved, dict):
            return resolved
        record = _visitor_pass_agent_payload(resolved, config.site_timezone)
        return {
            "found": True,
            "visitor_pass": record,
            "timezone": config.site_timezone,
            "answer_artifacts": _visitor_pass_answer_artifacts(
                arguments,
                [record],
                config.site_timezone,
            ),
        }


async def create_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    visitor_name = str(arguments.get("visitor_name") or "").strip()
    expected_value = arguments.get("expected_time")
    pass_type = _visitor_pass_type_from_arguments(arguments.get("pass_type"))
    visitor_phone = str(arguments.get("visitor_phone") or "").strip()
    number_plate = normalize_registration_number(str(arguments.get("number_plate") or "")) or None
    valid_from_value = arguments.get("valid_from")
    valid_until_value = arguments.get("valid_until")
    missing = []
    if not visitor_name:
        missing.append("visitor_name")
    if pass_type == VisitorPassType.DURATION:
        if not valid_from_value:
            missing.append("valid_from")
        if not valid_until_value:
            missing.append("valid_until")
    elif not expected_value:
        missing.append("expected_time")
    if missing:
        return {
            "created": False,
            "requires_details": True,
            "missing": missing,
            "detail": (
                "I need the visitor name and start/end times before I can prepare a duration Visitor Pass."
                if pass_type == VisitorPassType.DURATION
                else "I need the visitor name and expected time before I can prepare a Visitor Pass."
            ),
        }

    config = await get_runtime_config()
    try:
        if pass_type == VisitorPassType.DURATION:
            valid_from = _parse_agent_datetime(valid_from_value, config.site_timezone)
            valid_until = _parse_agent_datetime(valid_until_value, config.site_timezone)
            expected_time = _parse_agent_datetime(expected_value, config.site_timezone) if expected_value else valid_from
        else:
            valid_from = None
            valid_until = None
            expected_time = _parse_agent_datetime(expected_value, config.site_timezone)
    except (TypeError, ValueError) as exc:
        return {"created": False, "error": f"Invalid visitor pass time: {exc}"}
    if pass_type == VisitorPassType.DURATION and (valid_from is None or valid_until is None):
        return {"created": False, "error": "Duration Visitor Passes require valid_from and valid_until."}
    window_minutes = _bounded_int(
        arguments.get("window_minutes"),
        default=DEFAULT_WINDOW_MINUTES,
        minimum=1,
        maximum=1440,
    )
    if pass_type == VisitorPassType.DURATION:
        starts_at = valid_from
        ends_at = valid_until
    else:
        starts_at = expected_time - timedelta(minutes=window_minutes)
        ends_at = expected_time + timedelta(minutes=window_minutes)
    assert starts_at is not None
    assert ends_at is not None
    if ends_at < _agent_now(config.site_timezone):
        return {
            "created": False,
            "error": "That Visitor Pass window has already elapsed.",
            "expected_time": _agent_datetime_iso(expected_time, config.site_timezone),
        }

    if not bool(arguments.get("confirm")):
        return {
            "created": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": visitor_name,
            "visitor_name": visitor_name,
            "pass_type": pass_type.value,
            "visitor_phone": visitor_phone or None,
            "number_plate": number_plate,
            "expected_time": _agent_datetime_iso(expected_time, config.site_timezone),
            "expected_time_display": _agent_datetime_display(expected_time, config.site_timezone),
            "window_minutes": window_minutes,
            "window_start": _agent_datetime_iso(starts_at, config.site_timezone),
            "window_end": _agent_datetime_iso(ends_at, config.site_timezone),
            "detail": (
                f"Create a duration Visitor Pass for {visitor_name} from "
                f"{_agent_datetime_display(starts_at, config.site_timezone)} to "
                f"{_agent_datetime_display(ends_at, config.site_timezone)} and message them on WhatsApp?"
                if pass_type == VisitorPassType.DURATION
                else f"Create a Visitor Pass for {visitor_name} at "
                f"{_agent_datetime_display(expected_time, config.site_timezone)} "
                f"with a +/- {window_minutes} minute window?"
            ),
        }

    service = get_visitor_pass_service()
    context = get_chat_tool_context()
    async with AsyncSessionLocal() as session:
        try:
            visitor_pass = await service.create_pass(
                session,
                visitor_name=visitor_name,
                expected_time=expected_time,
                window_minutes=window_minutes,
                pass_type=pass_type,
                visitor_phone=visitor_phone or None,
                number_plate=number_plate,
                valid_from=valid_from,
                valid_until=valid_until,
                source="alfred",
                created_by_user_id=_uuid_from_value(context.get("user_id")),
                actor="Alfred_AI",
            )
            await session.commit()
            await session.refresh(visitor_pass)
        except VisitorPassError as exc:
            await session.rollback()
            return {"created": False, "error": str(exc)}
        payload = _visitor_pass_agent_payload(visitor_pass, config.site_timezone)

    await event_bus.publish("visitor_pass.created", {"visitor_pass": payload, "source": "alfred"})
    if pass_type == VisitorPassType.DURATION and payload.get("visitor_phone"):
        try:
            await get_whatsapp_messaging_service().send_visitor_pass_outreach(visitor_pass)
        except Exception as exc:
            logger.warning(
                "alfred_visitor_pass_whatsapp_outreach_failed",
                extra={"visitor_pass_id": payload["id"], "error": str(exc)[:240]},
            )
    return {
        "created": True,
        "visitor_pass": payload,
        "visitor_pass_id": payload["id"],
        "visitor_name": payload["visitor_name"],
        "expected_time_display": payload["expected_time_display"],
    }


async def update_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    expected_time: datetime | None = None
    if arguments.get("expected_time"):
        try:
            expected_time = _parse_agent_datetime(arguments.get("expected_time"), config.site_timezone)
        except (TypeError, ValueError) as exc:
            return {"updated": False, "error": f"Invalid visitor expected time: {exc}"}
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    if arguments.get("valid_from"):
        try:
            valid_from = _parse_agent_datetime(arguments.get("valid_from"), config.site_timezone)
        except (TypeError, ValueError) as exc:
            return {"updated": False, "error": f"Invalid Visitor Pass start time: {exc}"}
    if arguments.get("valid_until"):
        try:
            valid_until = _parse_agent_datetime(arguments.get("valid_until"), config.site_timezone)
        except (TypeError, ValueError) as exc:
            return {"updated": False, "error": f"Invalid Visitor Pass end time: {exc}"}
    window_minutes = (
        _bounded_int(arguments.get("window_minutes"), default=DEFAULT_WINDOW_MINUTES, minimum=1, maximum=1440)
        if arguments.get("window_minutes") is not None
        else None
    )
    pass_type = _visitor_pass_type_from_arguments(arguments.get("pass_type")) if arguments.get("pass_type") else None
    visitor_phone = str(arguments.get("visitor_phone") or "").strip() or None
    replacement_name = str(arguments.get("new_visitor_name") or arguments.get("replacement_visitor_name") or "").strip() or None
    if arguments.get("pass_id") and arguments.get("visitor_name"):
        replacement_name = str(arguments.get("visitor_name") or "").strip()
    if not any([replacement_name, expected_time, window_minutes is not None, pass_type, visitor_phone, valid_from, valid_until]):
        return {
            "updated": False,
            "requires_details": True,
            "detail": "Tell me which Visitor Pass field to change: name, type, phone, expected time, or time window.",
        }

    service = get_visitor_pass_service()
    context = get_chat_tool_context()
    async with AsyncSessionLocal() as session:
        resolved = await _resolve_visitor_pass_for_agent(session, arguments, editable_only=True)
        if isinstance(resolved, dict):
            return resolved
        visitor_pass = resolved
        if not bool(arguments.get("confirm")):
            return {
                "updated": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": visitor_pass.visitor_name,
                "visitor_pass_id": str(visitor_pass.id),
                "visitor_name": replacement_name or visitor_pass.visitor_name,
                "pass_type": (pass_type or visitor_pass.pass_type).value,
                "visitor_phone": visitor_phone or visitor_pass.visitor_phone,
                "expected_time": (
                    _agent_datetime_iso(expected_time, config.site_timezone)
                    if expected_time
                    else _agent_datetime_iso(visitor_pass.expected_time, config.site_timezone)
                ),
                "expected_time_display": (
                    _agent_datetime_display(expected_time, config.site_timezone)
                    if expected_time
                    else _agent_datetime_display(visitor_pass.expected_time, config.site_timezone)
                ),
                "window_minutes": window_minutes or visitor_pass.window_minutes,
                "detail": f"Update the Visitor Pass for {visitor_pass.visitor_name}?",
            }
        try:
            await service.update_pass(
                session,
                visitor_pass,
                visitor_name=replacement_name,
                expected_time=expected_time,
                window_minutes=window_minutes,
                pass_type=pass_type,
                visitor_phone=visitor_phone,
                valid_from=valid_from,
                valid_until=valid_until,
                actor="Alfred_AI",
                actor_user_id=_uuid_from_value(context.get("user_id")),
            )
            await session.commit()
            await session.refresh(visitor_pass)
        except VisitorPassError as exc:
            await session.rollback()
            return {"updated": False, "error": str(exc)}
        payload = _visitor_pass_agent_payload(visitor_pass, config.site_timezone)

    await event_bus.publish("visitor_pass.updated", {"visitor_pass": payload, "source": "alfred"})
    return {"updated": True, "visitor_pass": payload, "visitor_pass_id": payload["id"]}


async def cancel_visitor_pass(arguments: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    service = get_visitor_pass_service()
    context = get_chat_tool_context()
    async with AsyncSessionLocal() as session:
        resolved = await _resolve_visitor_pass_for_agent(session, arguments, editable_only=True)
        if isinstance(resolved, dict):
            return resolved
        visitor_pass = resolved
        if not bool(arguments.get("confirm")):
            return {
                "cancelled": False,
                "requires_confirmation": True,
                "confirmation_field": "confirm",
                "target": visitor_pass.visitor_name,
                "visitor_pass_id": str(visitor_pass.id),
                "visitor_name": visitor_pass.visitor_name,
                "expected_time_display": _agent_datetime_display(visitor_pass.expected_time, config.site_timezone),
                "detail": f"Cancel the Visitor Pass for {visitor_pass.visitor_name}?",
            }
        try:
            await service.cancel_pass(
                session,
                visitor_pass,
                actor="Alfred_AI",
                actor_user_id=_uuid_from_value(context.get("user_id")),
                reason=str(arguments.get("reason") or "Cancelled by Alfred").strip(),
            )
            await session.commit()
            await session.refresh(visitor_pass)
        except VisitorPassError as exc:
            await session.rollback()
            return {"cancelled": False, "error": str(exc)}
        payload = _visitor_pass_agent_payload(visitor_pass, config.site_timezone)

    await event_bus.publish("visitor_pass.cancelled", {"visitor_pass": payload, "source": "alfred"})
    return {"cancelled": True, "visitor_pass": payload, "visitor_pass_id": payload["id"]}


async def trigger_icloud_sync(arguments: dict[str, Any]) -> dict[str, Any]:
    if not bool(arguments.get("confirm")):
        return {
            "synced": False,
            "requires_confirmation": True,
            "confirmation_field": "confirm",
            "target": "iCloud Calendar",
            "detail": "Sync connected iCloud Calendars and create or update Visitor Passes for Open Gate events?",
        }
    context = get_chat_tool_context()
    service = get_icloud_calendar_service()
    try:
        result = await service.sync_all(
            trigger_source="alfred",
            triggered_by_user_id=_uuid_from_value(context.get("user_id")),
            actor="Alfred_AI",
        )
    except ICloudCalendarError as exc:
        return {"synced": False, "error": str(exc)}
    return {
        "synced": True,
        "sync": result,
        "account_count": result.get("account_count", 0),
        "events_scanned": result.get("events_scanned", 0),
        "events_matched": result.get("events_matched", 0),
        "passes_created": result.get("passes_created", 0),
        "passes_updated": result.get("passes_updated", 0),
        "passes_cancelled": result.get("passes_cancelled", 0),
        "passes_skipped": result.get("passes_skipped", 0),
        "account_results": result.get("account_results", []),
    }
