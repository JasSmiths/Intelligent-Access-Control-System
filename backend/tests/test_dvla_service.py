from datetime import date
from types import SimpleNamespace

import pytest

from app.api.v1 import integrations as integrations_api
from app.services.dvla import normalize_vehicle_enquiry_response


def test_normalize_vehicle_enquiry_response_maps_compliance_fields() -> None:
    normalized = normalize_vehicle_enquiry_response(
        {
            "registrationNumber": "pe70dhx",
            "make": "PEUGEOT",
            "colour": "SILVER",
            "motStatus": "VALID",
            "motExpiryDate": "2026-10-14",
            "taxStatus": "TAXED",
            "taxDueDate": "2027-01-01",
        },
        "PE70DHX",
    )

    assert normalized.registration_number == "PE70DHX"
    assert normalized.make == "Peugeot"
    assert normalized.colour == "Silver"
    assert normalized.mot_status == "Valid"
    assert normalized.tax_status == "Taxed"
    assert normalized.mot_expiry == date(2026, 10, 14)
    assert normalized.tax_expiry == date(2027, 1, 1)
    assert normalized.as_payload()["mot_expiry"] == "2026-10-14"


def test_normalize_vehicle_enquiry_response_marks_new_vehicle_mot_not_required() -> None:
    normalized = normalize_vehicle_enquiry_response(
        {
            "registrationNumber": "NEW26",
            "make": "TESLA",
            "colour": "WHITE",
            "dateOfFirstRegistration": "2026-05-05",
            "motStatus": "No details held by DVLA",
            "taxStatus": "TAXED",
        },
        "NEW26",
        today=date(2026, 4, 28),
    )

    assert normalized.mot_status == "Not Required"
    assert normalized.mot_expiry == date(2029, 5, 1)
    assert normalized.as_payload()["mot_expiry"] == "2029-05-01"


def test_normalize_vehicle_enquiry_response_keeps_mot_status_after_first_due_date() -> None:
    normalized = normalize_vehicle_enquiry_response(
        {
            "registrationNumber": "OLD23",
            "make": "FORD",
            "colour": "BLUE",
            "monthOfFirstRegistration": "2023-04",
            "motStatus": "EXPIRED",
            "motExpiryDate": "2026-04-01",
            "taxStatus": "TAXED",
        },
        "OLD23",
        today=date(2026, 4, 28),
    )

    assert normalized.mot_status == "Expired"
    assert normalized.mot_expiry == date(2026, 4, 1)


@pytest.mark.asyncio
async def test_manual_dvla_lookup_preserves_response_shape_and_adds_normalized_view(monkeypatch) -> None:
    async def fake_lookup(registration_number):
        assert registration_number == "PE70DHX"
        return {
            "registrationNumber": registration_number,
            "make": "PEUGEOT",
            "colour": "SILVER",
            "motStatus": "VALID",
            "motExpiryDate": "2026-10-14",
            "taxStatus": "SORN",
        }

    monkeypatch.setattr(integrations_api, "lookup_vehicle_registration", fake_lookup)
    monkeypatch.setattr(integrations_api, "emit_audit_log", lambda **_kwargs: None)

    response = await integrations_api.dvla_lookup(
        integrations_api.DvlaLookupRequest(registration_number="pe70 dhx"),
        user=SimpleNamespace(id=None, username="tester", full_name="Tester"),
    )

    assert response["registration_number"] == "PE70DHX"
    assert response["vehicle"]["make"] == "PEUGEOT"
    assert response["display_vehicle"]["make"] == "Peugeot"
    assert response["normalized_vehicle"] == {
        "registration_number": "PE70DHX",
        "make": "Peugeot",
        "colour": "Silver",
        "mot_status": "Valid",
        "tax_status": "SORN",
        "mot_expiry": "2026-10-14",
        "tax_expiry": None,
    }
