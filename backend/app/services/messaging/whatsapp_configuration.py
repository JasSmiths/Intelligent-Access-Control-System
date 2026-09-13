"""Settings-to-transport configuration. Reading it performs no vendor I/O."""
from __future__ import annotations

from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig, normalize_graph_api_version
from app.services.settings import get_runtime_config, get_runtime_config_for_session

async def load_whatsapp_config(values: dict[str, Any] | None = None, *, session: AsyncSession | None = None) -> WhatsAppIntegrationConfig:
    runtime = await get_runtime_config_for_session(session) if session is not None else await get_runtime_config()
    return whatsapp_config_from_runtime(runtime, values)


def whatsapp_config_from_runtime(runtime: Any, values: dict[str, Any] | None = None) -> WhatsAppIntegrationConfig:
    """Convert the already-authorized runtime snapshot without another read."""
    overrides = values or {}

    def text(key: str, default: str) -> str:
        value = overrides.get(key, default)
        if isinstance(value, bool):
            return default
        return str(value or "").strip()

    def bool_setting(key: str, default: bool) -> bool:
        value = overrides.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    return WhatsAppIntegrationConfig(
        enabled=bool_setting("whatsapp_enabled", runtime.whatsapp_enabled),
        access_token=text("whatsapp_access_token", runtime.whatsapp_access_token),
        phone_number_id=text("whatsapp_phone_number_id", runtime.whatsapp_phone_number_id),
        business_account_id=text("whatsapp_business_account_id", runtime.whatsapp_business_account_id),
        webhook_verify_token=text("whatsapp_webhook_verify_token", runtime.whatsapp_webhook_verify_token),
        app_secret=text("whatsapp_app_secret", runtime.whatsapp_app_secret),
        graph_api_version=normalize_graph_api_version(text("whatsapp_graph_api_version", runtime.whatsapp_graph_api_version)),
        visitor_pass_template_name=text("whatsapp_visitor_pass_template_name", runtime.whatsapp_visitor_pass_template_name),
        visitor_pass_template_language=text("whatsapp_visitor_pass_template_language", runtime.whatsapp_visitor_pass_template_language),
    )
