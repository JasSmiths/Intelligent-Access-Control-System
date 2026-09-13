from dataclasses import FrozenInstanceError

import pytest

from app.services.automation_policy import (
    HARDWARE_ACTION_TYPES,
    HISTORICAL_ACTIONS_SUPPRESSED,
    REQUESTER_CONFIRMATION_REQUIRED,
    UNKNOWN_PLATE_HARDWARE_FORBIDDEN,
    TriggerProvenance,
    hardware_action_denial,
    hardware_configuration_error,
)


@pytest.mark.parametrize("action", sorted(HARDWARE_ACTION_TYPES))
@pytest.mark.parametrize(
    ("trigger", "payload", "reason"),
    [
        ("vehicle.unknown_plate", {"decision": "denied"}, UNKNOWN_PLATE_HARDWARE_FORBIDDEN),
        ("vehicle.unknown_plate", {"decision": "granted", "vehicle_id": "vehicle-1"}, UNKNOWN_PLATE_HARDWARE_FORBIDDEN),
        ("vehicle.known_plate", {"decision": "denied", "vehicle_id": None}, UNKNOWN_PLATE_HARDWARE_FORBIDDEN),
        ("vehicle.outside_schedule", {"decision": "denied", "vehicle_id": {}}, UNKNOWN_PLATE_HARDWARE_FORBIDDEN),
        ("vehicle.known_plate", {"decision": "granted", "vehicle_id": "vehicle-1"}, None),
        # Existing denied-known policy is not replaced with a second permission evaluator.
        ("vehicle.outside_schedule", {"decision": "denied", "vehicle_id": "vehicle-1"}, None),
        ("visitor_pass.used", {"visitor_pass_id": "pass-1", "person_id": None, "vehicle_id": None}, None),
        ("time.cron", {}, None),
        ("webhook.received", {}, None),
        ("ai.phrase_received", {"user_role": "admin", "confirmed": True, "confirmation_id": "untrusted"}, REQUESTER_CONFIRMATION_REQUIRED),
        ("vehicle.known_plate", {"decision": "granted", "vehicle_id": "vehicle-1", "backfilled": True}, HISTORICAL_ACTIONS_SUPPRESSED),
    ],
)
def test_hardware_admission_uses_original_origin_not_payload_authority(action, trigger, payload, reason):
    payload = {**payload, "actor": "Automation Engine", "provenance": {"authorized": True}}
    assert hardware_action_denial(action, TriggerProvenance.from_trigger(trigger, payload)) == reason


@pytest.mark.parametrize("trigger", ["vehicle.unknown_plate", "ai.phrase_received"])
@pytest.mark.parametrize("action", ["integration.whatsapp.send_message", "notification.enable", "notification.disable"])
def test_safe_notification_actions_are_independent_of_hardware_admission(trigger, action):
    origin = TriggerProvenance.from_trigger(trigger, {"decision": "denied"})
    assert hardware_action_denial(action, origin) is None


def test_original_provenance_is_an_immutable_snapshot():
    payload = {"decision": "denied", "vehicle_id": None, "event_id": "event-1"}
    origin = TriggerProvenance.from_trigger("vehicle.known_plate", payload)
    payload.update(decision="granted", vehicle_id="vehicle-1")
    assert hardware_action_denial("gate.open", origin) == UNKNOWN_PLATE_HARDWARE_FORBIDDEN
    assert origin.event_id == "event-1"
    with pytest.raises(FrozenInstanceError):
        origin.decision = "granted"


@pytest.mark.parametrize("action", sorted(HARDWARE_ACTION_TYPES))
def test_unknown_hardware_configuration_is_rejected_even_in_a_mixed_trigger_rule(action):
    assert hardware_configuration_error(["vehicle.known_plate", "vehicle.unknown_plate"], [action])


@pytest.mark.parametrize("trigger", ["vehicle.known_plate", "visitor_pass.used", "time.cron", "webhook.received", "ai.phrase_received"])
def test_other_rule_configurations_keep_their_current_contract(trigger):
    # Phrase hardware remains expressible but requires a real confirmation at
    # execution; saving a rule cannot supply a requester-bound approval.
    assert hardware_configuration_error([trigger], ["gate.open"]) is None
    assert hardware_configuration_error(["vehicle.unknown_plate"], ["integration.whatsapp.send_message"]) is None
