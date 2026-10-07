"""Normalized vehicle information and pure MOT precedence/date policy."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["dvla", "dvsa"]
Outcome = Literal["found", "not_found", "disabled", "deferred", "failed"]
Freshness = Literal["fresh", "stale", "unknown"]


class MotDefect(BaseModel):
    text: str | None = None
    type: str | None = None
    dangerous: bool | None = None


class MotTest(BaseModel):
    number: str | None = None
    completed_at: datetime | None = None
    result: Literal["PASSED", "FAILED"]
    expiry: date | None = None
    mileage: str | None = None
    mileage_unit: str | None = None
    mileage_read: str | None = None
    source: str | None = None
    defects: list[MotDefect] | None = None


class VehicleRecord(BaseModel):
    registration_number: str
    make: str | None = None
    model: str | None = None
    colour: str | None = None
    fuel_type: str | None = None
    mot_status: str | None = None
    mot_expiry: date | None = None
    mot_due: date | None = None
    tax_status: str | None = None
    tax_expiry: date | None = None
    tests: list[MotTest] = Field(default_factory=list)


class ProviderOutcome(BaseModel):
    status: Outcome
    checked_at: datetime | None = None
    retry_at: datetime | None = None
    error: str | None = None


class ProviderResult(ProviderOutcome):
    record: VehicleRecord | None = None
    valid_until: datetime | None = None
    failures: int = 0


class VehicleInformation(BaseModel):
    registration_number: str
    make: str | None = None
    model: str | None = None
    colour: str | None = None
    fuel_type: str | None = None
    mot_status: str | None = None
    mot_expiry: date | None = None
    mot_expiry_kind: Literal["test", "first_test"] | None = None
    mot_source: Provider | None = None
    mot_checked_at: datetime | None = None
    mot_freshness: Freshness = "unknown"
    tax_status: str | None = None
    tax_expiry: date | None = None
    last_dvla_lookup_date: date | None = None
    providers: dict[Provider, ProviderOutcome] = Field(default_factory=dict)


class VehicleLookup(BaseModel):
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    information: VehicleInformation
    results: dict[Provider, ProviderResult]


class VehicleLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_number: str = Field(min_length=1, max_length=32)


class MotHistoryPage(BaseModel):
    registration_number: str
    checked_at: datetime | None = None
    freshness: Freshness = "unknown"
    outcome: str = "not_checked"
    items: list[MotTest] = Field(default_factory=list)
    total: int = 0
    next_cursor: str | None = None


def normalize_dvsa(payload: dict[str, Any], registration: str) -> VehicleRecord:
    """Validate at the boundary, retain only documented fields, sort across data sources."""
    if not any(key in payload for key in ("registration", "motTests", "motTestDueDate")):
        raise ValueError("invalid_response")
    returned = payload.get("registration")
    if not isinstance(returned, str) or returned.replace(" ", "").upper() != registration:
        raise ValueError("registration_mismatch")
    tests: list[MotTest] = []
    raw_tests = payload.get("motTests", [])
    if not isinstance(raw_tests, list) or len(raw_tests) > 2000:
        raise ValueError("invalid_tests")
    for item in raw_tests:
        tests.append(MotTest(
            number=item.get("motTestNumber"), completed_at=item.get("completedDate"),
            result=item["testResult"], expiry=item.get("expiryDate"),
            mileage=None if item.get("odometerValue") is None else str(item["odometerValue"]),
            mileage_unit=item.get("odometerUnit"), mileage_read=item.get("odometerResultType"),
            source=item.get("dataSource"),
            defects=[MotDefect.model_validate(d) for d in item["defects"]]
            if isinstance(item.get("defects"), list) else None,
        ))
    for test in tests:
        if test.completed_at is not None and test.completed_at.tzinfo is None:
            test.completed_at = test.completed_at.replace(tzinfo=UTC)
    tests.sort(key=lambda test: (test.completed_at or datetime.min.replace(tzinfo=UTC),
        test.number or "", test.source or "", test.model_dump_json()), reverse=True)
    latest_pass = next((test for test in tests if test.result == "PASSED"), None)
    return VehicleRecord(
        registration_number=registration, make=payload.get("make"), model=payload.get("model"),
        colour=payload.get("primaryColour"), fuel_type=payload.get("fuelType"),
        mot_expiry=latest_pass.expiry if latest_pass and not payload.get("motTestDueDate") else None,
        mot_due=payload.get("motTestDueDate"), tests=tests,
    )


def dated_mot_status(status: str | None, expiry: date | None, kind: str | None, today: date) -> str | None:
    if expiry:
        if kind == "first_test":
            return "Not Required" if today < expiry else "Due" if today == expiry else "Overdue"
        return "Valid" if today <= expiry else "Expired"
    return status


def resolve_information(registration: str, results: dict[Provider, ProviderResult], *, now: datetime, timezone: str) -> VehicleInformation:
    today = now.astimezone(ZoneInfo(timezone)).date()
    info = VehicleInformation(registration_number=registration, providers={
        name: ProviderOutcome(**result.model_dump(exclude={"record", "valid_until", "failures"}))
        for name, result in results.items()
    })
    dvla = results.get("dvla")
    dvsa = results.get("dvsa")
    if dvla and dvla.record:
        record = dvla.record
        info.make, info.colour, info.fuel_type = record.make, record.colour, record.fuel_type
        info.tax_status, info.tax_expiry = record.tax_status, record.tax_expiry
        info.last_dvla_lookup_date = dvla.checked_at.astimezone(ZoneInfo(timezone)).date() if dvla.checked_at else None
    if dvsa and dvsa.record:
        info.model = dvsa.record.model
        info.make = info.make or dvsa.record.make
        info.colour = info.colour or dvsa.record.colour
        info.fuel_type = info.fuel_type or dvsa.record.fuel_type
    candidates: list[tuple[bool, Provider, ProviderResult]] = []
    for name in ("dvsa", "dvla"):
        result = results.get(name)
        if result and result.record and (result.record.mot_expiry or result.record.mot_due or result.record.mot_status):
            fresh = bool(result.status == "found" and result.valid_until and result.valid_until > now)
            candidates.append((fresh, name, result))
    candidates.sort(key=lambda item: not item[0])  # stable: DVSA wins at equal freshness
    if candidates:
        fresh, name, result = candidates[0]
        selected = result.record
        assert selected is not None
        info.mot_source, info.mot_checked_at = name, result.checked_at
        info.mot_expiry = selected.mot_expiry or selected.mot_due
        info.mot_expiry_kind = "test" if selected.mot_expiry else "first_test" if selected.mot_due else None
        info.mot_status = dated_mot_status(selected.mot_status, info.mot_expiry, info.mot_expiry_kind, today)
        info.mot_freshness = "fresh" if fresh else "stale"
    return info
