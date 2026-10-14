"""Explicit connection-test dispatch. Routes own confirmation; adapters own I/O."""
from dataclasses import asdict
from typing import Any

from app.ai.providers import test_provider_connection
from app.modules.access_devices.esphome import ESPHomeAccessDeviceProvider
from app.modules.access_devices.registry import get_access_device_provider
from app.modules.home_assistant.client import test_connection as test_home_assistant
from app.modules.notifications.apprise_client import validate_apprise_urls
from app.services.dvla import test_vehicle_enquiry_connection
from app.services.settings import (
    CLEARABLE_SECRET_KEYS,
    SECRET_KEYS,
    get_runtime_config,
    runtime_config_from_values,
    validate_dynamic_setting_keys,
)
from app.services.unifi_protect import get_unifi_protect_service
from app.services.vehicle_information import get_vehicle_information_service

SUPPORTED_CONNECTION_TESTS = frozenset({"home_assistant", "esphome", "apprise", "dvla", "dvsa", "unifi_protect", "openai", "gemini", "claude", "anthropic", "ollama"})


async def test_integration_connection(integration: str, values: dict[str, Any]) -> None:
    if integration not in SUPPORTED_CONNECTION_TESTS:
        raise ValueError("Unsupported integration connection test.")
    updates = {key: value for key, value in values.items() if key != "device_id"}
    validate_dynamic_setting_keys(updates)
    current = asdict(await get_runtime_config())
    current.update({key: value for key, value in updates.items() if not (
        key in SECRET_KEYS and value in (None, "") and key not in CLEARABLE_SECRET_KEYS)})
    config = runtime_config_from_values(current)
    if integration == "home_assistant":
        await test_home_assistant(config)
    elif integration == "esphome":
        provider = get_access_device_provider("esphome")
        if not isinstance(provider, ESPHomeAccessDeviceProvider):
            raise ValueError("ESPHome connection-test adapter is unavailable.")
        await provider.test_connection(str(values.get("device_id") or "").strip())
    elif integration == "apprise":
        validate_apprise_urls(config.apprise_urls)
    elif integration == "dvla":
        await test_vehicle_enquiry_connection(updates)
    elif integration == "dvsa":
        await get_vehicle_information_service().test_connection(updates)
    elif integration == "unifi_protect":
        await get_unifi_protect_service().test_connection(updates)
    else:
        await test_provider_connection(integration, config)
