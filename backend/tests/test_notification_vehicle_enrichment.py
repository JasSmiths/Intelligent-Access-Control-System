"""Unknown-vehicle notices own their optional enrichment before rendering."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.services import notifications as owner

pytestmark = pytest.mark.asyncio


def notice(**facts):
    return SimpleNamespace(id=uuid.uuid4(), rules_override=[{
        "id": "unknown", "name": "Unknown", "trigger_event": "unauthorized_plate",
        "is_active": True, "conditions": [], "actions": [{
            "type": "in_app", "title_template": "Unknown vehicle",
            "message_template": "@VehicleColour @VehicleMake @Registration",
        }],
    }], context={"event_type": "unauthorized_plate", "subject": "AB12CDE", "severity": "warning",
                "facts": {"access_event_id": str(uuid.uuid4()), "registration_number": "AB12CDE",
                          "vehicle_make": "", "vehicle_colour": "", **facts}})


async def test_unknown_notice_enriches_before_rendering(monkeypatch):
    lookup = AsyncMock(return_value=SimpleNamespace(make="FORD", colour="BLUE"))
    monkeypatch.setattr(owner, "lookup_normalized_vehicle_registration", lookup)
    row = notice()
    plan = await owner.NotificationService().prepare_delivery_plan(row)
    assert plan[0]["action"]["message"] == "BLUE FORD AB12CDE"
    assert row.context["facts"]["vehicle_make"] == "FORD"
    assert row.context["facts"]["vehicle_color"] == "BLUE"
    lookup.assert_awaited_once_with("AB12CDE")


@pytest.mark.parametrize("error", [RuntimeError("provider unavailable"), TimeoutError()])
async def test_failed_lookup_still_prepares_alert(monkeypatch, error):
    monkeypatch.setattr(owner, "lookup_normalized_vehicle_registration", AsyncMock(side_effect=error))
    plan = await owner.NotificationService().prepare_delivery_plan(notice())
    assert plan[0]["state"] == "pending"
    assert "AB12CDE" in plan[0]["action"]["message"]


async def test_existing_colour_is_preserved(monkeypatch):
    monkeypatch.setattr(owner, "lookup_normalized_vehicle_registration",
                        AsyncMock(return_value=SimpleNamespace(make="FORD", colour="BLUE")))
    row = notice(vehicle_colour="Silver", vehicle_color="Silver")
    await owner.NotificationService().prepare_delivery_plan(row)
    assert row.context["facts"]["vehicle_colour"] == "Silver"


async def test_complete_details_skip_lookup(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(owner, "lookup_normalized_vehicle_registration", lookup)
    await owner.NotificationService().prepare_delivery_plan(notice(vehicle_make="FORD", vehicle_colour="BLUE"))
    lookup.assert_not_awaited()


async def test_cancellation_propagates(monkeypatch):
    monkeypatch.setattr(owner, "lookup_normalized_vehicle_registration", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await owner.NotificationService().prepare_delivery_plan(notice())
