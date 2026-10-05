"""Durable history and exact alert-group resolution in the isolated validation database."""

import os
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

assert {path.name for path in Path('/sys/class/net').iterdir()} == {'lo'}
assert '@127.0.0.1:5432/iacs_validation_' in os.environ.get('IACS_DATABASE_URL', '')

from app.api.dependencies import admin_user
from app.api.v1 import access, action_confirmations, events
from app.api.v1.history import range_boundary, read_cursor, site_zone
from app.api.v1.events import AlertActionRequest, AlertGroupConfirmationRequest
from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, Anomaly, AuditLog, MovementSagaRecord, User
from app.models.enums import (AccessDecision, AccessDirection, AnomalySeverity, AnomalyType,
                             MovementSagaState, TimingClassification, UserRole)
from app.services import action_confirmations as confirmation_owner

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _isolated_owner(monkeypatch):
    monkeypatch.setattr(events, 'get_runtime_config', _site_config)
    monkeypatch.setattr(confirmation_owner, 'emit_audit_log', lambda **_kwargs: None)
    yield
    await engine.dispose()


async def _site_config():
    return SimpleNamespace(site_timezone='Europe/London')


async def _user(role=UserRole.ADMIN) -> User:
    actor = User(username='synthetic-history-' + uuid4().hex, full_name='History Reviewer',
                 password_hash='unused', role=role, is_active=True)
    async with AsyncSessionLocal() as session:
        session.add(actor)
        await session.commit()
    return actor


async def _alert(plate: str, stamp: datetime) -> Anomaly:
    row = Anomaly(anomaly_type=AnomalyType.UNAUTHORIZED_PLATE, severity=AnomalySeverity.CRITICAL,
                  message='Synthetic denied plate', context={'registration_number': plate}, created_at=stamp)
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()
    return row


async def _page(actor, cursor=None, *, limit=50, q=None):
    async with AsyncSessionLocal() as session:
        return await events.alerts_history(
            status_filter='open', severity=None, type_filter=None, q=q,
            from_=None, to=None, limit=limit, cursor=cursor, _=actor, session=session,
        )


async def test_group_over_200_freezes_exact_members_and_later_arrivals() -> None:
    actor = await _user()
    plate = 'H' + uuid4().hex[:9].upper()
    now = datetime.now(UTC)
    stamp = now - timedelta(seconds=15)
    async with AsyncSessionLocal() as session:
        session.add_all([
            Anomaly(anomaly_type=AnomalyType.UNAUTHORIZED_PLATE, severity=AnomalySeverity.CRITICAL,
                    message='Synthetic denied plate', context={'registration_number': plate}, created_at=stamp)
            for _ in range(230)
        ])
        await session.commit()
    page = await _page(actor, q=plate)
    assert len(page.items) == 1
    group = page.items[0]
    assert group['count'] == 230 and group['severity'] == 'warning'
    async with AsyncSessionLocal() as session:
        prepared = await events.confirm_alert_group(
            AlertGroupConfirmationRequest(group_id=group['id'], as_of=page.as_of,
                                          member_hash=group['member_hash'], count=230, note='Reviewed'),
            actor, session,
        )
    late = await _alert(plate, datetime.now(UTC))
    async with AsyncSessionLocal() as session:
        result = await events.action_alerts(
            AlertActionRequest(group_id=group['id'], confirmation_token=prepared['confirmation_token'],
                               action='resolve', note='Reviewed'), actor, session,
        )
    assert result['updated'] == 230
    async with AsyncSessionLocal() as session:
        assert (await session.get(Anomaly, late.id)).resolved_at is None
        assert len((await session.scalars(select(Anomaly).where(Anomaly.id.in_(result['alert_ids'])))).all()) == 230
        assert await session.scalar(select(AuditLog).where(AuditLog.action == 'alert.resolve',
                                                         AuditLog.actor_user_id == actor.id)) is not None
        with pytest.raises(HTTPException) as replay:
            await events.action_alerts(
                AlertActionRequest(group_id=group['id'], confirmation_token=prepared['confirmation_token'],
                                   action='resolve', note='Reviewed'), actor, session,
            )
        assert replay.value.status_code == 409


async def test_group_prepare_rejects_changed_membership_and_generic_forgery() -> None:
    actor = await _user()
    plate = 'H' + uuid4().hex[:9].upper()
    await _alert(plate, datetime.now(UTC) - timedelta(seconds=5))
    page = await _page(actor, q=plate)
    group = page.items[0]
    await _alert(plate, page.as_of - timedelta(microseconds=1))
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as stale:
            await events.confirm_alert_group(
                AlertGroupConfirmationRequest(group_id=group['id'], as_of=page.as_of,
                                              member_hash=group['member_hash'], count=1), actor, session,
            )
        assert stale.value.status_code == 409
    app = FastAPI()
    app.include_router(action_confirmations.router, prefix='/api/v1/action-confirmations')
    app.dependency_overrides[admin_user] = lambda: actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        response = await client.post('/api/v1/action-confirmations', json={
            'action': ' alert.group.resolve ', 'payload': {'group_id': group['id'], 'action': 'resolve'},
            'metadata': {'alert_ids': group['alert_ids']},
        })
    assert response.status_code == 403


async def test_group_audit_failure_rolls_back_all_members_and_confirmation(monkeypatch) -> None:
    actor = await _user()
    plate = 'H' + uuid4().hex[:9].upper()
    await _alert(plate, datetime.now(UTC) - timedelta(seconds=5))
    page = await _page(actor, q=plate)
    group = page.items[0]
    async with AsyncSessionLocal() as session:
        prepared = await events.confirm_alert_group(
            AlertGroupConfirmationRequest(group_id=group['id'], as_of=page.as_of,
                                          member_hash=group['member_hash'], count=1), actor, session,
        )

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError('synthetic audit failure')

    monkeypatch.setattr(events, 'write_audit_log', fail_audit)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError, match='synthetic audit failure'):
            await events.action_alerts(
                AlertActionRequest(group_id=group['id'], confirmation_token=prepared['confirmation_token'],
                                   action='resolve'), actor, session,
            )
        await session.rollback()
    async with AsyncSessionLocal() as session:
        assert (await session.get(Anomaly, group['alert_ids'][0])).resolved_at is None
        confirmation = await confirmation_owner.find_action_confirmation(
            session, confirmation_owner.confirmation_token_hash(prepared['confirmation_token']))
        assert confirmation is not None and confirmation.consumed_at is None


async def test_event_and_movement_history_keyset_ties_and_new_arrival_cutoff() -> None:
    actor = await _user()
    plate = 'H' + uuid4().hex[:9].upper()
    stamp = datetime.now(UTC) - timedelta(days=1)
    event_ids = []
    movement_ids = []
    async with AsyncSessionLocal() as session:
        for index in range(260):
            event = AccessEvent(registration_number=plate, direction=AccessDirection.ENTRY,
                                decision=AccessDecision.GRANTED, confidence=.9, source='synthetic-history',
                                occurred_at=stamp, timing_classification=TimingClassification.NORMAL,
                                created_at=stamp)
            movement = MovementSagaRecord(idempotency_key=f'synthetic-history-{uuid4().hex}',
                                          registration_number=plate, source='synthetic-history',
                                          state=MovementSagaState.COMPLETED, occurred_at=stamp,
                                          gate_command_required=False, presence_committed=True,
                                          reconciliation_required=False, created_at=stamp)
            session.add_all([event, movement])
            event_ids.append(event)
            movement_ids.append(movement)
        await session.commit()
    expected_events = {str(row.id) for row in event_ids}
    expected_movements = {str(row.id) for row in movement_ids}

    async def fetch_events(cursor):
        async with AsyncSessionLocal() as session:
            return await events.events_history(limit=50, cursor=cursor, q=plate, from_=None, to=None,
                                                direction=None, decision=None, _=actor, session=session)

    async def fetch_movements(cursor):
        async with AsyncSessionLocal() as session:
            return await access.movements_history(_=actor, session=session, limit=50, cursor=cursor,
                                                  q=plate, from_=None, to=None, state=None,
                                                  reconciliation_required=None, category=None)

    first_events = await fetch_events(None)
    first_movements = await fetch_movements(None)
    async with AsyncSessionLocal() as session:
        session.add_all([
            AccessEvent(registration_number=plate, direction=AccessDirection.ENTRY,
                        decision=AccessDecision.GRANTED, confidence=.9, source='synthetic-history',
                        occurred_at=stamp, timing_classification=TimingClassification.NORMAL),
            MovementSagaRecord(idempotency_key=f'synthetic-history-{uuid4().hex}',
                               registration_number=plate, source='synthetic-history',
                               state=MovementSagaState.COMPLETED, occurred_at=stamp,
                               gate_command_required=False, presence_committed=True,
                               reconciliation_required=False),
        ])
        await session.commit()
    for first, fetch, expected in ((first_events, fetch_events, expected_events),
                                   (first_movements, fetch_movements, expected_movements)):
        seen = [item['id'] for item in first.items]
        page = first
        while page.next_cursor:
            page = await fetch(page.next_cursor)
            seen.extend(item['id'] for item in page.items)
        assert len(seen) == 260 and len(set(seen)) == 260
        assert set(seen) == expected
        assert seen == sorted(seen, reverse=True)  # every occurrence has the same timestamp


async def test_alert_history_display_severity_and_older_member_search() -> None:
    actor = await _user()
    plate = 'H' + uuid4().hex[:9].upper()
    old = datetime.now(UTC) - timedelta(minutes=2)
    async with AsyncSessionLocal() as session:
        session.add_all([
            Anomaly(anomaly_type=AnomalyType.UNAUTHORIZED_PLATE, severity=AnomalySeverity.CRITICAL,
                    message='Unique needle in oldest member', context={'registration_number': plate}, created_at=old),
            Anomaly(anomaly_type=AnomalyType.UNAUTHORIZED_PLATE, severity=AnomalySeverity.CRITICAL,
                    message='Recent member', context={'registration_number': plate}, created_at=old + timedelta(seconds=30)),
            Anomaly(anomaly_type=AnomalyType.DUPLICATE_ENTRY, severity=AnomalySeverity.CRITICAL,
                    message='Standalone severe alert', context={'registration_number': plate}, created_at=old),
        ])
        await session.commit()
    async with AsyncSessionLocal() as session:
        warning = await events.alerts_history(status_filter='open', severity=AnomalySeverity.WARNING,
            type_filter=None, q='Unique needle', from_=None, to=None, limit=50, cursor=None, _=actor, session=session)
        assert len(warning.items) == 1 and warning.items[0]['count'] == 2
        assert warning.items[0]['severity'] == 'warning'
        critical = await events.alerts_history(status_filter='open', severity=AnomalySeverity.CRITICAL,
            type_filter=None, q='Standalone severe', from_=None, to=None, limit=50, cursor=None, _=actor, session=session)
        assert len(critical.items) == 1 and critical.items[0]['severity'] == 'critical'
        displayed = await events.alerts_history(status_filter='open', severity=AnomalySeverity.WARNING,
            type_filter=None, q='Unauthorised Plate, Access Denied', from_=None, to=None,
            limit=250, cursor=None, _=actor, session=session)
        assert any(item['registration_number'] == plate for item in displayed.items)


async def test_group_target_actor_expiry_and_concurrent_single_use() -> None:
    actor = await _user()
    other = await _user()
    plate = 'H' + uuid4().hex[:9].upper()
    await _alert(plate, datetime.now(UTC) - timedelta(seconds=5))
    page = await _page(actor, q=plate)
    group = page.items[0]
    async with AsyncSessionLocal() as session:
        prepared = await events.confirm_alert_group(
            AlertGroupConfirmationRequest(group_id=group['id'], as_of=page.as_of,
                                          member_hash=group['member_hash'], count=1), actor, session,
        )
    request = AlertActionRequest(group_id=group['id'], confirmation_token=prepared['confirmation_token'], action='resolve')
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as wrong_actor:
            await events.action_alerts(request, other, session)
        assert wrong_actor.value.status_code == 403

    async def resolve_once():
        async with AsyncSessionLocal() as session:
            try:
                return (await events.action_alerts(request, actor, session))['updated']
            except HTTPException as exc:
                return exc.status_code

    results = await asyncio.gather(resolve_once(), resolve_once())
    assert sorted(results) == [1, 409]

    expired_plate = 'H' + uuid4().hex[:9].upper()
    await _alert(expired_plate, datetime.now(UTC) - timedelta(seconds=5))
    expired_page = await _page(actor, q=expired_plate)
    expired_group = expired_page.items[0]
    async with AsyncSessionLocal() as session:
        expired = await events.confirm_alert_group(
            AlertGroupConfirmationRequest(group_id=expired_group['id'], as_of=expired_page.as_of,
                                          member_hash=expired_group['member_hash'], count=1), actor, session,
        )
    async with AsyncSessionLocal() as session:
        confirmation = await confirmation_owner.find_action_confirmation(
            session, confirmation_owner.confirmation_token_hash(expired['confirmation_token']))
        confirmation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as stale:
            await events.action_alerts(
                AlertActionRequest(group_id=expired_group['id'], confirmation_token=expired['confirmation_token'],
                                   action='resolve'), actor, session,
            )
        assert stale.value.status_code == 403
        assert (await session.get(Anomaly, expired_group['alert_ids'][0])).resolved_at is None


async def test_blank_plate_group_is_resolvable() -> None:
    actor = await _user(UserRole.STANDARD)
    row = Anomaly(anomaly_type=AnomalyType.UNAUTHORIZED_PLATE, severity=AnomalySeverity.WARNING,
                  message='Synthetic legacy missing plate', context={}, created_at=datetime.now(UTC) - timedelta(seconds=5))
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()
    async with AsyncSessionLocal() as session:
        page = await events.alerts_history(status_filter='open', severity=None,
            type_filter=AnomalyType.UNAUTHORIZED_PLATE, q='Synthetic legacy missing plate',
            from_=None, to=None, limit=50, cursor=None, _=actor, session=session)
        group = page.items[0]
        assert group['registration_number'] == ''
        prepared = await events.confirm_alert_group(
            AlertGroupConfirmationRequest(group_id=group['id'], as_of=page.as_of,
                                          member_hash=group['member_hash'], count=1), actor, session,
        )
    async with AsyncSessionLocal() as session:
        result = await events.action_alerts(
            AlertActionRequest(group_id=group['id'], confirmation_token=prepared['confirmation_token'],
                               action='resolve'), actor, session,
        )
        assert result['updated'] == 1


async def test_alert_history_pages_over_250_logical_groups_with_timestamp_ties() -> None:
    actor = await _user()
    marker = 'H' + uuid4().hex[:6].upper()
    stamp = datetime.now(UTC) - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        session.add_all([
            Anomaly(anomaly_type=AnomalyType.UNAUTHORIZED_PLATE, severity=AnomalySeverity.WARNING,
                    message='Synthetic group history', context={'registration_number': f'{marker}{index:03d}'},
                    created_at=stamp)
            for index in range(260)
        ])
        await session.commit()
    seen = []
    cursor = None
    while True:
        page = await _page(actor, cursor=cursor, limit=50, q=marker)
        seen.extend(item['id'] for item in page.items)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert len(seen) == len(set(seen)) == 260


async def test_local_date_range_is_inclusive_start_exclusive_end_across_dst() -> None:
    actor = await _user()
    plate = 'H' + uuid4().hex[:9].upper()
    # Europe/London spring day is 23 hours: 00:00 UTC inclusive to 23:00 UTC exclusive.
    await _alert(plate, datetime(2026, 3, 29, 0, 0, tzinfo=UTC))
    await _alert(plate, datetime(2026, 3, 29, 22, 59, tzinfo=UTC))
    await _alert(plate, datetime(2026, 3, 29, 23, 0, tzinfo=UTC))
    async with AsyncSessionLocal() as session:
        page = await events.alerts_history(status_filter='open', severity=None, type_filter=None,
            q=plate, from_='2026-03-29', to='2026-03-30', limit=50, cursor=None, _=actor, session=session)
        assert len(page.items) == 1 and page.items[0]['count'] == 2
        next_day = await events.alerts_history(status_filter='open', severity=None, type_filter=None,
            q=plate, from_='2026-03-30', to='2026-03-31', limit=50, cursor=None, _=actor, session=session)
        assert len(next_day.items) == 1 and next_day.items[0]['count'] == 1


def test_local_history_boundaries_cover_dst_and_cursor_validation() -> None:
    zone = site_zone('Europe/London')
    spring_start = range_boundary('2026-03-29', zone)
    spring_end = range_boundary('2026-03-30', zone)
    autumn_start = range_boundary('2026-10-25', zone)
    autumn_end = range_boundary('2026-10-26', zone)
    assert spring_end - spring_start == timedelta(hours=23)
    assert autumn_end - autumn_start == timedelta(hours=25)
    with pytest.raises(HTTPException) as malformed:
        read_cursor('not-base64', {'q': 'A'})
    assert malformed.value.status_code == 422
    from app.api.v1.history import write_cursor
    bound_cursor = write_cursor(datetime.now(UTC), datetime.now(UTC), uuid4(), {'q': 'A'})
    with pytest.raises(HTTPException) as mismatch:
        read_cursor(bound_cursor, {'q': 'B'})
    assert mismatch.value.status_code == 422
