from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai import context as alfred_context
from app.ai import tools as ai_tools
from app.ai.tool_groups import _shared as alfred_shared
from app.ai.tool_groups import gate_maintenance_handlers as alfred_gate_maintenance_handlers
from app.ai.tool_groups import registry as alfred_registry
from app.ai.tool_groups import gate_maintenance_handlers as gate_tools
from app.models.enums import UserRole

from .helpers import assert_contract_subset, load_contract_fixture


@pytest.mark.asyncio
async def test_alfred_v3_tool_confirmation_contract_blocks_open_gate_before_confirmation(monkeypatch) -> None:
    async def fake_resolve_openable_device(arguments, *, kind_filter: str):
        return {
            "kind": "gate",
            "name": "Top Gate",
            "entity": {
                "id": "gate-top",
                "name": "Top Gate",
                "entity_id": "cover.top_gate",
            },
        }

    class FailingGateCommandCoordinator:
        async def execute_open(self, *_args, **_kwargs):
            raise AssertionError("Alfred must not mutate hardware before confirmation.")

    admin = SimpleNamespace(
        id=uuid.uuid4(),
        is_active=True,
        role=UserRole.ADMIN,
        auth_session_version=0,
    )
    access_devices = SimpleNamespace(
        preview_gate_open=AsyncMock(return_value={
            "action": "open",
            "target_device_key": "cover.top_gate",
            "targets": [],
        })
    )

    monkeypatch.setattr(gate_tools, "_resolve_openable_device", fake_resolve_openable_device)
    monkeypatch.setattr(alfred_shared, "_chat_context_user", AsyncMock(return_value=admin))
    monkeypatch.setattr(alfred_gate_maintenance_handlers, "get_access_device_service", lambda: access_devices)
    monkeypatch.setattr(alfred_gate_maintenance_handlers, "get_gate_command_coordinator", lambda: FailingGateCommandCoordinator())

    context_token = alfred_context.set_chat_tool_context({"user_id": str(admin.id), "user_role": "admin"})
    try:
        result = await alfred_gate_maintenance_handlers.open_device(
            {
                "target": "Top Gate",
                "kind": "gate",
                "action": "open",
                "reason": "Contract test",
                "confirm": False,
            }
        )
    finally:
        alfred_context.set_chat_tool_context({}, token=context_token)

    assert_contract_subset(result, load_contract_fixture("alfred/open_gate_confirmation.json"))


def test_alfred_v3_tool_registry_contract_marks_gate_mutations_confirmation_required() -> None:
    tools = alfred_registry.build_agent_tools()
    open_gate = tools["open_gate"]

    assert open_gate.requires_confirmation is True
    assert open_gate.read_only is False
    assert open_gate.safety_level == ai_tools.SAFETY_CONFIRMATION_REQUIRED
