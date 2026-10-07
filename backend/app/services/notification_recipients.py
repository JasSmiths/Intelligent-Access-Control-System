"""Notification recipient selection and discovery, independent of delivery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.models import Person
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.notifications.apprise_client import normalize_apprise_url, split_apprise_urls
from app.modules.notifications.base import NotificationDeliveryError
from app.services.settings import RuntimeConfig
from app.services.workflow_session import SessionFactory
from app.services.workflows.context import normalize_string_list

logger = get_logger(__name__)


class NotificationRecipients:
    def __init__(
        self, *, sessions: SessionFactory, clients: Callable[[], HomeAssistantClient]
    ) -> None:
        self.sessions = sessions
        self.clients = clients

    def select_apprise_urls(self, configured: str, action: dict[str, Any]) -> list[str]:
        urls = [normalize_apprise_url(url) for url in split_apprise_urls(configured)]
        if not urls:
            return []
        target_mode = str(action.get("target_mode") or "all")
        endpoint_ids = normalize_string_list(action.get("target_ids"), allow_scalar=False)
        if target_mode == "all" or "apprise:*" in endpoint_ids or not endpoint_ids:
            return urls
        chosen: list[str] = []
        for endpoint_id in endpoint_ids:
            if not endpoint_id.startswith("apprise:"):
                continue
            try:
                index = int(endpoint_id.split(":", 1)[1])
            except ValueError:
                continue
            if 0 <= index < len(urls):
                chosen.append(urls[index])
        return chosen

    async def select_home_assistant_mobile_targets(
        self, config: RuntimeConfig, action: dict[str, Any]
    ) -> list[str]:
        target_mode = str(action.get("target_mode") or "all")
        endpoint_ids = normalize_string_list(action.get("target_ids"), allow_scalar=False)
        if target_mode == "all" or "home_assistant_mobile:*" in endpoint_ids:
            return await self.all_home_assistant_mobile_targets(config)

        targets: list[str] = []
        for endpoint_id in endpoint_ids:
            if not endpoint_id.startswith("home_assistant_mobile:"):
                continue
            target = endpoint_id.split(":", 1)[1]
            if target and target != "*":
                if not target.startswith("notify.mobile_app_"):
                    raise NotificationDeliveryError(
                        "Home Assistant mobile targets must be notify.mobile_app_* services."
                    )
                targets.append(target)
        return list(dict.fromkeys(targets))

    async def select_voice_targets(
        self, config: RuntimeConfig, action: dict[str, Any]
    ) -> list[str]:
        target_mode = str(action.get("target_mode") or "all")
        endpoint_ids = normalize_string_list(action.get("target_ids"), allow_scalar=False)
        if target_mode == "all" or "home_assistant_tts:*" in endpoint_ids:
            targets = await self.all_media_player_targets(config)
            if targets:
                return targets
            return (
                [config.home_assistant_default_media_player]
                if config.home_assistant_default_media_player
                else []
            )

        selected_targets: list[str] = []
        for endpoint_id in endpoint_ids:
            if not endpoint_id.startswith("home_assistant_tts:"):
                continue
            target = endpoint_id.split(":", 1)[1]
            if target == "default":
                target = config.home_assistant_default_media_player
            if target and target != "*":
                selected_targets.append(target)
        if not selected_targets and config.home_assistant_default_media_player:
            selected_targets.append(config.home_assistant_default_media_player)
        return selected_targets

    async def voice_endpoint_catalog(self, config: RuntimeConfig) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        targets = await self.all_media_player_targets(config)
        if targets:
            endpoints.append(
                {
                    "id": "home_assistant_tts:*",
                    "provider": "Home Assistant",
                    "label": "All media players",
                    "detail": f"{len(targets)} media_player entities",
                }
            )
            endpoints.extend(
                {
                    "id": f"home_assistant_tts:{target}",
                    "provider": "Home Assistant",
                    "label": target.split(".", 1)[-1].replace("_", " ").title(),
                    "detail": target,
                }
                for target in targets
            )
        elif config.home_assistant_default_media_player:
            endpoints.append(
                {
                    "id": f"home_assistant_tts:{config.home_assistant_default_media_player}",
                    "provider": "Home Assistant",
                    "label": "Default media player",
                    "detail": config.home_assistant_default_media_player,
                }
            )
        return endpoints

    async def home_assistant_mobile_endpoint_catalog(
        self, config: RuntimeConfig
    ) -> list[dict[str, Any]]:
        targets = await self.all_home_assistant_mobile_targets(config)
        if not targets:
            return []

        endpoints: list[dict[str, Any]] = [
            {
                "id": "home_assistant_mobile:*",
                "provider": "Home Assistant",
                "label": "All Home Assistant mobile apps",
                "detail": f"{len(targets)} notify.mobile_app services",
            }
        ]
        person_labels = await self.home_assistant_mobile_person_labels()
        for target in targets:
            endpoints.append(
                {
                    "id": f"home_assistant_mobile:{target}",
                    "provider": "Home Assistant",
                    "label": person_labels.get(target)
                    or target.split(".", 1)[-1].replace("_", " ").title(),
                    "detail": target,
                }
            )
        return endpoints

    async def home_assistant_mobile_person_labels(self) -> dict[str, str]:
        async with self.sessions() as session:
            people = (
                await session.scalars(
                    select(Person).where(
                        Person.home_assistant_mobile_app_notify_service.is_not(None)
                    )
                )
            ).all()
        return {
            str(person.home_assistant_mobile_app_notify_service): person.display_name
            for person in people
            if person.home_assistant_mobile_app_notify_service
        }

    async def all_home_assistant_mobile_targets(self, config: RuntimeConfig) -> list[str]:
        configured_targets = await self.configured_home_assistant_mobile_targets()
        if not (config.home_assistant_url and config.home_assistant_token):
            return configured_targets
        try:
            services = await self.clients().list_services()
        except Exception as exc:  # noqa: BLE001 - unavailable discovery retains only configured recipients.
            logger.debug("notification_mobile_app_discovery_failed", extra={"error": str(exc)})
            return configured_targets
        discovered_targets = sorted(
            service.service_id
            for service in services
            if service.service_id.startswith("notify.mobile_app_")
        )
        return list(dict.fromkeys([*configured_targets, *discovered_targets]))

    async def configured_home_assistant_mobile_targets(self) -> list[str]:
        async with self.sessions() as session:
            people = (
                await session.scalars(
                    select(Person).where(
                        Person.home_assistant_mobile_app_notify_service.is_not(None)
                    )
                )
            ).all()
        return list(
            dict.fromkeys(
                str(person.home_assistant_mobile_app_notify_service)
                for person in people
                if person.home_assistant_mobile_app_notify_service
            )
        )

    async def all_media_player_targets(self, config: RuntimeConfig) -> list[str]:
        if not (config.home_assistant_url and config.home_assistant_token):
            return []
        try:
            states = await self.clients().list_states()
        except Exception as exc:  # noqa: BLE001 - unavailable discovery retains only configured recipients.
            logger.debug("notification_media_player_discovery_failed", extra={"error": str(exc)})
            return []
        return sorted(
            state.entity_id for state in states if state.entity_id.startswith("media_player.")
        )
