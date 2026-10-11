"""Actual owned setup/verify transactions around an inert synchronous SDK port."""
from test_recovery_boundaries import isolated_resources as isolated_resources
from test_calendar_sync_store_persistence import calendar_resources as calendar_resources
from test_calendar_sync_store_persistence import allowed, setup_accounts

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.db.session import AsyncSessionLocal, engine
from app.models import AuditLog, ICloudCalendarAccount
from app.modules.icloud_calendar.contracts import ICloudCalendarStopped
from app.services import calendar_accounts as account_owner
from app.services.calendar_accounts import CalendarAccountStore, CalendarAccountAuthChanged, CalendarAccountAuthUnknown
from app.services.calendar_authentication import CalendarAuthentication, CalendarAuthenticationError
from app.services.calendar_sync_store import CalendarSyncStore
from app.services.icloud_calendar_sdk import CalendarSDKBusy, CalendarSDKOwner

pytestmark = pytest.mark.asyncio


class InertAuthClient:
    def __init__(self, *, two_factor=False):
        self.two_factor, self.valid = two_factor, True
        self.calls, self.cleaned, self.detached = [], [], []
        self.started, self.release = Event(), Event()
        self.cleanup_started, self.cleanup_release = Event(), Event()
        self.block_start = self.block_cleanup = self.fail_cleanup = False
    def start_auth(self, apple_id, password, *, operation):
        assert engine.pool.checkedout() == 0
        self.calls.append(("start", apple_id))
        self.started.set()
        if self.block_start:
            assert self.release.wait(4)
        # Return the acquired handle even after stop; the actual owner must
        # close it instead of losing a late-acquired client.
        return SimpleNamespace(apple_id=apple_id, operation=operation)
    def requires_security_key(self, session): return False
    def requires_legacy_2sa(self, session): return False
    def requires_2fa(self, session): return self.two_factor
    def request_2fa_code(self, session):
        assert engine.pool.checkedout() == 0
        self.calls.append(("request", session.apple_id))
    def activate(self, session, operation):
        assert engine.pool.checkedout() == 0
        session.operation = operation
        self.calls.append(("activate", session.apple_id))
    def validate_2fa_code(self, session, code):
        assert engine.pool.checkedout() == 0
        self.calls.append(("validate", session.apple_id))
        return self.valid
    def trust_session(self, session):
        assert engine.pool.checkedout() == 0
        self.calls.append(("trust", session.apple_id))
        return True
    def session_bundle(self, session):
        assert engine.pool.checkedout() == 0
        return {"inert": "accepted-session"}
    def detach(self, session):
        assert engine.pool.checkedout() == 0
        session.operation = None
        self.detached.append(session.apple_id)
    def cleanup_auth_session(self, session):
        assert engine.pool.checkedout() == 0
        self.cleanup_started.set()
        if self.block_cleanup:
            assert self.cleanup_release.wait(4)
        self.cleaned.append(session.apple_id)
        session.operation = None
        if self.fail_cleanup:
            raise RuntimeError("synthetic cleanup fault")


async def setup(*, two_factor=False):
    actor, _ = await setup_accounts(0)
    client, accounts = InertAuthClient(two_factor=two_factor), CalendarAccountStore(CalendarSyncStore())
    sdk = CalendarSDKOwner(client)
    return actor, client, accounts, sdk, CalendarAuthentication(client, sdk, accounts)


async def start(owner, actor, apple_id="synthetic@example.invalid", *, authorize=allowed):
    return await owner.start(apple_id=apple_id, password="synthetic-not-a-real-password",
        actor="Synthetic Admin", actor_user_id=actor, authorize=authorize)


async def verify(owner, actor, handshake, *, authorize=allowed):
    return await owner.verify(handshake_id=handshake, code="123456", actor="Synthetic Admin",
        actor_user_id=actor, authorize=authorize)


async def test_connect_and_reconnect_preserve_logical_identity_and_receipt_before_close():
    actor, client, _, sdk, owner = await setup()
    first = await start(owner, actor)
    identity = uuid.UUID(first["account"]["id"])
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarAccount, identity)
        created, generation = row.created_at, row.sync_generation
        row.display_name = "Preserved Calendar"
        row.status, row.is_active = "removed", False
        row.last_error = "old failure"
        row.sync_lease_token = uuid.uuid4()
        row.sync_lease_until = datetime.now(UTC) + timedelta(seconds=30)
        await session.commit()
    second = await start(owner, actor)
    assert second["account"]["id"] == str(identity)
    assert second["account"]["display_name"] == "Preserved Calendar"
    assert second["account"]["created_by_user_id"] == str(actor)
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarAccount, identity)
        assert row.created_at == created and row.sync_generation == generation + 1
        assert row.sync_lease_token is None and row.sync_lease_until is None
        assert row.is_active and row.last_error is None
        assert json.loads(decrypt_secret(row.encrypted_session_bundle)) == {"inert": "accepted-session"}
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 2
    assert len(client.cleaned) == 2 and engine.pool.checkedout() == 0
    await sdk.stop()


async def test_pending_required_audit_rejected_code_and_success_use_one_retained_client():
    actor, client, _, sdk, owner = await setup(two_factor=True)
    response = await start(owner, actor)
    handshake = response["handshake_id"]
    assert response["requires_2fa"] and sdk.pending(handshake) is not None
    assert client.cleaned == [] and len(client.detached) == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "icloud_calendar.auth_2fa_required")) == 1
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarAccount)) == 0
    client.valid = False
    with pytest.raises(CalendarAuthenticationError, match="rejected"):
        await verify(owner, actor, handshake)
    assert sdk.pending(handshake) is not None and client.cleaned == []
    client.valid = True
    result = await verify(owner, actor, handshake)
    assert result["status"] == "connected" and sdk.pending(handshake) is None
    assert len(client.cleaned) == 1
    await sdk.stop()


async def test_other_admin_cannot_use_pending_handshake_before_sdk_or_drop():
    actor, client, _, sdk, owner = await setup(two_factor=True)
    handshake = (await start(owner, actor))["handshake_id"]
    before = list(client.calls)
    with pytest.raises(CalendarAuthenticationError, match="another Admin"):
        await verify(owner, uuid.uuid4(), handshake)
    assert client.calls == before and sdk.pending(handshake) is not None
    await sdk.stop()


async def test_changed_account_capture_rejects_old_verification_and_cleans_client():
    actor, client, _, sdk, owner = await setup()
    connected = await start(owner, actor)
    client.two_factor = True
    handshake = (await start(owner, actor))["handshake_id"]
    before = list(client.calls)
    async with AsyncSessionLocal() as session:
        row = await session.get(ICloudCalendarAccount, uuid.UUID(connected["account"]["id"]))
        row.sync_generation += 1
        await session.commit()
    with pytest.raises(CalendarAccountAuthChanged):
        await verify(owner, actor, handshake)
    assert client.calls == before and sdk.pending(handshake) is None
    await sdk.stop()


async def test_required_account_audit_failure_rolls_back_and_closes_acquired_client(monkeypatch):
    actor, client, _, sdk, owner = await setup()
    original = account_owner.write_audit_log
    async def fail(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError("synthetic required audit failure")
    monkeypatch.setattr(account_owner, "write_audit_log", fail)
    with pytest.raises(RuntimeError, match="required audit"):
        await start(owner, actor)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarAccount)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
    assert len(client.cleaned) == 1 and engine.pool.checkedout() == 0
    await sdk.stop()


async def test_required_handshake_audit_failure_does_not_retain_client(monkeypatch):
    actor, client, _, sdk, owner = await setup(two_factor=True)
    async def fail(*args, **kwargs): raise RuntimeError("synthetic setup audit failure")
    monkeypatch.setattr(account_owner, "write_audit_log", fail)
    with pytest.raises(RuntimeError, match="setup audit"):
        await start(owner, actor)
    assert len(client.cleaned) == 1 and not sdk._pending
    await sdk.stop()


async def test_account_commit_ack_loss_resolves_by_required_audit_and_preserves_receipt(monkeypatch):
    actor, client, _, sdk, owner = await setup()
    original = AsyncSession.commit
    async def commit_lost(session):
        await original(session)
        raise RuntimeError("synthetic accepted acknowledgement lost")
    monkeypatch.setattr(AsyncSession, "commit", commit_lost)
    receipt = await start(owner, actor)
    monkeypatch.setattr(AsyncSession, "commit", original)
    assert receipt["status"] == "connected" and len(client.cleaned) == 1
    assert engine.pool.checkedout() == 0
    await sdk.stop()


async def test_unresolved_account_commit_is_unknown_not_false_rollback(monkeypatch):
    actor, client, accounts, sdk, owner = await setup()
    original = AsyncSession.commit
    async def commit_lost(session):
        await original(session)
        raise RuntimeError("synthetic accepted acknowledgement lost")
    async def unavailable(*args): raise RuntimeError("synthetic outcome read unavailable")
    monkeypatch.setattr(AsyncSession, "commit", commit_lost)
    monkeypatch.setattr(accounts, "_resolve", unavailable)
    with pytest.raises(CalendarAccountAuthUnknown):
        await start(owner, actor)
    monkeypatch.setattr(AsyncSession, "commit", original)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarAccount)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1
    assert len(client.cleaned) == 1 and engine.pool.checkedout() == 0
    await sdk.stop()


async def test_two_caller_cancellations_after_account_acceptance_keep_receipt_and_actual_cleanup():
    actor, client, _, sdk, owner = await setup()
    client.block_cleanup = True
    task = asyncio.create_task(start(owner, actor))
    assert await asyncio.to_thread(client.cleanup_started.wait, 3)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    with pytest.raises(CalendarSDKBusy):
        await start(owner, actor, "another@example.invalid")
    client.cleanup_release.set()
    result = await task
    assert result["status"] == "connected" and len(client.cleaned) == 1
    assert engine.pool.checkedout() == 0
    await sdk.stop()


async def test_final_current_authority_failure_after_sdk_never_saves_account():
    actor, client, _, sdk, owner = await setup()
    async def changed(session, final):
        if final:
            raise CalendarAccountAuthChanged("synthetic revoked Admin authority")
        return await allowed(session, final)
    with pytest.raises(CalendarAccountAuthChanged, match="revoked"):
        await start(owner, actor, authorize=changed)
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarAccount)) == 0
    assert len(client.cleaned) == 1
    await sdk.stop()


async def test_known_account_acceptance_survives_late_native_cleanup_failure():
    actor, client, _, sdk, owner = await setup()
    client.fail_cleanup = True
    result = await start(owner, actor)
    assert result["status"] == "connected" and len(client.cleaned) == 1
    await sdk.stop()


async def test_pending_capacity_reserved_before_ninth_constructor_and_existing_verify_at_cap():
    actor, client, _, sdk, owner = await setup(two_factor=True)
    handshakes = [(await start(owner, actor, f"pending{index}@example.invalid"))["handshake_id"] for index in range(8)]
    before = list(client.calls)
    with pytest.raises(CalendarSDKBusy, match="Eight"):
        await start(owner, actor, "ninth@example.invalid")
    assert client.calls == before and len(sdk._pending) == 8
    assert (await verify(owner, actor, handshakes[0]))["status"] == "connected"
    assert len(sdk._pending) == 7
    await sdk.stop()
    assert len(client.cleaned) == 8


async def test_stop_during_actual_constructor_waits_for_late_acquired_cleanup():
    actor, client, _, sdk, owner = await setup()
    client.block_start = True
    task = asyncio.create_task(start(owner, actor))
    assert await asyncio.to_thread(client.started.wait, 3)
    stopping = asyncio.create_task(sdk.stop())
    await asyncio.sleep(0.02)
    assert not stopping.done() and not task.done() and engine.pool.checkedout() == 0
    client.release.set()
    with pytest.raises(ICloudCalendarStopped):
        await task
    await stopping
    assert len(client.cleaned) == 1 and engine.pool.checkedout() == 0


async def test_handshake_primary_audit_failure_survives_actual_session_close_fault(monkeypatch):
    actor, client, _, sdk, owner = await setup(two_factor=True)
    original_close = AsyncSession.close
    flagged = set()
    async def audit_fail(session, **kwargs):
        flagged.add(id(session))
        raise CalendarAccountAuthUnknown("synthetic primary unknown audit")
    async def close_fail(session):
        await original_close(session)
        if id(session) in flagged:
            raise RuntimeError("synthetic later actual session close fault")
    monkeypatch.setattr(account_owner, "write_audit_log", audit_fail)
    monkeypatch.setattr(AsyncSession, "close", close_fail)
    with pytest.raises(CalendarAccountAuthUnknown, match="primary unknown"):
        await start(owner, actor)
    monkeypatch.setattr(AsyncSession, "close", original_close)
    assert len(client.cleaned) == 1 and engine.pool.checkedout() == 0
    await sdk.stop()


@pytest.mark.parametrize("verify_pending", [False, True])
async def test_unknown_account_acceptance_survives_client_cleanup_failure(monkeypatch, verify_pending):
    actor, client, accounts, sdk, owner = await setup(two_factor=verify_pending)
    handshake = (await start(owner, actor))["handshake_id"] if verify_pending else None
    client.fail_cleanup = True
    original = AsyncSession.commit
    async def commit_lost(session):
        await original(session)
        raise RuntimeError("synthetic accepted ack lost")
    async def unavailable(*args): raise RuntimeError("synthetic lookup unavailable")
    monkeypatch.setattr(AsyncSession, "commit", commit_lost)
    monkeypatch.setattr(accounts, "_resolve", unavailable)
    with pytest.raises(CalendarAccountAuthUnknown):
        if handshake:
            await verify(owner, actor, handshake)
        else:
            await start(owner, actor)
    monkeypatch.setattr(AsyncSession, "commit", original)
    assert engine.pool.checkedout() == 0 and len(client.cleaned) == 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarAccount)) == 1
    if handshake:
        assert sdk.pending(handshake) is None
        assert handshake in sdk._retiring and handshake in sdk._pending
        calls = list(client.calls)
        client.fail_cleanup = False
        with pytest.raises(CalendarAuthenticationError, match="expired"):
            await verify(owner, actor, handshake)
        assert client.calls == calls and handshake not in sdk._pending
    client.fail_cleanup = False
    await sdk.stop()


@pytest.mark.parametrize("challenge", ["security_key", "legacy_2sa"])
async def test_unsupported_challenge_is_rejected_without_pending_or_account_and_cleans_client(challenge):
    actor, client, _, sdk, owner = await setup()
    setattr(client, "requires_" + challenge, lambda session: True)
    with pytest.raises(CalendarAuthenticationError, match="security key|older two-step"):
        await start(owner, actor)
    assert len(client.cleaned) == 1 and len(sdk._pending) == 0
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ICloudCalendarAccount)) == 0
    await sdk.stop()
