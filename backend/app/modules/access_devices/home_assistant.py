from __future__ import annotations

from typing import Any, AsyncIterator

from app.modules.access_devices.base import (
    ACCESS_DEVICE_KIND_GARAGE_DOOR,
    ACCESS_DEVICE_KIND_GATE,
    AccessDeviceBinding,
    AccessDeviceCommandResult,
    AccessDeviceDiscoveryItem,
    AccessDeviceProviderStatus,
    AccessDeviceProviderUnavailable,
)
from app.modules.gate.base import GateState
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError, get_home_assistant_client
from app.modules.home_assistant.covers import (
    DEFAULT_CLOSE_SERVICE,
    DEFAULT_OPEN_SERVICE,
    gate_state_from_cover_state,
    normalize_cover_state,
    title_from_entity_id,
)
from app.services.settings import get_runtime_config


class HomeAssistantAccessDeviceProvider:
    provider_key = "home_assistant"
    display_name = "Home Assistant"

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or get_home_assistant_client()

    async def configured(self) -> bool:
        config = await get_runtime_config()
        return bool(config.home_assistant_url and config.home_assistant_token)

    async def status(self, *, refresh: bool = False) -> AccessDeviceProviderStatus:
        configured = await self.configured()
        if not configured:
            return AccessDeviceProviderStatus(provider=self.provider_key, configured=False)
        try:
            if refresh:
                await self._client.list_states()
            return AccessDeviceProviderStatus(
                provider=self.provider_key,
                configured=True,
                connected=True,
            )
        except Exception as exc:
            return AccessDeviceProviderStatus(
                provider=self.provider_key,
                configured=True,
                connected=False,
                degraded=True,
                last_error=str(exc),
            )

    async def discover_covers(self, device_id: str | None = None) -> list[AccessDeviceDiscoveryItem]:
        try:
            states = await self._client.list_states()
        except Exception as exc:
            raise AccessDeviceProviderUnavailable(str(exc)) from exc
        items: list[AccessDeviceDiscoveryItem] = []
        for state in states:
            if not str(state.entity_id).startswith("cover."):
                continue
            name = str(state.attributes.get("friendly_name") or title_from_entity_id(state.entity_id))
            device_class = str(state.attributes.get("device_class") or "").lower()
            label = f"{state.entity_id} {name}".lower()
            kind = (
                ACCESS_DEVICE_KIND_GARAGE_DOOR
                if device_class == "garage" or "garage" in label
                else ACCESS_DEVICE_KIND_GATE
                if device_class == "gate" or "gate" in label
                else ACCESS_DEVICE_KIND_GARAGE_DOOR
                if "door" in label
                else ACCESS_DEVICE_KIND_GATE
            )
            items.append(
                AccessDeviceDiscoveryItem(
                    external_id=str(state.entity_id),
                    name=name,
                    kind=kind,
                    state=normalize_cover_state(str(state.state)),
                    metadata={
                        "device_class": device_class,
                        "open_service": DEFAULT_OPEN_SERVICE,
                        "close_service": DEFAULT_CLOSE_SERVICE,
                    },
                )
            )
        return items

    async def current_state(self, binding: AccessDeviceBinding) -> GateState:
        try:
            state = await self._client.get_state(binding.external_id)
        except Exception as exc:
            raise AccessDeviceProviderUnavailable(str(exc)) from exc
        return gate_state_from_cover_state(str(state.state))

    async def command_cover(
        self,
        binding: AccessDeviceBinding,
        action: str,
        reason: str,
    ) -> AccessDeviceCommandResult:
        if action not in {"open", "close"}:
            return AccessDeviceCommandResult(
                accepted=False,
                state=GateState.UNKNOWN,
                detail=f"Unsupported cover action: {action}",
                provider=self.provider_key,
                external_id=binding.external_id,
            )
        service_name = str(
            binding.config.get("open_service" if action == "open" else "close_service")
            or (DEFAULT_OPEN_SERVICE if action == "open" else DEFAULT_CLOSE_SERVICE)
        )
        try:
            await self._client.call_service(service_name, {"entity_id": binding.external_id})
            state = await self._client.get_state(binding.external_id)
        except HomeAssistantError as exc:
            raise AccessDeviceProviderUnavailable(str(exc)) from exc
        except Exception as exc:
            raise AccessDeviceProviderUnavailable(str(exc)) from exc
        return AccessDeviceCommandResult(
            accepted=True,
            state=gate_state_from_cover_state(str(state.state)),
            detail=reason,
            provider=self.provider_key,
            external_id=binding.external_id,
            metadata={"service": service_name},
        )

    async def subscribe_state_changes(self) -> AsyncIterator[dict[str, Any]]:
        async for message in self._client.subscribe_state_changed():
            yield message
