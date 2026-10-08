import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.modules.home_assistant.client import HomeAssistantError, HomeAssistantState
from app.modules.home_assistant.input_boolean_contracts import (
    INPUT_BOOLEAN_ACTIONS,
    FrozenHaConnection,
    HaAction,
    HaConnection,
    HaDelivery,
)


@dataclass(frozen=True)
class InputBooleanCommandOutcome:
    entity_id: str
    action: str
    accepted: bool
    state: str | None = None
    detail: str | None = None


class HomeAssistantInputBooleanClient(Protocol):
    async def call_service(
        self, service_name: str, service_data: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def get_state(self, entity_id: str) -> Any: ...


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
    except Exception as exc:  # noqa: BLE001 - Vendor boundary preserves failure truth for arbitrary SDK errors.
        return InputBooleanCommandOutcome(
            entity_id=entity_id,
            action=action,
            accepted=False,
            detail=_safe_detail(exc),
        )

    try:
        state = await client.get_state(entity_id)
    except Exception as exc:  # noqa: BLE001 - Vendor boundary preserves failure truth for arbitrary SDK errors.
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


class FrozenInputBooleanClient(Protocol):
    async def call_service(
        self, service_name: str, service_data: dict[str, Any], *,
        runtime_config: HaConnection | None = None,
        start_deadline: float | None = None,
    ) -> dict[str, Any]: ...

    async def get_state(
        self, entity_id: str, *, runtime_config: HaConnection | None = None,
        start_deadline: float | None = None,
    ) -> HomeAssistantState: ...


async def command_frozen_input_boolean(
    client: FrozenInputBooleanClient, entity_id: str, action: HaAction, *,
    connection: FrozenHaConnection, operation_deadline: float,
) -> HaDelivery:
    """One frozen POST, preserving delivery certainty and safe bounded metadata.

    The owner supplies the original absolute deadline and owns the actual child.
    This function creates no tasks/timer/retry. Cancellation after a known accepted
    POST only stops optional observation; it cannot turn that truth into failure.
    """
    if (
        not isinstance(entity_id, str) or not entity_id.startswith("input_boolean.")
        or not isinstance(action, str) or action not in INPUT_BOOLEAN_ACTIONS
    ):
        return HaDelivery("not_sent", "invalid_binding")
    if asyncio.get_running_loop().time() >= operation_deadline:
        return HaDelivery("not_sent", "operation_expired")
    try:
        await client.call_service(f"input_boolean.{action}", {"entity_id": entity_id},
            runtime_config=connection, start_deadline=operation_deadline)
    except HomeAssistantError as exc:
        if exc.delivery == "not_sent":
            return HaDelivery("not_sent", "provider_not_sent")
        if exc.delivery == "rejected":
            return HaDelivery("rejected", "provider_rejected")
        if exc.delivery != "accepted":
            return HaDelivery("unknown", "provider_unknown")
        # A malformed successful JSON receipt is still a known accepted POST.
    except asyncio.CancelledError:
        return HaDelivery("unknown", "operation_cancelled")
    except Exception:  # noqa: BLE001 - Unknown SDK failures cannot prove non-delivery.
        return HaDelivery("unknown", "provider_unknown")

    if asyncio.get_running_loop().time() >= operation_deadline:
        return HaDelivery("accepted", "accepted_observation_unavailable")
    try:
        observed = await client.get_state(entity_id, runtime_config=connection, start_deadline=operation_deadline)
        observed_state = observed.state
    except asyncio.CancelledError:
        return HaDelivery("accepted", "accepted_observation_unavailable")
    except Exception:  # noqa: BLE001 - Observation failure never rewrites an accepted POST.
        return HaDelivery("accepted", "accepted_observation_unavailable")
    state: Literal["on", "off"] | None = None
    if isinstance(observed_state, str):
        if observed_state == "on":
            state = "on"
        elif observed_state == "off":
            state = "off"
    return HaDelivery("accepted", "accepted", state)
