from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.ai import tools as ai_tools
from app.models import VisitorPass
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.services.access_events import AccessEventService
from app.services.visitor_passes import (
    VisitorPassService,
    append_visitor_pass_whatsapp_history,
    serialize_visitor_pass,
    visitor_pass_whatsapp_history,
)


def visitor_pass(
    *,
    name: str = "Sarah",
    expected_time: datetime,
    window_minutes: int = 30,
    status: VisitorPassStatus = VisitorPassStatus.SCHEDULED,
    pass_type: VisitorPassType = VisitorPassType.ONE_TIME,
    visitor_phone: str | None = None,
    created_at: datetime | None = None,
    number_plate: str | None = None,
) -> VisitorPass:
    row = VisitorPass(
        id=uuid.uuid4(),
        visitor_name=name,
        pass_type=pass_type,
        visitor_phone=visitor_phone,
        expected_time=expected_time,
        window_minutes=window_minutes,
        status=status,
        creation_source="ui",
        number_plate=number_plate,
    )
    row.created_at = created_at or expected_time - timedelta(hours=1)
    row.updated_at = row.created_at
    return row


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeVisitorPassSession:
    def __init__(self, rows):
        self._rows = rows

    async def scalars(self, _statement):
        return FakeScalarResult(self._rows)


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


def test_calendar_visitor_pass_uses_asymmetric_valid_window() -> None:
    service = VisitorPassService()
    start = datetime(2026, 4, 29, 11, 0, tzinfo=UTC)
    end = datetime(2026, 4, 29, 12, 15, tzinfo=UTC)
    row = visitor_pass(expected_time=start, window_minutes=30)
    row.valid_from = start - timedelta(minutes=30)
    row.valid_until = end

    assert service.window_start(row) == datetime(2026, 4, 29, 10, 30, tzinfo=UTC)
    assert service.window_end(row) == end
    assert service.status_for(row, start - timedelta(minutes=31)) == VisitorPassStatus.SCHEDULED
    assert service.status_for(row, start - timedelta(minutes=30)) == VisitorPassStatus.ACTIVE
    assert service.status_for(row, end - timedelta(seconds=1)) == VisitorPassStatus.ACTIVE
    assert service.status_for(row, end) == VisitorPassStatus.EXPIRED
    assert not service.is_within_window(row, end)


def test_duration_visitor_pass_uses_explicit_window() -> None:
    service = VisitorPassService()
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    end = datetime(2026, 5, 1, 17, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
    )
    row.valid_from = start
    row.valid_until = end

    assert service.status_for(row, start - timedelta(seconds=1)) == VisitorPassStatus.SCHEDULED
    assert service.status_for(row, start) == VisitorPassStatus.ACTIVE
    assert service.status_for(row, end - timedelta(seconds=1)) == VisitorPassStatus.ACTIVE
    assert service.status_for(row, end) == VisitorPassStatus.EXPIRED


def test_open_departure_lookup_index_covers_duration_passes() -> None:
    index = next(
        index
        for index in VisitorPass.__table__.indexes
        if index.name == "ix_visitor_passes_open_departure_lookup"
    )

    predicate = str(
        index.dialect_options["postgresql"]["where"].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "visitor_passes.status = 'USED'" in predicate
    assert "visitor_passes.pass_type = 'DURATION'" in predicate
    assert "visitor_passes.status = 'ACTIVE'" in predicate
    assert "visitor_passes.departure_time IS NULL" in predicate
    assert "visitor_passes.arrival_time IS NOT NULL" in predicate
    assert "visitor_passes.number_plate IS NOT NULL" in predicate


@pytest.mark.asyncio
async def test_update_one_time_visitor_pass_clears_explicit_window_when_nulls_are_provided(monkeypatch) -> None:
    service = VisitorPassService()
    expected = datetime.now(tz=UTC) + timedelta(days=2)
    row = visitor_pass(expected_time=expected, window_minutes=45, status=VisitorPassStatus.SCHEDULED)
    row.valid_from = expected - timedelta(hours=1)
    row.valid_until = expected + timedelta(hours=2)

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.update_pass(
        SimpleNamespace(),
        row,
        valid_from=None,
        valid_from_provided=True,
        valid_until=None,
        valid_until_provided=True,
    )

    assert row.valid_from is None
    assert row.valid_until is None
    assert service.window_start(row) == expected - timedelta(minutes=45)
    assert service.window_end(row) == expected + timedelta(minutes=45)


@pytest.mark.asyncio
async def test_update_one_time_visitor_pass_keeps_explicit_window_when_fields_are_omitted(monkeypatch) -> None:
    service = VisitorPassService()
    expected = datetime.now(tz=UTC) + timedelta(days=2)
    explicit_start = expected - timedelta(hours=1)
    explicit_end = expected + timedelta(hours=2)
    row = visitor_pass(expected_time=expected, window_minutes=45, status=VisitorPassStatus.SCHEDULED)
    row.valid_from = explicit_start
    row.valid_until = explicit_end

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.update_pass(SimpleNamespace(), row, visitor_name="Sarah Updated")

    assert row.visitor_name == "Sarah Updated"
    assert row.valid_from == explicit_start
    assert row.valid_until == explicit_end


@pytest.mark.asyncio
async def test_update_duration_visitor_pass_to_one_time_clears_duration_window_by_default(monkeypatch) -> None:
    service = VisitorPassService()
    expected = datetime.now(tz=UTC) + timedelta(days=2)
    row = visitor_pass(
        expected_time=expected,
        status=VisitorPassStatus.SCHEDULED,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
    )
    row.valid_from = expected
    row.valid_until = expected + timedelta(hours=8)

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.update_pass(SimpleNamespace(), row, pass_type=VisitorPassType.ONE_TIME)

    assert row.pass_type == VisitorPassType.ONE_TIME
    assert row.visitor_phone is None
    assert row.valid_from is None
    assert row.valid_until is None


@pytest.mark.asyncio
async def test_duration_visitor_pass_arrival_stays_active(monkeypatch) -> None:
    service = VisitorPassService()
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    event = SimpleNamespace(id=uuid.uuid4(), occurred_at=start + timedelta(hours=1), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.record_arrival(SimpleNamespace(), row, event=event)

    assert row.status == VisitorPassStatus.ACTIVE
    assert row.arrival_time == event.occurred_at
    assert row.arrival_event_id == event.id
    assert row.number_plate == "AB12CDE"


@pytest.mark.asyncio
async def test_duration_visitor_pass_claim_does_not_retime_open_visit(monkeypatch) -> None:
    service = VisitorPassService()
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    first_arrival = start + timedelta(minutes=10)
    first_event_id = uuid.uuid4()
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    row.arrival_time = first_arrival
    row.arrival_event_id = first_event_id

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    matched = await service.claim_active_pass(
        FakeVisitorPassSession([row]),
        occurred_at=start + timedelta(minutes=25),
        registration_number="AB12 CDE",
    )

    assert matched is row
    assert row.arrival_time == first_arrival
    assert row.arrival_event_id == first_event_id
    assert row.departure_time is None


@pytest.mark.asyncio
async def test_duration_visitor_pass_arrival_links_open_claim_without_retiming(monkeypatch) -> None:
    service = VisitorPassService()
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    first_arrival = start + timedelta(minutes=10)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    row.arrival_time = first_arrival
    event = SimpleNamespace(id=uuid.uuid4(), occurred_at=start + timedelta(minutes=12), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.record_arrival(SimpleNamespace(), row, event=event, trace_id="trace-1")

    assert row.arrival_time == first_arrival
    assert row.arrival_event_id == event.id
    assert row.telemetry_trace_id == "trace-1"


@pytest.mark.asyncio
async def test_duration_visitor_pass_repeated_arrival_does_not_overwrite_open_visit(monkeypatch) -> None:
    service = VisitorPassService()
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    first_arrival = start + timedelta(minutes=10)
    first_event_id = uuid.uuid4()
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    row.arrival_time = first_arrival
    row.arrival_event_id = first_event_id
    row.telemetry_trace_id = "trace-first"
    event = SimpleNamespace(id=uuid.uuid4(), occurred_at=start + timedelta(minutes=25), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.record_arrival(SimpleNamespace(), row, event=event, trace_id="trace-second")

    assert row.arrival_time == first_arrival
    assert row.arrival_event_id == first_event_id
    assert row.telemetry_trace_id == "trace-first"
    assert row.departure_time is None


@pytest.mark.asyncio
async def test_duration_visitor_pass_return_after_departure_starts_new_visit(monkeypatch) -> None:
    service = VisitorPassService()
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    first_arrival = start + timedelta(minutes=10)
    first_departure = start + timedelta(hours=1)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    row.arrival_time = first_arrival
    row.arrival_event_id = uuid.uuid4()
    row.departure_time = first_departure
    row.departure_event_id = uuid.uuid4()
    row.duration_on_site_seconds = 3000
    row.telemetry_trace_id = "trace-first"
    event = SimpleNamespace(id=uuid.uuid4(), occurred_at=start + timedelta(hours=2), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.record_arrival(SimpleNamespace(), row, event=event, trace_id="trace-return")

    assert row.arrival_time == event.occurred_at
    assert row.arrival_event_id == event.id
    assert row.departure_time is None
    assert row.departure_event_id is None
    assert row.duration_on_site_seconds is None
    assert row.telemetry_trace_id == "trace-return"


@pytest.mark.asyncio
async def test_update_visitor_plate_saves_dvla_vehicle_details_and_clears_stale_details(monkeypatch) -> None:
    service = VisitorPassService()
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="OLD123",
    )
    row.vehicle_make = "Ford"
    row.vehicle_colour = "Blue"

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)

    await service.update_visitor_plate(
        SimpleNamespace(),
        row,
        new_plate="AB12 CDE",
        vehicle_make="Tesla",
        vehicle_colour="Silver",
    )

    assert row.number_plate == "AB12CDE"
    assert row.vehicle_make == "Tesla"
    assert row.vehicle_colour == "Silver"

    await service.update_visitor_plate(SimpleNamespace(), row, new_plate="CD34 EFG")

    assert row.number_plate == "CD34EFG"
    assert row.vehicle_make is None
    assert row.vehicle_colour is None


def test_serialize_visitor_pass_includes_concierge_fields() -> None:
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)

    payload = serialize_visitor_pass(row, timezone_name="UTC")

    assert payload["pass_type"] == "duration"
    assert payload["visitor_phone"] == "447700900123"
    assert payload["number_plate"] == "AB12CDE"
    assert payload["valid_from"] == "2026-05-01T09:00:00+00:00"
    assert payload["whatsapp_status"] == "complete"
    assert payload["whatsapp_status_label"] == "Complete - Vehicle Registration: AB12CDE"


def test_serialize_visitor_pass_shows_requested_time_change_before_complete() -> None:
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    row.source_metadata = {
        "whatsapp_concierge_status": "timeframe_confirmation_pending",
        "whatsapp_concierge_status_detail": "Awaiting visitor confirmation for the requested timeframe change.",
    }

    payload = serialize_visitor_pass(row, timezone_name="UTC")

    assert payload["whatsapp_status"] == "timeframe_confirmation_pending"
    assert payload["whatsapp_status_label"] == "Requested Time Change"


def test_serialize_visitor_pass_shows_awaiting_time_change_approval_before_complete() -> None:
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    row.source_metadata = {
        "whatsapp_concierge_status": "timeframe_approval_pending",
        "whatsapp_concierge_status_detail": "Visitor requested a timeframe change that needs Admin approval.",
    }

    payload = serialize_visitor_pass(row, timezone_name="UTC")

    assert payload["whatsapp_status"] == "timeframe_approval_pending"
    assert payload["whatsapp_status_label"] == "Awaiting Time Change Approval"


def test_visitor_pass_whatsapp_history_is_serialized_from_metadata() -> None:
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
    )

    append_visitor_pass_whatsapp_history(
        row,
        direction="inbound",
        body="My registration is AB12 CDE",
        actor_label="Sarah",
        occurred_at=start,
    )
    append_visitor_pass_whatsapp_history(
        row,
        direction="outbound",
        body="Please confirm AB12CDE",
        actor_label="IACS",
        occurred_at=start + timedelta(minutes=1),
    )

    history = visitor_pass_whatsapp_history(row)

    assert [message["direction"] for message in history] == ["inbound", "outbound"]
    assert history[0]["body"] == "My registration is AB12 CDE"
    assert history[1]["actor_label"] == "IACS"


def test_serialize_visitor_pass_complete_label_includes_time_updated_after_change() -> None:
    start = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    row = visitor_pass(
        expected_time=start,
        status=VisitorPassStatus.ACTIVE,
        pass_type=VisitorPassType.DURATION,
        visitor_phone="447700900123",
        number_plate="AB12CDE",
    )
    row.valid_from = start
    row.valid_until = start + timedelta(hours=8)
    row.source_metadata = {
        "whatsapp_concierge_status": "timeframe_approved",
        "whatsapp_timeframe_request": {"status": "approved"},
    }

    payload = serialize_visitor_pass(row, timezone_name="UTC")

    assert payload["whatsapp_status"] == "complete"
    assert payload["whatsapp_status_label"] == "Complete - Vehicle Registration: AB12CDE Time Updated"


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
