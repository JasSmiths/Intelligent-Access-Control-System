import asyncio
from datetime import UTC, datetime
from functools import lru_cache
from time import monotonic

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import GateStateObservation
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
from app.services.maintenance import MAINTENANCE_HA_ENTITY_ID, set_mode
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, emit_audit_log

logger = get_logger(__name__)

FRONT_DOOR_ENTITY_ID = "binary_sensor.front_door"
BACK_DOOR_ENTITY_ID = "binary_sensor.back_door"
MAIN_GARAGE_DOOR_ENTITY_ID = "cover.main_garage_door"
MUMS_GARAGE_DOOR_ENTITY_ID = "cover.mums_garage_door"
DOOR_ENTITY_IDS = {
    FRONT_DOOR_ENTITY_ID: "front_door",
    BACK_DOOR_ENTITY_ID: "back_door",
}
STATE_REFRESH_MIN_INTERVAL_SECONDS = 30.0


class HomeAssistantIntegrationService:
    """Keeps Home Assistant state synchronized with IACS realtime state."""

    def __init__(self, client: HomeAssistantClient | None = None) -> None:
        self._client = client or HomeAssistantClient()
        self._listener: asyncio.Task | None = None
        self._state_refresh_task: asyncio.Task | None = None
        self._state_refresh_lock = asyncio.Lock()
        self._state_cache: dict[str, dict[str, str]] = {}
        self._last_state_refresh_at = 0.0
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
        self._state_refresh_task = asyncio.create_task(
            self._refresh_configured_states(force=True),
            name="home-assistant-state-bootstrap",
        )
        logger.info("home_assistant_listener_started")

    async def stop(self) -> None:
        for task in (self._listener, self._state_refresh_task):
            if not task:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._listener = None
        self._state_refresh_task = None
        logger.info("home_assistant_listener_stopped")

    async def status(self, *, refresh: bool = False) -> dict:
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
            "maintenance_mode_entity_id": MAINTENANCE_HA_ENTITY_ID,
            "maintenance_mode_state": self._state_cache.get(MAINTENANCE_HA_ENTITY_ID, {}).get("state"),
            "last_gate_state": self._last_gate_state.value,
            "state_source": "home_assistant_websocket_cache",
        }
        if status["configured"]:
            watched_entity_ids = self._configured_state_entity_ids(gate_entities, garage_door_entities)
            if refresh or any(entity_id not in self._state_cache for entity_id in watched_entity_ids):
                await self._refresh_configured_states(
                    gate_entities=gate_entities,
                    garage_door_entities=garage_door_entities,
                    force=refresh,
                )

            first_gate_state: str | None = None
            first_gate_state_changed_at: str | None = None
            for index, entity in enumerate(gate_entities):
                entity_id = str(entity["entity_id"])
                entity_state = self._cached_cover_state(entity_id)
                entity_state_changed_at = self._state_cache.get(entity_id, {}).get("last_changed") or None
                if index == 0:
                    first_gate_state = entity_state
                    first_gate_state_changed_at = entity_state_changed_at
                status["gate_entities"] = [
                    {
                        **row,
                        "state": entity_state if row["entity_id"] == entity["entity_id"] else row.get("state"),
                        "state_changed_at": (
                            entity_state_changed_at
                            if row["entity_id"] == entity["entity_id"]
                            else row.get("state_changed_at")
                        ),
                    }
                    for row in status["gate_entities"]
                ]
            if gate_entities:
                status["current_gate_state"] = first_gate_state or GateState.UNKNOWN.value
                status["current_gate_state_changed_at"] = first_gate_state_changed_at
            status["front_door_state"] = self._cached_gate_state(FRONT_DOOR_ENTITY_ID)
            status["back_door_state"] = self._cached_gate_state(BACK_DOOR_ENTITY_ID)
            for entity in garage_door_entities:
                entity_state = self._cached_cover_state(str(entity["entity_id"]))
                status["garage_door_entities"] = [
                    {
                        **row,
                        "state": entity_state if row["entity_id"] == entity["entity_id"] else row.get("state"),
                    }
                    for row in status["garage_door_entities"]
                ]
            refreshed_at = self._latest_cached_state_timestamp(watched_entity_ids)
            if refreshed_at:
                status["state_refreshed_at"] = refreshed_at
        return status

    async def _listen(self) -> None:
        async for message in self._client.subscribe_state_changed():
            if message.get("type") != "event":
                continue

            event = message.get("event", {})
            event_type = str(event.get("event_type") or "")
            if event_type == "mobile_app_notification_action":
                await self._handle_mobile_notification_action(event)
                continue
            if event_type and event_type != "state_changed":
                continue
            data = event.get("data", {})
            entity_id = data.get("entity_id")
            new_state = data.get("new_state") or {}
            state_value = new_state.get("state")
            if not entity_id or state_value is None:
                continue

            previous_gate_state = self._cached_gate_state(str(entity_id))
            last_changed = str(new_state.get("last_changed") or "") or None
            last_updated = str(new_state.get("last_updated") or "") or None
            self._remember_state(
                str(entity_id),
                str(state_value),
                last_changed=last_changed,
                last_updated=last_updated,
            )
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
                await self._sync_gate_state(
                    state_value,
                    entity_id,
                    str(gate_entity_map[entity_id].get("name") or entity_id),
                    previous_state=previous_gate_state,
                    last_changed=last_changed,
                    source="home_assistant_websocket",
                )
            if entity_id in garage_entity_map:
                await self._sync_cover_state(
                    state_value,
                    entity_id,
                    "garage_door",
                    str(garage_entity_map[entity_id].get("name") or entity_id),
                )
            if entity_id in DOOR_ENTITY_IDS:
                await self._sync_door_state(state_value, entity_id)
            if entity_id == MAINTENANCE_HA_ENTITY_ID:
                await self._sync_maintenance_mode_state(state_value)

    async def _handle_mobile_notification_action(self, event: dict) -> None:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        action_id = str(data.get("action") or "").strip()
        if not action_id:
            return
        from app.services.whatsapp_messaging import (
            get_whatsapp_messaging_service,
            parse_visitor_pass_timeframe_button_id,
        )

        decision = parse_visitor_pass_timeframe_button_id(action_id)
        if not decision:
            return
        try:
            result = await get_whatsapp_messaging_service().decide_visitor_timeframe_request(
                decision.pass_id,
                decision.request_id,
                decision.decision,
                actor_label="Home Assistant Notification",
            )
            logger.info(
                "home_assistant_notification_action_processed",
                extra={
                    "action": action_id,
                    "decision": decision.decision,
                    "visitor_pass_id": decision.pass_id,
                    "request_id": decision.request_id,
                    "admin_message": result.get("admin_message"),
                },
            )
            await event_bus.publish(
                "home_assistant.notification_action_processed",
                {
                    "action": action_id,
                    "decision": decision.decision,
                    "visitor_pass_id": decision.pass_id,
                    "request_id": decision.request_id,
                },
            )
        except Exception as exc:
            logger.warning(
                "home_assistant_notification_action_failed",
                extra={
                    "action": action_id,
                    "decision": decision.decision,
                    "visitor_pass_id": decision.pass_id,
                    "request_id": decision.request_id,
                    "error": str(exc),
                },
            )

    async def _sync_gate_state(
        self,
        state_value: str,
        entity_id: str,
        name: str | None = None,
        *,
        previous_state: str | None = None,
        last_changed: str | None = None,
        source: str = "home_assistant",
    ) -> None:
        self._remember_state(entity_id, state_value, last_changed=last_changed)
        previous_state = previous_state or self._last_gate_state.value
        self._last_gate_state = map_home_assistant_gate_state(state_value)
        if previous_state != self._last_gate_state.value:
            await self._record_gate_state_observation(
                entity_id,
                name,
                state_value,
                previous_state=previous_state,
                last_changed=last_changed,
                source=source,
            )
            emit_audit_log(
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="gate.state_changed",
                actor="System",
                target_entity="Gate",
                target_id=entity_id,
                target_label=name,
                diff={"old": {"state": previous_state}, "new": {"state": self._last_gate_state.value}},
                metadata={"raw_state": state_value, "source": "home_assistant"},
            )
        await event_bus.publish(
            "gate.state_changed",
            {
                "source": "home_assistant",
                "entity_id": entity_id,
                "name": name,
                "state": self._last_gate_state.value,
                "raw_state": state_value,
                "previous_state": previous_state,
                "state_changed_at": last_changed,
            },
        )

    async def _sync_door_state(self, state_value: str, entity_id: str) -> None:
        self._remember_state(entity_id, state_value)
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
        self._remember_state(entity_id, state_value)
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

    async def _sync_maintenance_mode_state(self, state_value: str) -> None:
        self._remember_state(MAINTENANCE_HA_ENTITY_ID, state_value)
        normalized = state_value.strip().lower()
        if normalized not in {"on", "off"}:
            return
        await set_mode(
            normalized == "on",
            actor="Home Assistant Sync",
            source="Home Assistant Sync",
            reason="Synced from Home Assistant",
            sync_ha=False,
        )

    async def _refresh_configured_states(
        self,
        *,
        gate_entities: list[dict] | None = None,
        garage_door_entities: list[dict] | None = None,
        force: bool = False,
    ) -> None:
        async with self._state_refresh_lock:
            now = monotonic()
            if not force and now - self._last_state_refresh_at < STATE_REFRESH_MIN_INTERVAL_SECONDS:
                return

            if gate_entities is None or garage_door_entities is None:
                config = await get_runtime_config()
                gate_entities = normalize_cover_entities(
                    config.home_assistant_gate_entities,
                    default_open_service=config.home_assistant_gate_open_service,
                ) or legacy_gate_entities(config.home_assistant_gate_entity_id, config.home_assistant_gate_open_service)
                garage_door_entities = normalize_cover_entities(
                    config.home_assistant_garage_door_entities,
                    default_open_service=config.home_assistant_gate_open_service,
                )

            entity_ids = self._configured_state_entity_ids(gate_entities, garage_door_entities)
            if not entity_ids:
                self._last_state_refresh_at = now
                return

            await asyncio.gather(*(self._refresh_entity_state(entity_id) for entity_id in entity_ids))
            if gate_entities:
                first_gate_state = self._state_cache.get(str(gate_entities[0]["entity_id"]), {}).get("state")
                if first_gate_state:
                    self._last_gate_state = map_home_assistant_gate_state(first_gate_state)
                for entity in gate_entities:
                    entity_id = str(entity["entity_id"])
                    cached = self._state_cache.get(entity_id, {})
                    if cached.get("state"):
                        await self._record_gate_state_observation(
                            entity_id,
                            str(entity.get("name") or entity_id),
                            str(cached["state"]),
                            previous_state=None,
                            last_changed=cached.get("last_changed") or None,
                            source="home_assistant_refresh",
                        )
            maintenance_state = self._state_cache.get(MAINTENANCE_HA_ENTITY_ID, {}).get("state")
            if maintenance_state:
                await self._sync_maintenance_mode_state(maintenance_state)
            self._last_state_refresh_at = now

    async def _refresh_entity_state(self, entity_id: str) -> None:
        try:
            state = await self._client.get_state(entity_id)
        except Exception as exc:
            logger.warning("home_assistant_state_refresh_failed", extra={"entity_id": entity_id, "error": str(exc)})
            return
        self._remember_state(
            entity_id,
            state.state,
            last_changed=state.last_changed,
            last_updated=state.last_updated,
        )

    def _configured_state_entity_ids(
        self,
        gate_entities: list[dict],
        garage_door_entities: list[dict],
    ) -> list[str]:
        entity_ids = [
            *(str(entity["entity_id"]) for entity in gate_entities if entity.get("entity_id")),
            FRONT_DOOR_ENTITY_ID,
            BACK_DOOR_ENTITY_ID,
            MAINTENANCE_HA_ENTITY_ID,
            *(str(entity["entity_id"]) for entity in garage_door_entities if entity.get("entity_id")),
        ]
        return list(dict.fromkeys(entity_ids))

    def _remember_state(
        self,
        entity_id: str,
        state: str,
        *,
        last_changed: str | None = None,
        last_updated: str | None = None,
    ) -> None:
        previous = self._state_cache.get(entity_id, {})
        self._state_cache[entity_id] = {
            "state": state,
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "last_changed": last_changed or previous.get("last_changed") or "",
            "last_updated": last_updated or previous.get("last_updated") or "",
        }

    def _cached_cover_state(self, entity_id: str) -> str:
        state = self._state_cache.get(entity_id, {}).get("state")
        return normalize_cover_state(state) if state else GateState.UNKNOWN.value

    def _cached_gate_state(self, entity_id: str) -> str:
        state = self._state_cache.get(entity_id, {}).get("state")
        return map_home_assistant_gate_state(state).value if state else GateState.UNKNOWN.value

    def _latest_cached_state_timestamp(self, entity_ids: list[str]) -> str | None:
        timestamps = [
            cached["updated_at"]
            for entity_id in entity_ids
            if (cached := self._state_cache.get(entity_id)) and cached.get("updated_at")
        ]
        return max(timestamps) if timestamps else None

    async def _record_gate_state_observation(
        self,
        entity_id: str,
        name: str | None,
        raw_state: str,
        *,
        previous_state: str | None,
        last_changed: str | None,
        source: str,
    ) -> None:
        state = map_home_assistant_gate_state(raw_state).value
        changed_at = self._parse_datetime(last_changed)
        async with AsyncSessionLocal() as session:
            if changed_at:
                existing = await session.scalar(
                    select(GateStateObservation.id).where(
                        GateStateObservation.gate_entity_id == entity_id,
                        GateStateObservation.state == state,
                        GateStateObservation.state_changed_at == changed_at,
                    )
                )
                if existing:
                    return
            else:
                latest = await session.scalar(
                    select(GateStateObservation)
                    .where(GateStateObservation.gate_entity_id == entity_id)
                    .order_by(GateStateObservation.observed_at.desc())
                    .limit(1)
                )
                if latest and latest.state == state and latest.previous_state == previous_state:
                    return
            session.add(
                GateStateObservation(
                    gate_entity_id=entity_id,
                    gate_name=name,
                    state=state,
                    raw_state=raw_state,
                    previous_state=previous_state,
                    observed_at=datetime.now(tz=UTC),
                    state_changed_at=changed_at,
                    source=source,
                )
            )
            await session.commit()

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@lru_cache
def get_home_assistant_service() -> HomeAssistantIntegrationService:
    return HomeAssistantIntegrationService()
