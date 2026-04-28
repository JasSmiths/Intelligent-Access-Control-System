from dataclasses import dataclass
from datetime import date
from typing import Any

from app.modules.dvla.vehicle_enquiry import (
    DEFAULT_TEST_REGISTRATION_NUMBER,
    DEFAULT_VEHICLE_ENQUIRY_URL,
    DvlaVehicleEnquiryClient,
    display_vehicle_record,
)
from app.services.settings import get_runtime_config


@dataclass(frozen=True)
class NormalizedDvlaVehicle:
    registration_number: str
    make: str | None
    colour: str | None
    mot_status: str | None
    tax_status: str | None
    mot_expiry: date | None
    tax_expiry: date | None

    def as_payload(self) -> dict[str, str | None]:
        return {
            "registration_number": self.registration_number,
            "make": self.make,
            "colour": self.colour,
            "mot_status": self.mot_status,
            "tax_status": self.tax_status,
            "mot_expiry": self.mot_expiry.isoformat() if self.mot_expiry else None,
            "tax_expiry": self.tax_expiry.isoformat() if self.tax_expiry else None,
        }


async def lookup_vehicle_registration(registration_number: str) -> dict[str, Any]:
    config = await get_runtime_config()
    client = DvlaVehicleEnquiryClient(
        api_key=config.dvla_api_key,
        endpoint_url=config.dvla_vehicle_enquiry_url or DEFAULT_VEHICLE_ENQUIRY_URL,
        timeout_seconds=config.dvla_timeout_seconds,
    )
    return await client.lookup(registration_number)


async def lookup_normalized_vehicle_registration(registration_number: str) -> NormalizedDvlaVehicle:
    vehicle = await lookup_vehicle_registration(registration_number)
    return normalize_vehicle_enquiry_response(vehicle, registration_number)


def normalize_vehicle_enquiry_response(
    vehicle: dict[str, Any],
    registration_number: str,
    *,
    display_vehicle: dict[str, Any] | None = None,
) -> NormalizedDvlaVehicle:
    display = display_vehicle or display_vehicle_record(vehicle, registration_number)
    return NormalizedDvlaVehicle(
        registration_number=str(display.get("registrationNumber") or registration_number or "").strip().upper(),
        make=_optional_text(display.get("make")),
        colour=_optional_text(display.get("colour") or display.get("color")),
        mot_status=_optional_text(display.get("motStatus")),
        tax_status=_optional_text(display.get("taxStatus")),
        mot_expiry=_parse_dvla_date(vehicle.get("motExpiryDate") or display.get("motExpiryDate")),
        tax_expiry=_parse_dvla_date(vehicle.get("taxDueDate") or display.get("taxDueDate")),
    )


async def test_vehicle_enquiry_connection(values: dict[str, Any]) -> dict[str, Any]:
    config = await get_runtime_config()
    api_key = str(values.get("dvla_api_key") or config.dvla_api_key or "")
    endpoint_url = str(values.get("dvla_vehicle_enquiry_url") or config.dvla_vehicle_enquiry_url or DEFAULT_VEHICLE_ENQUIRY_URL)
    registration_number = str(
        values.get("dvla_test_registration_number")
        or config.dvla_test_registration_number
        or DEFAULT_TEST_REGISTRATION_NUMBER
    )
    timeout_seconds = float(values.get("dvla_timeout_seconds") or config.dvla_timeout_seconds)
    client = DvlaVehicleEnquiryClient(
        api_key=api_key,
        endpoint_url=endpoint_url,
        timeout_seconds=timeout_seconds,
    )
    return await client.lookup(registration_number)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_dvla_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
