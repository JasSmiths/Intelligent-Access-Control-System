from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable

from app.models.enums import AccessDecision, AccessDirection, PresenceState
from app.modules.gate.base import GateState


ARRIVAL_GATE_STATES = {GateState.CLOSED}
DEPARTURE_GATE_STATES = {GateState.OPEN, GateState.OPENING, GateState.CLOSING}


class MovementState(StrEnum):
    OBSERVED = "observed"
    CANDIDATE_IDENTITY_RESOLVED = "candidate_identity_resolved"
    DIRECTION_RESOLVED = "direction_resolved"
    AUTHORIZED = "authorized"
    PHYSICAL_COMMAND_PENDING = "physical_command_pending"
    PHYSICAL_COMMAND_ACCEPTED = "physical_command_accepted"
    PRESENCE_COMMITTED = "presence_committed"
    COMPLETED = "completed"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


class MovementSuppressionKind(StrEnum):
    SAME_MOVEMENT_NOISE = "same_movement_noise"
    SAME_GATE_CYCLE_ECHO = "same_gate_cycle_echo"


@dataclass(frozen=True)
class CameraTieBreakerEvidence:
    direction: AccessDirection | None
    confidence: float | None = None
    clear: bool = False
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MovementIntent:
    source: str
    captured_at: datetime
    registration_number: str
    allowed: bool
    person_known: bool
    gate_state: GateState
    gate_observation: dict[str, Any] = field(default_factory=dict)
    vehicle_known: bool = False
    presence_state: PresenceState | None = None
    explicit_direction: AccessDirection | None = None
    visitor_pass_departure: bool = False
    gate_malfunction: dict[str, Any] | None = None
    previous_live_direction: AccessDirection | None = None
    previous_live_event_payload: dict[str, Any] = field(default_factory=dict)
    camera_tiebreaker: CameraTieBreakerEvidence | None = None


@dataclass(frozen=True)
class MovementDecision:
    state: MovementState
    direction: AccessDirection
    resolution: dict[str, Any]
    requires_external_evidence: str | None = None

    @property
    def physical_action_required(self) -> bool:
        return (
            self.direction == AccessDirection.ENTRY
            and self.resolution.get("physical_action") == "gate.open"
        )


@dataclass(frozen=True)
class ResolvedMovementWindow:
    source: str
    registration_number: str
    first_seen: datetime
    debounce_expires_at: datetime
    gate_cycle_expires_at: datetime
    direction: AccessDirection | None = None
    decision: AccessDecision | None = None


@dataclass(frozen=True)
class PlateReadMovementEvidence:
    source: str
    registration_number: str
    captured_at: datetime
    gate_state: GateState | None = None
    direction_hint: AccessDirection | None = None
    has_known_vehicle_match: bool = False


@dataclass(frozen=True)
class SuppressionDecision:
    suppress: bool
    state: MovementState
    reason: str | None = None
    kind: MovementSuppressionKind | None = None


class MovementDirectionFSM:
    """Deterministic movement classifier for access-control reads.

    The service layer gathers evidence that may require I/O. This FSM keeps the
    precedence rules in one transition table so entry, exit, convoy, visitor,
    and malfunction cases are not scattered through procedural branches.
    """

    def resolve(self, intent: MovementIntent) -> MovementDecision:
        resolution: dict[str, Any] = {
            "source": "unknown",
            "gate_observation": intent.gate_observation,
            "movement_state": MovementState.OBSERVED.value,
        }

        if not intent.allowed:
            resolution.update(
                {
                    "source": "access_denied",
                    "direction": AccessDirection.DENIED.value,
                    "movement_state": MovementState.FAILED.value,
                }
            )
            return MovementDecision(MovementState.FAILED, AccessDirection.DENIED, resolution)

        if intent.visitor_pass_departure:
            resolution.update(
                {
                    "source": "visitor_pass_presence",
                    "direction": AccessDirection.EXIT.value,
                    "movement_state": MovementState.DIRECTION_RESOLVED.value,
                }
            )
            return MovementDecision(MovementState.DIRECTION_RESOLVED, AccessDirection.EXIT, resolution)

        if intent.gate_malfunction and (intent.person_known or intent.vehicle_known):
            direction = (
                AccessDirection.EXIT
                if intent.previous_live_direction == AccessDirection.ENTRY
                else AccessDirection.ENTRY
            )
            resolution.update(
                {
                    "source": "gate_malfunction_vehicle_history",
                    "direction": direction.value,
                    "gate_malfunction": intent.gate_malfunction,
                    "history_lookup": "person_or_vehicle",
                    "movement_state": MovementState.DIRECTION_RESOLVED.value,
                    **intent.previous_live_event_payload,
                }
            )
            return MovementDecision(MovementState.DIRECTION_RESOLVED, direction, resolution)

        if intent.gate_state in ARRIVAL_GATE_STATES:
            if intent.person_known and intent.presence_state == PresenceState.PRESENT:
                if intent.camera_tiebreaker is None:
                    resolution.update(
                        {
                            "source": "gate_state",
                            "direction": AccessDirection.ENTRY.value,
                            "movement_state": MovementState.CANDIDATE_IDENTITY_RESOLVED.value,
                        }
                    )
                    return MovementDecision(
                        MovementState.CANDIDATE_IDENTITY_RESOLVED,
                        AccessDirection.ENTRY,
                        resolution,
                        requires_external_evidence="camera_tiebreaker",
                    )
                resolution["camera_tiebreaker"] = intent.camera_tiebreaker.payload
                if intent.camera_tiebreaker.direction and intent.camera_tiebreaker.clear:
                    direction = intent.camera_tiebreaker.direction
                    resolution.update(
                        {
                            "source": "camera_tiebreaker",
                            "direction": direction.value,
                            "movement_state": MovementState.DIRECTION_RESOLVED.value,
                        }
                    )
                    return MovementDecision(MovementState.DIRECTION_RESOLVED, direction, resolution)
                resolution["camera_tiebreaker_ignored_reason"] = "low_confidence"

            resolution.update(
                {
                    "source": "gate_state",
                    "direction": AccessDirection.ENTRY.value,
                    "movement_state": MovementState.PHYSICAL_COMMAND_PENDING.value,
                    "physical_action": "gate.open",
                }
            )
            return MovementDecision(
                MovementState.PHYSICAL_COMMAND_PENDING,
                AccessDirection.ENTRY,
                resolution,
            )

        if intent.gate_state in DEPARTURE_GATE_STATES:
            if intent.person_known and intent.presence_state != PresenceState.PRESENT:
                resolution.update(
                    {
                        "source": "presence_over_gate_state",
                        "direction": AccessDirection.ENTRY.value,
                        "gate_state_direction": AccessDirection.EXIT.value,
                        "presence_state": PresenceState.EXITED.value,
                        "movement_state": MovementState.DIRECTION_RESOLVED.value,
                    }
                )
                return MovementDecision(MovementState.DIRECTION_RESOLVED, AccessDirection.ENTRY, resolution)
            resolution.update(
                {
                    "source": "gate_state",
                    "direction": AccessDirection.EXIT.value,
                    "movement_state": MovementState.DIRECTION_RESOLVED.value,
                }
            )
            return MovementDecision(MovementState.DIRECTION_RESOLVED, AccessDirection.EXIT, resolution)

        if intent.explicit_direction in {AccessDirection.ENTRY, AccessDirection.EXIT}:
            resolution.update(
                {
                    "source": "payload",
                    "direction": intent.explicit_direction.value,
                    "movement_state": MovementState.DIRECTION_RESOLVED.value,
                }
            )
            return MovementDecision(MovementState.DIRECTION_RESOLVED, intent.explicit_direction, resolution)

        if not intent.person_known:
            resolution.update(
                {
                    "source": "default_entry_no_person",
                    "direction": AccessDirection.ENTRY.value,
                    "movement_state": MovementState.DIRECTION_RESOLVED.value,
                }
            )
            return MovementDecision(MovementState.DIRECTION_RESOLVED, AccessDirection.ENTRY, resolution)

        direction = (
            AccessDirection.EXIT
            if intent.presence_state == PresenceState.PRESENT
            else AccessDirection.ENTRY
        )
        resolution.update(
            {
                "source": "presence",
                "direction": direction.value,
                "movement_state": MovementState.DIRECTION_RESOLVED.value,
            }
        )
        return MovementDecision(MovementState.DIRECTION_RESOLVED, direction, resolution)


class MovementSuppressionFSM:
    """Classifies read suppression as explicit movement states."""

    def classify_exact_plate_read(
        self,
        evidence: PlateReadMovementEvidence,
        resolved_windows: Iterable[ResolvedMovementWindow],
    ) -> SuppressionDecision:
        for window in resolved_windows:
            if evidence.source != window.source:
                continue
            if window.first_seen <= evidence.captured_at <= window.debounce_expires_at:
                if (
                    evidence.has_known_vehicle_match
                    and evidence.registration_number != window.registration_number
                ):
                    continue
                return SuppressionDecision(
                    True,
                    MovementState.SUPPRESSED,
                    "exact_known_vehicle_plate_already_resolved_in_debounce_window",
                    MovementSuppressionKind.SAME_MOVEMENT_NOISE,
                )

            direction_conflicts = bool(
                window.direction
                and evidence.direction_hint
                and evidence.direction_hint != window.direction
            )
            entry_gate_echo = (
                window.direction == AccessDirection.ENTRY
                and evidence.direction_hint == AccessDirection.EXIT
            )
            if not (
                window.decision == AccessDecision.GRANTED
                and window.direction in {AccessDirection.ENTRY, AccessDirection.EXIT}
                and evidence.has_known_vehicle_match
                and evidence.registration_number == window.registration_number
                and evidence.gate_state != GateState.CLOSED
                and (not direction_conflicts or entry_gate_echo)
                and window.first_seen <= evidence.captured_at <= window.gate_cycle_expires_at
            ):
                continue
            return SuppressionDecision(
                True,
                MovementState.SUPPRESSED,
                "exact_known_vehicle_plate_already_resolved_in_gate_cycle",
                MovementSuppressionKind.SAME_GATE_CYCLE_ECHO,
            )
        return SuppressionDecision(False, MovementState.OBSERVED)
