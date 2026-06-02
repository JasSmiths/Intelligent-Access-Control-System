from __future__ import annotations

# ruff: noqa: F403,F405

import hashlib
import hmac
from typing import Any

from app.core.logging import get_logger
from app.modules.notifications.base import NotificationDeliveryError
from app.services import whatsapp_messaging as wm
from app.services.messaging.whatsapp_helpers import *  # noqa: F401,F403
from app.services.type_helpers import as_dict, as_dict_list, as_list

logger = get_logger(__name__)

class WhatsAppWebhookMixin:
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
    ) -> None:
        config = await wm.load_whatsapp_config()
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
                        self._last_error = f"Message failed: {error_summaries[0]}"
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
                        await self._update_visitor_concierge_status_for_phone(
                            recipient,
                            whatsapp_send_failure_status(NotificationDeliveryError(error_text)),
                            detail=error_text[:500],
                            error=error_text[:500],
                        )
                    elif status in {"delivered", "read"} and recipient:
                        await self._update_visitor_delivery_status_for_phone(
                            recipient,
                            "message_read" if status == "read" else "message_received",
                            message_id=message_id,
                        )
                    await wm.event_bus.publish(
                        "whatsapp.message_status",
                        {
                            "message_id": message_id,
                            "status": status,
                            "recipient_id": masked_phone_number(status_payload.get("recipient_id")),
                            "errors": error_summaries,
                        },
                    )
                for message in messages:
                    if isinstance(message, dict):
                        await self._handle_incoming_message(
                            message,
                            contacts=contacts,
                            phone_number_id=phone_number_id,
                            config=config,
                            signature_verified=signature_verified,
                        )
