"""Provider contract, cache coordination, date policy and public boundary tests."""
import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.v1 import integrations
from app.modules.dvsa.client import DvsaClient, DvsaCredentials, DvsaError
from app.schemas.directory import CreateVehicleRequest, UpdateVehicleRequest
from app.services.settings import SECRET_KEYS, DEFAULT_DYNAMIC_SETTINGS
from app.services.telemetry import sanitize_payload
from app.services.vehicle_information import VehicleInformationService, next_midnight
from app.services.vehicle_information_contracts import (
    ProviderResult, VehicleInformation, VehicleLookup, VehicleLookupRequest, VehicleRecord,
    dated_mot_status, normalize_dvsa, resolve_information, vehicle_model_name,
)
from app.services.vehicle_information_jobs import eligible_arrival
from app.models.enums import AccessDirection
from app.modules.gate.base import GateState

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
CREDS = DvsaCredentials("fixture-client", "fixture-secret", "fixture-key",
                       "https://login.microsoftonline.com/fixture-tenant/oauth2/v2.0/token")


def config(**changes):
    return SimpleNamespace(**dict(dict(dvsa_enabled=True, dvsa_client_id=CREDS.client_id,
        dvsa_client_secret=CREDS.client_secret, dvsa_api_key=CREDS.api_key,
        dvsa_token_url=CREDS.token_url, dvsa_scope=CREDS.scope, dvsa_timeout_seconds=10,
        dvla_api_key="", dvla_vehicle_enquiry_url="https://example.invalid", dvla_timeout_seconds=10,
        site_timezone="Europe/London"), **changes))


def payload(**changes):
    return dict(dict(registration="TEST123", make="Ford", model="Focus", primaryColour="Blue", motTests=[
        {"motTestNumber": "2", "completedDate": "2026-10-06T10:00:00Z", "testResult": "FAILED", "defects": [{"type": "DANGEROUS", "dangerous": True, "text": "Fixture defect"}]},
        {"motTestNumber": "1", "completedDate": "2026-01-01T10:00:00Z", "testResult": "PASSED", "expiryDate": "2027-01-01", "odometerResultType": "NO_ODOMETER"},
    ]), **changes)


class Cache:
    """Tiny atomic cache double; real Redis scripts are exercised in persistence checks."""
    def __init__(self): self.values = {}
    async def get(self, key): return self.values.get(key)
    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.values: return False
        self.values[key] = value
        return True
    async def exists(self, key): return key in self.values
    async def ttl(self, key): return 60 if key in self.values else -2
    async def delete(self, key): self.values.pop(key, None)
    async def eval(self, script, count, key, *args):
        if args:
            if self.values.get(key) == args[0]: self.values.pop(key)
        return 1
    async def aclose(self): pass


@pytest.mark.parametrize("make,model,expected", [
    ("MG", "ZS EXCLUSIVE EV", "Zs"),
    ("Mercedes-Benz", "SPRINTER", "Sprinter"),
    ("Volkswagen", "T-ROC LIFE TSI S-A", "T-Roc"),
    ("Volkswagen", "CADDY", "Caddy"),
    ("Iveco", "DAILY 35S14B", "Daily"),
    ("Iveco", "DAILY 35C14B", "Daily"),
    ("Renault", "TRAFIC ADVANCE RC LL30 ETCH EV", "Trafic"),
    ("Cupra", "FORMENTOR V1 PHEV S-A", "Formentor"),
    ("Ford", "TRANSIT CUSTOM 320LMTD EBLUE A", "Transit"),
    ("Ford", "FOCUS TITANIUM TDCI", "Focus"),
    ("Unknown make", "NEW-MODEL EXCLUSIVE EV", "New-Model"),
    (None, "  fOcUs\tTITANIUM  ", "Focus"),
    ("MG", None, None),
    ("MG", "", None),
    ("MG", " \t ", None),
    ("  tesla ", "model\ty LONG RANGE AWD", "Model Y"),
    ("Tesla", "MODEL 3 PERFORMANCE AWD", "Model 3"),
    ("Land Rover", "RANGE ROVER EVOQUE DYNAMIC", "Range Rover"),
    ("BMW", "3 SERIES 320D M SPORT", "3 Series"),
    ("Hyundai", "SANTA FE PREMIUM CRDI", "Santa Fe"),
    ("Hyundai", "IONIQ 5 ULTIMATE EV", "Ioniq 5"),
    ("Jeep", "GRAND CHEROKEE LIMITED", "Grand Cherokee"),
    ("Renault", "GRAND SCENIC DYNAMIQUE", "Grand Scenic"),
    ("Suzuki", "GRAND VITARA SZ5", "Grand Vitara"),
    ("Ford", "GRAND C-MAX TITANIUM", "Grand C-Max"),
    ("Other", "MODEL Y TRIM", "Model"),
    ("Tesla", "MODEL UNKNOWN TRIM", "Model"),
])
def test_model_names_drop_trim_and_title_case_without_losing_known_names(make, model, expected):
    assert vehicle_model_name(make, model) == expected


@pytest.mark.parametrize("status", ["found", "failed"])
def test_model_presentation_keeps_complete_provider_evidence_and_make(status):
    record = normalize_dvsa(payload(make="MG", model="ZS EXCLUSIVE EV"), "TEST123")
    result = ProviderResult(status=status, record=record, checked_at=NOW)
    info = resolve_information("TEST123", {"dvsa": result}, now=NOW, timezone="Europe/London")
    assert info.make == "MG" and info.model == "Zs"
    assert result.record.model == "ZS EXCLUSIVE EV"


def test_model_names_can_use_dvla_make_when_missing_from_dvsa():
    results = {
        "dvla": ProviderResult(status="found", record=VehicleRecord(registration_number="TEST123", make="Tesla")),
        "dvsa": ProviderResult(status="found", record=VehicleRecord(registration_number="TEST123", model="MODEL Y LONG RANGE")),
    }
    info = resolve_information("TEST123", results, now=NOW, timezone="Europe/London")
    assert info.model == "Model Y"


@pytest.mark.asyncio
async def test_cached_provider_model_uses_short_name_without_another_lookup():
    fetch = AsyncMock(return_value=payload(make="MG", model="ZS EXCLUSIVE EV"))
    service = VehicleInformationService(Cache(), SimpleNamespace(lookup=fetch, close=AsyncMock()))
    try:
        await service.lookup("TEST123", config=config())
        cached = await service.lookup("TEST123", config=config(), cached_only=True)
        assert cached.information.make == "MG" and cached.information.model == "Zs"
        assert cached.results["dvsa"].record.model == "ZS EXCLUSIVE EV"
        fetch.assert_awaited_once()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_adapter_reuses_token_refreshes_once_on_401_and_redacts_credentials():
    calls = []
    def transport(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": f"token-{len(calls)}", "expires_in": 120})
        if len(calls) == 3: return httpx.Response(401)
        assert request.headers["X-API-Key"] == "fixture-key"
        return httpx.Response(200, json=payload())
    client = DvsaClient(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    slot = AsyncMock()
    await client.lookup("TEST123", CREDS, request_slot=slot)
    await client.lookup("TEST123", CREDS, request_slot=slot)
    assert [call.method for call in calls] == ["POST", "GET", "GET", "POST", "GET"]
    assert slot.await_count == 3
    assert "fixture-secret" not in repr(CREDS) and "fixture-key" not in repr(CREDS)
    await client.close()


@pytest.mark.asyncio
async def test_token_expiry_and_configuration_change():
    calls = []
    def transport(request):
        calls.append(request.method)
        return httpx.Response(200, json={"access_token": "token", "expires_in": 100} if request.method == "POST" else payload())
    client = DvsaClient(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    await client.lookup("TEST123", CREDS)
    client._expires = 0
    await client.lookup("TEST123", CREDS)
    changed = DvsaCredentials("changed-client", CREDS.client_secret, CREDS.api_key, CREDS.token_url)
    await client.lookup("TEST123", changed)
    assert calls.count("POST") == 3
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(404,"not_found"),(429,"rate_limited"),(503,"provider_unavailable"),(403,"authentication_failed")])
async def test_provider_errors_are_fixed_safe_codes(status, code):
    def transport(request):
        if request.method == "POST": return httpx.Response(200, json={"access_token": "token", "expires_in": 120})
        return httpx.Response(status, text="sensitive raw provider response", headers={"Retry-After": "180"})
    client = DvsaClient(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with pytest.raises(DvsaError, match=code) as caught: await client.lookup("TEST123", CREDS)
    assert str(caught.value) == code
    if status in (429, 503): assert caught.value.retry_after == 180
    await client.close()


def test_sort_history_keep_failed_result_separate_from_expiry_and_missing_defects():
    record = normalize_dvsa(payload(), "TEST123")
    assert record.model == "Focus" and record.mot_expiry == date(2027,1,1)
    assert record.tests[0].result == "FAILED" and record.tests[1].defects is None
    assert record.tests[1].mileage is None
    for source in ("dvsa", "dva ni", "dvsa cvs"):
        raw = payload(); raw["motTests"][0]["dataSource"] = source
        assert normalize_dvsa(raw, "TEST123").tests[0].source == source
    with pytest.raises(ValueError): normalize_dvsa(payload(registration="OTHER"), "TEST123")
    with pytest.raises(ValueError): normalize_dvsa(payload(motTests={}), "TEST123")


@pytest.mark.parametrize("today,status", [(date(2026,10,6),"Not Required"),(date(2026,10,7),"Due"),(date(2026,10,8),"Overdue")])
def test_explicit_first_test_date(today, status):
    record = normalize_dvsa(payload(motTests=[], motTestDueDate="2026-10-07"), "TEST123")
    assert dated_mot_status(None, record.mot_due, "first_test", today) == status


def test_precedence_fresh_fallback_stale_and_no_invented_expiry():
    dvsa = ProviderResult(status="found", checked_at=NOW, valid_until=NOW+timedelta(hours=1), record=normalize_dvsa(payload(),"TEST123"))
    dvla = ProviderResult(status="found", checked_at=NOW, valid_until=NOW+timedelta(hours=1), record=VehicleRecord(registration_number="TEST123", mot_expiry=date(2025,1,1), tax_status="Taxed"))
    result = resolve_information("TEST123", {"dvsa":dvsa,"dvla":dvla}, now=NOW, timezone="Europe/London")
    assert result.mot_source == "dvsa" and result.mot_status == "Valid" and result.tax_status == "Taxed"
    dvsa.status = "failed"
    assert resolve_information("TEST123", {"dvsa":dvsa,"dvla":dvla}, now=NOW, timezone="Europe/London").mot_source == "dvla"
    dvla.status = "failed"
    assert resolve_information("TEST123", {"dvsa":dvsa,"dvla":dvla}, now=NOW, timezone="Europe/London").mot_freshness == "stale"
    assert resolve_information("TEST123", {}, now=NOW, timezone="Europe/London").mot_status is None
    assert dated_mot_status(None, date(2026,10,7), "test", date(2026,10,7)) == "Valid"
    assert (next_midnight(datetime(2026,10,25,tzinfo=UTC), "Europe/London") - datetime(2026,10,25,tzinfo=UTC)).total_seconds() <= 86400


@pytest.mark.asyncio
async def test_concurrent_setup_manual_arrival_coalesce_and_day_cache():
    fetch = AsyncMock(return_value=payload())
    service = VehicleInformationService(Cache(), SimpleNamespace(lookup=fetch, close=AsyncMock()))
    results = await asyncio.gather(*(service.lookup("test 123", config=config(), force=force) for force in (False,True,False)))
    assert fetch.await_count == 1 and all(result.information.model == "Focus" for result in results)
    await service.lookup("TEST123", config=config())
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_negative_cache_and_backoff_prevent_repeat_requests():
    fetch = AsyncMock(side_effect=DvsaError("not_found"))
    service = VehicleInformationService(Cache(), SimpleNamespace(lookup=fetch, close=AsyncMock()))
    first = await service.lookup("TEST123", config=config())
    second = await service.lookup("TEST123", config=config(), force=True)
    assert fetch.await_count == 1
    assert first.results["dvsa"].status == second.results["dvsa"].status == "not_found"
    assert first.results["dvsa"].retry_at > datetime.now(UTC)+timedelta(hours=5)


@pytest.mark.asyncio
async def test_redis_failure_disabled_provider_and_cache_only_never_send():
    cache = Cache(); cache.get = AsyncMock(side_effect=RedisConnectionError())
    fetch = AsyncMock()
    service = VehicleInformationService(cache, SimpleNamespace(lookup=fetch, close=AsyncMock()))
    assert (await service.lookup("TEST123", config=config())).results["dvsa"].status == "deferred"
    assert (await service.lookup("TEST123", config=config(dvsa_enabled=False))).results["dvsa"].status == "disabled"
    service.cache = Cache()
    await service.lookup("TEST123", config=config(), cached_only=True)
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_lookup_is_normalized_and_no_history_or_raw_provider_payload(monkeypatch):
    info = VehicleInformation(registration_number="TEST123", model="Focus")
    lookup = AsyncMock(return_value=VehicleLookup(information=info, results={}))
    monkeypatch.setattr(integrations, "get_vehicle_information_service", lambda: SimpleNamespace(lookup=lookup))
    monkeypatch.setattr(integrations, "record_audit_log", AsyncMock())
    response = await integrations.vehicle_lookup(VehicleLookupRequest(registration_number="TEST123"), SimpleNamespace(id=None, username="fixture", full_name="Fixture"))
    assert response == info and "tests" not in response.model_dump() and "vehicle" not in response.model_dump()


def test_contract_retirement_secrets_and_arrival_eligibility():
    routes = {route.path for route in integrations.router.routes}
    assert "/integrations/dvla/lookup" not in routes and "/dvla/lookup" not in routes
    for model in (CreateVehicleRequest, UpdateVehicleRequest):
        assert not set(model.model_fields) & {"mot_status", "mot_expiry", "last_dvla_lookup_date"}
    assert {"dvsa_client_secret", "dvsa_api_key"} <= SECRET_KEYS
    assert DEFAULT_DYNAMIC_SETTINGS["dvsa_enabled"][1] is False
    assert sanitize_payload({"dvsa_client_secret":"fixture", "dvsa_api_key":"fixture"}) == {"dvsa_client_secret":"[redacted]", "dvsa_api_key":"[redacted]"}
    assert eligible_arrival(AccessDirection.ENTRY, GateState.OPEN)
    assert not eligible_arrival(AccessDirection.EXIT, GateState.CLOSED)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["json", "timeout", "token"])
async def test_adapter_rejects_malformed_and_timed_out_responses_without_payload(failure):
    def transport(request):
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "fixture", "expires_in": "nan" if failure == "token" else 120})
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive fixture", request=request)
        return httpx.Response(200, text="sensitive malformed body")
    client = DvsaClient(httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with pytest.raises(DvsaError) as caught:
        await client.lookup("TEST123", CREDS)
    assert str(caught.value) in {"invalid_response", "provider_unavailable"}
    await client.close()


def test_nullable_ni_and_commercial_history_and_missing_latest_expiry():
    record = normalize_dvsa(payload(motTests=[{"completedDate": None, "testResult": "PASSED", "motTestNumber": None,
        "dataSource": "CVS", "expiryDate": "2027-01-01", "defects": None, "odometerResultType": "NO_ODOMETER"}]), "TEST123")
    assert record.tests[0].completed_at is None and record.tests[0].number is None
    assert record.tests[0].defects is None and record.mot_expiry == date(2027, 1, 1)
    raw = payload()
    raw["motTests"][0]["testResult"] = "PASSED"
    assert normalize_dvsa(raw, "TEST123").mot_expiry is None
    assert normalize_dvsa(payload(motTestDueDate="2027-02-02"), "TEST123").mot_expiry is None
    with pytest.raises(ValueError):
        normalize_dvsa({"motTests": []}, "TEST123")


@pytest.mark.asyncio
async def test_transient_backoff_escalates_and_preserves_last_success():
    from app.services.vehicle_information import _key
    cache = Cache()
    fetch = AsyncMock(return_value=payload())
    service = VehicleInformationService(cache, SimpleNamespace(lookup=fetch, close=AsyncMock()))
    await service.lookup("TEST123", config=config())
    fetch.side_effect = DvsaError("provider_unavailable")
    key = _key("dvsa", "TEST123", config())
    for seconds in (60, 300, 900):
        prior = ProviderResult.model_validate_json(cache.values[key])
        prior.retry_at = None
        cache.values[key] = prior.model_dump_json()
        result = await service.lookup("TEST123", config=config(), force=True)
        remaining = (result.results["dvsa"].retry_at-datetime.now(UTC)).total_seconds()
        assert seconds-2 <= remaining <= seconds
        assert result.information.model == "Focus" and result.information.mot_freshness == "stale"


async def test_in_session_invalidation_clears_only_owned_aggregate_and_never_commits():
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models import Vehicle
    from app.services.vehicle_information_store import INFORMATION_SUMMARY_FIELDS, invalidate_information_in_session
    vehicle = Vehicle(registration_number="SYNTH01", make="User make", model="User model", color="User colour")
    for field in INFORMATION_SUMMARY_FIELDS:
        setattr(vehicle, field, "synthetic-owned-value")
    session = AsyncMock(spec=AsyncSession)
    snapshot = object()
    session.get.return_value = snapshot
    await invalidate_information_in_session(session, vehicle)
    session.delete.assert_awaited_once_with(snapshot)
    session.commit.assert_not_awaited()
    assert all(getattr(vehicle, field) is None for field in INFORMATION_SUMMARY_FIELDS)
    assert (vehicle.registration_number, vehicle.make, vehicle.model, vehicle.color) == ("SYNTH01", "User make", "User model", "User colour")
