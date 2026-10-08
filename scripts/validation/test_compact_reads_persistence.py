"""Compact chart/presence/forecast parity on disposable PostgreSQL only."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event, insert

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, Person, Presence, Vehicle, VehiclePersonAssignment
from app.models.enums import AccessDecision, AccessDirection, PresenceState
from app.services import leaderboard, leaderboard_reads
from app.services.expected_presence import (
    HistoricalPresenceEvent,
    build_expected_presence_payload,
    expected_presence_summary_today,
    expected_presence_today,
    historical_presence_events_in_session,
)
from app.services.leaderboard_reads import (
    known_leader_from_state_in_session,
    known_leaders_in_session,
    unknown_leaders_in_session,
)
from app.services.presence_reads import (
    list_presence_in_session,
    presence_summary_in_session,
)

pytest_plugins = ("test_recovery_boundaries",)
pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def compact_resources(isolated_resources):
    yield


def observation(index, *, vehicle_id=None, person_id=None, plate=None, at=NOW, **values):
    return {"id": uuid.UUID(int=index), "registration_number": plate or f"SYN{index:08d}",
            "vehicle_id": vehicle_id, "person_id": person_id, "occurred_at": at,
            "direction": AccessDirection.ENTRY, "decision": AccessDecision.GRANTED,
            "confidence": .99, "source": "synthetic-compact", "raw_payload": {"unused": "x" * 4096},
            "created_at": NOW, **values}


async def save(*objects, observations=()):
    async with AsyncSessionLocal() as session:
        session.add_all(objects)
        await session.flush()
        if observations:
            await session.execute(insert(AccessEvent), observations)
        await session.commit()


def capture_sql():
    statements = []
    def before(_connection, _cursor, statement, parameters, _context, _many):
        statements.append((statement, parameters))
    event.listen(engine.sync_engine, "before_cursor_execute", before)
    return statements, before


async def test_known_chart_preserves_rank_group_identity_and_photo_reference_only():
    person = Person(id=uuid.uuid4(), first_name="Synthetic", last_name="Winner", display_name="Synthetic Winner",
                    profile_photo_data_url="data:image/png;base64," + "A" * 1024 * 1024, updated_at=NOW)
    other = Person(id=uuid.uuid4(), first_name="External", display_name="External Photo",
                   profile_photo_data_url="https://example.invalid/photo.png", updated_at=NOW)
    vehicle = Vehicle(id=uuid.uuid4(), registration_number="KNOWN", make=None, model="Model", color="Blue",
                      description=None, vehicle_photo_data_url="data:image/png;base64," + "B" * 1024 * 1024,
                      information_outcome={"unused": "C" * 1024 * 1024}, updated_at=NOW)
    rows = [observation(index, vehicle_id=vehicle.id, person_id=person.id, plate="KNOWN") for index in range(1, 4)]
    rows += [observation(4, vehicle_id=vehicle.id, person_id=other.id, plate="KNOWN"),
             observation(5, vehicle_id=vehicle.id, person_id=None, plate="KNOWN", direction=AccessDirection.EXIT)]
    await save(person, other, vehicle, observations=rows)
    statements, listener = capture_sql()
    hydrated = []
    try:
        async with AsyncSessionLocal() as session:
            event.listen(session.sync_session, "loaded_as_persistent", lambda _session, row: hydrated.append(row))
            result = await known_leaders_in_session(session, 25)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    assert not hydrated and len(statements) == 1
    assert [item["read_count"] for item in result] == [3, 1]
    first, second = result
    assert first["person_id"] == str(person.id) and first["vehicle_id"] == str(vehicle.id)
    assert first["person"]["profile_photo_data_url"] is None
    assert first["person"]["profile_photo_url"] == f"/api/v1/people/{person.id}/photo?v={int(NOW.timestamp())}"
    assert first["vehicle"]["vehicle_photo_url"] == f"/api/v1/vehicles/{vehicle.id}/photo?v={int(NOW.timestamp())}"
    assert first["vehicle"]["make"] is None and first["vehicle_name"] == "Blue Model"
    assert second["person"]["profile_photo_url"] == "https://example.invalid/photo.png"
    sql = statements[0][0].lower()
    assert "case when" in sql and "raw_payload" not in sql and "information_outcome" not in sql
    assert "garage_door_entity_ids" not in sql and "mot_status" not in sql


async def test_prior_winner_hydrates_exact_ids_counts_and_missing_relationship_shape():
    person = Person(id=uuid.uuid4(), first_name="Prior", display_name="Prior Winner")
    vehicle = Vehicle(id=uuid.uuid4(), registration_number="NEWPLATE", description="Named Vehicle")
    await save(person, vehicle)
    async with AsyncSessionLocal() as session:
        prior = await known_leader_from_state_in_session(session, registration_number="OLDPLATE",
            vehicle_id=vehicle.id, person_id=person.id, read_count=91)
        missing_id = uuid.uuid4()
        missing = await known_leader_from_state_in_session(session, registration_number="REMOVED",
            vehicle_id=missing_id, person_id=None, read_count=17)
    assert prior["registration_number"] == "OLDPLATE" and prior["vehicle"]["registration_number"] == "NEWPLATE"
    assert prior["read_count"] == 91 and prior["rank"] == 1 and prior["last_seen_at"] is None
    assert missing["vehicle_id"] == str(missing_id) and missing["person"]["id"] is None
    assert missing["display_name"] == missing["vehicle_name"] == "REMOVED"
    assert missing["vehicle"]["make"] == "" and missing["person"]["profile_photo_url"] is None


async def test_unknown_rank_then_selected_snapshot_preserves_null_bytes_and_missing_latest(monkeypatch):
    rows = []
    for index, plate in enumerate(("TOP", "SECOND", "UNSELECTED"), start=1):
        rows += [observation(index * 100 + offset, plate=plate, decision=AccessDecision.DENIED)
                 for offset in range(5 - index)]
    rows += [observation(110, plate="TOP", decision=AccessDecision.DENIED, at=NOW - timedelta(days=1),
                         snapshot_path="old-exists", snapshot_bytes=10),
             observation(111, plate="TOP", decision=AccessDecision.DENIED, snapshot_path="latest-missing"),
             observation(210, plate="SECOND", decision=AccessDecision.DENIED,
                         snapshot_path="null-byte-exists", snapshot_bytes=None)]
    await save(observations=rows)
    paths = []
    def exists(path):
        paths.append(path)
        return path == "null-byte-exists"
    monkeypatch.setattr(leaderboard_reads, "snapshot_path_available", exists)
    statements, listener = capture_sql()
    try:
        async with AsyncSessionLocal() as session:
            result = await unknown_leaders_in_session(session, 2)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    assert [item["registration_number"] for item in result] == ["TOP", "SECOND"]
    assert result[0]["latest_snapshot"] is None
    assert result[1]["latest_snapshot"]["event_id"] == str(uuid.UUID(int=210))
    assert result[1]["latest_snapshot"]["bytes"] is None
    assert paths == ["latest-missing", "null-byte-exists"]
    sql = statements[0][0].lower()
    assert "lateral" in sql and "row_number" not in sql and "raw_payload" not in sql
    assert len(statements) == 1


async def test_public_chart_keeps_present_latest_snapshot_contract(tmp_path, monkeypatch):
    event_id = uuid.UUID(int=301)
    relative_path = "snapshots/public-chart.jpg"
    snapshot_file = tmp_path / relative_path
    snapshot_file.parent.mkdir(parents=True)
    snapshot_file.write_bytes(b"synthetic-existing-snapshot")
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    await save(observations=[observation(301, plate="PUBLIC", decision=AccessDecision.DENIED,
               snapshot_path=relative_path, snapshot_bytes=None, snapshot_captured_at=NOW,
               snapshot_width=320, snapshot_height=180, snapshot_camera="camera.synthetic")])

    result = await leaderboard.LeaderboardService().get_leaderboard(enrich_unknowns=False)

    assert set(result) == {"known", "unknown", "top_known", "generated_at"}
    assert result["known"] == [] and result["top_known"] is None
    assert result["unknown"] == [{
        "rank": 1, "registration_number": "PUBLIC", "read_count": 1,
        "first_seen_at": NOW.isoformat(), "last_seen_at": NOW.isoformat(),
        "latest_snapshot": {"event_id": str(event_id),
            "url": f"/api/v1/events/{event_id}/snapshot", "captured_at": NOW.isoformat(),
            "bytes": None, "width": 320, "height": 180, "camera": "camera.synthetic"},
        "vehicle_information": {"status": "skipped", "information": None, "label": ""},
    }]


async def test_unknown_exact_timestamp_tie_uses_documented_id_order(monkeypatch):
    await save(observations=[observation(index, plate="TIE", decision=AccessDecision.DENIED,
                           snapshot_path=f"path-{index}") for index in (1, 2)])
    monkeypatch.setattr(leaderboard_reads, "snapshot_path_available", lambda _path: True)
    async with AsyncSessionLocal() as session:
        result = await unknown_leaders_in_session(session, 1)
    assert result[0]["latest_snapshot"]["event_id"] == str(uuid.UUID(int=2))


async def test_presence_public_population_order_and_internal_global_counts_one_snapshot():
    names = ["Éva", "Zed", "alpha", "😀 Person", "Alpha", "Ána", "beta", "éva", "Omega"]
    people = [Person(id=uuid.UUID(int=100 + index), display_name=name,
                     profile_photo_data_url="data:image/png;base64," + "A" * 4096) for index, name in enumerate(names)]
    people += [Person(id=uuid.UUID(int=200 + index), display_name=f"Other {index}") for index in range(5)]
    states = [PresenceState.PRESENT] * len(names) + [PresenceState.EXITED] * 3 + [PresenceState.UNKNOWN] * 2
    rows = [Presence(person_id=person.id, state=state, last_changed_at=NOW,
                     updated_at=NOW + timedelta(seconds=index)) for index, (person, state) in enumerate(zip(people, states))]
    await save(*people, *rows)
    statements, listener = capture_sql()
    try:
        async with AsyncSessionLocal() as session:
            public = await list_presence_in_session(session)
            statements.clear()
            summary = await presence_summary_in_session(session)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    assert [row["person_id"] for row in public] == [str(person.id) for person in reversed(people)]
    assert len(public) == 14 and all(set(row) == {"person_id", "display_name", "state", "last_changed_at"} for row in public)
    assert summary["counts"] == {"total": 14, "present": 9, "exited": 3, "unknown": 2}
    assert [row["display_name"] for row in summary["inside"]["people"]] == sorted(names, key=lambda name: name.encode("utf-8"))[:6]
    assert summary["inside"]["hidden"] == 3
    assert len(statements) == 1 and "count(*)" in statements[0][0].lower()
    assert "profile_photo_data_url" not in statements[0][0] and "COLLATE \"C\"" in statements[0][0]


async def test_presence_empty_and_equal_name_timestamp_ties():
    async with AsyncSessionLocal() as session:
        empty = await presence_summary_in_session(session)
    assert empty == {"counts": {"total": 0, "present": 0, "exited": 0, "unknown": 0}, "inside": {"people": [], "hidden": 0}}
    people = [Person(id=uuid.UUID(int=index), display_name="Same") for index in (1, 2, 3)]
    rows = [Presence(person_id=person.id, state=PresenceState.PRESENT, last_changed_at=None,
                     updated_at=NOW + timedelta(seconds=(index == 2))) for index, person in enumerate(people)]
    await save(*people, *rows)
    async with AsyncSessionLocal() as session:
        summary = await presence_summary_in_session(session)
    assert [item["person_id"] for item in summary["inside"]["people"]] == [str(people[2].id), str(people[0].id), str(people[1].id)]


async def test_forecast_recorded_identity_precedes_deduplicated_single_active_fallback():
    direct, owner, inactive, ambiguous = [Person(id=uuid.uuid4(), display_name=name, is_active=active)
        for name, active in (("Recorded", True), ("Current owner", True), ("Inactive", False), ("Other", True))]
    single = Vehicle(id=uuid.uuid4(), registration_number="SINGLE", person_id=owner.id, is_active=False)
    shared = Vehicle(id=uuid.uuid4(), registration_number="SHARED", person_id=owner.id)
    inactive_only = Vehicle(id=uuid.uuid4(), registration_number="INACTIVE", person_id=inactive.id)
    assignments = [VehiclePersonAssignment(vehicle_id=single.id, person_id=owner.id),
                   VehiclePersonAssignment(vehicle_id=single.id, person_id=inactive.id),
                   VehiclePersonAssignment(vehicle_id=shared.id, person_id=ambiguous.id)]
    rows = [observation(1, vehicle_id=shared.id, person_id=direct.id),
            observation(2, vehicle_id=single.id),
            observation(3, vehicle_id=shared.id),
            observation(4, vehicle_id=single.id, person_id=inactive.id),
            observation(5, vehicle_id=inactive_only.id),
            observation(6, vehicle_id=single.id, decision=AccessDecision.DENIED),
            observation(7, person_id=direct.id, direction=AccessDirection.DENIED)]
    await save(direct, owner, inactive, ambiguous, single, shared, inactive_only, *assignments, observations=rows)
    statements, listener = capture_sql()
    try:
        async with AsyncSessionLocal() as session:
            hydrated = []
            event.listen(session.sync_session, "loaded_as_persistent", lambda _session, row: hydrated.append(row))
            history = await historical_presence_events_in_session(session, window_start=NOW - timedelta(days=1), generated_at=NOW)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    assert sorted((row.person_id, row.display_name) for row in history) == sorted([(str(direct.id), "Recorded"), (str(owner.id), "Current owner")])
    assert not hydrated and len(statements) == 1
    sql = statements[0][0].lower()
    assert "union" in sql and "count(*)" in sql
    assert all(column not in sql for column in ("raw_payload", "profile_photo_data_url", "vehicle_photo_data_url", "information_outcome", "snapshot_path"))


@pytest.mark.parametrize("zone,now", [("Europe/London", datetime(2026, 3, 30, 8, tzinfo=UTC)),
                                      ("America/New_York", datetime(2026, 11, 2, 9, tzinfo=UTC))])
async def test_forecast_horizon_dst_and_internal_summary_preserve_predictor(zone, now):
    from datetime import time
    from zoneinfo import ZoneInfo
    local = now.astimezone(ZoneInfo(zone))
    window = datetime.combine(local.date() - timedelta(weeks=12), time.min, tzinfo=ZoneInfo(zone)).astimezone(UTC)
    people = [Person(id=uuid.uuid4(), display_name=f"Synthetic {index}") for index in range(7)]
    rows = [observation(index + 1, person_id=person.id, at=now) for index, person in enumerate(people)]
    rows += [observation(100, person_id=people[0].id, at=window),
             observation(101, person_id=people[0].id, at=window - timedelta(microseconds=1)),
             observation(102, person_id=people[0].id, at=now + timedelta(microseconds=1))]
    await save(*people, observations=rows)
    historical = [HistoricalPresenceEvent(str(person.id), person.display_name, now, AccessDirection.ENTRY) for person in people]
    historical += [HistoricalPresenceEvent(str(people[0].id), people[0].display_name, window, AccessDirection.ENTRY)]
    expected = build_expected_presence_payload(historical, target_date=local.date(), timezone_name=zone, generated_at=now)
    async with AsyncSessionLocal() as session:
        full = await expected_presence_today(session, timezone_name=zone, now=now)
        summary = await expected_presence_summary_today(session, timezone_name=zone, now=now)
    assert full == expected
    assert summary == {**expected, "people": expected["people"][:6], "hidden": max(expected["count"] - 6, 0)}
    assert len(summary["people"]) == 6 and summary["count"] == 7 and summary["hidden"] == 1


async def test_public_chart_close_remains_owned_during_caller_cancellation(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    session = AsyncSessionLocal()
    original_close = session.close
    async def close():
        entered.set()
        await release.wait()
        await original_close()
    monkeypatch.setattr(session, "close", close)
    monkeypatch.setattr(leaderboard, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(leaderboard, "known_leaders_in_session", AsyncMock(return_value=[]))
    monkeypatch.setattr(leaderboard, "unknown_leaders_in_session", AsyncMock(return_value=[]))
    task = asyncio.create_task(leaderboard.LeaderboardService().get_leaderboard())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
