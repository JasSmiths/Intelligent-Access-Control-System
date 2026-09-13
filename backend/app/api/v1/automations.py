import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import AutomationRule, User
from app.services.mutation_context import MutationError, load_active_admin
from app.services.automations import (
    WEBHOOK_NONCE_HEADER,
    WEBHOOK_SIGNATURE_HEADER,
    WEBHOOK_TIMESTAMP_HEADER,
    AutomationError,
    get_automation_service,
    serialize_rule,
    serialize_run,
)
from app.services.automation_execution import AutomationRunStore
from app.services.workflows.automation_definition import normalize_actions, normalize_conditions, normalize_triggers
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation

router = APIRouter()


class AutomationRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True
    confirmation_token: str | None = Field(default=None, max_length=160)


class AutomationRuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    triggers: list[dict[str, Any]] | None = None
    conditions: list[dict[str, Any]] | None = None
    actions: list[dict[str, Any]] | None = None
    is_active: bool | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class AutomationDryRunRequest(BaseModel):
    trigger_key: str | None = None
    trigger_payload: dict[str, Any] = Field(default_factory=dict)


class AutomationScheduleParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class AutomationRuleDeleteRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


@router.get("/catalog")
async def automation_catalog(_: User = Depends(admin_user)) -> dict[str, Any]:
    return await get_automation_service().catalog()


async def _current_history_admin(session: AsyncSession, user: User) -> None:
    try:
        await load_active_admin(session, user.id, auth_version=user.auth_session_version)
    except MutationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/runs")
async def list_automation_runs(
    limit: int = Query(default=25, ge=1, le=100),
    before_id: uuid.UUID | None = None,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _current_history_admin(session, user)
    try:
        rows, cursor = await AutomationRunStore().read_page(session, limit=limit, before_id=before_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"items": [serialize_run(row) for row in rows], "next_cursor": str(cursor) if cursor else None}


@router.get("/runs/{run_id}")
async def get_automation_run(
    run_id: uuid.UUID,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    await _current_history_admin(session, user)
    try:
        row = await AutomationRunStore().read_detail(session, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return serialize_run(row)


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
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    await require_confirmation(
        session,
        user=user,
        action="automation_rule.create",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
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
    except (AutomationError, MutationError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403 if isinstance(exc, MutationError) else 400, detail=str(exc)) from exc


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
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    await require_confirmation(
        session,
        user=user,
        action="automation_rule.update",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
    )
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
    except (AutomationError, MutationError) as exc:
        await session.rollback()
        raise HTTPException(status_code=403 if isinstance(exc, MutationError) else 400, detail=str(exc)) from exc


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation_rule(
    rule_id: uuid.UUID,
    request: AutomationRuleDeleteRequest | None = None,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    await require_confirmation(
        session,
        user=user,
        action="automation_rule.delete",
        payload={"rule_id": str(rule_id)},
        confirmation_token=request.confirmation_token if request else None,
    )
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
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid automation webhook JSON.",
        ) from exc
    if not isinstance(payload, dict):
        payload = {"value": payload}
    source_ip = request.client.host if request.client else "unknown"
    try:
        return await get_automation_service().handle_webhook(
            webhook_key,
            payload,
            source_ip=source_ip,
            raw_body=raw_body,
            signature=request.headers.get(WEBHOOK_SIGNATURE_HEADER),
            signature_timestamp=request.headers.get(WEBHOOK_TIMESTAMP_HEADER),
            nonce=request.headers.get(WEBHOOK_NONCE_HEADER),
        )
    except AutomationError as exc:
        status_code = status.HTTP_429_TOO_MANY_REQUESTS if "rate limit" in str(exc).lower() else status.HTTP_403_FORBIDDEN
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


async def get_rule_or_404(session: AsyncSession, rule_id: uuid.UUID) -> AutomationRule:
    rule = await session.get(AutomationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Automation rule not found.")
    return rule


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
