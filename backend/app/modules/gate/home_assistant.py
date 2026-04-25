from app.modules.gate.base import GateCommandResult, GateController, GateState
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError
from app.services.settings import get_runtime_config


class HomeAssistantGateController(GateController):
    """Home Assistant-backed gate controller."""

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or HomeAssistantClient()

    async def open_gate(self, reason: str) -> GateCommandResult:
        config = await get_runtime_config()
        if not config.home_assistant_gate_entity_id:
            return GateCommandResult(False, GateState.UNKNOWN, "Gate entity is not configured.")

        try:
            await self._client.call_service(
                config.home_assistant_gate_open_service,
                {"entity_id": config.home_assistant_gate_entity_id},
            )
            return GateCommandResult(True, await self.current_state(), reason)
        except HomeAssistantError as exc:
            return GateCommandResult(False, GateState.FAULT, str(exc))

    async def current_state(self) -> GateState:
        config = await get_runtime_config()
        if not config.home_assistant_gate_entity_id:
            return GateState.UNKNOWN
        try:
            state = await self._client.get_state(config.home_assistant_gate_entity_id)
            return map_home_assistant_gate_state(state.state)
        except HomeAssistantError:
            return GateState.UNKNOWN


def map_home_assistant_gate_state(state: str) -> GateState:
    normalized = state.lower()
    if normalized in {"open", "on", "unlocked"}:
        return GateState.OPEN
    if normalized in {"opening"}:
        return GateState.OPENING
    if normalized in {"closing"}:
        return GateState.CLOSING
    if normalized in {"closed", "off", "locked"}:
        return GateState.CLOSED
    if normalized in {"unavailable", "unknown", "problem"}:
        return GateState.UNKNOWN
    return GateState.UNKNOWN
