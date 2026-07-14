from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.investigations.contracts import (
    ActivityFilters,
    OUTCOMES,
    cursor_for_item,
    decode_cursor,
    encode_cursor,
    resolve_time_range,
)
from app.services.investigations.interpreter import interpret_question
from app.services.investigations.presenter import (
    build_audit_detail,
    build_audit_episode,
    build_trace_detail,
    build_trace_episode,
)
from app.services.investigations.repository import (
    enrich_traces,
    fetch_candidate_batch,
    filter_catalog,
    load_audit_or_linked_trace,
    load_trace_detail,
)
from app.services.settings import RuntimeConfig


MAX_ACTIVITY_SCAN_ROWS = 5000
PROBLEM_OUTCOMES = {"failed", "blocked", "pending", "unknown"}
INCOMPLETE_OUTCOMES = {"pending", "unknown"}


async def list_activity(
    session: AsyncSession,
    filters: ActivityFilters,
    *,
    limit: int,
    cursor: str | None,
    site_timezone: str,
) -> dict[str, Any]:
    requested_cursor = decode_cursor(cursor)
    scan_cursor = requested_cursor
    matched: list[dict[str, Any]] = []
    scanned = 0
    partial = False
    more_candidates = False
    last_scanned = None
    batch_size = min(500, max(100, limit * 3))

    while len(matched) <= limit and scanned < MAX_ACTIVITY_SCAN_ROWS:
        batch = await fetch_candidate_batch(
            session,
            filters,
            cursor=scan_cursor,
            batch_size=batch_size,
        )
        merged = [
            *(("trace", trace) for trace in batch.traces),
            *(("audit", audit) for audit in batch.audits),
        ]
        merged.sort(key=_candidate_sort_key, reverse=True)
        page = merged[:batch_size]
        more_candidates = len(merged) > batch_size or not (
            batch.traces_exhausted and batch.audits_exhausted
        )
        if not page:
            more_candidates = False
            break

        trace_rows = [row for kind, row in page if kind == "trace"]
        enrichment = await enrich_traces(session, trace_rows)
        for kind, row in page:
            scanned += 1
            if kind == "trace":
                linked = enrichment[row.trace_id]
                item = build_trace_episode(
                    row,
                    automation=linked.automation,
                    audits=linked.audits,
                    gate_commands=linked.gate_commands,
                )
            else:
                item = build_audit_episode(row)
            last_scanned = cursor_for_item(item)
            if filters.outcome and item["outcome"] != filters.outcome:
                if scanned >= MAX_ACTIVITY_SCAN_ROWS:
                    break
                continue
            matched.append(item)
            if len(matched) > limit:
                break

        if len(matched) > limit:
            more_candidates = True
            break
        if not more_candidates:
            break
        if last_scanned is None:
            break
        scan_cursor = last_scanned

    if scanned >= MAX_ACTIVITY_SCAN_ROWS and more_candidates and len(matched) <= limit:
        partial = True

    items = matched[:limit]
    next_cursor = None
    if len(matched) > limit and items:
        next_cursor = encode_cursor(cursor_for_item(items[-1]))
    elif partial and last_scanned:
        next_cursor = encode_cursor(last_scanned)

    return {
        "items": items,
        "next_cursor": next_cursor,
        "site_timezone": site_timezone,
        "resolved_range": _resolved_range(filters),
        "applied_filters": filters.as_payload(),
        "partial": partial,
    }


async def get_activity_detail(
    session: AsyncSession,
    episode_id: str,
    *,
    site_timezone: str,
) -> dict[str, Any] | None:
    kind, separator, row_id = episode_id.partition(":")
    if not separator or kind not in {"trace", "audit"} or not row_id:
        return None
    if kind == "trace":
        bundle = await load_trace_detail(session, row_id)
        return _trace_detail_payload(bundle, site_timezone) if bundle else None

    audit, linked_trace = await load_audit_or_linked_trace(session, row_id)
    if not audit:
        return None
    if linked_trace:
        bundle = await load_trace_detail(session, linked_trace.trace_id)
        return _trace_detail_payload(bundle, site_timezone) if bundle else None
    return build_audit_detail(audit, site_timezone=site_timezone)


async def investigation_filter_options(
    session: AsyncSession,
    *,
    site_timezone: str,
) -> dict[str, Any]:
    catalog = await filter_catalog(session)
    return {
        **catalog,
        "outcomes": [
            {"value": outcome, "label": outcome.replace("_", " ").title()}
            for outcome in OUTCOMES
        ],
        "time_ranges": [
            {"value": value, "label": value.replace("_", " ").title()}
            for value in ("today", "yesterday", "last_24_hours", "last_7_days", "custom")
        ],
        "site_timezone": site_timezone,
    }


async def investigation_overview(
    session: AsyncSession,
    *,
    site_timezone: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    from_at, to_at, range_key = resolve_time_range(
        "last_24_hours",
        from_at=None,
        to_at=None,
        timezone_name=site_timezone,
        now=now,
    )
    base = ActivityFilters(
        from_at=from_at,
        to_at=to_at,
        time_range=range_key,
        include_routine=False,
    )
    problem_items: list[dict[str, Any]] = []
    for outcome in ("failed", "blocked", "pending", "unknown"):
        payload = await list_activity(
            session,
            ActivityFilters(**{**base.__dict__, "outcome": outcome}),
            limit=40,
            cursor=None,
            site_timezone=site_timezone,
        )
        problem_items.extend(payload["items"])
    problems = _dedupe_sort(problem_items)
    important_payload = await list_activity(
        session,
        base,
        limit=30,
        cursor=None,
        site_timezone=site_timezone,
    )
    important = [item for item in important_payload["items"] if not item.get("routine")]
    return {
        "recent_problems": problems[:12],
        "incomplete_runs": [item for item in problems if item["outcome"] in INCOMPLETE_OUTCOMES][:8],
        "repeated_problems": _repeated_problems(problems),
        "important_activity": important[:12],
        "site_timezone": site_timezone,
        "resolved_range": {
            "key": range_key,
            "from": from_at.isoformat() if from_at else None,
            "to": to_at.isoformat() if to_at else None,
        },
    }


async def investigate(
    session: AsyncSession,
    *,
    question: str,
    scope: Mapping[str, Any],
    max_evidence: int,
    use_ai: bool,
    runtime: RuntimeConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    catalog = await investigation_filter_options(session, site_timezone=runtime.site_timezone)
    interpretation = await interpret_question(
        question,
        catalog,
        runtime=runtime,
        use_ai=use_ai,
        now=now,
    )
    merged = {**interpretation.filters, **{key: value for key, value in scope.items() if value is not None}}
    filters = _resolved_activity_filters(merged, runtime.site_timezone, now=now)

    if not _has_investigation_anchor(filters):
        return _insufficient_response(
            question,
            filters=filters,
            site_timezone=runtime.site_timezone,
            mode=interpretation.mode,
            ai_used=interpretation.ai_used,
            reason=(
                "The question does not identify a recorded device, automation, integration, outcome, "
                "trace, or searchable event. Add one of those details so IACS does not select unrelated activity."
            ),
        )

    activity = await list_activity(
        session,
        filters,
        limit=8,
        cursor=None,
        site_timezone=runtime.site_timezone,
    )
    episodes = activity["items"]
    if not episodes:
        return _insufficient_response(
            question,
            filters=filters,
            site_timezone=runtime.site_timezone,
            mode=interpretation.mode,
            ai_used=interpretation.ai_used,
            reason="No authorised recorded evidence matched the interpreted entities and time range.",
        )

    primary = episodes[0]
    detail = await get_activity_detail(
        session,
        str(primary["episode_id"]),
        site_timezone=runtime.site_timezone,
    )
    if not detail:
        return _insufficient_response(
            question,
            filters=filters,
            site_timezone=runtime.site_timezone,
            mode=interpretation.mode,
            ai_used=interpretation.ai_used,
            reason="The matching activity exists, but its supporting evidence could not be loaded.",
            episodes=episodes,
        )

    negative_question = _asks_about_missing_action(question)
    if primary["outcome"] == "unknown" or (
        negative_question and primary["outcome"] == "succeeded" and primary["dispatch_state"] == "not_applicable"
    ):
        return _insufficient_response(
            question,
            filters=filters,
            site_timezone=runtime.site_timezone,
            mode=interpretation.mode,
            ai_used=interpretation.ai_used,
            reason=(
                "Matching activity was found, but it does not record why the action in the question did or did not occur."
            ),
            episodes=episodes,
            detail=detail,
            max_evidence=max_evidence,
        )

    evidence = [
        {**item, "episode_id": primary["episode_id"]}
        for item in detail["timeline"][:max_evidence]
    ]
    citations = [
        {
            "id": item["id"],
            "label": item["title"],
            "timestamp": item["timestamp"],
            "episode_id": primary["episode_id"],
        }
        for item in evidence
    ]
    answer = _grounded_answer(primary)
    missing = _missing_evidence(primary, detail)
    certainty = "high" if primary["correlation"]["confidence"] == "exact" and not missing else "medium"
    return {
        "question": question,
        "answer": answer,
        "most_likely_reason": primary["summary"],
        "outcome": primary["outcome"],
        "dispatch_state": primary["dispatch_state"],
        "certainty": certainty,
        "evidence": evidence,
        "citations": citations,
        "episodes": episodes,
        "interpreted_filters": filters.as_payload(),
        "missing_evidence": missing,
        "site_timezone": runtime.site_timezone,
        "resolved_range": activity["resolved_range"],
        "ai_used": interpretation.ai_used,
        "mode": interpretation.mode,
    }


def _trace_detail_payload(bundle: Any, site_timezone: str) -> dict[str, Any]:
    return build_trace_detail(
        bundle.trace,
        spans=bundle.spans,
        automation=bundle.automation,
        audits=bundle.audits,
        access_event=bundle.access_event,
        movement_saga=bundle.movement_saga,
        gate_commands=bundle.gate_commands,
        current_schedule=bundle.current_schedule,
        site_timezone=site_timezone,
    )


def _candidate_sort_key(candidate: tuple[str, Any]) -> tuple[datetime, int, str]:
    kind, row = candidate
    timestamp = row.started_at if kind == "trace" else row.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    row_id = row.trace_id if kind == "trace" else str(row.id)
    return timestamp, 1 if kind == "trace" else 0, row_id


def _resolved_activity_filters(
    values: Mapping[str, Any],
    site_timezone: str,
    *,
    now: datetime | None,
) -> ActivityFilters:
    from_at, to_at, range_key = resolve_time_range(
        str(values.get("time_range") or "last_7_days"),
        from_at=values.get("from_at") if isinstance(values.get("from_at"), datetime) else None,
        to_at=values.get("to_at") if isinstance(values.get("to_at"), datetime) else None,
        timezone_name=site_timezone,
        now=now,
    )
    allowed = {
        "device",
        "automation",
        "schedule",
        "integration",
        "category",
        "outcome",
        "severity",
        "actor",
        "trigger",
        "trace",
        "q",
    }
    text_values = {
        key: str(values[key]).strip()
        for key in allowed
        if values.get(key) is not None and str(values[key]).strip()
    }
    return ActivityFilters(
        from_at=from_at,
        to_at=to_at,
        time_range=range_key,
        include_routine=bool(values.get("include_routine", False)),
        **text_values,
    )


def _has_investigation_anchor(filters: ActivityFilters) -> bool:
    return any(
        getattr(filters, key)
        for key in (
            "device",
            "automation",
            "schedule",
            "integration",
            "category",
            "outcome",
            "severity",
            "actor",
            "trigger",
            "trace",
            "q",
        )
    )


def _grounded_answer(episode: Mapping[str, Any]) -> str:
    summary = str(episode.get("summary") or "The activity has a recorded outcome.")
    dispatch = episode.get("dispatch_state")
    if dispatch == "withheld":
        return f"IACS decided not to send a device command. {summary}"
    if dispatch == "attempted_rejected":
        return f"IACS attempted the command, but it was rejected or failed. {summary}"
    if dispatch == "accepted_unverified":
        return f"IACS sent the command and it was accepted, but the expected device state was not confirmed. {summary}"
    if dispatch == "verified":
        return f"IACS sent the command and recorded the expected resulting state. {summary}"
    return summary


def _missing_evidence(episode: Mapping[str, Any], detail: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    if episode.get("dispatch_state") == "accepted_unverified":
        missing.append("No resulting device-state confirmation was recorded.")
    if episode.get("correlation", {}).get("confidence") != "exact":
        missing.append("This standalone audit record is not linked to a telemetry trace.")
    contexts = detail.get("configuration_context")
    if episode.get("reason_code") == "schedule_not_allowed" and isinstance(contexts, list):
        if not any(item.get("recorded_at_decision_time") for item in contexts if isinstance(item, Mapping)):
            missing.append("The schedule values used at decision time were not recorded.")
    return missing


def _insufficient_response(
    question: str,
    *,
    filters: ActivityFilters,
    site_timezone: str,
    mode: str,
    ai_used: bool,
    reason: str,
    episodes: list[dict[str, Any]] | None = None,
    detail: Mapping[str, Any] | None = None,
    max_evidence: int = 30,
) -> dict[str, Any]:
    primary_id = episodes[0]["episode_id"] if episodes else None
    raw_evidence = list(detail.get("timeline", []))[:max_evidence] if detail else []
    evidence = [{**item, "episode_id": primary_id} for item in raw_evidence]
    citations = [
        {
            "id": item.get("id"),
            "label": item.get("title"),
            "timestamp": item.get("timestamp"),
            "episode_id": item.get("episode_id"),
        }
        for item in evidence
    ]
    return {
        "question": question,
        "answer": f"IACS cannot determine the cause from the available evidence. {reason}",
        "most_likely_reason": None,
        "outcome": "unknown",
        "dispatch_state": "unknown",
        "certainty": "low",
        "evidence": evidence,
        "citations": citations,
        "episodes": episodes or [],
        "interpreted_filters": filters.as_payload(),
        "missing_evidence": [reason],
        "site_timezone": site_timezone,
        "resolved_range": _resolved_range(filters),
        "ai_used": ai_used,
        "mode": mode,
    }


def _resolved_range(filters: ActivityFilters) -> dict[str, Any]:
    return {
        "key": filters.time_range,
        "from": filters.from_at.isoformat() if filters.from_at else None,
        "to": filters.to_at.isoformat() if filters.to_at else None,
    }


def _dedupe_sort(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item["episode_id"]): item for item in items}
    return sorted(by_id.values(), key=lambda item: str(item["occurred_at"]), reverse=True)


def _repeated_problems(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        entities = item.get("entities")
        entity: Mapping[str, Any] = (
            next((value for value in entities if isinstance(value, Mapping)), {})
            if isinstance(entities, list)
            else {}
        )
        key = "|".join(
            (
                str(item.get("reason_code") or "unknown"),
                str(entity.get("id") or entity.get("label") or item.get("title") or "unknown"),
                str(item.get("category") or "unknown"),
            )
        )
        grouped[key].append(item)
    repeated = []
    for key, rows in grouped.items():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda item: str(item["occurred_at"]), reverse=True)
        repeated.append(
            {
                "key": key,
                "count": len(rows),
                "title": rows[0]["title"],
                "reason_code": rows[0]["reason_code"],
                "latest_at": rows[0]["occurred_at"],
                "episode_id": rows[0]["episode_id"],
            }
        )
    repeated.sort(key=lambda item: (item["count"], item["latest_at"]), reverse=True)
    return repeated[:8]


def _asks_about_missing_action(question: str) -> bool:
    text = question.casefold()
    return any(
        phrase in text
        for phrase in (
            "why didn't",
            "why did not",
            "why wasn’t",
            "why wasn't",
            "didn't open",
            "did not open",
            "not happen",
        )
    )
