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


async def test_compact_projection_uses_current_columns_and_named_json_never_full_vendor_payload(monkeypatch):
    monkeypatch.setattr(publication,'snapshot_path_available',lambda path: True)
    identity=await reserve(snapshot_path='synthetic/committed.jpg',snapshot_bytes=123,snapshot_width=640,snapshot_height=480,
        snapshot_camera='camera',snapshot_captured_at=NOW,raw_payload={'vendor_blob':'x'*900000,
        'visitor_pass':{'id':str(uuid.uuid4()),'visitor_name':'Guest','mode':'arrival','unused':'y'*90000},
        'movement_saga':{'id':str(uuid.uuid4()),'state':'completed','presence_committed':True,'admission_status':'verified',
            'admission_evidence':{'provider_raw':'z'*90000},'gate':{'command_id':str(uuid.uuid4()),'accepted':True,'state':'open',
                'mechanically_confirmed':True,'unused':'q'*90000}}})
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
