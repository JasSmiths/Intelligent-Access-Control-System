from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

from app.modules.gate.base import GateState


ACCESS_DEVICE_KIND_GATE = "gate"
ACCESS_DEVICE_KIND_GARAGE_DOOR = "garage_door"
ACCESS_DEVICE_KINDS = {ACCESS_DEVICE_KIND_GATE, ACCESS_DEVICE_KIND_GARAGE_DOOR}
ACCESS_DEVICE_PROVIDERS = {"home_assistant", "esphome"}


class AccessDeviceProviderError(RuntimeError):
    """Base provider error that should not automatically trigger failover."""


class AccessDeviceProviderUnavailable(AccessDeviceProviderError):
    """Provider could not be reached or its transport/session is unavailable."""


@dataclass(frozen=True)
class AccessDeviceBinding:
    provider: str
    external_id: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccessDeviceEntity:
    key: str
    kind: str
    name: str
    enabled: bool = True
    schedule_id: str | None = None
    open_for_access: bool = True
    sort_order: int = 0
    bindings: dict[str, AccessDeviceBinding] = field(default_factory=dict)


@dataclass(frozen=True)
class AccessDeviceDiscoveryItem:
    external_id: str
    name: str
    kind: str
    state: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccessDeviceCommandResult:
    accepted: bool
    state: GateState
    detail: str | None = None
    provider: str | None = None
    external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccessDeviceProviderStatus:
    provider: str
    configured: bool
    connected: bool = False
    degraded: bool = False
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AccessDeviceProvider(Protocol):
    provider_key: str
    display_name: str

    async def configured(self) -> bool:
        ...

    async def status(self, *, refresh: bool = False) -> AccessDeviceProviderStatus:
        ...

    async def discover_covers(self, device_id: str | None = None) -> list[AccessDeviceDiscoveryItem]:
        ...

    async def current_state(self, binding: AccessDeviceBinding) -> GateState:
        ...

    async def command_cover(
        self,
        binding: AccessDeviceBinding,
        action: str,
        reason: str,
    ) -> AccessDeviceCommandResult:
        ...

    async def subscribe_state_changes(self) -> AsyncIterator[dict[str, Any]]:
        ...
