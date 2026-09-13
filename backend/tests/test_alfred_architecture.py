"""Enforce Alfred's narrow contract and dependency boundaries without starting it."""

import ast
from pathlib import Path

import pytest


AI_ROOT = Path(__file__).resolve().parents[1] / "app" / "ai"


@pytest.mark.parametrize("filename", ["tools.py", "context.py", "tool_inputs.py"])
def test_tool_contracts_and_context_depend_only_on_the_standard_library(filename):
    import sys

    tree = ast.parse((AI_ROOT / filename).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module.split(".")[0] in sys.stdlib_module_names
        elif isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names)
    # Contract modules do not resolve other modules or spread mutation through hidden exports.
    forbidden = {"__getattr__", "__dir__", "_ToolFacadeModule", "_propagate_facade_override"}
    assert not any(getattr(node, "name", None) in forbidden for node in ast.walk(tree))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "__class__" and isinstance(node.ctx, ast.Store)
        for node in ast.walk(tree)
    )


def test_tool_groups_import_shared_definitions_instead_of_reexported_dependencies():
    shared = ast.parse((AI_ROOT / "tool_groups" / "_shared.py").read_text())
    definitions = {
        node.name for node in shared.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for node in shared.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            definitions.update(target.id for target in targets if isinstance(target, ast.Name))
    for path in (AI_ROOT / "tool_groups").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            assert all(alias.name != "*" for alias in node.names), path.name
            if node.module == "app.ai.tool_groups._shared":
                assert all(alias.name in definitions for alias in node.names), path.name


def test_registry_is_the_single_catalog_assembly_owner():
    owners = []
    for path in AI_ROOT.rglob("*.py"):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name == "build_agent_tools":
                owners.append(path.relative_to(AI_ROOT).as_posix())
            assert getattr(node, "name", None) not in {"build_grouped_tools", "build_grouped_tool_map"}
    assert owners == ["tool_groups/registry.py"]
