from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any


from app.services.messaging.whatsapp_delivery import WhatsAppDeliveryService
from app.services.messaging.visitor_conversation import WhatsAppVisitorConversationService
from app.services.messaging.identities import WhatsAppIdentityService
from app.services.messaging.whatsapp_replies import WhatsAppSender
from app.core.logging import get_logger
from app.models import User
from app.modules.messaging.base import IncomingChatMessage
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig
from app.services.messaging.whatsapp_helpers import (
    ADMIN_ALFRED_FEEDBACK_PROMPT,
    ADMIN_ALFRED_FEEDBACK_STATE_KEY,
    WhatsAppConfirmation,
    WhatsAppReaction,
    contact_display_name,
    contact_wa_id,
    extract_message_text,
    feedback_rating_for_reaction,
    normalize_whatsapp_phone_number,
    parse_button_message,
    parse_confirmation_button_id,
    parse_reaction_message,
    parse_visitor_pass_timeframe_button_id,
    parse_whatsapp_timestamp,
)

logger = get_logger(__name__)

class WhatsAppRouter:
    def __init__(self, *, delivery: WhatsAppDeliveryService,
        visitor: WhatsAppVisitorConversationService, identities: WhatsAppIdentityService):
        self._delivery = delivery
        self._visitor = visitor
        self._identities = identities
        self.result: dict[str, Any] = {"kind": "message"}

    async def _handle_incoming_message(
        self,
        message: dict[str, Any],
        *,
        contacts: list[Any],
        phone_number_id: str,
        config: WhatsAppIntegrationConfig,
        signature_verified: bool,
        sender_state: WhatsAppSender,
        history_recorded: bool = False,
    ) -> None:
        if config.phone_number_id and phone_number_id != config.phone_number_id:
            logger.info(
                "whatsapp_message_ignored_phone_number",
                extra={"phone_number_id": phone_number_id, "configured_phone_number_id": config.phone_number_id},
            )
            return
        acknowledged = False

        async def acknowledge(*, show_typing: bool) -> None:
            nonlocal acknowledged
            if acknowledged:
                return
            acknowledged = True
            await self._delivery.mark_incoming_message_read(
                message.get("id"),
                config=config,
                show_typing=show_typing,
            )

        sender = normalize_whatsapp_phone_number(message.get("from") or contact_wa_id(contacts))
        if not sender:
            await acknowledge(show_typing=False)
            logger.info("whatsapp_message_ignored_missing_sender", extra={"message_id": str(message.get("id") or "")})
            return
        admin = sender_state.admin
        if not admin:
            visitor_pass, visitor_state = sender_state.visitor_pass, sender_state.visitor_state
            if visitor_pass and visitor_state in {"active", "scheduled"}:
                visitor_muted = await self._visitor._state.visitor_reply_is_muted(visitor_pass.id, sender)
                await acknowledge(show_typing=not visitor_muted)
                if visitor_muted:
                    if not history_recorded:
                        await self._visitor._record_inbound_visitor_message(visitor_pass, message, sender=sender)
                    return
                await self._visitor._handle_visitor_message(
                    message,
                    sender=sender,
                    visitor_pass=visitor_pass,
                    config=config, history_recorded=history_recorded,
                )
                return
            if visitor_pass and visitor_state == "expired":
                await acknowledge(show_typing=True)
                await self._visitor._send_terminal_visitor_pass_reply_once(visitor_pass, sender, config=config)
                await self._identities.audit_denied_sender(sender, message, reason="visitor_pass_expired")
                return
            await acknowledge(show_typing=False)
            await self._identities.audit_denied_sender(sender, message)
            return

        display_name = contact_display_name(contacts) or admin.full_name or admin.username
        await self._identities.ensure_admin_identity(admin, sender, display_name, phone_number_id, signature_verified)
        reaction = parse_reaction_message(message)
        if reaction:
            rating = feedback_rating_for_reaction(reaction)
            await acknowledge(show_typing=rating is not None)
            if rating == "down":
                await self._start_admin_feedback_followup(
                    reaction,
                    admin=admin,
                    display_name=display_name,
                    sender=sender,
                    phone_number_id=phone_number_id,
                )
            elif rating == "up":
                await self._submit_admin_reaction_feedback(
                    reaction,
                    admin=admin,
                    display_name=display_name,
                    sender=sender,
                    phone_number_id=phone_number_id,
                    rating=rating,
                )
            return
        timeframe_decision = parse_button_message(message, parse_visitor_pass_timeframe_button_id)
        if timeframe_decision:
            await acknowledge(show_typing=True)
            await self._visitor._handle_visitor_timeframe_admin_decision(
                timeframe_decision,
                sender,
                admin,
                config=config,
            )
            return
        confirmation = parse_button_message(message, parse_confirmation_button_id)
        if confirmation:
            await acknowledge(show_typing=True)
            await self._handle_confirmation_reply(confirmation, sender, admin, display_name, phone_number_id)
            return

        text = extract_message_text(message)
        if not text:
            await acknowledge(show_typing=True)
            await self._delivery.send_text_message(sender, "I can read WhatsApp text and confirmation buttons right now.")
            return

        await acknowledge(show_typing=True)
        incoming = IncomingChatMessage(
            provider="whatsapp",
            provider_message_id=str(message.get("id") or f"whatsapp-{uuid.uuid4().hex}"),
            provider_channel_id=phone_number_id or config.phone_number_id,
            author_provider_id=sender,
            author_display_name=display_name,
            text=text,
            is_direct_message=True,
            mentioned_bot=True,
            raw_payload={
                "message_id": str(message.get("id") or ""),
                "type": str(message.get("type") or ""),
                "phone_number_id": phone_number_id,
                "signature_verified": signature_verified,
            },
            received_at=parse_whatsapp_timestamp(message.get("timestamp")),
            author_is_provider_admin=True,
        )
        if await self._handle_admin_feedback_followup(
            incoming,
            admin=admin,
            sender=sender,
        ):
            return
        from app.services.messaging_bridge import messaging_bridge_service

        result = await messaging_bridge_service.handle_message(incoming, is_admin_hint=True)
        self.result = {"kind": "chat", "session_id": result.session_id,
            "confirmation_id": (result.pending_action or {}).get("confirmation_id")}
        if result.response_text:
            await self._delivery.send_text_message(sender, result.response_text)
        if result.pending_action:
            await self._delivery.send_confirmation_message(sender, result.pending_action)


    async def _handle_confirmation_reply(
        self,
        confirmation: WhatsAppConfirmation,
        sender: str,
        admin: User,
        display_name: str,
        phone_number_id: str,
    ) -> None:
        from app.services.chat import chat_service
        from app.services.messaging_bridge import naturalize_messaging_response

        result = await chat_service.handle_tool_confirmation(
            confirmation_id=confirmation.confirmation_id,
            decision=confirmation.decision,
            session_id=confirmation.session_id,
            user_id=str(admin.id),
            user_role=admin.role.value,
            client_context={
                "source": "messaging",
                "messaging_provider": "whatsapp",
                "provider_channel_id": phone_number_id,
                "is_direct_message": True,
                "author_display_name": display_name,
            },
        )
        self.result = {"kind": "approval", "session_id": confirmation.session_id,
            "confirmation_id": confirmation.confirmation_id}
        response_text = naturalize_messaging_response(result.text, result.tool_results, "confirmation")
        await self._delivery.send_text_message(sender, response_text)

    async def _start_admin_feedback_followup(
        self,
        reaction: WhatsAppReaction,
        *,
        admin: User,
        display_name: str,
        sender: str,
        phone_number_id: str,
    ) -> None:
        from app.services.chat import chat_service
        from app.services.messaging_bridge import deterministic_session_id

        incoming = self._admin_feedback_incoming(
            sender=sender,
            display_name=display_name,
            phone_number_id=phone_number_id,
            text="",
            provider_message_id=reaction.message_id,
        )
        session_id = deterministic_session_id(incoming)
        session_uuid = await chat_service._ensure_session(session_id)
        memory = await chat_service._load_memory(session_uuid)
        memory[ADMIN_ALFRED_FEEDBACK_STATE_KEY] = {
            "rating": "down",
            "reacted_message_id": reaction.message_id,
            "requested_at": datetime.now(tz=UTC).isoformat(),
            "actor_user_id": str(admin.id),
            "actor_role": admin.role.value,
        }
        await chat_service._save_memory(session_uuid, memory)
        await self._delivery.send_text_message(sender, ADMIN_ALFRED_FEEDBACK_PROMPT)

    async def _submit_admin_reaction_feedback(
        self,
        reaction: WhatsAppReaction,
        *,
        admin: User,
        display_name: str,
        sender: str,
        phone_number_id: str,
        rating: str,
    ) -> None:
        from app.services.alfred.feedback import AlfredFeedbackError, alfred_feedback_service
        from app.services.messaging_bridge import deterministic_session_id

        incoming = self._admin_feedback_incoming(
            sender=sender,
            display_name=display_name,
            phone_number_id=phone_number_id,
            text="",
            provider_message_id=reaction.message_id,
        )
        try:
            await alfred_feedback_service.submit_feedback_for_last_response(
                session_id=deterministic_session_id(incoming),
                rating=rating,
                reason="",
                ideal_answer="",
                source_channel="whatsapp",
                actor_user_id=str(admin.id),
                actor_role=admin.role.value,
            )
        except AlfredFeedbackError as exc:
            await self._delivery.send_text_message(sender, f"I could not attach that feedback: {exc}")
            return
        await self._delivery.send_text_message(sender, "Thanks, I logged that Alfred feedback.")

    async def _handle_admin_feedback_followup(
        self,
        incoming: IncomingChatMessage,
        *,
        admin: User,
        sender: str,
    ) -> bool:
        from app.services.alfred.feedback import AlfredFeedbackError, alfred_feedback_service, parse_feedback_command
        from app.services.chat import chat_service
        from app.services.messaging_bridge import deterministic_session_id

        session_id = deterministic_session_id(incoming)
        session_uuid = await chat_service._ensure_session(session_id)
        memory = await chat_service._load_memory(session_uuid)
        state = memory.get(ADMIN_ALFRED_FEEDBACK_STATE_KEY)
        if not isinstance(state, dict):
            return False

        text = incoming.text.strip()
        if text.lower() in {"cancel", "never mind", "nevermind", "ignore it", "leave it"}:
            memory.pop(ADMIN_ALFRED_FEEDBACK_STATE_KEY, None)
            await chat_service._save_memory(session_uuid, memory)
            await self._delivery.send_text_message(sender, "No problem, I won't log feedback for that one.")
            return True

        feedback_command = parse_feedback_command(f"thumbs down {text}") or {
            "rating": "down",
            "reason": text,
            "ideal_answer": "",
        }
        reason = (feedback_command.get("reason") or "").strip()
        ideal_answer = (feedback_command.get("ideal_answer") or "").strip()
        if not reason:
            reason = "The previous answer needed correction."

        try:
            feedback = await alfred_feedback_service.submit_feedback_for_last_response(
                session_id=session_id,
                rating=str(state.get("rating") or "down"),
                reason=reason,
                ideal_answer=ideal_answer,
                source_channel="whatsapp",
                actor_user_id=str(admin.id),
                actor_role=admin.role.value,
            )
        except AlfredFeedbackError as exc:
            memory.pop(ADMIN_ALFRED_FEEDBACK_STATE_KEY, None)
            await chat_service._save_memory(session_uuid, memory)
            await self._delivery.send_text_message(sender, f"I could not attach that feedback: {exc}")
            return True

        memory.pop(ADMIN_ALFRED_FEEDBACK_STATE_KEY, None)
        await chat_service._save_memory(session_uuid, memory)
        corrected = str(feedback.get("corrected_answer") or "").strip()
        response_text = "Thanks, I logged that Alfred feedback."
        if corrected:
            response_text = f"{response_text}\n\nCorrected answer:\n{corrected}"
        await self._delivery.send_text_message(sender, response_text)
        return True

    def _admin_feedback_incoming(
        self,
        *,
        sender: str,
        display_name: str,
        phone_number_id: str,
        text: str,
        provider_message_id: str,
    ) -> IncomingChatMessage:
        return IncomingChatMessage(
            provider="whatsapp",
            provider_message_id=provider_message_id or f"whatsapp-{uuid.uuid4().hex}",
            provider_channel_id=phone_number_id,
            author_provider_id=sender,
            author_display_name=display_name,
            text=text,
            is_direct_message=True,
            mentioned_bot=True,
            raw_payload={"type": "reaction_feedback"},
            received_at=datetime.now(tz=UTC),
            author_is_provider_admin=True,
        )
