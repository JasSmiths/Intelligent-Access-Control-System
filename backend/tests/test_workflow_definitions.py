"""Contract conformance and extension checks without database/provider startup."""

import ast
import copy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.models import AutomationRule, AutomationRun
from app.services import automation_intake
from app.services.automation_actions import AutomationActionExecutor
from app.services.automation_execution import AutomationRunStore, frozen_action_plan
from app.services.automation_policy import HARDWARE_ACTION_TYPES
from app.services.automations import AutomationService
from app.services.notification_rules import rule_values
from app.services.notifications import NotificationService
from app.services.workflows import action_definitions, automation_definition, channel_definitions, export_contracts, notification_payloads
from app.services.workflows.action_definitions import (
    ACTION_BY_TYPE, ACTION_DEFINITIONS, ActionDefinition, EmptyConfiguration,
    WorkflowConfigurationError, execution_actions, pending_action_error,
    require_exhaustive_bindings, validate_actions,
)
from app.services.workflows.channel_definitions import (
    CHANNEL_BY_TYPE, pending_notification_error, validate_notification_actions,
)


def executor():
    return AutomationActionExecutor(
        gates=AsyncMock(side_effect=AssertionError("unexpected hardware owner")),
        devices=AsyncMock(side_effect=AssertionError("unexpected hardware owner")),
        maintenance_active=AsyncMock(return_value=False),
        maintenance_change=AsyncMock(side_effect=AssertionError("unexpected independent commit")),
        notification_activation=AsyncMock(return_value={"is_active": True}),
        authorize_hardware=AsyncMock(side_effect=AssertionError("unexpected authorization")),
    )


def test_definitions_and_actual_runtime_bindings_are_exhaustive_and_inert():
    assert set(executor().handlers) == set(ACTION_BY_TYPE)
    assert set(NotificationService().delivery_handlers) == set(CHANNEL_BY_TYPE)
    assert HARDWARE_ACTION_TYPES == {"gate.open", "garage_door.open", "garage_door.close"}
    assert {key for key, definition in ACTION_BY_TYPE.items() if definition.execution == "transactional"} == {
        "notification.enable", "notification.disable",
    }
    assert {key for key, definition in ACTION_BY_TYPE.items() if definition.continue_after_unknown} == {
        "notification.enable", "notification.disable",
    }
    assert ACTION_BY_TYPE["maintenance_mode.enable"].execution == "durable_effect"
    with pytest.raises(WorkflowConfigurationError, match="missing"):
        require_exhaustive_bindings(ACTION_BY_TYPE, {}, owner="fixture")
    with pytest.raises(WorkflowConfigurationError, match="unknown"):
        require_exhaustive_bindings({}, {"invented": object()}, owner="fixture")
    with pytest.raises(WorkflowConfigurationError, match="Duplicate"):
        action_definitions.definitions_by_type((ACTION_DEFINITIONS[0], ACTION_DEFINITIONS[0]))


def test_pure_contract_modules_cannot_import_runtime_policy_or_context_owners():
    owners = Path(action_definitions.__file__).parent
    for filename in ("action_definitions.py", "channel_definitions.py", "export_contracts.py"):
        modules = {
            node.module for node in ast.walk(ast.parse((owners / filename).read_text()))
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.")
        }
        assert modules <= {
            "app.services.workflows.action_definitions",
            "app.services.workflows.channel_definitions",
        }


@pytest.mark.parametrize("raw", [
    None, {}, ["mobile"], [{"type": "retired"}],
    [{"type": "gate.open", "config": {"reason": "silently ignored before"}}],
    [{"type": "garage_door.open", "config": {"target_entity_ids": "cover.garage"}}],
    [{"type": "notification.enable", "config": {"notification_rule_id": 42}}],
    [{"type": "integration.icloud_calendar.sync", "config": {"provider": "other"}}],
    [{"type": "gate.open"}, {"type": "retired"}],
])
def test_new_automation_writes_reject_instead_of_drop_malformed_or_unsupported_actions(raw):
    with pytest.raises(WorkflowConfigurationError):
        validate_actions(raw)


@pytest.mark.parametrize("raw", [
    [{"type": "retired"}], [{"type": "mobile", "target_mdoe": "selected"}],
    [{"type": "voice", "target_mode": "wrong"}],
    [{"type": "mobile", "media": {"attach_camera_snapshot": "yes"}}],
    [{"type": "in_app", "actionable": {"enabled": True, "action": ""}}],
    [{"type": "mobile", "variable_recipients": {"other_field": []}}],
])
def test_new_notification_writes_reject_config_and_nested_typos(raw):
    with pytest.raises(WorkflowConfigurationError):
        validate_notification_actions(raw)


def test_defaults_and_canonical_references_preserve_existing_meaning_without_shared_mutability():
    assert validate_actions([{"type": "notification.enable", "config": {
        "notification_rule_id": " rule-id ", "notification_rule_name": " Notice ",
    }}])[0]["config"] == {"notification_rule_id": "rule-id", "notification_rule_name": "Notice"}
    targets = [" cover.main ", "", "  ", "cover.main"]
    assert validate_actions([{"type": "garage_door.open", "config": {
        "target_entity_ids": targets,
    }}])[0]["config"] == {"target_entity_ids": ["cover.main", "cover.main"]}
    assert targets == [" cover.main ", "", "  ", "cover.main"]
    first = ACTION_BY_TYPE["garage_door.open"].defaults()
    first["target_entity_ids"].append("mutated")
    assert ACTION_BY_TYPE["garage_door.open"].defaults() == {"target_entity_ids": []}
    first_channel = CHANNEL_BY_TYPE["mobile"].defaults(editor=True)
    first_channel["media"]["camera_id"] = "mutated"
    first_channel["target_ids"].append("mutated")
    assert CHANNEL_BY_TYPE["mobile"].defaults(editor=True)["media"]["camera_id"] == ""
    assert CHANNEL_BY_TYPE["mobile"].defaults(editor=True)["target_ids"] == []
    assert CHANNEL_BY_TYPE["voice"].defaults(editor=True)["message_template"] == "@FirstName has arrived at the gate."


def test_variable_recipient_semantics_are_checked_after_typed_shape():
    with pytest.raises(ValueError, match="no longer match"):
        rule_values({"name": "Fixture", "trigger_event": "authorized_entry", "actions": [{
            "type": "mobile", "title_template": "@FirstName",
            "variable_recipients": {"title_template": [{
                "occurrence": 1, "name": "FirstName", "target_ids": ["apprise:0"],
            }]},
        }]})


def test_historical_unknown_records_are_readable_while_unfinished_config_is_held():
    unknown = {"id": "retained", "type": "retired", "config": {"old": ["exact"]}, "reason_template": "old"}
    assert automation_definition.normalize_actions([unknown]) == [unknown]
    notice = {"id": "retained", "type": "retired", "old_config": {"exact": True}}
    assert notification_payloads.normalize_actions([notice]) == [notice]
    assert pending_action_error([{"state": "pending", "action": unknown}]) == "unsupported_action_configuration"
    assert pending_action_error([{"state": "succeeded", "action": unknown}]) is None
    assert pending_notification_error([{"state": "accepted", "action": notice}]) is None
    malformed = [{"type": "gate.open", "config": {"unrecognized": "kept"}}]
    before = copy.deepcopy(malformed)
    adapted = execution_actions(malformed)
    assert adapted[0]["config"] == malformed[0]["config"]
    assert pending_action_error([{"state": "pending", "action": adapted[0]}]) == "unsupported_action_configuration"
    assert malformed == before


def rendered(channel="mobile"):
    return {"state": "pending", "rule": {"id": "rule", "name": "Fixture"}, "action": {
        "id": "a", "type": channel, **CHANNEL_BY_TYPE[channel].defaults(),
        "title": "Fixture", "message": "Fixture body", "snapshot": None,
    }}


def test_rendered_notification_adapter_accepts_scoped_content_and_exact_frozen_destinations():
    item = rendered()
    item["action"].update(
        frozen_mobile_targets=["notify.mobile_app_fixture"], frozen_apprise_indexes=[0],
        recipient_content={"apprise:0": {"title": "Scoped", "message": "Body"}},
    )
    before = copy.deepcopy(item)
    assert pending_notification_error([item]) is None
    assert item == before


@pytest.mark.parametrize("mutation", [
    lambda item: item.pop("state"),
    lambda item: item.update(state=[]),
    lambda item: item.update(state={}),
    lambda item: item.update(state=None),
    lambda item: item.pop("rule"),
    lambda item: item["action"].pop("id"),
    lambda item: item["action"].update(type="retired"),
    lambda item: item["action"].update(target_mdoe="selected"),
    lambda item: item["action"].update(media=None),
    lambda item: item["action"].update(frozen_apprise_indexes=[-1]),
])
def test_malformed_pending_notification_adapter_holds_before_attempt(mutation):
    item = rendered()
    mutation(item)
    assert pending_notification_error([item]) is not None


@pytest.mark.parametrize("literal", [
    {"type": "voice", "delivery_mode": "literal", "target": "media_player.fixture", "title": "Announcement",
     "message": "Fixture", "configured_default": True},
    {"type": "mobile", "delivery_mode": "literal", "target": "notify.mobile_app_fixture", "title": "Test", "message": "Fixture"},
    {"type": "mobile", "delivery_mode": "literal", "target": "notify.mobile_app_fixture", "title": "Allow entry",
     "message": "Fixture", "resident_recovery_output": {"attempt_id": "fixture", "kind": "approval", "context_id": None}},
    {"type": "mobile", "delivery_mode": "literal", "target": "notify.mobile_app_fixture", "title": "Entry",
     "message": "Fixture", "actionable_output": {"context_id": "fixture", "version": 1}},
])
def test_observed_literal_producers_keep_index_identity_and_native_content(literal):
    item = {"state": "pending", "rule": {"id": "literal", "name": "Fixture"}, "action": literal}
    before = copy.deepcopy(item)
    assert pending_notification_error([item]) is None
    assert item == before


def test_attempted_unknown_payload_is_not_validated_or_normalized_during_recovery():
    identity = uuid.uuid4()
    plan = frozen_action_plan(identity, [{"action": {
        "id": "retained", "type": "retired", "config": {"old_target": [" target ", "target"]},
    }, "target_plans": [{"physical_identity": "frozen", "ordered": [2, 1]}]}])
    plan[0].update(state="attempting", attempted_at="retained-attempt-time")
    before = copy.deepcopy(plan[0])
    row = AutomationRun(id=identity, action_plan=plan, action_results=[], condition_results=[])
    AutomationRunStore._recover_attempts(row, datetime.now(tz=UTC))
    for key in ("action", "target_plans", "operation_id", "idempotency_key", "attempted_at"):
        assert row.action_plan[0][key] == before[key]
    assert row.action_plan[0]["state"] == "unknown"
    notice = {"state": "attempting", "action": {"type": "retired", "media": "unparseable",
              "frozen_mobile_targets": [" old target "]}}
    original = copy.deepcopy(notice)
    assert pending_notification_error([notice]) is None
    assert notice == original


def test_export_is_deterministic_fresh_and_rejects_unsupported_schema_constructs():
    generated = Path(__file__).resolve().parents[2] / "frontend/src/api/generated/workflows.ts"
    assert export_contracts.render_contracts() == generated.read_text()
    assert export_contracts.render_contracts() == export_contracts.render_contracts()
    for schema in ({"type": "string", "pattern": ".*"}, {"type": "object", "additionalProperties": True},
                   {"anyOf": [{"type": "string"}, {"type": "integer"}]}):
        with pytest.raises(export_contracts.UnsupportedSchema):
            export_contracts.schema_type(schema, {})


async def test_temporary_new_action_extends_definitions_handler_editor_and_reservation_without_dispatch_branches(monkeypatch):
    action_type = "fixture.record"
    definition = ActionDefinition(
        type=action_type, group="fixture", group_label="Fixture", label="Fixture",
        description="Inert fixture", configuration=EmptyConfiguration,
        execution="durable_effect", effect="configuration", editor="none",
    )
    monkeypatch.setitem(ACTION_BY_TYPE, action_type, definition)
    sink = AsyncMock(return_value={"id": "new", "type": action_type, "status": "success"})
    original_bindings = AutomationActionExecutor._bind_handlers
    monkeypatch.setattr(AutomationActionExecutor, "_bind_handlers",
        lambda self: {**original_bindings(self), action_type: sink})
    monkeypatch.setattr(export_contracts, "ACTION_DEFINITIONS", (*ACTION_DEFINITIONS, definition))
    rendered_types = export_contracts.render_contracts()
    assert f'"{action_type}": Record<string, never>' in rendered_types
    assert f'"{action_type}": {{\n    "editor": "none"' in rendered_types
    action = validate_actions([{"id": "new", "type": action_type}])[0]
    owner = executor()
    context = automation_definition.AutomationContext(trigger_key="time.every_x", subject="Fixture", trigger_payload={})
    rule = AutomationRule(id=uuid.uuid4(), name="Fixture", actions=[action], triggers=[], trigger_keys=[], conditions=[])
    reserved = AsyncMock(return_value=uuid.uuid4())
    monkeypatch.setattr(automation_intake.AutomationRunStore, "reserve", reserved)
    session = SimpleNamespace(scalar=AsyncMock(return_value=datetime.now(tz=UTC)))
    await automation_intake.reserve_occurrence(
        session, rule, context, origin_kind="fixture", origin_id="fixture-origin", actor="Fixture", source="fixture",
    )
    assert reserved.await_args.kwargs["planned"][0]["action"] == action
    result = await owner.execute(session, action, context, rule=rule)
    assert result.status == "success"
    sink.assert_awaited_once()
    assert set(owner.handlers) == set(ACTION_BY_TYPE)
    broken = dict(owner.handlers)
    broken.pop(action_type)
    with pytest.raises(WorkflowConfigurationError, match="missing"):
        require_exhaustive_bindings(ACTION_BY_TYPE, broken, owner="fixture")


@pytest.mark.parametrize("raw", [
    [{"id": "typo", "type": "mobile", "target_mdoe": "selected"}],
    [{"id": "retired", "type": "retired", "custom": {"ordered": [2, 1]}}],
    [{"id": "nested", "type": "mobile", "media": {"camera_id_typo": "camera.private"}}],
    [{"id": "recipient", "type": "mobile", "title_template": "@FirstName",
      "variable_recipients": {"title_template": [{"occurrence": 1, "name": "FirstName", "target_ids": ["apprise:0"]}]}}],
])
async def test_executable_notification_overrides_validate_original_content_before_normalization(raw, monkeypatch):
    from app.api.v1 import notifications as api
    from app.services.notifications import sample_notification_context
    from fastapi import HTTPException

    before = copy.deepcopy(raw)
    rule = {"id": str(uuid.uuid4()), "name": "Retained", "trigger_event": "authorized_entry", "actions": raw}
    service = NotificationService()
    with pytest.raises(WorkflowConfigurationError):
        await service.planner.build(sample_notification_context("authorized_entry"), [rule])
    assert raw == before
    from app.models import NotificationRule
    persisted = NotificationRule(id=uuid.UUID(rule["id"]), name=rule["name"], trigger_event=rule["trigger_event"],
                                actions=copy.deepcopy(raw), conditions=[], is_active=True)
    monkeypatch.setattr(service.planner, "_rules_for_context", AsyncMock(return_value=[persisted]))
    with pytest.raises(WorkflowConfigurationError):
        await service.planner.build(sample_notification_context("authorized_entry"), None)
    assert persisted.actions == before

    send = AsyncMock(side_effect=AssertionError("Malformed content reached confirmation/send"))
    monkeypatch.setattr(api, "send_confirmed_notification", send)
    with pytest.raises(HTTPException) as error:
        await api.test_notification_rule_payload(
            api.NotificationRuleTestRequest(rule=rule), user=SimpleNamespace(), session=SimpleNamespace(),
        )
    assert error.value.status_code == 400
    now = datetime.now(tz=UTC)
    stored = SimpleNamespace(**rule, conditions=[], is_active=False, created_at=now, updated_at=now)
    monkeypatch.setattr(api, "get_rule_or_404", AsyncMock(return_value=stored))
    with pytest.raises(HTTPException) as error:
        await api.test_notification_rule(uuid.UUID(rule["id"]), user=SimpleNamespace(), session=SimpleNamespace())
    assert error.value.status_code == 400
    send.assert_not_awaited()
    assert stored.actions == before


@pytest.mark.parametrize("raw", [{"actions": []}, [None], [{"actions": [None]}]])
async def test_malformed_notification_override_envelope_holds_before_display_normalization(raw):
    from app.services.notifications import sample_notification_context
    with pytest.raises(WorkflowConfigurationError):
        await NotificationService().planner.build(sample_notification_context("authorized_entry"), raw)
