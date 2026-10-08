"""Required rejection audit after caller rollback on guarded disposable PG."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import json
import uuid
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.api.confirmations import send_confirmed_notification
from app.api.v1.events import AlertActionRequest, action_alerts
from app.db.session import AsyncSessionLocal
from app.models import ActionConfirmation, AuditLog, NotificationRun, User
from app.models.enums import UserRole
from app.modules.notifications.base import NotificationContext
from app.services import action_confirmations as confirmations, notifications, telemetry
from app.services.notifications import NotificationService

pytestmark = pytest.mark.asyncio
ACTION = 'notification.synthetic_rejection'
PAYLOAD = {'message': 'synthetic; never dispatched'}


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, 4)


async def actor():
    async with AsyncSessionLocal() as session:
        user = User(username='confirmation-' + uuid.uuid4().hex, first_name='Before',
                    last_name='Synthetic', full_name='Synthetic Admin', password_hash='inert',
                    role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
        return user


async def saved_token(user, kind, *, action=ACTION, payload=PAYLOAD):
    if kind == 'not_found':
        return 'synthetic-not-found', None
    async with AsyncSessionLocal() as session:
        approval = await confirmations.create_action_confirmation(session, user=user, action=action,
            payload=payload, target_entity='Synthetic', target_id='fixture', target_label='Safe synthetic target')
        identity = uuid.UUID(approval['confirmation_id'])
        if kind == 'consumed':
            row = await session.get(ActionConfirmation, identity)
            row.consumed_at = datetime.now(UTC)
            row.outcome = 'consumed'
            await session.commit()
    return approval['confirmation_token'], identity


async def rejected_rows():
    async with AsyncSessionLocal() as session:
        return list((await session.scalars(select(AuditLog).where(
            AuditLog.action == 'real_world_action.confirmation.rejected'))).all())


async def assert_original(user_id, confirmation_id, kind):
    async with AsyncSessionLocal() as session:
        assert (await session.get(User, user_id)).first_name == 'Before'
        if confirmation_id:
            row = await session.get(ActionConfirmation, confirmation_id)
            assert row.outcome == ('consumed' if kind == 'consumed' else None)
            assert bool(row.consumed_at) is (kind == 'consumed')


@pytest.mark.parametrize('kind', ['not_found', 'consumed', 'invalid'])
async def test_participant_rejection_is_scalar_then_audited_after_actor_lock_rollback(kind):
    user = await actor()
    token, identity = await saved_token(user, kind)
    payload = {'message': 'mismatch'} if kind == 'invalid' else PAYLOAD
    async with AsyncSessionLocal() as session:
        locked = await session.scalar(select(User).where(User.id == user.id).with_for_update())
        locked.first_name = 'Must roll back'
        await session.flush()
        with pytest.raises(confirmations.ActionConfirmationError) as caught:
            await bounded(confirmations.consume_action_confirmation(session, user=locked, action=ACTION,
                payload=payload, confirmation_token=token, commit=False))
        assert caught.value.rejection is not None
        assert not await bounded(rejected_rows())
        assert not session.new
        await session.rollback()
        await assert_original(user.id, identity, kind)
        await bounded(confirmations.persist_confirmation_rejection(caught.value.rejection))
    rows = await rejected_rows()
    assert len(rows) == 1 and rows[0].actor_user_id == user.id
    assert rows[0].metadata_['confirmation_id'] == (str(identity) if identity else None)
    assert rows[0].metadata_['payload_hash'] == confirmations.confirmation_payload_hash(payload)
    assert token not in json.dumps(rows[0].metadata_, default=str)
    await assert_original(user.id, identity, kind)
    if kind == 'invalid':
        # The participant's rejected request must not spend an otherwise valid
        # confirmation. The original payload still succeeds after rollback.
        async with AsyncSessionLocal() as retry:
            consumed = await confirmations.consume_action_confirmation(retry, user=user,
                action=ACTION, payload=PAYLOAD, confirmation_token=token)
            assert consumed.outcome == 'consumed'
        assert len(await rejected_rows()) == 1


@pytest.mark.parametrize('kind', ['not_found', 'consumed', 'invalid'])
async def test_standalone_rejection_commits_audit_in_actor_locked_session(kind):
    user = await actor()
    token, identity = await saved_token(user, kind)
    async with AsyncSessionLocal() as session:
        locked = await session.scalar(select(User).where(User.id == user.id).with_for_update())
        with pytest.raises(confirmations.ActionConfirmationError):
            await bounded(confirmations.consume_action_confirmation(session, user=locked, action=ACTION,
                payload={'message': 'mismatch'} if kind == 'invalid' else PAYLOAD,
                confirmation_token=token))
        # Durable audit is visible while this standalone caller session remains open.
        assert len(await bounded(rejected_rows())) == 1
    async with AsyncSessionLocal() as session:
        if identity:
            row = await session.get(ActionConfirmation, identity)
            assert row.outcome == ('rejected' if kind == 'invalid' else 'consumed')
            assert row.consumed_at is not None


@pytest.mark.parametrize('commit', [False, True])
async def test_missing_token_428_never_creates_new_rejection_audit(commit):
    user = await actor()
    async with AsyncSessionLocal() as session:
        with pytest.raises(confirmations.ActionConfirmationError) as caught:
            await confirmations.consume_action_confirmation(session, user=user, action=ACTION,
                payload=PAYLOAD, confirmation_token=None, commit=commit)
        assert caught.value.status_code == 428 and caught.value.rejection is None
        await session.rollback()
        await confirmations.persist_confirmation_rejection(caught.value.rejection)
    assert not await rejected_rows()


@pytest.mark.parametrize('route', ['notification', 'grouped_alert'])
@pytest.mark.parametrize('kind', ['not_found', 'consumed', 'invalid'])
async def test_actual_participant_outer_owner_releases_actor_before_auditing(route, kind, monkeypatch):
    user = await actor()
    action = ACTION if route == 'notification' else 'alert.group.resolve'
    payload = PAYLOAD if route == 'notification' else {'group_id': 'group:synthetic', 'action': 'resolve', 'note': None}
    token, identity = await saved_token(user, kind, action=action,
        payload={'different': True} if kind == 'invalid' else payload)
    service = NotificationService()
    forbidden = AsyncMock(side_effect=AssertionError('Rejected confirmation must never dispatch'))
    monkeypatch.setattr(service, 'dispatch_reserved', forbidden)
    monkeypatch.setattr(notifications, 'get_notification_service', lambda: service)
    async with AsyncSessionLocal() as session:
        locked = await session.scalar(select(User).where(User.id == user.id).with_for_update())
        locked.first_name = 'Must roll back'
        await session.flush()
        if route == 'notification':
            operation = send_confirmed_notification(session, user=locked, action=action, payload=payload,
                confirmation_token=token, context=NotificationContext('integration_test', 'Synthetic', 'info', {}),
                direct_action={'type': 'in_app', 'delivery_mode': 'literal', 'message': 'Synthetic', 'title': 'Synthetic'})
        else:
            operation = action_alerts(AlertActionRequest(group_id='group:synthetic', action='resolve',
                confirmation_token=token), actor=locked, session=session)
        with pytest.raises(HTTPException) as caught:
            await bounded(operation)
        assert caught.value.status_code == (409 if kind == 'consumed' else 403)
        assert len(await bounded(rejected_rows())) == 1
    assert forbidden.await_count == 0
    async with AsyncSessionLocal() as session:
        assert not await session.scalar(select(NotificationRun.id))
    await assert_original(user.id, identity, kind)


@pytest.mark.parametrize('participant', [False, True])
async def test_required_rejection_audit_failure_surfaces_and_does_not_spend_token(participant, monkeypatch):
    user = await actor()
    token, identity = await saved_token(user, 'invalid')
    original = confirmations.write_audit_log

    async def fail_after_flush(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError('synthetic required audit failure')

    monkeypatch.setattr(confirmations, 'write_audit_log', fail_after_flush)
    async with AsyncSessionLocal() as session:
        locked = await session.scalar(select(User).where(User.id == user.id).with_for_update())
        if participant:
            with pytest.raises(confirmations.ActionConfirmationError) as caught:
                await bounded(confirmations.consume_action_confirmation(session, user=locked, action=ACTION,
                    payload={'message': 'mismatch'}, confirmation_token=token, commit=False))
            await session.rollback()
            with pytest.raises(RuntimeError, match='required audit failure'):
                await bounded(confirmations.persist_confirmation_rejection(caught.value.rejection))
        else:
            with pytest.raises(RuntimeError, match='required audit failure'):
                await bounded(confirmations.consume_action_confirmation(session, user=locked, action=ACTION,
                    payload={'message': 'mismatch'}, confirmation_token=token))
            await session.rollback()
    assert not await rejected_rows()
    await assert_original(user.id, identity, 'invalid')


async def test_rejection_freezes_original_scalar_correlation_without_token_or_orm():
    user = await actor()
    trace = telemetry.CURRENT_TRACE_ID.set('original-synthetic-trace')
    request = telemetry.CURRENT_REQUEST_ID.set('original-synthetic-request')
    async with AsyncSessionLocal() as session:
        try:
            with pytest.raises(confirmations.ActionConfirmationError) as caught:
                await confirmations.consume_action_confirmation(session, user=user, action=ACTION,
                    payload=PAYLOAD, confirmation_token='synthetic-private-bearer', commit=False)
            evidence = caught.value.rejection
        finally:
            telemetry.CURRENT_TRACE_ID.reset(trace)
            telemetry.CURRENT_REQUEST_ID.reset(request)
        await session.rollback()
    assert evidence is not None
    assert all(value is None or isinstance(value, str | uuid.UUID) for value in asdict(evidence).values())
    assert 'synthetic-private-bearer' not in json.dumps(asdict(evidence), default=str)
    await bounded(confirmations.persist_confirmation_rejection(evidence))
    row = (await rejected_rows())[0]
    assert (row.trace_id, row.request_id) == ('original-synthetic-trace', 'original-synthetic-request')


@pytest.mark.parametrize('route', ['notification', 'grouped_alert'])
async def test_actual_outer_owner_surfaces_required_audit_failure_without_dispatch(route, monkeypatch):
    user = await actor()
    service = NotificationService()
    forbidden = AsyncMock(side_effect=AssertionError('Rejected confirmation must never dispatch'))
    monkeypatch.setattr(service, 'dispatch_reserved', forbidden)
    monkeypatch.setattr(notifications, 'get_notification_service', lambda: service)
    original = confirmations.write_audit_log

    async def fail_after_flush(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError('synthetic required outer audit failure')

    monkeypatch.setattr(confirmations, 'write_audit_log', fail_after_flush)
    async with AsyncSessionLocal() as session:
        locked = await session.scalar(select(User).where(User.id == user.id).with_for_update())
        locked.first_name = 'Must roll back'
        await session.flush()
        if route == 'notification':
            operation = send_confirmed_notification(session, user=locked, action=ACTION, payload=PAYLOAD,
                confirmation_token='not-found', context=NotificationContext('integration_test', 'Synthetic', 'info', {}),
                direct_action={'type': 'in_app', 'delivery_mode': 'literal', 'message': 'Synthetic', 'title': 'Synthetic'})
        else:
            operation = action_alerts(AlertActionRequest(group_id='group:synthetic', action='resolve',
                confirmation_token='not-found'), actor=locked, session=session)
        with pytest.raises(RuntimeError, match='required outer audit failure'):
            await bounded(operation)
    assert forbidden.await_count == 0 and not await rejected_rows()
    await assert_original(user.id, None, 'not_found')
    async with AsyncSessionLocal() as session:
        assert not await session.scalar(select(NotificationRun.id))
