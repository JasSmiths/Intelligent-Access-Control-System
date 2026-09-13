"""Requester-bound WhatsApp reply attempts; no routing or automatic retry."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProcessedMessagingMessage, User, VisitorPass
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig, WhatsAppSendError, WhatsAppTransport
from app.core.task_lifecycle import drain_owned
from app.services.messaging.incoming_messages import IncomingMessageClaim, IncomingMessageClaimLost, IncomingMessageStore
from app.services.messaging.whatsapp_configuration import load_whatsapp_config
from app.services.mutation_context import load_active_admin
from app.services.visitor_passes import get_visitor_pass_service
from app.services.workflows.visitor_conversations import normalize_contact_phone


@dataclass(frozen=True)
class WhatsAppSender:
    config: WhatsAppIntegrationConfig
    admin: User | None = None
    visitor_pass: VisitorPass | None = None
    visitor_state: str | None = None


async def current_whatsapp_sender(session: AsyncSession, row: ProcessedMessagingMessage) -> WhatsAppSender:
    """Recheck saved server authority without rebinding an accepted message."""
    config = await load_whatsapp_config(session=session)
    if not config.configured or row.provider != "whatsapp" or row.provider_channel_id != config.phone_number_id:
        raise IncomingMessageClaimLost("whatsapp_configuration_changed")
    binding = row.routing_context or {}
    if binding.get("business_account_id", "") != config.business_account_id:
        raise IncomingMessageClaimLost("whatsapp_configuration_changed")
    if binding.get("kind") == "admin":
        version = binding.get("auth_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise IncomingMessageClaimLost("whatsapp_actor_changed")
        admin = await load_active_admin(session, binding.get("user_id"), auth_version=version, lock=True)
        if normalize_contact_phone(admin.mobile_phone_number) != row.author_provider_id:
            raise IncomingMessageClaimLost("whatsapp_actor_changed")
        return WhatsAppSender(config, admin=admin)
    if binding.get("kind") == "visitor":
        try:
            identity = uuid.UUID(str(binding.get("pass_id")))
        except (TypeError, ValueError, AttributeError) as exc:
            raise IncomingMessageClaimLost("whatsapp_visitor_binding_invalid") from exc
        visitor = await session.scalar(select(VisitorPass).where(VisitorPass.id == identity)
            .with_for_update().execution_options(populate_existing=True))
        if (visitor is None or visitor.pass_type != VisitorPassType.DURATION
                or normalize_contact_phone(visitor.visitor_phone) != row.author_provider_id):
            raise IncomingMessageClaimLost("whatsapp_visitor_changed")
        now = await IncomingMessageStore._now(session)
        status = get_visitor_pass_service().status_for(visitor, now)
        state = "active" if status == VisitorPassStatus.ACTIVE else "scheduled" if status == VisitorPassStatus.SCHEDULED else "expired"
        return WhatsAppSender(config, visitor_pass=visitor, visitor_state=state)
    if binding.get("kind") == "denied":
        return WhatsAppSender(config)
    raise IncomingMessageClaimLost("whatsapp_actor_binding_invalid")


class WhatsAppReplyCheckpoint:
    def __init__(self, claim: IncomingMessageClaim, transport: WhatsAppTransport, *, store: IncomingMessageStore | None = None):
        self.claim = claim
        self.transport = transport
        self.store = store or IncomingMessageStore()
        self.next_index = 0

    async def send(self, payload: dict[str, Any], *, terminal_notice: bool = False,
        visitor_pass_id: str | uuid.UUID | None = None) -> dict[str, Any]:
        recipient = normalize_contact_phone(payload.get("to"))
        index = self.next_index
        async with self.store.sessions() as session:
            rows, _now = await self.store.owned(session, self.claim)
            sender = await current_whatsapp_sender(session, rows[0])
            if sender.admin is None and sender.visitor_pass is None:
                raise IncomingMessageClaimLost("whatsapp_sender_denied")
            if sender.visitor_pass is not None:
                if recipient != self.claim.author_id:
                    raise IncomingMessageClaimLost("whatsapp_reply_recipient_changed")
                if sender.visitor_state == "expired" and not terminal_notice:
                    raise IncomingMessageClaimLost("whatsapp_visitor_no_longer_valid")
            elif visitor_pass_id is None:
                if recipient != self.claim.author_id:
                    raise IncomingMessageClaimLost("whatsapp_reply_recipient_changed")
            else:
                # Admin decision notices still bind to the exact current visitor,
                # rather than a phone captured before a concurrent pass edit.
                visitor = await session.scalar(select(VisitorPass).where(VisitorPass.id == uuid.UUID(str(visitor_pass_id)))
                    .with_for_update().execution_options(populate_existing=True))
                if (visitor is None or visitor.pass_type != VisitorPassType.DURATION
                        or normalize_contact_phone(visitor.visitor_phone) != recipient):
                    raise IncomingMessageClaimLost("whatsapp_reply_recipient_changed")
                status = get_visitor_pass_service().status_for(visitor, await self.store._now(session))
                if status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED} and not terminal_notice:
                    raise IncomingMessageClaimLost("whatsapp_visitor_no_longer_valid")
            await self.store.begin_reply_in_session(session, self.claim, index=index, recipient=recipient, payload=payload)
            await session.commit()
        self.next_index += 1
        try:
            result = await self.transport.send(sender.config, payload)
        except asyncio.CancelledError:
            # The persisted attempt already prevents replay after a hard crash.
            # Cooperative cancellation also retains the unknown receipt now.
            await drain_owned(self.store.record_reply_outcome(self.claim, index, delivery="unknown"))
            raise
        except Exception as exc:
            delivery = exc.delivery if isinstance(exc, WhatsAppSendError) else "unknown"
            await self.store.record_reply_outcome(self.claim, index, delivery=delivery)
            raise
        if not await self.store.record_reply_outcome(self.claim, index, delivery="accepted", result=result):
            raise IncomingMessageClaimLost("whatsapp_reply_receipt_lost")
        return result
