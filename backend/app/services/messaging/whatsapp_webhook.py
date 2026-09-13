from __future__ import annotations

import hashlib
import hmac
from typing import Any


from app.core.logging import get_logger
from app.modules.notifications.base import NotificationDeliveryError
from app.services.event_bus import event_bus
from app.services.messaging.whatsapp_configuration import load_whatsapp_config
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig
from app.services.messaging.incoming_messages import IncomingMessageStore
from app.services.messaging.identities import get_whatsapp_identity_service
from app.services.messaging.whatsapp_delivery import get_whatsapp_delivery_service
from app.services.messaging.whatsapp_incoming import get_whatsapp_incoming_dispatcher
from app.services.visitor_conversations import get_visitor_conversation_service
from app.services.messaging.whatsapp_helpers import (
    VISITOR_TEXT_DEBOUNCE_SECONDS, contact_display_name, contact_wa_id, extract_message_text,
    masked_phone_number,
    normalize_whatsapp_phone_number,
    parse_whatsapp_timestamp,
    payload_shape,
    whatsapp_send_failure_status,
)
from app.services.type_helpers import as_dict, as_dict_list, as_list

logger = get_logger(__name__)

class WhatsAppWebhookService:
    def __init__(self, *, store=None, identities=None, conversations=None, delivery=None, dispatcher=None):
        self._store = store or IncomingMessageStore()
        self._identities = identities or get_whatsapp_identity_service()
        self._state = conversations or get_visitor_conversation_service()
        self._delivery = delivery or get_whatsapp_delivery_service()
        self._dispatcher = dispatcher or get_whatsapp_incoming_dispatcher()

    def validate_signature(self, raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
        if not app_secret:
            return False
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        supplied = signature_header.split("=", 1)[1].strip().lower()
        digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, digest)

    async def handle_webhook_payload(
        self,
        payload: dict[str, Any],
        *,
        signature_verified: bool,
        unsigned_allowed: bool,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> None:
        config = config or await load_whatsapp_config()
        if not config.enabled:
            logger.info("whatsapp_webhook_ignored_disabled", extra={"payload_shape": payload_shape(payload)})
            return
        if unsigned_allowed:
            logger.info("whatsapp_webhook_unsigned_accepted", extra={"payload_shape": payload_shape(payload)})

        entries = as_dict_list(payload.get("entry"))
        for entry in entries:
            changes = as_dict_list(entry.get("changes"))
            for change in changes:
                value = as_dict(change.get("value"))
                metadata = as_dict(value.get("metadata"))
                phone_number_id = str(metadata.get("phone_number_id") or "")
                if config.phone_number_id and phone_number_id != config.phone_number_id:
                    logger.info(
                        "whatsapp_webhook_ignored_phone_number",
                        extra={"phone_number_id": phone_number_id, "configured_phone_number_id": config.phone_number_id},
                    )
                    continue
                contacts = as_list(value.get("contacts"))
                messages = as_dict_list(value.get("messages"))
                statuses = as_dict_list(value.get("statuses"))
                for status_payload in statuses:
                    status = str(status_payload.get("status") or "")
                    message_id = str(status_payload.get("id") or "")
                    recipient = normalize_whatsapp_phone_number(status_payload.get("recipient_id"))
                    errors = as_dict_list(status_payload.get("errors"))
                    error_summaries = [
                        {
                            "code": error.get("code"),
                            "title": error.get("title"),
                            "message": error.get("message"),
                            "details": as_dict(error.get("error_data")).get("details"),
                        }
                        for error in errors
                    ]
                    logger.info(
                        "whatsapp_message_status",
                        extra={
                            "message_id": message_id,
                            "status": status,
                            "recipient_id": masked_phone_number(status_payload.get("recipient_id")),
                            "phone_number_id": phone_number_id,
                            "conversation_id": str(
                                as_dict(status_payload.get("conversation")).get("id") or ""
                            ),
                            "errors": error_summaries,
                        },
                    )
                    if status == "failed" and error_summaries:
                        self._delivery._transport.last_error = f"Message failed: {error_summaries[0]}"
                        error_text = "; ".join(
                            str(part)
                            for part in [
                                error_summaries[0].get("code"),
                                error_summaries[0].get("title"),
                                error_summaries[0].get("message"),
                                error_summaries[0].get("details"),
                            ]
                            if part
                        )
                        await self._state.update_visitor_concierge_status_for_phone(
                            recipient,
                            whatsapp_send_failure_status(NotificationDeliveryError(error_text)),
                            detail=error_text[:500],
                            error=error_text[:500],
                        )
                    elif status in {"delivered", "read"} and recipient:
                        await self._state.update_visitor_delivery_status_for_phone(
                            recipient,
                            "message_read" if status == "read" else "message_received",
                            message_id=message_id,
                        )
                    await event_bus.publish(
                        "whatsapp.message_status",
                        {
                            "message_id": message_id,
                            "status": status,
                            "recipient_id": masked_phone_number(status_payload.get("recipient_id")),
                            "errors": error_summaries,
                        },
                    )
                for message in messages:
                    await self._accept_incoming_provider_message(message, contacts=contacts,
                        phone_number_id=phone_number_id, config=config, signature_verified=signature_verified)

    async def _accept_incoming_provider_message(self, message: dict[str, Any], *, contacts: list[Any],
        phone_number_id: str, config: WhatsAppIntegrationConfig, signature_verified: bool) -> str:
        message_id = str(message.get("id") or "").strip()
        sender = normalize_whatsapp_phone_number(message.get("from") or contact_wa_id(contacts))
        if not message_id or not sender:
            raise ValueError("WhatsApp incoming messages require a provider identity and sender.")
        admin = await self._identities.admin_for_phone(sender)
        if admin:
            binding = {"kind": "admin", "user_id": str(admin.id), "auth_version": admin.auth_session_version}
        else:
            visitor, _state = await self._state.visitor_pass_for_phone(sender)
            binding = {"kind": "visitor", "pass_id": str(visitor.id)} if visitor else {"kind": "denied"}
        binding["business_account_id"] = config.business_account_id
        normalized = {key: message[key] for key in ("id", "type", "timestamp", "text", "button", "interactive", "reaction") if key in message}
        normalized["from"] = sender
        # Unsupported media retains its type/id, never downloaded content.
        command = extract_message_text(normalized).strip().casefold() in {"begin", "start"}
        batchable = normalized.get("type") == "text" and not command
        envelope = {"message": normalized, "contacts": [{"profile": {"name": contact_display_name(contacts)}}],
            "signature_verified": signature_verified, "batchable": batchable}
        async with self._store.sessions() as session:
            identity = await self._store.accept_in_session(session, provider="whatsapp", provider_message_id=message_id,
                provider_channel_id=phone_number_id, author_provider_id=sender, envelope=envelope, routing_context=binding,
                received_at=parse_whatsapp_timestamp(message.get("timestamp")),
                debounce_seconds=VISITOR_TEXT_DEBOUNCE_SECONDS if binding["kind"] == "visitor" and batchable else 0)
            await session.commit()
        self._dispatcher.wake()
        return str(identity)


_webhook_service: WhatsAppWebhookService | None = None

def get_whatsapp_webhook_service() -> WhatsAppWebhookService:
    global _webhook_service
    if _webhook_service is None:
        _webhook_service = WhatsAppWebhookService()
    return _webhook_service
