from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.directory import (
    derived_vehicle_person_id,
    normalize_person_pronouns,
    serialize_person,
    serialize_vehicle,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("he/him", "he/him"),
        ("HE/HIM", "he/him"),
        (" she/her ", "she/her"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_person_pronouns_accepts_supported_values(value: str | None, expected: str | None) -> None:
    assert normalize_person_pronouns(value) == expected


def test_normalize_person_pronouns_rejects_unsupported_values() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_person_pronouns("they/them")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Person pronouns must be he/him or she/her."


def test_derived_vehicle_person_id_is_only_set_for_single_assignment() -> None:
    person_id = uuid.uuid4()

    assert derived_vehicle_person_id([]) is None
    assert derived_vehicle_person_id([person_id]) == person_id
    assert derived_vehicle_person_id([person_id, uuid.uuid4()]) is None


def test_serialize_vehicle_exposes_multiple_assigned_people_without_legacy_owner() -> None:
    left = SimpleNamespace(id=uuid.uuid4(), display_name="Zoe Smith")
    right = SimpleNamespace(id=uuid.uuid4(), display_name="Ash Smith")
    vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="SHARED1",
        vehicle_photo_data_url=None,
        description="Shared car",
        make="Tesla",
        model="Model Y",
        color="Blue",
        fuel_type="Electric",
        mot_status="Valid",
        tax_status="Taxed",
        mot_expiry=None,
        tax_expiry=None,
        last_dvla_lookup_date=None,
        person_id=None,
        owner=None,
        person_assignments=[
            SimpleNamespace(person=left),
            SimpleNamespace(person=right),
        ],
        schedule_id=None,
        schedule=None,
        is_active=True,
    )

    payload = serialize_vehicle(vehicle)

    assert payload["person_id"] is None
    assert payload["owner"] is None
    assert payload["owners"] == ["Ash Smith", "Zoe Smith"]
    assert payload["person_ids"] == [str(right.id), str(left.id)]


def test_serialize_person_uses_vehicle_assignment_rows() -> None:
    assigned_vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="SHARED1",
        description="Shared car",
        vehicle_photo_data_url=None,
        make="Tesla",
        model="Model Y",
        color="Blue",
        fuel_type="Electric",
        mot_status="Valid",
        tax_status="Taxed",
        mot_expiry=None,
        tax_expiry=None,
        last_dvla_lookup_date=None,
        schedule_id=None,
        schedule=None,
    )
    legacy_vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="OLD1",
        description=None,
        vehicle_photo_data_url=None,
        make=None,
        model=None,
        color=None,
        fuel_type=None,
        mot_status=None,
        tax_status=None,
        mot_expiry=None,
        tax_expiry=None,
        last_dvla_lookup_date=None,
        schedule_id=None,
        schedule=None,
    )
    person = SimpleNamespace(
        id=uuid.uuid4(),
        first_name="Ash",
        last_name="Smith",
        display_name="Ash Smith",
        pronouns=None,
        profile_photo_data_url=None,
        group_id=None,
        group=None,
        schedule_id=None,
        schedule=None,
        is_active=True,
        notes=None,
        garage_door_entity_ids=[],
        home_assistant_mobile_app_notify_service=None,
        vehicle_assignments=[SimpleNamespace(vehicle=assigned_vehicle)],
        vehicles=[legacy_vehicle],
    )

    payload = serialize_person(person)

    assert [vehicle["registration_number"] for vehicle in payload["vehicles"]] == ["SHARED1"]
