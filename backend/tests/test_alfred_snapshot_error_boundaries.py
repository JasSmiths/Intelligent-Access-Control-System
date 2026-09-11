from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.ai.tool_groups import access_diagnostics_handlers as diagnostics


@pytest.mark.parametrize("error", [AttributeError, OSError, TypeError, ValueError, SQLAlchemyError])
def test_unavailable_snapshot_metadata_remains_optional(monkeypatch, error):
    def unavailable(_row):
        raise error("Synthetic unavailable metadata")

    monkeypatch.setattr(diagnostics, "alert_snapshot_metadata", unavailable)
    row = SimpleNamespace(context=None, event=None)
    assert diagnostics._alert_snapshot_for_agent(row) is None
    assert diagnostics._alert_snapshot_file(row) is None


@pytest.mark.parametrize("error", [OSError, RuntimeError, TypeError, ValueError])
def test_unavailable_snapshot_path_remains_optional(monkeypatch, error):
    def unavailable(_path):
        raise error("Synthetic unavailable snapshot")

    monkeypatch.setattr(diagnostics, "alert_snapshot_metadata", lambda _row: {"url": "/api/v1/events/synthetic/snapshot"})
    monkeypatch.setattr(diagnostics, "get_snapshot_manager", lambda: SimpleNamespace(resolve_path=unavailable))
    row = SimpleNamespace(event=SimpleNamespace(snapshot_path="synthetic.jpg"))
    assert diagnostics._alert_snapshot_file(row) is None
