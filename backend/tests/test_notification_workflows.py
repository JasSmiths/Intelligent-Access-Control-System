import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from app.api.v1 import notifications as notification_api
from app.ai import tools as ai_tools
from app.modules.notifications.home_assistant_mobile import (
    HomeAssistantMobileAppNotifier,
    HomeAssistantMobileAppTarget,
)
from app.modules.notifications.base import ComposedNotification, NotificationContext
from app.modules.notifications.base import NotificationDeliveryError
from app.services.notifications import (
    ACTIONABLE_NOTIFICATION_CATALOG,
    GATE_MALFUNCTION_EVENT_TYPE,
    HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID,
    INTEGRATION_DEGRADED_EVENT_TYPE,
    NotificationSnapshotAttachment,
    NotificationWorkflowResult,
    NotificationService,
    TRIGGER_CATALOG,
    VOICE_ANNOUNCEMENTS_DISABLED_MESSAGE,
    context_variables,
    gate_malfunction_action_supports_stage,
    gate_malfunction_fallback_content,
    gate_malfunction_plain_body,
    home_assistant_notification_actions,
    normalize_actions,
    normalize_conditions,
    normalize_rule_payload,
    notification_action_buttons,
    notification_text_looks_like_raw_data,
    parse_gate_malfunction_llm_content,
    postprocess_gate_malfunction_body,
    presence_condition_matches,
    render_template,
    visitor_pass_notification_contexts_from_event,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.home_assistant import HomeAssistantIntegrationService
from app.services.schedules import schedule_allows_at
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, seed_dynamic_settings_for_session
from app.services.tts_phonetics import apply_vehicle_tts_phonetics


class ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class FakeRuleSession:
    def __init__(self, rule=None) -> None:
        self.rule = rule
        self.deleted = None
        self.commits = 0

    async def scalars(self, _statement):
        return ScalarResult([self.rule] if self.rule else [])

    def add(self, rule) -> None:
        self.rule = rule

    async def get(self, _model, _rule_id):
        return self.rule

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, rule) -> None:
        if not rule.id:
            rule.id = uuid.uuid4()
        now = datetime(2026, 4, 26, 18, 42, tzinfo=UTC)
        rule.created_at = rule.created_at or now
        rule.updated_at = now

    async def delete(self, rule) -> None:
        self.deleted = rule
        self.rule = None


class FakeSettingsSession:
    def __init__(self) -> None:
        self.calls = 0
        self.committed = False
        self.record = SimpleNamespace(
            key="notification_rules",
            value={"plain": [{"id": "legacy-rule"}]},
        )
        self.deleted = []

    async def scalars(self, _statement):
        self.calls += 1
        if self.calls == 1:
            return ScalarResult(list(DEFAULT_DYNAMIC_SETTINGS.keys()))
        return ScalarResult([self.record])

    def add(self, _record) -> None:
        raise AssertionError("all default settings should already exist in this fake")

    async def delete(self, record) -> None:
        self.deleted.append(record.key)

    async def commit(self) -> None:
        self.committed = True


class FakeContextRuleSession:
    def __init__(self, rule) -> None:
        self.rule = rule
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    async def get(self, _model, _rule_id):
        return self.rule

    async def commit(self) -> None:
        self.commits += 1


def test_render_template_supports_at_mentions_and_legacy_tokens() -> None:
    rendered = render_template(
        "@FirstName arrived in [VehicleName] at @Time",
        {
            "FirstName": "Steph",
            "VehicleName": "2026 Tesla Model Y Dual Motor Long Range",
            "Time": "18:42",
        },
    )

    assert rendered == "Steph arrived in 2026 Tesla Model Y Dual Motor Long Range at 18:42"


def test_vehicle_tts_phonetics_use_strict_acronym_boundaries() -> None:
    rendered = apply_vehicle_tts_phonetics(
        "MG, VW, BMW, BYD, GMC and DS arrived. smug mg MGB VWs BMWs stay unchanged."
    )

    assert rendered == (
        "em gee, vee double you, bee em double you, bee why dee, gee em see and dee ess arrived. "
        "smug mg MGB VWs BMWs stay unchanged."
    )


def test_notification_workflow_result_status_reflects_delivery_outcome() -> None:
    notification = ComposedNotification(title="Gate", body="Malfunction")

    assert NotificationWorkflowResult(notification=notification).status == "skipped"
    assert NotificationWorkflowResult(notification=notification, failed_count=1).status == "failed"
    assert NotificationWorkflowResult(notification=notification, delivered_count=1, failed_count=1).status == "sent"


def test_notification_span_records_access_trace(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr(
        "app.services.notifications.telemetry.record_span",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    NotificationService()._record_notification_span(
        "Notification Workflow Skipped",
        NotificationContext(
            event_type="unauthorized_plate",
            subject="PE70DHX",
            severity="warning",
            facts={
                "telemetry_trace_id": "0" * 32,
                "access_event_id": "event-1",
            },
        ),
        output_payload={"event_type": "unauthorized_plate", "reason": "no_matching_workflow"},
    )

    assert captured[0][0] == ("Notification Workflow Skipped",)
    assert captured[0][1]["trace_id"] == "0" * 32
    assert captured[0][1]["output_payload"]["reason"] == "no_matching_workflow"
    assert captured[0][1]["attributes"]["access_event_id"] == "event-1"


def test_trigger_catalog_is_categorized_for_notification_builder() -> None:
    labels = [group["label"] for group in TRIGGER_CATALOG]
    assert labels == [
        "AI Agents",
        "Compliance",
        "Gate Actions",
        "Gate Malfunctions",
        "Integrations",
        "Leaderboard",
        "Maintenance Mode",
        "Vehicle Detections",
        "Visitor Pass",
    ]

    for group in TRIGGER_CATALOG:
        event_labels = [event["label"] for event in group["events"]]
        assert event_labels == sorted(event_labels)

    events = [event["value"] for group in TRIGGER_CATALOG for event in group["events"]]
    assert "integration_test" not in events
    assert {
        "agent_anomaly_alert",
        "authorized_entry",
        "duplicate_entry",
        "duplicate_exit",
        "expired_mot_detected",
        "expired_tax_detected",
        "garage_door_open_failed",
        "gate_malfunction",
        "gate_open_failed",
        "integration_degraded",
        "leaderboard_overtake",
        "maintenance_mode_disabled",
        "maintenance_mode_enabled",
        "outside_schedule",
        "unauthorized_plate",
        "visitor_pass_arranged",
        "visitor_pass_cancelled",
        "visitor_pass_created",
        "visitor_pass_expired",
        "visitor_pass_timeframe_change_requested",
        "visitor_pass_used",
        "visitor_pass_vehicle_arrived",
        "visitor_pass_vehicle_exited",
    }.issubset(set(events))
    assert not {
        "gate_malfunction_2hrs",
        "gate_malfunction_30m",
        "gate_malfunction_60m",
        "gate_malfunction_fubar",
        "gate_malfunction_initial",
    }.intersection(events)


def test_context_variables_include_vehicle_aliases_and_time() -> None:
    variables = context_variables(
        NotificationContext(
            event_type="authorized_entry",
            subject="Steph arrived",
            severity="info",
            facts={
                "first_name": "Steph",
                "vehicle_display_name": "Tesla Model Y",
                "registration_number": "STEPH26",
                "occurred_at": "2026-04-26T18:42:00+01:00",
            },
        )
    )

    assert variables["VehicleName"] == "Tesla Model Y"
    assert variables["Registration"] == "STEPH26"
    assert variables["Time"] == "18:42"


def test_context_variables_include_integration_degraded_aliases() -> None:
    variables = context_variables(
        NotificationContext(
            event_type=INTEGRATION_DEGRADED_EVENT_TYPE,
            subject="Home Assistant degraded",
            severity="warning",
            facts={
                "integration_name": "Home Assistant",
                "integration_status": "Degraded",
                "integration_reason": "Unable to reach Home Assistant.",
                "integration_last_connected_at": "2026-05-10T18:42:00+00:00",
                "integration_last_failure_at": "2026-05-10T18:55:35+00:00",
                "message": "Home Assistant is degraded: Unable to reach Home Assistant.",
            },
        )
    )

    assert variables["IntegrationName"] == "Home Assistant"
    assert variables["IntegrationStatus"] == "Degraded"
    assert variables["IntegrationReason"] == "Unable to reach Home Assistant."
    assert variables["IntegrationLastConnectedAt"] == "2026-05-10T18:42:00+00:00"
    assert variables["IntegrationLastFailureAt"] == "2026-05-10T18:55:35+00:00"
    assert render_template("@IntegrationName: @IntegrationReason", variables) == "Home Assistant: Unable to reach Home Assistant."


def test_context_variables_include_person_pronoun_aliases() -> None:
    variables = context_variables(
        NotificationContext(
            event_type="authorized_entry",
            subject="Steph arrived",
            severity="info",
            facts={
                "object_pronoun": "her",
                "possessive_determiner": "her",
            },
        )
    )

    assert variables["ObjectPronoun"] == "her"
    assert variables["PossessiveDeterminer"] == "her"
    assert render_template("I've let @ObjectPronoun in.", variables) == "I've let her in."


def test_context_variables_include_visitor_pass_timeframe_request_aliases() -> None:
    variables = context_variables(
        NotificationContext(
            event_type="visitor_pass_timeframe_change_requested",
            subject="Visitor Pass timeframe change requested for Vicky Thompson",
            severity="warning",
            facts={
                "visitor_name": "Vicky Thompson",
                "visitor_pass_original_time": "01 May 2026, 10:00 to 01 May 2026, 18:00",
                "visitor_pass_requested_time": "01 May 2026, 10:00 to 01 May 2026, 20:00",
            },
        )
    )

    assert variables["VisitorName"] == "Vicky Thompson"
    assert variables["VisitorPassName"] == "Vicky Thompson"
    assert variables["VisitorPassOriginalTime"] == "01 May 2026, 10:00 to 01 May 2026, 18:00"
    assert variables["VisitorPassRequestedTime"] == "01 May 2026, 10:00 to 01 May 2026, 20:00"


def test_leaderboard_overtake_trigger_and_variables_are_available() -> None:
    events = [event for group in TRIGGER_CATALOG for event in group["events"]]
    assert any(event["value"] == "leaderboard_overtake" for event in events)

    variables = context_variables(
        NotificationContext(
            event_type="leaderboard_overtake",
            subject="Steph took the lead",
            severity="info",
            facts={
                "new_winner_name": "Steph Smith",
                "overtaken_name": "Jason Smith",
                "read_count": "42",
                "vehicle_name": "Silver Ford Transit",
            },
        )
    )

    assert variables["NewWinnerName"] == "Steph Smith"
    assert variables["OvertakenName"] == "Jason Smith"
    assert variables["ReadCount"] == "42"
    assert variables["VehicleName"] == "Silver Ford Transit"


def test_dvla_compliance_triggers_and_variables_are_available() -> None:
    events = [event for group in TRIGGER_CATALOG for event in group["events"]]
    assert any(event["value"] == "expired_mot_detected" for event in events)
    assert any(event["value"] == "expired_tax_detected" for event in events)

    variables = context_variables(
        NotificationContext(
            event_type="expired_mot_detected",
            subject="Expired MOT detected",
            severity="warning",
            facts={
                "registration_number": "PE70DHX",
                "vehicle_make": "Peugeot",
                "vehicle_colour": "Silver",
                "mot_status": "Expired",
                "mot_expiry": "2026-10-14",
                "tax_status": "Taxed",
                "tax_expiry": "2027-01-01",
            },
        )
    )

    assert variables["VehicleMake"] == "Peugeot"
    assert variables["VehicleColor"] == "Silver"
    assert variables["VehicleColour"] == "Silver"
    assert variables["MotStatus"] == "Expired"
    assert variables["MotExpiry"] == "2026-10-14"
    assert variables["TaxStatus"] == "Taxed"
    assert variables["TaxExpiry"] == "2027-01-01"


def test_unknown_vehicle_variables_prefer_detected_visual_colour_and_type() -> None:
    variables = context_variables(
        NotificationContext(
            event_type="unauthorized_plate",
            subject="AB12CDE",
            severity="warning",
            facts={
                "registration_number": "AB12CDE",
                "vehicle_make": "Tesla",
                "vehicle_colour": "White",
                "detected_vehicle_colour": "Grey",
                "detected_vehicle_type": "Car",
            },
        )
    )

    assert variables["VehicleRegistrationNumber"] == "AB12CDE"
    assert variables["VehicleMake"] == "Tesla"
    assert variables["VehicleColour"] == "Grey"
    assert variables["VehicleColor"] == "Grey"
    assert variables["VehicleType"] == "Car"
    assert (
        render_template(
            "An unknown @VehicleColour @VehicleMake @VehicleType with registration @Registration has been detected at the gate.",
            variables,
        )
        == "An unknown Grey Tesla Car with registration AB12CDE has been detected at the gate."
    )


def test_visitor_pass_triggers_and_variables_are_available() -> None:
    events = [event for group in TRIGGER_CATALOG for event in group["events"]]
    for trigger in [
        "visitor_pass_created",
        "visitor_pass_cancelled",
        "visitor_pass_arranged",
        "visitor_pass_used",
        "visitor_pass_expired",
        "visitor_pass_timeframe_change_requested",
        "visitor_pass_vehicle_arrived",
        "visitor_pass_vehicle_exited",
    ]:
        assert any(event["value"] == trigger for event in events)

    variables = context_variables(
        NotificationContext(
            event_type="visitor_pass_vehicle_arrived",
            subject="Sarah arrived",
            severity="info",
            facts={
                "visitor_pass_vehicle_registration": "PE70DHX",
                "visitor_pass_time_window": "01 May 2026, 10:00 to 01 May 2026, 18:00",
                "registration_number": "WRONG",
                "visitor_pass_vehicle_make": "Peugeot",
                "vehicle_make": "Tesla",
                "visitor_pass_vehicle_colour": "Silver",
                "vehicle_colour": "White",
                "visitor_pass_duration_on_site": "1h 25m",
                "visitor_pass_current_window": "01 May 2026, 10:00 to 01 May 2026, 18:00",
                "visitor_pass_requested_window": "01 May 2026, 10:00 to 01 May 2026, 20:00",
                "visitor_pass_visitor_message": "Can I stay longer?",
            },
        )
    )

    assert variables["VisitorPassVehicleRegistration"] == "PE70DHX"
    assert variables["VisitorPassRegistration"] == "PE70DHX"
    assert variables["VisitorPassTimeWindow"] == "01 May 2026, 10:00 to 01 May 2026, 18:00"
    assert variables["VisitorPassVehicleMake"] == "Peugeot"
    assert variables["VisitorPassVehicleColour"] == "Silver"
    assert variables["VisitorPassDurationOnSite"] == "1h 25m"
    assert variables["VisitorPassCurrentWindow"] == "01 May 2026, 10:00 to 01 May 2026, 18:00"
    assert variables["VisitorPassRequestedWindow"] == "01 May 2026, 10:00 to 01 May 2026, 20:00"
    assert variables["VisitorPassVisitorMessage"] == "Can I stay longer?"
    assert variables["Registration"] == "PE70DHX"
    assert variables["VehicleMake"] == "Peugeot"
    assert variables["VehicleColour"] == "Silver"
    assert (
        render_template(
            "@VisitorPassVehicleColour @VisitorPassVehicleMake @VisitorPassVehicleRegistration stayed for @VisitorPassDurationOnSite.",
            variables,
        )
        == "Silver Peugeot PE70DHX stayed for 1h 25m."
    )


def test_visitor_pass_timeframe_notification_actions_are_available() -> None:
    actions = notification_action_buttons(
        NotificationContext(
            event_type="visitor_pass_timeframe_change_requested",
            subject="Visitor requested a timeframe change",
            severity="warning",
            facts={
                "visitor_pass_id": "pass-1",
                "visitor_pass_timeframe_request_id": "request-1",
            },
        )
    )

    assert actions == [
        {
            "id": "allow",
            "label": "Allow",
            "method": "POST",
            "path": "/api/v1/visitor-passes/pass-1/timeframe-requests/request-1/allow",
        },
        {
            "id": "deny",
            "label": "Deny",
            "method": "POST",
            "path": "/api/v1/visitor-passes/pass-1/timeframe-requests/request-1/deny",
        },
    ]


def test_visitor_pass_timeframe_home_assistant_actions_are_available() -> None:
    actions = home_assistant_notification_actions(
        NotificationContext(
            event_type="visitor_pass_timeframe_change_requested",
            subject="Visitor requested a timeframe change",
            severity="warning",
            facts={
                "visitor_pass_id": "pass-1",
                "visitor_pass_timeframe_request_id": "request-1",
            },
        )
    )

    assert actions == [
        {"action": "iacs:vp_time:allow:pass-1:request-1", "title": "Allow"},
        {"action": "iacs:vp_time:deny:pass-1:request-1", "title": "Deny", "destructive": True},
    ]


def test_unknown_vehicle_open_gate_actionable_catalog_is_available() -> None:
    assert ACTIONABLE_NOTIFICATION_CATALOG == [
        {
            "trigger_event": "unauthorized_plate",
            "actions": [
                {
                    "value": "gate.open",
                    "label": "Open Gate",
                    "description": "Let the selected Home Assistant mobile recipient open the gate for this unknown plate.",
                }
            ],
        }
    ]


def test_visitor_pass_realtime_events_map_to_notification_contexts() -> None:
    payload = {
        "id": "pass-1",
        "visitor_name": "Sarah",
        "status": "used",
        "creation_source": "ui",
        "number_plate": "PE70DHX",
        "vehicle_make": "Peugeot",
        "vehicle_colour": "Silver",
        "valid_from": "2026-04-29T14:00:00+01:00",
        "valid_until": "2026-04-29T18:00:00+01:00",
        "window_start": "2026-04-29T14:00:00+01:00",
        "window_end": "2026-04-29T18:00:00+01:00",
        "arrival_time": "2026-04-29T15:03:00+01:00",
        "departure_time": "2026-04-29T16:28:00+01:00",
        "duration_on_site_seconds": 5100,
        "arrival_event_id": "arrival-event",
        "departure_event_id": "departure-event",
        "telemetry_trace_id": "1" * 32,
        "created_at": "2026-04-29T14:00:00+01:00",
        "updated_at": "2026-04-29T16:28:00+01:00",
    }

    arranged_contexts = visitor_pass_notification_contexts_from_event(
        RealtimeEvent(
            type="visitor_pass.arranged",
            payload={"visitor_pass": payload, "source": "whatsapp_visitor"},
            created_at="2026-04-29T14:05:01+01:00",
        )
    )
    assert [context.event_type for context in arranged_contexts] == ["visitor_pass_arranged"]
    assert arranged_contexts[0].facts["visitor_pass_registration"] == "PE70DHX"
    assert arranged_contexts[0].facts["visitor_pass_time_window"] == "29 Apr 2026, 14:00 to 29 Apr 2026, 18:00"
    arranged_variables = context_variables(arranged_contexts[0])
    assert arranged_variables["VisitorPassName"] == "Sarah"
    assert arranged_variables["VisitorPassRegistration"] == "PE70DHX"
    assert arranged_variables["VisitorPassTimeWindow"] == "29 Apr 2026, 14:00 to 29 Apr 2026, 18:00"

    used_contexts = visitor_pass_notification_contexts_from_event(
        RealtimeEvent(
            type="visitor_pass.used",
            payload={"visitor_pass": payload, "source": "alfred"},
            created_at="2026-04-29T15:03:01+01:00",
        )
    )
    assert [context.event_type for context in used_contexts] == [
        "visitor_pass_used",
        "visitor_pass_vehicle_arrived",
    ]
    assert used_contexts[1].facts["visitor_pass_vehicle_registration"] == "PE70DHX"
    assert used_contexts[1].facts["visitor_pass_vehicle_make"] == "Peugeot"
    assert used_contexts[1].facts["visitor_pass_vehicle_colour"] == "Silver"
    assert used_contexts[1].facts["occurred_at"] == "2026-04-29T15:03:00+01:00"
    assert used_contexts[1].facts["access_event_id"] == "arrival-event"
    assert used_contexts[1].facts["telemetry_trace_id"] == "1" * 32

    departed_contexts = visitor_pass_notification_contexts_from_event(
        RealtimeEvent(
            type="visitor_pass.departure_recorded",
            payload={"visitor_pass": {**payload, "duration_human": None}},
            created_at="2026-04-29T16:28:01+01:00",
        )
    )
    assert [context.event_type for context in departed_contexts] == ["visitor_pass_vehicle_exited"]
    departed_variables = context_variables(departed_contexts[0])
    assert departed_contexts[0].facts["occurred_at"] == "2026-04-29T16:28:00+01:00"
    assert departed_contexts[0].facts["access_event_id"] == "departure-event"
    assert departed_variables["VisitorPassDurationOnSite"] == "1h 25m"


def test_visitor_pass_status_changed_only_notifies_expired() -> None:
    base_payload = {
        "id": "pass-1",
        "visitor_name": "Sarah",
        "status": "active",
        "window_end": "2026-04-29T15:30:00+01:00",
    }

    active_contexts = visitor_pass_notification_contexts_from_event(
        RealtimeEvent(
            type="visitor_pass.status_changed",
            payload={"visitor_pass": base_payload},
            created_at="2026-04-29T15:00:00+01:00",
        )
    )
    expired_contexts = visitor_pass_notification_contexts_from_event(
        RealtimeEvent(
            type="visitor_pass.status_changed",
            payload={"visitor_pass": {**base_payload, "status": "expired"}},
            created_at="2026-04-29T15:30:01+01:00",
        )
    )

    assert active_contexts == []
    assert [context.event_type for context in expired_contexts] == ["visitor_pass_expired"]
    assert expired_contexts[0].facts["occurred_at"] == "2026-04-29T15:30:00+01:00"


def test_gate_malfunction_triggers_and_variables_are_available() -> None:
    events = [event for group in TRIGGER_CATALOG for event in group["events"]]
    assert [event["value"] for event in events if event["value"].startswith("gate_malfunction")] == [
        GATE_MALFUNCTION_EVENT_TYPE
    ]

    variables = context_variables(
        NotificationContext(
            event_type=GATE_MALFUNCTION_EVENT_TYPE,
            subject="Gate malfunction detected",
            severity="warning",
            facts={
                "malfunction_stage": "initial",
                "malfunction_duration": "5m 0s",
                "malfunction_opened_time": "2026-04-26T07:30:00+01:00",
                "malfunction_fix_attempt_time": "2026-04-26T07:35:00+01:00",
                "malfunction_fix_attempts": "1",
                "malfunction_resolution_time": "2026-04-26T07:45:00+01:00",
                "last_known_vehicle": "Steph Smith exited in Tesla Model Y",
            },
        )
    )

    assert variables["MalfunctionDuration"] == "5m 0s"
    assert variables["MalfunctionOpenedTime"] == "2026-04-26T07:30:00+01:00"
    assert variables["MalfunctionFixAttemptTime"] == "2026-04-26T07:35:00+01:00"
    assert variables["MalfunctionFixAttempts"] == "1"
    assert variables["MalfunctionResolutionTime"] == "2026-04-26T07:45:00+01:00"
    assert variables["MalfunctionStage"] == "initial"
    assert variables["LastKnownVehicle"] == "Steph Smith exited in Tesla Model Y"


def test_gate_malfunction_legacy_rules_normalize_to_stage_filter() -> None:
    normalized = normalize_rule_payload(
        {
            "name": "Thirty minute voice",
            "trigger_event": "gate_malfunction_30m",
            "actions": [
                {
                    "type": "voice",
                    "message_template": "Gate stuck.",
                }
            ],
        }
    )

    assert normalized["trigger_event"] == GATE_MALFUNCTION_EVENT_TYPE
    assert normalized["actions"][0]["gate_malfunction_stages"] == ["30m"]
    assert gate_malfunction_action_supports_stage(normalized["actions"][0], "30m") is True
    assert gate_malfunction_action_supports_stage(normalized["actions"][0], "60m") is False


def test_gate_malfunction_text_post_processing_strips_duplicate_prefixes() -> None:
    body = postprocess_gate_malfunction_body(
        "voice",
        "Attention. Gate Malfunction Update: Gate is still open.",
        previous_notification=True,
        fallback_body="Gate is still open.",
    )

    assert body == "Attention. Gate Malfunction Update: Gate is still open."

    fallback = gate_malfunction_fallback_content(
        "mobile",
        NotificationContext(
            event_type=GATE_MALFUNCTION_EVENT_TYPE,
            subject="Gate alert",
            severity="warning",
            facts={
                "gate_name": "Top Gate",
                "malfunction_stage": "resolved",
                "malfunction_duration": "14m 2s",
                "malfunction_has_previous_notification": "true",
            },
        ),
        previous_notification=True,
    )
    assert fallback["title"] == "Gate malfunction resolved"
    assert fallback["body"].startswith("Gate Malfunction Update:")
    assert "vehicle" not in fallback["body"].lower()
    assert "recovery attempts" not in fallback["body"].lower()


def test_gate_malfunction_plain_body_is_household_friendly() -> None:
    assert gate_malfunction_plain_body("initial") == (
        "The gate has malfunctioned and is stuck open. Alfred is trying to resolve it."
    )
    assert gate_malfunction_plain_body("30m") == (
        "The gate is still stuck open. Alfred is still working on it."
    )
    assert gate_malfunction_plain_body("60m") == (
        "The gate has been stuck open for about an hour. It is not looking good, but Alfred is still on the case."
    )
    assert gate_malfunction_plain_body("resolved") == (
        "The gate malfunction has been resolved and the gate is closed again."
    )
    assert notification_text_looks_like_raw_data('{"malfunction_id": "abc"}') is True
    assert notification_text_looks_like_raw_data("The gate is still stuck open.") is False


def test_gate_malfunction_llm_content_parser_accepts_fenced_json() -> None:
    assert parse_gate_malfunction_llm_content(
        '```json\n{"title": "Gate alert", "body": "Top Gate is still open."}\n```'
    ) == {
        "title": "Gate alert",
        "body": "Top Gate is still open.",
    }
    assert parse_gate_malfunction_llm_content("Gate alert: still open") is None


async def test_gate_malfunction_actions_filter_each_channel_by_stage(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(llm_provider="local")

    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(NotificationService, "_record_notification_span", lambda *_args, **_kwargs: None)

    actions = await NotificationService()._gate_malfunction_actions_for_delivery(
        [
            {"type": "mobile", "gate_malfunction_stages": ["30m"]},
            {"type": "in_app", "gate_malfunction_stages": []},
            {"type": "voice", "gate_malfunction_stages": ["resolved"]},
            {"type": "discord", "gate_malfunction_stages": ["30m", "resolved"]},
            {"type": "whatsapp", "gate_malfunction_stages": ["initial"]},
        ],
        NotificationContext(
            event_type=GATE_MALFUNCTION_EVENT_TYPE,
            subject="Gate malfunction open for 30 minutes",
            severity="warning",
            facts={
                "gate_name": "Top Gate",
                "malfunction_stage": "30m",
                "malfunction_duration": "30m 0s",
            },
        ),
    )

    assert [action["type"] for action in actions] == ["mobile", "in_app", "discord"]
    assert all(action["message"] for action in actions)


async def test_gate_malfunction_llm_composer_generates_voice_update(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(llm_provider="openai")

    class FakeProvider:
        async def complete(self, messages):
            assert messages[-1].content
            return SimpleNamespace(
                text='{"title": " Gate check ", "body": "Attention. Gate Malfunction Update: Top Gate is still open."}'
            )

    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr("app.services.notifications.get_llm_provider", lambda _provider: FakeProvider())

    content = await NotificationService()._compose_gate_malfunction_content(
        "voice",
        NotificationContext(
            event_type=GATE_MALFUNCTION_EVENT_TYPE,
            subject="Gate malfunction open for 30 minutes",
            severity="warning",
            facts={
                "gate_name": "Top Gate",
                "malfunction_stage": "30m",
                "malfunction_has_previous_notification": "true",
            },
        ),
    )

    assert content["title"] == "Gate check"
    assert content["body"] == "Attention. Gate Malfunction Update: Top Gate is still open."


async def test_gate_malfunction_llm_fallback_records_telemetry_for_invalid_output(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(llm_provider="openai")

    class FakeProvider:
        async def complete(self, _messages):
            return SimpleNamespace(text="Top Gate is still open.")

    spans = []
    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr("app.services.notifications.get_llm_provider", lambda _provider: FakeProvider())
    monkeypatch.setattr(
        NotificationService,
        "_record_notification_span",
        lambda _self, *args, **kwargs: spans.append((args, kwargs)),
    )

    content = await NotificationService()._compose_gate_malfunction_content(
        "mobile",
        NotificationContext(
            event_type=GATE_MALFUNCTION_EVENT_TYPE,
            subject="Gate malfunction detected",
            severity="warning",
            facts={
                "gate_name": "Top Gate",
                "malfunction_stage": "initial",
            },
        ),
    )

    assert content["title"] == "Gate malfunction detected"
    assert "Alfred" in content["body"]
    assert spans[0][1]["output_payload"]["reason"] == "invalid_llm_content"


async def test_notification_catalog_exposes_single_gate_malfunction_trigger_and_stages(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(apprise_urls="")

    async def fake_integrations(_self, _config):
        return []

    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(NotificationService, "available_integrations", fake_integrations)

    catalog = await NotificationService().catalog()
    events = [event["value"] for group in catalog["triggers"] for event in group["events"]]

    assert events.count(GATE_MALFUNCTION_EVENT_TYPE) == 1
    assert [stage["value"] for stage in catalog["gate_malfunction_stages"]] == [
        "initial",
        "30m",
        "60m",
        "2hrs",
        "fubar",
        "resolved",
    ]


def test_normalizers_keep_workflow_shape_strict() -> None:
    actions = normalize_actions(
        [
            {
                "type": "mobile",
                "target_mode": "selected",
                "target_ids": ["apprise:0"],
                "title_template": "@Subject",
                "message_template": "@Message",
                "media": {"attach_camera_snapshot": True, "camera_id": "camera-1"},
                "actionable": {"enabled": True, "action": "gate.open"},
            },
            {
                "type": "discord",
                "target_mode": "selected",
                "target_ids": ["discord:123"],
                "title_template": "@Subject",
                "message_template": "@Message",
                "media": {"attach_camera_snapshot": True, "camera_id": "camera-2"},
            },
            {
                "type": "whatsapp",
                "target_mode": "selected",
                "target_ids": ["whatsapp:admin:user-1", "whatsapp:number:@AdminPhone"],
                "title_template": "@Subject",
                "message_template": "@Message",
            },
            {"type": "unsupported"},
        ]
    )
    conditions = normalize_conditions(
        [
            {"type": "presence", "mode": "person_home", "person_id": "person-1"},
            {"type": "nonsense"},
        ]
    )

    assert len(actions) == 3
    assert actions[0]["media"]["attach_camera_snapshot"] is True
    assert actions[0]["actionable"] == {"enabled": True, "action": "gate.open"}
    assert actions[1]["type"] == "discord"
    assert actions[1]["target_ids"] == ["discord:123"]
    assert actions[1]["media"]["camera_id"] == "camera-2"
    assert actions[2]["type"] == "whatsapp"
    assert actions[2]["target_ids"] == ["whatsapp:admin:user-1", "whatsapp:number:@AdminPhone"]
    assert len(conditions) == 1
    assert conditions[0]["mode"] == "person_home"


async def test_home_assistant_mobile_targets_accept_specific_notify_services() -> None:
    service = NotificationService()

    targets = await service._select_home_assistant_mobile_targets(
        SimpleNamespace(),
        {
            "target_mode": "selected",
            "target_ids": ["home_assistant_mobile:notify.mobile_app_jason"],
        },
    )

    assert targets == ["notify.mobile_app_jason"]

    with pytest.raises(NotificationDeliveryError):
        await service._select_home_assistant_mobile_targets(
            SimpleNamespace(),
            {
                "target_mode": "selected",
                "target_ids": ["home_assistant_mobile:notify.family_group"],
            },
        )


async def test_home_assistant_mobile_notifier_includes_image_attachment_payload() -> None:
    calls = []

    class FakeHomeAssistantClient:
        async def call_service(self, service_name, payload):
            calls.append((service_name, payload))
            return {}

    await HomeAssistantMobileAppNotifier(FakeHomeAssistantClient()).send(
        HomeAssistantMobileAppTarget("notify.mobile_app_jason"),
        "Gate",
        "Snapshot attached",
        NotificationContext(event_type="authorized_entry", subject="Gate", severity="info", facts={}),
        image_url="https://access.example.test/api/v1/notification-snapshots/snapshot.jpg",
        image_content_type="image/jpeg",
        actions=[{"action": "ack", "title": "Acknowledge"}],
    )

    assert calls[0][0] == "notify.mobile_app_jason"
    data = calls[0][1]["data"]
    assert data["image"] == "https://access.example.test/api/v1/notification-snapshots/snapshot.jpg"
    assert data["attachment"] == {
        "url": "https://access.example.test/api/v1/notification-snapshots/snapshot.jpg",
        "content-type": "jpeg",
    }
    assert data["actions"] == [{"action": "ack", "title": "Acknowledge"}]


async def test_mobile_workflow_passes_snapshot_url_to_home_assistant(monkeypatch) -> None:
    service = NotificationService()
    calls = []

    async def fake_snapshot_attachment(_media):
        return NotificationSnapshotAttachment(
            path="/tmp/iacs-test-snapshot.jpg",
            content_type="image/jpeg",
            public_url="https://access.example.test/api/v1/notification-snapshots/snapshot.jpg",
        )

    class FakeHomeAssistantNotifier:
        async def send(self, target, title, body, context, *, image_url=None, image_content_type=None, actions=None):
            calls.append((target.service_name, title, body, image_url, image_content_type, actions))

    monkeypatch.setattr(service, "_snapshot_attachment", fake_snapshot_attachment)
    monkeypatch.setattr("app.services.notifications.HomeAssistantMobileAppNotifier", FakeHomeAssistantNotifier)

    await service._send_mobile(
        {
            "type": "mobile",
            "target_mode": "selected",
            "target_ids": ["home_assistant_mobile:notify.mobile_app_jason"],
            "title": "Gate",
            "message": "Snapshot attached",
            "media": {"attach_camera_snapshot": True, "camera_id": "camera-1"},
        },
        NotificationContext(event_type="authorized_entry", subject="Gate", severity="info", facts={}),
        SimpleNamespace(apprise_urls=""),
    )

    assert calls == [
        (
            "notify.mobile_app_jason",
            "Gate",
            "Snapshot attached",
            "https://access.example.test/api/v1/notification-snapshots/snapshot.jpg",
            "image/jpeg",
            [],
        )
    ]


async def test_mobile_workflow_reports_partial_success_when_fallback_delivers(monkeypatch) -> None:
    service = NotificationService()
    context = NotificationContext(
        event_type="authorized_entry",
        subject="Steph arrived",
        severity="info",
        facts={"message": "Steph arrived."},
    )

    async def fake_apprise(_action, _context, _urls, _attachments, failures):
        failures.append("Apprise: temporary outage")
        return False

    async def fake_home_assistant(_action, _context, _targets, _snapshot, _failures):
        return True

    monkeypatch.setattr(service, "_send_mobile_apprise", fake_apprise)
    monkeypatch.setattr(service, "_send_mobile_home_assistant", fake_home_assistant)

    outcome = await service._send_mobile(
        {
            "type": "mobile",
            "title": "Steph arrived",
            "message": "Steph arrived.",
            "target_mode": "selected",
            "target_ids": ["apprise:0", "home_assistant_mobile:notify.mobile_app_jason"],
        },
        context,
        SimpleNamespace(apprise_urls="pover://user-token@app-token"),
    )

    assert outcome.delivered is True
    assert outcome.reason == "delivered_with_failures"
    assert outcome.metadata["partial_failure"] is True
    assert outcome.metadata["failures"] == ["Apprise: temporary outage"]


async def test_mobile_workflow_adds_configured_home_assistant_gate_action(monkeypatch) -> None:
    service = NotificationService()
    calls = []

    class FakeActionableService:
        async def create_gate_open_action(self, *, context, notify_service):
            calls.append((context.event_type, notify_service, context.facts["registration_number"]))
            return {"action": "iacs:gate_open:token", "title": "Open Gate"}

    monkeypatch.setattr(
        "app.services.notifications.get_actionable_notification_service",
        lambda: FakeActionableService(),
    )

    actions = await service._home_assistant_mobile_actions_for_target(
        {"actionable": {"enabled": True, "action": "gate.open"}},
        NotificationContext(
            event_type="unauthorized_plate",
            subject="AB12CDE",
            severity="warning",
            facts={"registration_number": "AB12CDE"},
        ),
        "notify.mobile_app_jason",
    )

    assert calls == [("unauthorized_plate", "notify.mobile_app_jason", "AB12CDE")]
    assert actions == [{"action": "iacs:gate_open:token", "title": "Open Gate"}]


async def test_mobile_workflow_omits_home_assistant_snapshot_without_public_base_url(monkeypatch) -> None:
    service = NotificationService()
    calls = []

    async def fake_snapshot_attachment(_media):
        return NotificationSnapshotAttachment(
            path="/tmp/iacs-test-snapshot.jpg",
            content_type="image/jpeg",
            public_url=None,
        )

    class FakeHomeAssistantNotifier:
        async def send(self, target, title, body, context, *, image_url=None, image_content_type=None, actions=None):
            calls.append((target.service_name, title, body, image_url, image_content_type, actions))

    monkeypatch.setattr(service, "_snapshot_attachment", fake_snapshot_attachment)
    monkeypatch.setattr("app.services.notifications.HomeAssistantMobileAppNotifier", FakeHomeAssistantNotifier)

    await service._send_mobile(
        {
            "type": "mobile",
            "target_mode": "selected",
            "target_ids": ["home_assistant_mobile:notify.mobile_app_jason"],
            "title": "Gate",
            "message": "Snapshot attached",
            "media": {"attach_camera_snapshot": True, "camera_id": "camera-1"},
        },
        NotificationContext(event_type="authorized_entry", subject="Gate", severity="info", facts={}),
        SimpleNamespace(apprise_urls=""),
    )

    assert calls == [
        (
            "notify.mobile_app_jason",
            "Gate",
            "Snapshot attached",
            None,
            None,
            [],
        )
    ]


async def test_home_assistant_mobile_action_decides_visitor_timeframe(monkeypatch) -> None:
    calls = []

    class FakeWhatsAppService:
        async def decide_visitor_timeframe_request(self, pass_id, request_id, decision, *, actor_label=None):
            calls.append((pass_id, request_id, decision, actor_label))
            return {"admin_message": "Approved"}

    monkeypatch.setattr(
        "app.services.whatsapp_messaging.get_whatsapp_messaging_service",
        lambda: FakeWhatsAppService(),
    )

    await HomeAssistantIntegrationService()._handle_mobile_notification_action(
        {"data": {"action": "iacs:vp_time:allow:pass-1:request-1"}}
    )

    assert calls == [("pass-1", "request-1", "allow", "Home Assistant Notification")]


async def test_home_assistant_mobile_action_routes_actionable_gate_event(monkeypatch) -> None:
    calls = []

    class FakeActionableService:
        async def handle_home_assistant_action(self, action_id, event_data):
            calls.append((action_id, event_data))
            return True

    monkeypatch.setattr(
        "app.services.actionable_notifications.get_actionable_notification_service",
        lambda: FakeActionableService(),
    )

    await HomeAssistantIntegrationService()._handle_mobile_notification_action(
        {"data": {"action": "iacs:gate_open:token", "device_id": "device-1"}}
    )

    assert calls == [("iacs:gate_open:token", {"action": "iacs:gate_open:token", "device_id": "device-1"})]


def test_presence_condition_modes() -> None:
    assert presence_condition_matches({"mode": "no_one_home"}, set())
    assert not presence_condition_matches({"mode": "no_one_home"}, {"person-1"})
    assert presence_condition_matches({"mode": "someone_home"}, {"person-1"})
    assert presence_condition_matches({"mode": "person_home", "person_id": "person-1"}, {"person-1"})
    assert not presence_condition_matches({"mode": "person_home", "person_id": "person-2"}, {"person-1"})


def test_schedule_condition_time_window_uses_existing_scheduler() -> None:
    schedule = SimpleNamespace(
        time_blocks={
            "0": [{"start": "08:00", "end": "12:00"}],
            "1": [],
            "2": [],
            "3": [],
            "4": [],
            "5": [],
            "6": [],
        }
    )

    assert schedule_allows_at(schedule, datetime(2026, 4, 27, 9, 30, tzinfo=UTC), "UTC")
    assert not schedule_allows_at(schedule, datetime(2026, 4, 27, 13, 0, tzinfo=UTC), "UTC")


def test_legacy_notification_setting_is_not_seeded() -> None:
    assert "notification_rules" not in DEFAULT_DYNAMIC_SETTINGS


async def test_notification_rule_crud_endpoints_use_db_workflow_shape(monkeypatch) -> None:
    async def confirmed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(notification_api, "require_confirmation", confirmed)
    session = FakeRuleSession()
    created = await notification_api.create_notification_rule(
        notification_api.NotificationRuleRequest(
            name=" Gate arrivals ",
            trigger_event="authorized_entry",
            conditions=[{"type": "presence", "mode": "someone_home"}],
            actions=[
                {
                    "type": "in_app",
                    "title_template": "@FirstName arrived",
                    "message_template": "@VehicleName",
                }
            ],
        ),
        _=SimpleNamespace(),
        session=session,
    )

    assert created["name"] == "Gate arrivals"
    assert created["trigger_event"] == "authorized_entry"
    assert created["conditions"][0]["type"] == "presence"
    assert created["actions"][0]["type"] == "in_app"

    listed = await notification_api.list_notification_rules(_=SimpleNamespace(), session=session)
    assert [rule["id"] for rule in listed] == [created["id"]]

    updated = await notification_api.update_notification_rule(
        uuid.UUID(created["id"]),
        notification_api.NotificationRuleUpdateRequest(
            name="Gate arrivals updated",
            actions=[
                {
                    "type": "mobile",
                    "target_mode": "selected",
                    "target_ids": ["apprise:0"],
                    "title_template": "@Subject",
                    "message_template": "@Message",
                }
            ],
        ),
        _=SimpleNamespace(),
        session=session,
    )
    assert updated["name"] == "Gate arrivals updated"
    assert updated["actions"][0]["target_ids"] == ["apprise:0"]

    fetched = await notification_api.get_notification_rule(
        uuid.UUID(created["id"]),
        _=SimpleNamespace(),
        session=session,
    )
    assert fetched["id"] == created["id"]

    await notification_api.delete_notification_rule(
        uuid.UUID(created["id"]),
        request=notification_api.NotificationRuleDeleteRequest(confirmation_token="confirmed"),
        _=SimpleNamespace(),
        session=session,
    )
    assert session.deleted is not None


async def test_notification_rule_create_requires_confirmation_before_write() -> None:
    session = FakeRuleSession()

    with pytest.raises(HTTPException) as exc:
        await notification_api.create_notification_rule(
            notification_api.NotificationRuleRequest(
                name="Gate arrivals",
                trigger_event="authorized_entry",
                actions=[{"type": "in_app", "title_template": "@Subject", "message_template": "@Message"}],
            ),
            _=SimpleNamespace(),
            session=session,
        )

    assert exc.value.status_code == 428
    assert session.rule is None


def test_notification_rule_serialization_normalizes_legacy_gate_malfunction_trigger() -> None:
    now = datetime(2026, 4, 26, 18, 42, tzinfo=UTC)
    serialized = notification_api.serialize_rule(
        SimpleNamespace(
            id=uuid.uuid4(),
            name="Old 60 minute alert",
            trigger_event="gate_malfunction_60m",
            conditions=[],
            actions=[{"type": "voice", "message_template": "Gate stuck."}],
            is_active=True,
            last_fired_at=None,
            created_at=now,
            updated_at=now,
        )
    )

    assert serialized["trigger_event"] == GATE_MALFUNCTION_EVENT_TYPE
    assert serialized["actions"][0]["gate_malfunction_stages"] == ["60m"]


async def test_preview_endpoint_resolves_mock_variables() -> None:
    preview = await notification_api.preview_notification_rule(
        notification_api.NotificationPreviewRequest(
            rule={
                "name": "Preview",
                "trigger_event": "authorized_entry",
                "actions": [
                    {
                        "type": "in_app",
                        "title_template": "@FirstName arrived",
                        "message_template": "@FirstName arrived in the @VehicleName.",
                    }
                ],
            }
        ),
        _=SimpleNamespace(),
    )

    assert preview["actions"][0]["title"] == "Steph arrived"
    assert "2026 Tesla Model Y Dual Motor Long Range" in preview["actions"][0]["message"]


async def test_rule_test_endpoint_propagates_delivery_failures(monkeypatch) -> None:
    class FailingNotificationService:
        async def process_context(self, *_args, **_kwargs):
            raise NotificationDeliveryError("No Apprise endpoints are configured or selected.")

    rule_id = uuid.uuid4()
    now = datetime(2026, 4, 26, 18, 42, tzinfo=UTC)
    session = FakeRuleSession(
        SimpleNamespace(
            id=rule_id,
            name="Mobile",
            trigger_event="authorized_entry",
            conditions=[],
            actions=[
                {
                    "type": "mobile",
                    "title_template": "@Subject",
                    "message_template": "@Message",
                }
            ],
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    monkeypatch.setattr(notification_api, "get_notification_service", lambda: FailingNotificationService())

    async def consume_confirmation(*_args, **_kwargs):
        return SimpleNamespace()

    async def write_test_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(notification_api, "consume_action_confirmation", consume_confirmation)
    monkeypatch.setattr(notification_api, "write_notification_test_audit", write_test_audit)

    with pytest.raises(HTTPException) as exc:
        await notification_api.test_notification_rule(
            rule_id,
            request=notification_api.StoredNotificationRuleTestRequest(confirmation_token="confirmed"),
            user=SimpleNamespace(id=uuid.uuid4()),
            session=session,
        )

    assert exc.value.status_code == 503
    assert "No Apprise endpoints" in str(exc.value.detail)


async def test_ai_alert_tool_does_not_report_false_success(monkeypatch) -> None:
    class FailingNotificationService:
        async def notify(self, *_args, **_kwargs):
            raise NotificationDeliveryError("No active notification workflow matched this event.")

    monkeypatch.setattr(ai_tools, "get_notification_service", lambda: FailingNotificationService())

    result = await ai_tools.trigger_anomaly_alert(
        {"subject": "Test anomaly", "severity": "critical", "message": "Something happened", "confirm": True}
    )

    assert result["sent"] is False
    assert "No active notification workflow" in result["error"]


def test_ai_notification_workflow_tools_are_registered() -> None:
    tools = ai_tools.build_agent_tools()

    expected = {
        "query_notification_catalog",
        "query_notification_workflows",
        "get_notification_workflow",
        "create_notification_workflow",
        "update_notification_workflow",
        "delete_notification_workflow",
        "preview_notification_workflow",
        "test_notification_workflow",
    }
    assert expected.issubset(tools.keys())


async def test_ai_preview_notification_workflow_resolves_variables() -> None:
    result = await ai_tools.preview_notification_workflow(
        {
            "rule": {
                "name": "Preview",
                "trigger_event": "authorized_entry",
                "actions": [
                    {
                        "type": "in_app",
                        "title_template": "@FirstName arrived",
                        "message_template": "@FirstName arrived in the @VehicleName.",
                    }
                ],
            }
        }
    )

    assert result["previewed"] is True
    assert result["preview"]["actions"][0]["title"] == "Steph arrived"


async def test_ai_notification_mutation_tools_require_confirmation() -> None:
    create_result = await ai_tools.create_notification_workflow(
        {
            "name": "Gate arrivals",
            "trigger_event": "authorized_entry",
            "actions": [{"type": "in_app"}],
        }
    )
    delete_result = await ai_tools.delete_notification_workflow({"rule_name": "Gate arrivals"})
    test_result = await ai_tools.test_notification_workflow(
        {
            "rule": {
                "name": "Preview",
                "trigger_event": "authorized_entry",
                "actions": [{"type": "in_app"}],
            }
        }
    )

    assert create_result["created"] is False
    assert create_result["requires_confirmation"] is True
    assert create_result["confirmation_field"] == "confirm"
    assert delete_result["deleted"] is False
    assert delete_result["requires_confirmation"] is True
    assert delete_result["confirmation_field"] == "confirm"
    assert test_result["sent"] is False
    assert test_result["requires_confirmation"] is True
    assert test_result["confirmation_field"] == "confirm_send"


async def test_ai_notification_test_tool_propagates_provider_failure(monkeypatch) -> None:
    class FailingWorkflowService:
        async def process_context(self, *_args, **_kwargs):
            raise NotificationDeliveryError("No Apprise endpoints are configured or selected.")

        async def preview_rule(self, rule, _context=None):
            return {"id": rule["id"], "actions": []}

    monkeypatch.setattr(ai_tools, "get_notification_service", lambda: FailingWorkflowService())

    result = await ai_tools.test_notification_workflow(
        {
            "confirm_send": True,
            "rule": {
                "name": "Mobile",
                "trigger_event": "authorized_entry",
                "actions": [{"type": "mobile", "title_template": "@Subject", "message_template": "@Message"}],
            },
        }
    )

    assert result["sent"] is False
    assert "No Apprise endpoints" in result["error"]


async def test_in_app_action_emits_realtime_notification(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace()

    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)
    captured = []

    async def capture(event):
        captured.append(event)

    event_bus.subscribe(capture)
    try:
        await NotificationService().process_context(
            NotificationContext(
                event_type="authorized_entry",
                subject="Steph arrived",
                severity="info",
                facts={"first_name": "Steph", "message": "Gate opened"},
            ),
            raise_on_failure=True,
            rules_override=[
                {
                    "id": "rule-1",
                    "name": "Dashboard alert",
                    "trigger_event": "authorized_entry",
                    "conditions": [],
                    "actions": [
                        {
                            "type": "in_app",
                            "title_template": "@FirstName arrived",
                            "message_template": "@Message",
                        }
                    ],
                    "is_active": True,
                }
            ],
        )
        await asyncio.sleep(0)
    finally:
        event_bus.unsubscribe(capture)

    in_app_events = [event for event in captured if event.type == "notification.in_app"]
    assert len(in_app_events) == 1
    assert in_app_events[0].payload["title"] == "Steph arrived"
    assert in_app_events[0].payload["body"] == "Gate opened"


async def test_discord_action_routes_through_discord_sender_and_cleans_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    snapshot_path = tmp_path / "notification-snapshots" / "snapshot.jpg"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"snapshot")
    calls = []

    class FakeDiscordService:
        async def send_notification_action(self, action, context, *, attachment_paths=None):
            calls.append((action, context, list(attachment_paths or [])))

    service = NotificationService()

    async def fake_snapshot_attachments(_media):
        return [str(snapshot_path)]

    monkeypatch.setattr(service, "_snapshot_attachments", fake_snapshot_attachments)
    monkeypatch.setattr("app.services.notifications.get_discord_messaging_service", lambda: FakeDiscordService())

    await service._send_discord(
        {
            "type": "discord",
            "target_mode": "selected",
            "target_ids": ["discord:123"],
            "title": "Gate",
            "message": "Steph arrived",
            "media": {"attach_camera_snapshot": True, "camera_id": "camera-1"},
        },
        NotificationContext(event_type="authorized_entry", subject="Gate", severity="info", facts={}),
    )

    assert calls[0][0]["target_ids"] == ["discord:123"]
    assert calls[0][2] == [str(snapshot_path)]
    assert not snapshot_path.exists()


async def test_voice_action_applies_phonetics_only_to_spoken_message(monkeypatch) -> None:
    calls = []

    class FakeTtsAnnouncer:
        async def announce(self, target, message: str) -> None:
            calls.append((target.entity_id, message))

    service = NotificationService()
    original_message = "MG BMW and DS arrived. smug mg MGB stays unchanged."
    action = {"message": original_message}

    async def fake_select_voice_targets(_config, _action):
        return ["media_player.kitchen"]

    async def fake_voice_preflight():
        return None

    monkeypatch.setattr("app.services.notifications.HomeAssistantTtsAnnouncer", FakeTtsAnnouncer)
    monkeypatch.setattr(service, "_select_voice_targets", fake_select_voice_targets)
    monkeypatch.setattr(service, "_voice_announcements_preflight", fake_voice_preflight)

    await service._send_voice(action, SimpleNamespace(home_assistant_default_media_player=""))

    assert calls == [
        (
            "media_player.kitchen",
            "em gee bee em double you and dee ess arrived. smug mg MGB stays unchanged.",
        )
    ]
    assert action["message"] == original_message


async def test_voice_action_attempts_all_targets_before_reporting_failures(monkeypatch) -> None:
    calls = []

    class FakeTtsAnnouncer:
        async def announce(self, target, message: str) -> None:
            calls.append((target.entity_id, message))
            if target.entity_id == "media_player.offline":
                raise RuntimeError("speaker offline")

    service = NotificationService()

    async def fake_select_voice_targets(_config, _action):
        return ["media_player.offline", "media_player.kitchen"]

    async def fake_voice_preflight():
        return None

    monkeypatch.setattr("app.services.notifications.HomeAssistantTtsAnnouncer", FakeTtsAnnouncer)
    monkeypatch.setattr(service, "_select_voice_targets", fake_select_voice_targets)
    monkeypatch.setattr(service, "_voice_announcements_preflight", fake_voice_preflight)

    with pytest.raises(NotificationDeliveryError) as excinfo:
        await service._send_voice({"message": "BMW arrived"}, SimpleNamespace(home_assistant_default_media_player=""))

    assert calls == [
        ("media_player.offline", "bee em double you arrived"),
        ("media_player.kitchen", "bee em double you arrived"),
    ]
    assert "media_player.offline: speaker offline" in str(excinfo.value)


async def test_voice_action_preflight_on_sends_to_targets(monkeypatch) -> None:
    calls = []
    state_checks = []

    class FakeHomeAssistantClient:
        async def get_state(self, entity_id: str):
            state_checks.append(entity_id)
            return SimpleNamespace(state="on")

    class FakeTtsAnnouncer:
        async def announce(self, target, message: str) -> None:
            calls.append((target.entity_id, message))

    service = NotificationService()

    async def fake_select_voice_targets(_config, _action):
        return ["media_player.kitchen"]

    monkeypatch.setattr("app.services.notifications.HomeAssistantClient", FakeHomeAssistantClient)
    monkeypatch.setattr("app.services.notifications.HomeAssistantTtsAnnouncer", FakeTtsAnnouncer)
    monkeypatch.setattr(service, "_select_voice_targets", fake_select_voice_targets)

    outcome = await service._send_voice({"message": "BMW arrived"}, SimpleNamespace(home_assistant_default_media_player=""))

    assert outcome.delivered is True
    assert outcome.skipped is False
    assert state_checks == [HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID]
    assert calls == [("media_player.kitchen", "bee em double you arrived")]


async def test_voice_action_off_suppresses_and_records_telemetry(monkeypatch) -> None:
    calls = []
    captured_spans = []
    captured_events = []

    class FakeHomeAssistantClient:
        async def get_state(self, entity_id: str):
            assert entity_id == HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID
            return SimpleNamespace(state="off")

    class FakeTtsAnnouncer:
        async def announce(self, target, message: str) -> None:
            calls.append((target.entity_id, message))

    async def fake_runtime_config():
        return SimpleNamespace(home_assistant_default_media_player="")

    async def capture(event):
        captured_events.append(event)

    monkeypatch.setattr("app.services.notifications.HomeAssistantClient", FakeHomeAssistantClient)
    monkeypatch.setattr("app.services.notifications.HomeAssistantTtsAnnouncer", FakeTtsAnnouncer)
    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(
        "app.services.notifications.telemetry.record_span",
        lambda *args, **kwargs: captured_spans.append((args, kwargs)),
    )

    event_bus.subscribe(capture)
    try:
        result = await NotificationService().execute_rule_with_result(
            {
                "id": "rule-voice",
                "name": "Voice alert",
                "trigger_event": "authorized_entry",
                "conditions": [],
                "actions": [
                    {
                        "type": "voice",
                        "target_mode": "selected",
                        "target_ids": ["home_assistant_tts:media_player.kitchen"],
                        "message_template": "BMW arrived",
                    }
                ],
                "is_active": True,
            },
            NotificationContext(
                event_type="authorized_entry",
                subject="Steph arrived",
                severity="info",
                facts={"telemetry_trace_id": "1" * 32},
            ),
        )
        await asyncio.sleep(0)
    finally:
        event_bus.unsubscribe(capture)

    assert calls == []
    assert result.status == "skipped"
    assert result.skipped_count == 1
    assert result.skipped_reasons == ["announcements_disabled"]
    assert captured_spans[0][0] == ("Notification Action Suppressed",)
    output_payload = captured_spans[0][1]["output_payload"]
    assert output_payload["reason"] == "announcements_disabled"
    assert output_payload["home_assistant_entity_id"] == HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID
    assert output_payload["home_assistant_state"] == "off"
    assert output_payload["message"] == VOICE_ANNOUNCEMENTS_DISABLED_MESSAGE
    skipped_events = [event for event in captured_events if event.type == "notification.skipped"]
    assert len(skipped_events) == 1
    assert skipped_events[0].payload["reason"] == "announcements_disabled"


async def test_voice_action_state_lookup_failure_suppresses_without_tts(monkeypatch) -> None:
    calls = []

    class FakeHomeAssistantClient:
        async def get_state(self, _entity_id: str):
            raise RuntimeError("home assistant offline")

    class FakeTtsAnnouncer:
        async def announce(self, target, message: str) -> None:
            calls.append((target.entity_id, message))

    service = NotificationService()

    async def fake_select_voice_targets(_config, _action):
        return ["media_player.kitchen"]

    monkeypatch.setattr("app.services.notifications.HomeAssistantClient", FakeHomeAssistantClient)
    monkeypatch.setattr("app.services.notifications.HomeAssistantTtsAnnouncer", FakeTtsAnnouncer)
    monkeypatch.setattr(service, "_select_voice_targets", fake_select_voice_targets)

    outcome = await service._send_voice({"message": "BMW arrived"}, SimpleNamespace(home_assistant_default_media_player=""))

    assert calls == []
    assert outcome.delivered is False
    assert outcome.skipped is True
    assert outcome.reason == "announcements_state_unavailable"
    assert outcome.metadata["fail_safe"] is True


async def test_suppressed_voice_action_does_not_block_other_workflow_actions(monkeypatch) -> None:
    captured_events = []

    class FakeHomeAssistantClient:
        async def get_state(self, _entity_id: str):
            return SimpleNamespace(state="off")

    class FakeTtsAnnouncer:
        async def announce(self, _target, _message: str) -> None:
            raise AssertionError("TTS dispatch should be suppressed")

    async def fake_runtime_config():
        return SimpleNamespace(home_assistant_default_media_player="")

    async def capture(event):
        captured_events.append(event)

    monkeypatch.setattr("app.services.notifications.HomeAssistantClient", FakeHomeAssistantClient)
    monkeypatch.setattr("app.services.notifications.HomeAssistantTtsAnnouncer", FakeTtsAnnouncer)
    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)

    event_bus.subscribe(capture)
    try:
        result = await NotificationService().execute_rule_with_result(
            {
                "id": "rule-mixed",
                "name": "Mixed alert",
                "trigger_event": "authorized_entry",
                "conditions": [],
                "actions": [
                    {
                        "type": "voice",
                        "target_mode": "selected",
                        "target_ids": ["home_assistant_tts:media_player.kitchen"],
                        "message_template": "Voice alert",
                    },
                    {
                        "type": "in_app",
                        "title_template": "Dashboard alert",
                        "message_template": "Still delivered",
                    },
                ],
                "is_active": True,
            },
            NotificationContext(
                event_type="authorized_entry",
                subject="Steph arrived",
                severity="info",
                facts={},
            ),
        )
        await asyncio.sleep(0)
    finally:
        event_bus.unsubscribe(capture)

    assert result.status == "sent"
    assert result.delivered_count == 1
    assert result.skipped_count == 1
    assert result.skipped_reasons == ["announcements_disabled"]
    assert [event.type for event in captured_events].count("notification.in_app") == 1
    assert [event.type for event in captured_events].count("notification.skipped") == 1


async def test_process_context_with_result_reports_delivery_status(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace()

    monkeypatch.setattr("app.services.notifications.get_runtime_config", fake_runtime_config)

    result = await NotificationService().process_context_with_result(
        NotificationContext(
            event_type="authorized_entry",
            subject="Steph arrived",
            severity="info",
            facts={"first_name": "Steph", "message": "Gate opened"},
        ),
        rules_override=[
            {
                "id": "rule-1",
                "name": "Dashboard alert",
                "trigger_event": "authorized_entry",
                "conditions": [],
                "actions": [
                    {
                        "type": "in_app",
                        "title_template": "@FirstName arrived",
                        "message_template": "@Message",
                    }
                ],
                "is_active": True,
            }
        ],
    )

    assert result.status == "sent"
    assert result.delivered_count == 1
    assert result.failed_count == 0


async def test_notification_rule_last_fired_timestamp_is_persisted(monkeypatch) -> None:
    rule = SimpleNamespace(id=uuid.uuid4(), last_fired_at=None)
    session = FakeContextRuleSession(rule)

    monkeypatch.setattr("app.services.notifications.AsyncSessionLocal", lambda: session)

    await NotificationService()._mark_rule_fired(rule)

    assert session.commits == 1
    assert rule.last_fired_at is not None
    assert rule.last_fired_at.tzinfo is not None


async def test_startup_seed_prunes_legacy_notification_rules() -> None:
    session = FakeSettingsSession()

    await seed_dynamic_settings_for_session(session)

    assert session.deleted == ["notification_rules"]
    assert session.committed is True
