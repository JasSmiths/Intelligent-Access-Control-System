from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as _SimpleNamespace
from typing import Any, cast
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models import VisitorPass
from app.models.enums import AccessDecision, AccessDirection, VisitorPassStatus, VisitorPassType
from app.services.access.execution import AccessExecution
from app.services.domain_events import publish_visitor_pass_status_changed
from app.services.visitor_passes import (
    VisitorPassService,
    VisitorPassError,
    serialize_visitor_pass,
)

SimpleNamespace = cast(Any, _SimpleNamespace)


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


@pytest.mark.asyncio
async def test_visitor_pass_status_changed_domain_event_preserves_payload_shape() -> None:
    published: list[tuple[str, dict]] = []

    class FakeBus:
        async def publish(self, event_type: str, payload: dict) -> None:
            published.append((event_type, payload))

    visitor_payload = {"id": "pass-1", "status": "expired", "visitor_name": "Sarah"}

    await publish_visitor_pass_status_changed(visitor_payload, bus=FakeBus())

    assert published == [("visitor_pass.status_changed", {"visitor_pass": visitor_payload})]


@pytest.mark.asyncio
async def test_refresh_statuses_publishes_status_changed_event_payload(monkeypatch) -> None:
    service = VisitorPassService()
    expected = datetime(2026, 4, 29, 15, 0, tzinfo=UTC)
    row = visitor_pass(expected_time=expected, window_minutes=30)
    published: list[tuple[str, dict]] = []

    async def audit_noop(*_args, **_kwargs):
        return None

    async def fake_publish(event_type: str, payload: dict) -> None:
        published.append((event_type, payload))

    monkeypatch.setattr(service, "_audit_change", audit_noop)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))
    monkeypatch.setattr("app.services.visitor_passes.event_bus.publish", fake_publish)

    changed = await service.refresh_statuses(
        session=FakeVisitorPassSession([row]),
        now=expected + timedelta(minutes=31),
        publish=True,
    )

    assert changed == [row]
    assert published == [
        ("visitor_pass.status_changed", {"visitor_pass": serialize_visitor_pass(row)})
    ]


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
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

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
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

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
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

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
    event = SimpleNamespace(decision=AccessDecision.GRANTED, direction=AccessDirection.ENTRY, id=uuid.uuid4(), occurred_at=start + timedelta(hours=1), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

    await service._record_verified_arrival(SimpleNamespace(), row, event=event)

    assert row.status == VisitorPassStatus.ACTIVE
    assert row.arrival_time == event.occurred_at
    assert row.arrival_event_id == event.id
    assert row.number_plate == "AB12CDE"


@pytest.mark.asyncio
async def test_duration_visitor_pass_candidate_selection_does_not_retime_open_visit(monkeypatch) -> None:
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
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

    matched = await service.find_arrival_candidate(
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
    event = SimpleNamespace(decision=AccessDecision.GRANTED, direction=AccessDirection.ENTRY, id=uuid.uuid4(), occurred_at=start + timedelta(minutes=12), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

    await service._record_verified_arrival(SimpleNamespace(), row, event=event, trace_id="trace-1")

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
    event = SimpleNamespace(decision=AccessDecision.GRANTED, direction=AccessDirection.ENTRY, id=uuid.uuid4(), occurred_at=start + timedelta(minutes=25), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

    await service._record_verified_arrival(SimpleNamespace(), row, event=event, trace_id="trace-second")

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
    event = SimpleNamespace(decision=AccessDecision.GRANTED, direction=AccessDirection.ENTRY, id=uuid.uuid4(), occurred_at=start + timedelta(hours=2), registration_number="AB12 CDE")

    async def audit_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_audit_change", audit_noop)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

    await service._record_verified_arrival(SimpleNamespace(), row, event=event, trace_id="trace-return")

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
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, row, **_kw: row))

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
    service = AccessExecution(None, None, None)
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
async def test_departure_duration_is_recorded_for_same_plate(monkeypatch) -> None:
    service = VisitorPassService()
    arrival = datetime(2026, 4, 29, 15, 0, tzinfo=UTC)
    departure = arrival + timedelta(hours=1, minutes=25)
    row = visitor_pass(expected_time=arrival, status=VisitorPassStatus.USED, number_plate="PE70DHX")
    row.arrival_time = arrival
    event = SimpleNamespace(id=uuid.uuid4(), occurred_at=departure, registration_number="PE70DHX")

    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(return_value=row))
    monkeypatch.setattr(service, "_audit_change", AsyncMock())
    await service.record_departure(SimpleNamespace(), row, event=event)

    assert row.departure_time == departure
    assert row.departure_event_id == event.id
    assert row.duration_on_site_seconds == 5100




@pytest.mark.asyncio
async def test_new_duration_pass_requires_plate_before_persistence():
    service = VisitorPassService()
    start = datetime.now(tz=UTC) + timedelta(days=1)
    session = SimpleNamespace(add=AsyncMock(), flush=AsyncMock())
    with pytest.raises(VisitorPassError, match="valid vehicle registration"):
        await service.create_pass(session, visitor_name="Visitor", pass_type=VisitorPassType.DURATION,
            valid_from=start, valid_until=start + timedelta(hours=2))
    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.asyncio
async def test_manual_duration_pass_normalizes_plate_without_contact_or_concierge(monkeypatch):
    service = VisitorPassService()
    start = datetime.now(tz=UTC) + timedelta(days=1)
    rows = []
    session = SimpleNamespace(add=rows.append, flush=AsyncMock())
    audit = AsyncMock()
    monkeypatch.setattr(service, "_audit_change", audit)
    row = await service.create_pass(session, visitor_name="Visitor", pass_type=VisitorPassType.DURATION,
        number_plate="ab12 cde", valid_from=start, valid_until=start + timedelta(hours=2), actor="Admin")
    assert row.number_plate == "AB12CDE"
    assert row.visitor_phone is None
    assert row.source_metadata is None
    assert rows == [row]
    assert audit.await_args.kwargs["action"] == "visitor_pass.create"


@pytest.mark.asyncio
async def test_manual_plate_edit_uses_audited_plate_owner(monkeypatch):
    service = VisitorPassService()
    start = datetime.now(tz=UTC) + timedelta(days=1)
    row = visitor_pass(expected_time=start, status=VisitorPassStatus.SCHEDULED,
        pass_type=VisitorPassType.DURATION, number_plate="AB12CDE")
    row.valid_from, row.valid_until = start, start + timedelta(hours=2)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, item, **_kw: item))
    audit = AsyncMock()
    monkeypatch.setattr(service, "_audit_change", audit)
    await service.update_pass(SimpleNamespace(), row, number_plate="xy34 zzz", number_plate_provided=True, actor="Admin")
    assert row.number_plate == "XY34ZZZ"
    assert [call.kwargs["action"] for call in audit.await_args_list] == ["visitor_pass.vehicle_plate_update", "visitor_pass.update"]


@pytest.mark.asyncio
async def test_legacy_unplated_duration_pass_retains_ordinary_editing(monkeypatch):
    service = VisitorPassService()
    start = datetime.now(tz=UTC) + timedelta(days=1)
    row = visitor_pass(expected_time=start, status=VisitorPassStatus.SCHEDULED, pass_type=VisitorPassType.DURATION)
    row.valid_from, row.valid_until = start, start + timedelta(hours=2)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, item, **_kw: item))
    monkeypatch.setattr(service, "_audit_change", AsyncMock())
    await service.update_pass(SimpleNamespace(), row, visitor_name="Updated")
    assert row.visitor_name == "Updated"
    assert row.number_plate is None


@pytest.mark.asyncio
async def test_conversion_to_duration_requires_manual_plate(monkeypatch):
    service = VisitorPassService()
    start = datetime.now(tz=UTC) + timedelta(days=1)
    row = visitor_pass(expected_time=start, status=VisitorPassStatus.SCHEDULED)
    monkeypatch.setattr(service, "_lock_for_mutation", AsyncMock(side_effect=lambda _session, item, **_kw: item))
    with pytest.raises(VisitorPassError, match="valid vehicle registration"):
        await service.update_pass(SimpleNamespace(), row, pass_type=VisitorPassType.DURATION,
            valid_from=start, valid_until=start + timedelta(hours=2))
    assert row.pass_type == VisitorPassType.ONE_TIME
