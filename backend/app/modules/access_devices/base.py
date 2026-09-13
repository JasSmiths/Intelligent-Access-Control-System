from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol

if TYPE_CHECKING:
    from app.services.settings import RuntimeConfig

from app.modules.gate.base import CommandDelivery, GateState


ACCESS_DEVICE_KIND_GATE = "gate"
ACCESS_DEVICE_KIND_GARAGE_DOOR = "garage_door"
ACCESS_DEVICE_KINDS = {ACCESS_DEVICE_KIND_GATE, ACCESS_DEVICE_KIND_GARAGE_DOOR}
ACCESS_DEVICE_PROVIDERS = {"home_assistant", "esphome"}


class AccessDeviceProviderError(RuntimeError):
    """Base provider error that should not automatically trigger failover."""


class AccessDeviceProviderUnavailable(AccessDeviceProviderError):
    """The command was definitely not sent; a configured fallback may be tried."""


class AccessDeviceCommandUncertain(AccessDeviceProviderError):
    """Command I/O began, but acceptance is unknown. Never retry or fail over."""


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
    device_id: str | None = None


def binding_is_commandable(
    binding: AccessDeviceBinding, *, home_assistant_url: str, home_assistant_token: str,
    esphome_devices: list[dict[str, Any]],
) -> bool:
    """Static configuration check shared by settings and execution planning."""
    if not binding.enabled or not binding.external_id.strip():
        return False
    if binding.provider == "home_assistant":
        return bool(home_assistant_url and home_assistant_token)
    if binding.provider != "esphome":
        return False
    devices = [item for item in esphome_devices if item.get("enabled", True) and item.get("host")]
    device_id = str(binding.config.get("device_id") or "").strip()
    if not device_id and ":" in binding.external_id:
        device_id = binding.external_id.split(":", 1)[0]
    if device_id:
        return any(str(item.get("id")) == device_id for item in devices)
    return len(devices) == 1


def validate_gate_admission_device_key(
    value: str | None, *, device_facts: list[dict[str, Any]], allow_unset: bool = True,
) -> str | None:
    key = str(value or "").strip() or None
    if key is None:
        if allow_unset:
            return None
        raise ValueError("An admission gate must be explicitly configured before automatic access.")
    device = next((item for item in device_facts if item.get("key") == key), None)
    if not device or device.get("kind") != "gate" or not all(
        device.get(field) for field in ("enabled", "open_for_access", "commandable")
    ):
        raise ValueError("The admission gate must be enabled, commandable and selected for automatic access.")
    return key


def resolve_legacy_cover_key(*, entity_id: str | None, target: str | None) -> str | None:
    """The two existing public request aliases; no implicit default target."""
    return entity_id or {
        "main_garage_door": "cover.main_garage_door",
        "mums_garage_door": "cover.mums_garage_door",
    }.get(target or "")


@dataclass(frozen=True)
class AccessDeviceDiscoveryItem:
    external_id: str
    name: str
    kind: str
    state: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccessDeviceStateObservation:
    state: GateState
    observed_at: datetime


@dataclass(frozen=True)
class AccessDeviceCommandResult:
    accepted: bool
    state: GateState
    detail: str | None = None
    provider: str | None = None
    external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    delivery: CommandDelivery | None = None
    observation: AccessDeviceStateObservation | None = None

    def __post_init__(self) -> None:
        if self.delivery is None:
            object.__setattr__(self, "delivery", CommandDelivery.ACCEPTED if self.accepted else CommandDelivery.REJECTED)


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

    async def observe_state(self, binding: AccessDeviceBinding, *, runtime_config: RuntimeConfig | None = None) -> AccessDeviceStateObservation:
        ...

    async def command_cover(
        self,
        binding: AccessDeviceBinding,
        action: str,
        reason: str,
        *, runtime_config: RuntimeConfig | None = None,
    ) -> AccessDeviceCommandResult:
        ...

    async def subscribe_state_changes(self) -> AsyncIterator[dict[str, Any]]:
        ...


def gate_receipt_projection(
    receipts: list[dict[str, Any]], *, admission_target_device_id: str | None,
    expected_target_count: int,
) -> dict[str, Any]:
    """One public projection: transport acceptance, physical evidence and admission."""
    complete = bool(receipts) and len(receipts) == expected_target_count
    admission = next((item for item in receipts if item["target_device_id"] == admission_target_device_id), None)
    accepted = complete and all(item["accepted"] for item in receipts)
    any_accepted = any(item["accepted"] for item in receipts)
    any_not_accepted = any(item["delivery"] in {"not_sent", "rejected"} for item in receipts)
    physically_mixed = (any(item["verified"] for item in receipts)
                        and any(not item["verified"] and item["delivery"] in {"not_sent", "rejected"} for item in receipts))
    delivery = ("unknown" if not complete or any(item["delivery"] == "unknown" for item in receipts)
                else "partial" if (any_accepted and any_not_accepted) or physically_mixed
                else "accepted" if accepted
                else "rejected" if any(item["delivery"] == "rejected" for item in receipts) else "not_sent")
    mechanically_confirmed = complete and all(item["verified"] for item in receipts)
    state = (admission["state"] if admission else receipts[0]["state"] if len(receipts) == 1
             else "open" if mechanically_confirmed else "unknown")
    return {"target_receipts": receipts, "admission_verified": bool(admission and admission["verified"]),
            "mechanically_confirmed": mechanically_confirmed, "accepted": accepted, "delivery": delivery,
            "requires_reconciliation": not complete or any(item["requires_reconciliation"] for item in receipts),
            "state": state}
