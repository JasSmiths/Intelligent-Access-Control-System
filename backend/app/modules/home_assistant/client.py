import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

import httpx
import websockets

from app.core.logging import get_logger
from app.services.settings import RuntimeConfig, get_runtime_config

logger = get_logger(__name__)


def home_assistant_connection_fingerprint(config: Any) -> str:
    """Opaque provenance for the actual authenticated socket configuration."""
    material = [str(config.home_assistant_url).rstrip("/"), str(config.home_assistant_token)]
    return hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()


class HomeAssistantError(RuntimeError):
    """Raised when Home Assistant rejects or cannot complete a request."""

    def __init__(
        self, message: str, *, delivery: Literal["not_sent", "rejected", "accepted", "unknown"] = "unknown"
    ) -> None:
        super().__init__(message)
        self.delivery = delivery


@dataclass(frozen=True)
class HomeAssistantState:
    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_changed: str | None = None
    last_updated: str | None = None


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

    def __init__(self) -> None:
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_signature: tuple[str, str] | None = None
        self._http_client_lock = asyncio.Lock()

    async def config(self) -> RuntimeConfig:
        return await get_runtime_config()

    async def close(self) -> None:
        async with self._http_client_lock:
            client = self._http_client
            self._http_client = None
            self._http_client_signature = None
        if client is not None:
            await client.aclose()

    async def call_service(self, service_name: str, service_data: dict[str, Any], *,
                           runtime_config: RuntimeConfig | None = None) -> dict[str, Any]:
        domain, service = self._split_service_name(service_name)
        return await self._request("POST", f"/api/services/{domain}/{service}", json=service_data, runtime_config=runtime_config)

    async def get_state(self, entity_id: str, *, runtime_config: RuntimeConfig | None = None) -> HomeAssistantState:
        data = await self._request("GET", f"/api/states/{entity_id}", runtime_config=runtime_config)
        return HomeAssistantState(
            entity_id=data["entity_id"],
            state=data["state"],
            attributes=data.get("attributes", {}),
            last_changed=data.get("last_changed"),
            last_updated=data.get("last_updated"),
        )

    async def list_states(self, *, runtime_config: RuntimeConfig | None = None) -> list[HomeAssistantState]:
        data = await self._request("GET", "/api/states", runtime_config=runtime_config)
        if not isinstance(data, list):
            raise HomeAssistantError("Home Assistant returned an unexpected states payload.")
        return [
            HomeAssistantState(
                entity_id=item["entity_id"],
                state=item.get("state", "unknown"),
                attributes=item.get("attributes", {}),
                last_changed=item.get("last_changed"),
                last_updated=item.get("last_updated"),
            )
            for item in data
            if isinstance(item, dict) and item.get("entity_id")
        ]

    async def list_services(self, *, runtime_config: RuntimeConfig | None = None) -> list[HomeAssistantService]:
        data = await self._request("GET", "/api/services", runtime_config=runtime_config)
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

    async def normalize_mobile_app_service_names(
        self, names: list[str], *, runtime_config: RuntimeConfig | None = None,
    ) -> list[str]:
        """Ask the installed HA slugify filter using a constant read-only template.

        Names are variables, never interpolated template code. This uses the
        server's actual Unicode normalization without adding a local approximation.
        """
        if len(names) > 1000 or any(
            not isinstance(name, str) or not name or len(name) > 255 for name in names
        ):
            raise HomeAssistantError("Home Assistant device name normalization is unavailable.", delivery="not_sent")
        if not names:
            return []
        try:
            values = await self._request("POST", "/api/template", json={
                "template": "{{ names | map('slugify') | list | to_json }}",
                "variables": {"names": [f"mobile_app_{name}" for name in names]},
            }, runtime_config=runtime_config)
            if not isinstance(values, list) or len(values) != len(names) or any(
                not isinstance(value, str) or len(value) > 255
                or re.fullmatch(r"mobile_app_[a-z0-9_]+", value) is None for value in values
            ):
                raise ValueError("Invalid normalized names")
            return [f"notify.{value}" for value in values]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise HomeAssistantError("Home Assistant device name normalization is unavailable.", delivery="not_sent") from exc

    async def list_recovery_registries(
        self, *, runtime_config: RuntimeConfig | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """One authenticated, bounded, read-only socket; never subscribe or retry.

        Registry metadata stays within discovery and is never logged or returned
        directly by the API. A failure invalidates both registry lists.
        """
        config = runtime_config if runtime_config is not None else await self.config()
        if not (config.home_assistant_url and config.home_assistant_token):
            raise HomeAssistantError("Home Assistant registry discovery is unavailable.", delivery="not_sent")
        try:
            async with asyncio.timeout(8):
                async with websockets.connect(
                    self._websocket_url(config.home_assistant_url),
                    proxy=None, open_timeout=5, close_timeout=1,
                    max_size=4 * 1024 * 1024, max_queue=4,
                ) as websocket:
                    greeting = json.loads(await websocket.recv())
                    if not isinstance(greeting, dict) or greeting.get("type") != "auth_required":
                        raise ValueError("Unexpected authentication greeting")
                    await websocket.send(json.dumps({"type": "auth", "access_token": config.home_assistant_token}))
                    authenticated = json.loads(await websocket.recv())
                    if not isinstance(authenticated, dict) or authenticated.get("type") != "auth_ok":
                        raise ValueError("Authentication refused")
                    results = []
                    for request_id, command in enumerate((
                        "config/entity_registry/list", "config/device_registry/list",
                    ), start=1):
                        await websocket.send(json.dumps({"id": request_id, "type": command}))
                        response = json.loads(await websocket.recv())
                        if not isinstance(response, dict) or (
                            response.get("type") != "result" or response.get("id") != request_id
                            or response.get("success") is not True
                        ):
                            raise ValueError("Registry request failed")
                        rows = response.get("result")
                        if not isinstance(rows, list) or len(rows) > 20000 or any(
                            not isinstance(row, dict) for row in rows
                        ):
                            raise ValueError("Invalid registry result")
                        results.append(rows)
                    return results[0], results[1]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Deliberately discard vendor diagnostics (which can contain secrets).
            raise HomeAssistantError("Home Assistant registry discovery is unavailable.", delivery="not_sent") from exc

    async def subscribe_state_changed(self) -> AsyncIterator[dict[str, Any]]:
        config = await self.config()
        if not (config.home_assistant_url and config.home_assistant_token):
            logger.info("home_assistant_listener_not_configured")
            return

        event_id = 1
        while True:
            try:
                config = await self.config()
                async with websockets.connect(
                    self._websocket_url(config.home_assistant_url),
                    ping_interval=30,
                    proxy=None,
                ) as websocket:
                    auth_required = await websocket.recv()
                    logger.debug("home_assistant_ws_auth_required", extra={"payload": auth_required})
                    await websocket.send(json.dumps({"type": "auth", "access_token": config.home_assistant_token}))
                    auth_response = await websocket.recv()
                    if '"auth_ok"' not in auth_response:
                        raise HomeAssistantError("Home Assistant WebSocket authentication failed.")

                    # Explicit vendor lifecycle fences cached journey evidence even
                    # though this generator reconnects internally.
                    yield {"type": "iacs_connection", "connected": True,
                           "configuration_fingerprint": home_assistant_connection_fingerprint(config)}
                    for event_type in ("state_changed", "mobile_app_notification_action"):
                        await websocket.send(
                            json.dumps(
                                {
                                    "id": event_id,
                                    "type": "subscribe_events",
                                    "event_type": event_type,
                                }
                            )
                        )
                        event_id += 1

                    async for message in websocket:
                        yield json.loads(message)
                    yield {"type": "iacs_connection", "connected": False}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                yield {"type": "iacs_connection", "connected": False}
                logger.warning("home_assistant_ws_reconnect", extra={"error": str(exc)})
                await asyncio.sleep(5)

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None,
        runtime_config: RuntimeConfig | None = None,
    ) -> dict[str, Any]:
        config = runtime_config if runtime_config is not None else await self.config()
        base_url = config.home_assistant_url.rstrip("/")
        token = config.home_assistant_token
        if not (base_url and token):
            raise HomeAssistantError("Home Assistant URL/token are not configured.", delivery="not_sent")

        try:
            client = await self._request_client(base_url=base_url, token=token)
        except Exception as exc:
            raise HomeAssistantError("Home Assistant request client is unavailable.", delivery="not_sent") from exc

        try:
            response = await client.request(
                method,
                f"{base_url}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=json,
            )
        except httpx.RequestError as exc:
            raise HomeAssistantError(
                f"Unable to reach Home Assistant: {exc}",
                delivery="not_sent" if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)) else "unknown",
            ) from exc

        if not 200 <= response.status_code < 300:
            raise HomeAssistantError(
                f"Home Assistant returned {response.status_code}: {response.text[:300]}",
                # Only authentication/routing/method refusal proves non-execution.
                # In particular 408, 429 and 5xx do not prove a command was rejected.
                delivery="rejected" if response.status_code in {401, 403, 404, 405} else "unknown",
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise HomeAssistantError("Home Assistant returned an invalid JSON receipt.", delivery="accepted") from exc

    async def _request_client(self, *, base_url: str, token: str) -> httpx.AsyncClient:
        signature = (base_url, token)
        async with self._http_client_lock:
            if self._http_client is not None and self._http_client_signature == signature:
                return self._http_client

            old_client = self._http_client
            new_client = httpx.AsyncClient(timeout=15, trust_env=False)
            self._http_client = new_client
            self._http_client_signature = signature

        if old_client is not None:
            await old_client.aclose()
        return new_client

    def _websocket_url(self, base_url: str) -> str:
        parsed = urlparse(base_url.rstrip("/"))
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, "/api/websocket", "", "", ""))

    def _split_service_name(self, service_name: str) -> tuple[str, str]:
        if "." not in service_name:
            raise HomeAssistantError(f"Invalid Home Assistant service name: {service_name}", delivery="not_sent")
        domain, service = service_name.split(".", 1)
        return domain, service


@lru_cache
def get_home_assistant_client() -> HomeAssistantClient:
    return HomeAssistantClient()
