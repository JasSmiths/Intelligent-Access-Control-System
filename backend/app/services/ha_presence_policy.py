"""Current Person binding policy and exact HA identity; no database/provider work."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from app.models import Person
from app.models.enums import AccessDirection, PresenceState
from app.modules.home_assistant.input_boolean_contracts import (
    INPUT_BOOLEAN_ACTIONS,
    FrozenHaConnection,
    HaAction,
)
from app.services.ha_presence_contracts import HaCurrentPerson

DEFAULT_INPUT_BOOLEAN_ACTION = "turn_off"


def normalize_input_boolean_entity_ids(entity_ids: list[str] | None) -> list[str]:
    selected = list(dict.fromkeys(str(entity_id).strip() for entity_id in entity_ids or [] if str(entity_id).strip()))
    for entity_id in selected:
        if not entity_id.startswith("input_boolean."):
            raise ValueError("Home Assistant presence entity IDs must start with input_boolean.")
    return selected


def normalize_input_boolean_action(action: str | None) -> str:
    if not action:
        return DEFAULT_INPUT_BOOLEAN_ACTION
    if action not in INPUT_BOOLEAN_ACTIONS:
        raise ValueError("Home Assistant presence input_boolean action must be turn_on or turn_off.")
    return action


def person_input_boolean_entity_ids(person: Person) -> list[str]:
    return normalize_input_boolean_entity_ids(
        getattr(person, "home_assistant_presence_input_boolean_entity_ids", None)
    )


def person_input_boolean_action_for_direction(person: Person, direction: AccessDirection) -> str:
    action = (
        getattr(person, "home_assistant_presence_input_boolean_entry_action", None)
        if direction == AccessDirection.ENTRY
        else getattr(person, "home_assistant_presence_input_boolean_exit_action", None)
    )
    return normalize_input_boolean_action(action)



def person_presence_binding_signature(person: Person) -> tuple[bool, tuple[str, ...], str, str]:
    """Only current eligibility and normalized presence bindings invalidate work."""
    return (
        bool(person.is_active), tuple(person_input_boolean_entity_ids(person)),
        person_input_boolean_action_for_direction(person, AccessDirection.ENTRY),
        person_input_boolean_action_for_direction(person, AccessDirection.EXIT),
    )


@dataclass(frozen=True)
class HaBinding:
    entity_ids: tuple[str, ...]
    action: HaAction
    fingerprint: str


def current_binding(person: HaCurrentPerson, state: PresenceState) -> HaBinding:
    entities = normalize_input_boolean_entity_ids(person.entity_ids)
    entry = normalize_input_boolean_action(person.entry_action)
    exit_action = normalize_input_boolean_action(person.exit_action)
    action = entry if state == PresenceState.PRESENT else exit_action
    return HaBinding(tuple(entities), cast(HaAction, action), _digest([entities, entry, exit_action]))


def physical_target_key(connection: FrozenHaConnection, entity_id: str) -> str:
    return _digest([connection.home_assistant_url.rstrip('/'), entity_id])


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
