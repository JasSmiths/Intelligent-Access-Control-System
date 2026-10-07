"""Durable foundation contracts on the explicitly disposable PostgreSQL namespace."""
import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import CheckConstraint, Column, MetaData, Table, func, inspect, select, text
from sqlalchemy.schema import CreateTable
from test_recovery_boundaries import isolated_resources as isolated_resources

from app.db.base import Base
from app.db.migration_policy import include_schema_object
from app.db.session import AsyncSessionLocal, engine
from app.models import (
    AccessCompletionWork,
    AccessEvent,
    AuditLog,
    LprCoordinatorState,
    LprDebounceWindow,
    LprIngestEvent,
    LprWindowMember,
    MovementSagaRecord,
)
from app.models.enums import AccessDecision, AccessDirection
from app.services.access.pipeline_contracts import IngestClaim, PipelineLeaseLost, WindowClaim
from app.services.access.pipeline_store import (
    AccessPipelineStore,
    assert_ingest_in_session,
    assert_window_in_session,
    database_now,
    terminalize_window_in_session,
)

pytestmark = pytest.mark.asyncio
MIGRATION = Path(__file__).resolve().parents[2] / 'backend/alembic/versions/20261007_0013_access_pipeline.py'
NEW_TABLES = ('lpr_debounce_windows','lpr_window_members','lpr_observation_tokens',
    'movement_session_observations','lpr_coordinator_state','access_completion_work',
    'ha_presence_desired','ha_presence_targets','ha_presence_effects',
    'ha_presence_effect_resolutions','ha_presence_coordinator','access_chart_dirty_state',
    'historical_access_candidates')
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


@pytest_asyncio.fixture(autouse=True)
async def owned_resources(isolated_resources):
    async with AsyncSessionLocal() as session:
        await session.execute(text('TRUNCATE ' + ','.join(NEW_TABLES) + ', lpr_ingest_events CASCADE'))
        await session.commit()
    yield


def migrate(connection, direction):
    spec = importlib.util.spec_from_file_location('pipeline_migration_fixture', MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


async def test_empty_upgrade_downgrade_and_complete_metadata_parity():
    # PostgreSQL transactional DDL keeps the harness template at head after rollback.
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(lambda sync: migrate(sync, 'downgrade'))
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            assert not tables.intersection(NEW_TABLES)
            assert await connection.scalar(text("SELECT to_regclass('lpr_ready_ordinal_seq')")) is None
            await connection.run_sync(lambda sync: migrate(sync, 'upgrade'))
            assert await connection.scalar(text("SELECT to_regclass('lpr_ready_ordinal_seq')")) == 'lpr_ready_ordinal_seq'
            differences = await connection.run_sync(lambda sync: compare_metadata(
                MigrationContext.configure(sync, opts={'include_object': include_schema_object}), Base.metadata))
            assert differences == [], differences
            # Autogenerate does not compare CHECK bodies or FK deferrability: check them explicitly.
            def structures(sync):
                inspector = inspect(sync)
                for name in NEW_TABLES:
                    expected = Base.metadata.tables[name]
                    actual_checks = {item['name']: item['sqltext'] for item in inspector.get_check_constraints(name)}
                    temporary = Table('expected_' + name, MetaData(),
                        *(Column(column.name,column.type,nullable=column.nullable) for column in expected.columns),
                        *(CheckConstraint(str(constraint.sqltext),name=constraint.name)
                          for constraint in expected.constraints if isinstance(constraint,CheckConstraint)),
                        prefixes=['TEMPORARY'])
                    sync.execute(CreateTable(temporary))
                    expected_checks = {item['name']:item['sqltext'] for item in inspect(sync).get_check_constraints(temporary.name)}
                    assert actual_checks == expected_checks, (name,actual_checks,expected_checks)
                    expected_fks = {(tuple(c.parent.name for c in fk.elements), fk.referred_table.name,
                        tuple(c.column.name for c in fk.elements), fk.ondelete) for fk in expected.foreign_key_constraints}
                    actual_fks = {(tuple(fk['constrained_columns']), fk['referred_table'],
                        tuple(fk['referred_columns']), fk.get('options', {}).get('ondelete')) for fk in inspector.get_foreign_keys(name)}
                    assert actual_fks == expected_fks, (name, actual_fks, expected_fks)
                member_unique = sync.execute(text("SELECT condeferrable,condeferred FROM pg_constraint WHERE conname='uq_lpr_window_member_order'")).one()
                assert tuple(member_unique) == (True,True)
            await connection.run_sync(structures)
        finally:
            await transaction.rollback()


async def test_legacy_pending_processing_hold_preserves_original_values():
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(lambda sync: migrate(sync, 'downgrade'))
            # Genuine pre-P6 columns, including original JSONB binary representation.
            for index, status in enumerate(('pending','processing','succeeded','failed','skipped')):
                await connection.execute(text("""INSERT INTO lpr_ingest_events
                    (id,idempotency_key,source,registration_number,captured_at,received_at,
                     normalized_payload,status,attempt_count,created_at,updated_at)
                    VALUES (:id,:key,'synthetic','AB12CDE',:captured,:received,
                    CAST(:payload AS jsonb),:status,3,:captured,:received)"""),
                    {'id':uuid.uuid4(),'key':f'legacy-{index}','captured':NOW,'received':NOW+timedelta(seconds=2),
                     'payload':'{"gate_observation":{"state":"unknown"},"nested":[3,null," retained "]}' if index % 2 else '{"missing_observation":true}', 'status':status})
            await connection.execute(text("""INSERT INTO access_events
                (id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification,created_at,updated_at)
                VALUES ('00000000-0000-0000-0000-000000000001','AB12CDE','ENTRY','GRANTED',1,'synthetic',:now,'UNKNOWN',:now,:now)"""), {'now':NOW})
            for status in ('queued','processing','succeeded'):
                event = uuid.uuid4()
                await connection.execute(text("""INSERT INTO access_events
                    (id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification,created_at,updated_at)
                    VALUES (:id,'AB12CDE','ENTRY','GRANTED',1,'synthetic',:now,'UNKNOWN',:now,:now)"""), {'id':event,'now':NOW})
                await connection.execute(text("""INSERT INTO vehicle_information_jobs
                    (event_id,registration_number,created_at,deadline,status) VALUES (:event,'AB12CDE',:now,:deadline,:status)"""),
                    {'event':event,'now':NOW,'deadline':NOW+timedelta(minutes=5),'status':status})
            before = (await connection.execute(text('SELECT id,idempotency_key,captured_at,received_at,pg_catalog.jsonb_send(normalized_payload),attempt_count,created_at,updated_at FROM lpr_ingest_events ORDER BY idempotency_key'))).all()
            jobs_before = (await connection.execute(text('SELECT event_id,created_at,deadline,status FROM vehicle_information_jobs ORDER BY event_id'))).all()
            await connection.run_sync(lambda sync: migrate(sync, 'upgrade'))
            after = (await connection.execute(text('SELECT id,idempotency_key,captured_at,received_at,pg_catalog.jsonb_send(normalized_payload),attempt_count,created_at,updated_at FROM lpr_ingest_events ORDER BY idempotency_key'))).all()
            assert before == after
            rows = (await connection.execute(text('SELECT status,observation_state,terminal_reason FROM lpr_ingest_events ORDER BY idempotency_key'))).all()
            assert rows[:2] == [('held','held','legacy_unordered')]*2
            assert rows[2:] == [(value,'legacy_terminal',None) for value in ('succeeded','failed','skipped')]
            jobs_after = {row.event_id:row for row in (await connection.execute(text('SELECT event_id,created_at,deadline,status,readiness,outcome FROM vehicle_information_jobs'))).all()}
            for old in jobs_before:
                new = jobs_after[old.event_id]
                assert (new.created_at,new.deadline) == (old.created_at,old.deadline)
                assert new.status == ('held' if old.status in {'queued','processing'} else old.status)
                assert new.readiness == ('held' if old.status in {'queued','processing'} else 'legacy')
            with pytest.raises(RuntimeError, match='verified matching backup'):
                await connection.run_sync(lambda sync: migrate(sync, 'downgrade'))
            assert set(NEW_TABLES).issubset(await connection.run_sync(lambda sync: set(inspect(sync).get_table_names())))
        finally:
            await transaction.rollback()


@pytest.mark.parametrize('populated', ['coordinator','revision','ingest_capture'])
async def test_populated_downgrade_refuses_before_ddl(populated):
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            if populated == 'coordinator':
                await connection.execute(text('INSERT INTO lpr_coordinator_state (id,epoch,next_open_ordinal,handled_ready_ordinal) VALUES (1,0,1,0)'))
            elif populated == 'revision':
                await connection.execute(text("""INSERT INTO access_events
                    (id,registration_number,direction,decision,confidence,source,occurred_at,timing_classification,created_at,updated_at,resource_revision)
                    VALUES (:id,'AB12CDE','ENTRY','GRANTED',1,'synthetic',:now,'UNKNOWN',:now,:now,1)"""), {'id':uuid.uuid4(),'now':NOW})
            else:
                await connection.execute(text("""INSERT INTO lpr_ingest_events
                    (id,idempotency_key,source,registration_number,captured_at,received_at,normalized_payload,status,attempt_count,created_at,updated_at,capture_until)
                    VALUES (:id,'capture','synthetic','AB12CDE',:now,:now,'{}','succeeded',0,:now,:now,:now)"""), {'id':uuid.uuid4(),'now':NOW})
            with pytest.raises(RuntimeError, match='verified matching backup'):
                await connection.run_sync(lambda sync: migrate(sync, 'downgrade'))
            assert set(NEW_TABLES).issubset(await connection.run_sync(lambda sync: set(inspect(sync).get_table_names())))
        finally:
            await transaction.rollback()


async def seed_window():
    store = AccessPipelineStore()
    coordinator = await store.acquire_coordinator()
    assert coordinator is not None
    claim = WindowClaim(uuid.uuid4(), uuid.uuid4(), coordinator)
    async with AsyncSessionLocal() as session:
        now = await database_now(session)
        window = LprDebounceWindow(id=claim.window_id,open_ordinal=1,source='synthetic',state='finalizing',
            first_seen=NOW,updated_at=NOW,min_received_at=NOW,member_count=2,ranking_context={},
            lease_token=claim.token,lease_until=now+timedelta(seconds=120),coordinator_epoch=coordinator.epoch)
        session.add(window)
        await session.flush()
        ids=[]
        for index in range(2):
            row = LprIngestEvent(id=uuid.uuid4(),idempotency_key=f'member-{index}',source='synthetic',registration_number='AB12CDE',
                captured_at=NOW,received_at=NOW,normalized_payload={'immutable':index},status='processing',observation_state='ready',
                ready_ordinal=index+1,lease_token=uuid.uuid4(),lease_until=now+timedelta(seconds=120),window_id=window.id)
            session.add(row)
            await session.flush()
            session.add(LprWindowMember(ingest_id=row.id,window_id=window.id,ordinal=index,prepared_context={}))
            ids.append(row.id)
        await session.commit()
    return store,claim,ids


async def test_exclusive_coordinator_and_stale_epoch_are_fenced():
    store=AccessPipelineStore()
    first=await store.acquire_coordinator()
    assert first is not None and await store.acquire_coordinator() is None
    renewed=await store.renew_coordinator(first)
    assert renewed.token == first.token and renewed.epoch == first.epoch
    await store.release_coordinator(renewed)
    second=await store.acquire_coordinator()
    assert second is not None and second.epoch > first.epoch
    with pytest.raises(PipelineLeaseLost):
        await store.renew_coordinator(first)
    async with AsyncSessionLocal() as session:
        row = await session.get(LprCoordinatorState, 1, with_for_update=True)
        row.lease_until = await database_now(session) - timedelta(seconds=1)
        await session.commit()
    with pytest.raises(PipelineLeaseLost):
        await store.renew_coordinator(second)
    third = await store.acquire_coordinator()
    assert third is not None and third.epoch > second.epoch and third.token != second.token


@pytest.mark.parametrize('kind', ['window','ingest'])
async def test_blocked_lock_past_lease_uses_fresh_database_clock(kind):
    _,claim,ids=await seed_window()
    model,identity=(LprDebounceWindow,claim.window_id) if kind=='window' else (LprIngestEvent,ids[0])
    async with AsyncSessionLocal() as locker, AsyncSessionLocal() as owner, AsyncSessionLocal() as observer:
        locked=await locker.get(model,identity,with_for_update=True)
        locked.lease_until=await database_now(locker)+timedelta(milliseconds=100)
        await locker.commit()
        await locker.get(model,identity,with_for_update=True,populate_existing=True)
        pid=await owner.scalar(text('SELECT pg_backend_pid()'))
        transaction_time=await owner.scalar(select(func.now()))
        if kind=='window':
            task=asyncio.create_task(assert_window_in_session(owner,claim))
        else:
            row=await observer.get(LprIngestEvent,identity)
            task=asyncio.create_task(assert_ingest_in_session(owner,IngestClaim(identity,row.lease_token,claim.coordinator)))
        try:
            async def wait_until_blocked():
                while await observer.scalar(text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"),{'pid':pid}) != 'Lock':
                    await asyncio.sleep(.01)
            await asyncio.wait_for(wait_until_blocked(),5)
            async def wait_until_expired():
                while await database_now(observer) <= locked.lease_until:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(wait_until_expired(),5)
            assert transaction_time < locked.lease_until
            await locker.rollback()
            with pytest.raises(PipelineLeaseLost,match=kind+'_lease_lost'):
                await asyncio.wait_for(task,5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
            await owner.rollback()


async def test_window_outcome_and_required_writes_share_caller_commit():
    _,claim,ids=await seed_window()
    event_id,saga_id=uuid.uuid4(),uuid.uuid4()
    async def write(session, fail_audit=False):
        event=AccessEvent(id=event_id,registration_number='AB12CDE',direction=AccessDirection.ENTRY,
            decision=AccessDecision.GRANTED,confidence=1,source='synthetic',occurred_at=NOW,resource_revision=1)
        session.add(event)
        await session.flush()
        session.add(MovementSagaRecord(id=saga_id,idempotency_key='window-fixture',source='synthetic',occurred_at=NOW,access_event_id=event_id))
        await session.flush()
        session.add(AccessCompletionWork(id=uuid.uuid4(),event_id=event_id,window_id=claim.window_id,stage='publication',
            origin={'window_id':str(claim.window_id),'version':2},origin_at=NOW,deadline=None,
            readiness='ready',state='queued',due_at=NOW,attempt_count=0,result={}))
        await terminalize_window_in_session(session,claim,event_id=event_id,saga_id=saga_id)
        if fail_audit:
            raise RuntimeError('required audit unavailable')
        session.add(AuditLog(category='access',action='fixture.outcome',actor='synthetic',target_id=str(event_id)))
        await session.flush()
        await assert_window_in_session(session,claim,completed=True)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError,match='required audit'):
            await write(session,True)
        await session.rollback()
    async with AsyncSessionLocal() as observer:
        assert await observer.get(AccessEvent,event_id) is None
        assert await observer.get(MovementSagaRecord,saga_id) is None
        assert (await observer.get(LprDebounceWindow,claim.window_id)).state=='finalizing'
        assert set(await observer.scalars(select(LprIngestEvent.status)))=={'processing'}
        assert await observer.scalar(select(func.count()).select_from(AccessCompletionWork))==0
    async with AsyncSessionLocal() as session:
        await write(session)
        async with AsyncSessionLocal() as observer:
            assert await observer.get(AccessEvent,event_id) is None
            assert set(await observer.scalars(select(LprIngestEvent.status)))=={'processing'}
        await session.commit()
    async with AsyncSessionLocal() as observer:
        assert await observer.get(AccessEvent,event_id) is not None
        assert await observer.get(MovementSagaRecord,saga_id) is not None
        assert (await observer.get(LprDebounceWindow,claim.window_id)).state=='completed'
        rows=list(await observer.scalars(select(LprIngestEvent)))
        assert {row.id for row in rows}==set(ids)
        assert all(row.status=='succeeded' and row.access_event_id==event_id and row.movement_saga_id==saga_id for row in rows)
        assert await observer.scalar(select(func.count()).select_from(AccessCompletionWork))==1
        assert await observer.scalar(select(func.count()).select_from(AuditLog))==1
