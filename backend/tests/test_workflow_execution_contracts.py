"""Internal workflow boundaries retain identity, delivery truth and JSON shape."""

import copy
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.automation_actions import AutomationActionExecutor
from app.services.automation_integration_actions import registered_integration_action_types
from app.services.workflows.automation_definition import ACTION_CATALOG, AutomationContext
from app.services.workflows.execution_contracts import (
    AutomationActionResult,
    checked_action,
    checked_execution,
    checked_notification_plan,
)


def action(kind: str = "notification.enable") -> dict:
    return {"id": "configured-action", "type": kind, "config": {}}


@pytest.mark.parametrize("value", [None, [], "action"])
def test_action_boundary_rejects_non_objects(value):
    with pytest.raises(TypeError):
        checked_action(value)


@pytest.mark.parametrize("value", [{}, {"id": "", "type": "gate.open"},
                                     {"id": "a", "type": 1}, {"id": "a", "type": "gate.open", "config": []}])
def test_action_boundary_rejects_invalid_identity_or_configuration(value):
    with pytest.raises(ValueError):
        checked_action(value)


def test_result_preserves_receipts_without_mutating_the_checkpoint():
    payload = {**action(), "status": "success", "delivery": "accepted",
               "requires_reconciliation": True, "outcomes": [{"delivery": "unknown"}]}
    before = copy.deepcopy(payload)
    result = AutomationActionResult.from_payload(payload, action=checked_action(action()))
    assert result.checkpoint_state == "unknown"
    assert result.as_payload() == before
    result.as_payload()["outcomes"][0]["delivery"] = "accepted"
    assert payload == before
    assert result.as_payload() == before


@pytest.mark.parametrize("status,details,expected", [
    ("success", {}, "succeeded"), ("queued", {}, "succeeded"),
    ("failed", {"delivery": "rejected"}, "failed"),
    ("failed", {"delivery": "unknown"}, "unknown"),
    ("success", {"requires_review": True}, "unknown"),
    ("skipped", {"requires_review": True}, "skipped"),
])
def test_checked_result_does_not_promote_ambiguous_delivery(status, details, expected):
    result = AutomationActionResult.from_payload({**action(), "status": status, **details})
    assert result.checkpoint_state == expected


def test_result_cannot_rebind_a_configured_action():
    with pytest.raises(ValueError, match="identity"):
        AutomationActionResult.from_payload({**action(), "id": "different", "status": "success"},
                                            action=checked_action(action()))
    with pytest.raises(ValueError, match="status"):
        AutomationActionResult.from_payload({**action(), "status": "accepted"})


def test_plan_checks_pending_work_and_keeps_terminal_historical_rows():
    plan = [{"state": "skipped", "reason": "historical"},
            {"state": "unknown", "reason": "provider_outcome_unknown"},
            {"state": "pending", "rule": {"name": "confirmed"},
             "action": {"type": "voice", "delivery_mode": "literal", "target": "media_player.fixture"}}]
    assert checked_notification_plan(plan) is plan
    with pytest.raises(ValueError, match="channel"):
        checked_notification_plan([{"state": "pending", "rule": {}, "action": {}}])
    with pytest.raises(ValueError, match="states"):
        checked_notification_plan([{"state": "unrecognised"}])


def executor():
    return AutomationActionExecutor(
        gates=AsyncMock(), devices=AsyncMock(), maintenance_active=AsyncMock(return_value=False),
        maintenance_change=AsyncMock(), notification_activation=AsyncMock(return_value={"enabled": True}),
        integrations=AsyncMock(), authorize_hardware=AsyncMock(),
    )


def test_supported_actions_have_exactly_one_registered_handler():
    supported = {entry["type"] for group in ACTION_CATALOG for entry in group["actions"]}
    assert set(executor().handlers) == supported | registered_integration_action_types()


async def test_malformed_action_cannot_reach_any_domain_owner():
    owner = executor()
    with pytest.raises(ValueError):
        await owner.execute(SimpleNamespace(), {"id": "fixture", "type": ""},
                            AutomationContext("webhook.received", "Fixture", {}),
                            rule=SimpleNamespace(name="Fixture"))
    owner.maintenance_active.assert_not_awaited()
    owner.notification_activation.assert_not_awaited()
    owner.maintenance_change.assert_not_awaited()
    owner.integrations.assert_not_awaited()


def execution_checkpoint():
    operation = str(uuid.uuid4())
    return {"run_id": str(uuid.uuid4()), "claim_token": str(uuid.uuid4()), "index": 0,
            "operation_id": operation, "idempotency_key": operation,
            "target_plan": {"version": 1, "targets": []}, "expires_at": datetime.now(tz=UTC)}


def test_execution_checkpoint_preserves_owned_operation_and_checked_deadline():
    checkpoint = execution_checkpoint()
    assert checked_execution(checkpoint) is checkpoint


@pytest.mark.parametrize("key,value", [
    ("run_id", "invalid"), ("claim_token", ""), ("operation_id", None),
    ("index", True), ("index", -1), ("idempotency_key", ""),
    ("target_plan", []), ("target_plans", [{} , "invalid"]),
    ("expires_at", datetime(2026, 1, 1)), ("automatic_entry_policy", "true"),
])
def test_execution_checkpoint_rejects_malformed_owned_state(key, value):
    checkpoint = execution_checkpoint()
    checkpoint[key] = value
    with pytest.raises((TypeError, ValueError)):
        checked_execution(checkpoint)
