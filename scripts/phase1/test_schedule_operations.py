"""Schedule adapter/persistence parity. Only run in the isolated harness."""
import asyncio
import os
from pathlib import Path
import uuid

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
assert '@127.0.0.1:5432/iacs_p1_' in os.environ.get('IACS_DATABASE_URL', '')

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select

from app.ai.context import set_chat_tool_context
from app.ai.tool_groups import schedules_handlers as alfred
from app.api.dependencies import current_user
from app.api.v1 import schedules as api
from app.db.session import AsyncSessionLocal, engine
from app.models import AccessDevice, AuditLog, Person, Schedule, User, Vehicle
from app.models.enums import UserRole
from app.services import action_confirmations, schedule_operations as operations
from app.services.schedules import normalize_time_blocks
from app.services.telemetry import actor_from_user

BLOCKS = {'0': [{'start': '08:00', 'end': '09:00'}, {'start': '08:30', 'end': '10:00'}]}


@pytest_asyncio.fixture(autouse=True)
async def isolated_test(monkeypatch):
    # Confirmation telemetry queue is not started by the harness. CRUD audit
    # remains real and is queried from a new SQL session below.
    monkeypatch.setattr(action_confirmations, 'emit_audit_log', lambda **_kwargs: None)
    yield
    await engine.dispose()


async def make_user(role=UserRole.ADMIN, *, active=True):
    user = User(username='synthetic-' + uuid.uuid4().hex, full_name='Synthetic Admin',
                password_hash='unused', role=role, is_active=active)
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
    return user


async def call(channel, action, user, *, data=None, schedule_id=None, confirmed=True):
    data = dict(data or {})
    if channel == 'alfred':
        context = set_chat_tool_context({'user_id': str(user.id), 'user_role': user.role.value})
        try:
            if schedule_id:
                data['schedule_id'] = str(schedule_id)
            data['confirm'] = confirmed
            return await getattr(alfred, action + '_schedule')(data)
        finally:
            set_chat_tool_context({}, token=context)

    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1/schedules')
    app.dependency_overrides[current_user] = lambda: user
    if confirmed:
        payload = {} if action == 'delete' else {**{'time_blocks': {}}, **data}
        payload = {k: v for k, v in payload.items() if v is not None}
        if schedule_id:
            payload['schedule_id'] = str(schedule_id)
        async with AsyncSessionLocal() as session:
            confirmation = await action_confirmations.create_action_confirmation(
                session, user=user, action='schedule.' + action, payload=payload,
            )
        data['confirmation_token'] = confirmation['confirmation_token']
    url = '/api/v1/schedules' + (f'/{schedule_id}' if schedule_id else '')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        response = await client.request({'create': 'POST', 'update': 'PATCH', 'delete': 'DELETE'}[action], url, json=data)
    if response.status_code >= 400:
        return {'error': response.json()['detail'], 'status': response.status_code}
    return {action + 'd' if action != 'update' else 'updated': True,
            'schedule': response.json() if response.content else None}


async def audit_rows(user_id):
    async with AsyncSessionLocal() as session:
        return (await session.scalars(select(AuditLog).where(
            AuditLog.actor_user_id == user_id, AuditLog.action.like('schedule.%')
        ).order_by(AuditLog.timestamp, AuditLog.id))).all()


async def schedule_row(schedule_id):
    async with AsyncSessionLocal() as session:
        return await session.get(Schedule, uuid.UUID(str(schedule_id)))


async def schedule_count(name):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(Schedule).where(Schedule.name == name))


async def test_api_and_alfred_commit_equivalent_schedule_fields_and_audit():
    user = await make_user()
    name = 'Parity-' + uuid.uuid4().hex
    evidence = []
    for channel in ('api', 'alfred'):
        result = await call(channel, 'create', user, data={'name': '  ' + name + '  ', 'description': '  note  ', 'time_blocks': BLOCKS})
        assert result['created'] is True, result
        schedule_id = result['schedule']['id']
        row = await schedule_row(schedule_id)
        assert (row.name, row.description, row.time_blocks) == (name, 'note', normalize_time_blocks(BLOCKS))
        result = await call(channel, 'update', user, schedule_id=schedule_id,
                            data={'name': name, 'description': ' ', 'time_blocks': BLOCKS})
        assert result['updated'] is True, result
        assert (await schedule_row(schedule_id)).description is None
        result = await call(channel, 'delete', user, schedule_id=schedule_id)
        assert result['deleted'] is True, result
        assert await schedule_row(schedule_id) is None
        rows = [r for r in await audit_rows(user.id) if r.target_id == schedule_id]
        assert [r.action for r in rows] == ['schedule.create', 'schedule.update', 'schedule.delete']
        for record in rows:
            assert record.actor == actor_from_user(user)
            assert record.outcome == 'success'
            assert record.metadata_ == {'source': channel}
            assert record.target_entity == 'Schedule'
            assert record.target_label == name
        # Identity is intentionally generated per create; all business changes match.
        evidence.append([{side: {k: v for k, v in r.diff[side].items() if k != 'id'}
                          for side in ('old', 'new')} for r in rows])
    assert evidence[0] == evidence[1]


async def test_alfred_description_only_update_preserves_blocks_and_omitted_name():
    user = await make_user()
    created = await call('alfred', 'create', user, data={'name': 'Partial-' + uuid.uuid4().hex, 'time_blocks': BLOCKS})
    original = created['schedule']
    changed = await call('alfred', 'update', user, schedule_id=original['id'], data={'description': 'Ordinary maintenance note'})
    assert changed['updated'] is True
    row = await schedule_row(original['id'])
    assert row.name == original['name']
    assert row.time_blocks == original['time_blocks']
    assert row.description == 'Ordinary maintenance note'
    assert (await audit_rows(user.id))[-1].diff == {'old': {'description': None}, 'new': {'description': row.description}}


@pytest.mark.parametrize('channel', ['api', 'alfred'])
async def test_no_confirmation_creates_no_schedule_or_crud_audit(channel):
    user = await make_user()
    name = 'Unconfirmed-' + uuid.uuid4().hex
    result = await call(channel, 'create', user, data={'name': name, 'time_blocks': BLOCKS}, confirmed=False)
    assert result.get('requires_confirmation') or result.get('status') == 428
    assert await schedule_count(name) == 0
    assert await audit_rows(user.id) == []


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('name', ['   ', 'x' * 121])
async def test_invalid_names_fail_without_persistence(channel, name):
    user = await make_user()
    result = await call(channel, 'create', user, data={'name': name, 'time_blocks': BLOCKS})
    assert result.get('error')
    assert await schedule_count(name.strip()) == 0
    assert await audit_rows(user.id) == []


@pytest.mark.parametrize('channel', ['api', 'alfred'])
async def test_duplicate_create_and_rename_preserve_existing_records_and_audit(channel):
    user = await make_user()
    name = 'Duplicate-' + uuid.uuid4().hex
    data = {'name': name, 'time_blocks': BLOCKS}
    created = await call(channel, 'create', user, data=data)
    duplicate = await call(channel, 'create', user, data=data)
    assert 'already exists' in duplicate['error']
    another = await call(channel, 'create', user, data={**data, 'name': name + '-other'})
    conflict = await call(channel, 'update', user, schedule_id=another['schedule']['id'], data=data)
    assert 'already exists' in conflict['error']
    assert (await schedule_row(another['schedule']['id'])).name == name + '-other'
    assert await schedule_count(name) == 1
    assert [r.action for r in await audit_rows(user.id)] == ['schedule.create', 'schedule.create']
    assert await schedule_row(created['schedule']['id']) is not None


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('action', ['update', 'delete'])
async def test_missing_schedule_returns_not_found_without_audit(channel, action):
    user = await make_user()
    result = await call(channel, action, user, schedule_id=uuid.uuid4(), data={'name': 'Missing', 'time_blocks': BLOCKS} if action == 'update' else {})
    assert 'not found' in result['error']
    assert await audit_rows(user.id) == []


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('kind', ['person', 'vehicle', 'door'])
async def test_assigned_schedule_cannot_be_deleted(channel, kind):
    user = await make_user()
    result = await call(channel, 'create', user, data={'name': 'In-use-' + uuid.uuid4().hex, 'time_blocks': BLOCKS})
    schedule_id = uuid.UUID(result['schedule']['id'])
    async with AsyncSessionLocal() as session:
        if kind == 'person':
            dependent = Person(first_name='Synthetic', last_name='Dependency', display_name='Synthetic Dependency', schedule_id=schedule_id)
        elif kind == 'vehicle':
            dependent = Vehicle(registration_number=uuid.uuid4().hex[:20], schedule_id=schedule_id)
        else:
            dependent = AccessDevice(key='synthetic-' + uuid.uuid4().hex, kind='gate', name='Synthetic door', schedule_id=schedule_id)
        session.add(dependent)
        await session.commit()
    denied = await call(channel, 'delete', user, schedule_id=schedule_id)
    assert denied.get('error')
    assert await schedule_row(schedule_id) is not None
    assert len(await audit_rows(user.id)) == 1


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('action', ['create', 'update', 'delete'])
async def test_audit_failure_rolls_back_mutation(channel, action, monkeypatch):
    user = await make_user()
    name = 'Rollback-' + uuid.uuid4().hex
    schedule_id = None
    if action != 'create':
        original = await call(channel, 'create', user, data={'name': name, 'time_blocks': BLOCKS})
        schedule_id = original['schedule']['id']
    before_audits = len(await audit_rows(user.id))

    async def failed_audit(*_args, **_kwargs):
        raise RuntimeError('Synthetic durable audit failure')

    monkeypatch.setattr(operations, 'write_audit_log', failed_audit)
    with pytest.raises(RuntimeError, match='Synthetic durable audit failure'):
        await call(channel, action, user, schedule_id=schedule_id,
                   data={'name': name + '-new', 'time_blocks': BLOCKS} if action != 'delete' else {})
    assert len(await audit_rows(user.id)) == before_audits
    assert await schedule_count(name + '-new') == 0
    if schedule_id:
        assert (await schedule_row(schedule_id)).name == name


@pytest.mark.parametrize('channel', ['api', 'alfred'])
async def test_standard_user_cannot_mutate_even_with_confirmation(channel):
    user = await make_user(UserRole.STANDARD)
    name = 'Denied-' + uuid.uuid4().hex
    result = await call(channel, 'create', user, data={'name': name, 'time_blocks': BLOCKS})
    assert 'Admin access' in result['error']
    assert await schedule_count(name) == 0
    assert await audit_rows(user.id) == []


async def test_concurrent_partial_updates_merge_against_current_locked_state():
    user = await make_user()
    original = await call('alfred', 'create', user, data={'name': 'Concurrent-' + uuid.uuid4().hex, 'time_blocks': BLOCKS})
    schedule_id = uuid.UUID(original['schedule']['id'])

    async def update(changes):
        async with AsyncSessionLocal() as session:
            await operations.update_schedule(session, schedule_id, changes, user=user, source='alfred')

    await asyncio.wait_for(asyncio.gather(update({'name': original['schedule']['name'] + '-new'}),
                                          update({'description': 'Concurrent description'})), timeout=10)
    row = await schedule_row(schedule_id)
    assert row.name == original['schedule']['name'] + '-new'
    assert row.description == 'Concurrent description'
    assert len(await audit_rows(user.id)) == 3


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('action', ['update', 'delete'])
async def test_confirmation_is_required_before_updating_or_deleting(channel, action):
    user = await make_user()
    name = 'Preview-' + uuid.uuid4().hex
    original = await call(channel, 'create', user, data={'name': name, 'time_blocks': BLOCKS})
    result = await call(channel, action, user, schedule_id=original['schedule']['id'], confirmed=False,
                        data={'name': name + '-edited', 'time_blocks': BLOCKS} if action == 'update' else {})
    assert result.get('requires_confirmation') or result.get('status') == 428
    assert (await schedule_row(original['schedule']['id'])).name == name
    assert len(await audit_rows(user.id)) == 1


@pytest.mark.parametrize('channel', ['api', 'alfred'])
async def test_invalid_time_blocks_do_not_create_a_schedule(channel):
    user = await make_user()
    name = 'Invalid-time-' + uuid.uuid4().hex
    result = await call(channel, 'create', user, data={
        'name': name, 'time_blocks': {'0': [{'start': '08:15', 'end': '10:00'}]},
    })
    assert result.get('error')
    assert await schedule_count(name) == 0
    assert await audit_rows(user.id) == []


async def test_api_empty_schedule_and_alfred_clarification_are_explicit_channel_policies():
    user = await make_user()
    name = 'Empty-' + uuid.uuid4().hex
    preview = await call('alfred', 'create', user, data={'name': name, 'time_blocks': {}})
    assert preview['requires_details'] is True
    assert await audit_rows(user.id) == []
    created = await call('api', 'create', user, data={'name': name, 'time_blocks': {}})
    assert created['created'] is True
    assert (await schedule_row(created['schedule']['id'])).time_blocks == normalize_time_blocks({})


async def make_target(kind):
    async with AsyncSessionLocal() as session:
        if kind == 'person':
            target = Person(first_name='Synthetic', last_name='Target', display_name='Synthetic Target')
        elif kind == 'vehicle':
            target = Vehicle(registration_number=uuid.uuid4().hex[:20])
        else:
            target = AccessDevice(key='assignment-' + uuid.uuid4().hex, name='Synthetic Device', kind=kind)
        session.add(target)
        await session.commit()
    return target


async def call_assignment(channel, user, target, schedule_id, *, confirmed=True):
    from app.api.v1 import access_devices as devices_api, directory
    kind = 'person' if isinstance(target, Person) else 'vehicle' if isinstance(target, Vehicle) else target.kind
    if channel == 'alfred':
        context = set_chat_tool_context({'user_id': str(user.id), 'user_role': user.role.value})
        try:
            return await alfred.assign_schedule_to_entity({
                'entity_type': kind, 'entity_id': target.key if isinstance(target, AccessDevice) else str(target.id),
                'schedule_id': str(schedule_id) if schedule_id else None,
                'clear_schedule': schedule_id is None, 'confirm': confirmed,
            })
        finally:
            set_chat_tool_context({}, token=context)
    app = FastAPI()
    app.include_router(directory.router, prefix='/api/v1')
    app.include_router(devices_api.router, prefix='/api/v1/access-devices')
    app.dependency_overrides[current_user] = lambda: user
    entity_name = 'person' if kind == 'person' else 'vehicle' if kind == 'vehicle' else 'access_device'
    identifier = target.key if isinstance(target, AccessDevice) else str(target.id)
    id_field = 'device_id' if isinstance(target, AccessDevice) else entity_name + '_id'
    path = 'people' if kind == 'person' else 'vehicles' if kind == 'vehicle' else 'access-devices'
    data = {'schedule_id': str(schedule_id) if schedule_id else ('' if isinstance(target, AccessDevice) else None)}
    if confirmed:
        async with AsyncSessionLocal() as session:
            confirmation = await action_confirmations.create_action_confirmation(
                session, user=user, action=entity_name + '.update', payload={**data, id_field: identifier},
            )
        data['confirmation_token'] = confirmation['confirmation_token']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        response = await client.patch(f'/api/v1/{path}/{identifier}', json=data)
    if response.status_code >= 400:
        return {'error': response.json()['detail'], 'status': response.status_code}
    return {'assigned': True, 'target': response.json()}


@pytest.mark.parametrize('kind', ['person', 'vehicle', 'gate', 'garage_door'])
async def test_assignment_and_clear_share_persistence_and_audit_across_adapters(kind, monkeypatch):
    from app.services import access_devices

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError('Schedule assignment must never construct a hardware provider')

    monkeypatch.setattr(access_devices, 'get_access_device_provider', forbidden_provider)
    user = await make_user()
    created = await call('api', 'create', user, data={'name': 'Assign-' + uuid.uuid4().hex, 'time_blocks': BLOCKS})
    schedule_id = uuid.UUID(created['schedule']['id'])
    snapshots = []
    for channel in ('api', 'alfred'):
        target = await make_target(kind)
        assigned = await call_assignment(channel, user, target, schedule_id)
        assert assigned.get('assigned'), assigned
        async with AsyncSessionLocal() as session:
            assert (await session.get(type(target), target.id)).schedule_id == schedule_id
        if kind in ('gate', 'garage_door'):
            listed = await alfred.query_schedule_targets({'entity_type': kind, 'search': target.key})
            assert listed['doors'][0]['schedule_id'] == str(schedule_id)
        cleared = await call_assignment(channel, user, target, None)
        assert cleared.get('assigned'), cleared
        async with AsyncSessionLocal() as session:
            assert (await session.get(type(target), target.id)).schedule_id is None
        rows = [r for r in await audit_rows(user.id) if r.target_id == str(target.id)]
        assert [r.action for r in rows] == ['schedule.assign', 'schedule.assign']
        assert all(r.actor == actor_from_user(user) and r.metadata_ == {'source': channel} for r in rows)
        snapshots.append([r.diff for r in rows])
    assert snapshots[0] == snapshots[1]


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('kind', ['person', 'vehicle', 'gate'])
async def test_unconfirmed_assignment_has_no_persistent_effect(channel, kind):
    user = await make_user()
    created = await call('api', 'create', user, data={'name': 'No-assign-' + uuid.uuid4().hex, 'time_blocks': BLOCKS})
    target = await make_target(kind)
    result = await call_assignment(channel, user, target, created['schedule']['id'], confirmed=False)
    assert result.get('requires_confirmation') or result.get('status') == 428
    async with AsyncSessionLocal() as session:
        assert (await session.get(type(target), target.id)).schedule_id is None
    assert not [r for r in await audit_rows(user.id) if r.action == 'schedule.assign']


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('kind', ['person', 'vehicle', 'gate'])
async def test_assignment_audit_failure_rolls_back_the_target(channel, kind, monkeypatch):
    from app.services import schedule_assignments
    user = await make_user()
    created = await call('api', 'create', user, data={'name': 'Assign-rollback-' + uuid.uuid4().hex, 'time_blocks': BLOCKS})
    target = await make_target(kind)

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError('Synthetic assignment audit failure')

    monkeypatch.setattr(schedule_assignments, 'write_audit_log', fail_audit)
    with pytest.raises(RuntimeError, match='Synthetic assignment audit failure'):
        await call_assignment(channel, user, target, created['schedule']['id'])
    async with AsyncSessionLocal() as session:
        assert (await session.get(type(target), target.id)).schedule_id is None
    assert not [r for r in await audit_rows(user.id) if r.action == 'schedule.assign']


@pytest.mark.parametrize('channel', ['api', 'alfred'])
@pytest.mark.parametrize('kind', ['person', 'vehicle', 'gate'])
async def test_missing_assignment_reference_cannot_be_saved(channel, kind):
    user = await make_user()
    target = await make_target(kind)
    result = await call_assignment(channel, user, target, uuid.uuid4())
    assert result.get('error')
    assert await audit_rows(user.id) == []
    async with AsyncSessionLocal() as session:
        assert (await session.get(type(target), target.id)).schedule_id is None


async def test_cleared_vehicle_assignment_inherits_owner_schedule():
    from datetime import UTC, datetime
    from sqlalchemy.orm import selectinload
    from app.services.schedules import evaluate_vehicle_schedule

    user = await make_user()
    created = await call('api', 'create', user, data={'name': 'Inherited-' + uuid.uuid4().hex, 'time_blocks': BLOCKS})
    schedule_id = uuid.UUID(created['schedule']['id'])
    owner = await make_target('person')
    vehicle = await make_target('vehicle')
    await call_assignment('api', user, owner, schedule_id)
    async with AsyncSessionLocal() as session:
        row = await session.get(Vehicle, vehicle.id)
        row.person_id = owner.id
        await session.commit()
    await call_assignment('alfred', user, vehicle, schedule_id)
    cleared = await call_assignment('alfred', user, vehicle, None)
    assert cleared['vehicle']['inherits_from_owner'] is True
    assert cleared['vehicle']['owner_schedule_id'] == str(schedule_id)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(Vehicle).options(selectinload(Vehicle.schedule),
            selectinload(Vehicle.owner).selectinload(Person.schedule)).where(Vehicle.id == vehicle.id))
        result = await evaluate_vehicle_schedule(session, row, datetime(2026, 1, 5, 9, tzinfo=UTC),
                                                 timezone_name='Europe/London', default_policy='deny')
        assert result.allowed is True
        assert result.source == 'person'
        assert result.schedule_id == schedule_id


async def test_invalid_device_schedule_id_is_rejected_instead_of_cleared():
    user = await make_user()
    target = await make_target('gate')
    result = await call_assignment('api', user, target, 'not-a-uuid')
    assert result.get('status') == 400
    assert 'Invalid schedule ID' in result['error']
    assert await audit_rows(user.id) == []


async def call_override(user, person, *, confirmed=True):
    token = set_chat_tool_context({'user_id': str(user.id), 'user_role': user.role.value})
    try:
        return await alfred.override_schedule({
            'person_id': str(person.id), 'time': '2026-07-06T09:00:00+01:00',
            'duration_minutes': 60, 'reason': 'Synthetic allowance', 'confirm': confirmed,
        })
    finally:
        set_chat_tool_context({}, token=token)


async def overrides_for(person_id):
    from app.models import ScheduleOverride
    async with AsyncSessionLocal() as session:
        return (await session.scalars(select(ScheduleOverride).where(ScheduleOverride.person_id == person_id))).all()


async def test_override_preview_has_no_durable_side_effects():
    user = await make_user()
    person = await make_target('person')
    result = await call_override(user, person, confirmed=False)
    assert result['requires_confirmation'] is True
    assert await overrides_for(person.id) == []
    assert await audit_rows(user.id) == []


async def test_confirmed_override_commits_with_audit_and_preserves_display(monkeypatch):
    from datetime import UTC, datetime, timedelta
    from app.services import schedule_overrides
    events = []

    async def record_event(name, payload):
        events.append((name, payload))

    monkeypatch.setattr(schedule_overrides.event_bus, 'publish', record_event)
    user = await make_user()
    person = await make_target('person')
    result = await call_override(user, person)
    assert result['created'] is True
    assert result['starts_at'] == '2026-07-06T09:00:00+01:00'
    rows = await overrides_for(person.id)
    assert len(rows) == 1
    assert rows[0].starts_at == datetime(2026, 7, 6, 8, tzinfo=UTC)
    assert rows[0].ends_at - rows[0].starts_at == timedelta(minutes=60)
    assert rows[0].created_by_user_id == user.id
    audit = await audit_rows(user.id)
    assert len(audit) == 1
    assert audit[0].action == 'schedule.override.create'
    assert audit[0].target_id == str(rows[0].id)
    assert audit[0].actor == actor_from_user(user)
    assert audit[0].diff['new']['reason'] == 'Synthetic allowance'
    assert events[0][0] == 'schedule.override_created'
    assert events[0][1]['override_id'] == str(rows[0].id)


async def test_override_audit_failure_rolls_back_override(monkeypatch):
    from app.services import schedule_overrides
    user = await make_user()
    person = await make_target('person')

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError('Synthetic override audit failure')

    monkeypatch.setattr(schedule_overrides, 'write_audit_log', fail_audit)
    with pytest.raises(RuntimeError, match='Synthetic override audit failure'):
        await call_override(user, person)
    assert await overrides_for(person.id) == []
    assert await audit_rows(user.id) == []


async def test_committed_override_stays_successful_when_realtime_delivery_fails(monkeypatch):
    from app.services import schedule_overrides
    user = await make_user()
    person = await make_target('person')
    attempts = []

    async def fail_event(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError('Synthetic event delivery failure')

    monkeypatch.setattr(schedule_overrides.event_bus, 'publish', fail_event)
    result = await call_override(user, person)
    assert result['created'] is True
    assert len(await overrides_for(person.id)) == 1
    assert len(await audit_rows(user.id)) == 1
    assert attempts == [True]


async def test_standard_user_cannot_create_an_override():
    user = await make_user(UserRole.STANDARD)
    person = await make_target('person')
    result = await call_override(user, person)
    assert result['created'] is False
    assert 'Admin access' in result['error']
    assert await overrides_for(person.id) == []
    assert await audit_rows(user.id) == []


@pytest.mark.parametrize('minutes,aware', [(0, True), (1441, True), (60, False)])
async def test_override_operation_rejects_invalid_duration_or_naive_datetime(minutes, aware):
    from datetime import UTC, datetime
    from app.services.schedule_overrides import create_schedule_override
    user = await make_user()
    person = await make_target('person')
    async with AsyncSessionLocal() as session:
        with pytest.raises(operations.ScheduleOperationError) as caught:
            await create_schedule_override(session, person_id=person.id,
                starts_at=datetime(2026, 1, 1, tzinfo=UTC if aware else None), duration_minutes=minutes,
                reason='Synthetic', user=user, source='alfred')
    assert caught.value.code == 'invalid_override'
    assert await overrides_for(person.id) == []
    assert await audit_rows(user.id) == []
