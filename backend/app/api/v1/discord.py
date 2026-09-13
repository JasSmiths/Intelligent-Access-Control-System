from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.confirmations import send_confirmed_notification
from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import MessagingIdentity, Person, User
from app.modules.notifications.base import NotificationContext
from app.services.discord_messaging import get_discord_messaging_service, load_current_discord_config
from app.services.messaging.incoming_messages import IncomingMessageStore
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, actor_from_user, emit_audit_log

router = APIRouter()


class DiscordTestRequest(BaseModel):
    channel_id: str | None = Field(default=None, max_length=80)
    message: str = Field(default="IACS Discord integration test", max_length=500)
    confirmation_token: str | None = Field(default=None, max_length=160)


class DiscordIdentityUpdate(BaseModel):
    user_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None


@router.get("/status")
async def discord_status(_: User = Depends(admin_user)) -> dict[str, Any]:
    return await get_discord_messaging_service().status()


@router.get("/channels")
async def discord_channels(_: User = Depends(admin_user)) -> dict[str, Any]:
    return {"channels": await get_discord_messaging_service().available_channels()}


@router.get("/identities")
async def discord_identities(
    _: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    identities = (
        await session.scalars(
            select(MessagingIdentity)
            .options(selectinload(MessagingIdentity.user), selectinload(MessagingIdentity.person))
            .where(MessagingIdentity.provider == "discord")
            .order_by(MessagingIdentity.last_seen_at.desc().nullslast(), MessagingIdentity.provider_display_name)
        )
    ).all()
    return {"identities": [_serialize_identity(identity) for identity in identities]}


@router.patch("/identities/{identity_id}")
async def update_discord_identity(
    identity_id: uuid.UUID,
    request: DiscordIdentityUpdate,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    identity = await session.get(MessagingIdentity, identity_id)
    if not identity or identity.provider != "discord":
        raise HTTPException(status_code=404, detail="Discord identity not found.")
    linked_user = await session.get(User, request.user_id) if request.user_id else None
    linked_person = await session.get(Person, request.person_id) if request.person_id else None
    if request.user_id and not linked_user:
        raise HTTPException(status_code=404, detail="Linked user not found.")
    if request.person_id and not linked_person:
        raise HTTPException(status_code=404, detail="Linked person not found.")
    identity.user_id = request.user_id
    identity.person_id = request.person_id
    await session.commit()
    await session.refresh(identity, attribute_names=["user", "person"])
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="discord.identity.link",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="MessagingIdentity",
        target_id=str(identity.id),
        target_label=identity.provider_display_name,
        metadata={
            "provider": "discord",
            "provider_user_id": identity.provider_user_id,
            "user_id": str(identity.user_id) if identity.user_id else None,
            "person_id": str(identity.person_id) if identity.person_id else None,
        },
    )
    return _serialize_identity(identity)


@router.post("/test")
async def send_discord_test(
    request: DiscordTestRequest,
    user: User = Depends(admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, bool]:
    confirmation_payload = request.model_dump(exclude={"confirmation_token"}, exclude_none=True)
    config = await load_current_discord_config()
    channel_id = str(request.channel_id or config.default_notification_channel_id or "").strip()
    if not channel_id:
        raise HTTPException(status_code=400, detail="Select a Discord channel or configure a default channel.")
    if not channel_id.isdecimal():
        raise HTTPException(
            status_code=400,
            detail="Use the numeric Discord channel ID or pick a discovered channel from the Discord settings panel.",
        )
    await send_confirmed_notification(
        session,
        user=user,
        action="discord.test_notification",
        payload=confirmation_payload,
        confirmation_token=request.confirmation_token,
        context=NotificationContext(
            event_type="integration_test",
            subject="IACS Discord integration test",
            severity="info",
            facts={},
        ),
        rules_override=[
            {
                "id": "discord-integration-test",
                "name": "IACS Discord integration test",
                "trigger_event": "integration_test",
                "conditions": [],
                "actions": [
                    {
                        "id": "discord-integration-test-action",
                        "type": "discord",
                        "target_mode": "selected",
                        "target_ids": [f"discord:{channel_id}"],
                        "title_template": "IACS Discord integration test",
                        "message_template": request.message,
                    }
                ],
                "is_active": True,
            }
        ],
    )
    return {"ok": True}


def _serialize_identity(identity: MessagingIdentity) -> dict[str, Any]:
    linked_user = identity.user
    linked_person = identity.person
    return {
        "id": str(identity.id),
        "provider": identity.provider,
        "provider_user_id": identity.provider_user_id,
        "provider_display_name": identity.provider_display_name,
        "user_id": str(identity.user_id) if identity.user_id else None,
        "user_label": linked_user.full_name if linked_user else None,
        "person_id": str(identity.person_id) if identity.person_id else None,
        "person_label": linked_person.display_name if linked_person else None,
        "last_seen_at": identity.last_seen_at.isoformat() if identity.last_seen_at else None,
        "metadata": identity.metadata_ or {},
    }


@router.get("/incoming")
async def incoming_messages(
    _: User = Depends(admin_user), limit: int = Query(default=25, ge=1, le=100),
    before_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    try:
        return await IncomingMessageStore().recovery_page(provider="discord", limit=limit, before_id=before_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Incoming message cursor not found.") from exc


@router.get("/incoming/{incoming_id}")
async def incoming_message(incoming_id: uuid.UUID, _: User = Depends(admin_user)) -> dict[str, Any]:
    try:
        return await IncomingMessageStore().recovery_detail(incoming_id, provider="discord")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Incoming message not found.") from exc
