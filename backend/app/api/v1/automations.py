import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import AutomationRule, User
from app.services.automations import (
    AutomationError,
    get_automation_service,
    normalize_actions,
    normalize_conditions,
    normalize_triggers,
    serialize_rule,
)

router = APIRouter()


class AutomationRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True


class AutomationRuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    triggers: list[dict[str, Any]] | None = None
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    is_active: bool | None = None


class AutomationDryRunRequest(BaseModel):
    trigger_key: str | None = None
    trigger_payload: dict[str, Any] = Field(default_factory=dict)


class AutomationScheduleParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.get("/catalog")
async def automation_catalog(_: User = Depends(admin_user)) -> dict[str, Any]:
    return await get_automation_service().catalog()


@router.get("/rules")
async def list_automation_rules(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    rules = await get_automation_service().list_rules(session)
    return [serialize_rule(rule) for rule in rules]


@router.post("/rules", status_code=status.HTTP_201_CREATED)
async def create_automation_rule(
    request: AutomationRuleRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        rule = await get_automation_service().create_rule(
            session,
            name=request.name,
            description=request.description,
            triggers=request.triggers,
            conditions=request.conditions,
            actions=request.actions,
            is_active=request.is_active,
            created_by=user,
        )
        await session.commit()
        await session.refresh(rule)
        return serialize_rule(rule)
    except AutomationError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/rules/{rule_id}")
async def get_automation_rule(
    rule_id: uuid.UUID,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    return serialize_rule(await get_rule_or_404(session, rule_id))


@router.patch("/rules/{rule_id}")
async def update_automation_rule(
    rule_id: uuid.UUID,
    request: AutomationRuleUpdateRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rule = await get_rule_or_404(session, rule_id)
    try:
        await get_automation_service().update_rule(
            session,
            rule,
            actor=user,
            name=request.name,
            description=request.description if "description" in request.model_fields_set else None,
            triggers=request.triggers if "triggers" in request.model_fields_set else None,
            conditions=request.conditions if "conditions" in request.model_fields_set else None,
            actions=request.actions if "actions" in request.model_fields_set else None,
            is_active=request.is_active,
        )
        await session.commit()
        await session.refresh(rule)
        return serialize_rule(rule)
    except AutomationError as exc:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation_rule(
    rule_id: uuid.UUID,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    rule = await get_rule_or_404(session, rule_id)
    await get_automation_service().delete_rule(session, rule, actor=user)
    await session.commit()


@router.post("/rules/{rule_id}/dry-run")
async def dry_run_automation_rule(
    rule_id: uuid.UUID,
    request: AutomationDryRunRequest | None = None,
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    rule = await get_rule_or_404(session, rule_id)
    body = request or AutomationDryRunRequest()
    return await get_automation_service().dry_run_rule(
        rule,
        trigger_key=body.trigger_key,
        trigger_payload=body.trigger_payload,
    )


@router.post("/dry-run")
async def dry_run_unsaved_automation_rule(
    request: dict[str, Any],
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    rule = {
        "name": request.get("name") or "Unsaved Automation",
        "triggers": normalize_triggers(request.get("triggers")),
        "conditions": normalize_conditions(request.get("conditions")),
        "actions": normalize_actions(request.get("actions")),
        "is_active": request.get("is_active", True),
    }
    trigger_key = str(request.get("trigger_key") or "") or None
    trigger_payload = request.get("trigger_payload") if isinstance(request.get("trigger_payload"), dict) else {}
    return await get_automation_service().dry_run_rule(
        rule,
        trigger_key=trigger_key,
        trigger_payload=trigger_payload,
    )


@router.post("/parse-schedule")
async def parse_automation_schedule(
    request: AutomationScheduleParseRequest,
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    return await get_automation_service().parse_ai_schedule(request.text)


@router.post("/webhooks/{webhook_key}")
async def receive_automation_webhook(
    webhook_key: str,
    request: Request,
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid automation webhook JSON.",
        ) from exc
    if not isinstance(payload, dict):
        payload = {"value": payload}
    source_ip = request.client.host if request.client else "unknown"
    return await get_automation_service().handle_webhook(
        webhook_key,
        payload,
        source_ip=source_ip,
    )


async def get_rule_or_404(session: AsyncSession, rule_id: uuid.UUID) -> AutomationRule:
    rule = await session.get(AutomationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Automation rule not found.")
    return rule
