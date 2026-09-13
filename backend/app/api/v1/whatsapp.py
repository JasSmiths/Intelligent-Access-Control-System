import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.services.messaging.incoming_messages import IncomingMessageStore
from app.models import User
from app.modules.notifications.base import NotificationContext
from app.api.confirmations import send_confirmed_notification
from app.services.messaging.whatsapp_delivery import get_whatsapp_delivery_service
from app.services.messaging.whatsapp_configuration import load_whatsapp_config

router = APIRouter()


class WhatsAppTestRequest(BaseModel):
    phone_number: str | None = Field(default=None, max_length=40)
    message: str = Field(default="IACS WhatsApp test from Alfred.", max_length=1024)
    values: dict[str, Any] = Field(default_factory=dict)
    confirmation_token: str | None = Field(default=None, max_length=160)


@router.get("/status")
async def whatsapp_status(_: User = Depends(admin_user)) -> dict[str, Any]:
    return await get_whatsapp_delivery_service().status()


@router.get("/admin-targets")
async def whatsapp_admin_targets(_: User = Depends(admin_user)) -> dict[str, Any]:
    return {"targets": await get_whatsapp_delivery_service().available_admin_targets()}


@router.post("/test")
async def send_whatsapp_test(
    request: WhatsAppTestRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    target = request.phone_number or user.mobile_phone_number
    if not target:
        raise HTTPException(status_code=400, detail="Provide a WhatsApp phone number or add one to your Admin profile.")
    config = await load_whatsapp_config(request.values)
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Enable WhatsApp before sending a test message.")
    if not config.access_token or not config.phone_number_id:
        raise HTTPException(status_code=400, detail="WhatsApp access token and phone number ID are required.")
    result = await send_confirmed_notification(
        session, user=user, action="whatsapp.test_message",
        payload=request.model_dump(exclude={"confirmation_token"}, exclude_none=True),
        confirmation_token=request.confirmation_token,
        context=NotificationContext("integration_test", "WhatsApp test", "info", {}),
        direct_action={"type": "whatsapp", "delivery_mode": "literal", "target": target,
            "title": "", "message": request.message}, ephemeral_config=config,
    )
    return {"ok": True, "notification_run_id": result.run_id}


@router.get("/incoming")
async def incoming_messages(
    _: User = Depends(admin_user), limit: int = Query(default=25, ge=1, le=100),
    before_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    try:
        return await IncomingMessageStore().recovery_page(provider="whatsapp", limit=limit, before_id=before_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Incoming message cursor not found.") from exc


@router.get("/incoming/{incoming_id}")
async def incoming_message(incoming_id: uuid.UUID, _: User = Depends(admin_user)) -> dict[str, Any]:
    try:
        return await IncomingMessageStore().recovery_detail(incoming_id, provider="whatsapp")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Incoming message not found.") from exc
