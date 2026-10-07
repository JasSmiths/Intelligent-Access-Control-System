from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.notifications.base import NotificationContext
from app.services import notifications as owner
from app.services.workflows.notification_payloads import normalize_actions, notification_rule_definition_fingerprint
from app.services.workflows.template_recipients import (
    TemplateRecipientError, content_for_recipient, render_recipient_template,
)

JASON = "home_assistant_mobile:notify.mobile_app_jason"
STEPH = "home_assistant_mobile:notify.mobile_app_steph"


def rule(channel="mobile", target=JASON):
    return {"id": "scoped", "name": "Arrival", "trigger_event": "authorized_entry", "actions": [{
        "id": "arrival", "type": channel, "target_mode": "selected", "target_ids": [target],
        "title_template": "Arrival", "message_template": "@VehicleTimeAway then @VehicleTimeAway.",
        "variable_recipients": {"message_template": [{"occurrence": 1, "name": "VehicleTimeAway", "target_ids": [target]}]},
    }]}


def rendered(channel="mobile", target=JASON):
    return owner.NotificationService().render_rule(rule(channel, target), NotificationContext(
        "authorized_entry", "Arrival", "info", {"vehicle_time_away_seconds": "8400"}))["actions"][0]


def test_only_the_selected_occurrence_is_restricted():
    action = rendered()
    assert content_for_recipient(action, JASON)["message"] == "after 2hrs 20m then after 2hrs 20m."
    assert content_for_recipient(action, STEPH)["message"] == "after 2hrs 20m then."
    assert action["message"] == "after 2hrs 20m then."
    assert action["variable_recipients"] == rule()["actions"][0]["variable_recipients"]


def test_arrival_comma_spacing_for_allowed_and_omitted_recipients():
    value = rule()
    action = value["actions"][0]
    action["message_template"] = (
        "Sylvia's Mercedes-Benz Slk has been detected at the gate @VehicleTimeAway , I've let her in.")
    action["variable_recipients"]["message_template"] = [
        {"occurrence": 0, "name": "VehicleTimeAway", "target_ids": [JASON]},
    ]
    frozen = owner.NotificationService().render_rule(value, NotificationContext(
        "authorized_entry", "Arrival", "info", {"vehicle_time_away_seconds": "1560"}))["actions"][0]
    assert content_for_recipient(frozen, JASON)["message"] == (
        "Sylvia's Mercedes-Benz Slk has been detected at the gate after 26m, I've let her in.")
    assert content_for_recipient(frozen, STEPH)["message"] == (
        "Sylvia's Mercedes-Benz Slk has been detected at the gate, I've let her in.")


def test_allowed_recipient_token_spacing_preserves_literal_and_value_whitespace():
    restrictions = [{"occurrence": 0, "name": "VehicleTimeAway", "target_ids": [JASON]}]
    assert render_recipient_template(
        "Keep  literal , spacing.\n@VehicleTimeAway \t, Next  line.",
        {"VehicleTimeAway": "after 26m "}, restrictions, JASON,
    ) == (
        "Keep  literal , spacing.\nafter 26m , Next  line.")


@pytest.mark.parametrize("change", [
    {"occurrence": -1}, {"occurrence": 4}, {"occurrence": True}, {"name": "FirstName"},
    {"target_ids": ["home_assistant_mobile:*"]}, {"target_ids": ["unsupported:someone"]},
    {"target_ids": "Jason"},
])
def test_invalid_visibility_is_rejected_instead_of_becoming_public(change):
    value = rule()["actions"][0]
    value["variable_recipients"]["message_template"][0].update(change)
    with pytest.raises(TemplateRecipientError):
        normalize_actions([value])


def test_no_selected_recipients_omits_the_occurrence_for_everyone():
    value = rule()
    value["actions"][0]["variable_recipients"]["message_template"][0]["target_ids"] = []
    action = owner.NotificationService().render_rule(value, NotificationContext(
        "authorized_entry", "Arrival", "info", {"vehicle_time_away_seconds": "8400"}))["actions"][0]
    assert action["recipient_content"] == {}
    assert content_for_recipient(action, JASON)["message"] == "after 2hrs 20m then."


def test_title_and_message_can_have_independent_audiences_and_unicode():
    value = rule()
    action = value["actions"][0]
    action["title_template"] = "🚙 @VehicleTimeAway"
    action["variable_recipients"]["title_template"] = [{"occurrence": 0, "name": "VehicleTimeAway", "target_ids": [STEPH]}]
    frozen = owner.NotificationService().render_rule(value, NotificationContext(
        "authorized_entry", "Private fallback", "info", {"vehicle_time_away_seconds": "8400"}))["actions"][0]
    assert content_for_recipient(frozen, JASON)["title"] == "🚙"
    assert content_for_recipient(frozen, STEPH)["title"] == "🚙 after 2hrs 20m"


def test_changing_an_audience_changes_confirmation_and_rule_fingerprints():
    value = rule()
    first = notification_rule_definition_fingerprint(value)
    value["actions"][0]["variable_recipients"]["message_template"][0]["target_ids"] = [STEPH]
    assert notification_rule_definition_fingerprint(value) != first


@pytest.mark.asyncio
async def test_mobile_fanout_sends_different_copy_to_each_actual_destination(monkeypatch):
    notifier = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(owner, "HomeAssistantMobileAppNotifier", lambda: notifier)
    service = owner.NotificationService()
    monkeypatch.setattr(service, "_home_assistant_mobile_actions_for_target", AsyncMock(return_value=[]))
    context = NotificationContext("authorized_entry", "Arrival", "info", {})
    targets = [JASON.split(":", 1)[1], STEPH.split(":", 1)[1]]
    assert await service._send_mobile_home_assistant(rendered(), context, targets, None, [])
    assert [call.args[2] for call in notifier.send.await_args_list] == [
        "after 2hrs 20m then after 2hrs 20m.", "after 2hrs 20m then."]


@pytest.mark.asyncio
async def test_voice_destinations_receive_their_own_copy(monkeypatch):
    service = owner.NotificationService()
    monkeypatch.setattr(service, "_select_voice_targets", AsyncMock(return_value=["media_player.jason", "media_player.shared"]))
    monkeypatch.setattr(service, "_voice_announcements_preflight", AsyncMock(return_value=None))
    announcer = SimpleNamespace(announce=AsyncMock())
    monkeypatch.setattr(owner, "HomeAssistantTtsAnnouncer", lambda: announcer)
    await service._send_voice(rendered("voice", "home_assistant_tts:media_player.jason"), SimpleNamespace())
    assert [call.args[1] for call in announcer.announce.await_args_list] == [
        "after 2hrs 20m then after 2hrs 20m.", "after 2hrs 20m then."]
