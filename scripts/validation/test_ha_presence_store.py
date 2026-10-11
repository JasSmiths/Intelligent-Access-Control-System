"""Dormant HA reservation/attempt boundaries in guarded disposable PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, event as sql_event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import AsyncSessionLocal, engine
from app.models import (
    AccessEvent, AuditLog, HaPresenceCoordinator, HaPresenceDesired, HaPresenceEffect,
    HaPresenceResolution, HaPresenceTarget, MaintenanceModeState, Person, Presence, SystemSetting,
)
from app.models.enums import AccessDecision, AccessDirection, PresenceState
from app.modules.home_assistant.input_boolean_contracts import HaDelivery
from app.services import (
    ha_presence_attempts as attempts, ha_presence_reservations as reservations,
    settings as settings_owner,
)
from app.services.auth_root import AUTH_ROOT_LOCK_KEY
from app.services.ha_presence_attempts import HaPresenceAttemptStore
from app.services.ha_presence_contracts import HaCoordinationBusy, HaLeaseLost, HaPresenceReservation
from app.services.ha_presence_database import database_now

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def clear_synthetic_ha_state(isolated_resources):
    async with AsyncSessionLocal() as session:
        for model in [HaPresenceResolution, HaPresenceEffect, HaPresenceTarget, HaPresenceDesired, HaPresenceCoordinator, MaintenanceModeState]:
            await session.execute(delete(model))
        for key, value in [('home_assistant_url', 'http://synthetic.invalid'), ('home_assistant_token', 'synthetic-secret-never-sent')]:
            row = await session.get(SystemSetting, key)
            if row is None:
                row = SystemSetting(key=key, category='integrations', is_secret=False)
                session.add(row)
            row.value = {'plain': value}
        await session.commit()
    try:
        yield
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SystemSetting))
            await session.commit()


async def seed(*, entity=None, state=PresenceState.PRESENT, offset=0, photo=False):
    async with AsyncSessionLocal() as session:
        now = await database_now(session)
        person_id, event_id = uuid.uuid4(), uuid.uuid4()
        person = Person(id=person_id, display_name='Synthetic HA person', is_active=True,
            home_assistant_presence_input_boolean_entity_ids=[entity or 'input_boolean.'+person_id.hex],
            profile_photo_data_url=('data:image/jpeg;base64,'+'x'*1024*1024) if photo else None)
        session.add(person)
        await session.flush()
        session.add(AccessEvent(id=event_id, person_id=person_id, registration_number='SYN'+event_id.hex[:10],
            direction=AccessDirection.ENTRY if state==PresenceState.PRESENT else AccessDirection.EXIT,
            decision=AccessDecision.GRANTED, confidence=1, source='synthetic', occurred_at=now, raw_payload={}))
        await session.flush()
        session.add(Presence(person_id=person_id, state=state, last_event_id=event_id, last_changed_at=now))
        await session.commit()
        return HaPresenceReservation(person_id, event_id, event_id, state.value, now+timedelta(seconds=offset), True)


async def reserve(proposal):
    async with AsyncSessionLocal() as session:
        summary = await reservations.reserve_all_in_session(session, [proposal])
        await session.commit()
        return summary


async def prepare():
    return await HaPresenceAttemptStore().prepare_attempt(asyncio.get_running_loop().time()+10)


async def target_for(person_id):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(HaPresenceTarget).where(HaPresenceTarget.person_id==person_id))


async def test_reservation_participates_in_core_rollback_without_early_visibility():
    proposal = await seed()
    async with AsyncSessionLocal() as owner:
        result = await reservations.reserve_all_in_session(owner, [proposal])
        assert result.applied == 1
        async with AsyncSessionLocal() as observer:
            assert await observer.scalar(select(func.count()).select_from(HaPresenceDesired)) == 0
        await owner.rollback()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(HaPresenceTarget)) == 0


@pytest.mark.parametrize('state', [PresenceState.PRESENT, PresenceState.EXITED])
async def test_current_policy_defaults_to_turn_off_for_both_states(state):
    proposal = await seed(state=state)
    await reserve(proposal)
    prepared = await prepare()
    assert prepared is not None and prepared.action == 'turn_off'
    assert prepared.origin_at == proposal.origin_at
    assert prepared.deadline == proposal.origin_at+timedelta(seconds=300)


async def test_replay_after_invalidation_never_reenables_or_renews_origin():
    proposal = await seed()
    await reserve(proposal)
    async with AsyncSessionLocal() as session:
        generation = await reservations.invalidate_people_in_session(session, [proposal.person_id], reason='person_changed')
        await session.commit()
    assert (await reserve(proposal)).skipped == 1
    async with AsyncSessionLocal() as session:
        desired = await session.get(HaPresenceDesired, proposal.person_id)
        assert not desired.eligible and desired.generation == generation
        assert desired.origin_at == proposal.origin_at
    assert await prepare() is None


async def test_shared_target_latest_person_is_not_invalidated_by_former_owner():
    first, latest = await seed(entity='input_boolean.shared'), await seed(entity='input_boolean.shared')
    await reserve(first)
    await reserve(latest)
    async with AsyncSessionLocal() as session:
        await reservations.invalidate_people_in_session(session, [first.person_id], reason='person_changed')
        await session.commit()
    target = await target_for(latest.person_id)
    assert target is not None and target.state == 'ready'
    prepared = await prepare()
    assert prepared is not None
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        assert effect.person_id == latest.person_id


async def test_old_accepted_receipt_survives_new_desired_and_releases_only_matching_barrier():
    first = await seed(entity='input_boolean.shared')
    await reserve(first)
    prepared = await prepare()
    later = await seed(entity='input_boolean.shared')
    await reserve(later)
    target = await target_for(later.person_id)
    generation = target.generation
    await HaPresenceAttemptStore().record_receipt(prepared.claim, HaDelivery('accepted', 'accepted'))
    target = await target_for(later.person_id)
    assert target.generation == generation and target.state == 'ready' and target.barrier_attempt_id is None
    assert await HaPresenceAttemptStore().release_capacity(prepared.claim)
    following = await prepare()
    assert following is not None and following.claim.generation == generation
    async with AsyncSessionLocal() as session:
        old = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        assert old.state == 'accepted' and old.outcome == 'accepted'


@pytest.mark.parametrize('kind, expected', [('accepted','accepted'),('rejected','rejected'),('not_sent','rejected'),('unknown','unknown')])
async def test_delivery_mapping_retains_certainty_and_unknown_barrier(kind, expected):
    proposal = await seed()
    await reserve(proposal)
    prepared = await prepare()
    await HaPresenceAttemptStore().record_receipt(prepared.claim, HaDelivery(kind, 'accepted' if kind=='accepted' else 'provider_'+kind))
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        target = await session.get(HaPresenceTarget, prepared.claim.target_key)
        coordinator = await session.get(HaPresenceCoordinator, 1)
        assert effect.state == expected and effect.outcome == kind
        assert (target.barrier_attempt_id == prepared.claim.attempt_id) == (kind=='unknown')
        assert coordinator.lease_token == prepared.claim.token
    assert await HaPresenceAttemptStore().release_capacity(prepared.claim)
    async with AsyncSessionLocal() as session:
        assert (await session.get(HaPresenceCoordinator, 1)).lease_token is None


async def test_known_receipt_after_original_optional_expiry_is_not_a_new_send():
    proposal = await seed(offset=-299)
    await reserve(proposal)
    prepared = await prepare()
    assert prepared is not None
    await asyncio.sleep(1.1)
    await HaPresenceAttemptStore().record_receipt(prepared.claim, HaDelivery('accepted', 'accepted_observation_unavailable'))
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        assert effect.state == 'accepted'
    assert await prepare() is None


async def test_competing_owners_can_persist_only_one_current_attempt():
    await reserve(await seed())
    await reserve(await seed())
    first, second = await asyncio.gather(prepare(), prepare())
    assert sum(value is not None for value in [first, second]) == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(HaPresenceEffect)) == 1


async def test_complete_terminal_page_progresses_to_next_ready_target():
    for _ in range(32):
        proposal = await seed()
        await reserve(proposal)
        async with AsyncSessionLocal() as session:
            target = await session.scalar(select(HaPresenceTarget).where(HaPresenceTarget.person_id==proposal.person_id))
            target.deadline -= timedelta(seconds=301)
            await session.commit()
    ready = await seed()
    await reserve(ready)
    prepared = await prepare()
    assert prepared is not None
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        assert effect.person_id == ready.person_id
        assert await session.scalar(select(func.count()).select_from(HaPresenceTarget).where(HaPresenceTarget.state=='held')) == 32


async def test_root_contention_returns_busy_and_whole_caller_rollback():
    proposal = await seed()
    async with AsyncSessionLocal() as blocker, AsyncSessionLocal() as owner:
        await blocker.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key':AUTH_ROOT_LOCK_KEY})
        person = await owner.get(Person, proposal.person_id, with_for_update=True)
        person.display_name = 'Must roll back'
        with pytest.raises(HaCoordinationBusy):
            await asyncio.wait_for(reservations.reserve_all_in_session(owner, [proposal]), 1)
        await owner.rollback()
        await blocker.rollback()
    async with AsyncSessionLocal() as session:
        assert (await session.get(Person, proposal.person_id)).display_name == 'Synthetic HA person'
        assert await session.scalar(select(func.count()).select_from(HaPresenceDesired)) == 0


async def test_locked_person_projection_excludes_fat_photo_from_reserve_and_prepare():
    proposal = await seed(photo=True)
    statements=[]
    def record(_conn,_cursor,statement,_parameters,_context,_many):
        statements.append(statement)
    sql_event.listen(engine.sync_engine, 'before_cursor_execute', record)
    try:
        await reserve(proposal)
        assert await prepare() is not None
    finally:
        sql_event.remove(engine.sync_engine, 'before_cursor_execute', record)
    person_reads = [sql for sql in statements if sql.startswith('SELECT') and 'FROM people' in sql]
    assert person_reads and all('profile_photo_data_url' not in sql and 'people.notes' not in sql for sql in person_reads)


async def test_long_complete_identity_and_irrelevant_corrupt_setting_remain_accepted():
    long_url = 'http://synthetic.invalid/'+'u'*8192
    long_entity = 'input_boolean.'+'e'*8192
    async with AsyncSessionLocal() as session:
        (await session.get(SystemSetting, 'home_assistant_url')).value = {'plain':long_url}
        session.add(SystemSetting(key='dvla_timeout_seconds', category='synthetic', is_secret=False, value={'plain':{'malformed':'irrelevant'}}))
        await session.commit()
    proposal = await seed(entity=long_entity)
    assert (await reserve(proposal)).applied == 1
    prepared = await prepare()
    assert prepared is not None and prepared.entity_id == long_entity
    assert prepared.connection.home_assistant_url == long_url
    assert 'synthetic-secret-never-sent' not in repr(prepared)


async def test_attempt_audit_failure_rolls_back_claim_and_global_capacity(monkeypatch):
    await reserve(await seed())
    async def fail(*_args, **_kwargs):
        raise RuntimeError('required audit failed')
    monkeypatch.setattr(attempts, 'write_audit_log', fail)
    with pytest.raises(RuntimeError, match='required audit'):
        await prepare()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(HaPresenceEffect)) == 0
        coordinator = await session.get(HaPresenceCoordinator, 1)
        assert coordinator.lease_token is None


async def test_late_receipt_cannot_revive_expired_claim_and_recovery_keeps_unknown():
    await reserve(await seed())
    prepared = await prepare()
    async with AsyncSessionLocal() as session:
        coordinator = await session.get(HaPresenceCoordinator, 1)
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        coordinator.lease_until = effect.lease_until = await database_now(session)-timedelta(seconds=1)
        await session.commit()
    store=HaPresenceAttemptStore()
    assert not await store.renew_attempt(prepared.claim)
    with pytest.raises(HaLeaseLost):
        await store.record_receipt(prepared.claim, HaDelivery('accepted', 'accepted'))
    assert await store.recover_expired_attempts() == 1
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        target = await session.get(HaPresenceTarget, prepared.claim.target_key)
        assert effect.state == 'unknown' and target.barrier_attempt_id == effect.id
    assert await prepare() is None


@pytest.mark.parametrize('offset, reason', [(-301,'origin_expired'),(100,'origin_in_future')])
async def test_bad_original_origin_holds_without_allocating_attempt(offset, reason):
    proposal = await seed(offset=offset)
    assert (await reserve(proposal)).invalidated == 1
    assert await prepare() is None
    async with AsyncSessionLocal() as session:
        desired = await session.get(HaPresenceDesired, proposal.person_id)
        assert not desired.eligible and desired.outcome == reason
        assert await session.scalar(select(func.count()).select_from(HaPresenceEffect)) == 0


async def test_stale_projection_does_not_allocate_new_generation():
    from dataclasses import replace
    proposal = await seed()
    assert (await reserve(replace(proposal, presence_event_id=uuid.uuid4()))).skipped == 1
    async with AsyncSessionLocal() as session:
        assert (await session.get(HaPresenceCoordinator, 1)).next_generation == 0
        assert await session.get(HaPresenceDesired, proposal.person_id) is None


async def test_secret_clear_disables_optional_effect_without_core_failure():
    proposal = await seed()
    async with AsyncSessionLocal() as session:
        (await session.get(SystemSetting, 'home_assistant_token')).value = {'plain':''}
        await session.commit()
    assert (await reserve(proposal)).invalidated == 1
    assert await prepare() is None


async def test_forced_identity_collision_is_explainable_and_never_links_different_full_value(monkeypatch):
    proposal = await seed(entity='input_boolean.first')
    async with AsyncSessionLocal() as session:
        (await session.get(Person, proposal.person_id)).home_assistant_presence_input_boolean_entity_ids = [
            'input_boolean.first','input_boolean.second']
        await session.commit()
    monkeypatch.setattr(reservations, 'physical_target_key', lambda *_args:'c'*64)
    assert (await reserve(proposal)).invalidated == 1
    async with AsyncSessionLocal() as session:
        desired = await session.get(HaPresenceDesired, proposal.person_id)
        target = await session.get(HaPresenceTarget, 'c'*64)
        assert not desired.eligible and desired.outcome == 'physical_identity_collision'
        assert target.entity_id == 'input_boolean.first'
    assert await prepare() is None


async def test_receipt_resamples_fresh_db_clock_after_blocked_target_lock():
    await reserve(await seed())
    prepared = await prepare()
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        coordinator = await session.get(HaPresenceCoordinator, 1)
        effect.lease_until = coordinator.lease_until = await database_now(session)+timedelta(seconds=.1)
        await session.commit()
    async with AsyncSessionLocal() as blocker:
        await blocker.get(HaPresenceTarget, prepared.claim.target_key, with_for_update=True)
        result = asyncio.create_task(HaPresenceAttemptStore().record_receipt(prepared.claim, HaDelivery('accepted','accepted')))
        await asyncio.sleep(.15)
        assert not result.done()
        await blocker.rollback()
        with pytest.raises(HaLeaseLost):
            await asyncio.wait_for(result, 1)
    async with AsyncSessionLocal() as session:
        assert (await session.get(HaPresenceEffect, prepared.claim.attempt_id)).state == 'attempting'


async def test_receipt_audit_failure_does_not_relabel_or_release_attempt(monkeypatch):
    await reserve(await seed())
    prepared = await prepare()
    async def fail(*_args, **_kwargs):
        raise RuntimeError('required receipt audit failed')
    monkeypatch.setattr(attempts, 'write_audit_log', fail)
    with pytest.raises(RuntimeError, match='required receipt audit'):
        await HaPresenceAttemptStore().record_receipt(prepared.claim, HaDelivery('accepted','accepted'))
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        target = await session.get(HaPresenceTarget, prepared.claim.target_key)
        coordinator = await session.get(HaPresenceCoordinator, 1)
        assert effect.state == 'attempting' and target.barrier_attempt_id == effect.id
        assert coordinator.lease_token == prepared.claim.token


async def test_old_receipt_cannot_clear_replacement_barrier():
    await reserve(await seed())
    prepared = await prepare()
    replacement = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        target = await session.get(HaPresenceTarget, prepared.claim.target_key)
        target.barrier_attempt_id, target.barrier_version = replacement, target.barrier_version+1
        await session.commit()
    await HaPresenceAttemptStore().record_receipt(prepared.claim, HaDelivery('accepted','accepted'))
    async with AsyncSessionLocal() as session:
        target = await session.get(HaPresenceTarget, prepared.claim.target_key)
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        assert effect.state == 'accepted' and target.barrier_attempt_id == replacement


async def test_configuration_nowait_contention_maps_to_full_caller_busy():
    proposal = await seed()
    await reserve(proposal)
    async with AsyncSessionLocal() as blocker, AsyncSessionLocal() as owner:
        await blocker.get(SystemSetting, 'home_assistant_token', with_for_update=True)
        person = await owner.get(Person, proposal.person_id, with_for_update=True)
        person.display_name = 'Must roll back'
        with pytest.raises(HaCoordinationBusy):
            await asyncio.wait_for(reservations.invalidate_configuration_in_session(owner), 1)
        await owner.rollback()
        await blocker.rollback()
    async with AsyncSessionLocal() as session:
        assert (await session.get(Person, proposal.person_id)).display_name == 'Synthetic HA person'
        assert (await session.get(HaPresenceDesired, proposal.person_id)).eligible


async def test_real_settings_writer_auth_root_fence_rolls_back_whole_reservation_caller(monkeypatch):
    proposal = await seed()
    async with AsyncSessionLocal() as session:
        await session.execute(delete(SystemSetting).where(SystemSetting.key == 'home_assistant_url'))
        await session.commit()
    acquired, release = asyncio.Event(), asyncio.Event()

    class PausedSettingWriter(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            # Root-first settings composition must be observed before settings.
            if str(statement) == 'SELECT pg_advisory_xact_lock(:key)' and args[0] == {'key': AUTH_ROOT_LOCK_KEY}:
                acquired.set()
                await release.wait()
            return result

    writer_sessions = async_sessionmaker(engine, class_=PausedSettingWriter, expire_on_commit=False)
    monkeypatch.setattr(settings_owner, 'AsyncSessionLocal', writer_sessions)
    replacement = 'http://replacement.synthetic.invalid'
    writer = asyncio.create_task(settings_owner.update_settings({'home_assistant_url': replacement}))
    try:
        await asyncio.wait_for(acquired.wait(), 2)
        async with AsyncSessionLocal() as owner:
            person = await owner.get(Person, proposal.person_id, with_for_update=True)
            person.display_name = 'Must roll back'
            await owner.flush()
            with pytest.raises(HaCoordinationBusy, match='^ha_auth_root_busy$'):
                await asyncio.wait_for(reservations.reserve_all_in_session(owner, [proposal]), 1)
            await owner.rollback()
        async with AsyncSessionLocal() as session:
            assert await session.get(SystemSetting, 'home_assistant_url') is None
            assert (await session.get(Person, proposal.person_id)).display_name == 'Synthetic HA person'
            assert await session.get(HaPresenceDesired, proposal.person_id) is None
    finally:
        release.set()
        await asyncio.wait_for(writer, 3)
    assert (await reserve(proposal)).applied == 1
    async with AsyncSessionLocal() as session:
        desired = await session.get(HaPresenceDesired, proposal.person_id)
        target = await target_for(proposal.person_id)
        assert desired.origin_at == proposal.origin_at
        assert desired.deadline == proposal.origin_at + timedelta(seconds=300)
        assert target.url == replacement
        assert await session.scalar(select(AuditLog).where(AuditLog.action == 'settings.update')) is not None


async def test_missing_setting_creation_fence_rolls_back_whole_configuration_participant(monkeypatch):
    from app.services.home_assistant_configuration import lock_home_assistant_connection

    proposal = await seed()
    async with AsyncSessionLocal() as session:
        await session.execute(delete(SystemSetting).where(SystemSetting.key == 'home_assistant_url'))
        await session.commit()
    acquired, release = asyncio.Event(), asyncio.Event()

    class PausedSettingWriter(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            if str(statement) == 'SELECT pg_advisory_xact_lock(hashtext(:setting))' and args[0] == {'setting': 'iacs:setting:home_assistant_url'}:
                acquired.set()
                await release.wait()
            return result

    writer_sessions = async_sessionmaker(engine, class_=PausedSettingWriter, expire_on_commit=False)
    monkeypatch.setattr(settings_owner, 'AsyncSessionLocal', writer_sessions)
    replacement = 'http://setting-fence.synthetic.invalid'
    writer = asyncio.create_task(settings_owner.update_settings({'home_assistant_url': replacement}))
    try:
        await asyncio.wait_for(acquired.wait(), 2)
        async with AsyncSessionLocal() as owner:
            person = await owner.get(Person, proposal.person_id, with_for_update=True)
            person.display_name = 'Must roll back'
            await owner.flush()
            # Exercise the narrow setting participant directly: the complete
            # reservation would correctly stop earlier at the auth-root fence.
            with pytest.raises(HaCoordinationBusy, match='^ha_setting_busy$'):
                await asyncio.wait_for(lock_home_assistant_connection(owner), 1)
            await owner.rollback()
        async with AsyncSessionLocal() as observer:
            assert await observer.get(SystemSetting, 'home_assistant_url') is None
            assert (await observer.get(Person, proposal.person_id)).display_name == 'Synthetic HA person'
            assert await observer.get(HaPresenceDesired, proposal.person_id) is None
    finally:
        release.set()
        await asyncio.wait_for(writer, 3)
    async with AsyncSessionLocal() as observer:
        assert (await observer.get(SystemSetting, 'home_assistant_url')).value == {'plain': replacement}
        assert await observer.scalar(select(AuditLog).where(AuditLog.action == 'settings.update')) is not None


async def test_historical_applied_projection_invalidates_without_new_live_budget():
    from dataclasses import replace
    first = await seed(entity='input_boolean.historical')
    await reserve(first)
    prepared = await prepare()
    assert prepared is not None
    async with AsyncSessionLocal() as session:
        event_id = uuid.uuid4()
        session.add(AccessEvent(id=event_id, person_id=first.person_id,
            registration_number='SYNHISTORY', direction=AccessDirection.EXIT,
            decision=AccessDecision.GRANTED, confidence=1, source='synthetic-historical',
            occurred_at=first.origin_at-timedelta(hours=1), raw_payload={}))
        await session.flush()
        presence = await session.get(Presence, first.person_id, with_for_update=True)
        presence.state, presence.last_event_id = PresenceState.EXITED, event_id
        await session.commit()
    historical = replace(first, event_id=event_id, presence_event_id=event_id,
        presence_state='exited', origin_at=None, live_eligible=False)
    assert (await reserve(historical)).invalidated == 1
    async with AsyncSessionLocal() as session:
        desired = await session.get(HaPresenceDesired, historical.person_id)
        target = await session.get(HaPresenceTarget, prepared.claim.target_key)
        assert not desired.eligible and desired.outcome == 'historical_invalidation'
        assert desired.origin_at is None and desired.deadline is None
        assert target.state == 'invalidated' and target.origin_at == first.origin_at
        assert target.barrier_attempt_id == prepared.claim.attempt_id
    assert await prepare() is None



async def test_renewal_final_clock_cannot_revive_old_expiry_during_flush(monkeypatch):
    await reserve(await seed())
    prepared = await prepare()
    assert prepared is not None
    async with AsyncSessionLocal() as session:
        now = await database_now(session)
        original = now+timedelta(seconds=1)
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        coordinator = await session.get(HaPresenceCoordinator, 1)
        effect.lease_until = coordinator.lease_until = original
        await session.commit()
    samples = iter([now, original+timedelta(seconds=1)])
    async def clock(_session):
        return next(samples)
    monkeypatch.setattr(attempts, 'database_now', clock)
    assert not await HaPresenceAttemptStore().renew_attempt(prepared.claim)
    async with AsyncSessionLocal() as session:
        assert (await session.get(HaPresenceEffect, prepared.claim.attempt_id)).lease_until == original
        assert (await session.get(HaPresenceCoordinator, 1)).lease_until == original


@pytest.mark.parametrize('changed, reason', [
    ('inactive', 'person_unavailable'), ('binding', 'binding_changed'),
    ('configuration', 'configuration_changed'), ('maintenance', 'maintenance_active'),
])
async def test_attempt_rechecks_current_authority_before_new_effect(changed, reason):
    proposal = await seed()
    await reserve(proposal)
    async with AsyncSessionLocal() as session:
        if changed == 'inactive':
            (await session.get(Person, proposal.person_id)).is_active = False
        elif changed == 'binding':
            (await session.get(Person, proposal.person_id)).home_assistant_presence_input_boolean_entry_action = 'turn_on'
        elif changed == 'configuration':
            (await session.get(SystemSetting, 'home_assistant_token')).value = {'plain':'changed-synthetic-secret'}
        else:
            session.add(MaintenanceModeState(id=1, is_active=True))
        await session.commit()
    assert await prepare() is None
    async with AsyncSessionLocal() as session:
        desired = await session.get(HaPresenceDesired, proposal.person_id)
        assert not desired.eligible and desired.outcome == reason
        assert await session.scalar(select(func.count()).select_from(HaPresenceEffect)) == 0


async def test_stalled_database_response_cannot_extend_original_operation_budget(monkeypatch):
    await reserve(await seed())
    actual_clock = attempts.database_now
    monkeypatch.setattr(attempts, 'HA_OPERATION_SECONDS', .01)
    calls = 0
    async def delayed_clock(session):
        nonlocal calls
        calls += 1
        observed = await actual_clock(session)
        if calls == 1:
            await asyncio.sleep(.03)
        return observed
    monkeypatch.setattr(attempts, 'database_now', delayed_clock)
    with pytest.raises(HaLeaseLost):
        await HaPresenceAttemptStore().prepare_attempt(asyncio.get_running_loop().time()+10)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(HaPresenceEffect)) == 0
        assert (await session.get(HaPresenceCoordinator, 1)).lease_token is None


async def test_accepted_receipt_close_retains_capacity_until_actual_cleanup_and_explicit_release(monkeypatch):
    await reserve(await seed())
    prepared = await prepare()
    assert prepared is not None
    await reserve(await seed())
    entered, release = asyncio.Event(), asyncio.Event()
    session = AsyncSessionLocal()
    actual_close = session.close
    async def close():
        entered.set()
        await release.wait()
        await actual_close()
    monkeypatch.setattr(session, 'close', close)
    receipt = asyncio.create_task(HaPresenceAttemptStore(lambda:session).record_receipt(
        prepared.claim, HaDelivery('accepted', 'accepted')))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        async with AsyncSessionLocal() as observer:
            effect = await observer.get(HaPresenceEffect, prepared.claim.attempt_id)
            coordinator = await observer.get(HaPresenceCoordinator, 1)
            assert effect.state == 'accepted' and coordinator.lease_token == prepared.claim.token
        assert await prepare() is None
        assert not receipt.done()
    finally:
        release.set()
        await receipt
    assert await prepare() is None  # Dedicated client/operation may still own cleanup.
    assert await HaPresenceAttemptStore().release_capacity(prepared.claim)
    assert await prepare() is not None


async def test_terminal_renewal_retains_only_capacity_without_rewriting_receipt():
    await reserve(await seed())
    prepared = await prepare()
    assert prepared is not None
    store = HaPresenceAttemptStore()
    await store.record_receipt(prepared.claim, HaDelivery('accepted', 'accepted'))
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        before = (effect.state, effect.outcome, effect.detail, effect.completed_at, effect.lease_until)
    assert await store.renew_attempt(prepared.claim)
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        assert (effect.state, effect.outcome, effect.detail, effect.completed_at, effect.lease_until) == before
        assert (await session.get(HaPresenceCoordinator, 1)).lease_token == prepared.claim.token
    with pytest.raises(HaLeaseLost):
        await store.record_receipt(prepared.claim, HaDelivery('accepted', 'accepted'))
    assert await store.release_capacity(prepared.claim)


async def test_capacity_release_refuses_attempting_or_replacement_owner():
    await reserve(await seed())
    prepared = await prepare()
    assert prepared is not None
    store = HaPresenceAttemptStore()
    assert not await store.release_capacity(prepared.claim)
    await store.record_receipt(prepared.claim, HaDelivery('accepted', 'accepted'))
    replacement = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        coordinator = await session.get(HaPresenceCoordinator, 1)
        coordinator.lease_token = replacement
        coordinator.epoch += 1
        await session.commit()
    assert not await store.release_capacity(prepared.claim)
    async with AsyncSessionLocal() as session:
        assert (await session.get(HaPresenceCoordinator, 1)).lease_token == replacement


async def test_confirmed_claim_callback_precedes_actual_close_and_retains_renewable_capacity(monkeypatch):
    proposal = await seed()
    await reserve(proposal)
    closing, release = asyncio.Event(), asyncio.Event()
    claims = []
    def factory():
        session = AsyncSessionLocal()
        committed = False
        commit, close = session.commit, session.close
        async def commit_owned():
            nonlocal committed
            await commit()
            committed = True
        async def close_owned():
            if committed:
                closing.set()
                await release.wait()
            await close()
        monkeypatch.setattr(session, 'commit', commit_owned)
        monkeypatch.setattr(session, 'close', close_owned)
        return session
    owner = HaPresenceAttemptStore(factory)
    actual = asyncio.create_task(owner.prepare_attempt(
        asyncio.get_running_loop().time()+10, on_committed=claims.append))
    try:
        await asyncio.wait_for(closing.wait(), 2)
        assert len(claims) == 1 and not actual.done()
        assert await HaPresenceAttemptStore().renew_attempt(claims[0].claim)
        assert await prepare() is None
        async with AsyncSessionLocal() as session:
            coordinator = await session.get(HaPresenceCoordinator, 1)
            assert coordinator.lease_token == claims[0].claim.token
            assert coordinator.lease_until > await database_now(session)
        release.set()
        assert await actual is claims[0]
    finally:
        release.set()
        await asyncio.gather(actual, return_exceptions=True)


async def test_unknown_claim_commit_does_not_invoke_ownership_callback(monkeypatch):
    proposal = await seed()
    await reserve(proposal)
    claims = []
    def factory():
        session = AsyncSessionLocal()
        commit = session.commit
        async def uncertain_commit():
            await commit()  # Simulate lost response after the server committed.
            raise ConnectionError('synthetic commit response lost')
        monkeypatch.setattr(session, 'commit', uncertain_commit)
        return session
    with pytest.raises(ConnectionError, match='synthetic commit response lost'):
        await HaPresenceAttemptStore(factory).prepare_attempt(
            asyncio.get_running_loop().time()+10, on_committed=claims.append)
    assert claims == []
    async with AsyncSessionLocal() as session:
        effect = await session.scalar(select(HaPresenceEffect))
        assert effect is not None and effect.state == 'attempting'
        target = await session.get(HaPresenceTarget, effect.target_key)
        assert target.barrier_attempt_id == effect.id
    assert await prepare() is None


async def test_callback_failure_keeps_committed_attempt_and_barrier_recoverable():
    proposal = await seed()
    await reserve(proposal)
    calls = []
    def fail(prepared):
        calls.append(prepared)
        raise RuntimeError('synthetic ownership callback failure')
    with pytest.raises(RuntimeError, match='synthetic ownership callback failure'):
        await HaPresenceAttemptStore().prepare_attempt(
            asyncio.get_running_loop().time()+10, on_committed=fail)
    assert len(calls) == 1
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, calls[0].claim.attempt_id)
        assert effect.state == 'attempting'
        assert (await session.get(HaPresenceTarget, effect.target_key)).barrier_attempt_id == effect.id
        past = await database_now(session)-timedelta(seconds=1)
        effect.lease_until = past
        coordinator = await session.get(HaPresenceCoordinator, 1)
        coordinator.lease_until = past
        await session.commit()
    assert await HaPresenceAttemptStore().recover_expired_attempts() == 1
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, calls[0].claim.attempt_id)
        assert effect.state == 'unknown'
        assert (await session.get(HaPresenceTarget, effect.target_key)).barrier_attempt_id == effect.id
