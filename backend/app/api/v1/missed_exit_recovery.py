"""Admin-only recovery diagnostics. No source locations or capability tokens."""
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


def effective_outcome(now: datetime):
    return case((
        (MissedExitRecoveryAttempt.outcome == "awaiting_resident") &
        (NotificationActionContext.expires_at <= now), "expired"), else_=MissedExitRecoveryAttempt.outcome)


async def serialize_attempt(session: AsyncSession, row: MissedExitRecoveryAttempt, now: datetime) -> dict[str, Any]:
    result = {key: getattr(row, key) for key in ("id", "occurred_at", "owner_id", "owner_name", "vehicle_id",
        "registration_number", "event_id", "saga_id", "recovery_event_id", "command_id", "method", "outcome",
        "reason", "policy_version", "checks", "timeline", "notification", "duration_ms")}
    result["timeline"] = list(row.timeline or [])
    result["notification"] = dict(row.notification or {})
    context = await session.get(NotificationActionContext, row.action_context_id) if row.action_context_id else None
    if context and row.notification.get("canonical_attempt_id"):
        canonical = await session.get(MissedExitRecoveryAttempt, uuid.UUID(row.notification["canonical_attempt_id"]))
        if canonical and canonical.id != row.id:
            canonical_expired = canonical.outcome == "awaiting_resident" and context.expires_at <= now
            result["notification"]["canonical_outcome"] = "expired" if canonical_expired else canonical.outcome
            result["notification"]["canonical_reason"] = "resident_approval_expired" if canonical_expired else canonical.reason
            result["notification"]["action_at"] = (canonical.notification or {}).get("action_at")
    if context and result["outcome"] == "awaiting_resident" and context.expires_at <= now:
        result["outcome"], result["reason"] = "expired", "resident_approval_expired"
        result["timeline"].append({"at": context.expires_at.isoformat(), "stage": "resident_action",
            "status": "expired", "reason": "resident_approval_expired"})
    for kind in ("delivery_id", "apology_delivery_id"):
        identity = result["notification"].get(kind)
        if not identity:
            continue
        run = await session.get(NotificationRun, uuid.UUID(identity))
        if run:
            prefix = "approval" if kind == "delivery_id" else "apology"
            result["notification"].update({f"{prefix}_delivery_status": run.status,
                f"{prefix}_queued_at": run.queued_at, f"{prefix}_started_at": run.started_at,
                f"{prefix}_finished_at": run.finished_at, f"{prefix}_delivered_count": run.delivered_count,
                f"{prefix}_failed_count": run.failed_count, f"{prefix}_skipped_count": run.skipped_count,
                f"{prefix}_review_reason": run.review_reason})
            if kind == "delivery_id":
                result["notification"]["status"] = run.status
    return result


@router.get("/attempts")
async def list_attempts(
    _: Annotated[User, Depends(admin_user)], session: Annotated[AsyncSession, Depends(get_db_session)],
    owner_id: uuid.UUID | None = None,
    registration_number: str | None = Query(default=None, max_length=32),
    outcome: str | None = Query(default=None, max_length=40),
    method: str | None = Query(default=None, max_length=40),
    from_at: datetime | None = None, to_at: datetime | None = None,
    offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    if any(value is not None and value.tzinfo is None for value in (from_at, to_at)):
        raise HTTPException(status_code=400, detail="Date filters require a timezone.")
    if from_at and to_at and from_at > to_at:
        raise HTTPException(status_code=400, detail="Start date must precede end date.")
    now = datetime.now(UTC)
    query = select(MissedExitRecoveryAttempt).outerjoin(NotificationActionContext,
        NotificationActionContext.id == MissedExitRecoveryAttempt.action_context_id)
    if owner_id:
        query = query.where(MissedExitRecoveryAttempt.owner_id == owner_id)
    if registration_number:
        query = query.where(MissedExitRecoveryAttempt.registration_number == registration_number.upper().replace(" ", ""))
    if outcome:
        query = query.where(effective_outcome(now) == outcome)
    if method:
        query = query.where(MissedExitRecoveryAttempt.method == method)
    if from_at:
        query = query.where(MissedExitRecoveryAttempt.occurred_at >= from_at)
    if to_at:
        query = query.where(MissedExitRecoveryAttempt.occurred_at <= to_at)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list((await session.scalars(query.order_by(MissedExitRecoveryAttempt.occurred_at.desc(),
        MissedExitRecoveryAttempt.id.desc()).offset(offset).limit(limit))).all())
    return {"items": [await serialize_attempt(session, row, now) for row in rows],
            "total": total, "offset": offset, "limit": limit}


@router.get("/attempts/{attempt_id}")
async def get_attempt(attempt_id: uuid.UUID, _: Annotated[User, Depends(admin_user)],
                      session: Annotated[AsyncSession, Depends(get_db_session)]) -> dict:
    row = await session.get(MissedExitRecoveryAttempt, attempt_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Recovery attempt not found.")
    return await serialize_attempt(session, row, datetime.now(UTC))
