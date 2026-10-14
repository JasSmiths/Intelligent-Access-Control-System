"""Serialize settings saves with process-local provider activation.

Persistence owns validation/audit. A provider failure cannot revoke that receipt.
Every application settings-save entry point uses this owner, so an older save
cannot restart a provider after a newer save. Shutdown closes admission before
draining the current save, while providers and the database are still available.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, NotRequired, TypedDict

from app.core.task_lifecycle import drain_owned
from app.models import User
from app.services.integration_settings import IntegrationSettingsEdit
from app.services.settings import SettingsUpdateResult, update_settings

type Restart = Callable[[], Awaitable[None]]


class SettingsActivationUnavailable(RuntimeError):
    """Shutdown refused a new save before persistence started."""


class ActivationReceipt(TypedDict):
    state: Literal["not_required", "activated", "failed", "partial_failure", "pending"]
    services: dict[str, str]


class SettingsSaveReceipt(TypedDict):
    saved: bool
    settings: list[dict[str, Any]]
    changed_keys: list[str]
    activation: ActivationReceipt
    integration: NotRequired[dict[str, Any]]


class SettingsActivationCoordinator:
    def __init__(self, restarts: Mapping[str, Restart]) -> None:
        self._restarts = dict(restarts)
        self._lock = asyncio.Lock()
        self._closing = False

    async def start(self) -> None:
        async with self._lock:
            self._closing = False

    async def stop(self) -> None:
        self._closing = True
        async with self._lock:
            pass

    async def save(self, updates: dict[str, Any], *, user: User, source: str,
                   integration_edit: IntegrationSettingsEdit | None = None) -> SettingsSaveReceipt:
        async with self._lock:
            if self._closing:
                raise SettingsActivationUnavailable("Settings save was not started because the service is stopping.")
            # Admission owns persistence through every affected activation.
            # A cancelled request cannot strand an acknowledged save before
            # activation, release serialization, or race provider disposal.
            return await drain_owned(self._save_admitted(updates, user=user, source=source, integration_edit=integration_edit))

    async def _save_admitted(self, updates: dict[str, Any], *, user: User, source: str,
                             integration_edit: IntegrationSettingsEdit | None = None) -> SettingsSaveReceipt:
        result = (await update_settings(updates, user=user, source=source) if integration_edit is None else
                  await update_settings(updates, user=user, source=source, integration_edit=integration_edit))
        activation = await self._activate(result)
        receipt: SettingsSaveReceipt = {"saved": True, "settings": result.rows, "changed_keys": sorted(result.changed_keys), "activation": activation}
        if result.integration is not None:
            receipt["integration"] = result.integration
        return receipt

    async def _activate(self, result: SettingsUpdateResult) -> ActivationReceipt:
        prefixes = {"home_assistant": ("home_assistant_",), "access_devices": ("esphome_", "gate_"), "unifi_protect": ("unifi_protect_",)}
        affected = [name for name, keys in prefixes.items() if any(key.startswith(keys) for key in result.changed_keys)]
        if not affected:
            return {"state": "not_required", "services": {}}
        if self._closing:
            return {"state": "pending", "services": dict.fromkeys(affected, "pending")}
        outcomes: dict[str, str] = {}
        for name in affected:
            try:
                await self._restarts[name]()
                outcomes[name] = "activated"
            except Exception:  # noqa: BLE001 - committed persistence survives arbitrary activation failure.
                outcomes[name] = "failed"
        failures = sum(value == "failed" for value in outcomes.values())
        return {"state": "failed" if failures == len(affected) else "partial_failure" if failures else "activated", "services": outcomes}


async def _restart_home_assistant() -> None:
    from app.services.home_assistant import get_home_assistant_service

    service = get_home_assistant_service()
    await service.stop()
    await service.start()


async def _restart_devices() -> None:
    from app.services.access_devices import get_access_device_service

    await get_access_device_service().restart()


async def _restart_protect() -> None:
    from app.services.unifi_protect import get_unifi_protect_service

    await get_unifi_protect_service().restart()


settings_activation = SettingsActivationCoordinator({"home_assistant": _restart_home_assistant, "access_devices": _restart_devices, "unifi_protect": _restart_protect})
