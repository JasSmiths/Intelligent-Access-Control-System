import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.api.confirmations import send_confirmed_notification
from app.db.session import get_db_session
from app.models import GateMalfunctionNotificationOutbox, NotificationRule, NotificationRun, User
from app.services.notification_runs import review_filter, run_summary
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation
from app.services import notification_rules
from app.services.mutation_context import MutationError
from app.services.workflows.notification_payloads import (normalize_actions, normalize_conditions, normalize_rule_payload)
from app.services.notifications import (
    get_notification_service,
    notification_context_from_payload,
    sample_notification_context,
)

router = APIRouter()


class NotificationRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    trigger_event: str = Field(min_length=1, max_length=120)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True
    confirmation_token: str | None = Field(default=None, max_length=160)


class NotificationRuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    trigger_event: str | None = Field(default=None, min_length=1, max_length=120)
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    is_active: bool | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class NotificationPreviewRequest(BaseModel):
    rule: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] | None = None


class NotificationRuleTestRequest(BaseModel):
    rule: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class StoredNotificationRuleTestRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


class NotificationRuleDeleteRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


@router.get("/runs")
async def list_notification_runs(
    _: User = Depends(admin_user), session: AsyncSession = Depends(get_db_session),
    review_only: bool = False, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
) -> list[dict[str, Any]]:
    query = select(NotificationRun)
    if review_only:
        query = query.where(review_filter())
    rows = (await session.scalars(query.order_by(NotificationRun.queued_at.desc(), NotificationRun.id)
                                  .offset(offset).limit(limit))).all()
    return [run_summary(row) for row in rows]


@router.get("/runs/{run_id}")
async def get_notification_run(
    run_id: uuid.UUID, _: User = Depends(admin_user), session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    row = await session.get(NotificationRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification run not found")
    return run_summary(row)


@router.get("/recovery/gate-outbox")
async def gate_notification_review(
    _: User = Depends(admin_user), session: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
) -> list[dict[str, Any]]:
    from sqlalchemy import and_, or_
    rows = (await session.scalars(select(GateMalfunctionNotificationOutbox).where(or_(
        GateMalfunctionNotificationOutbox.status == "review_required",
        and_(GateMalfunctionNotificationOutbox.recovery_version.is_(None),
             GateMalfunctionNotificationOutbox.status.in_(("pending", "sending", "failed"))),
    )).order_by(GateMalfunctionNotificationOutbox.occurred_at.desc(), GateMalfunctionNotificationOutbox.id)
        .offset(offset).limit(limit))).all()
    return [{"id": str(row.id), "status": "review_required", "stored_status": row.status,
             "review_reason": "historical_unfinished" if row.recovery_version is None else
             "dispatch_age_exceeded" if row.last_error == "dispatch_age_exceeded" else "provider_outcome_unknown",
             "occurred_at": row.occurred_at,
             "notification_run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"iacs:gate-notification:{row.id}"))
             if row.recovery_version == 1 else None} for row in rows]


@router.get("/catalog")
async def notification_catalog(_: User = Depends(admin_user)) -> dict[str, Any]:
    return await get_notification_service().catalog()


@router.get("/rules")
async def list_notification_rules(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    rules = (
        await session.scalars(
            select(NotificationRule).order_by(NotificationRule.created_at.desc(), NotificationRule.name)
        )
    ).all()
    return [serialize_rule(rule) for rule in rules]


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_notification_rule(
    request: NotificationRuleRequest,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    user = _
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    await require_confirmation(
        session,
        user=user,
        action="notification_rule.create",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    try:
        rule = await notification_rules.create_rule(session, confirmation_payload, user=user, source="api")
    except MutationError as exc:
        raise rule_http_error(exc) from exc
    return serialize_rule(rule)


@router.get("/rules/{rule_id}")
async def get_notification_rule(
    rule_id: uuid.UUID,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rule = await get_rule_or_404(session, rule_id)
    return serialize_rule(rule)


@router.patch("/rules/{rule_id}")
async def update_notification_rule(
    rule_id: uuid.UUID,
    request: NotificationRuleUpdateRequest,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    user = _
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    await require_confirmation(
        session,
        user=user,
        action="notification_rule.update",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
    try:
        rule = await notification_rules.update_rule(session, rule_id, confirmation_payload, user=user, source="api")
    except MutationError as exc:
        raise rule_http_error(exc) from exc
    return serialize_rule(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_rule(
    rule_id: uuid.UUID,
    request: NotificationRuleDeleteRequest | None = None,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    user = _
    await require_confirmation(
        session,
        user=user,
        action="notification_rule.delete",
        payload={"rule_id": str(rule_id)},
        confirmation_token=request.confirmation_token if request else None,
    )
    try:
        await notification_rules.delete_rule(session, rule_id, user=user, source="api")
    except MutationError as exc:
        raise rule_http_error(exc) from exc


@router.post("/rules/preview")
async def preview_notification_rule(
    request: NotificationPreviewRequest,
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    rule = normalize_rule_payload(request.rule)
    context = (
        notification_context_from_payload(request.context)
        if request.context
        else sample_notification_context(str(rule.get("trigger_event") or "authorized_entry"))
    )
    return await get_notification_service().preview_rule(rule, context)


@router.post("/rules/test")
async def test_notification_rule_payload(
    request: NotificationRuleTestRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rule = normalize_rule_payload(request.rule)
    if not rule["trigger_event"]:
        raise HTTPException(status_code=400, detail="A trigger is required before sending a test.")
    if not rule["actions"]:
        raise HTTPException(status_code=400, detail="At least one notification action is required before sending a test.")

    context = (
        notification_context_from_payload(request.context)
        if request.context else sample_notification_context(str(rule["trigger_event"]))
    )
    result = await send_confirmed_notification(
        session, user=user, action="notification_rule.test",
        payload=request.model_dump(exclude={"confirmation_token"}, exclude_none=True),
        confirmation_token=request.confirmation_token, context=context, rules_override=[rule],
    )
    return {
        "status": "sent",
        "delivery_status": result.status,
        "notification_run_id": result.run_id,
        "title": result.notification.title,
        "body": result.notification.body,
        "preview": await get_notification_service().preview_rule(rule, context),
    }


@router.post("/rules/{rule_id}/test")
async def test_notification_rule(
    rule_id: uuid.UUID,
    request: StoredNotificationRuleTestRequest | None = None,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rule = await get_rule_or_404(session, rule_id)
    serialized = serialize_rule(rule)
    context = sample_notification_context(serialized["trigger_event"])
    result = await send_confirmed_notification(
        session, user=user, action="notification_rule.test", payload={"rule_id": str(rule_id)},
        confirmation_token=request.confirmation_token if request else None,
        context=context, rules_override=[serialized],
    )
    return {
        "status": "sent",
        "delivery_status": result.status,
        "notification_run_id": result.run_id,
        "title": result.notification.title,
        "body": result.notification.body,
        "preview": await get_notification_service().preview_rule(serialized, context),
    }


async def require_confirmation(
    session: AsyncSession,
    *,
    user: User,
    action: str,
    payload: dict[str, Any],
    confirmation_token: str | None,
) -> None:
    try:
        await consume_action_confirmation(
            session,
            user=user,
            action=action,
            payload=payload,
            confirmation_token=confirmation_token,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def get_rule_or_404(session: AsyncSession, rule_id: uuid.UUID) -> NotificationRule:
    rule = await session.get(NotificationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Notification workflow not found.")
    return rule


def serialize_rule(rule: NotificationRule) -> dict[str, Any]:
    last_fired_at = getattr(rule, "last_fired_at", None)
    normalized = normalize_rule_payload(
        {
            "id": str(rule.id),
            "name": rule.name,
            "trigger_event": rule.trigger_event,
            "conditions": rule.conditions,
            "actions": rule.actions,
            "is_active": rule.is_active,
        }
    )
    return {
        "id": str(rule.id),
        "name": normalized["name"],
        "trigger_event": normalized["trigger_event"],
        "conditions": normalized["conditions"],
        "actions": normalized["actions"],
        "is_active": normalized["is_active"],
        "last_fired_at": last_fired_at.isoformat() if last_fired_at else None,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }


def rule_http_error(exc: MutationError) -> HTTPException:
    return HTTPException(status_code={"forbidden": 403, "not_found": 404}.get(exc.code, 400), detail=str(exc))
