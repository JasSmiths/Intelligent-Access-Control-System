from types import SimpleNamespace as _SimpleNamespace
from typing import Any, cast
import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.directory import (
    derived_vehicle_person_id,
    normalize_person_presence_input_boolean_action,
    normalize_person_presence_input_boolean_entity_ids,
    normalize_person_pronouns,
    serialize_person,
    serialize_vehicle,
)

SimpleNamespace = cast(Any, _SimpleNamespace)


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


def test_normalize_presence_input_boolean_entity_ids_trims_and_dedupes() -> None:
    assert normalize_person_presence_input_boolean_entity_ids(
        [" input_boolean.person ", "input_boolean.person", "input_boolean.announcements"]
    ) == ["input_boolean.person", "input_boolean.announcements"]


def test_normalize_presence_input_boolean_entity_ids_rejects_non_input_boolean() -> None:
    with pytest.raises(HTTPException) as exc_info:
        normalize_person_presence_input_boolean_entity_ids(["switch.person"])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Home Assistant presence entity IDs must start with input_boolean."


def test_normalize_presence_input_boolean_action_defaults_and_validates() -> None:
    assert normalize_person_presence_input_boolean_action(None) == "turn_off"
    assert normalize_person_presence_input_boolean_action("turn_on") == "turn_on"

    with pytest.raises(HTTPException) as exc_info:
        normalize_person_presence_input_boolean_action("toggle")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Home Assistant presence input_boolean action must be turn_on or turn_off."


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
        vehicle_photo_data_url="data:image/png;base64,vehicle",
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
    assert payload["vehicle_photo_data_url"] == "data:image/png;base64,vehicle"
    assert payload["vehicle_photo_url"] == f"/api/v1/vehicles/{vehicle.id}/photo"
    compact_payload = serialize_vehicle(vehicle, include_media=False)
    assert compact_payload["vehicle_photo_data_url"] is None
    assert compact_payload["vehicle_photo_url"] == f"/api/v1/vehicles/{vehicle.id}/photo"


def test_serialize_vehicle_uses_snapshot_fallback_when_photo_missing() -> None:
    vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="AGS7X",
        vehicle_photo_data_url=None,
        description=None,
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
        person_assignments=[],
        schedule_id=None,
        schedule=None,
        is_active=True,
    )

    payload = serialize_vehicle(
        vehicle,
        include_media=False,
        fallback_photo_urls={"AGS7X": "/api/v1/events/event-id/snapshot"},
    )

    assert payload["vehicle_photo_data_url"] is None
    assert payload["vehicle_photo_url"] == "/api/v1/events/event-id/snapshot"


def test_serialize_person_uses_vehicle_assignment_rows() -> None:
    assigned_vehicle = SimpleNamespace(
        id=uuid.uuid4(),
        registration_number="SHARED1",
        description="Shared car",
        vehicle_photo_data_url="data:image/png;base64,vehicle",
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
        profile_photo_data_url="data:image/png;base64,person",
        group_id=None,
        group=None,
        schedule_id=None,
        schedule=None,
        is_active=True,
        notes=None,
        garage_door_entity_ids=[],
        home_assistant_mobile_app_notify_service=None,
        home_assistant_presence_input_boolean_entity_ids=["input_boolean.ash_home"],
        home_assistant_presence_input_boolean_entry_action="turn_on",
        home_assistant_presence_input_boolean_exit_action="turn_off",
        vehicle_assignments=[SimpleNamespace(vehicle=assigned_vehicle)],
        vehicles=[legacy_vehicle],
    )

    payload = serialize_person(person)

    assert [vehicle["registration_number"] for vehicle in payload["vehicles"]] == ["SHARED1"]
    assert payload["home_assistant_presence_input_boolean_entity_ids"] == ["input_boolean.ash_home"]
    assert payload["home_assistant_presence_input_boolean_entry_action"] == "turn_on"
    assert payload["home_assistant_presence_input_boolean_exit_action"] == "turn_off"
    assert payload["profile_photo_data_url"] == "data:image/png;base64,person"
    assert payload["profile_photo_url"] == f"/api/v1/people/{person.id}/photo"
    assert payload["vehicles"][0]["vehicle_photo_data_url"] == "data:image/png;base64,vehicle"
    assert payload["vehicles"][0]["vehicle_photo_url"] == f"/api/v1/vehicles/{assigned_vehicle.id}/photo"
    compact_payload = serialize_person(person, include_media=False)
    assert compact_payload["profile_photo_data_url"] is None
    assert compact_payload["profile_photo_url"] == f"/api/v1/people/{person.id}/photo"
    assert compact_payload["vehicles"][0]["vehicle_photo_data_url"] is None
    assert compact_payload["vehicles"][0]["vehicle_photo_url"] == f"/api/v1/vehicles/{assigned_vehicle.id}/photo"
