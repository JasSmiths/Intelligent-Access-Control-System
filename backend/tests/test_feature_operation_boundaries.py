"""Keep mutation policy out of transport adapters."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1] / "app"


def test_notification_crud_adapters_cannot_persist_rules_independently():
    for relative in ["api/v1/notifications.py", "ai/tool_groups/notifications_handlers.py"]:
        tree = ast.parse((ROOT / relative).read_text())
        for function in tree.body:
            if not isinstance(function, ast.AsyncFunctionDef) or not function.name.startswith(
                ("create_", "update_", "delete_")
            ):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert not (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "session"
                        and node.func.attr in {"add", "commit", "delete"}
                    ), function.name
                assert not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "NotificationRule"
                ), function.name


def test_automation_adapter_does_not_normalize_mutations():
    tree = ast.parse((ROOT / "ai/tool_groups/automations_handlers.py").read_text())
    for function in tree.body:
        if isinstance(function, ast.AsyncFunctionDef) and function.name in {
            "create_automation",
            "edit_automation",
        }:
            assert not any(
                isinstance(n, ast.Name) and n.id.startswith("normalize_automation_")
                for n in ast.walk(function)
            )
