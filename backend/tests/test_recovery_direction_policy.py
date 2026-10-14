from dataclasses import replace

import pytest

from app.models.enums import AccessDirection as Direction, PresenceState as Presence
from app.modules.gate.base import GateState
from app.services.movement_fsm import MovementDirectionFSM, RecoveryDirectionIntent
from app.services.access.decision import access_is_allowed, identity_is_active

BASE = RecoveryDirectionIntent(allowed=True, exact_plate=True, explicit_direction=None,
    phone_ready=False, phone_reason="phone_journey_missing", safety_block=None,
    camera_direction=None, camera_clear=False)


@pytest.mark.parametrize("updates,direction,allowed,method,approval,camera,reason", [
    ({}, Direction.ENTRY, False, "none", True, False, "phone_journey_missing"),
    ({"phone_ready": True}, Direction.ENTRY, True, "phone_automatic", False, False, "phone_journey_missing"),
    ({"phone_ready": True, "explicit_direction": Direction.EXIT}, Direction.EXIT, True, "none", False, False, "departure_evidence"),
    ({"allowed": False, "phone_ready": True}, Direction.ENTRY, False, "none", False, False, "phone_journey_missing"),
    ({"probe_camera": True}, Direction.ENTRY, True, "none", False, True, "phone_journey_missing"),
    ({"probe_camera": True, "exact_plate": False}, Direction.ENTRY, False, "none", False, False, "phone_journey_missing"),
    ({"camera_clear": True, "camera_direction": Direction.EXIT}, Direction.EXIT, True, "none", False, False, "camera_confirmed_departure"),
    ({"camera_clear": True, "camera_direction": Direction.ENTRY}, Direction.ENTRY, True, "camera_automatic", False, False, "camera_confirmed_entry"),
    ({"camera_clear": False, "camera_direction": Direction.ENTRY}, Direction.ENTRY, False, "none", True, False, "phone_journey_missing"),
    ({"camera_clear": True, "camera_direction": Direction.ENTRY, "exact_plate": False}, Direction.ENTRY, False, "none", False, False, "phone_journey_missing"),
    ({"camera_clear": True, "camera_direction": Direction.ENTRY, "safety_block": "maintenance_mode"}, Direction.ENTRY, False, "none", False, False, "maintenance_mode"),
    ({"phone_ready": True, "safety_block": "unresolved_gate_command"}, Direction.ENTRY, False, "none", False, False, "unresolved_gate_command"),
    ({"camera_clear": True, "camera_direction": Direction.EXIT, "safety_block": "gate_malfunction"}, Direction.EXIT, True, "none", False, False, "camera_confirmed_departure"),
])
def test_recovery_precedence(updates, direction, allowed, method, approval, camera, reason):
    result = MovementDirectionFSM.resolve_recovery(replace(BASE, **updates))
    assert (result.direction, result.allowed, result.method, result.approval_eligible, result.requires_camera, result.reason) == (
        direction, allowed, method, approval, camera, reason)


@pytest.mark.parametrize("gate,presence,explicit,relevant", [
    (GateState.OPEN, Presence.PRESENT, None, False),
    (GateState.OPENING, Presence.PRESENT, Direction.EXIT, False),
    (GateState.CLOSING, Presence.PRESENT, Direction.ENTRY, True),
    (GateState.OPEN, Presence.EXITED, None, True),
    (GateState.CLOSED, Presence.PRESENT, None, True),
])
def test_only_ordinary_departures_skip_conflict_evaluation(gate, presence, explicit, relevant):
    assert MovementDirectionFSM.recovery_conflict_is_relevant(gate, presence, explicit) is relevant


@pytest.mark.parametrize("vehicle,owner,active,expected", [(False, False, False, False),
    (True, False, False, True), (True, True, False, False), (True, True, True, True)])
def test_registration_identity_eligibility(vehicle, owner, active, expected):
    assert identity_is_active(vehicle_known=vehicle, owner_known=owner, owner_active=active) is expected


@pytest.mark.parametrize("schedule,identity,visitor,external,expected", [
    (False, True, True, True, False), (True, False, False, False, False),
    (True, True, False, False, True), (True, False, True, False, True),
    (True, False, False, True, True)])
def test_schedule_denial_precedes_all_admission_sources(schedule, identity, visitor, external, expected):
    assert access_is_allowed(schedule_allowed=schedule, identity_active=identity,
        visitor_pass_matched=visitor, external_admission_matched=external) is expected
