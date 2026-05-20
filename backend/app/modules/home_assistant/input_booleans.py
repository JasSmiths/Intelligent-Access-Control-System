from dataclasses import dataclass
from typing import Any, Protocol


INPUT_BOOLEAN_ACTIONS = {"turn_on", "turn_off"}


@dataclass(frozen=True)
class InputBooleanCommandOutcome:
    entity_id: str
    action: str
    accepted: bool
    state: str | None = None
    detail: str | None = None


class HomeAssistantInputBooleanClient(Protocol):
    async def call_service(self, service_name: str, service_data: dict[str, Any]) -> dict[str, Any]:
        ...

    async def get_state(self, entity_id: str) -> Any:
        ...


async def command_input_boolean(
    client: HomeAssistantInputBooleanClient,
    entity_id: str,
    action: str,
) -> InputBooleanCommandOutcome:
    entity_id = entity_id.strip()
    if not entity_id.startswith("input_boolean."):
        return InputBooleanCommandOutcome(
            entity_id=entity_id,
            action=action,
            accepted=False,
            detail="Home Assistant input boolean entity IDs must start with input_boolean.",
        )
    if action not in INPUT_BOOLEAN_ACTIONS:
        return InputBooleanCommandOutcome(
            entity_id=entity_id,
            action=action,
            accepted=False,
            detail="Home Assistant input boolean action must be turn_on or turn_off.",
        )

    try:
        await client.call_service(f"input_boolean.{action}", {"entity_id": entity_id})
    except Exception as exc:
        return InputBooleanCommandOutcome(
            entity_id=entity_id,
            action=action,
            accepted=False,
            detail=_safe_detail(exc),
        )

    try:
        state = await client.get_state(entity_id)
    except Exception as exc:
        return InputBooleanCommandOutcome(
            entity_id=entity_id,
            action=action,
            accepted=True,
            detail=f"Command accepted; state refresh failed: {_safe_detail(exc)}",
        )

    return InputBooleanCommandOutcome(
        entity_id=entity_id,
        action=action,
        accepted=True,
        state=str(getattr(state, "state", "") or "") or None,
    )


def _safe_detail(exc: Exception) -> str:
    return str(exc)[:300]
