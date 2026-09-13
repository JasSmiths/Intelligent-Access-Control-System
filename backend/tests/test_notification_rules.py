"""Notification activation ownership and transaction-participant contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.models import NotificationRule
from app.services import automations, notification_rules
from app.services.automations import AutomationService
from app.services.workflows.automation_definition import AutomationContext
from app.services.mutation_context import MutationError


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_machine_activation_changes_only_state_and_never_commits_or_impersonates_admin(active):
    row = NotificationRule(id=uuid.uuid4(), name="Synthetic notice", trigger_event="authorized_entry",
                           conditions=[], actions=[{"type": "in_app"}], is_active=not active)
    session = SimpleNamespace(scalar=AsyncMock(return_value=row), commit=AsyncMock(), rollback=AsyncMock())
    result = await notification_rules.set_automation_activation(
        session, reference={"notification_rule_id": str(row.id)}, active=active,
    )
    assert result == {"notification_rule_id": str(row.id), "is_active": active}
    assert row.name == "Synthetic notice" and row.actions == [{"type": "in_app"}]
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["exact", "partial", "ambiguous", "missing"])
async def test_machine_name_lookup_preserves_exact_and_unique_partial_matching(kind):
    rows = [NotificationRule(id=uuid.uuid4(), name=name, is_active=False)
            for name in ["Synthetic Arrival", "Synthetic Departure"]]
    name = {"exact": "SYNTHETIC ARRIVAL", "partial": "Arrival", "ambiguous": "Synthetic", "missing": "unknown"}[kind]
    session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)),
                              scalar=AsyncMock(return_value=rows[0]))
    if kind in {"ambiguous", "missing"}:
        with pytest.raises(MutationError, match="not found"):
            await notification_rules.set_automation_activation(session, reference={"notification_rule_name": name}, active=True)
        session.scalar.assert_not_awaited()
    else:
        result = await notification_rules.set_automation_activation(session, reference={"notification_rule_name": name}, active=True)
        assert result["notification_rule_id"] == str(rows[0].id) and rows[0].is_active


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_automation_delegates_activation_to_notification_owner(monkeypatch, active):
    target = str(uuid.uuid4())
    delegate = AsyncMock(return_value={"notification_rule_id": target, "is_active": active})
    monkeypatch.setattr(automations, "set_automation_activation", delegate)
    monkeypatch.setattr(automations, "is_maintenance_mode_active", AsyncMock(return_value=False))
    session = SimpleNamespace()
    reference = {"notification_rule_id": target}
    action = {"id": "synthetic-toggle", "type": "notification.enable" if active else "notification.disable", "config": reference}
    context = AutomationContext(trigger_key="time.every_x", subject="Synthetic timer", trigger_payload={})
    result = await AutomationService()._execute_action(session, action, context, rule=SimpleNamespace())
    delegate.assert_awaited_once_with(session, reference=reference, active=active)
    assert result["status"] == "success" and result["is_active"] is active


@pytest.mark.asyncio
async def test_missing_target_retains_machine_action_failure_code(monkeypatch):
    monkeypatch.setattr(automations, "set_automation_activation", AsyncMock(side_effect=MutationError("not_found", "Notification workflow not found.")))
    monkeypatch.setattr(automations, "is_maintenance_mode_active", AsyncMock(return_value=False))
    result = await AutomationService()._execute_action(
        SimpleNamespace(), {"id": "missing", "type": "notification.enable", "config": {}},
        AutomationContext(trigger_key="time.every_x", subject="Synthetic timer", trigger_payload={}), rule=SimpleNamespace(),
    )
    assert result["status"] == "failed" and result["error"] == "notification_rule_not_found"
