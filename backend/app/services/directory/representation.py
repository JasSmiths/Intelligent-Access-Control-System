import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, String, column, select, true, values
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AccessEvent, Group, Person, Vehicle
from app.modules.dvla.vehicle_enquiry import friendly_vehicle_text
from app.services.person_presence_input_booleans import (
    normalize_input_boolean_action,
    normalize_input_boolean_entity_ids,
)
from app.services.profile_photos import (
    ProfilePhotoError,
    normalize_profile_photo_data_url,
    stored_image_url,
)
from app.services.vehicle_information_store import information_summary

from .errors import DirectoryOperationError


@dataclass(frozen=True)
class DirectoryPhotoReferences:
    """Small stored-photo references projected without loading image bodies."""

    people: Mapping[str, str | None] = field(default_factory=dict)
    vehicles: Mapping[str, str | None] = field(default_factory=dict)


def _vehicle_photo_reference(
    vehicle: Vehicle, references: DirectoryPhotoReferences | None
) -> str | None:
    return (
        references.vehicles[str(vehicle.id)]
        if references is not None
        else vehicle.vehicle_photo_data_url
    )


def compose_person_name(first_name: str, last_name: str) -> str:
    return f"{first_name.strip()} {last_name.strip()}".strip()


def serialize_group(group: Group) -> dict:
    return {
        "id": str(group.id),
        "name": group.name,
        "category": group.category.value,
        "subtype": group.subtype,
        "description": group.description,
        "people_count": len(group.people),
    }


def normalize_registration_number(registration_number: str) -> str:
    return registration_number.strip().upper().replace(" ", "")


def normalize_vehicle_text(value: str | None) -> str | None:
    return friendly_vehicle_text(value) if value else None


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def local_today(timezone_name: str | None) -> date:
    try:
        timezone = ZoneInfo(timezone_name or "Europe/London")
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    return datetime.now(tz=timezone).date()


def normalize_home_assistant_mobile_notify_service(service_name: str | None) -> str | None:
    service_name = normalize_optional_text(service_name)
    if service_name and not service_name.startswith("notify.mobile_app_"):
        raise DirectoryOperationError(
            status_code=400,
            detail="Home Assistant mobile notification target must be a notify.mobile_app_* service.",
        )
    return service_name


def normalize_person_presence_input_boolean_entity_ids(entity_ids: list[str] | None) -> list[str]:
    try:
        return normalize_input_boolean_entity_ids(entity_ids)
    except ValueError as exc:
        raise DirectoryOperationError(status_code=400, detail=str(exc)) from exc


def normalize_person_presence_input_boolean_action(action: str | None) -> str:
    try:
        return normalize_input_boolean_action(action)
    except ValueError as exc:
        raise DirectoryOperationError(status_code=400, detail=str(exc)) from exc


def normalize_person_pronouns(pronouns: str | None) -> str | None:
    pronouns = normalize_optional_text(pronouns)
    if pronouns is None:
        return None
    normalized = pronouns.casefold()
    if normalized not in {"he/him", "she/her"}:
        raise DirectoryOperationError(
            status_code=400,
            detail="Person pronouns must be he/him or she/her.",
        )
    return normalized


async def normalize_profile_photo(profile_photo_data_url: str | None) -> str | None:
    try:
        return await asyncio.to_thread(normalize_profile_photo_data_url, profile_photo_data_url)
    except ProfilePhotoError as exc:
        raise DirectoryOperationError(
            status_code=400,
            detail="Profile photo could not be processed.",
        ) from exc


def assigned_people_for_vehicle(vehicle: Vehicle) -> list[Person]:
    people = [
        assignment.person
        for assignment in getattr(vehicle, "person_assignments", []) or []
        if assignment.person
    ]
    if people:
        return sorted(people, key=lambda person: person.display_name)
    return [vehicle.owner] if vehicle.owner else []


def assigned_vehicles_for_person(person: Person) -> list[Vehicle]:
    vehicles = [
        assignment.vehicle
        for assignment in getattr(person, "vehicle_assignments", []) or []
        if assignment.vehicle
    ]
    if vehicles:
        return sorted(vehicles, key=lambda vehicle: vehicle.registration_number)
    return list(person.vehicles or [])


def vehicle_photo_url(
    vehicle: Vehicle,
    fallback_photo_urls: Mapping[str, str] | None = None,
    photo_references: DirectoryPhotoReferences | None = None,
) -> str | None:
    return stored_image_url(
        _vehicle_photo_reference(vehicle, photo_references),
        f"/api/v1/vehicles/{vehicle.id}/photo",
        getattr(vehicle, "updated_at", None),
    ) or (fallback_photo_urls or {}).get(vehicle.registration_number)


async def latest_vehicle_snapshot_urls(
    session: AsyncSession,
    vehicles: Sequence[Vehicle],
    photo_references: DirectoryPhotoReferences | None = None,
) -> dict[str, str]:
    registrations = sorted(
        {
            vehicle.registration_number
            for vehicle in vehicles
            if not _vehicle_photo_reference(vehicle, photo_references)
        }
    )
    if not registrations:
        return {}

    photo_urls: dict[str, str] = {}
    for offset in range(0, len(registrations), 200):
        rows = await session.execute(latest_snapshot_query(registrations[offset : offset + 200]))
        photo_urls.update(
            {registration: f"/api/v1/events/{event_id}/snapshot" for registration, event_id in rows}
        )
    return photo_urls


def latest_snapshot_query(registrations: list[str]) -> Select[tuple[str, uuid.UUID]]:
    requested = values(column("registration_number", String), name="snapshot_registrations").data(
        [(registration,) for registration in registrations]
    )
    latest = (
        select(AccessEvent.id.label("event_id"))
        .where(
            AccessEvent.registration_number == requested.c.registration_number,
            AccessEvent.snapshot_path.is_not(None),
            AccessEvent.snapshot_bytes.is_not(None),
        )
        .order_by(AccessEvent.occurred_at.desc())
        .limit(1)
        .lateral("latest_snapshot")
    )
    return select(requested.c.registration_number, latest.c.event_id).select_from(
        requested.join(latest, true())
    )


def serialize_vehicle(
    vehicle: Vehicle,
    *,
    include_media: bool = True,
    fallback_photo_urls: Mapping[str, str] | None = None,
    photo_references: DirectoryPhotoReferences | None = None,
    timezone_name: str | None = None,
) -> dict:
    assigned_people = assigned_people_for_vehicle(vehicle)
    owners = [person.display_name for person in assigned_people]
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "vehicle_photo_data_url": vehicle.vehicle_photo_data_url if include_media else None,
        "vehicle_photo_url": vehicle_photo_url(vehicle, fallback_photo_urls, photo_references),
        "description": vehicle.description,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "fuel_type": vehicle.fuel_type,
        **information_summary(vehicle, timezone=timezone_name or settings.site_timezone),
        "tax_status": vehicle.tax_status,
        "mot_expiry": vehicle.mot_expiry,
        "tax_expiry": vehicle.tax_expiry,
        "last_dvla_lookup_date": vehicle.last_dvla_lookup_date,
        "person_id": str(vehicle.person_id) if vehicle.person_id else None,
        "owner": vehicle.owner.display_name
        if vehicle.owner
        else (owners[0] if len(owners) == 1 else None),
        "person_ids": [str(person.id) for person in assigned_people],
        "owners": owners,
        "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
        "schedule": vehicle.schedule.name if vehicle.schedule else None,
        "is_active": vehicle.is_active,
    }


def serialize_person(
    person: Person,
    *,
    include_media: bool = True,
    fallback_vehicle_photo_urls: Mapping[str, str] | None = None,
    photo_references: DirectoryPhotoReferences | None = None,
    timezone_name: str | None = None,
) -> dict:
    assigned_vehicles = assigned_vehicles_for_person(person)
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person.display_name,
        "pronouns": person.pronouns,
        "profile_photo_data_url": person.profile_photo_data_url if include_media else None,
        "profile_photo_url": stored_image_url(
            photo_references.people[str(person.id)]
            if photo_references is not None
            else person.profile_photo_data_url,
            f"/api/v1/people/{person.id}/photo",
            getattr(person, "updated_at", None),
        ),
        "group_id": str(person.group_id) if person.group_id else None,
        "group": person.group.name if person.group else None,
        "category": person.group.category.value if person.group else None,
        "schedule_id": str(person.schedule_id) if person.schedule_id else None,
        "schedule": person.schedule.name if person.schedule else None,
        "is_active": person.is_active,
        "notes": person.notes,
        "garage_door_entity_ids": list(person.garage_door_entity_ids or []),
        "home_assistant_mobile_app_notify_service": person.home_assistant_mobile_app_notify_service,
        "missed_exit_recovery_enabled": person.missed_exit_recovery_enabled,
        "missed_exit_recovery_tracker_entity_id": person.missed_exit_recovery_tracker_entity_id,
        "home_assistant_presence_input_boolean_entity_ids": list(
            getattr(person, "home_assistant_presence_input_boolean_entity_ids", None) or []
        ),
        "home_assistant_presence_input_boolean_entry_action": normalize_input_boolean_action(
            getattr(person, "home_assistant_presence_input_boolean_entry_action", None)
        ),
        "home_assistant_presence_input_boolean_exit_action": normalize_input_boolean_action(
            getattr(person, "home_assistant_presence_input_boolean_exit_action", None)
        ),
        "vehicles": [
            {
                "id": str(vehicle.id),
                "registration_number": vehicle.registration_number,
                "description": vehicle.description,
                "vehicle_photo_data_url": vehicle.vehicle_photo_data_url if include_media else None,
                "vehicle_photo_url": vehicle_photo_url(
                    vehicle, fallback_vehicle_photo_urls, photo_references
                ),
                "make": vehicle.make,
                "model": vehicle.model,
                "color": vehicle.color,
                "fuel_type": vehicle.fuel_type,
                **information_summary(vehicle, timezone=timezone_name or settings.site_timezone),
                "tax_status": vehicle.tax_status,
                "mot_expiry": vehicle.mot_expiry,
                "tax_expiry": vehicle.tax_expiry,
                "last_dvla_lookup_date": vehicle.last_dvla_lookup_date,
                "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
                "schedule": vehicle.schedule.name if vehicle.schedule else None,
            }
            for vehicle in assigned_vehicles
        ],
    }


def person_audit_snapshot(person: Person) -> dict:
    return {
        "id": str(person.id),
        "first_name": person.first_name,
        "last_name": person.last_name,
        "display_name": person.display_name,
        "pronouns": person.pronouns,
        "group_id": str(person.group_id) if person.group_id else None,
        "schedule_id": str(person.schedule_id) if person.schedule_id else None,
        "garage_door_entity_ids": list(person.garage_door_entity_ids or []),
        "home_assistant_mobile_app_notify_service": person.home_assistant_mobile_app_notify_service,
        "missed_exit_recovery_enabled": person.missed_exit_recovery_enabled,
        "missed_exit_recovery_tracker_entity_id": person.missed_exit_recovery_tracker_entity_id,
        "home_assistant_presence_input_boolean_entity_ids": list(
            getattr(person, "home_assistant_presence_input_boolean_entity_ids", None) or []
        ),
        "home_assistant_presence_input_boolean_entry_action": normalize_input_boolean_action(
            getattr(person, "home_assistant_presence_input_boolean_entry_action", None)
        ),
        "home_assistant_presence_input_boolean_exit_action": normalize_input_boolean_action(
            getattr(person, "home_assistant_presence_input_boolean_exit_action", None)
        ),
        "notes": person.notes,
        "is_active": person.is_active,
    }


def vehicle_audit_snapshot(vehicle: Vehicle) -> dict:
    return {
        "id": str(vehicle.id),
        "registration_number": vehicle.registration_number,
        "person_id": str(vehicle.person_id) if vehicle.person_id else None,
        "schedule_id": str(vehicle.schedule_id) if vehicle.schedule_id else None,
        "make": vehicle.make,
        "model": vehicle.model,
        "color": vehicle.color,
        "fuel_type": vehicle.fuel_type,
        **information_summary(vehicle, timezone=settings.site_timezone),
        "tax_status": vehicle.tax_status,
        "mot_expiry": vehicle.mot_expiry.isoformat() if vehicle.mot_expiry else None,
        "tax_expiry": vehicle.tax_expiry.isoformat() if vehicle.tax_expiry else None,
        "last_dvla_lookup_date": vehicle.last_dvla_lookup_date.isoformat()
        if vehicle.last_dvla_lookup_date
        else None,
        "description": vehicle.description,
        "is_active": vehicle.is_active,
    }


def group_audit_snapshot(group: Group) -> dict:
    return {
        "id": str(group.id),
        "name": group.name,
        "category": group.category.value,
        "subtype": group.subtype,
        "description": group.description,
    }
