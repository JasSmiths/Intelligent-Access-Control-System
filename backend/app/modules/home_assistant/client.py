import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets

from app.core.logging import get_logger
from app.services.settings import RuntimeConfig, get_runtime_config

logger = get_logger(__name__)


class HomeAssistantError(RuntimeError):
    """Raised when Home Assistant rejects or cannot complete a request."""


@dataclass(frozen=True)
class HomeAssistantState:
    entity_id: str
    state: str
    attributes: dict[str, Any]


@dataclass(frozen=True)
class HomeAssistantService:
    service_id: str
    domain: str
    service: str
    name: str | None
    description: str | None


class HomeAssistantClient:
    """Small async Home Assistant REST/WebSocket client.

    This module is the single vendor boundary for Home Assistant. Gate control,
    TTS, and state listeners use this client so token handling and URL behavior
    stay consistent across the system.
    """

    async def config(self) -> RuntimeConfig:
        return await get_runtime_config()

    async def call_service(self, service_name: str, service_data: dict[str, Any]) -> dict[str, Any]:
        domain, service = self._split_service_name(service_name)
        return await self._request("POST", f"/api/services/{domain}/{service}", json=service_data)

    async def get_state(self, entity_id: str) -> HomeAssistantState:
        data = await self._request("GET", f"/api/states/{entity_id}")
        return HomeAssistantState(
            entity_id=data["entity_id"],
            state=data["state"],
            attributes=data.get("attributes", {}),
        )

    async def list_states(self) -> list[HomeAssistantState]:
        data = await self._request("GET", "/api/states")
        if not isinstance(data, list):
            raise HomeAssistantError("Home Assistant returned an unexpected states payload.")
        return [
            HomeAssistantState(
                entity_id=item["entity_id"],
                state=item.get("state", "unknown"),
                attributes=item.get("attributes", {}),
            )
            for item in data
            if isinstance(item, dict) and item.get("entity_id")
        ]

    async def list_services(self) -> list[HomeAssistantService]:
        data = await self._request("GET", "/api/services")
        if not isinstance(data, list):
            raise HomeAssistantError("Home Assistant returned an unexpected services payload.")

        services: list[HomeAssistantService] = []
        for domain_payload in data:
            if not isinstance(domain_payload, dict):
                continue
            domain = str(domain_payload.get("domain") or "").strip()
            service_payloads = domain_payload.get("services")
            if not domain or not isinstance(service_payloads, dict):
                continue
            for service, details in service_payloads.items():
                service_name = str(service or "").strip()
                if not service_name:
                    continue
                detail_map = details if isinstance(details, dict) else {}
                services.append(
                    HomeAssistantService(
                        service_id=f"{domain}.{service_name}",
                        domain=domain,
                        service=service_name,
                        name=str(detail_map.get("name")) if detail_map.get("name") else None,
                        description=str(detail_map.get("description")) if detail_map.get("description") else None,
                    )
                )
        return services

    async def subscribe_state_changed(self) -> AsyncIterator[dict[str, Any]]:
        config = await self.config()
        if not (config.home_assistant_url and config.home_assistant_token):
            logger.info("home_assistant_listener_not_configured")
            return

        event_id = 1
        while True:
            try:
                config = await self.config()
                async with websockets.connect(self._websocket_url(config.home_assistant_url), ping_interval=30) as websocket:
                    auth_required = await websocket.recv()
                    logger.debug("home_assistant_ws_auth_required", extra={"payload": auth_required})
                    await websocket.send(json.dumps({"type": "auth", "access_token": config.home_assistant_token}))
                    auth_response = await websocket.recv()
                    if '"auth_ok"' not in auth_response:
                        raise HomeAssistantError("Home Assistant WebSocket authentication failed.")

                    await websocket.send(
                        json.dumps(
                            {
                                "id": event_id,
                                "type": "subscribe_events",
                                "event_type": "state_changed",
                            }
                        )
                    )
                    event_id += 1

                    async for message in websocket:
                        yield json.loads(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("home_assistant_ws_reconnect", extra={"error": str(exc)})
                await asyncio.sleep(5)

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        config = await self.config()
        base_url = config.home_assistant_url.rstrip("/")
        token = config.home_assistant_token
        if not (base_url and token):
            raise HomeAssistantError("Home Assistant URL/token are not configured.")

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(
                method,
                f"{base_url}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=json,
            )

        if response.status_code >= 400:
            raise HomeAssistantError(
                f"Home Assistant returned {response.status_code}: {response.text[:300]}"
            )
        if not response.content:
            return {}
        return response.json()

    def _websocket_url(self, base_url: str) -> str:
        parsed = urlparse(base_url.rstrip("/"))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, "/api/websocket", "", "", ""))

    def _split_service_name(self, service_name: str) -> tuple[str, str]:
        if "." not in service_name:
            raise HomeAssistantError(f"Invalid Home Assistant service name: {service_name}")
        domain, service = service_name.split(".", 1)
        return domain, service
