import asyncio
from functools import lru_cache

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import Person, Presence
from app.models.enums import PresenceState
from app.modules.gate.base import GateState
from app.modules.gate.home_assistant import HomeAssistantGateController, map_home_assistant_gate_state
from app.modules.home_assistant.client import HomeAssistantClient
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config

logger = get_logger(__name__)


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
        status = {
            "configured": bool(config.home_assistant_url and config.home_assistant_token),
            "gate_entity_id": config.home_assistant_gate_entity_id,
            "default_media_player": config.home_assistant_default_media_player,
            "presence_entities": config.home_assistant_presence_entities,
            "last_gate_state": self._last_gate_state.value,
        }
        if status["configured"] and config.home_assistant_gate_entity_id:
            status["current_gate_state"] = (
                await HomeAssistantGateController(self._client).current_state()
            ).value
        return status

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
            if entity_id == config.home_assistant_gate_entity_id:
                await self._sync_gate_state(state_value, entity_id)
            await self._sync_presence_state(entity_id, state_value, config.home_assistant_presence_entities)

    async def _sync_gate_state(self, state_value: str, entity_id: str) -> None:
        self._last_gate_state = map_home_assistant_gate_state(state_value)
        await event_bus.publish(
            "gate.state_changed",
            {
                "source": "home_assistant",
                "entity_id": entity_id,
                "state": self._last_gate_state.value,
                "raw_state": state_value,
            },
        )

    async def _sync_presence_state(
        self,
        entity_id: str,
        state_value: str,
        presence_entities: dict[str, str],
    ) -> None:
        person_name = self._person_name_for_entity(entity_id, presence_entities)
        if not person_name:
            return

        presence_state = self._map_presence_state(state_value)
        if not presence_state:
            return

        async with AsyncSessionLocal() as session:
            person = await session.scalar(select(Person).where(Person.display_name == person_name))
            if not person:
                logger.warning(
                    "home_assistant_presence_person_missing",
                    extra={"person_name": person_name, "entity_id": entity_id},
                )
                return

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
