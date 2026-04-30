from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import admin_user
from app.db.session import get_db_session
from app.models import MessagingIdentity, Person, User
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services.discord_messaging import get_discord_messaging_service
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, actor_from_user, emit_audit_log

router = APIRouter()


class DiscordTestRequest(BaseModel):
    channel_id: str | None = Field(default=None, max_length=80)
    message: str = Field(default="IACS Discord integration test", max_length=500)


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
) -> dict[str, bool]:
    service = get_discord_messaging_service()
    status = await service.status()
    channel_id = request.channel_id or str(status.get("default_notification_channel_id") or "")
    if not channel_id:
        raise HTTPException(status_code=400, detail="Select a Discord channel or configure a default channel.")
    try:
        await service.send_notification_to_channels(
            [channel_id],
            "IACS Discord integration test",
            request.message,
            NotificationContext(
                event_type="integration_test",
                subject="IACS Discord integration test",
                severity="info",
                facts={},
            ),
        )
    except NotificationDeliveryError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{exc} Use the numeric Discord channel ID or pick a discovered channel from the Discord settings panel.",
        ) from exc
    emit_audit_log(
        category=TELEMETRY_CATEGORY_INTEGRATIONS,
        action="discord.test_notification",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="Discord",
        target_id=channel_id,
        target_label="Discord test notification",
        metadata={"channel_id": channel_id},
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
