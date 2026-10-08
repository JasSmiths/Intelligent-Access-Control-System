"""Sanitized settlement input and immutable replay identity without provider I/O."""
import uuid
from datetime import UTC, datetime

import pytest

from app.models import HaPresenceResolution
from app.services.ha_presence_resolution_contracts import (
    HaAcknowledgement, HaAdminRequest, HaResolutionError, acknowledgement_payload,
)
from app.services.ha_presence_resolutions import _matching_receipt, _request_fingerprint


def request(**changes):
    values = dict(attempt_id=uuid.uuid4(), expected_preview_fingerprint='a'*64,
        previous_request_settled=True, reason='Prior provider request is settled.',
        confirmation_id=uuid.uuid4(), confirmation_token='synthetic-confirmation-secret')
    return HaAcknowledgement(**(values | changes))


@pytest.mark.parametrize('settled', [False, None, 1, 'true', [], {}])
def test_settlement_requires_explicit_boolean_assertion(settled):
    with pytest.raises(HaResolutionError):
        request(previous_request_settled=settled)


@pytest.mark.parametrize('reason', ['', '   ', 'x'*501, [], None])
def test_settlement_reason_is_bounded_and_nonempty(reason):
    with pytest.raises(HaResolutionError):
        request(reason=reason)


@pytest.mark.parametrize('fingerprint', ['', 'a'*63, 'A'*64, [], None])
def test_preview_identity_is_exact_opaque_digest(fingerprint):
    with pytest.raises(HaResolutionError):
        request(expected_preview_fingerprint=fingerprint)


def test_generic_confirmation_payload_has_no_self_referential_new_id_or_secret():
    supplied = request()
    payload = acknowledgement_payload(supplied)
    assert payload == {'attempt_id':str(supplied.attempt_id), 'preview_fingerprint':'a'*64,
        'previous_request_settled':True, 'reason':supplied.reason}
    assert str(supplied.confirmation_id) not in repr(payload)
    assert supplied.confirmation_token not in repr(payload)
    assert supplied.confirmation_token not in repr(supplied)


def test_committed_matching_receipt_retrieval_needs_no_old_confirmation_token_or_current_target():
    supplied = request()
    authority = HaAdminRequest(uuid.uuid4(), 'synthetic-bearer-secret')
    stored = HaPresenceResolution(attempt_id=supplied.attempt_id,
        confirmation_id=supplied.confirmation_id, target_key='b'*64,
        target_generation=7, barrier_version=9, request_fingerprint=_request_fingerprint(supplied),
        actor_id=authority.actor_user_id, actor_auth_version=3,
        reason=supplied.reason, resolved_at=datetime.now(UTC))
    retried = request(attempt_id=supplied.attempt_id, confirmation_id=supplied.confirmation_id,
        confirmation_token=None)
    receipt = _matching_receipt(stored, authority, retried, _request_fingerprint(retried))
    assert receipt['target_generation'] == 7 and receipt['barrier_version'] == 9
    assert 'secret' not in repr(receipt) and 'synthetic-bearer-secret' not in repr(authority)
    with pytest.raises(HaResolutionError):
        _matching_receipt(stored, authority, request(), _request_fingerprint(request()))


def test_request_identity_includes_explicit_confirmation_and_semantic_reason_but_no_tokens():
    first = request()
    second = request(attempt_id=first.attempt_id, confirmation_id=first.confirmation_id,
        confirmation_token='different-secret')
    assert _request_fingerprint(first) == _request_fingerprint(second)
    changed = request(attempt_id=first.attempt_id, confirmation_id=first.confirmation_id,
        reason='Different operator decision')
    assert _request_fingerprint(first) != _request_fingerprint(changed)


@pytest.mark.asyncio
async def test_known_committed_receipt_survives_repeated_cancel_during_actual_session_close(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.ha_presence_resolutions import HaPresenceResolutionService
    session = AsyncSession()
    entered, release = asyncio.Event(), asyncio.Event()
    close = session.close
    async def closing():
        entered.set()
        await release.wait()
        await close()
    monkeypatch.setattr(session, 'connection', AsyncMock(return_value=None))
    monkeypatch.setattr(session, 'close', closing)
    owner = HaPresenceResolutionService(lambda: session, owns_local_capacity=lambda: False)
    receipt = {'attempt_id':'committed-fixture'}
    monkeypatch.setattr(owner, '_acknowledge_in_session', AsyncMock(return_value=receipt))
    caller = asyncio.create_task(owner.acknowledge_attempt(request(), HaAdminRequest(uuid.uuid4(), 'inert')))
    await entered.wait()
    caller.cancel()
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done()
    release.set()
    assert await caller is receipt


@pytest.mark.asyncio
async def test_late_close_fault_preserves_known_receipt_and_observes_fallback_cleanup(monkeypatch):
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.ha_presence_resolutions import HaPresenceResolutionService
    session = AsyncSession()
    close = session.close
    calls = 0
    async def closing():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('synthetic actual close failure')
        await close()
    monkeypatch.setattr(session, 'connection', AsyncMock(return_value=None))
    monkeypatch.setattr(session, 'close', closing)
    owner = HaPresenceResolutionService(lambda: session, owns_local_capacity=lambda: False)
    receipt = {'attempt_id':'committed-fixture'}
    monkeypatch.setattr(owner, '_acknowledge_in_session', AsyncMock(return_value=receipt))
    assert await owner.acknowledge_attempt(request(), HaAdminRequest(uuid.uuid4(), 'inert')) is receipt
    assert calls == 2


def rejection_evidence():
    from app.services.action_confirmations import ConfirmationRejection
    return ConfirmationRejection('Synthetic Admin', uuid.uuid4(), 'ha.fixture', 'a'*64,
        'not_found', None, None, None, None, None, None)


@pytest.mark.asyncio
async def test_local_reset_cannot_prove_original_release_or_drop_frozen_rejection(monkeypatch):
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services import ha_presence_resolutions as owner
    session = AsyncSession()
    evidence = rejection_evidence()
    audit = AsyncMock()
    monkeypatch.setattr(owner, 'persist_confirmation_rejection', audit)
    with pytest.raises(owner.HaUnresolvedCleanupError) as caught:
        await owner._finish_acknowledgement(session, None, None, evidence)
    assert caught.value.rejection is evidence and caught.value.committed_receipt is None
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_required_rejection_audit_failure_is_observable_after_proved_release(monkeypatch):
    from unittest.mock import AsyncMock
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services import ha_presence_resolutions as owner
    class Transaction:
        is_active = True
        async def rollback(self):
            self.is_active = False
    class Connection:
        closed, invalidated = False, False
        def __init__(self):
            self.transaction = Transaction()
        def get_transaction(self):
            return self.transaction
    session = AsyncSession()
    evidence = rejection_evidence()
    audit = AsyncMock(side_effect=RuntimeError('synthetic mandatory audit failure'))
    monkeypatch.setattr(owner, 'persist_confirmation_rejection', audit)
    with pytest.raises(RuntimeError, match='mandatory audit failure'):
        await owner._finish_acknowledgement(session, Connection(), None, evidence)
    audit.assert_awaited_once_with(evidence)
