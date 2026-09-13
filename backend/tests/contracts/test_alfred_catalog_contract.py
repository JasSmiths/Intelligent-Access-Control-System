"""The current catalog is frozen independently of its Python import layout."""

from dataclasses import fields
from importlib import import_module
import json

from app.ai.tool_groups.registry import build_agent_tools

from .helpers import load_contract_fixture


def test_current_tool_catalog_preserves_all_public_metadata():
    tools = build_agent_tools()
    baseline = load_contract_fixture("alfred/tool_catalog.json")
    old_fields = set(baseline[0])
    execution_fields = {"status_label", "success_fields", "finish_after_confirmation", "summary_handler", "button_handler"}
    assert {f.name for f in fields(next(iter(tools.values())))} == old_fields | execution_fields | {"handler"}
    metadata = [
        {field.name: getattr(tool, field.name) for field in fields(tool) if field.name in old_fields}
        for tool in tools.values()
    ]
    # JSON normalisation reflects the public representation of tuples and preserves order.
    assert json.loads(json.dumps(metadata)) == load_contract_fixture("alfred/tool_catalog.json")


def test_catalog_binds_handlers_directly_to_their_defining_modules():
    for tool in build_agent_tools().values():
        assert tool.handler.__module__.startswith("app.ai.tool_groups.")
        owner = import_module(tool.handler.__module__)
        assert getattr(owner, tool.handler.__name__) is tool.handler


def test_registry_resolves_handler_dependencies_when_constructed(monkeypatch):
    from app.ai.tool_groups import general_handlers

    # Build once first: an already imported catalog must not freeze a handler alias.
    original = build_agent_tools()["query_presence"].handler

    async def fake_presence(_arguments):
        return {"people": []}

    monkeypatch.setattr(general_handlers, "query_presence", fake_presence)
    assert build_agent_tools()["query_presence"].handler is fake_presence
    assert original is not fake_presence


def test_execution_metadata_matches_explicit_catalog_fixture():
    assert {name: tool.execution_metadata() for name, tool in build_agent_tools().items()} == load_contract_fixture("alfred/tool_execution_contract.json")
