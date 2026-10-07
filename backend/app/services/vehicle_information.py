"""One provider/cache owner for setup, manual and arrival vehicle lookups."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.modules.dvla.vehicle_enquiry import (
    DvlaVehicleEnquiryClient,
    DvlaVehicleEnquiryError,
    normalize_registration_number,
)
from app.modules.dvsa.client import DvsaClient, DvsaCredentials, DvsaError
from app.services.dvla import normalize_vehicle_enquiry_response
from app.services.settings import RuntimeConfig, get_runtime_config
from app.services.vehicle_information_contracts import (
    Provider,
    ProviderResult,
    VehicleLookup,
    VehicleRecord,
    normalize_dvsa,
    resolve_information,
)

_RELEASE = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) end return 0"
_BUCKET = """
local clock=redis.call('TIME'); local now=tonumber(clock[1])+tonumber(clock[2])/1000000
local row=redis.call('HMGET',KEYS[1],'tokens','time')
local tokens=math.min(5,tonumber(row[1] or 5)+(now-tonumber(row[2] or now))*5)
local allowed=0; if tokens>=1 then tokens=tokens-1; allowed=1 end
redis.call('HSET',KEYS[1],'tokens',tokens,'time',now); redis.call('EXPIRE',KEYS[1],10)
return allowed
"""


def credentials(config: RuntimeConfig) -> DvsaCredentials:
    return DvsaCredentials(config.dvsa_client_id, config.dvsa_client_secret, config.dvsa_api_key,
                           config.dvsa_token_url, config.dvsa_scope, config.dvsa_timeout_seconds)


def _key(provider: Provider, plate: str, config: RuntimeConfig) -> str:
    values = ((config.dvla_api_key, config.dvla_vehicle_enquiry_url) if provider == "dvla" else
              (config.dvsa_enabled, config.dvsa_client_id, config.dvsa_client_secret,
               config.dvsa_api_key, config.dvsa_token_url, config.dvsa_scope))
    generation = hashlib.sha256(json.dumps(values).encode()).hexdigest()[:24]
    return f"iacs:vehicle-information:{provider}:{generation}:{plate}"


def next_midnight(now: datetime, timezone: str) -> datetime:
    local = now.astimezone(ZoneInfo(timezone))
    return (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


class VehicleInformationService:
    def __init__(self, cache: Redis | None = None, dvsa: DvsaClient | None = None) -> None:
        self.cache = cache or Redis.from_url(settings.redis_url, decode_responses=True,
                                             socket_connect_timeout=1, socket_timeout=1)
        self.dvsa = dvsa or DvsaClient()

    async def close(self) -> None:
        await self.dvsa.close()
        await self.cache.aclose()

    async def _cached(self, key: str) -> ProviderResult | None:
        value = await self.cache.get(key)
        if not value:
            return None
        try:
            return ProviderResult.model_validate_json(value)
        except (ValueError, ValidationError):
            await self.cache.delete(key)
            return None

    async def _dvsa_slot(self) -> None:
        cooldown = await self.cache.ttl("iacs:vehicle-information:dvsa:cooldown")
        if cooldown > 0:
            raise DvsaError("throttled", retry_after=cooldown)
        if not await self.cache.eval(_BUCKET, 1, "iacs:vehicle-information:dvsa:rate"):
            raise DvsaError("throttled", retry_after=1)

    async def _fetch(self, provider: Provider, plate: str, config: RuntimeConfig) -> VehicleRecord:
        if provider == "dvsa":
            payload = await self.dvsa.lookup(plate, credentials(config), request_slot=self._dvsa_slot)
            return normalize_dvsa(payload, plate)
        client = DvlaVehicleEnquiryClient(api_key=config.dvla_api_key,
            endpoint_url=config.dvla_vehicle_enquiry_url, timeout_seconds=config.dvla_timeout_seconds)
        raw = await client.lookup(plate)
        returned = normalize_registration_number(str(raw.get("registrationNumber") or plate))
        if returned != plate:
            raise ValueError("registration_mismatch")
        normalized = normalize_vehicle_enquiry_response(raw, plate,
            today=datetime.now(UTC).astimezone(ZoneInfo(config.site_timezone)).date())
        record = VehicleRecord(**normalized.as_payload())
        if record.mot_status == "Not Required":
            record.mot_due, record.mot_expiry = record.mot_expiry, None
        return record

    async def _provider(self, provider: Provider, plate: str, config: RuntimeConfig, *, force: bool, cached_only: bool, started: datetime) -> ProviderResult:
        if (provider == "dvsa" and not config.dvsa_enabled) or (provider == "dvla" and not config.dvla_api_key):
            return ProviderResult(status="disabled")
        key = _key(provider, plate, config)
        previous: ProviderResult | None = None
        try:
            previous = await self._cached(key)
            if previous and ((previous.retry_at and previous.retry_at > started) or
                             ((not force or (previous.checked_at and previous.checked_at >= started)) and previous.valid_until and previous.valid_until > started)):
                return previous
            if cached_only:
                return previous or ProviderResult(status="deferred")
            lease = uuid.uuid4().hex
            acquired = await self.cache.set(key + ":lock", lease, nx=True, ex=60)
            if not acquired:
                # Only followers wait; they consume the winner's normalized result, even for a forced refresh.
                for _ in range(80):
                    await asyncio.sleep(0.25)
                    result = await self._cached(key)
                    if result and result != previous:
                        return result
                    if not await self.cache.exists(key + ":lock"):
                        return result or ProviderResult(status="deferred")
                return previous or ProviderResult(status="deferred")
            try:
                # Recheck after acquisition to close the cache-read / lease-acquisition race.
                current = await self._cached(key)
                if current and current.checked_at and current.checked_at >= started:
                    return current
                try:
                    record = await self._fetch(provider, plate, config)
                    now = datetime.now(UTC)
                    result = ProviderResult(status="found", record=record, checked_at=now,
                                            valid_until=next_midnight(now, config.site_timezone))
                except (DvsaError, DvlaVehicleEnquiryError, ValueError, TypeError, KeyError) as exc:
                    now = datetime.now(UTC)
                    missing = ((isinstance(exc, DvsaError) and exc.code == "not_found") or
                               (isinstance(exc, DvlaVehicleEnquiryError) and exc.status_code == 404))
                    failures = min((previous.failures if previous else 0) + 1, 3)
                    delay = 21600 if missing else (60, 300, 900)[failures - 1]
                    deferred = isinstance(exc, DvsaError) and exc.code == "throttled"
                    if isinstance(exc, DvsaError):
                        delay = max(1 if deferred else delay, int(exc.retry_after))
                        if exc.code == "rate_limited":
                            await self.cache.set("iacs:vehicle-information:dvsa:cooldown", "1", ex=max(1, delay))
                    result = ProviderResult(status="not_found" if missing else "deferred" if deferred else "failed",
                        error="not_found" if missing else exc.code if isinstance(exc, DvsaError) else "provider_unavailable",
                        checked_at=previous.checked_at if previous and previous.record else now,
                        record=previous.record if previous else None,
                        valid_until=previous.valid_until if previous else None,
                        retry_at=now + timedelta(seconds=delay), failures=previous.failures if deferred and previous else 0 if deferred else failures)
                await self.cache.set(key, result.model_dump_json(), ex=86400)
                return result
            finally:
                await self.cache.eval(_RELEASE, 1, key + ":lock", lease)
        except RedisError:
            # Provider calls require shared coordination; do not bypass it on outage.
            return ProviderResult(status="deferred", error="coordination_unavailable",
                                  record=previous.record if previous else None,
                                  checked_at=previous.checked_at if previous else None)

    async def lookup(self, registration: str, *, force: bool = False, cached_only: bool = False,
                     config: RuntimeConfig | None = None) -> VehicleLookup:
        plate = normalize_registration_number(registration)
        if not plate or len(plate) > 32:
            raise ValueError("Vehicle registration number is required")
        started = datetime.now(UTC)
        runtime = config or await get_runtime_config()
        providers: tuple[Provider, Provider] = ("dvla", "dvsa")
        fetched = await asyncio.gather(*(self._provider(provider, plate, runtime, force=force,
                                       cached_only=cached_only, started=started) for provider in providers))
        results = dict(zip(providers, fetched, strict=True))
        return VehicleLookup(requested_at=started, information=resolve_information(plate, results, now=datetime.now(UTC),
                                                             timezone=runtime.site_timezone), results=results)

    async def test_connection(self, values: dict[str, Any]) -> None:
        config = await get_runtime_config()
        credential = DvsaCredentials(
            str(values.get("dvsa_client_id") or config.dvsa_client_id),
            str(values.get("dvsa_client_secret") or config.dvsa_client_secret),
            str(values.get("dvsa_api_key") or config.dvsa_api_key),
            str(values.get("dvsa_token_url") or config.dvsa_token_url),
            str(values.get("dvsa_scope") or config.dvsa_scope),
            float(values.get("dvsa_timeout_seconds") or config.dvsa_timeout_seconds),
        )
        plate = normalize_registration_number(str(values.get("dvsa_test_registration_number") or config.dvsa_test_registration_number))
        try:
            normalize_dvsa(await self.dvsa.lookup(plate, credential, request_slot=self._dvsa_slot), plate)
        except RedisError as exc:
            raise DvsaError("coordination_unavailable") from exc


@lru_cache
def get_vehicle_information_service() -> VehicleInformationService:
    return VehicleInformationService()
