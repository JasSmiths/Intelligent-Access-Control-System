from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import User
from app.modules.notifications.base import NotificationDeliveryError
from app.services.action_confirmations import ActionConfirmationError, consume_action_confirmation
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    emit_audit_log,
)
from app.services.whatsapp_messaging import get_whatsapp_messaging_service, load_whatsapp_config

router = APIRouter()


class WhatsAppTestRequest(BaseModel):
    phone_number: str | None = Field(default=None, max_length=40)
    message: str = Field(default="IACS WhatsApp test from Alfred.", max_length=1024)
    values: dict[str, Any] = Field(default_factory=dict)
    confirmation_token: str | None = Field(default=None, max_length=160)


@router.get("/status")
async def whatsapp_status(_: User = Depends(admin_user)) -> dict[str, Any]:
    return await get_whatsapp_messaging_service().status()


@router.get("/admin-targets")
async def whatsapp_admin_targets(_: User = Depends(admin_user)) -> dict[str, Any]:
    return {"targets": await get_whatsapp_messaging_service().available_admin_targets()}


@router.post("/test")
async def send_whatsapp_test(
    request: WhatsAppTestRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, bool]:
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    try:
        await consume_action_confirmation(
            session,
            user=user,
            action="whatsapp.test_message",
            payload=confirmation_payload,
            confirmation_token=request.confirmation_token,
        )
    except ActionConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    target = request.phone_number or user.mobile_phone_number
    if not target:
        raise HTTPException(status_code=400, detail="Provide a WhatsApp phone number or add one to your Admin profile.")
    config = await load_whatsapp_config(request.values)
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Enable WhatsApp before sending a test message.")
    if not config.access_token or not config.phone_number_id:
        raise HTTPException(status_code=400, detail="WhatsApp access token and phone number ID are required.")
    try:
        await get_whatsapp_messaging_service().send_text_message(target, request.message, config=config)
    except NotificationDeliveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="whatsapp.test_message",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="WhatsApp",
        metadata={"target": "current_admin" if not request.phone_number else "manual_phone"},
    )
    return {"ok": True}
