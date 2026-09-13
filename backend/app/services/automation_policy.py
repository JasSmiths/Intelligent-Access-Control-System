"""Pure automation admission rules; transport payloads never grant actor authority."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


HARDWARE_ACTION_TYPES = frozenset({"gate.open", "garage_door.open", "garage_door.close"})
RECOGNITION_TRIGGER_KEYS = frozenset(
    {"vehicle.known_plate", "vehicle.unknown_plate", "vehicle.outside_schedule"}
)
UNKNOWN_PLATE_HARDWARE_FORBIDDEN = "unknown_plate_hardware_forbidden"
REQUESTER_CONFIRMATION_REQUIRED = "requester_confirmation_required"
HISTORICAL_ACTIONS_SUPPRESSED = "historical_actions_suppressed"

HARDWARE_DENIAL_DETAILS = {
    UNKNOWN_PLATE_HARDWARE_FORBIDDEN: "Unknown plates cannot trigger gate or garage commands.",
    REQUESTER_CONFIRMATION_REQUIRED: (
        "Phrase-triggered hardware needs confirmation by the requesting active Admin. "
        "Ask Alfred to prepare the hardware action for confirmation."
    ),
    HISTORICAL_ACTIONS_SUPPRESSED: "Historical observations cannot replay hardware commands.",
}


@dataclass(frozen=True)
class TriggerProvenance:
    """Original trigger evidence, captured before templates or action processing.

    This is evidence for admission, not a permission token. Only the trigger's
    decision/identity fields are read; nested role, actor, confirmation and
    caller-supplied provenance objects cannot authorize an action.
    """

    trigger_key: str
    decision: str | None
    vehicle_id: str | None
    event_id: str | None
    historical: bool

    @classmethod
    def from_trigger(cls, trigger_key: str, payload: Mapping[str, Any]) -> TriggerProvenance:
        def optional_text(value: Any) -> str | None:
            return value.strip() or None if isinstance(value, str) else None

        return cls(
            trigger_key=trigger_key,
            decision=(optional_text(payload.get("decision")) or "").lower() or None,
            vehicle_id=optional_text(payload.get("vehicle_id")),
            event_id=optional_text(payload.get("access_event_id") or payload.get("event_id")),
            historical=bool(payload.get("backfilled") or payload.get("skip_automation_actions")),
        )


def hardware_action_denial(action_type: str, provenance: TriggerProvenance) -> str | None:
    """Return the specific reason to withhold a hardware action, if any.

    Schedule/webhook rules retain their existing standing authority. This
    boundary does not reimplement vehicle permissions or known-vehicle policy.
    A phrase observation is always pre-confirmation: a separate requester-bound
    approval is required, and no boolean from an event is a substitute for it.
    """
    if action_type not in HARDWARE_ACTION_TYPES:
        return None
    if provenance.historical:
        return HISTORICAL_ACTIONS_SUPPRESSED
    if provenance.trigger_key == "ai.phrase_received":
        return REQUESTER_CONFIRMATION_REQUIRED
    if provenance.trigger_key == "vehicle.unknown_plate" or (
        provenance.trigger_key in RECOGNITION_TRIGGER_KEYS
        and provenance.decision == "denied"
        and not provenance.vehicle_id
    ):
        return UNKNOWN_PLATE_HARDWARE_FORBIDDEN
    return None


def hardware_configuration_error(
    trigger_keys: Iterable[str], action_types: Iterable[str]
) -> str | None:
    if "vehicle.unknown_plate" in trigger_keys and HARDWARE_ACTION_TYPES.intersection(action_types):
        return HARDWARE_DENIAL_DETAILS[UNKNOWN_PLATE_HARDWARE_FORBIDDEN]
    return None
