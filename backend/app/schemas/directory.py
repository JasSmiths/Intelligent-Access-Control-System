import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import GroupCategory
from app.services.person_presence_input_booleans import DEFAULT_INPUT_BOOLEAN_ACTION


class PersonVehicleResponse(BaseModel):
    id: str
    registration_number: str
    description: str | None
    vehicle_photo_data_url: str | None
    vehicle_photo_url: str | None = None
    make: str | None
    model: str | None
    color: str | None
    fuel_type: str | None = None
    mot_status: str | None = None
    tax_status: str | None = None
    mot_expiry: date | None = None
    tax_expiry: date | None = None
    last_dvla_lookup_date: date | None = None
    schedule_id: str | None = None
    schedule: str | None = None


class PersonResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    display_name: str
    pronouns: str | None
    profile_photo_data_url: str | None
    profile_photo_url: str | None = None
    group_id: str | None
    group: str | None
    category: str | None
    schedule_id: str | None
    schedule: str | None
    is_active: bool
    notes: str | None
    garage_door_entity_ids: list[str]
    home_assistant_mobile_app_notify_service: str | None
    missed_exit_recovery_enabled: bool = False
    missed_exit_recovery_tracker_entity_id: str | None = None
    home_assistant_presence_input_boolean_entity_ids: list[str]
    home_assistant_presence_input_boolean_entry_action: Literal["turn_on", "turn_off"]
    home_assistant_presence_input_boolean_exit_action: Literal["turn_on", "turn_off"]
    vehicles: list[PersonVehicleResponse]


class CreatePersonRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    pronouns: str | None = Field(default=None, max_length=24)
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    group_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    vehicle_ids: list[uuid.UUID] = Field(default_factory=list)
    garage_door_entity_ids: list[str] = Field(default_factory=list)
    home_assistant_mobile_app_notify_service: str | None = Field(default=None, max_length=255)
    missed_exit_recovery_enabled: bool = False
    missed_exit_recovery_tracker_entity_id: str | None = Field(default=None, max_length=255)
    home_assistant_presence_input_boolean_entity_ids: list[str] = Field(default_factory=list)
    home_assistant_presence_input_boolean_entry_action: str = Field(
        default=DEFAULT_INPUT_BOOLEAN_ACTION,
        pattern="^(turn_on|turn_off)$",
    )
    home_assistant_presence_input_boolean_exit_action: str = Field(
        default=DEFAULT_INPUT_BOOLEAN_ACTION,
        pattern="^(turn_on|turn_off)$",
    )
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True
    confirmation_token: str | None = Field(default=None, max_length=160)


class UpdatePersonRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    pronouns: str | None = Field(default=None, max_length=24)
    profile_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    group_id: uuid.UUID | None = None
    schedule_id: uuid.UUID | None = None
    vehicle_ids: list[uuid.UUID] | None = None
    garage_door_entity_ids: list[str] | None = None
    home_assistant_mobile_app_notify_service: str | None = Field(default=None, max_length=255)
    missed_exit_recovery_enabled: bool | None = None
    missed_exit_recovery_tracker_entity_id: str | None = Field(default=None, max_length=255)
    home_assistant_presence_input_boolean_entity_ids: list[str] | None = None
    home_assistant_presence_input_boolean_entry_action: str | None = Field(
        default=None,
        pattern="^(turn_on|turn_off)$",
    )
    home_assistant_presence_input_boolean_exit_action: str | None = Field(
        default=None,
        pattern="^(turn_on|turn_off)$",
    )
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class VehicleResponse(BaseModel):
    id: str
    registration_number: str
    vehicle_photo_data_url: str | None
    vehicle_photo_url: str | None = None
    description: str | None
    make: str | None
    model: str | None
    color: str | None
    fuel_type: str | None
    mot_status: str | None
    tax_status: str | None
    mot_expiry: date | None
    tax_expiry: date | None
    last_dvla_lookup_date: date | None
    person_id: str | None
    owner: str | None
    person_ids: list[str]
    owners: list[str]
    schedule_id: str | None
    schedule: str | None
    is_active: bool


class CreateVehicleRequest(BaseModel):
    registration_number: str = Field(min_length=1, max_length=32)
    vehicle_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=80)
    fuel_type: str | None = Field(default=None, max_length=80)
    mot_status: str | None = Field(default=None, max_length=80)
    tax_status: str | None = Field(default=None, max_length=80)
    mot_expiry: date | None = None
    tax_expiry: date | None = None
    last_dvla_lookup_date: date | None = None
    description: str | None = Field(default=None, max_length=255)
    person_id: uuid.UUID | None = None
    person_ids: list[uuid.UUID] | None = None
    schedule_id: uuid.UUID | None = None
    is_active: bool = True
    confirmation_token: str | None = Field(default=None, max_length=160)


class UpdateVehicleRequest(BaseModel):
    registration_number: str | None = Field(default=None, min_length=1, max_length=32)
    vehicle_photo_data_url: str | None = Field(default=None, max_length=11_200_000)
    make: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, max_length=80)
    fuel_type: str | None = Field(default=None, max_length=80)
    mot_status: str | None = Field(default=None, max_length=80)
    tax_status: str | None = Field(default=None, max_length=80)
    mot_expiry: date | None = None
    tax_expiry: date | None = None
    last_dvla_lookup_date: date | None = None
    description: str | None = Field(default=None, max_length=255)
    person_id: uuid.UUID | None = None
    person_ids: list[uuid.UUID] | None = None
    schedule_id: uuid.UUID | None = None
    is_active: bool | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class GroupResponse(BaseModel):
    id: str
    name: str
    category: str
    subtype: str | None
    description: str | None
    people_count: int


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: GroupCategory
    subtype: str | None = Field(default=None, max_length=120)
    description: str | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class UpdateGroupRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: GroupCategory | None = None
    subtype: str | None = Field(default=None, max_length=120)
    description: str | None = None
    confirmation_token: str | None = Field(default=None, max_length=160)


class DirectoryConfirmationRequest(BaseModel):
    confirmation_token: str | None = Field(default=None, max_length=160)


class DirectoryPage[Item](BaseModel):
    items: list[Item]
    total: int
    next_cursor: str | None
