"""Live predecessor and timing policy through real isolated evidence reads."""
from test_recovery_boundaries import isolated_resources as isolated_resources

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid
import re

import pytest
from sqlalchemy import event as sql_event

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, Person, Vehicle
from app.models.enums import AccessDecision, AccessDirection, TimingClassification
from app.modules.gate.base import GateState
from app.modules.lpr.base import PlateRead
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.reads import GATE_MALFUNCTION_PAYLOAD_KEY, GATE_OBSERVATION_PAYLOAD_KEY

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


async def history(rows):
    async with AsyncSessionLocal() as session:
        person = Person(id=uuid.uuid4(), first_name="Synthetic", last_name="Person", display_name="Synthetic Person", is_active=True)
        vehicle = Vehicle(id=uuid.uuid4(), registration_number="AB12CDE", person_id=person.id, is_active=True)
        session.add(person)
        await session.flush()
        session.add(vehicle)
        await session.flush()
        events = []
        for index, (source, payload, time, direction) in enumerate(rows):
            row = AccessEvent(id=uuid.UUID(int=index+1), person_id=person.id, vehicle_id=vehicle.id,
                registration_number=vehicle.registration_number, decision=AccessDecision.GRANTED,
                direction=direction, source=source, raw_payload=payload, occurred_at=time, confidence=1)
            session.add(row)
            events.append(row)
        await session.commit()
        return person, vehicle, events


async def test_more_than_twenty_backfills_do_not_hide_live_predecessor_or_change_malfunction_direction(isolated_resources):
    person, vehicle, rows = await history([("live", None, NOW-timedelta(hours=2), AccessDirection.ENTRY)] + [
        ("BACKFILL" if i % 2 else "live", {} if i % 2 else {"backfill": None}, NOW-timedelta(minutes=i+1), AccessDirection.EXIT)
        for i in range(30)])
    resolver = AccessEvidenceResolver()
    async with AsyncSessionLocal() as session:
        previous = await resolver._latest_live_person_or_vehicle_event(session, person=person, vehicle=vehicle, before=NOW)
        assert previous.id == rows[0].id
        read = PlateRead(registration_number="AB12CDE", confidence=1, source="live", captured_at=NOW,
            raw_payload={GATE_OBSERVATION_PAYLOAD_KEY: {"state": GateState.OPEN.value},
                GATE_MALFUNCTION_PAYLOAD_KEY: {"id": str(uuid.uuid4()), "status": "active"}})
        direction, result = await resolver._resolve_direction(session, read, person, True, vehicle=vehicle)
    assert direction == AccessDirection.EXIT
    assert result["previous_live_event_id"] == str(rows[0].id)


@pytest.mark.parametrize("payload", [None, {}, {"other": "backfill"}, ["backfill"], "backfill"])
async def test_non_object_metadata_retains_live_semantics_and_timestamp_ties_are_deterministic(isolated_resources, payload):
    person, vehicle, rows = await history([("live", payload, NOW-timedelta(minutes=1), AccessDirection.ENTRY),
        ("live", payload, NOW-timedelta(minutes=1), AccessDirection.EXIT)])
    async with AsyncSessionLocal() as session:
        row = await AccessEvidenceResolver()._latest_live_person_or_vehicle_event(session, person=person, vehicle=vehicle, before=NOW)
    assert row.id == rows[1].id


async def test_no_preceding_live_event_returns_none(isolated_resources):
    person, vehicle, _ = await history([("live_backfill", {}, NOW-timedelta(minutes=1), AccessDirection.ENTRY),
        ("live", {"backfill": {}}, NOW-timedelta(minutes=2), AccessDirection.EXIT)])
    async with AsyncSessionLocal() as session:
        assert await AccessEvidenceResolver()._latest_live_person_or_vehicle_event(session, person=person, vehicle=vehicle, before=NOW) is None


async def test_live_predicate_precedes_bounded_query_and_payload_is_not_materialized(isolated_resources):
    person, vehicle, _ = await history([("live", {"unused": "x"*100000}, NOW-timedelta(minutes=1), AccessDirection.ENTRY)])
    statements = []
    def capture(_connection, _cursor, statement, parameters, _context, _many):
        statements.append((statement, parameters))
    sql_event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with AsyncSessionLocal() as session:
            row = await AccessEvidenceResolver()._latest_live_person_or_vehicle_event(session, person=person, vehicle=vehicle, before=NOW)
            assert "raw_payload" not in row.__dict__
    finally:
        sql_event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    statement, parameters = statements[0]
    assert "backfill" in statement or "backfill" in str(parameters)
    limit = re.search(r"LIMIT \$(\d+)::INTEGER", statement)
    assert limit and parameters[int(limit[1])-1] == 1


@pytest.mark.parametrize("times,observed,expected", [
    (["23:55", "00:00", "00:05"], "00:00", TimingClassification.NORMAL),
    (["23:55", "00:00", "00:05"], "22:00", TimingClassification.EARLIER_THAN_USUAL),
    (["23:55", "00:00", "00:05"], "02:00", TimingClassification.LATER_THAN_USUAL),
    (["08:00", "09:00", "10:00"], "09:45", TimingClassification.NORMAL),
    (["08:00", "09:00", "10:00"], "09:46", TimingClassification.LATER_THAN_USUAL),
    (["08:00", "09:00", "10:00"], "08:14", TimingClassification.EARLIER_THAN_USUAL),
    (["00:00", "00:05", "12:00"], "00:00", TimingClassification.UNKNOWN),
    (["00:00", "00:05"], "00:00", TimingClassification.UNKNOWN),
])
async def test_wrap_aware_timing_through_evidence_owner(isolated_resources, times, observed, expected):
    def instant(day, time):
        hour, minute = map(int, time.split(":"))
        return datetime(2026, 10, day, hour, minute, tzinfo=UTC)
    person, _, _ = await history([("live", {}, instant(i+1, time), AccessDirection.ENTRY) for i, time in enumerate(times)])
    resolver = AccessEvidenceResolver(SimpleNamespace(site_timezone="UTC"))
    async with AsyncSessionLocal() as session:
        result = await resolver._classify_timing(session, person, AccessDirection.ENTRY, instant(14, observed))
    assert result == expected


@pytest.mark.parametrize('mode,winner',[
    ('person_only',0),('vehicle_only',1),('person_wins',0),('vehicle_wins',1),('both_tie',1),('duplicate_winner',1),('neither',None),
])
async def test_bounded_identity_seek_preserves_union_ordering(isolated_resources,mode,winner):
    first_time=NOW-timedelta(minutes=1 if mode=='person_wins' else 2)
    second_time=NOW-timedelta(minutes=2 if mode=='person_wins' else 1)
    if mode=='both_tie':
        first_time=second_time=NOW-timedelta(minutes=1)
    person,vehicle,events=await history([('live',{},first_time,AccessDirection.ENTRY),('live',{},second_time,AccessDirection.EXIT)])
    async with AsyncSessionLocal() as session:
        from sqlalchemy import update
        if mode!='duplicate_winner':
            await session.execute(update(AccessEvent).where(AccessEvent.id==events[0].id).values(vehicle_id=None))
            await session.execute(update(AccessEvent).where(AccessEvent.id==events[1].id).values(person_id=None))
        await session.commit()
    async with AsyncSessionLocal() as session:
        result=await AccessEvidenceResolver()._latest_live_person_or_vehicle_event(session,
            person=None if mode in {'vehicle_only','neither'} else person,
            vehicle=None if mode in {'person_only','neither'} else vehicle,before=NOW)
        assert (result.id if result else None)==(events[winner].id if winner is not None else None)
