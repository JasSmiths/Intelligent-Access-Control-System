import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import NotificationRule, User
from app.modules.notifications.base import NotificationDeliveryError
from app.services.notifications import (
    get_notification_service,
    normalize_actions,
    normalize_conditions,
    normalize_rule_payload,
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


class NotificationRuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    trigger_event: str | None = Field(default=None, min_length=1, max_length=120)
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    is_active: bool | None = None


class NotificationPreviewRequest(BaseModel):
    rule: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] | None = None


class NotificationRuleTestRequest(BaseModel):
    rule: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] | None = None


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
    actions = normalize_actions(request.actions)
    if not actions:
        raise HTTPException(status_code=400, detail="At least one notification action is required.")

    rule = NotificationRule(
        name=request.name.strip(),
        trigger_event=request.trigger_event.strip(),
        conditions=normalize_conditions(request.conditions),
        actions=actions,
        is_active=request.is_active,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
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
    rule = await get_rule_or_404(session, rule_id)
    if request.name is not None:
        rule.name = request.name.strip()
    if request.trigger_event is not None:
        rule.trigger_event = request.trigger_event.strip()
    if request.conditions is not None:
        rule.conditions = normalize_conditions(request.conditions)
    if request.actions is not None:
        actions = normalize_actions(request.actions)
        if not actions:
            raise HTTPException(status_code=400, detail="At least one notification action is required.")
        rule.actions = actions
    if request.is_active is not None:
        rule.is_active = request.is_active
    await session.commit()
    await session.refresh(rule)
    return serialize_rule(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_rule(
    rule_id: uuid.UUID,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    rule = await get_rule_or_404(session, rule_id)
    await session.delete(rule)
    await session.commit()


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
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    rule = normalize_rule_payload(request.rule)
    if not rule["trigger_event"]:
        raise HTTPException(status_code=400, detail="A trigger is required before sending a test.")
    if not rule["actions"]:
        raise HTTPException(status_code=400, detail="At least one notification action is required before sending a test.")

    context = (
        notification_context_from_payload(request.context)
        if request.context
        else sample_notification_context(str(rule["trigger_event"]))
    )
    try:
        notification = await get_notification_service().process_context(
            context,
            raise_on_failure=True,
            rules_override=[rule],
        )
    except NotificationDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "sent",
        "title": notification.title,
        "body": notification.body,
        "preview": await get_notification_service().preview_rule(rule, context),
    }


@router.post("/rules/{rule_id}/test")
async def test_notification_rule(
    rule_id: uuid.UUID,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rule = await get_rule_or_404(session, rule_id)
    context = sample_notification_context(rule.trigger_event)
    try:
        notification = await get_notification_service().process_context(
            context,
            raise_on_failure=True,
            rules_override=[serialize_rule(rule)],
        )
    except NotificationDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "sent",
        "title": notification.title,
        "body": notification.body,
        "preview": await get_notification_service().preview_rule(rule, context),
    }


async def get_rule_or_404(session: AsyncSession, rule_id: uuid.UUID) -> NotificationRule:
    rule = await session.get(NotificationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Notification workflow not found.")
    return rule


def serialize_rule(rule: NotificationRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "trigger_event": rule.trigger_event,
        "conditions": normalize_conditions(rule.conditions),
        "actions": normalize_actions(rule.actions),
        "is_active": rule.is_active,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }
