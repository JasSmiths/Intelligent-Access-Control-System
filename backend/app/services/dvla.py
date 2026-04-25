from typing import Any

from app.modules.dvla.vehicle_enquiry import (
    DEFAULT_TEST_REGISTRATION_NUMBER,
    DEFAULT_VEHICLE_ENQUIRY_URL,
    DvlaVehicleEnquiryClient,
)
from app.services.settings import get_runtime_config


async def lookup_vehicle_registration(registration_number: str) -> dict[str, Any]:
    config = await get_runtime_config()
    client = DvlaVehicleEnquiryClient(
        api_key=config.dvla_api_key,
        endpoint_url=config.dvla_vehicle_enquiry_url or DEFAULT_VEHICLE_ENQUIRY_URL,
        timeout_seconds=config.dvla_timeout_seconds,
    )
    return await client.lookup(registration_number)


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
