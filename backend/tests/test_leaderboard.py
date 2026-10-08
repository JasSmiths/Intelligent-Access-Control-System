import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.models import LeaderboardState
from app.models.enums import AccessDecision, AccessDirection
from app.modules.notifications.base import NotificationContext
from app.services import leaderboard as leaderboard_module
from app.services.leaderboard import KNOWN_TOP_STATE_KEY, LeaderboardService
from app.services.leaderboard_reads import snapshot_payload
from app.services.snapshots import access_event_snapshot_relative_path


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return None


class FakeLeaderboardSession:
    def __init__(self, event, state=None) -> None:
        self.event = event
        self.state = state
        self.added: list[Any] = []
        self.commits = 0

    async def get(self, model, key):
        if model.__name__ == "AccessEvent":
            return self.event
        if model is LeaderboardState and key == KNOWN_TOP_STATE_KEY:
            return self.state
        return None

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, LeaderboardState):
            self.state = row

    async def commit(self) -> None:
        self.commits += 1


class FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event_type, payload):
        self.events.append((event_type, payload))


class FakeNotificationService:
    def __init__(self) -> None:
        self.contexts: list[NotificationContext] = []

    async def notify(self, context):
        self.contexts.append(context)


def _event(vehicle_id):
    return SimpleNamespace(
        vehicle_id=vehicle_id,
        decision=AccessDecision.GRANTED,
        direction=AccessDirection.ENTRY,
    )


def _leader(*, vehicle_id, person_id, registration_number="VIP123", read_count=3, name="Steph Smith"):
    return {
        "registration_number": registration_number,
        "vehicle_id": str(vehicle_id),
        "person_id": str(person_id),
        "read_count": read_count,
        "display_name": name,
        "vehicle_name": "Silver Ford Transit",
        "person": {
            "display_name": name,
            "first_name": name.split(" ", 1)[0],
            "last_name": name.split(" ", 1)[1] if " " in name else "",
            "profile_photo_data_url": None,
        },
        "vehicle": {
            "make": "Ford",
            "model": "Transit",
            "color": "Silver",
            "display_name": "Silver Ford Transit",
        },
    }


def test_leaderboard_snapshot_payload_requires_existing_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    event_id = uuid.uuid4()
    relative_path = access_event_snapshot_relative_path(event_id)

    assert snapshot_payload(
        event_id=event_id,
        captured_at=datetime(2026, 5, 2, 16, 30, tzinfo=UTC),
        relative_path=relative_path,
        byte_count=1200,
        width=320,
        height=180,
        camera="camera.gate",
    ) is None

    snapshot_path = tmp_path / relative_path
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(b"jpeg")

    payload = snapshot_payload(
        event_id=event_id,
        captured_at=datetime(2026, 5, 2, 16, 30, tzinfo=UTC),
        relative_path=relative_path,
        byte_count=1200,
        width=320,
        height=180,
        camera="camera.gate",
    )

    assert payload == {
        "event_id": str(event_id),
        "url": f"/api/v1/events/{event_id}/snapshot",
        "captured_at": "2026-05-02T16:30:00+00:00",
        "bytes": 1200,
        "width": 320,
        "height": 180,
        "camera": "camera.gate",
    }


@pytest.mark.asyncio
async def test_get_leaderboard_returns_shape_and_enriches_unknowns(monkeypatch) -> None:
    service = LeaderboardService()
    known = [_leader(vehicle_id=uuid.uuid4(), person_id=uuid.uuid4(), read_count=5)]
    unknown = [
        {
            "rank": 1,
            "registration_number": "MYSTERY1",
            "read_count": 2,
            "first_seen_at": "2026-04-27T10:00:00+00:00",
            "last_seen_at": "2026-04-27T10:05:00+00:00",
            "vehicle_information": {"status": "pending"},
        }
    ]

    async def fake_known(_session, limit):
        assert limit == 25
        return known

    async def fake_unknown(_session, limit):
        assert limit == 25
        return unknown

    async def fake_lookup(registration_number):
        assert registration_number == "MYSTERY1"
        return {
            "status": "ok",
            "vehicle": {"make": "FORD"},
            "display_vehicle": {"make": "Ford", "colour": "Silver"},
            "label": "Silver Ford",
        }

    monkeypatch.setattr(leaderboard_module, "AsyncSessionLocal", lambda: SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(leaderboard_module, "known_leaders_in_session", fake_known)
    monkeypatch.setattr(leaderboard_module, "unknown_leaders_in_session", fake_unknown)
    monkeypatch.setattr(service, "_lookup_unknown_vehicle", fake_lookup)

    result = await service.get_leaderboard()

    assert result["top_known"] == known[0]
    assert result["known"] == known
    assert result["unknown"][0]["vehicle_information"]["label"] == "Silver Ford"
    assert result["generated_at"]


@pytest.mark.asyncio
@pytest.mark.parametrize("enrich_unknowns", [False, True])
async def test_public_chart_preserves_latest_snapshot_from_real_projection(tmp_path, monkeypatch, enrich_unknowns):
    event_id = uuid.UUID(int=301)
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    relative_path = "snapshots/public-chart.jpg"
    snapshot_file = tmp_path / relative_path
    snapshot_file.parent.mkdir(parents=True)
    snapshot_file.write_bytes(b"synthetic-existing-snapshot")
    monkeypatch.setattr("app.services.snapshots.settings.data_dir", tmp_path)
    row = {"registration_number": "PUBLIC", "read_count": 1, "first_seen_at": now,
           "last_seen_at": now, "snapshot_event_id": event_id, "snapshot_captured_at": now,
           "snapshot_path": relative_path, "snapshot_bytes": None, "snapshot_width": 320,
           "snapshot_height": 180, "snapshot_camera": "camera.synthetic"}
    # Keep the actual unknown scalar projection/serializer and public composition.
    # Only the SQL transport is inert here; real SQL parity is covered by the PG fixture.
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [row]))),
        close=AsyncMock(),
    )
    monkeypatch.setattr(leaderboard_module, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(leaderboard_module, "known_leaders_in_session", AsyncMock(return_value=[]))
    service = LeaderboardService()
    information = {"status": "ok", "information": None, "label": "Synthetic"}
    lookup = AsyncMock(return_value=information)
    monkeypatch.setattr(service, "_lookup_unknown_vehicle", lookup)

    result = await service.get_leaderboard(enrich_unknowns=enrich_unknowns)

    assert set(result) == {"known", "unknown", "top_known", "generated_at"}
    assert result["known"] == [] and result["top_known"] is None
    assert result["unknown"] == [{
        "rank": 1, "registration_number": "PUBLIC", "read_count": 1,
        "first_seen_at": now.isoformat(), "last_seen_at": now.isoformat(),
        "latest_snapshot": {"event_id": str(event_id),
            "url": f"/api/v1/events/{event_id}/snapshot", "captured_at": now.isoformat(),
            "bytes": None, "width": 320, "height": 180, "camera": "camera.synthetic"},
        "vehicle_information": information if enrich_unknowns else {
            "status": "skipped", "information": None, "label": ""},
    }]
    session.close.assert_awaited_once()
    if enrich_unknowns:
        lookup.assert_awaited_once_with("PUBLIC")
    else:
        lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_information_uses_only_shared_cache(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from app.services.vehicle_information_contracts import VehicleInformation
    lookup = AsyncMock(return_value=SimpleNamespace(information=VehicleInformation(registration_number="NOPE123")))
    monkeypatch.setattr(leaderboard_module, "get_vehicle_information_service", lambda: SimpleNamespace(lookup=lookup))
    result = await LeaderboardService()._lookup_unknown_vehicle("NOPE123")
    assert result["status"] == "unavailable"
    assert "vehicle" not in result
    lookup.assert_awaited_once_with("NOPE123", cached_only=True)


@pytest.mark.asyncio
async def test_first_known_leader_initializes_state_without_overtake(monkeypatch) -> None:
    vehicle_id = uuid.uuid4()
    person_id = uuid.uuid4()
    event_id = uuid.uuid4()
    session = FakeLeaderboardSession(_event(vehicle_id))
    service = LeaderboardService()
    fake_bus = FakeEventBus()
    fake_notifications = FakeNotificationService()

    async def fake_top(_session):
        return _leader(vehicle_id=vehicle_id, person_id=person_id, read_count=1)

    monkeypatch.setattr(leaderboard_module, "AsyncSessionLocal", lambda: FakeSessionContext(session))
    monkeypatch.setattr(leaderboard_module, "event_bus", fake_bus)
    monkeypatch.setattr(leaderboard_module, "get_notification_service", lambda: fake_notifications)
    monkeypatch.setattr(service, "_current_top_known_leader", fake_top)

    result = await service.evaluate_known_overtake(event_id)

    assert result["initialized"] is True
    assert session.state.registration_number == "VIP123"
    assert session.state.read_count == 1
    assert fake_bus.events == []
    assert fake_notifications.contexts == []


@pytest.mark.asyncio
async def test_same_known_leader_updates_state_without_overtake(monkeypatch) -> None:
    vehicle_id = uuid.uuid4()
    person_id = uuid.uuid4()
    event_id = uuid.uuid4()
    state = LeaderboardState(
        key=KNOWN_TOP_STATE_KEY,
        registration_number="VIP123",
        vehicle_id=vehicle_id,
        person_id=person_id,
        read_count=2,
    )
    session = FakeLeaderboardSession(_event(vehicle_id), state)
    service = LeaderboardService()
    fake_bus = FakeEventBus()

    async def fake_top(_session):
        return _leader(vehicle_id=vehicle_id, person_id=person_id, read_count=3)

    monkeypatch.setattr(leaderboard_module, "AsyncSessionLocal", lambda: FakeSessionContext(session))
    monkeypatch.setattr(leaderboard_module, "event_bus", fake_bus)
    monkeypatch.setattr(service, "_current_top_known_leader", fake_top)
    prior = AsyncMock(return_value=_leader(vehicle_id=vehicle_id, person_id=person_id, read_count=2))
    monkeypatch.setattr(leaderboard_module, "known_leader_from_state_in_session", prior)

    result = await service.evaluate_known_overtake(event_id)

    assert result["changed"] is False
    prior.assert_awaited_once_with(session, registration_number="VIP123", vehicle_id=vehicle_id, person_id=person_id, read_count=2)
    assert state.read_count == 3
    assert state.last_event_id == event_id
    assert fake_bus.events == []


@pytest.mark.asyncio
async def test_changed_known_leader_emits_overtake_event_and_notification(monkeypatch) -> None:
    new_vehicle_id = uuid.uuid4()
    new_person_id = uuid.uuid4()
    old_vehicle_id = uuid.uuid4()
    old_person_id = uuid.uuid4()
    event_id = uuid.uuid4()
    state = LeaderboardState(
        key=KNOWN_TOP_STATE_KEY,
        registration_number="OLD123",
        vehicle_id=old_vehicle_id,
        person_id=old_person_id,
        read_count=4,
    )
    session = FakeLeaderboardSession(_event(new_vehicle_id), state)
    service = LeaderboardService()
    fake_bus = FakeEventBus()
    fake_notifications = FakeNotificationService()
    new_leader = _leader(
        vehicle_id=new_vehicle_id,
        person_id=new_person_id,
        registration_number="NEW123",
        read_count=5,
        name="Steph Smith",
    )
    old_leader = _leader(
        vehicle_id=old_vehicle_id,
        person_id=old_person_id,
        registration_number="OLD123",
        read_count=4,
        name="Jason Smith",
    )

    async def fake_top(_session):
        return new_leader

    async def fake_previous(_session, _state):
        return old_leader

    monkeypatch.setattr(leaderboard_module, "AsyncSessionLocal", lambda: FakeSessionContext(session))
    monkeypatch.setattr(leaderboard_module, "event_bus", fake_bus)
    monkeypatch.setattr(leaderboard_module, "get_notification_service", lambda: fake_notifications)
    monkeypatch.setattr(service, "_current_top_known_leader", fake_top)
    monkeypatch.setattr(service, "_leader_from_state", fake_previous)

    result = await service.evaluate_known_overtake(event_id)

    assert result["changed"] is True
    assert state.registration_number == "NEW123"
    assert fake_bus.events[0][0] == "leaderboard_overtake"
    assert fake_bus.events[0][1]["overtaken_name"] == "Jason Smith"
    assert fake_notifications.contexts[0].event_type == "leaderboard_overtake"
    assert fake_notifications.contexts[0].facts["new_winner_name"] == "Steph Smith"
    assert fake_notifications.contexts[0].facts["read_count"] == "5"
