"""Durable debounce/evidence parity on the disposable migrated PostgreSQL namespace."""
import asyncio
import gc
import hashlib
import json
import time
import tracemalloc
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event as sql_event
from sqlalchemy import func, select, text
from test_recovery_boundaries import isolated_resources as isolated_resources

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, LprCoordinatorState, LprDebounceWindow, LprIngestEvent, LprObservationToken, LprWindowMember, MovementSagaRecord
from app.models.enums import AccessDecision, AccessDirection
from app.modules.lpr.base import PlateRead
from app.modules.lpr.ubiquiti import UbiquitiLprAdapter
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.pipeline_store import AccessPipelineStore, assert_window_in_session, database_now, terminalize_window_in_session
from app.services.access.read_codec import CLASSIFICATION_KEYS, read_from_ingest, read_from_member
from app.services.access.reads import KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY, VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY
from app.services.access.window_store import AccessWindowStore
from app.services.access import observation_tokens
from app.services.access.observation_tokens import exact_token_match, token_digest
from app.services.access.window_candidates import candidate_page_in_session
from app.services.movement.sessions import candidate_registration_numbers
from scripts.benchmarks.evidence import write_evidence
from app.services.lpr_ingest import LprIngestRepository

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def window_resources(isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE lpr_coordinator_state, lpr_ingest_events CASCADE"))
        await session.commit()


def value(plate="AB12CDE", seconds=0, confidence=.9, source="synthetic", **payload):
    return PlateRead(plate, confidence, source, NOW+timedelta(seconds=seconds), payload)


async def ready(read, key=None, received_at=NOW):
    repository = LprIngestRepository()
    row, _ = await repository.persist_read(read, received_at=received_at, idempotency_key=key or str(uuid.uuid4()),
        normalized_payload={"registration_number":read.registration_number,"confidence":read.confidence,
            "source":read.source,"captured_at":read.captured_at.isoformat(),"raw_payload":read.raw_payload,
            "candidate_registration_numbers":list(read.candidate_registration_numbers)})
    result = await repository.complete_observation(row.id, row.capture_token, {"state":"closed","observed_at":NOW.isoformat()})
    assert result is not None
    return result.id


async def claim_read(pipeline, coordinator, classified):
    identity = await ready(classified)
    claim = await pipeline.claim_next_read(coordinator)
    assert claim is not None and claim.ingest_id == identity
    async with AsyncSessionLocal() as session:
        row = await session.get(LprIngestEvent, identity)
        canonical = read_from_ingest(row)
    # These annotations stand in for the real code-owned classification result;
    # canonical decode itself never trusts annotations in the original payload.
    read = replace(canonical, registration_number=classified.registration_number,
        raw_payload={**canonical.raw_payload, **{key:classified.raw_payload[key]
            for key in CLASSIFICATION_KEYS if key in classified.raw_payload}})
    return claim, read


async def append(pipeline, windows, coordinator, classified, *, flush=True, threshold=1):
    claim, read = await claim_read(pipeline, coordinator, classified)
    async with AsyncSessionLocal() as session:
        summary = await windows.append_in_session(session, claim, read, threshold=threshold)
        if flush:
            await pipeline.begin_flush_in_session(session, claim)
        await session.commit()
    if flush:
        assert await pipeline.next_flush_window(coordinator, quiet_seconds=1e9, max_seconds=1e9) is None
    return claim, read, summary


async def commit_window(windows, claim, *, visitor_seconds=None):
    event_id, saga_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(AccessEvent(id=event_id, registration_number="AB12CDE", direction=AccessDirection.EXIT,
            decision=AccessDecision.GRANTED, confidence=1, source="synthetic", occurred_at=NOW, resource_revision=1))
        await session.flush()
        session.add(MovementSagaRecord(id=saga_id, idempotency_key=str(saga_id), source="synthetic",
            occurred_at=NOW, access_event_id=event_id))
        await session.flush()
        await terminalize_window_in_session(session, claim, event_id=event_id, saga_id=saga_id)
        if visitor_seconds is not None:
            await windows.activate_visitor_resolution_in_session(session, claim, max_seconds=visitor_seconds)
        await assert_window_in_session(session, claim, completed=True)
        await session.commit()


async def visitor_marker(pipeline, windows, coordinator, *, seconds=10, source="synthetic"):
    claim, read, summary = await append(pipeline, windows, coordinator, value(seconds=seconds, source=source,
        **{VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY:{"kind":"departure"}}), flush=False)
    async with AsyncSessionLocal() as session:
        window_claim = await windows.claim_immediate_in_session(session, claim, summary.evidence.window_id,
            reason="visitor_pass_departure_plate")
        await session.commit()
    await commit_window(windows, window_claim, visitor_seconds=10)
    assert await pipeline.next_flush_window(coordinator, quiet_seconds=1e9, max_seconds=1e9) is None
    return window_claim


async def marker_check(pipeline, windows, coordinator, read):
    claim, prepared = await claim_read(pipeline, coordinator, read)
    async with AsyncSessionLocal() as session:
        result = await windows.suppress_after_visitor_resolution_in_session(session, claim, prepared)
        await pipeline.suppress_read_in_session(session, claim, reason="inert-marker-check")
        await session.commit()
    assert await pipeline.next_flush_window(coordinator, quiet_seconds=1e9, max_seconds=1e9) is None
    return result


async def test_grouping_has_no_time_filter_and_preserves_first_best_ties_and_append_clock():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    first, _, summary = await append(pipeline, windows, coordinator, value(seconds=100))
    earlier, _, second = await append(pipeline, windows, coordinator, value(seconds=10))
    tie, _, third = await append(pipeline, windows, coordinator, value(seconds=100))
    assert summary.evidence == second.evidence == third.evidence
    async with AsyncSessionLocal() as session:
        loaded = await windows.load_in_session(session, summary.evidence.window_id)
        assert loaded.summary.first_seen == NOW+timedelta(seconds=100)
        assert loaded.summary.updated_at == NOW+timedelta(seconds=100)
        assert loaded.summary.first_ingest_id == earlier.ingest_id
        assert loaded.summary.best_ingest_id == first.ingest_id
        assert [str(item.ingest_id) for item in loaded.preview] == [str(item.ingest_id) for item in (first,earlier,tie)]
    _, _, regressed = await append(pipeline, windows, coordinator, value(seconds=-500))
    assert regressed.updated_at == NOW-timedelta(seconds=500)
    assert regressed.first_seen == NOW+timedelta(seconds=100)
    _, _, other_source = await append(pipeline, windows, coordinator, value(source="other"))
    assert other_source.evidence != summary.evidence


async def test_first_matching_window_search_crosses_bounded_pages():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    summaries=[]
    for index in range(55):
        _, _, summary = await append(pipeline, windows, coordinator, value(plate=f"P{index:06d}"))
        summaries.append(summary)
    _, _, found = await append(pipeline, windows, coordinator, value(plate="P000054", seconds=-1000))
    assert found.evidence == summaries[54].evidence and found.count == 2
    # With a broad threshold more than one window matches. It remains the first
    # open window, not the exact or latest candidate later in the page stream.
    _, _, first = await append(pipeline, windows, coordinator, value(plate="P000054"), threshold=.1)
    assert first.evidence == summaries[0].evidence


async def test_merge_concatenates_selected_then_related_open_order_and_preserves_rank_ties():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    a, _, one = await append(pipeline, windows, coordinator, value("AAAAAAA", seconds=90))
    b, _, two = await append(pipeline, windows, coordinator, value("BBBBBBB", seconds=90))
    selected_first, _, selected = await append(pipeline, windows, coordinator, value("CCCCCCC", seconds=100))
    selected_early, _, _ = await append(pipeline, windows, coordinator, value("CCCCCCC", seconds=90))
    anchor, prepared = await claim_read(pipeline, coordinator, value("CCCCCCC", seconds=100))
    async with AsyncSessionLocal() as session:
        await windows.append_in_session(session, anchor, prepared, threshold=1)
        merged = await windows.merge_related_in_session(session, anchor, selected.evidence.window_id,
            anchor_read=prepared, max_seconds=20)
        await pipeline.begin_flush_in_session(session, anchor)
        await session.commit()
    assert merged.first_seen == NOW+timedelta(seconds=90) and merged.updated_at == NOW+timedelta(seconds=100)
    assert merged.first_ingest_id == selected_early.ingest_id
    assert merged.best_ingest_id == selected_first.ingest_id
    async with AsyncSessionLocal() as session:
        members = list(await session.scalars(select(LprWindowMember.ingest_id).where(
            LprWindowMember.window_id==selected.evidence.window_id).order_by(LprWindowMember.ordinal)))
        assert members == [selected_first.ingest_id,selected_early.ingest_id,anchor.ingest_id,a.ingest_id,b.ingest_id]
        assert all([(await session.get(LprDebounceWindow,item.evidence.window_id)).state=="merged" for item in (one,two)])
        assert set(await session.scalars(select(LprIngestEvent.window_id))) == {selected.evidence.window_id}


async def test_preview_is_bounded_but_full_membership_and_tokens_include_late_evidence():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    for index in range(70):
        _, _, summary = await append(pipeline, windows, coordinator,
            value(seconds=index, confidence=index/70, eventId=f"protect-{index}"))
    async with AsyncSessionLocal() as session:
        loaded = await windows.load_in_session(session, summary.evidence.window_id)
        assert len(loaded.preview)==50 and loaded.summary.count==70
        assert loaded.best_read.raw_payload["eventId"]=="protect-69"
        assert not hasattr(loaded,"reads")
        assert await session.scalar(select(func.count()).select_from(LprWindowMember))==70
        assert await session.scalar(select(func.count()).select_from(LprObservationToken).where(LprObservationToken.kind=="protect_event"))==70
        late = await session.scalar(select(LprObservationToken).where(LprObservationToken.value=="protect-69"))
        assert late.ingest_id==loaded.summary.best_ingest_id


async def test_membership_tokens_and_summary_rollback_together_and_cannot_change_classification():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    claim, prepared = await claim_read(pipeline, coordinator, value(eventId="original"))
    async with AsyncSessionLocal() as session:
        summary=await windows.append_in_session(session, claim, prepared, threshold=1)
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.get(LprWindowMember,claim.ingest_id) is None
        assert await session.scalar(select(func.count()).select_from(LprObservationToken))==0
        assert await session.get(LprDebounceWindow,summary.evidence.window_id) is None
        summary=await windows.append_in_session(session, claim, prepared, threshold=1)
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(PipelineLeaseLost,match="classification_changed"):
            await windows.append_in_session(session, claim, replace(prepared,registration_number="CHANGED"), threshold=1)
        await session.rollback()
        member=await session.get(LprWindowMember,claim.ingest_id)
        row=await session.get(LprIngestEvent,claim.ingest_id)
        assert read_from_member(row,member.prepared_context).registration_number==prepared.registration_number


async def test_takeover_finishes_same_flush_before_next_ready_and_tolerates_sequence_gaps():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    claim, _, summary = await append(pipeline, windows, coordinator, value(), flush=False)
    async with AsyncSessionLocal() as session:
        await pipeline.begin_flush_in_session(session,claim)
        await session.commit()
        saved=await session.get(LprCoordinatorState,1)
        flush_clock=saved.flush_clock
        await session.execute(text("SELECT nextval('lpr_ready_ordinal_seq') FROM generate_series(1,7)"))
        await session.commit()
    next_identity=await ready(value("ZZZZZZZ"))
    with pytest.raises(PipelineLeaseLost,match="unfinished_read_flush"):
        await pipeline.claim_next_read(coordinator)
    async with AsyncSessionLocal() as session:
        row=await session.get(LprCoordinatorState,1,with_for_update=True)
        row.lease_until=await database_now(session)-timedelta(seconds=1)
        await session.commit()
    takeover=await pipeline.acquire_coordinator()
    finalizing=await pipeline.next_flush_window(takeover,quiet_seconds=0,max_seconds=0)
    assert finalizing.window_id==summary.evidence.window_id
    with pytest.raises(PipelineLeaseLost):
        await pipeline.claim_next_read(coordinator)
    with pytest.raises(PipelineLeaseLost,match="unfinished_read_flush"):
        await pipeline.claim_next_read(takeover)
    async with AsyncSessionLocal() as session:
        assert (await session.get(LprCoordinatorState,1)).flush_clock==flush_clock
        assert (await session.get(LprIngestEvent,next_identity)).status=="pending"
    await commit_window(windows,finalizing)
    assert await pipeline.next_flush_window(takeover,quiet_seconds=0,max_seconds=0) is None
    second=await pipeline.claim_next_read(takeover)
    assert second.ingest_id==next_identity


@pytest.mark.parametrize("offset,expected", [(9,False),(10,True),(15,True),(20,True),(21,False)])
async def test_visitor_marker_source_and_inclusive_interval_survive_restart(offset,expected):
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    marker=await visitor_marker(pipeline,windows,coordinator)
    # Fresh owner objects prove state is durable, not a retained Python list.
    assert await marker_check(AccessPipelineStore(),AccessWindowStore(),coordinator,value(seconds=offset)) is expected
    async with AsyncSessionLocal() as session:
        stored=await session.get(LprDebounceWindow,marker.window_id)
        assert stored.visitor_resolution_expires_at==NOW+timedelta(seconds=20)


@pytest.mark.parametrize("exemption", [None,"exact","visitor"])
async def test_global_existing_marker_prune_before_exemptions_is_irreversible(exemption):
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    marker=await visitor_marker(pipeline,windows,coordinator)
    annotations={KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY:{"exact":True}} if exemption=="exact" else (
        {VISITOR_PASS_PLATE_MATCH_PAYLOAD_KEY:{"kind":"departure"}} if exemption=="visitor" else {})
    assert not await marker_check(pipeline,windows,coordinator,value(seconds=100,source="other",**annotations))
    assert not await marker_check(pipeline,windows,coordinator,value(seconds=11))
    async with AsyncSessionLocal() as session:
        stored=await session.get(LprDebounceWindow,marker.window_id)
        assert not stored.visitor_resolution_active
        assert stored.visitor_resolution_expires_at==NOW+timedelta(seconds=20)


async def test_new_old_timestamp_marker_after_high_capture_remains_active_until_later_prune():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    assert not await marker_check(pipeline,windows,coordinator,value(seconds=100))
    await visitor_marker(pipeline,windows,coordinator,seconds=10)
    assert await marker_check(pipeline,windows,coordinator,value(seconds=11))
    assert not await marker_check(pipeline,windows,coordinator,value(seconds=11,source="other"))


async def test_visitor_marker_activation_is_immutable_idempotent_and_not_reactivated():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    marker=await visitor_marker(pipeline,windows,coordinator)
    assert not await marker_check(pipeline,windows,coordinator,value(seconds=100))
    async with AsyncSessionLocal() as session:
        await windows.activate_visitor_resolution_in_session(session,marker,max_seconds=999)
        await session.commit()
        stored=await session.get(LprDebounceWindow,marker.window_id)
        assert stored.visitor_resolution_expires_at==NOW+timedelta(seconds=20)
        assert not stored.visitor_resolution_active


async def test_merge_offsets_complete_related_pages_without_uuid_order_or_per_read_copy():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    identities=[]
    for index in range(55):
        claim, _, _ = await append(pipeline,windows,coordinator,value(plate=f"M{index:06d}",seconds=90))
        identities.append(claim.ingest_id)
    selected, prepared, summary = await append(pipeline,windows,coordinator,value(plate="SELECTED",seconds=100),flush=False)
    async with AsyncSessionLocal() as session:
        merged=await windows.merge_related_in_session(session,selected,summary.evidence.window_id,
            anchor_read=prepared,max_seconds=20)
        await pipeline.begin_flush_in_session(session,selected)
        await session.commit()
        members=list(await session.scalars(select(LprWindowMember.ingest_id).where(
            LprWindowMember.window_id==summary.evidence.window_id).order_by(LprWindowMember.ordinal)))
        assert members==[selected.ingest_id,*identities]
        assert merged.count==56 and merged.first_seen==NOW+timedelta(seconds=90)
        assert await session.scalar(select(func.count()).select_from(LprDebounceWindow).where(LprDebounceWindow.state=="merged"))==55


async def test_still_live_old_ingest_claim_cannot_cross_current_read_boundary():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    old, old_read, _ = await append(pipeline,windows,coordinator,value())
    current, _ = await claim_read(pipeline,coordinator,value("CURRENT"))
    async with AsyncSessionLocal() as session:
        with pytest.raises(PipelineLeaseLost,match="read_boundary_changed"):
            await windows.append_in_session(session,old,old_read,threshold=1)
        await session.rollback()
        with pytest.raises(PipelineLeaseLost,match="read_boundary_changed"):
            await windows.suppress_after_visitor_resolution_in_session(session,old,old_read)
        await session.rollback()
        assert (await session.get(LprCoordinatorState,1)).boundary_ingest_id==current.ingest_id


async def test_recover_preparing_keeps_original_read_and_blocks_next_ready():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    old, original = await claim_read(pipeline,coordinator,value(eventId="first"))
    await ready(value("SECOND"))
    async with AsyncSessionLocal() as session:
        saved=await session.get(LprIngestEvent,old.ingest_id)
        frozen=(saved.normalized_payload,saved.original_observation,saved.received_at,saved.ready_ordinal)
        row=await session.get(LprCoordinatorState,1,with_for_update=True)
        row.lease_until=await database_now(session)-timedelta(seconds=1)
        await session.commit()
    takeover=await pipeline.acquire_coordinator()
    recovered=await pipeline.recover_preparing_read(takeover)
    assert recovered.ingest_id==old.ingest_id and recovered.token!=old.token
    with pytest.raises(PipelineLeaseLost,match="unfinished_read_flush"):
        await pipeline.claim_next_read(takeover)
    async with AsyncSessionLocal() as session:
        saved=await session.get(LprIngestEvent,recovered.ingest_id)
        assert (saved.normalized_payload,saved.original_observation,saved.received_at,saved.ready_ordinal)==frozen
        assert saved.attempt_count==2
        read=read_from_ingest(saved)
        assert read.raw_payload["eventId"]==original.raw_payload["eventId"]
        await windows.append_in_session(session,recovered,read,threshold=1)
        await pipeline.begin_flush_in_session(session,recovered)
        await session.commit()


async def test_cancellation_rolls_back_membership_and_leaves_preparing_read_recoverable():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    claim, prepared = await claim_read(pipeline,coordinator,value(eventId="cancelled"))
    flushed=asyncio.Event()
    async def transaction():
        async with AsyncSessionLocal() as session:
            await windows.append_in_session(session,claim,prepared,threshold=1)
            flushed.set()
            await asyncio.Event().wait()
            await session.commit()
    task=asyncio.create_task(transaction())
    await asyncio.wait_for(flushed.wait(),5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(LprWindowMember))==0
        assert await session.scalar(select(func.count()).select_from(LprObservationToken))==0
        assert await session.scalar(select(func.count()).select_from(LprDebounceWindow))==0
        coordinator_row=await session.get(LprCoordinatorState,1)
        assert coordinator_row.boundary_ingest_id==claim.ingest_id and coordinator_row.boundary_phase=="preparing"


async def test_blocked_selected_window_past_expiry_cannot_merge_or_set_anchor():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    claim, prepared, summary = await append(pipeline,windows,coordinator,value(),flush=False)
    async with AsyncSessionLocal() as blocker:
        await blocker.get(LprDebounceWindow,summary.evidence.window_id,with_for_update=True)
        async with AsyncSessionLocal() as session:
            row=await session.get(LprCoordinatorState,1,with_for_update=True)
            expiry=await database_now(session)+timedelta(milliseconds=500)
            row.lease_until=expiry
            ingest=await session.get(LprIngestEvent,claim.ingest_id,with_for_update=True)
            ingest.lease_until=expiry
            await session.commit()
        async def merge():
            async with AsyncSessionLocal() as session:
                await windows.merge_related_in_session(session,claim,summary.evidence.window_id,
                    anchor_read=prepared,max_seconds=10)
                await session.commit()
        task=asyncio.create_task(merge())
        try:
            async with AsyncSessionLocal() as observer:
                async def blocked_and_expired():
                    while not await observer.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock')")):
                        await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                        await asyncio.sleep(.01)
                    while await database_now(observer)<=expiry:
                        await asyncio.sleep(.01)
                await asyncio.wait_for(blocked_and_expired(),5)
            await blocker.rollback()
            with pytest.raises(PipelineLeaseLost):
                await asyncio.wait_for(task,5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
    async with AsyncSessionLocal() as observer:
        assert (await observer.get(LprDebounceWindow,summary.evidence.window_id)).anchor_ingest_id is None


async def test_window_receipt_origin_ignores_forged_future_diagnostic_trace():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    future=(NOW+timedelta(days=500)).isoformat()
    identity=await ready(value(webhook_trace={"received_at":future},_iacs_ingest={"webhook_received_at":future}),
        received_at=NOW-timedelta(seconds=5))
    claim=await pipeline.claim_next_read(coordinator)
    assert claim.ingest_id==identity
    async with AsyncSessionLocal() as session:
        read=read_from_ingest(await session.get(LprIngestEvent,identity))
        assert read.raw_payload["webhook_trace"]["received_at"]==future
        summary=await windows.append_in_session(session,claim,read,threshold=1)
        assert summary.min_received_at==NOW-timedelta(seconds=5)
        await pipeline.begin_flush_in_session(session,claim)
        await session.commit()


async def test_terminal_metadata_page_continues_to_pending_and_hydrates_only_selected_body():
    pipeline=AccessPipelineStore()
    coordinator=await pipeline.acquire_coordinator()
    async with AsyncSessionLocal() as session:
        await session.execute(text("""INSERT INTO lpr_ingest_events
            (id,idempotency_key,source,registration_number,captured_at,received_at,normalized_payload,
             status,observation_state,original_observation,ready_ordinal,attempt_count)
            SELECT gen_random_uuid(),'terminal-page-'||n,'synthetic','AB12CDE',:now,:now,
              jsonb_build_object('raw_payload',jsonb_build_object('irrelevant',repeat('x',900000))),
              'succeeded','ready',jsonb_build_object('state','closed','observed_at',CAST(:observed AS text)),
              nextval('lpr_ready_ordinal_seq'),0 FROM generate_series(1,50) n"""),
            {'now':NOW,'observed':NOW.isoformat()})
        await session.commit()
    identity=await ready(value("PENDING"))
    statements=[]
    def capture(connection,cursor,statement,parameters,context,many):
        if statement.lstrip().startswith('SELECT'):
            statements.append(statement)
    sql_event.listen(engine.sync_engine,'before_cursor_execute',capture)
    try:
        claim=await pipeline.claim_next_read(coordinator)
    finally:
        sql_event.remove(engine.sync_engine,'before_cursor_execute',capture)
    assert claim.ingest_id==identity
    metadata=[statement for statement in statements if 'lpr_ingest_events' in statement and 'LIMIT' in statement]
    assert len(metadata)==2 and all('normalized_payload' not in statement for statement in metadata)
    full=[statement for statement in statements if 'lpr_ingest_events.normalized_payload' in statement]
    assert full and all('WHERE lpr_ingest_events.id =' in statement for statement in full)


async def test_compact_candidate_projection_sanitizes_trace_and_paginates_complete_membership():
    pipeline, windows=AccessPipelineStore(),AccessWindowStore()
    coordinator=await pipeline.acquire_coordinator()
    classified=value(webhook_trace={'request_id':'r'*10000,'path':{'nested':'x'*900000},
        'received_at':(NOW+timedelta(days=500)).isoformat(),'extra':'x'*100000,
        'captured_to_webhook_ms':{'wrong':'shape'}},irrelevant_vendor_blob='x'*900000,
        **{KNOWN_VEHICLE_PLATE_MATCH_PAYLOAD_KEY:{'exact':True,'similarity':1,'threshold':.8,
            'detected_registration_number':'AB12CDX','registration_number':'AB12CDE',
            'normalized_registration_number':'AB12CDE','irrelevant':'x'*100000}})
    _,_,summary=await append(pipeline,windows,coordinator,classified)
    async with AsyncSessionLocal() as session:
        loaded=await windows.load_in_session(session,summary.evidence.window_id)
        candidate=loaded.preview[0]
        assert candidate.detected_registration_number=='AB12CDX'
        assert candidate.known_vehicle_plate_match['exact'] is True
        assert 'irrelevant' not in candidate.known_vehicle_plate_match
        assert len(candidate.webhook_trace['request_id'])==256
        assert 'path' not in candidate.webhook_trace and 'extra' not in candidate.webhook_trace
        assert 'captured_to_webhook_ms' not in candidate.webhook_trace
        assert len(json.dumps(candidate.as_payload()))<2000
        assert len(loaded.best_read.raw_payload['irrelevant_vendor_blob'])==900000
        assert await candidate_page_in_session(session,summary.evidence.window_id,after_ordinal=0)==()
        with pytest.raises(ValueError):
            await candidate_page_in_session(session,summary.evidence.window_id,limit=51)


@pytest.mark.parametrize('family,count,candidate_count',[
    ('fat-small',4,0),('fat-target',60,0),('candidate-small',4,5000),('candidate-target',12,5000)])
async def test_near_limit_webhook_preview_memory_output_and_query_plans(tmp_path,family,count,candidate_count):
    pipeline,windows=AccessPipelineStore(),AccessWindowStore()
    coordinator=await pipeline.acquire_coordinator()
    body={'registrationNumber':'AB12CDE','confidence':.9,'capturedAt':NOW.isoformat(),
        'webhook_trace':{'request_id':'r'*4000,'received_at':NOW.isoformat(),'unused':'t'*1000},
        'alarm':{'triggers':[{'key':'license_plate','value':f'Q{index:06d}'} for index in range(candidate_count)]}}
    body['irrelevant_vendor_blob']='v'*(980000-len(json.dumps(body,separators=(',',':')))-29)
    input_bytes=len(json.dumps(body,separators=(',',':')).encode())
    assert 950000<input_bytes<1048576
    template=UbiquitiLprAdapter().to_plate_read(body)
    assert len(template.candidate_registration_numbers)==candidate_count+1
    for index in range(count):
        read=replace(template,captured_at=NOW+timedelta(seconds=index),
            confidence=1 if index==count-2 else .5 if index==count-1 else .9)
        claim,prepared,summary=await append(pipeline,windows,coordinator,read,flush=index<count-1)
    async with AsyncSessionLocal() as session:
        await windows.merge_related_in_session(session,claim,summary.evidence.window_id,
            anchor_read=prepared,max_seconds=20)
        await pipeline.begin_flush_in_session(session,claim)
        await session.commit()
    gc.collect()
    tracemalloc.start()
    started=time.perf_counter()
    async with AsyncSessionLocal() as session:
        rows=(await session.execute(select(LprIngestEvent,LprWindowMember.prepared_context)
            .join(LprWindowMember,LprWindowMember.ingest_id==LprIngestEvent.id)
            .where(LprWindowMember.window_id==summary.evidence.window_id)
            .order_by(LprWindowMember.ordinal))).all()
        legacy=[read_from_member(row,context) for row,context in rows]
    before_seconds=time.perf_counter()-started
    _,before_peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    expected=[(read.registration_number,read.confidence,read.captured_at,
        candidate_registration_numbers(read)) for read in legacy[:50]]
    legacy_body_bytes=sum(len(json.dumps(read.raw_payload).encode()) for read in legacy)
    del rows,legacy
    gc.collect()
    captured=[]
    def capture(connection,cursor,statement,parameters,context,many):
        if statement.lstrip().startswith('SELECT'):
            captured.append((statement,parameters))
    sql_event.listen(engine.sync_engine,'before_cursor_execute',capture)
    tracemalloc.start()
    started=time.perf_counter()
    try:
        async with AsyncSessionLocal() as session:
            loaded=await windows.load_in_session(session,summary.evidence.window_id)
    finally:
        after_seconds=time.perf_counter()-started
        _,after_peak=tracemalloc.get_traced_memory()
        tracemalloc.stop()
        sql_event.remove(engine.sync_engine,'before_cursor_execute',capture)
    assert [(item.registration_number,item.confidence,item.captured_at,item.candidate_registration_numbers)
        for item in loaded.preview]==expected
    assert len(loaded.preview)==min(count,50) and loaded.summary.count==count
    assert len({loaded.summary.first_ingest_id,loaded.summary.best_ingest_id,loaded.summary.anchor_ingest_id})==3
    assert len(captured)==3
    assert 'lpr_ingest_events.normalized_payload,' in captured[1][0] and 'LIMIT' in captured[1][0]
    assert 'lpr_ingest_events.normalized_payload,' not in captured[2][0]
    compact_bytes=len(json.dumps([item.as_payload() for item in loaded.preview]).encode())
    assert all(len(item.webhook_trace['request_id'])==256 for item in loaded.preview)
    assert all('unused' not in item.webhook_trace for item in loaded.preview)
    if family=='fat-target':
        assert after_peak<before_peak/3
    async with AsyncSessionLocal() as session:
        tokens=await session.scalar(select(func.count()).select_from(LprObservationToken).where(LprObservationToken.kind=='ocr'))
        assert tokens==count*(candidate_count+1)
        plans=[]
        connection=await session.connection()
        for statement,parameters in captured:
            plan=(await connection.exec_driver_sql('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+statement,parameters)).scalar_one()
            plans.append({'sql':statement,'parameter_count':len(parameters),'plan':plan})
    write_evidence(tmp_path/'window-preview-evidence.json',{
        'kind':'synthetic-load-phase-comparison','family':family,'window_members':count,
        'webhook_input_bytes':input_bytes,'candidates_per_observation':candidate_count+1,
        'before':{'full_vendor_rows':count,'load_peak_tracemalloc_bytes':before_peak,
            'load_seconds':before_seconds,'raw_payload_json_bytes':legacy_body_bytes},
        'after':{'full_anchor_rows':3,'compact_preview_rows':len(loaded.preview),
            'load_peak_tracemalloc_bytes':after_peak,'load_seconds':after_seconds,
            'compact_preview_json_bytes':compact_bytes,'query_count':len(captured)},
        'complete_ocr_token_count':tokens,'equivalent_candidate_display_fields':True,
        'plans':plans,'limits':'Python allocation peaks cover load/decode only, not input preparation, PostgreSQL memory or process RSS. Three full anchors remain near1MiB each. Relevant OCR arrays are preserved and remain variable-size; no fixed-tiny-memory claim. Timings are local synthetic observations, not production guarantees.'})


async def test_long_adapter_tokens_retain_full_text_order_and_exact_matches():
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    event_id = "x" * 513
    similar_id = "x" * 512 + "y"
    # Deterministic high entropy: exceeds a B-tree tuple even when compressed.
    large_id = "".join(hashlib.sha256(f"token-fixture-{index}".encode()).hexdigest() for index in range(160))
    assert len(large_id.encode()) == 10240
    path_id = "é𐐀" * 600
    candidate = "Z" * 1025
    body = {"registrationNumber": "AB12CDE", "capturedAt": NOW.isoformat(),
        "eventId": [event_id, similar_id, large_id, event_id], "nested": {"eventPath": "/event/" + path_id + "?a=1"},
        "alarm": {"triggers": [{"key": "license_plate", "value": candidate}]}}
    assert len(json.dumps(body).encode()) < 1048576
    read = UbiquitiLprAdapter().to_plate_read(body)
    claim, _, summary = await append(pipeline, windows, coordinator, read)
    async with AsyncSessionLocal() as session:
        tokens = list(await session.scalars(select(LprObservationToken)
            .where(LprObservationToken.ingest_id == claim.ingest_id)
            .order_by(LprObservationToken.kind, LprObservationToken.ordinal)))
        assert [row.value for row in tokens if row.kind == 'protect_event'] == sorted([event_id, similar_id, large_id, path_id])
        assert [row.value for row in tokens if row.kind == 'ocr'] == sorted(['AB12CDE', candidate])
        assert all(row.value_digest == token_digest(row.value) and len(row.value_digest) == 32 for row in tokens)
        for kind, token in [('protect_event',event_id),('protect_event',similar_id),('protect_event',large_id),('protect_event',path_id),('ocr',candidate)]:
            matches = list(await session.scalars(select(LprObservationToken.value).where(exact_token_match(kind, token))))
            assert matches == [token]
        loaded = await windows.load_in_session(session, summary.evidence.window_id)
        assert candidate in loaded.preview[0].candidate_registration_numbers


async def test_digest_collision_retains_distinct_tokens_and_never_matches_other_text(monkeypatch):
    monkeypatch.setattr(observation_tokens, 'token_digest', lambda value: b'x' * 32)
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    first, second = 'a' * 900, 'b' * 900
    claim, _, _ = await append(pipeline, windows, coordinator, value(eventId=[second,first,first]))
    async with AsyncSessionLocal() as session:
        rows = list(await session.scalars(select(LprObservationToken).where(
            LprObservationToken.ingest_id == claim.ingest_id, LprObservationToken.kind == 'protect_event')
            .order_by(LprObservationToken.ordinal)))
        assert [row.value for row in rows] == [first,second]
        assert len({row.value_digest for row in rows}) == 1
        for token in (first,second):
            assert list(await session.scalars(select(LprObservationToken.value).where(
                exact_token_match('protect_event',token)))) == [token]
        assert not list(await session.scalars(select(LprObservationToken.value).where(
            exact_token_match('protect_event','c' * 900))))
