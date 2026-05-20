from datetime import UTC, datetime, timedelta

from app.models.enums import AccessDecision, AccessDirection, PresenceState
from app.modules.gate.base import GateState
from app.services.movement_fsm import (
    CameraTieBreakerEvidence,
    MovementDirectionFSM,
    MovementIntent,
    MovementState,
    MovementSuppressionFSM,
    PlateReadMovementEvidence,
    ResolvedMovementWindow,
)


def test_movement_fsm_resolves_closed_gate_entry_as_physical_command() -> None:
    decision = MovementDirectionFSM().resolve(
        _intent(gate_state=GateState.CLOSED, presence_state=PresenceState.EXITED)
    )

    assert decision.direction == AccessDirection.ENTRY
    assert decision.state == MovementState.PHYSICAL_COMMAND_PENDING
    assert decision.physical_action_required is True
    assert decision.resolution["source"] == "gate_state"


def test_movement_fsm_resolves_open_gate_absent_person_as_convoy_entry() -> None:
    decision = MovementDirectionFSM().resolve(
        _intent(gate_state=GateState.OPEN, presence_state=PresenceState.EXITED)
    )

    assert decision.direction == AccessDirection.ENTRY
    assert decision.state == MovementState.DIRECTION_RESOLVED
    assert decision.physical_action_required is False
    assert decision.resolution["source"] == "presence_over_gate_state"


def test_movement_fsm_requires_camera_tiebreaker_for_closed_gate_present_person() -> None:
    decision = MovementDirectionFSM().resolve(
        _intent(gate_state=GateState.CLOSED, presence_state=PresenceState.PRESENT)
    )

    assert decision.requires_external_evidence == "camera_tiebreaker"
    assert decision.direction == AccessDirection.ENTRY


def test_movement_fsm_accepts_clear_camera_exit_tiebreaker() -> None:
    decision = MovementDirectionFSM().resolve(
        _intent(
            gate_state=GateState.CLOSED,
            presence_state=PresenceState.PRESENT,
            camera_tiebreaker=CameraTieBreakerEvidence(
                direction=AccessDirection.EXIT,
                confidence=0.91,
                clear=True,
                payload={"direction": "exit", "confidence": 0.91},
            ),
        )
    )

    assert decision.direction == AccessDirection.EXIT
    assert decision.resolution["source"] == "camera_tiebreaker"


def test_suppression_fsm_distinguishes_gate_cycle_echo_from_late_departure() -> None:
    first_seen = datetime(2026, 5, 1, 21, 6, tzinfo=UTC)
    window = ResolvedMovementWindow(
        source="ubiquiti",
        registration_number="PE70DHX",
        first_seen=first_seen,
        debounce_expires_at=first_seen + timedelta(seconds=6),
        gate_cycle_expires_at=first_seen + timedelta(seconds=60),
        direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,
    )
    fsm = MovementSuppressionFSM()

    immediate_echo = fsm.classify_exact_plate_read(
        PlateReadMovementEvidence(
            source="ubiquiti",
            registration_number="PE70DHX",
            captured_at=first_seen + timedelta(seconds=12),
            gate_state=GateState.OPEN,
            direction_hint=AccessDirection.EXIT,
            has_known_vehicle_match=True,
        ),
        [window],
    )
    late_departure = fsm.classify_exact_plate_read(
        PlateReadMovementEvidence(
            source="ubiquiti",
            registration_number="PE70DHX",
            captured_at=first_seen + timedelta(seconds=70),
            gate_state=GateState.OPEN,
            direction_hint=AccessDirection.EXIT,
            has_known_vehicle_match=True,
        ),
        [window],
    )

    assert immediate_echo.suppress is True
    assert immediate_echo.reason == "exact_known_vehicle_plate_already_resolved_in_gate_cycle"
    assert late_departure.suppress is False


def _intent(
    *,
    gate_state: GateState,
    presence_state: PresenceState | None,
    camera_tiebreaker: CameraTieBreakerEvidence | None = None,
):
    return MovementIntent(
        source="ubiquiti",
        captured_at=datetime(2026, 5, 1, 21, 6, tzinfo=UTC),
        registration_number="PE70DHX",
        allowed=True,
        person_known=True,
        vehicle_known=True,
        gate_state=gate_state,
        gate_observation={"state": gate_state.value},
        presence_state=presence_state,
        camera_tiebreaker=camera_tiebreaker,
    )
