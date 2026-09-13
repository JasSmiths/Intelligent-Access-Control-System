"""Origin transaction handoffs; collection requires the inert PostgreSQL guard."""

from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import NotificationRun
from app.services.notification_runs import NotificationRunStore
from app.services.notifications import NotificationService
from app.modules.notifications.base import NotificationContext

pytestmark = pytest.mark.asyncio


def payload():
    return {"event_type": "synthetic.required", "subject": "Synthetic notice", "severity": "info", "facts": {}}


async def test_reservation_is_invisible_until_origin_commit_and_rolls_back_with_origin():
    store, identity = NotificationRunStore(), uuid.uuid4()
    async with AsyncSessionLocal() as origin:
        assert await store.enqueue_in_session(origin, payload(), run_id=identity) == identity
        async with AsyncSessionLocal() as observer:
            assert await observer.get(NotificationRun, identity) is None
        await origin.rollback()
    assert await store.claim(identity) is None
    async with AsyncSessionLocal() as observer:
        assert await observer.get(NotificationRun, identity) is None


async def test_committed_handoff_is_discovered_without_wakeup_and_duplicate_is_immutable():
    store, identity = NotificationRunStore(), uuid.uuid4()
    override = [{"id": "synthetic-rule", "actions": []}]
    async with AsyncSessionLocal() as origin:
        await store.enqueue_in_session(origin, payload(), run_id=identity, rules_override=override)
        await origin.commit()
    before = await store.get(identity)
    assert before.status == "queued" and before.claim_count == 0 and before.claim_token is None
    async with AsyncSessionLocal() as origin:
        await store.enqueue_in_session(origin, {**payload(), "subject": "Must not replace"}, run_id=identity, rules_override=[])
        await origin.commit()
    retained = await store.get(identity)
    assert retained.context == payload() and retained.rules_override == override and retained.queued_at == before.queued_at
    claimed = await NotificationRunStore().claim(identity)
    assert claimed.id == identity and claimed.status == "processing" and claimed.claim_count == 1


async def test_service_participant_never_wakes_or_publishes_before_commit(monkeypatch):
    service, identity = NotificationService(), uuid.uuid4()
    def forbidden(*args, **kwargs):
        raise AssertionError("A transaction participant must not wake or perform external I/O")
    monkeypatch.setattr(service.dispatcher, "wake", forbidden)
    from app.services import notifications
    monkeypatch.setattr(notifications.event_bus, "publish", forbidden)
    context = NotificationContext(event_type="synthetic.required", subject="Synthetic notice", severity="info", facts={})
    async with AsyncSessionLocal() as origin:
        assert await service.enqueue_in_session(origin, context, dispatch_id=identity) == identity
        assert await origin.scalar(select(NotificationRun.id).where(NotificationRun.id == identity)) == identity
        await origin.rollback()
    with pytest.raises(LookupError):
        await service.run_store.get(identity)


async def test_existing_immediate_reservation_still_claims_atomically():
    store = NotificationRunStore()
    identity, claimed = await store.reserve(payload())
    assert claimed.id == identity and claimed.status == "processing" and claimed.claim_count == 1
    assert await NotificationRunStore().claim(identity) is None
    repeated, duplicate_claim = await store.reserve(payload(), run_id=identity)
    assert repeated == identity and duplicate_claim is None
