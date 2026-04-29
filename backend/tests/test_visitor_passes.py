from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest

from app.ai import tools as ai_tools
from app.models import VisitorPass
from app.models.enums import VisitorPassStatus
from app.services.access_events import AccessEventService
from app.services.visitor_passes import VisitorPassService


def visitor_pass(
    *,
    name: str = "Sarah",
    expected_time: datetime,
    window_minutes: int = 30,
    status: VisitorPassStatus = VisitorPassStatus.SCHEDULED,
    created_at: datetime | None = None,
    number_plate: str | None = None,
) -> VisitorPass:
    row = VisitorPass(
        id=uuid.uuid4(),
        visitor_name=name,
        expected_time=expected_time,
        window_minutes=window_minutes,
        status=status,
        creation_source="ui",
        number_plate=number_plate,
    )
    row.created_at = created_at or expected_time - timedelta(hours=1)
    row.updated_at = row.created_at
    return row


def test_visitor_pass_lifecycle_statuses() -> None:
    service = VisitorPassService()
    expected = datetime(2026, 4, 29, 15, 0, tzinfo=UTC)
    row = visitor_pass(expected_time=expected, window_minutes=30)

    assert service.status_for(row, expected - timedelta(minutes=31)) == VisitorPassStatus.SCHEDULED
    assert service.status_for(row, expected - timedelta(minutes=30)) == VisitorPassStatus.ACTIVE
    assert service.status_for(row, expected + timedelta(minutes=30)) == VisitorPassStatus.ACTIVE
    assert service.status_for(row, expected + timedelta(minutes=31)) == VisitorPassStatus.EXPIRED

    row.status = VisitorPassStatus.USED
    assert service.status_for(row, expected + timedelta(days=1)) == VisitorPassStatus.USED


def test_overlap_matching_prefers_closest_expected_time_then_oldest_created() -> None:
    service = VisitorPassService()
    now = datetime(2026, 4, 29, 15, 0, tzinfo=UTC)
    older = visitor_pass(
        name="Older",
        expected_time=now + timedelta(minutes=5),
        status=VisitorPassStatus.ACTIVE,
        created_at=now - timedelta(hours=2),
    )
    newer = visitor_pass(
        name="Newer",
        expected_time=now + timedelta(minutes=5),
        status=VisitorPassStatus.ACTIVE,
        created_at=now - timedelta(hours=1),
    )
    closer = visitor_pass(
        name="Closer",
        expected_time=now + timedelta(minutes=2),
        status=VisitorPassStatus.ACTIVE,
        created_at=now,
    )

    assert service.select_best_active_match([older, newer, closer], now) is closer
    assert service.select_best_active_match([newer, older], now) is older


def test_expired_and_cancelled_passes_do_not_match() -> None:
    service = VisitorPassService()
    now = datetime(2026, 4, 29, 15, 0, tzinfo=UTC)
    expired = visitor_pass(expected_time=now, status=VisitorPassStatus.EXPIRED)
    cancelled = visitor_pass(expected_time=now, status=VisitorPassStatus.CANCELLED)

    assert service.select_best_active_match([expired, cancelled], now) is None


@pytest.mark.asyncio
async def test_lpr_visitor_pass_suppresses_unknown_plate_anomaly() -> None:
    service = AccessEventService()
    event = SimpleNamespace(registration_number="PE70DHX")
    row = visitor_pass(
        expected_time=datetime(2026, 4, 29, 15, 0, tzinfo=UTC),
        status=VisitorPassStatus.USED,
        number_plate="PE70DHX",
    )

    anomalies = await service._build_anomalies(
        SimpleNamespace(),
        event,
        person=None,
        vehicle=None,
        allowed=True,
        visitor_pass=row,
    )

    assert anomalies == []


@pytest.mark.asyncio
async def test_departure_duration_is_recorded_for_same_plate() -> None:
    service = VisitorPassService()
    arrival = datetime(2026, 4, 29, 15, 0, tzinfo=UTC)
    departure = arrival + timedelta(hours=1, minutes=25)
    row = visitor_pass(expected_time=arrival, status=VisitorPassStatus.USED, number_plate="PE70DHX")
    row.arrival_time = arrival
    event = SimpleNamespace(id=uuid.uuid4(), occurred_at=departure, registration_number="PE70DHX")

    await service.record_departure(SimpleNamespace(), row, event=event)

    assert row.departure_time == departure
    assert row.departure_event_id == event.id
    assert row.duration_on_site_seconds == 5100


def test_api_status_filter_parser_accepts_multi_status_values() -> None:
    statuses = ai_tools._visitor_pass_statuses_from_arguments(
        {"statuses": ["active", "scheduled", "used", "bogus"]}
    )

    assert statuses == [
        VisitorPassStatus.ACTIVE,
        VisitorPassStatus.SCHEDULED,
        VisitorPassStatus.USED,
    ]


@pytest.mark.asyncio
async def test_alfred_create_visitor_pass_requires_confirmation(monkeypatch) -> None:
    async def fake_runtime_config():
        return SimpleNamespace(site_timezone="Europe/London")

    monkeypatch.setattr(ai_tools, "get_runtime_config", fake_runtime_config)

    result = await ai_tools.create_visitor_pass(
        {
            "visitor_name": "Sarah",
            "expected_time": "2027-04-29T15:00:00+01:00",
            "window_minutes": 30,
            "confirm": False,
        }
    )

    assert result["requires_confirmation"] is True
    assert result["confirmation_field"] == "confirm"
    assert result["visitor_name"] == "Sarah"
    assert "Europe/London" not in result["detail"]
    assert result["expected_time_display"] == "29 Apr 2027, 15:00"
