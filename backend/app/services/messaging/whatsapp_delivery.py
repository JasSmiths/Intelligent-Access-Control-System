from __future__ import annotations

# ruff: noqa: F403,F405

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import AutomationRule, User
from app.models.enums import UserRole
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services import whatsapp_messaging as wm
from app.services.messaging.whatsapp_helpers import *  # noqa: F401,F403
from app.services.type_helpers import as_dict

logger = get_logger(__name__)

class WhatsAppDeliveryMixin:
    async def status(self) -> dict[str, Any]:
        config = await wm.load_whatsapp_config()
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
            "last_error": self._last_error,
        }

    async def test_connection(self, values: dict[str, Any]) -> None:
        config = await wm.load_whatsapp_config(values)
        if not config.access_token or not config.phone_number_id:
            raise ValueError("WhatsApp access token and phone number ID are required.")
        url = self._graph_url(config, config.phone_number_id)
        headers = {"Authorization": f"Bearer {config.access_token}"}
        client = await self._request_client()
        response = await client.get(
            url,
            headers=headers,
            params={"fields": "id,display_phone_number,verified_name"},
        )
        if response.status_code >= 400:
            raise ValueError(f"WhatsApp API test failed with HTTP {response.status_code}: {response.text[:240]}")

    async def available_admin_targets(self) -> list[dict[str, str]]:
        users = await self._admin_users_with_phone()
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


    async def send_text_message(self, to: str, body: str, *, config: WhatsAppIntegrationConfig | None = None) -> dict[str, Any]:
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
        )

    async def send_template_message(
        self,
        to: str,
        *,
        template_name: str,
        language_code: str,
        body_parameters: list[str],
        config: WhatsAppIntegrationConfig | None = None,
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
            config=config,
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

    async def send_notification_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        *,
        variables: dict[str, str] | None = None,
    ) -> None:
        config = await wm.load_whatsapp_config()
        if not config.configured:
            raise NotificationDeliveryError("WhatsApp integration is not enabled or configured.")
        phones = await self._notification_target_phones(action, variables or {})
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

    async def execute_automation_action(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: Any,
        *,
        rule: AutomationRule,
    ) -> dict[str, Any]:
        config = await wm.load_whatsapp_config()
        if not config.configured:
            return self._automation_result(action, "skipped", reason="whatsapp_not_configured")
        action_config = as_dict(action.get("config"))
        variables = getattr(context, "variables", {}) if isinstance(getattr(context, "variables", {}), dict) else {}
        phones = await self._automation_target_phones(session, action_config, variables)
        message_template = str(action_config.get("message_template") or "@Subject")
        message = render_token_template(message_template, variables) or str(getattr(context, "subject", "") or rule.name)
        if not phones:
            return self._automation_result(action, "skipped", reason="no_whatsapp_targets")
        delivered, failures = await self._send_to_phones(phones, message, config=config)
        if failures:
            return self._automation_result(action, "failed", error="; ".join(failures), delivered_count=delivered)
        return self._automation_result(action, "success", target_count=len(phones), delivered_count=delivered)

    def _automation_result(self, action: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
        return {
            "id": action.get("id"),
            "type": action.get("type"),
            "status": status,
            "integration_provider": "whatsapp",
            "integration_action": "send_message",
            **{key: value for key, value in extra.items() if value is not None},
        }

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
        config = config or await wm.load_whatsapp_config()
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
            result = await self._post_message(config, payload)
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
        await wm.event_bus.publish(
            "whatsapp.message_read",
            {
                "message_id": normalized_message_id,
                "typing_indicator": show_typing,
            },
        )
        return result

    async def _automation_target_phones(
        self,
        session: AsyncSession,
        config: dict[str, Any],
        variables: dict[str, str],
    ) -> list[str]:
        target_mode = str(config.get("target_mode") or "selected")
        if target_mode == "all":
            users = await self._admin_users_with_phone()
            return unique_phone_numbers(user.mobile_phone_number for user in users)
        if target_mode == "dynamic":
            phone = render_token_template(str(config.get("phone_number_template") or ""), variables)
            return unique_phone_numbers([phone])
        raw_user_ids = config.get("target_user_ids")
        user_ids = [coerce_uuid(value) for value in raw_user_ids if str(value).strip()] if isinstance(raw_user_ids, list) else []
        if not user_ids:
            return []
        users = (
            await session.scalars(
                select(User)
                .where(User.id.in_([value for value in user_ids if value]))
                .where(User.role == UserRole.ADMIN)
                .where(User.is_active.is_(True))
            )
        ).all()
        return unique_phone_numbers(user.mobile_phone_number for user in users)

    async def _send_whatsapp_message(
        self,
        to: str,
        payload: dict[str, Any],
        history_body: str,
        *,
        kind: str,
        config: WhatsAppIntegrationConfig | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config = config or await wm.load_whatsapp_config()
        if not config.configured:
            raise NotificationDeliveryError("WhatsApp integration is not enabled or configured.")
        recipient = normalize_whatsapp_phone_number(to)
        if not recipient:
            raise NotificationDeliveryError("WhatsApp destination phone number is missing.")
        payload = {**payload, "to": recipient}
        result = await self._post_message(config, payload)
        await self._record_outbound_visitor_message(
            recipient,
            history_body,
            kind=kind,
            provider_message_id=whatsapp_response_message_id(result),
            metadata=metadata,
        )
        return result

    async def _post_message(self, config: WhatsAppIntegrationConfig, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._graph_url(config, f"{config.phone_number_id}/messages")
        headers = {
            "Authorization": f"Bearer {config.access_token}",
            "Content-Type": "application/json",
        }
        client = await self._request_client()
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            self._last_error = f"HTTP {response.status_code}: {response.text[:240]}"
            raise NotificationDeliveryError(f"WhatsApp API send failed with HTTP {response.status_code}: {response.text[:240]}")
        self._last_error = None
        try:
            return response.json()
        except ValueError:
            return {"status": "ok"}

    def _graph_url(self, config: WhatsAppIntegrationConfig, path: str) -> str:
        version = normalize_graph_api_version(config.graph_api_version)
        return f"https://graph.facebook.com/{version}/{path.lstrip('/')}"

    async def _request_client(self) -> httpx.AsyncClient:
        async with self._http_client_lock:
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(timeout=15, trust_env=False)
            return self._http_client
