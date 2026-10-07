"""DVSA protocol I/O. No database, policy, logging, or business-service imports."""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

BASE_URL = "https://history.mot.api.gov.uk/v1/trade/vehicles/registration/"
TOKEN_URL = re.compile(r"https://login\.microsoftonline\.com/[a-zA-Z0-9-]+/oauth2/v2\.0/token\Z")


@dataclass(frozen=True)
class DvsaCredentials:
    client_id: str
    client_secret: str = field(repr=False)
    api_key: str = field(repr=False)
    token_url: str = ""
    scope: str = "https://tapi.dvsa.gov.uk/.default"
    timeout: float = 10.0


class DvsaError(RuntimeError):
    """Only fixed, safe error codes cross the provider boundary."""

    def __init__(self, code: str, *, retry_after: float = 0) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def retry_delay(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
        return max(0, seconds) if math.isfinite(seconds) else 60.0
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 60.0


class DvsaClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(trust_env=False, follow_redirects=False)
        self._token_lock = asyncio.Lock()
        self._credentials: DvsaCredentials | None = None
        self._token = ""
        self._expires = 0.0

    async def close(self) -> None:
        await self._client.aclose()
        self._token = ""
        self._credentials = None

    async def _access_token(self, credentials: DvsaCredentials, *, rejected: str = "") -> str:
        async with self._token_lock:
            if (self._credentials == credentials and self._token
                    and self._token != rejected and time.monotonic() < self._expires):
                return self._token
            if not (TOKEN_URL.fullmatch(credentials.token_url) and credentials.client_id
                    and credentials.client_secret and credentials.api_key
                    and credentials.scope == "https://tapi.dvsa.gov.uk/.default"):
                raise DvsaError("configuration_error")
            response = await self._client.post(credentials.token_url, data={
                "grant_type": "client_credentials", "client_id": credentials.client_id,
                "client_secret": credentials.client_secret, "scope": credentials.scope,
            }, timeout=credentials.timeout)
            if response.status_code == 429:
                raise DvsaError("rate_limited", retry_after=retry_delay(response))
            if response.status_code != 200:
                raise DvsaError("authentication_failed")
            try:
                data = response.json()
                token, lifetime = data["access_token"], float(data["expires_in"])
                if not isinstance(token, str) or not token or not math.isfinite(lifetime) or not 0 < lifetime <= 86400:
                    raise ValueError
            except (ValueError, TypeError, KeyError) as exc:
                raise DvsaError("invalid_response") from exc
            self._credentials, self._token = credentials, token
            self._expires = time.monotonic() + max(0, lifetime - min(60, lifetime / 10))
            return token

    async def lookup(self, registration: str, credentials: DvsaCredentials, *, request_slot: Callable[[], Awaitable[None]] | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Z0-9]{1,32}", registration):
            raise DvsaError("invalid_registration")
        try:
            token = await self._access_token(credentials)
            for attempt in range(2):
                if request_slot:
                    await request_slot()
                response = await self._client.get(BASE_URL + quote(registration, safe=""), headers={
                    "Authorization": f"Bearer {token}", "X-API-Key": credentials.api_key,
                    "Accept": "application/json",
                }, timeout=credentials.timeout)
                if response.status_code == 401 and attempt == 0:
                    token = await self._access_token(credentials, rejected=token)
                    continue
                if response.status_code == 404:
                    raise DvsaError("not_found")
                if response.status_code == 429:
                    raise DvsaError("rate_limited", retry_after=retry_delay(response))
                if response.status_code in (401, 403):
                    raise DvsaError("authentication_failed")
                if response.status_code != 200:
                    raise DvsaError("provider_unavailable", retry_after=retry_delay(response) if "Retry-After" in response.headers else 0)
                if len(response.content) > 2_000_000:
                    raise DvsaError("invalid_response")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise DvsaError("invalid_response") from exc
                if not isinstance(data, dict):
                    raise DvsaError("invalid_response")
                return data
        except httpx.HTTPError as exc:
            raise DvsaError("provider_unavailable") from exc
        raise DvsaError("authentication_failed")
