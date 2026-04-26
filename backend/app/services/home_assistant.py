import asyncio
from functools import lru_cache

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import Person, Presence
from app.models.enums import PresenceState
from app.modules.gate.base import GateState
from app.modules.gate.home_assistant import map_home_assistant_gate_state
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.home_assistant.covers import (
    cover_entity_state_payload,
    legacy_gate_entities,
    normalize_cover_entities,
    normalize_cover_state,
)
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config

logger = get_logger(__name__)

FRONT_DOOR_ENTITY_ID = "binary_sensor.front_door"
BACK_DOOR_ENTITY_ID = "binary_sensor.back_door"
MAIN_GARAGE_DOOR_ENTITY_ID = "cover.main_garage_door"
MUMS_GARAGE_DOOR_ENTITY_ID = "cover.mums_garage_door"
DOOR_ENTITY_IDS = {
    FRONT_DOOR_ENTITY_ID: "front_door",
    BACK_DOOR_ENTITY_ID: "back_door",
}


class HomeAssistantIntegrationService:
    """Keeps Home Assistant state synchronized with IACS realtime state."""

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or HomeAssistantClient()
        self._listener: asyncio.Task | None = None
        self._last_gate_state = GateState.UNKNOWN

    async def configured(self) -> bool:
        config = await get_runtime_config()
        return bool(config.home_assistant_url and config.home_assistant_token)

    async def start(self) -> None:
        if not await self.configured():
            logger.info("home_assistant_not_configured")
            return
        if self._listener and not self._listener.done():
            return
        self._listener = asyncio.create_task(self._listen(), name="home-assistant-listener")
        logger.info("home_assistant_listener_started")

    async def stop(self) -> None:
        if not self._listener:
            return
        self._listener.cancel()
        try:
            await self._listener
        except asyncio.CancelledError:
            pass
        logger.info("home_assistant_listener_stopped")

    async def status(self) -> dict:
        config = await get_runtime_config()
        gate_entities = normalize_cover_entities(
            config.home_assistant_gate_entities,
            default_open_service=config.home_assistant_gate_open_service,
        ) or legacy_gate_entities(config.home_assistant_gate_entity_id, config.home_assistant_gate_open_service)
        garage_door_entities = normalize_cover_entities(
            config.home_assistant_garage_door_entities,
            default_open_service=config.home_assistant_gate_open_service,
        )
        status = {
            "configured": bool(config.home_assistant_url and config.home_assistant_token),
            "gate_entity_id": gate_entities[0]["entity_id"] if gate_entities else config.home_assistant_gate_entity_id,
            "gate_entities": [cover_entity_state_payload(entity) for entity in gate_entities],
            "garage_door_entities": [cover_entity_state_payload(entity) for entity in garage_door_entities],
            "default_media_player": config.home_assistant_default_media_player,
            "presence_entities": config.home_assistant_presence_entities,
            "last_gate_state": self._last_gate_state.value,
        }
        if status["configured"]:
            first_gate_state: str | None = None
            for index, entity in enumerate(gate_entities):
                entity_state = await self._cover_state(str(entity["entity_id"]))
                if index == 0:
                    first_gate_state = entity_state
                status["gate_entities"] = [
                    {
                        **row,
                        "state": entity_state if row["entity_id"] == entity["entity_id"] else row.get("state"),
                    }
                    for row in status["gate_entities"]
                ]
            if gate_entities:
                status["current_gate_state"] = first_gate_state or GateState.UNKNOWN.value
            status["front_door_state"] = await self._door_state(FRONT_DOOR_ENTITY_ID)
            status["back_door_state"] = await self._door_state(BACK_DOOR_ENTITY_ID)
            for entity in garage_door_entities:
                entity_state = await self._cover_state(str(entity["entity_id"]))
                status["garage_door_entities"] = [
                    {
                        **row,
                        "state": entity_state if row["entity_id"] == entity["entity_id"] else row.get("state"),
                    }
                    for row in status["garage_door_entities"]
                ]
        return status

    async def _door_state(self, entity_id: str) -> str:
        try:
            state = await self._client.get_state(entity_id)
        except Exception as exc:
            logger.warning("home_assistant_door_state_failed", extra={"entity_id": entity_id, "error": str(exc)})
            return GateState.UNKNOWN.value
        return map_home_assistant_gate_state(state.state).value

    async def _cover_state(self, entity_id: str) -> str:
        try:
            state = await self._client.get_state(entity_id)
        except Exception as exc:
            logger.warning("home_assistant_cover_state_failed", extra={"entity_id": entity_id, "error": str(exc)})
            return GateState.UNKNOWN.value
        return normalize_cover_state(state.state)

    async def _listen(self) -> None:
        async for message in self._client.subscribe_state_changed():
            if message.get("type") != "event":
                continue

            event = message.get("event", {})
            data = event.get("data", {})
            entity_id = data.get("entity_id")
            new_state = data.get("new_state") or {}
            state_value = new_state.get("state")
            if not entity_id or state_value is None:
                continue

            config = await get_runtime_config()
            gate_entities = normalize_cover_entities(
                config.home_assistant_gate_entities,
                default_open_service=config.home_assistant_gate_open_service,
            ) or legacy_gate_entities(config.home_assistant_gate_entity_id, config.home_assistant_gate_open_service)
            gate_entity_map = {str(entity["entity_id"]): entity for entity in gate_entities}
            garage_entity_map = {
                str(entity["entity_id"]): entity
                for entity in normalize_cover_entities(
                    config.home_assistant_garage_door_entities,
                    default_open_service=config.home_assistant_gate_open_service,
                )
            }
            if entity_id in gate_entity_map:
                await self._sync_gate_state(state_value, entity_id, str(gate_entity_map[entity_id].get("name") or entity_id))
            if entity_id in garage_entity_map:
                await self._sync_cover_state(
                    state_value,
                    entity_id,
                    "garage_door",
                    str(garage_entity_map[entity_id].get("name") or entity_id),
                )
            if entity_id in DOOR_ENTITY_IDS:
                await self._sync_door_state(state_value, entity_id)
            if entity_id.startswith("person.") or entity_id in set(config.home_assistant_presence_entities.values()):
                await self._sync_presence_state(entity_id, state_value, config.home_assistant_presence_entities)

    async def _sync_gate_state(self, state_value: str, entity_id: str, name: str | None = None) -> None:
        self._last_gate_state = map_home_assistant_gate_state(state_value)
        await event_bus.publish(
            "gate.state_changed",
            {
                "source": "home_assistant",
                "entity_id": entity_id,
                "name": name,
                "state": self._last_gate_state.value,
                "raw_state": state_value,
            },
        )

    async def _sync_door_state(self, state_value: str, entity_id: str) -> None:
        door_state = map_home_assistant_gate_state(state_value)
        await event_bus.publish(
            "door.state_changed",
            {
                "source": "home_assistant",
                "entity_id": entity_id,
                "door": DOOR_ENTITY_IDS[entity_id],
                "state": door_state.value,
                "raw_state": state_value,
            },
        )

    async def _sync_cover_state(self, state_value: str, entity_id: str, cover_type: str, name: str) -> None:
        cover_state = normalize_cover_state(state_value)
        await event_bus.publish(
            "door.state_changed",
            {
                "source": "home_assistant",
                "entity_id": entity_id,
                "name": name,
                "door": cover_type,
                "state": cover_state,
                "raw_state": state_value,
            },
        )

    async def _sync_presence_state(
        self,
        entity_id: str,
        state_value: str,
        presence_entities: dict[str, str],
    ) -> None:
        legacy_person_name = self._person_name_for_entity(entity_id, presence_entities)

        presence_state = self._map_presence_state(state_value)
        if not presence_state:
            return

        async with AsyncSessionLocal() as session:
            person = await session.scalar(
                select(Person).where(Person.home_assistant_presence_entity_id == entity_id)
            )
            if not person and legacy_person_name:
                person = await session.scalar(select(Person).where(Person.display_name == legacy_person_name))
            if not person:
                if legacy_person_name:
                    logger.warning(
                        "home_assistant_presence_person_missing",
                        extra={"person_name": legacy_person_name, "entity_id": entity_id},
                    )
                return

            person_name = person.display_name
            presence = await session.get(Presence, person.id)
            if not presence:
                presence = Presence(person_id=person.id)
                session.add(presence)

            presence.state = presence_state
            await session.commit()

        await event_bus.publish(
            "presence.state_changed",
            {
                "source": "home_assistant",
                "entity_id": entity_id,
                "person": person_name,
                "state": presence_state.value,
            },
        )

    def _person_name_for_entity(self, entity_id: str, presence_entities: dict[str, str]) -> str | None:
        for person_name, configured_entity_id in presence_entities.items():
            if configured_entity_id == entity_id:
                return person_name
        return None

    def _map_presence_state(self, state_value: str) -> PresenceState | None:
        normalized = state_value.lower()
        if normalized in {"home", "on", "present", "detected"}:
            return PresenceState.PRESENT
        if normalized in {"not_home", "off", "away", "clear"}:
            return PresenceState.EXITED
        return None


@lru_cache
def get_home_assistant_service() -> HomeAssistantIntegrationService:
    return HomeAssistantIntegrationService()
