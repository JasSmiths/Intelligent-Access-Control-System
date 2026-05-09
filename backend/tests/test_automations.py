from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from app.ai import tools as ai_tools
from app.api.v1 import automations as automations_api
from app.models import AutomationRule, AutomationRun, Presence
from app.models.enums import PresenceState
from app.services import automation_integration_actions
from app.services import automations
from app.services.automation_integration_actions import registered_integration_action_types
from app.services.automations import (
    ACTION_CATALOG,
    CONDITION_CATALOG,
    TRIGGER_CATALOG,
    AutomationContext,
    AutomationService,
    ScheduledAutomationClaim,
    build_context_variables,
    context_missing_references,
    cron_from_recurrence,
    deterministic_schedule_parse,
    due_time_trigger,
    facts_from_payload,
    next_run_for_trigger,
    normalize_actions,
    normalize_conditions,
    normalize_triggers,
    trigger_keys_for_triggers,
    validate_schedule_parse,
)
from app.services.event_bus import RealtimeEvent


def make_request(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers or [],
        },
        receive,
    )


def test_automation_registries_expose_required_keys() -> None:
    trigger_keys = {item["type"] for group in TRIGGER_CATALOG for item in group["triggers"]}
    condition_keys = {item["type"] for group in CONDITION_CATALOG for item in group["conditions"]}
    action_keys = {item["type"] for group in ACTION_CATALOG for item in group["actions"]}

    assert {
        "time.specific_datetime",
        "time.every_x",
        "time.cron",
        "time.ai_text",
        "vehicle.known_plate",
        "vehicle.unknown_plate",
        "vehicle.outside_schedule",
        "maintenance_mode.enabled",
        "maintenance_mode.disabled",
        "visitor_pass.created",
        "visitor_pass.detected",
        "visitor_pass.used",
        "visitor_pass.expired",
        "ai.phrase_received",
        "ai.issue_detected",
        "webhook.received",
        "webhook.unrecognized",
        "webhook.new_sender",
    }.issubset(trigger_keys)
    assert {
        "person.on_site",
        "person.off_site",
        "vehicle.on_site",
        "vehicle.off_site",
        "maintenance_mode.enabled",
        "maintenance_mode.disabled",
    }.issubset(condition_keys)
    assert {
        "notification.enable",
        "notification.disable",
        "gate.open",
        "garage_door.open",
        "garage_door.close",
        "maintenance_mode.enable",
        "maintenance_mode.disable",
    }.issubset(action_keys)


def test_automation_schema_normalization_shapes_payloads() -> None:
    triggers = normalize_triggers(
        [
            {"type": "vehicle.outside_schedule", "config": {"person_id": "person-1", "ignored": True}},
            {"type": "not.allowed", "config": {}},
        ]
    )
    conditions = normalize_conditions(
        [{"type": "person.on_site", "config": {"person_id": "person-1", "extra": "ignored"}}]
    )
    actions = normalize_actions(
        [
            {
                "type": "garage_door.open",
                "config": {"target_entity_ids": ["cover.left", ""]},
                "reason_template": "@DisplayName arrived.",
            }
        ]
    )

    assert trigger_keys_for_triggers(triggers) == ["vehicle.outside_schedule"]
    assert triggers[0]["config"] == {"person_id": "person-1"}
    assert conditions[0]["config"] == {"person_id": "person-1"}
    assert actions[0]["config"] == {"target_entity_ids": ["cover.left"]}
    assert actions[0]["reason_template"] == "@DisplayName arrived."


def test_integration_action_normalization_sets_provider_and_action() -> None:
    actions = normalize_actions(
        [
            {
                "type": "integration.icloud_calendar.sync",
                "config": {},
            },
            {
                "type": "integration.whatsapp.send_message",
                "config": {
                    "target_mode": "dynamic",
                    "phone_number_template": "@AdminPhone",
                    "message_template": "@Subject",
                    "target_user_ids": ["ignored-for-dynamic"],
                },
            }
        ]
    )

    assert "integration.icloud_calendar.sync" in registered_integration_action_types()
    assert "integration.whatsapp.send_message" in registered_integration_action_types()
    assert actions[0] == {
        "id": "action-1",
        "type": "integration.icloud_calendar.sync",
        "config": {"provider": "icloud_calendar", "action": "sync_calendars"},
        "reason_template": "",
    }
    assert actions[1]["type"] == "integration.whatsapp.send_message"
    assert actions[1]["config"] == {
        "provider": "whatsapp",
        "action": "send_message",
        "target_mode": "dynamic",
        "target_user_ids": ["ignored-for-dynamic"],
        "phone_number_template": "@AdminPhone",
        "message_template": "@Subject",
    }


@pytest.mark.asyncio
async def test_visitor_pass_used_context_maps_registration_variable() -> None:
    context = await AutomationService().context_for_trigger(
        "visitor_pass.used",
        {
            "visitor_pass": {
                "id": "not-a-db-uuid",
                "visitor_name": "Pat",
                "status": "used",
                "number_plate": "AB12 CDE",
                "vehicle_make": "Tesla",
                "vehicle_colour": "Blue",
                "duration_human": "42 minutes",
            },
            "occurred_at": "2026-04-30T20:15:00+01:00",
        },
    )

    assert context.variables["VisitorPassVehicleRegistration"] == "AB12 CDE"
    assert context.variables["Registration"] == "AB12 CDE"
    assert context.variables["VisitorName"] == "Pat"
    assert context_missing_references(context, {"reason_template": "@VisitorPassVehicleRegistration"}) == []
    assert context.to_payload()["missing_required_variables"] == []


def test_missing_variable_references_skip_unavailable_trigger_scopes() -> None:
    context = AutomationContext(
        trigger_key="time.cron",
        subject="Schedule tick",
        trigger_payload={},
        facts={"occurred_at": "2026-04-30T20:15:00+01:00"},
        scopes={"time", "event"},
    )
    context.variables = build_context_variables(context)

    missing = context_missing_references(
        context,
        {"reason_template": "Open for @VisitorPassVehicleRegistration"},
    )

    assert missing == ["VisitorPassVehicleRegistration"]
    assert context.to_payload()["missing_required_variables"] == ["VisitorPassVehicleRegistration"]
    assert "not available" in context.warnings[0]


def test_missing_unknown_variable_references_skip_cleanly() -> None:
    context = AutomationContext(
        trigger_key="vehicle.known_plate",
        subject="Known plate",
        trigger_payload={},
        facts={"registration_number": "AB12 CDE"},
        scopes={"vehicle", "event"},
    )
    context.variables = build_context_variables(context)

    missing = context_missing_references(context, {"reason_template": "Open for @UnknownVariable"})

    assert missing == ["UnknownVariable"]
    assert context.to_payload()["missing_required_variables"] == ["UnknownVariable"]
    assert "Unknown variable @UnknownVariable" in context.warnings[0]


def test_event_bridge_uses_direct_access_event_domain_events() -> None:
    service = AutomationService()
    created_at = "2026-04-30T20:15:00+00:00"

    known = service._event_to_triggers(
        RealtimeEvent(
            "access_event.finalized",
            {"decision": "granted", "vehicle_id": "vehicle-1", "occurred_at": created_at},
            created_at,
        )
    )
    outside_schedule = service._event_to_triggers(
        RealtimeEvent(
            "access_event.finalized",
            {"decision": "denied", "vehicle_id": "vehicle-1", "occurred_at": created_at},
            created_at,
        )
    )
    unknown = service._event_to_triggers(
        RealtimeEvent(
            "access_event.finalized",
            {"decision": "denied", "registration_number": "AB12 CDE", "occurred_at": created_at},
            created_at,
        )
    )
    backfilled = service._event_to_triggers(
        RealtimeEvent(
            "access_event.finalized",
            {"decision": "granted", "vehicle_id": "vehicle-1", "occurred_at": created_at, "backfilled": True},
            created_at,
        )
    )

    assert known[0][0] == "vehicle.known_plate"
    assert outside_schedule[0][0] == "vehicle.outside_schedule"
    assert unknown[0][0] == "vehicle.unknown_plate"
    assert backfilled == []


def test_event_bridge_ignores_notification_and_automation_status_events() -> None:
    service = AutomationService()
    for event_type in (
        "notification.trigger",
        "notification.sent",
        "notification.failed",
        "notification.skipped",
        "automation.run.success",
        "automation.run.failed",
        "automation.run.skipped",
    ):
        assert service._event_to_triggers(RealtimeEvent(event_type, {"event_type": "unauthorized_plate"}, "2026-04-30T20:15:00+00:00")) == []


@pytest.mark.asyncio
async def test_person_condition_evaluation_uses_presence_state() -> None:
    person_id = "11111111-1111-1111-1111-111111111111"

    class Session:
        async def get(self, model, _id):
            assert model is Presence
            return SimpleNamespace(state=PresenceState.PRESENT)

    context = AutomationContext(
        trigger_key="vehicle.known_plate",
        subject="Steph arrived",
        trigger_payload={},
        facts={"person_id": person_id},
        entities={"person_id": person_id},
        scopes={"person", "vehicle", "event"},
    )
    context.variables = build_context_variables(context)

    result = await AutomationService()._evaluate_condition(
        Session(),
        {"id": "condition-1", "type": "person.on_site", "config": {}},
        context,
    )

    assert result["passed"] is True
    assert result["details"]["person_id"] == person_id


@pytest.mark.asyncio
async def test_maintenance_mode_skips_actions_but_allows_disable(monkeypatch) -> None:
    async def active() -> bool:
        return True

    async def set_mode(active_state, **_kwargs):
        return {"is_active": active_state}

    monkeypatch.setattr(automations, "is_maintenance_mode_active", active)
    monkeypatch.setattr(automations, "set_maintenance_mode", set_mode)
    service = AutomationService()
    rule = SimpleNamespace(name="Nightly reset")
    context = AutomationContext(
        trigger_key="time.cron",
        subject="Schedule tick",
        trigger_payload={},
        scopes={"time", "event", "maintenance"},
    )
    context.variables = build_context_variables(context)

    skipped = await service._execute_action(
        SimpleNamespace(),
        {"id": "action-1", "type": "gate.open", "config": {}, "reason_template": ""},
        context,
        rule=rule,
    )
    allowed = await service._execute_action(
        SimpleNamespace(),
        {"id": "action-2", "type": "maintenance_mode.disable", "config": {}, "reason_template": ""},
        context,
        rule=rule,
    )

    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "maintenance_mode"
    assert allowed["status"] == "success"
    assert allowed["maintenance_mode"]["is_active"] is False


@pytest.mark.asyncio
async def test_integration_icloud_sync_action_routes_to_service(monkeypatch) -> None:
    calls = []

    async def inactive() -> bool:
        return False

    async def integration_enabled(_definition) -> SimpleNamespace:
        return SimpleNamespace(enabled=True, disabled_reason=None)

    class Service:
        async def sync_all(self, **kwargs):
            calls.append(kwargs)
            return {
                "account_count": 1,
                "events_scanned": 3,
                "events_matched": 1,
                "passes_created": 1,
                "passes_updated": 0,
                "passes_cancelled": 0,
                "passes_skipped": 0,
            }

    monkeypatch.setattr(automations, "is_maintenance_mode_active", inactive)
    monkeypatch.setattr(automation_integration_actions, "get_icloud_calendar_service", lambda: Service())
    monkeypatch.setattr(automation_integration_actions, "integration_action_status", integration_enabled)

    rule = SimpleNamespace(name="Calendar sync", created_by_user_id="11111111-1111-1111-1111-111111111111")
    context = AutomationContext(
        trigger_key="time.cron",
        subject="Schedule tick",
        trigger_payload={},
        scopes={"time", "event"},
    )
    context.variables = build_context_variables(context)

    result = await AutomationService()._execute_action(
        SimpleNamespace(),
        {
            "id": "action-1",
            "type": "integration.icloud_calendar.sync",
            "config": {"provider": "icloud_calendar", "action": "sync_calendars"},
            "reason_template": "",
        },
        context,
        rule=rule,
    )

    assert result["status"] == "success"
    assert result["integration_provider"] == "icloud_calendar"
    assert result["integration_action"] == "sync_calendars"
    assert result["passes_created"] == 1
    assert calls[0]["trigger_source"] == "automation"
    assert str(calls[0]["triggered_by_user_id"]) == rule.created_by_user_id
    assert calls[0]["actor"] == "Automation Engine"


@pytest.mark.asyncio
async def test_disabled_integration_action_skips_without_crashing(monkeypatch) -> None:
    async def inactive() -> bool:
        return False

    async def integration_disabled(_definition) -> SimpleNamespace:
        return SimpleNamespace(enabled=False, disabled_reason="No active iCloud Calendar session.")

    monkeypatch.setattr(automations, "is_maintenance_mode_active", inactive)
    monkeypatch.setattr(automation_integration_actions, "integration_action_status", integration_disabled)

    rule = SimpleNamespace(name="Calendar sync", created_by_user_id=None)
    context = AutomationContext(
        trigger_key="time.cron",
        subject="Schedule tick",
        trigger_payload={},
        scopes={"time", "event"},
    )
    context.variables = build_context_variables(context)

    result = await AutomationService()._execute_action(
        SimpleNamespace(),
        {
            "id": "action-1",
            "type": "integration.icloud_calendar.sync",
            "config": {"provider": "icloud_calendar", "action": "sync_calendars"},
            "reason_template": "",
        },
        context,
        rule=rule,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "integration_disabled"
    assert result["disabled_reason"] == "No active iCloud Calendar session."


@pytest.mark.asyncio
async def test_integration_catalog_marks_disabled_actions(monkeypatch) -> None:
    async def integration_disabled(_definition) -> SimpleNamespace:
        return SimpleNamespace(enabled=False, disabled_reason="No active iCloud Calendar session.")

    monkeypatch.setattr(automation_integration_actions, "integration_action_status", integration_disabled)

    catalog = await automation_integration_actions.integration_action_catalog()
    action = catalog[0]["actions"][0]

    assert action["type"] == "integration.icloud_calendar.sync"
    assert action["enabled"] is False
    assert action["disabled_reason"] == "No active iCloud Calendar session."


@pytest.mark.asyncio
async def test_dry_run_previews_integration_actions_without_executing(monkeypatch) -> None:
    calls = []

    class Service:
        async def sync_all(self, **kwargs):
            calls.append(kwargs)
            return {"account_count": 1}

    monkeypatch.setattr(automation_integration_actions, "get_icloud_calendar_service", lambda: Service())

    result = await AutomationService().dry_run_rule(
        {
            "name": "Calendar sync preview",
            "triggers": [{"type": "time.cron", "config": {"cron_expression": "0 9 * * *"}}],
            "conditions": [],
            "actions": [{"type": "integration.icloud_calendar.sync", "config": {}}],
            "is_active": True,
        }
    )

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert result["would_run"] is True
    assert "not executed" in result["message"]
    assert result["action_previews"][0]["executed"] is False
    assert result["action_previews"][0]["would_execute"] is True
    assert calls == []


def test_ai_schedule_parser_validation_handles_thursday_until_june() -> None:
    now = datetime(2026, 4, 30, 10, 0, tzinfo=ZoneInfo("Europe/London"))
    parsed = deterministic_schedule_parse(
        "Every Thursday at 9pm until 4th June",
        now=now,
        timezone_name="Europe/London",
    )
    validated = validate_schedule_parse(
        parsed,
        now=now,
        timezone_name="Europe/London",
        raw_text="",
    )

    assert validated["cron_expression"] == "0 21 * * 4"
    assert validated["end_at"] == "2026-06-04T23:59:59+01:00"
    assert validated["next_run_at"] == "2026-04-30T20:00:00+00:00"
    assert validated["requires_review"] is False


def test_scheduler_next_run_and_due_trigger_helpers() -> None:
    now = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    every_x = {"id": "trigger-1", "type": "time.every_x", "config": {"interval": 15, "unit": "minutes"}}
    weekly_start = datetime(2026, 4, 30, 21, 0, tzinfo=UTC)

    assert next_run_for_trigger(every_x, now=now) == now + timedelta(minutes=15)
    assert due_time_trigger([every_x], now=now, scheduled_for=now)["id"] == "trigger-1"
    assert cron_from_recurrence(weekly_start, "weekly") == "0 21 * * 4"


def test_malformed_every_x_interval_falls_back_safely() -> None:
    now = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)
    trigger = normalize_triggers(
        [{"type": "time.every_x", "config": {"interval": "not-a-number", "unit": "hours"}}]
    )[0]

    assert trigger["config"]["interval"] == 1
    assert next_run_for_trigger(trigger, now=now) == now + timedelta(hours=1)


def test_webhook_payload_facts_capture_sender_and_shape_message() -> None:
    facts = facts_from_payload(
        "webhook.received",
        {
            "webhook_key": "hook-key",
            "source_ip": "192.0.2.10",
            "payload": {"event": "doorbell", "nested": {"state": "pressed"}},
        },
    )

    assert facts["webhook_key"] == "hook-key"
    assert facts["webhook_sender_ip"] == "192.0.2.10"
    assert "doorbell" in facts["message"]


@pytest.mark.asyncio
async def test_alfred_automation_tools_require_confirmation() -> None:
    create_result = await ai_tools.create_automation(
        {
            "name": "Open for Steph outside schedule",
            "triggers": [{"type": "vehicle.outside_schedule", "config": {"person_id": "person-1"}}],
            "actions": [{"type": "gate.open", "config": {}}],
        }
    )
    delete_result = await ai_tools.delete_automation({"automation_name": "Open for Steph outside schedule"})
    enable_result = await ai_tools.enable_automation({"automation_name": "Open for Steph outside schedule"})

    assert create_result["requires_confirmation"] is True
    assert create_result["confirmation_field"] == "confirm"
    assert delete_result["requires_confirmation"] is True
    assert enable_result["requires_confirmation"] is True


def test_alfred_registers_automation_tool_metadata() -> None:
    tools = ai_tools.build_agent_tools()

    assert tools["query_automation_catalog"].requires_confirmation is False
    assert tools["create_automation"].requires_confirmation is True
    assert tools["create_automation"].safety_level == ai_tools.SAFETY_CONFIRMATION_REQUIRED
    assert tools["edit_automation"].requires_confirmation is True
    assert tools["delete_automation"].requires_confirmation is True
    assert "Automations" in tools["create_automation"].categories


@pytest.mark.asyncio
async def test_receive_automation_webhook_rejects_invalid_json() -> None:
    with pytest.raises(HTTPException) as exc:
        await automations_api.receive_automation_webhook(
            "hook-key",
            make_request("POST", "/api/v1/automations/webhooks/hook-key", body=b"{not-json"),
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_scheduler_claims_due_rule_before_execution(monkeypatch) -> None:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    rule = AutomationRule(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        name="Every 15 minutes",
        is_active=True,
        triggers=[{"type": "time.every_x", "config": {"interval": 15, "unit": "minutes"}}],
        trigger_keys=["time.every_x"],
        conditions=[],
        actions=[{"id": "action-1", "type": "notification.enable", "config": {}, "reason_template": ""}],
        next_run_at=now - timedelta(minutes=1),
        last_fired_at=now - timedelta(minutes=16),
        run_count=0,
    )

    class ScalarRows:
        def all(self):
            return [rule]

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def scalars(self, _statement):
            return ScalarRows()

        def add(self, row):
            self.added.append(row)

        async def flush(self):
            for row in self.added:
                if isinstance(row, AutomationRun) and row.id is None:
                    row.id = uuid.UUID("22222222-2222-2222-2222-222222222222")

        async def commit(self):
            self.committed = True

    fake_session = FakeSession()
    monkeypatch.setattr(automations, "AsyncSessionLocal", lambda: fake_session)

    claims = await AutomationService()._claim_due_rules(now)

    assert fake_session.committed is True
    assert len(claims) == 1
    assert claims[0].rule_id == str(rule.id)
    assert claims[0].run_id == "22222222-2222-2222-2222-222222222222"
    assert claims[0].trigger_key == "time.every_x"
    assert claims[0].trigger_payload["scheduled_for"] == "2026-05-03T11:59:00+00:00"
    assert rule.next_run_at == now + timedelta(minutes=15)
    assert fake_session.added[0].status == "claimed"


@pytest.mark.asyncio
async def test_process_due_rules_executes_claimed_runs(monkeypatch) -> None:
    service = AutomationService()
    claim = ScheduledAutomationClaim(
        rule_id="11111111-1111-1111-1111-111111111111",
        run_id="22222222-2222-2222-2222-222222222222",
        trigger_key="time.every_x",
        trigger_payload={"scheduled_for": "2026-05-03T12:00:00+00:00"},
    )
    calls = []

    async def fake_claim_due_rules(_now):
        return [claim]

    async def fake_execute_rule(rule_id, **kwargs):
        calls.append({"rule_id": rule_id, **kwargs})
        return {"status": "success"}

    monkeypatch.setattr(service, "_claim_due_rules", fake_claim_due_rules)
    monkeypatch.setattr(service, "execute_rule", fake_execute_rule)

    await service._process_due_rules()

    assert calls == [
        {
            "rule_id": claim.rule_id,
            "trigger_key": claim.trigger_key,
            "trigger_payload": claim.trigger_payload,
            "actor": "Automation Scheduler",
            "source": "scheduler",
            "claimed_run_id": claim.run_id,
        }
    ]
