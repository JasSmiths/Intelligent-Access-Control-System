"""Current WhatsApp account binding and notification-recipient selection.

Routing and delivery depend on this concrete directory. Neither owns the other's
policy, and provider I/O is absent from account/phone lookup and denied auditing.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import MessagingIdentity, User
from app.models.enums import UserRole
from app.services.messaging.whatsapp_helpers import coerce_uuid, masked_phone_number, render_token_template
from app.services.workflows.visitor_conversations import (
    normalize_contact_phone as normalize_whatsapp_phone_number,
)
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log

logger = get_logger(__name__)

class WhatsAppIdentityService:
    async def ensure_admin_identity(
        self,
        admin: User,
        sender: str,
        display_name: str,
        phone_number_id: str,
        signature_verified: bool,
    ) -> None:
        now = datetime.now(tz=UTC)
        async with AsyncSessionLocal() as session:
            identity = await session.scalar(
                select(MessagingIdentity)
                .where(MessagingIdentity.provider == "whatsapp")
                .where(MessagingIdentity.provider_user_id == sender)
            )
            if not identity:
                identity = MessagingIdentity(
                    provider="whatsapp",
                    provider_user_id=sender,
                    provider_display_name=display_name,
                    metadata_={},
                )
                session.add(identity)
            identity.provider_display_name = display_name
            identity.user_id = admin.id
            identity.person_id = admin.person_id
            identity.last_seen_at = now
            identity.metadata_ = {
                **(identity.metadata_ or {}),
                "phone_number_id": phone_number_id,
                "last_provider_admin": True,
                "signature_verified": signature_verified,
            }
            await session.commit()


    async def audit_denied_sender(self, sender: str, message: dict[str, Any], *, reason: str = "unknown_admin") -> None:
        logger.info(
            "whatsapp_message_denied_unknown_admin",
            extra={
                "sender": masked_phone_number(sender),
                "message_id": str(message.get("id") or ""),
                "message_type": str(message.get("type") or ""),
                "reason": reason,
            },
        )
        async with AsyncSessionLocal() as session:
            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="whatsapp.message.denied",
                actor="WhatsApp Webhook",
                target_entity="WhatsAppMessage",
                target_id=str(message.get("id") or ""),
                outcome="denied",
                level="warning",
                metadata={
                    "sender": masked_phone_number(sender),
                    "message_type": str(message.get("type") or ""),
                    "reason": reason,
                },
            )
            await session.commit()


    async def admin_for_phone(self, phone: str) -> User | None:
        for user in await self.admin_users_with_phone():
            if normalize_whatsapp_phone_number(user.mobile_phone_number) == phone:
                return user
        return None


    async def admin_users_with_phone(self) -> list[User]:
        async with AsyncSessionLocal() as session:
            users = (
                await session.scalars(
                    select(User)
                    .where(User.role == UserRole.ADMIN)
                    .where(User.is_active.is_(True))
                    .where(User.mobile_phone_number.is_not(None))
                    .order_by(User.full_name.asc(), User.username.asc())
                )
            ).all()
        return [user for user in users if normalize_whatsapp_phone_number(user.mobile_phone_number)]


    async def notification_recipient_bindings(self, action: dict[str, Any], variables: dict[str, str]) -> list[dict[str, Any]]:
        target_mode = str(action.get("target_mode") or "all")
        target_ids = [str(target) for target in action.get("target_ids", []) if str(target).strip()]
        users: list[User] = []
        if target_mode == "all" or not target_ids or "whatsapp:*" in target_ids:
            users.extend(await self.admin_users_with_phone())
        if target_mode != "all" and target_ids:
            parsed = [coerce_uuid(target.removeprefix("whatsapp:admin:")) for target in target_ids if target.startswith("whatsapp:admin:")]
            if any(parsed):
                async with AsyncSessionLocal() as session:
                    users.extend((await session.scalars(select(User).where(User.id.in_([value for value in parsed if value]),
                        User.role == UserRole.ADMIN, User.is_active.is_(True)))).all())
        recipients: dict[str, dict[str, Any]] = {}
        for user in users:
            phone = normalize_whatsapp_phone_number(user.mobile_phone_number)
            if phone and phone not in recipients:
                recipients[phone] = {"kind": "admin", "user_id": str(user.id),
                    "auth_version": user.auth_session_version, "phone": phone}
        if target_mode != "all" and target_ids:
            for target in target_ids:
                if target.startswith("whatsapp:number:"):
                    phone = normalize_whatsapp_phone_number(render_token_template(target.removeprefix("whatsapp:number:"), variables))
                    if phone:
                        # An explicitly configured number remains an explicit
                        # destination even when it also belongs to an Admin.
                        recipients[phone] = {"kind": "number", "phone": phone}
        return list(recipients.values())



def get_whatsapp_identity_service() -> WhatsAppIdentityService:
    return WhatsAppIdentityService()
