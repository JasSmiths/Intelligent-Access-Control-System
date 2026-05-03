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
    legacy_gate_malfunction_stage,
    normalize_actions,
    normalize_conditions,
    normalize_gate_malfunction_stages,
    normalize_rule_payload,
    normalize_trigger_event,
    notification_context_from_payload,
    sample_notification_context,
)
from app.services.telemetry import (
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
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
    user = _
    normalized = normalize_rule_payload(request.model_dump())
    actions = normalized["actions"]
    if not actions:
        raise HTTPException(status_code=400, detail="At least one notification action is required.")

    rule = NotificationRule(
        name=normalized["name"],
        trigger_event=normalized["trigger_event"],
        conditions=normalized["conditions"],
        actions=actions,
        is_active=normalized["is_active"],
    )
    session.add(rule)
    if hasattr(session, "flush"):
        await session.flush()
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="notification_rule.create",
        actor=actor_from_user(user),
        actor_user_id=getattr(user, "id", None),
        target_entity="NotificationRule",
        target_id=rule.id,
        target_label=rule.name,
        diff={"old": {}, "new": rule_audit_snapshot(rule)},
    )
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
    user = _
    rule = await get_rule_or_404(session, rule_id)
    before = rule_audit_snapshot(rule)
    legacy_stage = legacy_gate_malfunction_stage(request.trigger_event) if request.trigger_event is not None else ""
    if request.name is not None:
        rule.name = request.name.strip()
    if request.trigger_event is not None:
        rule.trigger_event = normalize_trigger_event(request.trigger_event)
        if legacy_stage and request.actions is None:
            rule.actions = [
                {
                    **action,
                    "gate_malfunction_stages": [legacy_stage],
                }
                for action in normalize_actions(rule.actions)
            ]
    if request.conditions is not None:
        rule.conditions = normalize_conditions(request.conditions)
    if request.actions is not None:
        actions = normalize_actions(request.actions)
        if legacy_stage:
            actions = [
                {
                    **action,
                    "gate_malfunction_stages": normalize_gate_malfunction_stages([legacy_stage]),
                }
                for action in actions
            ]
        if not actions:
            raise HTTPException(status_code=400, detail="At least one notification action is required.")
        rule.actions = actions
    if request.is_active is not None:
        rule.is_active = request.is_active
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="notification_rule.update",
        actor=actor_from_user(user),
        actor_user_id=getattr(user, "id", None),
        target_entity="NotificationRule",
        target_id=rule.id,
        target_label=rule.name,
        diff=audit_diff(before, rule_audit_snapshot(rule)),
    )
    await session.commit()
    await session.refresh(rule)
    return serialize_rule(rule)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_rule(
    rule_id: uuid.UUID,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    user = _
    rule = await get_rule_or_404(session, rule_id)
    await write_audit_log(
        session,
        category=TELEMETRY_CATEGORY_CRUD,
        action="notification_rule.delete",
        actor=actor_from_user(user),
        actor_user_id=getattr(user, "id", None),
        target_entity="NotificationRule",
        target_id=rule.id,
        target_label=rule.name,
        diff={"old": rule_audit_snapshot(rule), "new": {}},
    )
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
    serialized = serialize_rule(rule)
    context = sample_notification_context(serialized["trigger_event"])
    try:
        notification = await get_notification_service().process_context(
            context,
            raise_on_failure=True,
            rules_override=[serialized],
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


def rule_audit_snapshot(rule: NotificationRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "trigger_event": rule.trigger_event,
        "conditions": normalize_conditions(rule.conditions),
        "actions": normalize_actions(rule.actions),
        "is_active": rule.is_active,
    }
