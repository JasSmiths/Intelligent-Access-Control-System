from __future__ import annotations

from app.modules.access_devices.base import AccessDeviceProvider
from app.modules.access_devices.esphome import ESPHomeAccessDeviceProvider
from app.modules.access_devices.home_assistant import HomeAssistantAccessDeviceProvider


class UnsupportedAccessDeviceProviderError(ValueError):
    """Raised when a configured access-device provider is not registered."""


_PROVIDERS: dict[str, AccessDeviceProvider] = {
    "home_assistant": HomeAssistantAccessDeviceProvider(),
    "esphome": ESPHomeAccessDeviceProvider(),
}


def get_access_device_provider(name: str) -> AccessDeviceProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise UnsupportedAccessDeviceProviderError(f"Unsupported access device provider: {name}") from exc


def access_device_provider_keys() -> list[str]:
    return ["home_assistant", "esphome"]
