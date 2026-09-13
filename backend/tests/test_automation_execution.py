"""Stable automation occurrence/action identities and immutable captured inputs."""

import copy
from datetime import UTC, datetime
import uuid

import pytest

from app.services.automation_execution import frozen_action_plan, occurrence_key


def test_origin_identity_includes_rule_trigger_and_server_origin():
    rule = uuid.uuid4()
    first = occurrence_key("access_event", "synthetic-event", rule, "vehicle.known_plate")
    assert first == occurrence_key("access_event", "synthetic-event", rule, "vehicle.known_plate")
    variants = [
        occurrence_key("access_event", "synthetic-event", uuid.uuid4(), "vehicle.known_plate"),
        occurrence_key("access_event", "synthetic-event", rule, "visitor_pass.used"),
        occurrence_key("access_event", "synthetic-event-2", rule, "vehicle.known_plate"),
        occurrence_key("chat_message", "synthetic-event", rule, "vehicle.known_plate"),
    ]
    assert len({first, *variants}) == 5
    assert len(first) <= 255 and "synthetic-event" not in first


@pytest.mark.parametrize("origin,identity,trigger", [("", "x", "t"), ("o", " ", "t"), ("o", "x", "")])
def test_missing_origin_identity_is_rejected(origin, identity, trigger):
    with pytest.raises(ValueError, match="stable"):
        occurrence_key(origin, identity, uuid.uuid4(), trigger)


def test_action_identity_is_stable_across_retry_and_unique_by_occurrence_and_ordinal():
    run = uuid.uuid4()
    inputs = [{"action": {"id": "same-configured-id", "type": "gate.open"}}] * 2
    first = frozen_action_plan(run, inputs)
    assert first == frozen_action_plan(run, inputs)
    assert first[0]["operation_id"] != first[1]["operation_id"]
    assert first[0]["operation_id"] != frozen_action_plan(uuid.uuid4(), inputs)[0]["operation_id"]
    assert first[0]["idempotency_key"] == first[0]["operation_id"]


def test_plan_does_not_accept_checkpoint_identity_from_action_input_or_mutate_source():
    inputs = [{"action": {"id": "a", "type": "gate.open", "config": {"target": "synthetic"}},
               "operation_id": "forged", "idempotency_key": "forged", "state": "succeeded",
               "result": {"accepted": True}, "attempted_at": "forged"}]
    before = copy.deepcopy(inputs)
    plan = frozen_action_plan(uuid.uuid4(), inputs)
    assert inputs == before
    assert plan[0]["state"] == "pending" and plan[0]["operation_id"] != "forged"
    assert "result" not in plan[0] and "attempted_at" not in plan[0]
    plan[0]["action"]["config"]["target"] = "modified-copy"
    assert inputs == before


def test_dispatch_input_is_not_truncated_as_telemetry():
    text = "synthetic " * 2000
    plan = frozen_action_plan(uuid.uuid4(), [{"action": {"id": "a", "type": "integration.whatsapp.send_message", "config": {"message_template": text}}}])
    assert plan[0]["action"]["config"]["message_template"] == text


@pytest.mark.parametrize("action", [{}, {"id": "a"}, {"type": "gate.open"}])
def test_missing_configured_action_identity_is_rejected(action):
    with pytest.raises(ValueError, match="configured ID"):
        frozen_action_plan(uuid.uuid4(), [{"action": action}])


def test_nonpersistable_plan_input_is_rejected_before_reservation():
    with pytest.raises(TypeError):
        frozen_action_plan(uuid.uuid4(), [{"action": {"id": "a", "type": "gate.open"}, "time": datetime.now(tz=UTC)}])


def test_fingerprint_excludes_progress_but_binds_actual_configuration():
    from app.models import AutomationRule
    from app.services.automation_authorization import automation_rule_fingerprint
    rule = AutomationRule(name="Synthetic", triggers=[], conditions=[], actions=[])
    before = automation_rule_fingerprint(rule)
    rule.run_count, rule.is_active, rule.last_run_status = 10, False, "failed"
    assert automation_rule_fingerprint(rule) == before
    rule.actions = [{"id": "changed", "type": "gate.open", "config": {}}]
    assert automation_rule_fingerprint(rule) != before


@pytest.mark.parametrize("outcome,expected", [
    ({"status": "success", "requires_reconciliation": True}, "unknown"),
    ({"status": "failed", "delivery": "unknown"}, "unknown"),
    ({"status": "failed", "delivery": "not_sent"}, "failed"),
    ({"status": "skipped", "command_sent": False}, "skipped"),
    ({"status": "queued", "delivered_count": 0}, "succeeded"),
    ({"status": "success", "delivery": "accepted", "requires_reconciliation": False}, "succeeded"),
])
def test_action_checkpoint_preserves_delivery_uncertainty(outcome, expected):
    from app.services.automation_dispatch import action_checkpoint_state
    assert action_checkpoint_state(outcome) == expected


def test_public_recovery_contract_exposes_operation_identity_without_private_dispatch_inputs():
    import json
    from pathlib import Path
    from app.models import AutomationRun
    from app.services.automations import serialize_run
    fixture_path = Path(__file__).parent / "contracts/fixtures/automations/recovery_run.json"
    expected = json.loads(fixture_path.read_text())
    run = AutomationRun(id=uuid.UUID(expected["id"]), rule_id=uuid.UUID(expected["rule_id"]),
        trigger_key="time.every_x", status="review_required", recovery_version=1,
        started_at=datetime(2026, 9, 12, 12, tzinfo=UTC), finished_at=datetime(2026, 9, 12, 12, 5, tzinfo=UTC),
        trigger_payload={}, context={"version": 1, "rule_fingerprint": "private-binding", "dispatch": {"private_input": "synthetic"}},
        action_plan=[{"index": 0, "action": {"id": "gate", "type": "gate.open"}, "operation_id": expected["action_states"][0]["operation_id"], "state": "unknown"}],
        action_results=expected["action_results"], condition_results=[], review_reason="action_outcome_unknown",
        error="action_outcome_unknown", actor="Automation Engine", source="scheduler")
    assert serialize_run(run) == expected
    generated = Path(__file__).resolve().parents[2] / "frontend/src/api/fixtures/automationRecovery.generated.json"
    assert generated.read_bytes() == fixture_path.read_bytes()
