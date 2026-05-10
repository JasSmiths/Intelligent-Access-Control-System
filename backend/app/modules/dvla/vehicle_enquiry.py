import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_VEHICLE_ENQUIRY_URL = "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles"
DEFAULT_TEST_REGISTRATION_NUMBER = "AA19AAA"
ACRONYMS = {
    "BMW",
    "BYD",
    "DAF",
    "DS",
    "DVLA",
    "GMC",
    "JCB",
    "KGM",
    "KTM",
    "LDV",
    "MAN",
    "MG",
    "MOT",
    "TVR",
    "SORN",
    "VW",
}
SMALL_WORDS = {"and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
DISPLAY_TEXT_FIELDS = {
    "make",
    "colour",
    "fuelType",
    "taxStatus",
    "motStatus",
    "euroStatus",
    "typeApproval",
}


class DvlaVehicleEnquiryError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DvlaVehicleEnquiryClient:
    api_key: str
    endpoint_url: str = DEFAULT_VEHICLE_ENQUIRY_URL
    timeout_seconds: float = 10.0

    async def lookup(self, registration_number: str) -> dict[str, Any]:
        vrn = normalize_registration_number(registration_number)
        if not vrn:
            raise DvlaVehicleEnquiryError("Vehicle registration number is required.", status_code=400)
        if not self.api_key:
            raise DvlaVehicleEnquiryError("DVLA API key is not configured.", status_code=400)

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Correlation-Id": str(uuid.uuid4()),
            "x-api-key": self.api_key,
        }
        payload = {"registrationNumber": vrn}
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            try:
                response = await client.post(self.endpoint_url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                raise DvlaVehicleEnquiryError(f"DVLA lookup failed: {exc}") from exc

        if response.status_code >= 400:
            raise DvlaVehicleEnquiryError(
                _error_detail(response),
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise DvlaVehicleEnquiryError("DVLA returned a non-JSON response.") from exc
        if not isinstance(data, dict):
            raise DvlaVehicleEnquiryError("DVLA returned an unexpected response shape.")
        return data


def normalize_registration_number(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def display_vehicle_record(vehicle: dict[str, Any], registration_number: str | None = None) -> dict[str, Any]:
    display = dict(vehicle)
    if registration_number:
        display["registrationNumber"] = normalize_registration_number(registration_number)
    for key in DISPLAY_TEXT_FIELDS:
        if isinstance(display.get(key), str):
            display[key] = friendly_vehicle_text(display[key])
    return display


def friendly_vehicle_text(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return ""
    upper = cleaned.upper()
    if upper in ACRONYMS:
        return upper
    return " ".join(
        _friendly_hyphenated_word(part, is_first=index == 0)
        for index, part in enumerate(cleaned.split(" "))
    )


def _friendly_hyphenated_word(value: str, *, is_first: bool) -> str:
    return "-".join(
        _friendly_word(part, is_first=is_first and index == 0)
        for index, part in enumerate(value.split("-"))
    )


def _friendly_word(value: str, *, is_first: bool) -> str:
    if not value:
        return value
    upper = value.upper()
    if upper in ACRONYMS:
        return upper
    lower = value.lower()
    if not is_first and lower in SMALL_WORDS:
        return lower
    if value.isdigit():
        return value
    return upper[:1] + upper[1:].lower()


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"DVLA returned HTTP {response.status_code}: {response.text[:180]}"

    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                detail = first.get("detail") or first.get("title") or first.get("code")
                if detail:
                    return f"DVLA returned HTTP {response.status_code}: {detail}"
        message = payload.get("message") or payload.get("detail") or payload.get("title")
        if message:
            return f"DVLA returned HTTP {response.status_code}: {message}"

    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        message = payload[0].get("detail") or payload[0].get("title")
        if message:
            return f"DVLA returned HTTP {response.status_code}: {message}"

    return f"DVLA returned HTTP {response.status_code}."
