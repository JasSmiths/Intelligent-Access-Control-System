"""Concrete WhatsApp Graph transport; no database, chat, or visitor decisions."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.modules.notifications.base import NotificationDeliveryError

@dataclass(frozen=True)
class WhatsAppIntegrationConfig:
    enabled: bool
    access_token: str
    phone_number_id: str
    business_account_id: str
    webhook_verify_token: str
    app_secret: str
    graph_api_version: str
    visitor_pass_template_name: str
    visitor_pass_template_language: str

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.access_token and self.phone_number_id)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_verify_token)


def normalize_graph_api_version(value: str) -> str:
    version = str(value or "v25.0").strip()
    return "v25.0" if not version else version if version.startswith("v") else f"v{version}"


class WhatsAppSendError(NotificationDeliveryError):
    def __init__(self, message: str, *, delivery: str):
        super().__init__(message)
        self.delivery = delivery


class WhatsAppTransport:
    def __init__(self) -> None:
        self.last_error: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def stop(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def test_connection(self, config: WhatsAppIntegrationConfig) -> None:
        if not config.access_token or not config.phone_number_id:
            raise ValueError("WhatsApp access token and phone number ID are required.")
        client = await self._request_client()
        response = await client.get(self._graph_url(config, config.phone_number_id),
            headers={"Authorization": f"Bearer {config.access_token}"},
            params={"fields": "id,display_phone_number,verified_name"})
        if response.status_code >= 400:
            raise ValueError(f"WhatsApp API test failed with HTTP {response.status_code}: {response.text[:240]}")

    async def send(self, config: WhatsAppIntegrationConfig, payload: dict[str, Any]) -> dict[str, Any]:
        client = await self._request_client()
        response = await client.post(self._graph_url(config, f"{config.phone_number_id}/messages"),
            headers={"Authorization": f"Bearer {config.access_token}", "Content-Type": "application/json"},
            json=payload)
        if response.status_code >= 400:
            self.last_error = f"HTTP {response.status_code}: {response.text[:240]}"
            raise WhatsAppSendError(
                f"WhatsApp API send failed with HTTP {response.status_code}: {response.text[:240]}",
                delivery="rejected" if response.status_code < 500 else "unknown",
            )
        self.last_error = None
        try:
            return response.json()
        except ValueError:
            return {"status": "ok"}

    @staticmethod
    def _graph_url(config: WhatsAppIntegrationConfig, path: str) -> str:
        return f"https://graph.facebook.com/{normalize_graph_api_version(config.graph_api_version)}/{path.lstrip('/')}"

    async def _request_client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=15, trust_env=False)
            return self._client
