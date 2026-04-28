import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from app.api.v1 import notifications as notification_api
from app.ai import tools as ai_tools
from app.modules.notifications.base import ComposedNotification, NotificationContext
from app.modules.notifications.base import NotificationDeliveryError
from app.services.notifications import (
    NotificationWorkflowResult,
    NotificationService,
    TRIGGER_CATALOG,
    context_variables,
    normalize_actions,
    normalize_conditions,
    presence_condition_matches,
    render_template,
)
from app.services.event_bus import event_bus
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
        "Leaderboard",
        "Maintenance Mode",
        "Vehicle Detections",
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
        "gate_malfunction_2hrs",
        "gate_malfunction_30m",
        "gate_malfunction_60m",
        "gate_malfunction_fubar",
        "gate_malfunction_initial",
        "gate_open_failed",
        "leaderboard_overtake",
        "maintenance_mode_disabled",
        "maintenance_mode_enabled",
        "outside_schedule",
        "unauthorized_plate",
    }.issubset(set(events))


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


def test_gate_malfunction_triggers_and_variables_are_available() -> None:
    events = [event for group in TRIGGER_CATALOG for event in group["events"]]
    for trigger in [
        "gate_malfunction_initial",
        "gate_malfunction_30m",
        "gate_malfunction_60m",
        "gate_malfunction_2hrs",
        "gate_malfunction_fubar",
    ]:
        assert any(event["value"] == trigger for event in events)

    variables = context_variables(
        NotificationContext(
            event_type="gate_malfunction_initial",
            subject="Gate malfunction detected",
            severity="warning",
            facts={
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
    assert variables["LastKnownVehicle"] == "Steph Smith exited in Tesla Model Y"


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

    assert len(actions) == 1
    assert actions[0]["media"]["attach_camera_snapshot"] is True
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


async def test_notification_rule_crud_endpoints_use_db_workflow_shape() -> None:
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
        _=SimpleNamespace(),
        session=session,
    )
    assert session.deleted is not None


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

    with pytest.raises(HTTPException) as exc:
        await notification_api.test_notification_rule(rule_id, _=SimpleNamespace(), session=session)

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

    monkeypatch.setattr("app.services.notifications.HomeAssistantTtsAnnouncer", FakeTtsAnnouncer)
    monkeypatch.setattr(service, "_select_voice_targets", fake_select_voice_targets)

    await service._send_voice(action, SimpleNamespace(home_assistant_default_media_player=""))

    assert calls == [
        (
            "media_player.kitchen",
            "em gee bee em double you and dee ess arrived. smug mg MGB stays unchanged.",
        )
    ]
    assert action["message"] == original_message


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
