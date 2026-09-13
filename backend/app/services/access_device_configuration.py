"""Configuration-only access-device reads and frozen dispatch plans.

No provider instances, worker lifetimes, commands or side effects belong here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models import AccessDevice
from app.modules.access_devices.base import (
    ACCESS_DEVICE_KIND_GATE, AccessDeviceBinding, AccessDeviceEntity,
    binding_is_commandable, validate_gate_admission_device_key,
)
from app.services.settings import RuntimeConfig, get_runtime_config_for_session


class AccessDeviceConfiguration:
    async def list_devices(self, *, kind: str | None = None, enabled_only: bool = False,
                           session: AsyncSession | None = None) -> list[AccessDeviceEntity]:
        if session is not None:
            return await self.list_devices_for_session(session, kind=kind, enabled_only=enabled_only)
        async with AsyncSessionLocal() as owned:
            return await self.list_devices_for_session(owned, kind=kind, enabled_only=enabled_only)


    async def list_devices_for_session(
        self,
        session: AsyncSession,
        *,
        kind: str | None = None,
        enabled_only: bool = False,
    ) -> list[AccessDeviceEntity]:
        statement = select(AccessDevice).options(selectinload(AccessDevice.provider_bindings))
        if kind:
            statement = statement.where(AccessDevice.kind == kind)
        if enabled_only:
            statement = statement.where(AccessDevice.enabled.is_(True))
        rows = (await session.scalars(statement.order_by(AccessDevice.sort_order, AccessDevice.name))).all()
        return [self.entity_from_row(row) for row in rows]


    async def device_eligibility(self, devices: list[AccessDeviceEntity]) -> dict[str, dict[str, bool]]:
        async with AsyncSessionLocal() as session:
            config = await get_runtime_config_for_session(session)
        return {device.key: self.eligibility(device, config) for device in devices}


    @staticmethod
    def eligibility(device: AccessDeviceEntity, config: RuntimeConfig) -> dict[str, bool]:
        commandable = bool(device.enabled and any(binding_is_commandable(binding,
            home_assistant_url=config.home_assistant_url, home_assistant_token=config.home_assistant_token,
            esphome_devices=config.esphome_devices) for binding in device.bindings.values()))
        return {"commandable": commandable, "admission_eligible": bool(commandable
            and device.kind == ACCESS_DEVICE_KIND_GATE and device.open_for_access)}


    async def preview_gate_open(self, *, target_device_key: str | None = None,
                                require_admission: bool = False, automatic_entry_policy: bool = False,
                                session: AsyncSession | None = None) -> dict[str, Any]:
        if session is None:
            async with AsyncSessionLocal() as owned:
                return await self.preview_gate_open(target_device_key=target_device_key,
                    require_admission=require_admission, automatic_entry_policy=automatic_entry_policy, session=owned)
        return self.target_plan(await self.list_devices_for_session(session),
            await get_runtime_config_for_session(session), action="open", target_device_key=target_device_key,
            require_admission=require_admission, gate_only=True, automatic_entry_policy=automatic_entry_policy)

    async def preview_device_command(self, device_key: str, action: str, *,
                                     session: AsyncSession | None = None) -> dict[str, Any]:
        if session is None:
            async with AsyncSessionLocal() as owned:
                return await self.preview_device_command(device_key, action, session=owned)
        return self.target_plan(await self.list_devices_for_session(session),
            await get_runtime_config_for_session(session), action=action, target_device_key=device_key,
            require_admission=False, gate_only=False)

    def target_plan(self, devices: list[AccessDeviceEntity], config: Any, *, action: str,
                     target_device_key: str | None, require_admission: bool,
                     gate_only: bool, automatic_entry_policy: bool = False) -> dict[str, Any]:
        if automatic_entry_policy and (not require_admission or action != "open" or not gate_only):
            raise ValueError("Automatic entry policy requires an admission-validated gate-open plan.")
        if action not in {"open", "close"}:
            raise ValueError("Access device action must be open or close.")
        facts = [{"key": device.key, "kind": device.kind, "enabled": device.enabled,
                  "open_for_access": device.open_for_access, **self.eligibility(device, config)}
                 for device in devices]
        admission_key = str(getattr(config, "gate_admission_device_key", "") or "").strip() or None
        if require_admission:
            admission_key = validate_gate_admission_device_key(admission_key, device_facts=facts, allow_unset=False)
        targets = [device for device in devices if
                   (device.key == target_device_key if target_device_key else
                    device.kind == ACCESS_DEVICE_KIND_GATE and device.enabled and device.open_for_access)]
        if not targets or (target_device_key and len(targets) != 1):
            raise ValueError("The selected access device was not found, or no automatic gates are configured.")
        if any(not item.enabled or (gate_only and item.kind != ACCESS_DEVICE_KIND_GATE) for item in targets):
            raise ValueError("The selected gate/device must be enabled and of the requested kind.")
        if any(not next(fact["commandable"] for fact in facts if fact["key"] == item.key) for item in targets):
            raise ValueError("Every selected access device requires a commandable provider binding.")
        if require_admission and admission_key not in {device.key for device in targets}:
            raise ValueError("Automatic access must include the explicitly designated admission gate.")
        # Two logical devices with an identical native binding are not independent
        # physical targets. Refuse activation until configuration has one owner.
        identities: dict[tuple[str, str], str] = {}
        for device in devices:
            if not device.enabled:
                continue
            for binding in device.bindings.values():
                if not binding.enabled:
                    continue
                identity = self.binding_identity(binding, config)
                other = identities.setdefault(identity, device.device_id or device.key)
                if other != (device.device_id or device.key) and (
                    device in targets or any(item.device_id == other for item in targets)
                ):
                    raise ValueError("Multiple access devices share a provider target; consolidate that binding before dispatch.")
        admission = next((item for item in targets if item.key == admission_key), None)
        return {"version": 1, "action": action, "target_device_key": target_device_key,
                "require_admission": require_admission, "gate_only": gate_only,
                "automatic_entry_policy": automatic_entry_policy,
                "admission_device_key": admission_key,
                "admission_target_device_id": admission.device_id if admission else None,
                "targets": [self.target_snapshot(device, config) for device in targets]}


    @staticmethod
    def fingerprint(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


    def binding_identity(self, binding: AccessDeviceBinding, config: Any) -> tuple[str, str]:
        if binding.provider == "home_assistant":
            identity = [config.home_assistant_url.rstrip("/"), binding.external_id]
        else:
            device_id = binding.config.get("device_id") or binding.external_id.split(":", 1)[0]
            configured = [item for item in config.esphome_devices if item.get("enabled", True) and item.get("host")]
            endpoint = next((item for item in configured if str(item.get("id")) == str(device_id)), None)
            if endpoint is None and len(configured) == 1:
                endpoint = configured[0]
            identity = [endpoint.get("host") if endpoint else device_id,
                        endpoint.get("port", 6053) if endpoint else 6053,
                        binding.config.get("key") or binding.external_id.split(":", 1)[-1]]
        return binding.provider, self.fingerprint(identity)


    def target_snapshot(self, device: AccessDeviceEntity, config: Any) -> dict[str, Any]:
        if not device.device_id:
            raise ValueError("A persisted access-device identity is required.")
        providers = []
        for name in self.provider_order(device, config):
            binding = device.bindings[name]
            runtime = ({"url": config.home_assistant_url, "token": config.home_assistant_token}
                       if name == "home_assistant" else config.esphome_devices)
            providers.append({"provider": name, "external_id": binding.external_id,
                              "config_fingerprint": self.fingerprint([binding.config, runtime])})
        snapshot = {"providers": providers, "schedule_id": device.schedule_id,
                    "enabled": device.enabled, "open_for_access": device.open_for_access}
        return {"target_device_id": device.device_id, "device_key": device.key, "kind": device.kind,
                "binding_snapshot": snapshot,
                "binding_fingerprint": self.fingerprint([device.device_id, device.key, device.kind, snapshot])}


    @staticmethod
    def provider_order(device: AccessDeviceEntity, config: Any) -> list[str]:
        preferred = [
            config.gate_control_provider,
            config.gate_failover_provider if config.gate_failover_provider != "none" else "",
            "home_assistant",
            "esphome",
            *sorted(device.bindings),
        ]
        order: list[str] = []
        for provider_name in preferred:
            if not provider_name or provider_name in order:
                continue
            binding = device.bindings.get(provider_name)
            if binding and binding.enabled:
                order.append(provider_name)
        return order


    def entity_from_row(self, row: AccessDevice) -> AccessDeviceEntity:
        return AccessDeviceEntity(
            key=row.key,
            device_id=str(row.id),
            kind=row.kind,
            name=row.name,
            enabled=row.enabled,
            schedule_id=str(row.schedule_id) if row.schedule_id else None,
            open_for_access=row.open_for_access,
            sort_order=row.sort_order,
            bindings={
                binding.provider: AccessDeviceBinding(
                    provider=binding.provider,
                    external_id=binding.external_id,
                    enabled=binding.enabled,
                    config=dict(binding.config or {}),
                )
                for binding in row.provider_bindings
            },
        )

