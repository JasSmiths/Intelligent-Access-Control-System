from __future__ import annotations

# ruff: noqa: F401,F403,F405

import asyncio

import httpx

from app.ai.providers import complete_with_provider_options, get_llm_provider
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.services.dvla import lookup_normalized_vehicle_registration
from app.services.event_bus import event_bus
from app.services.messaging.visitor_conversation import WhatsAppVisitorConversationMixin
from app.services.messaging.whatsapp_delivery import WhatsAppDeliveryMixin
from app.services.messaging.whatsapp_helpers import *  # noqa: F401,F403
from app.services.messaging.whatsapp_router import WhatsAppRouterMixin
from app.services.messaging.whatsapp_webhook import WhatsAppWebhookMixin
from app.services.settings import get_runtime_config
from app.services.telemetry import write_audit_log
from app.services.visitor_passes import (
    VisitorPassError,
    append_visitor_pass_whatsapp_history,
    get_visitor_pass_service,
    serialize_visitor_pass,
    visitor_pass_whatsapp_history,
)

logger = get_logger(__name__)


class WhatsAppMessagingService(
    WhatsAppWebhookMixin,
    WhatsAppDeliveryMixin,
    WhatsAppRouterMixin,
    WhatsAppVisitorConversationMixin,
):
    provider_name = "whatsapp"

    def __init__(self) -> None:
        self._last_error: str | None = None
        self._visitor_message_tasks: dict[str, asyncio.Task[None]] = {}
        self._visitor_message_debounce_seconds = VISITOR_TEXT_DEBOUNCE_SECONDS
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_lock = asyncio.Lock()

    async def stop(self) -> None:
        tasks = list(self._visitor_message_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._visitor_message_tasks.clear()
        async with self._http_client_lock:
            client = self._http_client
            self._http_client = None
        if client is not None:
            await client.aclose()


whatsapp_messaging_service = WhatsAppMessagingService()


def get_whatsapp_messaging_service() -> WhatsAppMessagingService:
    return whatsapp_messaging_service
