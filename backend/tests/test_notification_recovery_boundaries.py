"""Keep durable notification dispatch as the single production delivery owner."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def test_retired_notification_execution_paths_have_no_callers():
    retired = {
        "process_context",
        "process_context_with_result",
        "execute_rule_with_result",
        "_create_notification_run",
        "_finish_notification_run",
        "_mark_notification_run_started",
    }
    source = (APP / "services/notifications.py").read_text()
    tree = ast.parse(source)
    assert (
        not {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}
        & retired
    )
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in retired, str(path)


def test_journal_has_no_provider_or_realtime_dependencies():
    tree = ast.parse((APP / "services/notification_runs.py").read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert all(not name.startswith("app.modules") for name in imports if name)
    assert "app.services.event_bus" not in imports
    assert "app.services.notifications" not in imports


def test_trigger_listener_only_wakes_durable_dispatch():
    tree = ast.parse((APP / "services/notifications.py").read_text())
    listener = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle_realtime_event"
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"_deliver_action", "send_notification_now"}
        for node in ast.walk(listener)
    )
