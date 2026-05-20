from typing import Any

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, Person
from app.models.enums import AccessDirection
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.home_assistant.input_booleans import (
    INPUT_BOOLEAN_ACTIONS,
    InputBooleanCommandOutcome,
    command_input_boolean,
)
from app.services.event_bus import event_bus
from app.services.maintenance import is_maintenance_mode_active
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    audit_log_event_payload,
    write_audit_log,
)

logger = get_logger(__name__)

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


async def apply_person_presence_input_boolean_actions(
    person: Person,
    event: AccessEvent,
    *,
    source: str,
) -> None:
    entity_ids = person_input_boolean_entity_ids(person)
    if not entity_ids:
        return

    action = person_input_boolean_action_for_direction(person, event.direction)
    if await is_maintenance_mode_active():
        for entity_id in entity_ids:
            await _record_input_boolean_result(
                person,
                event,
                entity_id=entity_id,
                action=action,
                source=source,
                outcome="skipped",
                level="warning",
                accepted=False,
                state=None,
                detail="Maintenance Mode is active. Automated Home Assistant presence actions are disabled.",
            )
        return

    client = HomeAssistantClient()
    for entity_id in entity_ids:
        result = await command_input_boolean(client, entity_id, action)
        await _record_input_boolean_result(
            person,
            event,
            entity_id=entity_id,
            action=action,
            source=source,
            outcome="accepted" if result.accepted else "failed",
            level="info" if result.accepted else "error",
            accepted=result.accepted,
            state=result.state,
            detail=result.detail,
        )
        if result.accepted:
            logger.info(
                "person_presence_input_boolean_commanded",
                extra={
                    "person_id": str(person.id),
                    "event_id": str(event.id),
                    "entity_id": entity_id,
                    "action": action,
                    "state": result.state,
                },
            )
        else:
            logger.warning(
                "person_presence_input_boolean_failed",
                extra={
                    "person_id": str(person.id),
                    "event_id": str(event.id),
                    "entity_id": entity_id,
                    "action": action,
                    "detail": result.detail,
                },
            )


async def _record_input_boolean_result(
    person: Person,
    event: AccessEvent,
    *,
    entity_id: str,
    action: str,
    source: str,
    outcome: str,
    level: str,
    accepted: bool,
    state: str | None,
    detail: str | None,
) -> None:
    metadata = _input_boolean_metadata(
        person,
        event,
        entity_id=entity_id,
        action=action,
        source=source,
        accepted=accepted,
        state=state,
        detail=detail,
    )
    async with AsyncSessionLocal() as session:
        row = await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action=f"person_presence_input_boolean.{action}",
            actor="Access Event Automation",
            target_entity="HomeAssistantInputBoolean",
            target_id=entity_id,
            target_label=entity_id,
            outcome=outcome,
            level=level,
            metadata=metadata,
        )
        await session.commit()
        await session.refresh(row)
    await event_bus.publish("audit.log.created", audit_log_event_payload(row))
    await event_bus.publish(
        f"person_presence_input_boolean.{outcome}",
        {
            "person_id": str(person.id),
            "person": person.display_name,
            "event_id": str(event.id),
            "registration_number": event.registration_number,
            "direction": event.direction.value,
            "entity_id": entity_id,
            "action": action,
            "accepted": accepted,
            "state": state,
            "detail": detail,
            "source": source,
        },
    )


def _input_boolean_metadata(
    person: Person,
    event: AccessEvent,
    *,
    entity_id: str,
    action: str,
    source: str,
    accepted: bool,
    state: str | None,
    detail: str | None,
) -> dict[str, Any]:
    vehicle = getattr(event, "vehicle", None)
    vehicle_id = getattr(event, "vehicle_id", None) or getattr(vehicle, "id", None)
    return {
        "source": source,
        "access_event_id": str(event.id),
        "registration_number": event.registration_number,
        "direction": event.direction.value,
        "decision": event.decision.value if hasattr(event.decision, "value") else str(event.decision),
        "person_id": str(person.id),
        "person": person.display_name,
        "vehicle_id": str(vehicle_id) if vehicle_id else None,
        "vehicle_registration_number": getattr(vehicle, "registration_number", None),
        "entity_id": entity_id,
        "action": action,
        "accepted": accepted,
        "state": state,
        "detail": detail,
        "event_source": getattr(event, "source", None),
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
    }
