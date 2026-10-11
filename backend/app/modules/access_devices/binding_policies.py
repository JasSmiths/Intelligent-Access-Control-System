"""Pure access-device binding configuration; no adapters, I/O or transactions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol


class BindingSettings(Protocol):
    @property
    def home_assistant_url(self) -> str: ...

    @property
    def home_assistant_token(self) -> str: ...

    @property
    def esphome_devices(self) -> list[dict[str, Any]]: ...


ConfiguredInput = Callable[[str, dict[str, Any], BindingSettings], bool]
PhysicalInput = Callable[[str, dict[str, Any], BindingSettings], list[Any]]
RuntimeInput = Callable[[BindingSettings], Any]
ConfigInput = Callable[[Any], dict[str, Any]]


def _existing_config(value: Any) -> dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class BindingPolicy:
    provider: str
    target_setting_keys: frozenset[str]
    configured_setting_keys: frozenset[str]
    configured_input: ConfiguredInput
    physical_identity_input: PhysicalInput
    frozen_runtime_input: RuntimeInput
    validate_config_input: ConfigInput = _existing_config
    affected_setting_prefix: str | None = None

    def validate_config(self, value: Any) -> dict[str, Any]:
        """Retain current coercion and all vendor/discovery metadata verbatim."""
        return self.validate_config_input(value)

    def configured(
        self, external_id: str, config: dict[str, Any], *, enabled: bool, settings: BindingSettings
    ) -> bool:
        return bool(
            enabled and external_id.strip() and self.configured_input(external_id, config, settings)
        )

    def affected_by(self, keys: set[str]) -> bool:
        # Keep the established HA prefix behavior in mixed-setting updates, even
        # when an unrelated HA key alone would not trigger target locking.
        return bool(
            keys & self.target_setting_keys
            or self.affected_setting_prefix
            and any(key.startswith(self.affected_setting_prefix) for key in keys)
        )


def _ha_configured(_external_id: str, _config: dict[str, Any], settings: BindingSettings) -> bool:
    return bool(settings.home_assistant_url and settings.home_assistant_token)


def _ha_identity(external_id: str, _config: dict[str, Any], settings: BindingSettings) -> list[Any]:
    return [settings.home_assistant_url.rstrip("/"), external_id]


def _ha_runtime(settings: BindingSettings) -> Any:
    return {"url": settings.home_assistant_url, "token": settings.home_assistant_token}


def _esphome_endpoints(settings: BindingSettings) -> list[dict[str, Any]]:
    return [
        item for item in settings.esphome_devices if item.get("enabled", True) and item.get("host")
    ]


def _esphome_configured(
    external_id: str, config: dict[str, Any], settings: BindingSettings
) -> bool:
    devices = _esphome_endpoints(settings)
    device_id = str(config.get("device_id") or "").strip()
    if not device_id and ":" in external_id:
        device_id = external_id.split(":", 1)[0]
    if device_id:
        return any(str(item.get("id")) == device_id for item in devices)
    return len(devices) == 1


def _esphome_identity(
    external_id: str, config: dict[str, Any], settings: BindingSettings
) -> list[Any]:
    device_id = config.get("device_id") or external_id.split(":", 1)[0]
    devices = _esphome_endpoints(settings)
    endpoint = next((item for item in devices if str(item.get("id")) == str(device_id)), None)
    if endpoint is None and len(devices) == 1:
        endpoint = devices[0]
    return [
        endpoint.get("host") if endpoint else device_id,
        endpoint.get("port", 6053) if endpoint else 6053,
        config.get("key") or external_id.split(":", 1)[-1],
    ]


def _esphome_runtime(settings: BindingSettings) -> Any:
    # The complete ordered list, including unrelated devices and secret values,
    # is the established frozen fingerprint input. Never narrow it to a match.
    return settings.esphome_devices


BINDING_POLICIES: dict[str, BindingPolicy] = {
    "home_assistant": BindingPolicy(
        provider="home_assistant",
        target_setting_keys=frozenset(
            {"home_assistant_url", "home_assistant_token", "home_assistant_gate_open_service"}
        ),
        configured_setting_keys=frozenset({"home_assistant_url", "home_assistant_token"}),
        configured_input=_ha_configured,
        physical_identity_input=_ha_identity,
        frozen_runtime_input=_ha_runtime,
        affected_setting_prefix="home_assistant_",
    ),
    "esphome": BindingPolicy(
        provider="esphome",
        target_setting_keys=frozenset({"esphome_devices"}),
        configured_setting_keys=frozenset({"esphome_devices"}),
        configured_input=_esphome_configured,
        physical_identity_input=_esphome_identity,
        frozen_runtime_input=_esphome_runtime,
    ),
}


def binding_policy(provider: str) -> BindingPolicy | None:
    return BINDING_POLICIES.get(provider)


def require_binding_policy(provider: str) -> BindingPolicy:
    policy = binding_policy(provider)
    if policy is None:
        raise ValueError(f"Unsupported access device provider: {provider}")
    return policy


def target_setting_keys() -> set[str]:
    return set().union(*(policy.target_setting_keys for policy in BINDING_POLICIES.values()))


def configured_setting_keys() -> set[str]:
    return set().union(*(policy.configured_setting_keys for policy in BINDING_POLICIES.values()))


def affected_provider_names(keys: set[str]) -> list[str]:
    return [name for name, policy in BINDING_POLICIES.items() if policy.affected_by(keys)]


def validate_policy_runtime_coverage(runtime_names: Iterable[str]) -> None:
    names = list(runtime_names)
    if len(names) != len(set(names)):
        raise ValueError("Duplicate access-device runtime provider.")
    if any(name != policy.provider for name, policy in BINDING_POLICIES.items()):
        raise ValueError("Access-device policy key must match its provider.")
    if any(not policy.configured_setting_keys <= policy.target_setting_keys
           for policy in BINDING_POLICIES.values()):
        raise ValueError("Configured access-device settings must be target settings.")
    missing_runtime = sorted(BINDING_POLICIES.keys() - set(names))
    missing_policy = sorted(set(names) - BINDING_POLICIES.keys())
    if missing_runtime or missing_policy:
        raise ValueError(
            f"Access-device provider composition mismatch: missing runtime {missing_runtime}; missing policy {missing_policy}."
        )
