"""Concrete WhatsApp inbox worker; accepted input survives missed wakeups."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.core.logging import get_logger
from app.core.recovery_hold import is_recovery_hold
from app.core.task_lifecycle import drain_owned
from app.services.messaging.identities import get_whatsapp_identity_service
from app.services.messaging.incoming_messages import IncomingMessageClaimLost, IncomingMessageStore
from app.services.messaging.visitor_conversation import WhatsAppVisitorConversationService
from app.services.messaging.whatsapp_delivery import WhatsAppDeliveryService, get_whatsapp_delivery_service
from app.services.messaging.whatsapp_helpers import extract_message_text
from app.services.messaging.whatsapp_replies import WhatsAppReplyCheckpoint, current_whatsapp_sender
from app.services.messaging.whatsapp_router import WhatsAppRouter
from app.services.mutation_context import MutationError
from app.services.visitor_conversations import get_visitor_conversation_service

logger = get_logger(__name__)
POLL_SECONDS = 2
HANDLER_TIMEOUT_SECONDS = 240


class WhatsAppIncomingDispatcher:
    def __init__(self, *, store: IncomingMessageStore | None = None, delivery: WhatsAppDeliveryService | None = None):
        self.store = store or IncomingMessageStore()
        self.delivery = delivery or get_whatsapp_delivery_service()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name="whatsapp-incoming")

    def wake(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        await drain_owned(self._stop())

    async def _stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _worker(self) -> None:
        while True:
            self._wake.clear()
            try:
                for _ in range(20):
                    if not await self.run_once():
                        break
            except Exception:
                logger.exception("whatsapp_incoming_tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), POLL_SECONDS)

    async def run_once(self, message_id=None) -> bool:
        if is_recovery_hold():
            return False
        claim = await self.store.claim(message_id, provider="whatsapp")
        if claim is None:
            return False
        identities, conversations = get_whatsapp_identity_service(), get_visitor_conversation_service()
        replies = WhatsAppReplyCheckpoint(claim, self.delivery._transport, store=self.store)
        delivery = WhatsAppDeliveryService(transport=self.delivery._transport,
            identities=identities, conversations=conversations, replies=replies)
        visitor = WhatsAppVisitorConversationService(delivery=delivery, identities=identities, conversations=conversations)
        router = WhatsAppRouter(delivery=delivery, visitor=visitor, identities=identities)
        try:
            async with self.store.sessions() as session:
                rows, _now = await self.store.owned(session, claim)
                rows.sort(key=lambda row: claim.message_ids.index(row.id))
                sender = await current_whatsapp_sender(session, rows[0])
                messages = [(row.envelope or {})["message"] for row in rows]
                envelope = rows[-1].envelope
                await session.commit()
            if is_recovery_hold():
                raise IncomingMessageClaimLost("recovery_hold")
            async with asyncio.timeout(HANDLER_TIMEOUT_SECONDS):
                # Keep every provider message/history entry. Only an already
                # claimed contiguous visitor text batch is combined for parsing.
                if len(messages) > 1 and sender.visitor_pass is not None:
                    for message in messages:
                        await visitor._record_inbound_visitor_message(sender.visitor_pass, message, sender=claim.author_id)
                    message = {**messages[-1], "text": {"body": "\n".join(extract_message_text(item) for item in messages)}}
                else:
                    message = messages[0]
                await router._handle_incoming_message(message, contacts=envelope.get("contacts", []),
                    phone_number_id=claim.channel_id or "", config=sender.config,
                    signature_verified=bool(envelope.get("signature_verified")), sender_state=sender,
                    history_recorded=len(messages) > 1 and sender.visitor_pass is not None)
            async with self.store.sessions() as session:
                await self.store.finish_handling_in_session(session, claim, router.result)
                await session.commit()
        except (IncomingMessageClaimLost, MutationError):
            await self.store.interrupt(claim, "sender_binding_changed", result=router.result)
        except asyncio.CancelledError:
            await self.store.interrupt(claim, "handler_interrupted", result=router.result)
            raise
        except Exception:
            await self.store.interrupt(claim, "handler_failed", result=router.result)
            logger.exception("whatsapp_incoming_handler_failed", extra={"incoming_id": str(claim.batch_id)})
        return True


_dispatcher: WhatsAppIncomingDispatcher | None = None


def get_whatsapp_incoming_dispatcher() -> WhatsAppIncomingDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = WhatsAppIncomingDispatcher()
    return _dispatcher
