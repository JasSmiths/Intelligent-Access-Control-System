from __future__ import annotations

from typing import Any
from datetime import UTC, datetime
import uuid
from sqlalchemy.ext.asyncio import AsyncSession


from app.core.logging import get_logger
from app.models import VisitorPass
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services.event_bus import event_bus
from app.services.messaging.whatsapp_configuration import load_whatsapp_config
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig, WhatsAppTransport
from app.services.messaging.identities import WhatsAppIdentityService, get_whatsapp_identity_service
from app.services.messaging.whatsapp_replies import WhatsAppReplyCheckpoint
from app.services.visitor_conversations import VisitorConversationService, get_visitor_conversation_service
from app.services.mutation_context import MutationError, load_active_admin
from app.services.visitor_passes import get_visitor_pass_service
from app.services.messaging.whatsapp_helpers import (
    masked_phone_number,
    normalize_whatsapp_phone_number,
    visitor_pass_timeframe_notification_buttons,
    whatsapp_confirmation_button_id,
    whatsapp_response_message_id,
    visitor_pass_window_label,
)

logger = get_logger(__name__)

class WhatsAppDeliveryService:
    def __init__(self, *, transport: WhatsAppTransport | None = None,
        identities: WhatsAppIdentityService | None = None, conversations: VisitorConversationService | None = None,
        replies: WhatsAppReplyCheckpoint | None = None):
        self._transport = transport or WhatsAppTransport()
        self._identities = identities or get_whatsapp_identity_service()
        self._conversations = conversations or get_visitor_conversation_service()
        self._replies = replies

    async def reserve_outreach_in_session(
        self, session: AsyncSession, visitor_pass: VisitorPass, *,
        actor_user_id: uuid.UUID, auth_version: int, source: str,
    ) -> uuid.UUID | None:
        """The existing UI/Alfred creation transaction owns welcome intake."""
        from app.services.notification_runs import NotificationRunStore

        if visitor_pass.pass_type != VisitorPassType.DURATION or not visitor_pass.visitor_phone:
            return None
        await session.flush()
        visitor, origin = await self._conversations.prepare_manual_notification_origin(
            session, visitor_pass.id, actor_user_id=actor_user_id,
            auth_version=auth_version, kind="outreach", source=source,
        )
        config = await load_whatsapp_config(session=session)
        parameters = [str(visitor.visitor_name or "there")]
        if config.visitor_pass_template_name.strip().lower() != "iacs_visitor_welcome":
            parameters.append(visitor_pass_window_label(visitor))
        action = {"type": "whatsapp", "delivery_mode": "whatsapp_template", "target": origin["recipient"],
            "title": "", "message": "", "template_name": config.visitor_pass_template_name,
            "language_code": config.visitor_pass_template_language, "body_parameters": parameters}
        item = {"rule": {"id": "visitor-outreach", "name": "Visitor welcome", "trigger_event": "visitor_outreach"},
            "action": action, "state": "pending"}
        if not origin["recipient"]:
            item.update(state="skipped", reason="whatsapp_outreach_recipient_invalid")
        elif get_visitor_pass_service().status_for(visitor, datetime.now(tz=UTC)) not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
            item.update(state="skipped", reason="whatsapp_outreach_pass_invalid")
        elif not config.configured or not config.visitor_pass_template_name:
            item.update(state="skipped", reason="whatsapp_outreach_not_configured")
        return await NotificationRunStore().enqueue_prepared_in_session(session,
            {"event_type": "visitor_outreach", "subject": "Visitor welcome", "severity": "info", "facts": {},
                "visitor_conversation_origin": origin},
            run_id=uuid.uuid5(visitor.id, "visitor-outreach"), plan=[item])


    async def stop(self) -> None:
        await self._transport.stop()

    async def status(self) -> dict[str, Any]:
        config = await load_whatsapp_config()
        endpoints = await self.available_admin_targets()
        return {
            "enabled": config.enabled,
            "configured": config.configured,
            "webhook_configured": config.webhook_configured,
            "signature_configured": bool(config.app_secret),
            "phone_number_id": config.phone_number_id,
            "business_account_id": config.business_account_id,
            "graph_api_version": config.graph_api_version,
            "visitor_pass_template_name": config.visitor_pass_template_name,
            "visitor_pass_template_language": config.visitor_pass_template_language,
            "admin_target_count": sum(endpoint["id"].startswith("whatsapp:admin:") for endpoint in endpoints),
            "last_error": self._transport.last_error,
        }


    async def available_admin_targets(self) -> list[dict[str, str]]:
        users = await self._identities.admin_users_with_phone()
        if not users:
            return []
        endpoints = [{
            "id": "whatsapp:*",
            "provider": "WhatsApp",
            "label": "All Admins with mobile numbers",
            "detail": f"{len(users)} active Admin user{'s' if len(users) != 1 else ''}",
        }]
        endpoints.extend(
            {
                "id": f"whatsapp:admin:{user.id}",
                "provider": "WhatsApp",
                "label": user.full_name or user.username,
                "detail": masked_phone_number(user.mobile_phone_number),
            }
            for user in users
        )
        return endpoints


    async def send_text_message(self, to: str, body: str, *, config: WhatsAppIntegrationConfig | None = None,
        visitor_pass_id: str | None = None, terminal_notice: bool = False, record_history: bool = True) -> dict[str, Any]:
        return await self._send_whatsapp_message(
            to,
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "type": "text",
                "text": {"preview_url": False, "body": body[:4096]},
            },
            body[:4096],
            kind="text",
            config=config,
            visitor_pass_id=visitor_pass_id,
            terminal_notice=terminal_notice, record_history=record_history,
        )

    async def send_template_message(
        self,
        to: str,
        *,
        template_name: str,
        language_code: str,
        body_parameters: list[str],
        config: WhatsAppIntegrationConfig | None = None,
        record_history: bool = True,
    ) -> dict[str, Any]:
        name = str(template_name or "").strip()
        if not name:
            raise NotificationDeliveryError("WhatsApp visitor-pass template name is not configured.")
        language = str(language_code or "en").strip() or "en"
        parameters = [{"type": "text", "text": str(value)[:1024]} for value in body_parameters]
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": language},
                "components": [{"type": "body", "parameters": parameters}],
            },
        }
        return await self._send_whatsapp_message(
            to,
            payload,
            f"Template {name}: {' · '.join(str(value) for value in body_parameters if str(value).strip())}",
            kind="template",
            metadata={"template_name": name, "language_code": language},
            config=config, record_history=record_history,
        )

    async def send_interactive_buttons(
        self,
        to: str,
        body: str,
        buttons: list[dict[str, str]],
        *,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> dict[str, Any]:
        normalized_buttons: list[dict[str, Any]] = [
            {
                "type": "reply",
                "reply": {
                    "id": str(button.get("id") or "")[:256],
                    "title": str(button.get("title") or "Select")[:20],
                },
            }
            for button in buttons
            if str(button.get("id") or "").strip()
        ][:3]
        if not normalized_buttons:
            raise NotificationDeliveryError("WhatsApp interactive message needs at least one reply button.")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},
                "action": {"buttons": normalized_buttons},
            },
        }
        return await self._send_whatsapp_message(
            to,
            payload,
            body[:1024],
            kind="interactive",
            metadata={"buttons": [button["reply"]["title"] for button in normalized_buttons]},
            config=config,
        )

    async def send_confirmation_message(self, to: str, pending_action: dict[str, Any]) -> None:
        session_id = str(pending_action.get("session_id") or "")
        confirmation_id = str(pending_action.get("confirmation_id") or "")
        if not session_id or not confirmation_id:
            return
        title = str(pending_action.get("title") or "Confirm this action?")
        description = str(pending_action.get("description") or "Alfred needs confirmation before continuing.")
        body = "\n\n".join(part for part in [title, description] if part)
        await self.send_interactive_buttons(to, body, [
            {"id": whatsapp_confirmation_button_id("confirm", session_id, confirmation_id), "title": str(pending_action.get("confirm_label") or "Confirm")},
            {"id": whatsapp_confirmation_button_id("cancel", session_id, confirmation_id), "title": str(pending_action.get("cancel_label") or "Cancel")},
        ])

    async def prepare_notification_action(self, action: dict[str, Any], context: NotificationContext, *,
        variables: dict[str, str] | None = None) -> dict[str, Any]:
        return {**action, "frozen_whatsapp_recipients": await self._identities.notification_recipient_bindings(action, variables or {})}

    async def authorize_notification_action_in_session(self, session: AsyncSession, action: dict[str, Any]) -> str | None:
        recipients = action.get("frozen_whatsapp_recipients")
        if not isinstance(recipients, list) or not recipients:
            return "whatsapp_frozen_recipients_unavailable"
        if any(not isinstance(item, dict) for item in recipients):
            return "whatsapp_frozen_recipients_invalid"
        for item in sorted(recipients, key=lambda item: str(item.get("user_id") or "")):
            phone = item.get("phone")
            if not phone or normalize_whatsapp_phone_number(phone) != phone:
                return "whatsapp_frozen_recipients_invalid"
            if item.get("kind") == "number":
                continue
            version = item.get("auth_version")
            if item.get("kind") != "admin" or type(version) is not int:
                return "whatsapp_frozen_recipients_invalid"
            try:
                user = await load_active_admin(session, item.get("user_id"), auth_version=version, lock=True)
            except MutationError:
                return "whatsapp_recipient_changed"
            if normalize_whatsapp_phone_number(user.mobile_phone_number) != phone:
                return "whatsapp_recipient_changed"
        return None

    async def send_notification_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        *,
        variables: dict[str, str] | None = None,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        if not config.configured:
            raise NotificationDeliveryError("WhatsApp integration is not enabled or configured.")
        recipients = action.get("frozen_whatsapp_recipients")
        if not isinstance(recipients, list) or any(not isinstance(item, dict) for item in recipients):
            raise NotificationDeliveryError("Stored WhatsApp recipients are unavailable; review is required.")
        phones = [str(item.get("phone") or "") for item in recipients]
        if not phones:
            raise NotificationDeliveryError("No WhatsApp Admin users or phone-number targets are configured or selected.")
        title = str(action.get("title") or context.subject).strip()
        message = str(action.get("message") or "").strip()
        body = "\n\n".join(part for part in [title, message] if part) or context.subject
        delivered, failures = await self._send_to_phones(
            phones,
            body,
            config=config,
            buttons=visitor_pass_timeframe_notification_buttons(context),
        )
        if failures:
            raise NotificationDeliveryError("; ".join(failures))
        if delivered == 0:
            raise NotificationDeliveryError("No WhatsApp messages were delivered.")



    async def _send_to_phones(
        self,
        phones: list[str],
        body: str,
        *,
        config: WhatsAppIntegrationConfig,
        buttons: list[dict[str, str]] | None = None,
    ) -> tuple[int, list[str]]:
        failures: list[str] = []
        delivered = 0
        for phone in phones:
            try:
                if buttons:
                    await self.send_interactive_buttons(phone, body, buttons, config=config)
                else:
                    await self.send_text_message(phone, body, config=config)
                delivered += 1
            except Exception as exc:
                failures.append(f"{masked_phone_number(phone)}: {exc}")
        return delivered, failures


    async def mark_incoming_message_read(
        self,
        message_id: Any,
        *,
        config: WhatsAppIntegrationConfig | None = None,
        show_typing: bool = False,
    ) -> dict[str, Any] | None:
        config = config or await load_whatsapp_config()
        if not config.configured:
            return None
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return None
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": normalized_message_id,
        }
        if show_typing:
            payload["typing_indicator"] = {"type": "text"}
        try:
            result = await self._transport.send(config, payload)
        except Exception as exc:
            logger.info(
                "whatsapp_read_receipt_failed",
                extra={
                    "message_id": normalized_message_id,
                    "typing_indicator": show_typing,
                    "error": str(exc)[:240],
                },
            )
            return None
        await event_bus.publish(
            "whatsapp.message_read",
            {
                "message_id": normalized_message_id,
                "typing_indicator": show_typing,
            },
        )
        return result


    async def _send_whatsapp_message(
        self,
        to: str,
        payload: dict[str, Any],
        history_body: str,
        *,
        kind: str,
        config: WhatsAppIntegrationConfig | None = None,
        metadata: dict[str, Any] | None = None,
        visitor_pass_id: str | None = None,
        terminal_notice: bool = False,
        record_history: bool = True,
    ) -> dict[str, Any]:
        config = config or await load_whatsapp_config()
        if not config.configured:
            raise NotificationDeliveryError("WhatsApp integration is not enabled or configured.")
        recipient = normalize_whatsapp_phone_number(to)
        if not recipient:
            raise NotificationDeliveryError("WhatsApp destination phone number is missing.")
        payload = {**payload, "to": recipient}
        result = (await self._replies.send(payload, visitor_pass_id=visitor_pass_id, terminal_notice=terminal_notice)
                  if self._replies else await self._transport.send(config, payload))
        if record_history:
            await self._conversations.record_outbound_visitor_message(
                recipient,
                history_body,
                kind=kind,
                provider_message_id=whatsapp_response_message_id(result),
                metadata=metadata,
            )
        return result




    async def test_connection(self, values: dict[str, Any]) -> None:
        await self._transport.test_connection(await load_whatsapp_config(values))


_delivery_service: WhatsAppDeliveryService | None = None

def get_whatsapp_delivery_service() -> WhatsAppDeliveryService:
    global _delivery_service
    if _delivery_service is None:
        _delivery_service = WhatsAppDeliveryService()
    return _delivery_service
