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


async def test_complete_session_evidence_beyond_preview_and_suppression_commit_atomically():
    from app.models import MovementSessionObservation, MovementSessionRecord
    from app.services.movement.sessions import MovementSessionService
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    for ordinal in range(80):
        _, _, summary = await append(pipeline, windows, coordinator,
            value(seconds=ordinal, eventId=f"full-evidence-{ordinal}"))
    claim = await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0)
    sessions = MovementSessionService()
    async with AsyncSessionLocal() as session:
        window = await windows.load_in_session(session, summary.evidence.window_id)
        assert len(window.preview) == 50 and window.summary.count == 80
        event = AccessEvent(registration_number="AB12CDE", direction=AccessDirection.DENIED,
            decision=AccessDecision.DENIED, confidence=.9, source="synthetic", occurred_at=NOW)
        session.add(event)
        await session.flush()
        await sessions.remember_session_in_db(session, event, (),
            first_seen=window.summary.first_seen, updated_at=window.summary.updated_at,
            read=window.best_read, evidence=window.summary.evidence)
        saga = MovementSagaRecord(idempotency_key=f"synthetic-session-{event.id}", source="synthetic",
            occurred_at=NOW, access_event_id=event.id)
        session.add(saga)
        await session.flush()
        await terminalize_window_in_session(session, claim, event_id=event.id, saga_id=saga.id)
        await session.commit()
        event_id = event.id
    assert await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0) is None
    read_claim, read = await claim_read(pipeline, coordinator,
        value("ZZ99ZZZ", seconds=81, eventId="full-evidence-79"))
    suppression = await sessions.suppression_for_read(read)
    assert suppression is not None and suppression.matched_by == "movement_session_protect_event_id"
    session_id = suppression.session.id
    async with AsyncSessionLocal() as session:
        row = await session.get(MovementSessionRecord, session_id)
        assert row.protect_event_ids == [] and row.ocr_variants == []
        assert await session.scalar(select(func.count()).select_from(MovementSessionObservation)
            .where(MovementSessionObservation.session_id == session_id)) == 80
        await sessions.annotate_suppressed_read_in_session(session, read, suppression, ingest_id=read_claim.ingest_id)
        await pipeline.suppress_read_in_session(session, read_claim, reason=suppression.reason)
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.get(MovementSessionObservation, read_claim.ingest_id) is None
        assert (await session.get(LprIngestEvent, read_claim.ingest_id)).status == "processing"
        assert (await session.get(MovementSessionRecord, session_id)).suppressed_read_count == 0
        await sessions.annotate_suppressed_read_in_session(session, read, suppression, ingest_id=read_claim.ingest_id)
        await sessions.annotate_suppressed_read_in_session(session, read, suppression, ingest_id=read_claim.ingest_id)
        await pipeline.suppress_read_in_session(session, read_claim, reason=suppression.reason)
        await session.commit()
    async with AsyncSessionLocal() as session:
        row = await session.get(MovementSessionRecord, session_id)
        assert row.suppressed_read_count == 1 and row.protect_event_ids == [] and row.ocr_variants == []
        association = await session.get(MovementSessionObservation, read_claim.ingest_id)
        assert association.session_id == session_id and association.ordinal == 80 and association.role == "suppressed"
        assert (await session.get(LprIngestEvent, read_claim.ingest_id)).status == "skipped"
        assert await observation_tokens.matching_session_tokens_in_session(session, [session_id],
            kind="ocr", values=("ZZ99ZZZ",)) == {session_id: {"ZZ99ZZZ"}}
        assert (await session.get(AccessEvent, event_id)).resource_revision == 0


async def test_suppression_locked_reload_preserves_another_transaction_after_stale_preload():
    from app.models import MovementSessionObservation, MovementSessionRecord
    from app.services.movement.sessions import MovementSessionService, VehicleSessionSuppression
    first_read, second_read = value(seconds=1, eventId="suppression-first"), value(seconds=2, eventId="suppression-second")
    first_ingest, second_ingest = await ready(first_read), await ready(second_read)
    sessions = MovementSessionService()
    async with AsyncSessionLocal() as session:
        event = AccessEvent(registration_number="AB12CDE", direction=AccessDirection.DENIED,
            decision=AccessDecision.DENIED, confidence=.9, source="synthetic", occurred_at=NOW,
            raw_payload={"vehicle_session": {"suppressed_read_count": 0, "suppressed_reads": []}})
        session.add(event)
        await session.flush()
        await sessions.remember_session_in_db(session, event, (), first_seen=NOW, updated_at=NOW,
            read=value())
        row = await session.scalar(select(MovementSessionRecord).where(MovementSessionRecord.access_event_id == event.id))
        await session.commit()
        event_id, session_id = event.id, row.id
        suppression = VehicleSessionSuppression(row, "vehicle_session_already_active", "protect_event_id")
    async with AsyncSessionLocal() as stale, AsyncSessionLocal() as concurrent:
        stale_event = await stale.get(AccessEvent, event_id)
        stale_session = await stale.get(MovementSessionRecord, session_id)
        assert stale_session.suppressed_read_count == 0
        await sessions.annotate_suppressed_read_in_session(concurrent, first_read, suppression, ingest_id=first_ingest)
        await concurrent.commit()
        assert stale_session.suppressed_read_count == 0 and stale_event.raw_payload["vehicle_session"]["suppressed_read_count"] == 0
        await sessions.annotate_suppressed_read_in_session(stale, second_read, suppression, ingest_id=second_ingest)
        await stale.commit()
    async with AsyncSessionLocal() as session:
        row = await session.get(MovementSessionRecord, session_id)
        event = await session.get(AccessEvent, event_id)
        assert row.suppressed_read_count == 2
        assert event.raw_payload["vehicle_session"]["suppressed_read_count"] == 2
        assert [item["captured_at"] for item in row.suppressed_reads] == [first_read.captured_at.isoformat(), second_read.captured_at.isoformat()]
        assert [item["captured_at"] for item in event.raw_payload["vehicle_session"]["suppressed_reads"]] == [first_read.captured_at.isoformat(), second_read.captured_at.isoformat()]
        assert list(await session.scalars(select(MovementSessionObservation.ordinal)
            .where(MovementSessionObservation.session_id == session_id).order_by(MovementSessionObservation.ordinal))) == [0, 1]


async def test_finalizing_restart_charges_original_budget_and_holds_every_member():
    from app.services.access.pipeline_store import finalization_failed_in_session
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    _, _, summary = await append(pipeline, windows, coordinator, value(), flush=True)
    second, _, _ = await append(pipeline, windows, coordinator, value(seconds=1), flush=True)
    claim = await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0)
    assert claim is not None
    async with AsyncSessionLocal() as session:
        window = await session.get(LprDebounceWindow, claim.window_id)
        chosen_id = window.best_ingest_id
        chosen = await session.get(LprIngestEvent, chosen_id)
        assert chosen.attempt_count == 1
        assert await finalization_failed_in_session(session, claim, detail='handled first failure', max_attempts=3)
        assert chosen.attempt_count == 1
        await session.commit()
    original_origin = summary.min_received_at
    for expected in (2, 3):
        # Simulate a lost process/expired operation without a failure checkpoint.
        async with AsyncSessionLocal() as session:
            state = await session.get(LprCoordinatorState, 1, with_for_update=True)
            state.lease_until = await database_now(session) - timedelta(seconds=1)
            window = await session.get(LprDebounceWindow, claim.window_id, with_for_update=True)
            window.lease_until = state.lease_until
            await session.commit()
        coordinator = await pipeline.acquire_coordinator()
        claim = await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0)
        assert claim is not None
        async with AsyncSessionLocal() as session:
            assert (await session.get(LprIngestEvent, chosen_id)).attempt_count == expected
            assert (await session.get(LprDebounceWindow, claim.window_id)).min_received_at == original_origin
    resumed_id = claim.window_id
    async with AsyncSessionLocal() as session:
        state = await session.get(LprCoordinatorState, 1, with_for_update=True)
        state.lease_until = await database_now(session) - timedelta(seconds=1)
        await session.commit()
    coordinator = await pipeline.acquire_coordinator()
    assert await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0) is None
    async with AsyncSessionLocal() as session:
        held = await session.get(LprDebounceWindow, resumed_id)
        assert held.state == 'held' and held.member_count == 2
        members = list(await session.scalars(select(LprIngestEvent).where(LprIngestEvent.window_id == resumed_id)))
        assert len(members) == 2 and second.ingest_id in {row.id for row in members}
        assert all(row.status == 'held' and row.terminal_reason == 'processing_attempts_exhausted' for row in members)
        assert (await session.get(LprIngestEvent, chosen_id)).attempt_count == 3
        assert await session.scalar(select(func.count()).select_from(AccessEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(LprWindowMember).where(
            LprWindowMember.window_id == resumed_id)) == 2
    await pipeline.release_coordinator(coordinator)


async def test_stop_retains_open_window_without_early_finalization():
    from app.services.access_events import AccessEventService
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    _, _, summary = await append(pipeline, windows, coordinator, value(), flush=True)
    service = AccessEventService()
    await service.start()
    await asyncio.sleep(0)
    await service.stop()
    async with AsyncSessionLocal() as session:
        stored = await session.get(LprDebounceWindow, summary.evidence.window_id)
        assert stored.state == 'open' and stored.member_count == 1
        assert await session.scalar(select(func.count()).select_from(AccessEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(LprWindowMember)) == 1
    await pipeline.release_coordinator(coordinator)
    restarted = await pipeline.acquire_coordinator()
    assert await pipeline.next_flush_window(restarted, quiet_seconds=0, max_seconds=0) is not None
    await pipeline.release_coordinator(restarted)


async def test_actual_coordinator_connection_close_is_owned_through_repeated_cancellation(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    pipeline = AccessPipelineStore()
    coordinator = await pipeline.acquire_coordinator()
    closing, release = asyncio.Event(), asyncio.Event()
    original_close = AsyncSession.close
    closes = []

    async def blocked_close(session):
        # This query proves close still owns the real transaction/connection
        # while cleanup is blocked, rather than only a mocked session wrapper.
        closes.append(await session.scalar(text('SELECT pg_backend_pid()')))
        closing.set()
        await release.wait()
        await original_close(session)

    monkeypatch.setattr(AsyncSession, 'close', blocked_close)
    operation = asyncio.create_task(pipeline.boundary(coordinator), name='fixture-coordinator-boundary')
    try:
        await asyncio.wait_for(closing.wait(), 5)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done() and len(closes) == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert len(closes) == 1 and engine.pool.checkedout() == 0
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
        monkeypatch.setattr(AsyncSession, 'close', original_close)
    await pipeline.release_coordinator(coordinator)


async def test_authenticated_complete_candidate_api_pages_beyond_preview_and_rejects_other_window(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from app.api.dependencies import current_user
    from app.api.v1 import events
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    for index in range(80):
        _, _, summary = await append(pipeline, windows, coordinator, value(seconds=index,
            vendor_secret='must never be public', webhook_trace={'request_id': 'safe', 'authorization': 'secret'}))
    claim = await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0)
    assert claim is not None
    await commit_window(windows, claim)
    async with AsyncSessionLocal() as session:
        event_id = (await session.get(LprDebounceWindow, claim.window_id)).access_event_id
        loaded = await windows.load_in_session(session, claim.window_id)
        assert len(loaded.preview) == 50 and loaded.summary.count == 80
    assert await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0) is None
    _, _, _ = await append(pipeline, windows, coordinator, value(plate='OTHER123'))
    other_claim = await pipeline.next_flush_window(coordinator, quiet_seconds=0, max_seconds=0)
    await commit_window(windows, other_claim)
    async with AsyncSessionLocal() as session:
        other_id = (await session.get(LprDebounceWindow, other_claim.window_id)).access_event_id
    app = FastAPI()
    app.include_router(events.router, prefix='/api/v1')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        assert (await client.get(f'/api/v1/events/{event_id}/candidates')).status_code == 401
        app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
        first = await client.get(f'/api/v1/events/{event_id}/candidates')
        assert first.status_code == 200
        page = first.json()
        assert page['total'] == 80 and len(page['items']) == 50 and page['next_cursor']
        assert 'vendor_secret' not in first.text and 'authorization' not in first.text and 'secret' not in first.text
        last = await client.get(f'/api/v1/events/{event_id}/candidates', params={'cursor': page['next_cursor']})
        assert last.status_code == 200 and len(last.json()['items']) == 30 and last.json()['next_cursor'] is None
        identities = [item['ingest_id'] for item in page['items'] + last.json()['items']]
        assert len(set(identities)) == 80
        assert (await client.get(f'/api/v1/events/{other_id}/candidates', params={'cursor': page['next_cursor']})).status_code == 422
        assert (await client.get(f'/api/v1/events/{event_id}/candidates', params={'cursor': 'not-a-cursor'})).status_code == 422
        assert (await client.get(f'/api/v1/events/{event_id}/candidates', params={'limit': 51})).status_code == 422
    await pipeline.release_coordinator(coordinator)


async def test_health_reports_durable_queue_window_counts_and_original_oldest_age(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.api.v1 import health
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    _, _, summary = await append(pipeline, windows, coordinator, value(), flush=True)
    pending = await ready(value(plate='WAIT123'), received_at=NOW + timedelta(seconds=10))
    service = SimpleNamespace(status=lambda: {'status': 'ok', 'worker_running': True}, backlog_status=pipeline.backlog_status)
    monkeypatch.setattr(health, 'get_access_event_service', lambda: service)
    monkeypatch.setattr(health, '_realtime_check', lambda: {'status': 'ok', 'started': True})
    monkeypatch.setattr(health, '_maintenance_check', AsyncMock(return_value={'status': 'ok'}))
    monkeypatch.setattr(health, '_home_assistant_check', AsyncMock(return_value={'status': 'disabled'}))
    app = FastAPI()
    app.state.startup_complete = True
    app.include_router(health.router, prefix='/api/v1')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        response = await client.get('/api/v1/health')
        assert response.status_code == 200
        backlog = response.json()['checks']['access_events']
        assert backlog['queue_depth'] == 1 and backlog['pending_windows'] == 1
        assert datetime.fromisoformat(backlog['oldest_pending_at']) == summary.min_received_at
        assert backlog['oldest_pending_age_seconds'] > 0
        service.backlog_status = AsyncMock(side_effect=AssertionError('Readiness must not read backlog'))
        ready_response = await client.get('/api/v1/health/ready')
        assert ready_response.status_code == 200 and ready_response.json() == {'ready': True}
    async with AsyncSessionLocal() as session:
        assert (await session.get(LprIngestEvent, pending)).received_at == NOW + timedelta(seconds=10)
    await pipeline.release_coordinator(coordinator)


@pytest.mark.parametrize('count', [4, 80])
async def test_distinct_pending_windows_keep_vendor_backlog_out_of_retained_work(tmp_path, count):
    pipeline, windows = AccessPipelineStore(), AccessWindowStore()
    coordinator = await pipeline.acquire_coordinator()
    summaries = []
    for index in range(count):
        _, _, summary = await append(pipeline, windows, coordinator,
            value(plate=f'P{index:06d}', irrelevant_vendor_blob='v' * 131072))
        summaries.append(summary)
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    async with AsyncSessionLocal() as session:
        legacy_rows = (await session.execute(select(LprDebounceWindow.id,
            LprIngestEvent, LprWindowMember.prepared_context)
            .join(LprWindowMember, LprWindowMember.window_id == LprDebounceWindow.id)
            .join(LprIngestEvent, LprIngestEvent.id == LprWindowMember.ingest_id)
            .where(LprDebounceWindow.state == 'open').order_by(LprDebounceWindow.open_ordinal))).all()
        legacy = [(identity, read_from_member(ingest, context)) for identity, ingest, context in legacy_rows]
    before_seconds = time.perf_counter() - started
    _, before_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    expected_window = next(identity for identity, read in legacy if read.registration_number == f'P{count-1:06d}')
    before_payload_bytes = sum(len(json.dumps(read.raw_payload).encode()) for _, read in legacy)
    del legacy_rows, legacy
    gc.collect()
    # The new owner holds one current input, while ordered matching pages only
    # compact durable window summaries. Startup/backlog detail hydrates no input.
    claim, read = await claim_read(pipeline, coordinator, value(plate=f'P{count-1:06d}', seconds=1, confidence=.1))
    captured = []
    def capture(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith('SELECT'):
            captured.append((statement, parameters))
    sql_event.listen(engine.sync_engine, 'before_cursor_execute', capture)
    tracemalloc.start()
    started = time.perf_counter()
    try:
        backlog = await pipeline.backlog_status()
        async with AsyncSessionLocal() as session:
            result = await windows.append_in_session(session, claim, read, threshold=1)
            await pipeline.begin_flush_in_session(session, claim)
            await session.commit()
    finally:
        after_seconds = time.perf_counter() - started
        _, after_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        sql_event.remove(engine.sync_engine, 'before_cursor_execute', capture)
    assert backlog['pending_windows'] == count and backlog['queue_depth'] == 1
    assert result.evidence.window_id == expected_window == summaries[-1].evidence.window_id
    assert result.count == 2 and result.best_ingest_id == summaries[-1].best_ingest_id
    pages = [(sql, parameters) for sql, parameters in captured
             if 'FROM lpr_debounce_windows' in sql and 'LIMIT' in sql]
    assert len(pages) == (count + 49) // 50
    assert all('normalized_payload' not in sql for sql, _ in pages)
    if count == 80:
        assert after_peak < before_peak / 3
    plans = []
    async with AsyncSessionLocal() as session:
        connection = await session.connection()
        for statement, parameters in pages:
            plan = (await connection.exec_driver_sql('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) ' + statement, parameters)).scalar_one()
            plans.append({'sql': statement, 'plan': plan})
    write_evidence(tmp_path / 'distinct-window-retention-evidence.json', {
        'kind': 'synthetic-load-phase-comparison', 'distinct_pending_windows': count,
        'before': {'retained_vendor_rows': count, 'raw_payload_json_bytes': before_payload_bytes,
                   'load_peak_tracemalloc_bytes': before_peak, 'load_seconds': before_seconds},
        'after': {'current_input_rows': 1, 'window_page_limit': 50, 'window_page_queries': len(pages),
                  'load_peak_tracemalloc_bytes': after_peak, 'load_seconds': after_seconds},
        'equivalent_first_matching_window_and_rank': True, 'plans': plans,
        'limits': 'Equivalent synthetic pending state and matching result. Python allocation peaks cover load/append only, not input preparation, PostgreSQL memory or process RSS. Timings are local observations, not production guarantees.',
    })
    await pipeline.release_coordinator(coordinator)
