"""Configuration planning stays usable without importing hardware owners."""
import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.access_device_configuration import AccessDeviceConfiguration
from app.services.access_devices import AccessDeviceService


def test_configuration_owner_has_no_hardware_or_notification_imports():
    source = Path(__file__).parents[1] / "app/services/access_device_configuration.py"
    imports = {node.module for node in ast.walk(ast.parse(source.read_text())) if isinstance(node, ast.ImportFrom)}
    assert not imports.intersection({"app.services.access_devices", "app.services.notifications",
        "app.services.gate_commands", "app.modules.access_devices.registry"})
    assert all(not (name or "").startswith(("app.modules.home_assistant", "app.modules.esphome")) for name in imports)


@pytest.mark.asyncio
async def test_public_service_preview_delegates_exact_selection_and_policy_to_configuration():
    service = AccessDeviceService()
    service._configuration.preview_gate_open = AsyncMock(return_value={"synthetic": "same-plan"})
    assert await service.preview_gate_open(target_device_key="entry", require_admission=True,
                                           automatic_entry_policy=True) == {"synthetic": "same-plan"}
    service._configuration.preview_gate_open.assert_awaited_once_with(target_device_key="entry",
        require_admission=True, automatic_entry_policy=True)


@pytest.mark.asyncio
async def test_intake_can_read_devices_in_its_existing_transaction():
    configuration = AccessDeviceConfiguration()
    session = object()
    configuration.list_devices_for_session = AsyncMock(return_value=[])
    assert await configuration.list_devices(kind="garage_door", enabled_only=True, session=session) == []
    configuration.list_devices_for_session.assert_awaited_once_with(session, kind="garage_door", enabled_only=True)
