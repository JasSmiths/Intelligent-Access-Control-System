"""Completion origin/readiness/atomicity contracts in the isolated PG namespace."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event as sql_event, func, select, text
from sqlalchemy.orm.attributes import flag_modified
from test_recovery_boundaries import isolated_resources as isolated_resources

from app.db.session import AsyncSessionLocal, engine
from app.models import (
    AccessCompletionWork,
    AccessEvent,
    AuditLog,
    LprDebounceWindow,
    LprIngestEvent,
    LprWindowMember,
)
from app.models.enums import AccessDecision, AccessDirection
from app.services.access import completion_store
from app.services.access.completion_contracts import ExternalCompletionOrigin
from app.services.access.completion_store import (
    AccessCompletionStore,
    assert_completion_before_commit,
    assert_completion_in_session,
    finish_completion_in_session,
    lock_completion_event_in_session,
    reserve_external_completion_in_session,
    reserve_window_completion_in_session,
)
from app.services.access.pipeline_contracts import PipelineLeaseLost

pytestmark = pytest.mark.asyncio
CAPTURED = datetime(2001, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def completion_resources(isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE lpr_ingest_events,access_events CASCADE"))
        await session.commit()


async def make(session, *, offset=0, visitor=False):
    receipt = await completion_store.database_now(session) + timedelta(seconds=offset)
    ingest = uuid.uuid4()
    window = uuid.uuid4()
    identity = uuid.uuid4()
    read = LprIngestEvent(
        id=ingest,
        idempotency_key=str(ingest),
        source="synthetic",
        registration_number="AB12CDE",
        captured_at=CAPTURED,
        received_at=receipt,
        status="succeeded",
        observation_state="ready",
        original_observation={"state": "closed"},
        normalized_payload={
            "raw_payload": {
                "webhook_trace": {"received_at": "2099-01-01T00:00:00+00:00"},
                "_iacs_ingest": {"webhook_received_at": "2099-01-01T00:00:00+00:00"},
            }
        },
    )
    session.add(read)
    await session.flush()
    session.add(
        LprDebounceWindow(
            id=window,
            open_ordinal=await session.scalar(
                select(func.coalesce(func.max(LprDebounceWindow.open_ordinal), 0) + 1)
            ),
            source="synthetic",
            state="finalizing",
            first_seen=CAPTURED,
            updated_at=CAPTURED,
            min_received_at=receipt,
            member_count=1,
            first_ingest_id=ingest,
            best_ingest_id=ingest,
            ranking_context={},
        )
    )
    await session.flush()
    read.window_id = window
    session.add(
        LprWindowMember(
            ingest_id=ingest,
            window_id=window,
            ordinal=0,
            prepared_context={"registration_number": "AB12CDE", "annotations": {}},
        )
    )
    raw = {"visitor_pass": {"id": str(uuid.uuid4()), "mode": "arrival"}} if visitor else {}
    session.add(
        AccessEvent(
            id=identity,
            registration_number="AB12CDE",
            direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,
            confidence=0.9,
            source="synthetic",
            occurred_at=CAPTURED,
            resource_revision=1,
            raw_payload=raw,
        )
    )
    await session.flush()
    await reserve_window_completion_in_session(
        session, event_id=identity, window_id=window, ingest_id=ingest
    )
    return identity, window, ingest, receipt


async def reserve(**kwargs):
    async with AsyncSessionLocal() as session:
        result = await make(session, **kwargs)
        await session.commit()
        return result


async def works(identity):
    async with AsyncSessionLocal() as session:
        return {
            row.stage: row
            for row in await session.scalars(
                select(AccessCompletionWork).where(AccessCompletionWork.event_id == identity)
            )
        }


async def only_stage(identity, stage="visual"):
    async with AsyncSessionLocal() as session:
        for work in await session.scalars(
            select(AccessCompletionWork).where(AccessCompletionWork.event_id == identity)
        ):
            if work.stage != stage:
                work.state = "skipped"
        await session.commit()


async def test_core_reservations_and_decision_are_invisible_then_roll_back_together():
    async with AsyncSessionLocal() as session:
        identity, *_ = await make(session, visitor=True)
        async with AsyncSessionLocal() as observer:
            assert await observer.get(AccessEvent, identity) is None
            assert (
                await observer.scalar(select(func.count()).select_from(AccessCompletionWork)) == 0
            )
        await session.rollback()
    assert await works(identity) == {}


async def test_original_receipt_controls_every_stage_despite_ancient_capture_and_forged_trace():
    identity, window, ingest, receipt = await reserve(visitor=True)
    items = await works(identity)
    assert set(items) == {"visual", "snapshot", "zone_shadow", "visitor_link"}
    for work in items.values():
        assert work.window_id == window and work.origin["ingest_id"] == str(ingest)
        assert work.origin_at == receipt and work.deadline == receipt + timedelta(seconds=300)
    assert items["visitor_link"].state == "waiting"
    assert all(items[stage].state == "queued" for stage in ["visual", "snapshot", "zone_shadow"])
    claims = await AccessCompletionStore().claim(2)
    assert len(claims) == 2 and all(claim.stage != "visitor_link" for claim in claims)
    budgets = [await AccessCompletionStore().assert_owned(claim) - asyncio.get_running_loop().time() for claim in claims]
    assert all(0 < budget <= 90 for budget in budgets)


async def test_duplicate_reservation_never_refreshes_origin_requeues_terminal_or_resets_attempt():
    identity, window, ingest, receipt = await reserve()
    await only_stage(identity)
    store = AccessCompletionStore()
    claim = (await store.claim(1))[0]
    async with AsyncSessionLocal() as session:
        await lock_completion_event_in_session(session, claim)
        await finish_completion_in_session(
            session, claim, state="succeeded", outcome="fixture_result"
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        await reserve_window_completion_in_session(
            session, event_id=identity, window_id=window, ingest_id=ingest
        )
        await session.commit()
    work = (await works(identity))["visual"]
    assert work.state == "succeeded" and work.attempt_count == 1 and work.origin_at == receipt
    assert await store.claim(2) == []


async def test_missing_member_holds_optional_work_without_substituting_enqueue_or_trace_time():
    identity, window, ingest, _ = await reserve()
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM access_completion_work"))
        await reserve_window_completion_in_session(
            session, event_id=identity, window_id=window, ingest_id=uuid.uuid4()
        )
        await session.commit()
    assert all(
        work.state == "held" and work.origin_at is None and work.deadline is None
        for work in (await works(identity)).values()
    )
    assert await AccessCompletionStore().claim(2) == []


async def test_explicit_external_original_origin_is_not_inferred_from_event_source_or_trace():
    async with AsyncSessionLocal() as session:
        now = await completion_store.database_now(session)
        identity = uuid.uuid4()
        session.add(
            AccessEvent(
                id=identity,
                registration_number="AB12CDE",
                direction=AccessDirection.EXIT,
                decision=AccessDecision.DENIED,
                confidence=0.9,
                source="gate_state_changed",
                occurred_at=CAPTURED,
                raw_payload={"webhook_trace": {"received_at": "2099-01-01T00:00:00+00:00"}},
            )
        )
        await session.flush()
        origin = ExternalCompletionOrigin(uuid.uuid4(), now - timedelta(seconds=1))
        await reserve_external_completion_in_session(session, event_id=identity, origin=origin)
        await session.commit()
    assert all(
        work.origin_at == origin.observed_at and work.window_id is None
        for work in (await works(identity)).values()
    )


async def test_external_fallback_refuses_to_replace_an_events_durable_ingest_origin():
    identity, window, ingest, _ = await reserve()
    async with AsyncSessionLocal() as session:
        row = await session.get(LprIngestEvent, ingest)
        row.access_event_id = identity
        await session.execute(text("DELETE FROM access_completion_work"))
        await reserve_external_completion_in_session(
            session,
            event_id=identity,
            origin=ExternalCompletionOrigin(
                uuid.uuid4(), await completion_store.database_now(session)
            ),
        )
        await session.commit()
    assert all(
        work.state == "held" and work.origin_at is None for work in (await works(identity)).values()
    )


@pytest.mark.parametrize("slots", [-1, 3, True, "2"])
async def test_claim_rejects_capacity_larger_than_actual_owned_slots(slots):
    with pytest.raises(ValueError, match="free two slots"):
        await AccessCompletionStore().claim(slots)


async def test_claim_projects_metadata_and_leases_only_free_slots_with_no_hidden_job_queue():
    for _ in range(14):
        await reserve()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sql_event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        claims = await AccessCompletionStore().claim(2)
    finally:
        sql_event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert len(claims) == 2
    query = next(
        value for value in statements if "LIMIT" in value and "access_completion_work" in value
    )
    assert (
        "access_completion_work.id" in query
        and "access_completion_work.origin," not in query
        and "access_completion_work.result" not in query
    )
    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AccessCompletionWork)
                .where(AccessCompletionWork.state == "leased")
            )
            == 2
        )
    assert await AccessCompletionStore().claim(0) == []


async def test_expired_full_metadata_prefix_does_not_hide_ready_work():
    for _ in range(12):
        await reserve(offset=-400)
    identity, *_ = await reserve()
    claims = await AccessCompletionStore().claim(1)
    assert len(claims) == 1 and claims[0].event_id == identity
    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AccessCompletionWork)
                .where(AccessCompletionWork.state == "expired")
            )
            == 36
        )


async def test_stage_results_merge_under_event_lock_despite_unrelated_public_revision_changes():
    identity, *_ = await reserve()
    store = AccessCompletionStore()
    claims = await store.claim(2)
    for index, claim in enumerate(claims):
        async with AsyncSessionLocal() as session:
            event = await lock_completion_event_in_session(session, claim)
            event.resource_revision += 1
            event.confidence = 0.8 - index * 0.1
            work = await finish_completion_in_session(
                session, claim, state="succeeded", outcome="accepted_fixture"
            )
            work.result = {"result_revision": 1}
            await assert_completion_before_commit(session, claim, finished=True)
            await session.commit()
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, identity)
        assert event.resource_revision == 3 and event.confidence == pytest.approx(0.7)
    completed = await works(identity)
    assert all(completed[claim.stage].state == "succeeded" for claim in claims)


async def test_result_receipt_domain_mutation_and_required_audit_share_caller_rollback():
    identity, *_ = await reserve()
    claim = (await AccessCompletionStore().claim(1))[0]
    async with AsyncSessionLocal() as session:
        event = await lock_completion_event_in_session(session, claim)
        event.confidence = 0.1
        await finish_completion_in_session(
            session, claim, state="succeeded", outcome="accepted_fixture"
        )
        session.add(
            AuditLog(
                category="fixture",
                action="completion.fixture",
                actor="Synthetic",
                target_id=str(identity),
                outcome="success",
                level="info",
            )
        )
        await assert_completion_before_commit(session, claim, finished=True)
        async with AsyncSessionLocal() as observer:
            assert (await observer.get(AccessEvent, identity)).confidence == 0.9
            assert (await observer.get(AccessCompletionWork, claim.work_id)).state == "leased"
            assert (
                await observer.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.target_id == str(identity))
                )
                == 0
            )
        await session.rollback()
    assert (await works(identity))[claim.stage].state == "leased"


@pytest.mark.parametrize("operation", ["renew", "retry", "finish"])
async def test_blocking_locks_past_expiry_cannot_renew_retry_or_accept_results(operation):
    identity, *_ = await reserve()
    store = AccessCompletionStore()
    claim = (await store.claim(1))[0]
    async with AsyncSessionLocal() as session:
        work = await session.get(AccessCompletionWork, claim.work_id)
        work.lease_until = await completion_store.database_now(session) + timedelta(seconds=0.15)
        await session.commit()
    async with AsyncSessionLocal() as blocker:
        if operation == "finish":
            await blocker.get(AccessEvent, identity, with_for_update=True)
        else:
            await blocker.get(AccessCompletionWork, claim.work_id, with_for_update=True)

        async def execute():
            if operation == "renew":
                await store.renew(claim)
            elif operation == "retry":
                await store.retry(claim)
            else:
                async with AsyncSessionLocal() as session:
                    event = await lock_completion_event_in_session(session, claim)
                    event.confidence = 0.1
                    await finish_completion_in_session(
                        session, claim, state="succeeded", outcome="accepted_fixture"
                    )
                    await session.commit()

        task = asyncio.create_task(execute())
        await asyncio.sleep(0.23)
        assert not task.done()
        await blocker.rollback()
        with pytest.raises(PipelineLeaseLost):
            await task
    work = (await works(identity))[claim.stage]
    assert work.state == "leased"
    async with AsyncSessionLocal() as session:
        assert (await session.get(AccessEvent, identity)).confidence == 0.9


async def test_final_clock_follows_all_domain_result_audit_flushes_and_expiry_rolls_back(
    monkeypatch,
):
    identity, *_ = await reserve()
    claim = (await AccessCompletionStore().claim(1))[0]
    original = completion_store.database_now
    samples = []

    async def checked_clock(session):
        assert not session.dirty and not session.new
        samples.append(1)
        return claim.deadline + timedelta(seconds=1)

    async with AsyncSessionLocal() as session:
        event = await lock_completion_event_in_session(session, claim)
        await finish_completion_in_session(
            session, claim, state="succeeded", outcome="accepted_fixture"
        )
        event.confidence = 0.1
        session.add(
            AuditLog(
                category="fixture",
                action="completion.fixture",
                actor="Synthetic",
                target_id=str(identity),
                outcome="success",
                level="info",
            )
        )
        monkeypatch.setattr(completion_store, "database_now", checked_clock)
        with pytest.raises(PipelineLeaseLost):
            await assert_completion_before_commit(session, claim, finished=True)
        await session.rollback()
    monkeypatch.setattr(completion_store, "database_now", original)
    assert samples and (await works(identity))[claim.stage].state == "leased"


@pytest.mark.parametrize("change", ["registration", "origin_identity", "origin_at", "revision"])
async def test_claim_cannot_accept_changed_origin_registration_or_stage_revision(change):
    identity, *_ = await reserve()
    claim = (await AccessCompletionStore().claim(1))[0]
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, identity, with_for_update=True)
        work = await session.get(AccessCompletionWork, claim.work_id, with_for_update=True)
        if change == "registration":
            event.registration_number = "CD34EFG"
        elif change == "origin_identity":
            work.origin = {**work.origin, "ingest_id": str(uuid.uuid4())}
        elif change == "origin_at":
            work.origin_at += timedelta(seconds=1)
            work.deadline += timedelta(seconds=1)
        else:
            work.expected_revision += 1
        await session.commit()
    async with AsyncSessionLocal() as session:
        await lock_completion_event_in_session(session, claim)
        with pytest.raises(PipelineLeaseLost):
            await finish_completion_in_session(
                session, claim, state="succeeded", outcome="accepted_fixture"
            )


async def test_waiting_visitor_link_keeps_original_provenance_after_provider_expiry():
    identity, *_ = await reserve(offset=-360, visitor=True)
    assert await AccessCompletionStore().claim(2) == []
    work = (await works(identity))["visitor_link"]
    assert work.state == "waiting" and work.deadline < datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessCompletionWork, work.id, with_for_update=True)
        row.state = "queued"
        row.readiness = "retained_result_link"
        row.result = {
            **row.result,
            "requested": {
                "visual": {
                    "result_id": str(uuid.uuid4()),
                    "revision": 1,
                    "accepted_at": (row.origin_at + timedelta(seconds=240)).isoformat(),
                }
            },
        }
        await session.commit()
    claim = (await AccessCompletionStore().claim(1))[0]
    assert claim.stage == "visitor_link" and claim.deadline == work.deadline
    assert 4 < await AccessCompletionStore().assert_owned(claim) - asyncio.get_running_loop().time() <= 5


async def test_result_first_accepted_after_original_deadline_cannot_create_runnable_link():
    identity, *_ = await reserve(offset=-360, visitor=True)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(AccessCompletionWork).where(
                AccessCompletionWork.event_id == identity,
                AccessCompletionWork.stage == "visitor_link",
            )
        )
        row.state = "queued"
        row.readiness = "retained_result_link"
        row.result = {
            **row.result,
            "requested": {
                "visual": {
                    "result_id": str(uuid.uuid4()),
                    "revision": 1,
                    "accepted_at": (row.deadline + timedelta(seconds=1)).isoformat(),
                }
            },
        }
        await session.commit()
    assert await AccessCompletionStore().claim(2) == []
    assert (await works(identity))["visitor_link"].outcome == "retained_result_unavailable"


async def test_cancelled_domain_lock_wait_leaves_no_result_or_mutation_for_restart():
    identity, *_ = await reserve()
    claim = (await AccessCompletionStore().claim(1))[0]
    async with AsyncSessionLocal() as blocker:
        await blocker.get(AccessEvent, identity, with_for_update=True)

        async def accept():
            async with AsyncSessionLocal() as session:
                event = await lock_completion_event_in_session(session, claim)
                event.confidence = 0.1
                await finish_completion_in_session(
                    session, claim, state="succeeded", outcome="accepted_fixture"
                )
                await session.commit()

        task = asyncio.create_task(accept())
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await blocker.rollback()
    assert (await works(identity))[claim.stage].state == "leased"
    async with AsyncSessionLocal() as session:
        assert (await session.get(AccessEvent, identity)).confidence == 0.9


@pytest.mark.parametrize("invalid", [None, False, {}, [], 123, "not-a-uuid"])
async def test_malformed_retained_reference_is_held_once_not_requeued(invalid):
    identity, *_ = await reserve(visitor=True)
    await only_stage(identity, "visitor_link")
    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(AccessCompletionWork).where(
                AccessCompletionWork.event_id == identity,
                AccessCompletionWork.stage == "visitor_link",
            )
        )
        row.state = "queued"
        row.readiness = "retained_result_link"
        row.result = {
            **row.result,
            "requested": {
                "visual": {
                    "result_id": invalid,
                    "revision": 1,
                    "accepted_at": row.origin_at.isoformat(),
                }
            },
        }
        await session.commit()
    assert await AccessCompletionStore().claim(1) == []
    assert (await works(identity))["visitor_link"].state == "held"
    assert await AccessCompletionStore().claim(1) == []


@pytest.mark.parametrize("change", ["unknown_readiness", "boolean_version", "malformed_identity"])
async def test_malformed_origin_or_unknown_media_readiness_is_held_once(change):
    identity, *_ = await reserve()
    await only_stage(identity)
    async with AsyncSessionLocal() as session:
        work = await session.scalar(
            select(AccessCompletionWork).where(
                AccessCompletionWork.event_id == identity, AccessCompletionWork.stage == "visual"
            )
        )
        if change == "unknown_readiness":
            work.readiness = "retired_checkpoint"
        elif change == "boolean_version":
            work.origin = {**work.origin, "version": True}
            # Python compares True == 1; force the deliberate persisted JSONB
            # corruption rather than letting ORM equality suppress its UPDATE.
            flag_modified(work, "origin")
        else:
            work.origin = {**work.origin, "ingest_id": []}
        await session.commit()
    assert await AccessCompletionStore().claim(1) == []
    assert (await works(identity))["visual"].state == "held"
    assert await AccessCompletionStore().claim(1) == []


async def test_retry_preserves_origin_and_clips_due_time_without_authority_refresh():
    identity, *_ = await reserve(offset=-290)
    await only_stage(identity)
    store = AccessCompletionStore()
    claim = (await store.claim(1))[0]
    original = (await works(identity))["visual"]
    assert 0 < await store.assert_owned(claim) - asyncio.get_running_loop().time() < 10
    await store.retry(claim, delay_seconds=90)
    current = (await works(identity))["visual"]
    assert (
        current.due_at == original.deadline
        and current.origin_at == original.origin_at
        and current.deadline == original.deadline
    )
    assert (
        current.origin == original.origin
        and current.expected_revision == original.expected_revision
    )
    assert current.state == "queued" and current.lease_token is None and current.lease_until is None


@pytest.mark.parametrize("change", ["still_open", "other_event", "other_plate"])
async def test_origin_reservation_holds_a_nonfinalizing_or_wrong_window_member(change):
    identity, window, ingest, _ = await reserve()
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM access_completion_work"))
        row = await session.get(LprDebounceWindow, window)
        if change == "still_open":
            row.state = "open"
        elif change == "other_event":
            other = AccessEvent(
                id=uuid.uuid4(),
                registration_number="AB12CDE",
                direction=AccessDirection.ENTRY,
                decision=AccessDecision.GRANTED,
                confidence=0.9,
                source="synthetic",
                occurred_at=CAPTURED,
            )
            session.add(other)
            await session.flush()
            row.state = "completed"
            row.access_event_id = other.id
        else:
            member = await session.get(LprWindowMember, ingest)
            member.prepared_context = {"registration_number": "CD34EFG", "annotations": {}}
        await reserve_window_completion_in_session(
            session, event_id=identity, window_id=window, ingest_id=ingest
        )
        await session.commit()
    assert all(
        work.state == "held" and work.origin_at is None for work in (await works(identity)).values()
    )
