"""Admin-only recovery diagnostics. No source locations or capability tokens."""

import math
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import MissedExitRecoveryAttempt, NotificationActionContext, NotificationRun, User

router = APIRouter()


def next_expiry_ms(deadline: datetime, now: datetime) -> int:
    # Return a relative deadline so an operator's workstation clock can differ.
    return max(1, math.ceil((deadline - now).total_seconds() * 1000))


def approval_expired(now: datetime):
    return (
        (MissedExitRecoveryAttempt.outcome == "awaiting_resident")
        & (NotificationActionContext.expires_at <= now)
    )


async def serialize_attempts(
    session: AsyncSession, rows: list[MissedExitRecoveryAttempt], now: datetime,
) -> list[dict[str, Any]]:
    contexts = {c.id: c for c in await session.scalars(select(NotificationActionContext).where(
        NotificationActionContext.id.in_({r.action_context_id for r in rows if r.action_context_id})))}
    canonical_ids = {uuid.UUID(r.notification["canonical_attempt_id"]) for r in rows
                     if (r.notification or {}).get("canonical_attempt_id")}
    canonicals = {a.id: a for a in await session.scalars(select(MissedExitRecoveryAttempt).where(
        MissedExitRecoveryAttempt.id.in_(canonical_ids)))} if canonical_ids else {}
    delivery_keys = ("delivery_id", "apology_delivery_id", "expiry_delivery_id")
    run_ids = {uuid.UUID(r.notification[k]) for r in [*rows, *canonicals.values()] for k in delivery_keys
               if (r.notification or {}).get(k)}
    runs = {r.id: r for r in await session.scalars(select(NotificationRun).where(
        NotificationRun.id.in_(run_ids)))} if run_ids else {}
    return [serialize_attempt(row, contexts.get(row.action_context_id) if row.action_context_id else None,
            canonicals.get(uuid.UUID(row.notification["canonical_attempt_id"]))
            if (row.notification or {}).get("canonical_attempt_id") else None, runs, now) for row in rows]


def serialize_attempt(row: MissedExitRecoveryAttempt, context: NotificationActionContext | None,
                      canonical: MissedExitRecoveryAttempt | None, runs: dict[uuid.UUID, NotificationRun],
                      now: datetime) -> dict[str, Any]:
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "occurred_at",
            "owner_id",
            "owner_name",
            "vehicle_id",
            "registration_number",
            "event_id",
            "saga_id",
            "recovery_event_id",
            "command_id",
            "method",
            "outcome",
            "reason",
            "policy_version",
            "checks",
            "timeline",
            "notification",
            "duration_ms",
        )
    }
    result["timeline"] = list(row.timeline or [])
    result["notification"] = dict(row.notification or {})
    if context and context.expires_at > now and (
        row.outcome == "awaiting_resident" or (canonical and canonical.outcome == "awaiting_resident")
    ):
        result["next_expiry_in_ms"] = next_expiry_ms(context.expires_at, now)
    if context:
        result["notification"].update(approval_recipient=row.owner_name, approval_device=context.notify_service,
            approval_action_state=(context.outcome or "used") if context.consumed_at else
                                  ("expired" if context.expires_at <= now else "pending"))
    if context and canonical and canonical.id != row.id:
        canonical_expired = (
            canonical.outcome == "awaiting_resident" and context.expires_at <= now
        )
        result["notification"]["canonical_outcome"] = (
            "expired" if canonical_expired else canonical.outcome
        )
        result["notification"]["canonical_reason"] = (
            "resident_approval_expired" if canonical_expired else canonical.reason
        )
        for key in ("action_at", "delivery_id", "apology_delivery_id", "expiry_delivery_id"):
            if value := (canonical.notification or {}).get(key):
                result["notification"][key] = value
    if context and result["outcome"] == "awaiting_resident" and context.expires_at <= now:
        result["outcome"], result["reason"] = "expired", "resident_approval_expired"
        result["timeline"].append(
            {
                "at": context.expires_at.isoformat(),
                "stage": "resident_action",
                "status": "expired",
                "reason": "resident_approval_expired",
            }
        )
    for kind, prefix in (("delivery_id", "approval"), ("apology_delivery_id", "apology"), ("expiry_delivery_id", "expiry")):
        identity = result["notification"].get(kind)
        if not identity:
            continue
        run = runs.get(uuid.UUID(identity))
        if run:
            result["notification"].update(
                {
                    f"{prefix}_delivery_status": run.status,
                    f"{prefix}_provider_accepted_at": run.finished_at if run.status == "provider_accepted" else None,
                    f"{prefix}_queued_at": run.queued_at,
                    f"{prefix}_started_at": run.started_at,
                    f"{prefix}_finished_at": run.finished_at,
                    f"{prefix}_delivered_count": run.delivered_count,
                    f"{prefix}_failed_count": run.failed_count,
                    f"{prefix}_skipped_count": run.skipped_count,
                    f"{prefix}_review_reason": run.review_reason,
                }
            )
            if kind == "delivery_id" and not canonical:
                result["notification"]["status"] = run.status
    return result


@router.get("/attempts")
async def list_attempts(
    _: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    owner_id: uuid.UUID | None = None,
    registration_number: Annotated[str | None, Query(max_length=32)] = None,
    outcome: Annotated[str | None, Query(max_length=40)] = None,
    method: Annotated[str | None, Query(max_length=40)] = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict:
    if any(value is not None and value.tzinfo is None for value in (from_at, to_at)):
        raise HTTPException(status_code=400, detail="Date filters require a timezone.")
    if from_at and to_at and from_at > to_at:
        raise HTTPException(status_code=400, detail="Start date must precede end date.")
    now = datetime.now(UTC)
    attempt = MissedExitRecoveryAttempt
    expired = approval_expired(now)
    display_outcome = case((expired, "expired"), else_=attempt.outcome)
    query = select(
        attempt.id, attempt.occurred_at, attempt.owner_name, attempt.registration_number,
        attempt.method, display_outcome.label("outcome"),
        case((expired, "resident_approval_expired"), else_=attempt.reason).label("reason"),
    ).select_from(attempt).outerjoin(
        NotificationActionContext,
        NotificationActionContext.id == MissedExitRecoveryAttempt.action_context_id,
    )
    if owner_id:
        query = query.where(MissedExitRecoveryAttempt.owner_id == owner_id)
    if registration_number:
        query = query.where(
            MissedExitRecoveryAttempt.registration_number
            == registration_number.upper().replace(" ", "")
        )
    if method:
        query = query.where(MissedExitRecoveryAttempt.method == method)
    if from_at:
        query = query.where(MissedExitRecoveryAttempt.occurred_at >= from_at)
    if to_at:
        query = query.where(MissedExitRecoveryAttempt.occurred_at <= to_at)
    # Include pending approvals in the deadline calculation even for an empty
    # "expired" page: expiry alone can add its first matching row without a write.
    count = func.count()
    if outcome in {"awaiting_resident", "expired"}:
        query = query.where(attempt.outcome.in_(("awaiting_resident", outcome)))
        count = count.filter(display_outcome == outcome)
    elif outcome:
        query = query.where(attempt.outcome == outcome)
    next_deadline = func.min(NotificationActionContext.expires_at).filter(
        attempt.outcome == "awaiting_resident", NotificationActionContext.expires_at > now,
    )
    total, deadline = (await session.execute(query.with_only_columns(count, next_deadline))).one()
    if outcome in {"awaiting_resident", "expired"}:
        query = query.where(display_outcome == outcome)
    rows = await session.execute(
        query.order_by(attempt.occurred_at.desc(), attempt.id.desc()).offset(offset).limit(limit)
    )
    return {
        **({"next_expiry_in_ms": next_expiry_ms(deadline, now)} if deadline is not None else {}),
        "items": [dict(row) for row in rows.mappings()],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/attempts/{attempt_id}")
async def get_attempt(
    attempt_id: uuid.UUID,
    _: Annotated[User, Depends(admin_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    row = await session.get(MissedExitRecoveryAttempt, attempt_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recovery attempt not found.")
    return (await serialize_attempts(session, [row], datetime.now(UTC)))[0]
