"""Snapshot writes and bounded history reads; never performs provider I/O."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Vehicle, VehicleInformationSnapshot
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.services.vehicle_information_contracts import (
    MotHistoryPage,
    MotTest,
    Provider,
    ProviderResult,
    VehicleInformation,
    VehicleLookup,
    dated_mot_status,
    resolve_information,
)


async def apply_information(session: AsyncSession, vehicle: Vehicle, lookup: VehicleLookup, *, timezone: str) -> VehicleInformation:
    """Caller owns the short transaction and vehicle row lock."""
    if normalize_registration_number(vehicle.registration_number) != lookup.information.registration_number:
        return lookup.information
    snapshot = await session.get(VehicleInformationSnapshot, vehicle.id)
    if vehicle.information_checked_at and vehicle.information_checked_at > lookup.requested_at:
        if snapshot and snapshot.registration_number == vehicle.registration_number:
            retained: dict[Provider, ProviderResult] = {
                name: ProviderResult.model_validate(getattr(snapshot, name))
                for name in ("dvla", "dvsa")
            }
            return resolve_information(vehicle.registration_number, retained, now=datetime.now(UTC), timezone=timezone)
        return VehicleInformation(registration_number=vehicle.registration_number, model=vehicle.model)
    if snapshot and snapshot.registration_number != lookup.information.registration_number:
        await session.delete(snapshot)
        await session.flush()
        snapshot = None
    if snapshot is None:
        snapshot = VehicleInformationSnapshot(vehicle_id=vehicle.id, registration_number=lookup.information.registration_number)
        session.add(snapshot)
    results = dict(lookup.results)
    for provider in ("dvla", "dvsa"):
        name: Provider = provider
        incoming = results[name]
        saved = getattr(snapshot, name)
        if saved and incoming.record is None:
            previous = ProviderResult.model_validate(saved)
            incoming = incoming.model_copy(update={"record": previous.record, "checked_at": previous.checked_at,
                                                   "valid_until": previous.valid_until})
            results[name] = incoming
        setattr(snapshot, name, incoming.model_dump(mode="json"))
    info = resolve_information(lookup.information.registration_number, results, now=datetime.now(UTC), timezone=timezone)
    for field, value in (("make", info.make), ("color", info.colour), ("fuel_type", info.fuel_type)):
        if value:
            setattr(vehicle, field, value)
    if not vehicle.model and info.model:
        vehicle.model = info.model[:120]
    if info.mot_source:
        vehicle.mot_status, vehicle.mot_expiry = info.mot_status, info.mot_expiry
        vehicle.mot_source, vehicle.mot_checked_at = info.mot_source, info.mot_checked_at
        vehicle.mot_expiry_kind = info.mot_expiry_kind
        vehicle.mot_valid_until = results[info.mot_source].valid_until if info.mot_freshness == "fresh" else None
    else:
        vehicle.mot_valid_until = None
    if info.tax_status is not None:
        vehicle.tax_status, vehicle.tax_expiry = info.tax_status, info.tax_expiry
        vehicle.last_dvla_lookup_date = info.last_dvla_lookup_date
    vehicle.information_checked_at = lookup.requested_at
    vehicle.information_outcome = {name: result.model_dump(mode="json") for name, result in info.providers.items()}
    return info


def information_summary(vehicle: Vehicle, *, timezone: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    expiry_kind = getattr(vehicle, "mot_expiry_kind", None)
    valid_until = getattr(vehicle, "mot_valid_until", None)
    checked_at = getattr(vehicle, "mot_checked_at", None)
    return {
        "mot_status": dated_mot_status(vehicle.mot_status, vehicle.mot_expiry, expiry_kind, now.astimezone(ZoneInfo(timezone)).date()),
        "mot_expiry_kind": expiry_kind,
        "mot_source": getattr(vehicle, "mot_source", None), "mot_checked_at": checked_at,
        "mot_freshness": "fresh" if valid_until and valid_until > now else "stale" if checked_at or vehicle.mot_expiry or vehicle.mot_status else "unknown",
        "information_checked_at": getattr(vehicle, "information_checked_at", None),
        "information_outcome": getattr(vehicle, "information_outcome", None) or {},
    }


async def read_mot_history(session: AsyncSession, vehicle_id: uuid.UUID, *, cursor: str | None, limit: int) -> MotHistoryPage:
    if not 1 <= limit <= 50:
        raise ValueError("History page limit must be between 1 and 50")
    plate = await session.scalar(select(Vehicle.registration_number).where(Vehicle.id == vehicle_id))
    if plate is None:
        raise LookupError("Vehicle not found")
    snapshot = VehicleInformationSnapshot
    data = snapshot.dvsa
    row = (await session.execute(select(data["checked_at"].astext, data["valid_until"].astext,
        data["status"].astext, func.jsonb_array_length(data["record"]["tests"]))
        .where(snapshot.vehicle_id == vehicle_id, snapshot.registration_number == normalize_registration_number(plate)))).first()
    if not row or not row[0]:
        return MotHistoryPage(registration_number=plate, outcome=row[2] if row and row[2] else "not_checked")
    checked_at = datetime.fromisoformat(row[0])
    valid_until = datetime.fromisoformat(row[1]) if row[1] else None
    offset = 0
    if cursor:
        try:
            version, offset = json.loads(base64.urlsafe_b64decode(cursor).decode())
            if version != row[0] or type(offset) is not int or offset < 0:
                raise ValueError
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("History changed or cursor is invalid; reload history") from exc
    count = row[3] or 0
    items = []
    if count:
        items = await session.scalar(select(func.jsonb_path_query_array(data["record"]["tests"],
            cast(f"$[{offset} to {offset + limit - 1}]", JSONPATH))).where(snapshot.vehicle_id == vehicle_id,
                snapshot.registration_number == normalize_registration_number(plate), data["checked_at"].astext == row[0]))
        if items is None:
            raise ValueError("History changed; reload history")
    next_cursor = base64.urlsafe_b64encode(json.dumps([row[0], offset + limit]).encode()).decode() if offset + limit < count else None
    return MotHistoryPage(registration_number=plate, checked_at=checked_at,
        freshness="fresh" if row[2] == "found" and valid_until and valid_until > datetime.now(UTC) else "stale",
        outcome="no_history" if not count and row[2] == "found" else row[2],
        items=[MotTest.model_validate(item) for item in (items or [])], total=count, next_cursor=next_cursor)
