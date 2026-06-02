from __future__ import annotations

import pytest

from app.ai import tools as ai_tools
from app.ai.tool_groups import gate_maintenance_handlers as gate_tools

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

    monkeypatch.setattr(gate_tools, "_resolve_openable_device", fake_resolve_openable_device)
    monkeypatch.setattr(ai_tools, "get_gate_command_coordinator", lambda: FailingGateCommandCoordinator())

    result = await ai_tools.open_device(
        {
            "target": "Top Gate",
            "kind": "gate",
            "action": "open",
            "reason": "Contract test",
            "confirm": False,
        }
    )

    assert_contract_subset(result, load_contract_fixture("alfred/open_gate_confirmation.json"))


def test_alfred_v3_tool_registry_contract_marks_gate_mutations_confirmation_required() -> None:
    tools = ai_tools.build_agent_tools()
    open_gate = tools["open_gate"]

    assert open_gate.requires_confirmation is True
    assert open_gate.read_only is False
    assert open_gate.safety_level == ai_tools.SAFETY_CONFIRMATION_REQUIRED
