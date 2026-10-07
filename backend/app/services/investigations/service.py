from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.investigations.contracts import (
    OUTCOMES,
    ActivityFilters,
    cursor_for_item,
    decode_cursor,
    encode_cursor,
    resolve_time_range,
)
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
            {"value": outcome, "label": outcome.replace("_", " ").title()} for outcome in OUTCOMES
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
        "incomplete_runs": [item for item in problems if item["outcome"] in INCOMPLETE_OUTCOMES][
            :8
        ],
        "repeated_problems": _repeated_problems(problems),
        "important_activity": important[:12],
        "site_timezone": site_timezone,
        "resolved_range": {
            "key": range_key,
            "from": from_at.isoformat() if from_at else None,
            "to": to_at.isoformat() if to_at else None,
        },
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
