from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models import Presence
from app.models.enums import AccessDirection, PresenceState
from app.services.access_events import AccessEventService

from .helpers import assert_contract_subset, load_contract_fixture


PERSON_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ENTRY_EVENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class _PresenceSession:
    def __init__(self, presence: Presence | None = None) -> None:
        self.presence = presence
        self.added: list[object] = []

    async def get(self, model, key):
        assert model is Presence
        assert key == PERSON_ID
        return self.presence

    def add(self, row: object) -> None:
        self.added.append(row)
        if isinstance(row, Presence):
            self.presence = row


@pytest.mark.asyncio
async def test_presence_entry_contract_commits_current_presence_payload() -> None:
    session = _PresenceSession()
    event = SimpleNamespace(
        id=ENTRY_EVENT_ID,
        direction=AccessDirection.ENTRY,
        occurred_at=_dt("2026-05-31T08:15:00+00:00"),
    )
    person = SimpleNamespace(id=PERSON_ID)

    changed = await AccessEventService()._update_presence(session, person, event)

    assert changed is True
    assert session.presence is not None
    realtime_payload = {
        "person_id": str(session.presence.person_id),
        "state": session.presence.state.value,
        "last_event_id": str(session.presence.last_event_id),
        "last_changed_at": session.presence.last_changed_at.isoformat(),
    }
    assert_contract_subset(realtime_payload, load_contract_fixture("realtime/presence_changed.json"))


@pytest.mark.asyncio
async def test_presence_update_contract_ignores_stale_event_and_preserves_expected_state() -> None:
    existing = Presence(person_id=PERSON_ID)
    existing.state = PresenceState.PRESENT
    existing.last_event_id = ENTRY_EVENT_ID
    existing.last_changed_at = _dt("2026-05-31T08:15:00+00:00")
    session = _PresenceSession(existing)
    stale_exit = SimpleNamespace(
        id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        direction=AccessDirection.EXIT,
        occurred_at=existing.last_changed_at - timedelta(minutes=5),
    )

    changed = await AccessEventService()._update_presence(session, SimpleNamespace(id=PERSON_ID), stale_exit)

    assert changed is False
    assert session.presence.state == PresenceState.PRESENT
    assert session.presence.last_event_id == ENTRY_EVENT_ID
    assert session.presence.last_changed_at == _dt("2026-05-31T08:15:00+00:00")
