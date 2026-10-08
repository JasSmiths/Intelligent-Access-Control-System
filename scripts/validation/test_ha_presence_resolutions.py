"""No-send HA acknowledgement in the fail-closed disposable PostgreSQL namespace."""
from test_ha_presence_store import (
    clear_synthetic_ha_state as clear_synthetic_ha_state,
    isolated_resources as isolated_resources,
    prepare, reserve, seed,
)

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal
from app.models import ActionConfirmation, AuditLog, HaPresenceEffect, HaPresenceResolution, HaPresenceTarget, Person, User
from app.models.enums import UserRole
from app.modules.home_assistant.input_boolean_contracts import HaDelivery
from app.services import action_confirmations as confirmations, ha_presence_resolutions as resolutions
from app.services.auth import create_access_token, revoke_access_token
from app.services.ha_presence_attempts import HaPresenceAttemptStore
from app.services.ha_presence_resolution_contracts import (
    HA_ACKNOWLEDGEMENT_ACTION, HaAcknowledgement, HaAdminRequest, HaResolutionError, acknowledgement_payload,
)
from app.services.ha_presence_resolutions import HaPresenceResolutionService

pytestmark = pytest.mark.asyncio


def service(*, local_busy=False, factory=AsyncSessionLocal):
    return HaPresenceResolutionService(factory, owns_local_capacity=lambda: local_busy)


async def admin():
    async with AsyncSessionLocal() as session:
        user = User(username='ha-admin-'+uuid.uuid4().hex, full_name='Synthetic HA Admin',
            password_hash='inert', role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
    token, _ = await create_access_token(user)
    return user, HaAdminRequest(user.id, token)


async def unknown(*, release=True, entity='input_boolean.shared'):
    proposal = await seed(entity=entity)
    await reserve(proposal)
    prepared = await prepare()
    assert prepared is not None
    store = HaPresenceAttemptStore()
    await store.record_receipt(prepared.claim, HaDelivery('unknown', 'provider_unknown'))
    if release:
        assert await store.release_capacity(prepared.claim)
    return proposal, prepared


async def confirmed(user, authority, attempt_id, *, preview=None, token_kind='valid', owner=None):
    owner = owner or service()
    preview = preview or await owner.preview_attempt(attempt_id, authority)
    request = HaAcknowledgement(attempt_id, preview['preview_fingerprint'], True,
        'Operator confirms the former provider request is settled.', uuid.uuid4(), None)
    async with AsyncSessionLocal() as session:
        approval = await confirmations.create_action_confirmation(session, user=user,
            action=HA_ACKNOWLEDGEMENT_ACTION, payload=acknowledgement_payload(request),
            target_entity='ha_presence_effect', target_id=str(attempt_id),
            target_label='Home Assistant presence target')
        identity = uuid.UUID(approval['confirmation_id'])
        if token_kind == 'consumed':
            row = await session.get(ActionConfirmation, identity)
            row.consumed_at, row.outcome = datetime.now(UTC), 'consumed'
            await session.commit()
        elif token_kind == 'invalid':
            row = await session.get(ActionConfirmation, identity)
            row.payload_hash = 'c'*64
            await session.commit()
    token = 'not-found-synthetic-token' if token_kind == 'not_found' else approval['confirmation_token']
    return replace(request, confirmation_id=identity, confirmation_token=token)


async def count_audit(action):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == action))


async def assert_barrier(prepared, *, retained=True):
    async with AsyncSessionLocal() as session:
        effect = await session.get(HaPresenceEffect, prepared.claim.attempt_id)
        target = await session.get(HaPresenceTarget, prepared.claim.target_key)
        assert effect.state == 'unknown' and effect.outcome == 'unknown'
        assert target.barrier_attempt_id == (effect.id if retained else None)


async def test_idle_running_capacity_preview_and_acknowledgement_are_sanitized_and_send_nothing():
    _, prepared = await unknown()
    user, authority = await admin()
    owner = service()
    preview = await owner.preview_attempt(prepared.claim.attempt_id, authority)
    assert not preview['live_owner'] and preview['resolution'] is None
    assert 'synthetic.invalid' not in repr(preview) and 'synthetic-secret' not in repr(preview)
    request = await confirmed(user, authority, prepared.claim.attempt_id, preview=preview)
    receipt = await owner.acknowledge_attempt(request, authority)
    assert receipt['attempt_id'] == str(prepared.claim.attempt_id)
    await assert_barrier(prepared, retained=False)
    assert await count_audit('home_assistant.presence_effect.acknowledged') == 1
    assert await prepare() is None  # The original generation is never replayed.


@pytest.mark.parametrize('retirement', ['deleted', 'inactive', 'binding_removed'])
async def test_unknown_settlement_remains_possible_after_origin_person_retirement(retirement):
    proposal, prepared = await unknown()
    user, authority = await admin()
    async with AsyncSessionLocal() as session:
        if retirement == 'deleted':
            await session.execute(delete(Person).where(Person.id == proposal.person_id))
        else:
            person = await session.get(Person, proposal.person_id)
            if retirement == 'inactive':
                person.is_active = False
            else:
                person.home_assistant_presence_input_boolean_entity_ids = []
        await session.commit()
    owner = service()
    preview = await owner.preview_attempt(prepared.claim.attempt_id, authority)
    assert preview['current_person_exists'] is (retirement != 'deleted')
    request = await confirmed(user, authority, prepared.claim.attempt_id, preview=preview)
    await owner.acknowledge_attempt(request, authority)
    await assert_barrier(prepared, retained=False)


async def test_exact_committed_retry_precedes_new_generation_active_lease_and_old_consumed_token():
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id)
    owner = service()
    receipt = await owner.acknowledge_attempt(request, authority)
    later = await seed(entity='input_boolean.shared')
    await reserve(later)
    active = await prepare()
    assert active is not None and active.claim.generation > prepared.claim.generation
    retried = await owner.acknowledge_attempt(replace(request, confirmation_token=None), authority)
    assert retried == receipt
    assert await count_audit('home_assistant.presence_effect.acknowledged') == 1
    assert await count_audit('real_world_action.confirmation.consumed') == 1
    with pytest.raises(HaResolutionError, match='different acknowledgement'):
        await owner.acknowledge_attempt(replace(request, reason='Different decision'), authority)


@pytest.mark.parametrize('busy', ['durable_lease', 'local_actual_cleanup'])
async def test_live_attempt_or_actual_local_cleanup_blocks_new_acknowledgement(busy):
    _, prepared = await unknown(release=busy != 'durable_lease')
    user, authority = await admin()
    owner = service(local_busy=busy == 'local_actual_cleanup')
    request = await confirmed(user, authority, prepared.claim.attempt_id, owner=owner)
    with pytest.raises(HaResolutionError, match='still owns capacity'):
        await owner.acknowledge_attempt(request, authority)
    await assert_barrier(prepared)
    async with AsyncSessionLocal() as session:
        assert (await session.get(ActionConfirmation, request.confirmation_id)).consumed_at is None
        assert await session.get(HaPresenceResolution, prepared.claim.attempt_id) is None


async def test_stale_preview_binding_drift_does_not_consume_confirmation():
    proposal, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id)
    async with AsyncSessionLocal() as session:
        person = await session.get(Person, proposal.person_id)
        person.home_assistant_presence_input_boolean_entry_action = 'turn_on'
        await session.commit()
    with pytest.raises(HaResolutionError, match='changed'):
        await service().acknowledge_attempt(request, authority)
    await assert_barrier(prepared)
    async with AsyncSessionLocal() as session:
        assert (await session.get(ActionConfirmation, request.confirmation_id)).consumed_at is None


@pytest.mark.parametrize('kind', ['not_found', 'consumed', 'invalid'])
async def test_typed_rejection_audits_after_actual_admin_lock_rollback_without_fk_self_block(kind):
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id, token_kind=kind)
    with pytest.raises(confirmations.ActionConfirmationError):
        await asyncio.wait_for(service().acknowledge_attempt(request, authority), 4)
    await assert_barrier(prepared)
    assert await count_audit('real_world_action.confirmation.rejected') == 1
    async with AsyncSessionLocal() as session:
        row = await session.get(ActionConfirmation, request.confirmation_id)
        assert row.outcome == ('consumed' if kind == 'consumed' else None)
        assert await session.get(HaPresenceResolution, prepared.claim.attempt_id) is None


async def test_missing_token_keeps_428_unaudited_and_confirmation_unconsumed():
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id)
    with pytest.raises(confirmations.ActionConfirmationError) as caught:
        await service().acknowledge_attempt(replace(request, confirmation_token=None), authority)
    assert caught.value.status_code == 428 and caught.value.rejection is None
    assert await count_audit('real_world_action.confirmation.rejected') == 0
    await assert_barrier(prepared)


async def test_resolution_audit_failure_rolls_back_confirmation_barrier_and_resolution(monkeypatch):
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id)
    async def fail(*_args, **_kwargs):
        raise RuntimeError('synthetic required audit unavailable')
    monkeypatch.setattr(resolutions, 'write_audit_log', fail)
    with pytest.raises(RuntimeError, match='required audit unavailable'):
        await service().acknowledge_attempt(request, authority)
    await assert_barrier(prepared)
    async with AsyncSessionLocal() as session:
        assert (await session.get(ActionConfirmation, request.confirmation_id)).consumed_at is None
        assert await session.get(HaPresenceResolution, prepared.claim.attempt_id) is None


async def test_rejection_audit_failure_is_visible_after_safe_outer_rollback(monkeypatch):
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id, token_kind='invalid')
    async def fail(*_args, **_kwargs):
        raise RuntimeError('synthetic rejected audit unavailable')
    monkeypatch.setattr(resolutions, 'persist_confirmation_rejection', fail)
    with pytest.raises(RuntimeError, match='rejected audit unavailable'):
        await service().acknowledge_attempt(request, authority)
    await assert_barrier(prepared)


async def test_revoked_current_admin_cannot_acknowledge_or_read_prior_receipt():
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id)
    async with AsyncSessionLocal() as session:
        await revoke_access_token(session, authority.token)
    with pytest.raises(HaResolutionError, match='session is no longer valid'):
        await service().acknowledge_attempt(request, authority)
    with pytest.raises(HaResolutionError, match='session is no longer valid'):
        await service().preview_attempt(prepared.claim.attempt_id, authority)
    await assert_barrier(prepared)


@pytest.mark.parametrize('phase', ['original_rollback', 'actual_close'])
async def test_rejection_cleanup_and_fresh_audit_survive_repeated_caller_cancel(monkeypatch, phase):
    from sqlalchemy.ext.asyncio import AsyncTransaction
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id, token_kind='invalid')
    entered, release = asyncio.Event(), asyncio.Event()
    original_transactions = []
    actual_rollback = AsyncTransaction.rollback
    async def rollback(transaction):
        if phase == 'original_rollback' and transaction.sync_transaction in original_transactions:
            entered.set()
            await release.wait()
        await actual_rollback(transaction)
    monkeypatch.setattr(AsyncTransaction, 'rollback', rollback)
    def factory():
        session = AsyncSessionLocal()
        connection, close = session.connection, session.close
        async def original_connection(*args, **kwargs):
            result = await connection(*args, **kwargs)
            original_transactions.append(result.get_transaction().sync_transaction)
            return result
        async def actual_close():
            if phase == 'actual_close':
                entered.set()
                await release.wait()
            await close()
        monkeypatch.setattr(session, 'connection', original_connection)
        monkeypatch.setattr(session, 'close', actual_close)
        return session
    owner = service(factory=factory)
    caller = asyncio.create_task(owner.acknowledge_attempt(request, authority))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        caller.cancel()
        await asyncio.sleep(0)
        caller.cancel()
        await asyncio.sleep(.02)
        assert not caller.done()
        assert await count_audit('real_world_action.confirmation.rejected') == 0
        release.set()
        with pytest.raises(confirmations.ActionConfirmationError):
            await caller
        assert await count_audit('real_world_action.confirmation.rejected') == 1
        await assert_barrier(prepared)
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)


async def test_known_resolution_commit_survives_late_close_fault_and_owned_fallback(monkeypatch):
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id)
    closes = []
    def factory():
        session = AsyncSessionLocal()
        close = session.close
        async def actual_close():
            closes.append(True)
            if len(closes) == 1:
                raise RuntimeError('synthetic late close fault')
            await close()
        monkeypatch.setattr(session, 'close', actual_close)
        return session
    receipt = await service(factory=factory).acknowledge_attempt(request, authority)
    assert receipt['attempt_id'] == str(prepared.claim.attempt_id) and len(closes) == 2
    await assert_barrier(prepared, retained=False)
    assert await count_audit('home_assistant.presence_effect.acknowledged') == 1
    assert await service().acknowledge_attempt(request, authority) == receipt


async def test_invalidated_original_transaction_is_not_fresh_rejection_audit_release_proof(monkeypatch):
    _, prepared = await unknown()
    user, authority = await admin()
    request = await confirmed(user, authority, prepared.claim.attempt_id, token_kind='invalid')
    actual = resolutions.consume_action_confirmation
    async def invalidated(session, **kwargs):
        try:
            return await actual(session, **kwargs)
        except confirmations.ActionConfirmationError:
            await session.invalidate()  # Local reset is deliberately NOT rollback proof.
            raise
    monkeypatch.setattr(resolutions, 'consume_action_confirmation', invalidated)
    with pytest.raises(resolutions.HaUnresolvedCleanupError) as caught:
        await service().acknowledge_attempt(request, authority)
    assert caught.value.rejection is not None and caught.value.committed_receipt is None
    assert await count_audit('real_world_action.confirmation.rejected') == 0
    await assert_barrier(prepared)
