"""Publication transaction/claim/revision contracts on migrated disposable PostgreSQL."""
import asyncio
import json
import os
import socket
from urllib.parse import urlsplit
import httpx
import uuid
from datetime import UTC,datetime,timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event as sql_event,select,text
from test_recovery_boundaries import DATABASE_NAME

from app.db.session import AsyncSessionLocal,engine
from app.models import AccessEvent,AccessCompletionWork
from app.models.enums import AccessDecision,AccessDirection
from app.services.access import publication
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.publication import AccessPublicationStore,mark_public_event_changed_in_session,reserve_initial_publication_in_session

pytestmark=pytest.mark.asyncio
NOW=datetime(2026,10,7,12,tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def publication_resources(monkeypatch):
    # The shared recovery import already validates loopback-only Linux, explicit
    # synthetic credentials/database, no runtime source paths or Docker socket.
    # This owner additionally needs the runner's disposable Redis, never a vendor.
    redis_url=urlsplit(os.environ['IACS_REDIS_URL'])
    assert redis_url.scheme=='redis' and redis_url.hostname=='127.0.0.1' and redis_url.port==6379
    assert redis_url.path.removeprefix('/').isdigit() and not redis_url.query and not redis_url.fragment
    assert redis_url.username is None and redis_url.password is None
    original_connect,original_connect_ex=socket.socket.connect,socket.socket.connect_ex
    def check(address):
        assert isinstance(address,tuple) and address[:2] in {('127.0.0.1',5432),('127.0.0.1',6379)}, 'Publication attempted non-isolated socket'
    def connect(sock,address):check(address);return original_connect(sock,address)
    def connect_ex(sock,address):check(address);return original_connect_ex(sock,address)
    async def no_http(*args,**kwargs):raise AssertionError('Publication attempted HTTP/provider I/O')
    monkeypatch.setattr(socket.socket,'connect',connect)
    monkeypatch.setattr(socket.socket,'connect_ex',connect_ex)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',no_http)
    try:
        async with AsyncSessionLocal() as session:
            assert await session.scalar(text('SELECT current_database()'))==DATABASE_NAME
            assert await session.scalar(text('SELECT count(*) FROM system_settings'))==0
            await session.execute(text("TRUNCATE access_completion_work,access_events CASCADE"))
            await session.commit()
        yield
    finally:await engine.dispose()


def event_value(**kwargs):
    return AccessEvent(id=uuid.uuid4(),registration_number="AB12CDE",direction=AccessDirection.ENTRY,
        decision=AccessDecision.GRANTED,confidence=.9,source="synthetic",occurred_at=NOW,**kwargs)


async def reserve(**kwargs):
    async with AsyncSessionLocal() as session:
        event=event_value(**kwargs)
        session.add(event)
        await session.flush()
        assert await reserve_initial_publication_in_session(session,event.id)==1
        await session.commit()
        return event.id


async def work_value(identity):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id==identity))


async def test_event_initial_reservation_and_public_mutation_share_caller_commit_and_rollback():
    identity=uuid.uuid4()
    async with AsyncSessionLocal() as session:
        event=event_value();identity=event.id
        session.add(event);await session.flush()
        await reserve_initial_publication_in_session(session,identity)
        async with AsyncSessionLocal() as observer:
            assert await observer.get(AccessEvent,identity) is None
            assert await observer.scalar(select(AccessCompletionWork.id)) is None
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.get(AccessEvent,identity) is None
    identity=await reserve()
    async with AsyncSessionLocal() as session:
        event=await session.get(AccessEvent,identity,with_for_update=True)
        event.confidence=.1
        assert await mark_public_event_changed_in_session(session,identity)==2
        await session.rollback()
    async with AsyncSessionLocal() as session:
        event=await session.get(AccessEvent,identity)
        assert event.resource_revision==1 and event.confidence==.9
        work=await work_value(identity)
        assert work.expected_revision==1 and work.result=={'published_revision':0}


async def test_initial_reservation_is_idempotent_and_does_not_requeue_acknowledged_revision():
    identity=await reserve()
    store=AccessPublicationStore();claim=await store.claim();message=await store.load_message(claim)
    assert message.revision==1 and message.event_type=='access_event.finalized'
    assert not await store.acknowledge(claim,message.revision)
    async with AsyncSessionLocal() as session:
        assert await reserve_initial_publication_in_session(session,identity)==1
        await session.commit()
    assert await store.claim() is None
    work=await work_value(identity)
    assert work.state=='succeeded' and work.result=={'published_revision':1}


async def test_new_revision_during_delivery_leaves_exactly_one_trailing_current_projection():
    identity=await reserve()
    store=AccessPublicationStore();claim=await store.claim();first=await store.load_message(claim)
    async with AsyncSessionLocal() as session:
        event=await session.get(AccessEvent,identity,with_for_update=True)
        event.confidence=.2
        assert await mark_public_event_changed_in_session(session,identity)==2
        assert await mark_public_event_changed_in_session(session,identity)==3
        await session.commit()
    assert await store.claim() is None
    assert await store.acknowledge(claim,first.revision)
    trailing=await store.claim();message=await store.load_message(trailing)
    assert message.revision==3 and message.payload['confidence']==.2 and message.event_type=='access_event.updated'
    assert not await store.acknowledge(trailing,message.revision)
    assert await store.claim() is None
    work=await work_value(identity)
    assert work.attempt_count==2 and work.result=={'published_revision':3}


async def test_acknowledgement_requires_exact_loaded_revision_not_only_current_requested_revision():
    identity=await reserve();store=AccessPublicationStore();claim=await store.claim()
    with pytest.raises(PipelineLeaseLost,match='publication_revision_changed'):
        await store.acknowledge(claim,1)
    first=await store.load_message(claim)
    async with AsyncSessionLocal() as session:
        await mark_public_event_changed_in_session(session,identity);await session.commit()
    with pytest.raises(PipelineLeaseLost,match='publication_revision_changed'):
        await store.acknowledge(claim,2)
    assert await store.acknowledge(claim,first.revision)


async def test_compact_projection_uses_current_columns_and_named_json_never_full_vendor_payload():
    saga_id = uuid.uuid4()
    identity=await reserve(snapshot_path='synthetic/committed.jpg',snapshot_bytes=123,snapshot_width=640,snapshot_height=480,
        snapshot_camera='camera',snapshot_captured_at=NOW,raw_payload={'vendor_blob':'x'*900000,
        'visitor_pass':{'id':str(uuid.uuid4()),'visitor_name':'Guest','mode':'arrival','unused':'y'*90000},
        'movement_saga':{'id':str(saga_id),'state':'failed','presence_committed':True,'admission_status':'verified',
            'admission_evidence':{'provider_raw':'z'*90000},'gate':{'command_id':str(uuid.uuid4()),'accepted':True,'state':'open',
                'mechanically_confirmed':True,'unused':'q'*90000}}})
    from app.models import MovementSagaRecord
    from app.models.enums import MovementSagaState
    async with AsyncSessionLocal() as session:
        session.add(MovementSagaRecord(id=saga_id, idempotency_key=f"synthetic-{saga_id}",
            source="synthetic", occurred_at=NOW, access_event_id=identity,
            state=MovementSagaState.COMPLETED, presence_committed=True))
        await session.commit()
    store=AccessPublicationStore();claim=await store.claim();captured=[]
    def capture(connection,cursor,statement,parameters,context,many):
        if 'FROM access_events' in statement:captured.append(statement)
    sql_event.listen(engine.sync_engine,'before_cursor_execute',capture)
    try:message=await store.load_message(claim)
    finally:sql_event.remove(engine.sync_engine,'before_cursor_execute',capture)
    assert len(captured)==1 and 'access_events.raw_payload,' not in captured[0]
    body=json.dumps(message.payload)
    assert len(body)<4000 and 'vendor_blob' not in body and 'admission_evidence' not in body and 'unused' not in body
    assert message.payload['visitor_name']=='Guest' and message.payload['snapshot_bytes']==123
    assert message.payload['snapshot_url'].endswith(f'{identity}/snapshot')
    assert message.payload['movement_saga']['state'] == 'completed'
    assert message.payload['movement_saga']['gate']['mechanically_confirmed'] is True


async def test_claim_scans_identifiers_but_leases_only_one_free_publication_slot():
    async with AsyncSessionLocal() as session:
        for _ in range(40):
            event=event_value();session.add(event);await session.flush()
            await reserve_initial_publication_in_session(session,event.id)
        await session.commit()
    captured=[]
    def capture(connection,cursor,statement,parameters,context,many):
        if 'FROM access_completion_work' in statement:captured.append(statement)
    sql_event.listen(engine.sync_engine,'before_cursor_execute',capture)
    try:claim=await AccessPublicationStore().claim()
    finally:sql_event.remove(engine.sync_engine,'before_cursor_execute',capture)
    assert claim is not None and 'LIMIT' in captured[0] and captured[0].startswith('SELECT access_completion_work.id')
    async with AsyncSessionLocal() as session:
        leased=list(await session.scalars(select(AccessCompletionWork.id).where(AccessCompletionWork.state=='leased')))
        assert leased==[claim.work_id]


async def test_expired_claim_takeover_fences_old_message_ack_and_retry():
    identity=await reserve();store=AccessPublicationStore();old=await store.claim();message=await store.load_message(old)
    async with AsyncSessionLocal() as session:
        work=await session.get(AccessCompletionWork,old.work_id,with_for_update=True)
        work.lease_until=await publication.database_now(session)-timedelta(seconds=1);await session.commit()
    current=await AccessPublicationStore().claim()
    assert current.event_id==identity and current.token!=old.token
    for operation in (lambda:store.load_message(old),lambda:store.acknowledge(old,message.revision),lambda:store.retry(old)):
        with pytest.raises(PipelineLeaseLost):await operation()
    current_message=await store.load_message(current)
    assert not await store.acknowledge(current,current_message.revision)


@pytest.mark.parametrize('operation',['load','ack','renew','retry'])
async def test_blocked_work_lock_past_expiry_uses_fresh_database_clock(operation):
    await reserve();store=AccessPublicationStore();claim=await store.claim();message=await store.load_message(claim)
    async with AsyncSessionLocal() as session:
        work=await session.get(AccessCompletionWork,claim.work_id,with_for_update=True)
        work.lease_until=await publication.database_now(session)+timedelta(milliseconds=300);await session.commit()
    async with AsyncSessionLocal() as blocker:
        await blocker.get(AccessCompletionWork,claim.work_id,with_for_update=True)
        methods={'load':lambda:store.load_message(claim),'ack':lambda:store.acknowledge(claim,message.revision),
            'renew':lambda:store.renew(claim),'retry':lambda:store.retry(claim)}
        task=asyncio.create_task(methods[operation]())
        await asyncio.sleep(.4);assert not task.done()
        await blocker.rollback()
    with pytest.raises(PipelineLeaseLost,match='publication_lease_lost'):await task
    work=await work_value(claim.event_id)
    assert work.state=='leased' and work.result['published_revision']==0


async def test_ack_final_clock_has_clean_orm_and_cannot_commit_after_original_expiry(monkeypatch):
    await reserve();store=AccessPublicationStore();claim=await store.claim();message=await store.load_message(claim)
    real_clock=publication.database_now;seen=[]
    async def checked_clock(session):
        now=await real_clock(session)
        work=session.identity_map.get((AccessCompletionWork,(claim.work_id,),None))
        if work is not None and work.state=='succeeded':
            assert not session.dirty and work.lease_token is None and work.lease_until is None
            seen.append(True);return now+timedelta(seconds=200)
        return now
    monkeypatch.setattr(publication,'database_now',checked_clock)
    with pytest.raises(PipelineLeaseLost):await store.acknowledge(claim,message.revision)
    assert seen
    work=await work_value(claim.event_id)
    assert work.state=='leased' and work.result['published_revision']==0 and work.lease_token==claim.token


async def test_parallel_public_mutations_allocate_serial_revisions_without_lost_dirty_mark():
    identity=await reserve()
    async def change():
        async with AsyncSessionLocal() as session:
            revision=await mark_public_event_changed_in_session(session,identity)
            await session.commit();return revision
    assert sorted(await asyncio.gather(change(),change()))==[2,3]
    work=await work_value(identity)
    assert work.expected_revision==3 and work.result=={'published_revision':0}


async def test_cancelled_blocked_ack_has_no_result_write_and_current_claim_remains_recoverable():
    await reserve();store=AccessPublicationStore();claim=await store.claim();message=await store.load_message(claim)
    async with AsyncSessionLocal() as blocker:
        await blocker.get(AccessCompletionWork,claim.work_id,with_for_update=True)
        task=asyncio.create_task(store.acknowledge(claim,message.revision))
        await asyncio.sleep(.05);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        await blocker.rollback()
    work=await work_value(claim.event_id)
    assert work.result['published_revision']==0 and work.state=='leased'
    assert not await store.acknowledge(claim,message.revision)


async def test_real_redis_acceptance_and_restart_publication_use_current_committed_revision():
    from app.core.config import settings
    from app.services.access.publication_worker import AccessPublicationLane
    from app.services.event_bus import EventBus
    import redis.asyncio as redis_asyncio
    key='synthetic:publication:'+str(uuid.uuid4())
    bus=EventBus(redis_url=settings.redis_url,stream_key=key)
    client=redis_asyncio.from_url(settings.redis_url,decode_responses=True)
    identity=await reserve();store=AccessPublicationStore();lane=AccessPublicationLane(store,bus)
    try:
        await bus.start();await lane.deliver(await store.claim())
        assert (await work_value(identity)).result=={'published_revision':1}
        async with AsyncSessionLocal() as session:
            event=await session.get(AccessEvent,identity,with_for_update=True);event.confidence=.4
            await mark_public_event_changed_in_session(session,identity);await session.commit()
        # A fresh owner recovers the durable checkpoint without an in-memory wake.
        restarted=AccessPublicationLane(AccessPublicationStore(),bus)
        await restarted.deliver(await restarted.store.claim())
        records=await client.xrange(key)
        assert len(records)==2
        messages=[json.loads(fields['event']) for _,fields in records]
        assert [(item['type'],item['payload']['resource_revision']) for item in messages]==[
            ('access_event.finalized',1),('access_event.updated',2)]
        assert messages[-1]['payload']['confidence']==.4
    finally:
        await lane.stop();await bus.stop();await client.aclose()


async def test_guard_injected_configured_redis_failure_retries_without_ack_or_origin_refresh():
    from app.services.access.publication_worker import AccessPublicationLane
    from app.services.event_bus import EventBus
    identity=await reserve();store=AccessPublicationStore()
    bus=EventBus(redis_url='redis://127.0.0.1:1/0')
    lane=AccessPublicationLane(store,bus);seen=[];effects=set()
    async def observe(event):
        seen.append(event.payload['resource_revision'])
        effects.add((event.payload['event_id'],event.payload['resource_revision']))
    bus.subscribe(observe)
    original=await work_value(identity)
    try:
        await bus.start()
        for _ in range(2):
            await lane.deliver(await store.claim())
            async with AsyncSessionLocal() as session:
                work=await session.get(AccessCompletionWork,original.id,with_for_update=True)
                work.due_at=await publication.database_now(session);await session.commit()
        await asyncio.sleep(.01)
        current=await work_value(identity)
        assert current.state=='queued' and current.result=={'published_revision':0}
        assert current.origin==original.origin and current.origin_at==original.origin_at and current.deadline==original.deadline
        assert current.created_at==original.created_at and current.attempt_count==2
        assert seen==[1,1] and effects=={(str(identity),1)}
    finally:await lane.stop();await bus.stop()


async def test_publication_fixture_rejects_every_nonisolated_destination_and_http():
    for address in [('127.0.0.1',8088),('127.0.0.1',1),('192.0.2.1',6379),('localhost',6379)]:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as connection:
            with pytest.raises(AssertionError,match='non-isolated socket'):connection.connect(address)
            with pytest.raises(AssertionError,match='non-isolated socket'):connection.connect_ex(address)
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError,match='HTTP/provider I/O'):
            await client.get('http://127.0.0.1:8088/never-send')


async def test_real_redis_xadd_rejection_and_repair_retain_durable_current_revision():
    from app.core.config import settings
    from app.services.access.publication_worker import AccessPublicationLane
    from app.services.event_bus import EventBus
    import redis.asyncio as redis_asyncio
    key='synthetic:publication-rejection:'+str(uuid.uuid4())
    bus=EventBus(redis_url=settings.redis_url,stream_key=key)
    client=redis_asyncio.from_url(settings.redis_url,decode_responses=True)
    identity=await reserve();store=AccessPublicationStore();lane=AccessPublicationLane(store,bus)
    original=await work_value(identity)
    try:
        await client.set(key,'synthetic wrong stream type')
        await bus.start();await lane.deliver(await store.claim())
        failed=await work_value(identity)
        assert failed.state=='queued' and failed.result=={'published_revision':0}
        assert failed.origin_at==original.origin_at and failed.deadline==original.deadline
        assert failed.created_at==original.created_at
        await client.delete(key)
        async with AsyncSessionLocal() as session:
            work=await session.get(AccessCompletionWork,original.id,with_for_update=True)
            work.due_at=await publication.database_now(session);await session.commit()
        await lane.deliver(await store.claim())
        assert (await work_value(identity)).result=={'published_revision':1}
        records=await client.xrange(key)
        assert len(records)==1
        payload=json.loads(records[0][1]['event'])['payload']
        assert payload['event_id']==str(identity) and payload['resource_revision']==1
    finally:await lane.stop();await bus.stop();await client.aclose()


async def test_actual_event_gets_and_publication_share_journal_snapshot_and_persisted_snapshot_metadata(monkeypatch):
    from fastapi import FastAPI
    from app.api.dependencies import current_user
    from app.api.v1 import events
    from app.models import MovementSagaRecord
    from app.models.enums import MovementSagaState
    from types import SimpleNamespace
    identity = await reserve(snapshot_path="synthetic/missing-file.jpg", snapshot_bytes=123,
        raw_payload={"movement_saga": {"state": "completed", "presence_committed": True}})
    async with AsyncSessionLocal() as session:
        saga = MovementSagaRecord(idempotency_key=f"synthetic-{identity}", source="synthetic",
            occurred_at=NOW, access_event_id=identity, state=MovementSagaState.FAILED,
            presence_committed=False, failure_detail="Current journal failure")
        session.add(saga)
        await session.commit()
    async def config(): return SimpleNamespace(site_timezone="Europe/London")
    monkeypatch.setattr(events, "get_runtime_config", config)
    app = FastAPI()
    app.include_router(events.router, prefix="/api/v1")
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        detail = await client.get(f"/api/v1/events/{identity}")
        listed = await client.get("/api/v1/events?limit=40")
        history = await client.get("/api/v1/events/history?limit=50")
        missing = await client.get(f"/api/v1/events/{identity}/snapshot")
    assert detail.status_code == listed.status_code == history.status_code == 200
    payload = detail.json()
    assert payload == listed.json()[0] == history.json()["items"][0]
    assert payload["resource_revision"] == 1
    assert payload["movement_saga"]["state"] == "failed"
    assert payload["movement_saga"]["failure_detail"] == "Current journal failure"
    assert payload["movement_saga"]["presence_committed"] is False
    assert set(payload["movement_saga"]) == {"id", "state", "reconciliation_required",
        "gate_command_required", "presence_committed", "failure_detail", "updated_at"}
    assert payload["snapshot_url"] and payload["snapshot_bytes"] == 123
    assert missing.status_code == 404
    store = AccessPublicationStore()
    claim = await store.claim()
    message = await store.load_message(claim)
    projected = {key: message.payload[key] for key in payload if key != "id"}
    assert projected == {key: value for key, value in payload.items() if key != "id"}


async def test_conflicting_legacy_sagas_are_held_without_raw_fallback_or_repair_winner():
    from fastapi import FastAPI
    from app.api.dependencies import admin_user, current_user
    from app.api.v1 import access, events
    from app.models import MovementSagaRecord
    from app.models.enums import MovementSagaState
    from types import SimpleNamespace
    identity = await reserve(raw_payload={"movement_saga": {"state": "completed", "gate": {"state": "open"}}})
    async with AsyncSessionLocal() as session:
        for _ in range(2):
            session.add(MovementSagaRecord(idempotency_key=f"synthetic-{uuid.uuid4()}",
                source="synthetic", occurred_at=NOW, access_event_id=identity,
                state=MovementSagaState.COMPLETED, presence_committed=True))
        await session.commit()
    app = FastAPI()
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(access.router, prefix="/api/v1/access")
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[admin_user] = lambda: SimpleNamespace(id=uuid.uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        detail = await client.get(f"/api/v1/events/{identity}")
        repair = await client.post(f"/api/v1/access/events/{identity}/movement-reconciliation", json={"reason": "review"})
    held = detail.json()["movement_saga"]
    assert held["id"] is None and held["state"] == "held" and held["reconciliation_required"] is True
    assert "Conflicting" in held["failure_detail"] and "gate" not in held
    assert repair.status_code == 409
    store = AccessPublicationStore()
    message = await store.load_message(await store.claim())
    assert message.payload["movement_saga"] == held


async def test_two_concurrent_missing_saga_repairs_use_nonblocking_authority_before_event_lock():
    from app.services.movement_ledger import MovementLedgerRepository
    from app.models import MovementSagaRecord
    from app.models.enums import MovementSagaState
    identity = await reserve()
    ledger = MovementLedgerRepository()
    async with AsyncSessionLocal() as first, AsyncSessionLocal() as second:
        event, saga = await ledger.event_repair_target_in_session(first, identity, detail="first")
        with pytest.raises(ValueError, match="busy"):
            await asyncio.wait_for(ledger.event_repair_target_in_session(second, identity, detail="second"), timeout=1)
        await second.rollback()
        await ledger.transition_movement_saga(first, saga, MovementSagaState.RECONCILIATION_REQUIRED,
            access_event_id=event.id, reconciliation_required=True)
        await first.commit()
        event2, saga2 = await ledger.event_repair_target_in_session(second, identity, detail="retry")
        assert saga2.id == saga.id and event2.resource_revision == 2
        assert len(list(await second.scalars(select(MovementSagaRecord.id).where(
            MovementSagaRecord.access_event_id == identity)))) == 1
        await second.rollback()


async def test_legacy_zero_saga_mutation_and_publication_checkpoint_roll_back_together():
    from app.services.movement_ledger import MovementLedgerRepository
    from app.models import MovementSagaRecord
    from app.models.enums import MovementSagaState
    async with AsyncSessionLocal() as session:
        event = event_value()
        session.add(event)
        await session.flush()
        saga = MovementSagaRecord(idempotency_key=f"synthetic-{event.id}", source="synthetic",
            occurred_at=NOW, access_event_id=event.id, state=MovementSagaState.COMPLETED)
        session.add(saga)
        await session.commit()
        identity, saga_id = event.id, saga.id
    async with AsyncSessionLocal() as session:
        saga = await session.get(MovementSagaRecord, saga_id, with_for_update=True)
        await MovementLedgerRepository().transition_movement_saga(session, saga,
            MovementSagaState.RECONCILIATION_REQUIRED, reconciliation_required=True)
        assert (await session.get(AccessEvent, identity)).resource_revision == 1
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert (await session.get(AccessEvent, identity)).resource_revision == 0
        assert (await session.get(MovementSagaRecord, saga_id)).state == MovementSagaState.COMPLETED
        assert await session.scalar(select(AccessCompletionWork.id)) is None


async def test_two_concurrent_actual_repair_requests_commit_one_parent_and_one_audit(monkeypatch):
    from fastapi import FastAPI
    from app.api.dependencies import admin_user
    from app.api.v1 import access
    from app.models import AuditLog, MovementSagaRecord, User
    from app.models.enums import UserRole
    identity = await reserve()
    actor = User(username=f"synthetic-repair-{uuid.uuid4()}", full_name="Repair Reviewer",
                 password_hash="unused", role=UserRole.ADMIN, is_active=True)
    async with AsyncSessionLocal() as session:
        session.add(actor)
        await session.commit()
    reached, release = asyncio.Event(), asyncio.Event()
    original = access.write_audit_log
    async def paused_audit(*args, **kwargs):
        reached.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(access, "write_audit_log", paused_audit)
    app = FastAPI()
    app.include_router(access.router, prefix="/api/v1/access")
    app.dependency_overrides[admin_user] = lambda: actor
    path = f"/api/v1/access/events/{identity}/movement-reconciliation"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        first = asyncio.create_task(client.post(path, json={"reason": "review first"}))
        try:
            await asyncio.wait_for(reached.wait(), timeout=3)
            second = await asyncio.wait_for(client.post(path, json={"reason": "review second"}), timeout=1)
            assert second.status_code == 409 and "busy" in second.json()["detail"]
        finally:
            release.set()
        result = await asyncio.wait_for(first, timeout=3)
        assert result.status_code == 200
    async with AsyncSessionLocal() as session:
        assert len(list(await session.scalars(select(MovementSagaRecord.id).where(
            MovementSagaRecord.access_event_id == identity)))) == 1
        assert len(list(await session.scalars(select(AuditLog.id).where(
            AuditLog.action == "access_event.movement_reconciliation_requested",
            AuditLog.target_id == str(identity))))) == 1
        event = await session.get(AccessEvent, identity)
        assert event.resource_revision == 2


@pytest.mark.parametrize("terminal_state", ["failed", "suppressed"])
@pytest.mark.parametrize("presence_committed", [False, True])
async def test_terminal_admission_presence_change_and_revision_share_commit_and_rollback(terminal_state, presence_committed):
    from app.models import MovementSagaRecord, Person, Presence
    from app.models.enums import MovementSagaState, PresenceState
    from app.services.movement.admission import finalize_in_session
    async with AsyncSessionLocal() as session:
        person = Person(display_name="Synthetic terminal admission")
        session.add(person)
        await session.flush()
        event = event_value(person_id=person.id)
        event.direction = AccessDirection.EXIT
        session.add(event)
        await session.flush()
        saga = MovementSagaRecord(idempotency_key=f"synthetic-terminal-{event.id}", source="synthetic",
            occurred_at=NOW, access_event_id=event.id, state=MovementSagaState(terminal_state),
            presence_committed=presence_committed, failure_detail="retained terminal evidence", updated_at=NOW)
        session.add(saga)
        if presence_committed:
            session.add(Presence(person_id=person.id, state=PresenceState.EXITED,
                                 last_event_id=event.id, last_changed_at=event.occurred_at))
        await reserve_initial_publication_in_session(session, event.id)
        await session.commit()
        event_id, saga_id, person_id = event.id, saga.id, person.id
    async with AsyncSessionLocal() as session:
        result = await finalize_in_session(session, saga_id=saga_id)
        assert result.presence_changed is not presence_committed
        assert result.saga.presence_committed
        await session.refresh(result.saga)
        assert result.saga.updated_at > NOW
        assert result.saga.state == MovementSagaState(terminal_state)
        assert result.saga.failure_detail == "retained terminal evidence"
        assert result.event.resource_revision == 2
        work = await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id == event_id))
        assert work.expected_revision == 2
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert (await session.get(AccessEvent, event_id)).resource_revision == 1
        assert (await session.get(MovementSagaRecord, saga_id)).presence_committed is presence_committed
        assert (await session.get(MovementSagaRecord, saga_id)).updated_at == NOW
        assert bool(await session.get(Presence, person_id)) is presence_committed
        work = await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id == event_id))
        assert work.expected_revision == 1
        await finalize_in_session(session, saga_id=saga_id)
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert (await session.get(AccessEvent, event_id)).resource_revision == 2
        assert (await session.get(MovementSagaRecord, saga_id)).state == MovementSagaState(terminal_state)
        assert (await session.get(MovementSagaRecord, saga_id)).presence_committed
        assert (await session.get(Presence, person_id)).last_event_id == event_id
        updated_at = (await session.get(MovementSagaRecord, saga_id)).updated_at
        await finalize_in_session(session, saga_id=saga_id)
        await session.commit()
        await session.refresh(await session.get(MovementSagaRecord, saga_id))
        assert (await session.get(MovementSagaRecord, saga_id)).updated_at == updated_at
        assert (await session.get(AccessEvent, event_id)).resource_revision == 2
        work = await session.scalar(select(AccessCompletionWork).where(AccessCompletionWork.event_id == event_id))
        assert work.expected_revision == 2


async def test_visitor_signal_retries_with_publication_and_later_media_revision_does_not_repeat():
    from app.models import MovementSagaRecord, VisitorPass
    from app.models.enums import MovementSagaState, VisitorPassStatus
    from app.services.access.publication_worker import AccessPublicationLane
    from app.services.realtime_contracts import RealtimePublishReceipt
    event_id, visitor_id, saga_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with AsyncSessionLocal() as session:
        event = event_value()
        event.id = event_id
        event.raw_payload = {'visitor_pass': {'id': str(visitor_id), 'visitor_name': 'Synthetic visitor', 'mode': 'arrival'},
                             'movement_saga': {'id': str(saga_id), 'admission_status': 'verified'}}
        session.add(event)
        await session.flush()
        session.add_all([
            VisitorPass(id=visitor_id, visitor_name='Synthetic visitor', expected_time=NOW,
                status=VisitorPassStatus.USED, arrival_event_id=event_id),
            MovementSagaRecord(id=saga_id, idempotency_key=str(saga_id), source='synthetic', occurred_at=NOW,
                access_event_id=event_id, state=MovementSagaState.COMPLETED, admission_status='verified'),
        ])
        await reserve_initial_publication_in_session(session, event_id)
        await session.commit()
    sent = []
    fail_visitor = True
    class Transport:
        async def publish(self, event_type, payload):
            sent.append((event_type, payload))
            return RealtimePublishReceipt('in_process', not (fail_visitor and event_type == 'visitor_pass.used'))
    store = AccessPublicationStore()
    lane = AccessPublicationLane(store, Transport())
    await lane.deliver(await store.claim())
    work = await work_value(event_id)
    assert work.state == 'queued' and work.result == {'published_revision': 0}
    assert sent[-1][0] == 'visitor_pass.used'
    assert sent[-1][1]['visitor_pass']['arrival_event_id'] == str(event_id)
    fail_visitor = False
    async with AsyncSessionLocal() as session:
        work = await session.get(AccessCompletionWork, work.id, with_for_update=True)
        work.due_at = datetime.now(UTC)
        await session.commit()
    await lane.deliver(await store.claim())
    work = await work_value(event_id)
    assert work.result == {'published_revision': 1, 'visitor_transition': 'visitor_pass.used'}
    assert [event_type for event_type, _ in sent].count('visitor_pass.used') == 2
    async with AsyncSessionLocal() as session:
        event = await session.get(AccessEvent, event_id, with_for_update=True)
        event.confidence = .8
        await mark_public_event_changed_in_session(session, event_id)
        await session.commit()
    await lane.deliver(await store.claim())
    assert sent[-1][0] == 'access_event.updated'
    assert [event_type for event_type, _ in sent].count('visitor_pass.used') == 2
    assert (await work_value(event_id)).result['visitor_transition'] == 'visitor_pass.used'
