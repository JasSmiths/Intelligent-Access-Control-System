"""Actual visitor-pass semantics under complete frozen calendar coverage."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_calendar_sync_store_persistence import calendar_resources as calendar_resources
from test_calendar_sync_store_persistence import admit, allowed, setup_accounts, work

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal, engine
from app.models import ICloudCalendarAccount, VisitorPass, VisitorPassReservationRecord
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.modules.icloud_calendar.contracts import CalendarSnapshot, ICloudCalendarEvent
from app.services.calendar_reconciliation import CalendarAccountReconciliation, source_reference_for_event
from app.services.calendar_sync_commit import CalendarAccountCommit
from app.services.calendar_sync_store import CalendarSyncStore
from app.services.visitor_passes import VisitorPassService

pytestmark = pytest.mark.asyncio


async def setup():
    actor, ids = await setup_accounts(1)
    store = CalendarSyncStore()
    admission = await admit(store, actor)
    claim = await store.claim_account(admission, ids[0], authorize=allowed, work=work())
    return actor, store, admission, claim


def event(identity="event-one", *, title="Memory Clinic: Vicky Thompson", marker=True, start=None):
    begins = start or datetime.now(UTC) + timedelta(hours=2)
    return ICloudCalendarEvent("inert-home", identity, title, begins, begins + timedelta(hours=1),
        "Open Gate" if marker else "", "")


async def reconcile(actor, store, admission, claim, events):
    snapshot = CalendarSnapshot(admission.context.coverage, tuple(events), 0)
    return await CalendarAccountCommit(store).apply(claim, work=work(), authorize=allowed,
        stage=CalendarAccountReconciliation(claim, snapshot, work=work(), actor="Inert Calendar Admin", actor_user_id=actor),
        actor="Inert Calendar Admin", actor_user_id=actor)


async def seed_pass(account_id, *, key, starts_at, metadata=None, status=VisitorPassStatus.SCHEDULED, source="icloud_calendar", duration=False):
    async with AsyncSessionLocal() as session:
        row = await VisitorPassService().create_pass(session, visitor_name="Original Inert Visitor", expected_time=starts_at,
            valid_from=starts_at - timedelta(minutes=30), valid_until=starts_at + timedelta(hours=1),
            source=source, source_reference=f"icloud:{account_id}:inert-home:{key}",
            source_metadata=metadata, actor="Inert Calendar Admin", visitor_phone="07123456789",
            number_plate="AB12CDE", pass_type=VisitorPassType.DURATION if duration else VisitorPassType.ONE_TIME)
        row.status = status
        await session.commit()
        return row.id


async def test_real_match_uses_original_start_explicit_window_and_fallback_name():
    actor, store, admission, claim = await setup()
    item = event()
    result = await reconcile(actor, store, admission, claim, [item])
    assert result.checkpoint.events_scanned == result.checkpoint.events_matched == result.checkpoint.passes_created == 1
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(VisitorPass).where(VisitorPass.source_reference == source_reference_for_event(claim.account_id, item)))
        assert row.visitor_name == "Vicky Thompson" and row.expected_time == item.starts_at
        assert row.valid_from == item.starts_at - timedelta(minutes=30) and row.valid_until == item.ends_at
        assert row.source_metadata["event_start"] == item.starts_at.isoformat()
    assert engine.pool.checkedout() == 0


async def test_absence_only_cancels_valid_in_coverage_active_calendar_sources():
    actor, store, admission, claim = await setup()
    begins = datetime.now(UTC) + timedelta(hours=2)
    eligible = await seed_pass(claim.account_id, key="eligible", starts_at=begins, metadata={"event_start": begins.isoformat()})
    preserved = []
    for key, metadata in [("missing", {}), ("malformed", {"event_start": "not-a-date"}),
        ("naive", {"event_start": begins.replace(tzinfo=None).isoformat()}),
        ("wrong-type", {"event_start": [begins.isoformat()]}),
        ("outside", {"event_start": (admission.context.coverage.effective_end + timedelta(days=1)).isoformat()})]:
        preserved.append(await seed_pass(claim.account_id, key=key, starts_at=begins, metadata=metadata))
    for status in (VisitorPassStatus.USED, VisitorPassStatus.CANCELLED, VisitorPassStatus.EXPIRED):
        preserved.append(await seed_pass(claim.account_id, key=status.value, starts_at=begins,
            metadata={"event_start": begins.isoformat()}, status=status))
    preserved.append(await seed_pass(claim.account_id, key="manual", starts_at=begins,
        metadata={"event_start": begins.isoformat()}, source="manual"))
    async with AsyncSessionLocal() as session:
        before = {identity: (await session.get(VisitorPass, identity)).status for identity in preserved}
    result = await reconcile(actor, store, admission, claim, [])
    assert result.checkpoint.passes_cancelled == 1 and result.checkpoint.historical_coverage_skipped == 5
    async with AsyncSessionLocal() as session:
        assert (await session.get(VisitorPass, eligible)).status == VisitorPassStatus.CANCELLED
        assert {identity: (await session.get(VisitorPass, identity)).status for identity in preserved} == before
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("reservation_state", ["reserved", "held"])
async def test_matching_reserved_or_held_pass_is_seen_before_update_skip_then_absence_can_revoke(reservation_state):
    actor, store, admission, claim = await setup()
    item = event()
    identity = await seed_pass(claim.account_id, key=item.event_id, starts_at=item.starts_at,
        metadata={"event_start": item.starts_at.isoformat()})
    reservation_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(VisitorPassReservationRecord(id=reservation_id, visitor_pass_id=identity,
            access_event_id=uuid.uuid4(), intent_id=uuid.uuid4(), pass_type="one-time", normalized_plate="AB12CDE",
            state=reservation_state, dispatch_deadline=datetime.now(UTC) + timedelta(minutes=1)))
        await session.commit()
    result = await reconcile(actor, store, admission, claim, [item])
    assert result.checkpoint.passes_skipped == 1 and result.checkpoint.passes_cancelled == 0
    async with AsyncSessionLocal() as session:
        row = await session.get(VisitorPass, identity)
        assert row.status == VisitorPassStatus.SCHEDULED and row.visitor_name == "Original Inert Visitor"
        assert (await session.get(VisitorPassReservationRecord, reservation_id)).state == reservation_state
    await store.release_account(claim)
    await store.finish_attempt(admission.run_id, admission.context.attempt.token)
    next_admission = await admit(store, actor)
    next_claim = await store.claim_account(next_admission, claim.account_id, authorize=allowed, work=work())
    cancelled = await reconcile(actor, store, next_admission, next_claim, [])
    assert cancelled.checkpoint.passes_cancelled == 1
    async with AsyncSessionLocal() as session:
        assert (await session.get(VisitorPass, identity)).status == VisitorPassStatus.CANCELLED
        assert (await session.get(VisitorPassReservationRecord, reservation_id)).state == reservation_state
    assert engine.pool.checkedout() == 0


async def test_active_arrived_duration_pass_updates_calendar_fields_without_rebinding_identity():
    actor, store, admission, claim = await setup()
    item = event()
    identity = await seed_pass(claim.account_id, key=item.event_id, starts_at=item.starts_at,
        metadata={"event_start": item.starts_at.isoformat()}, status=VisitorPassStatus.ACTIVE, duration=True)
    arrival = datetime.now(UTC) - timedelta(minutes=1)
    async with AsyncSessionLocal() as session:
        row = await session.get(VisitorPass, identity)
        row.arrival_time = arrival
        await session.commit()
    result = await reconcile(actor, store, admission, claim, [item])
    assert result.checkpoint.passes_updated == 1 and result.checkpoint.passes_cancelled == 0
    async with AsyncSessionLocal() as session:
        row = await session.get(VisitorPass, identity)
        assert row.visitor_name == "Vicky Thompson" and row.arrival_time == arrival
        assert row.pass_type == VisitorPassType.DURATION and row.number_plate == "AB12CDE"
        assert row.visitor_phone == "07123456789"
    assert engine.pool.checkedout() == 0
