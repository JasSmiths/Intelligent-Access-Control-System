from datetime import UTC, datetime

from app.db.session import AsyncSessionLocal
from app.modules.gate.base import GateCommandResult, GateController, GateState
from app.modules.home_assistant.covers import command_cover, enabled_cover_entities, legacy_gate_entities, normalize_cover_entities
from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError
from app.services.schedules import evaluate_schedule_id
from app.services.settings import get_runtime_config


class HomeAssistantGateController(GateController):
    """Home Assistant-backed gate controller."""

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or HomeAssistantClient()

    async def open_gate(self, reason: str) -> GateCommandResult:
        config = await get_runtime_config()
        configured_gate_entities = normalize_cover_entities(
            config.home_assistant_gate_entities,
            default_open_service=config.home_assistant_gate_open_service,
        )
        gate_entities = (
            enabled_cover_entities(configured_gate_entities, default_open_service=config.home_assistant_gate_open_service)
            if configured_gate_entities
            else legacy_gate_entities(config.home_assistant_gate_entity_id, config.home_assistant_gate_open_service)
        )
        if not gate_entities:
            return GateCommandResult(False, GateState.UNKNOWN, "Gate entity is not configured.")

        now = datetime.now(tz=UTC)
        async with AsyncSessionLocal() as session:
            schedule_evaluations = [
                await evaluate_schedule_id(
                    session,
                    entity.get("schedule_id"),
                    now,
                    timezone_name=config.site_timezone,
                    default_policy=config.schedule_default_policy,
                    source="gate",
                )
                for entity in gate_entities
            ]
        denied = [
            f"{entity.get('name') or entity['entity_id']}: {evaluation.reason or 'outside schedule'}"
            for entity, evaluation in zip(gate_entities, schedule_evaluations, strict=False)
            if not evaluation.allowed
        ]
        if denied:
            return GateCommandResult(False, GateState.FAULT, "; ".join(denied))

        outcomes = [await command_cover(self._client, entity, "open", reason) for entity in gate_entities]
        failed = [outcome for outcome in outcomes if not outcome.accepted]
        if failed:
            detail = "; ".join(
                f"{outcome.name}: {outcome.detail or 'command failed'}"
                for outcome in failed
            )
            return GateCommandResult(False, GateState.FAULT, detail)

        return GateCommandResult(True, await self.current_state(), reason)

    async def current_state(self) -> GateState:
        config = await get_runtime_config()
        configured_gate_entities = normalize_cover_entities(
            config.home_assistant_gate_entities,
            default_open_service=config.home_assistant_gate_open_service,
        )
        gate_entities = (
            enabled_cover_entities(configured_gate_entities, default_open_service=config.home_assistant_gate_open_service)
            if configured_gate_entities
            else legacy_gate_entities(config.home_assistant_gate_entity_id, config.home_assistant_gate_open_service)
        )
        if not gate_entities:
            return GateState.UNKNOWN
        try:
            state = await self._client.get_state(str(gate_entities[0]["entity_id"]))
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
