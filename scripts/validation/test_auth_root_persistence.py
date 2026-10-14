"""Real fenced ciphertext writers and mandatory rotation receipts in isolated PG."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import auth_secret
from app.core.crypto import decrypt_secret, encrypt_secret
from app.db.session import AsyncSessionLocal, engine
from app.models import AuditLog, NotificationActionContext, SystemSetting, User
from app.models.enums import UserRole
from app.services import auth_secret_management as owner
from app.services import settings as setting_owner
from app.services.auth_root import AUTH_ROOT_LOCK_KEY

pytestmark = pytest.mark.asyncio
OLD = "synthetic-old-root-never-production-" + "a" * 32
NEW = "synthetic-new-root-never-production-" + "b" * 32


@pytest_asyncio.fixture(autouse=True)
async def root_resources(isolated_resources, tmp_path, monkeypatch):
    path = tmp_path / "auth-root.key"
    monkeypatch.delenv("IACS_AUTH_SECRET_KEY", raising=False)
    monkeypatch.setattr(auth_secret.settings, "auth_secret_key", "")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_file", path)
    auth_secret.write_auth_secret_file(path, OLD)
    auth_secret.reset_auth_secret_cache()
    setting_owner.invalidate_runtime_config_cache()
    yield path
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE system_settings CASCADE"))
        await session.commit()
    auth_secret.reset_auth_secret_cache()
    setting_owner.invalidate_runtime_config_cache()


async def actor():
    row = User(id=uuid.uuid4(), username="synthetic-root-admin", full_name="Synthetic Admin",
               password_hash="synthetic-not-usable", role=UserRole.ADMIN, is_active=True)
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()
    return row


async def secret_row(value="before"):
    async with AsyncSessionLocal() as session:
        session.add(SystemSetting(key="lpr_webhook_token", category="lpr", is_secret=True,
                                  value={"encrypted": encrypt_secret(value, secret=OLD)}, description="synthetic"))
        await session.commit()


async def assert_no_root_lock_or_pool_checkout():
    assert engine.pool.checkedout() == 0
    async with AsyncSessionLocal() as session:
        assert await session.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=:high AND objid=:low AND granted"),
                                    {"high": AUTH_ROOT_LOCK_KEY >> 32, "low": AUTH_ROOT_LOCK_KEY & 0xffffffff}) == 0


async def test_zero_ciphertext_rotation_has_required_durable_audit_and_releases(root_resources):
    user = await actor()
    result = await owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW)
    assert result["rotated"] and result["settings_reencrypted"] == 0
    assert root_resources.read_text().strip() == NEW
    assert not auth_secret.previous_auth_secret_path().exists()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth_secret.rotate")) == 1
    await assert_no_root_lock_or_pool_checkout()


async def test_required_audit_failure_rolls_back_all_ciphertext_and_keeps_original_file(root_resources, monkeypatch):
    user = await actor()
    await secret_row()
    original = owner.write_audit_log

    async def fail_after_staging(session, **kwargs):
        await original(session, **kwargs)
        await session.flush()
        raise RuntimeError("synthetic mandatory audit failure")

    monkeypatch.setattr(owner, "write_audit_log", fail_after_staging)
    with pytest.raises(RuntimeError, match="mandatory audit"):
        await owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW)
    assert root_resources.read_text().strip() == OLD
    assert not auth_secret.previous_auth_secret_path().exists()
    async with AsyncSessionLocal() as session:
        row = await session.get(SystemSetting, "lpr_webhook_token")
        assert decrypt_secret(row.value["encrypted"], secret=OLD) == "before"
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
    await assert_no_root_lock_or_pool_checkout()


async def test_committed_but_ack_lost_is_positive_audit_acceptance(root_resources, monkeypatch):
    user = await actor()
    await secret_row()
    commit = AsyncSession.commit

    async def commit_then_lose_ack(session):
        await commit(session)
        raise RuntimeError("synthetic lost acknowledgement")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_ack)
    result = await owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW)
    assert result["rotated"]
    assert root_resources.read_text().strip() == NEW
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth_secret.rotate")) == 1
        row = await session.get(SystemSetting, "lpr_webhook_token")
        assert decrypt_secret(row.value["encrypted"], secret=NEW) == "before"
    monkeypatch.setattr(AsyncSession, "commit", commit)
    await assert_no_root_lock_or_pool_checkout()


async def test_unresolved_commit_keeps_both_keys_and_refuses_blind_repeat_then_guarded_recovery(root_resources, monkeypatch):
    user = await actor()
    await secret_row()
    commit, bounds = AsyncSession.commit, owner._outcome_bounds

    async def commit_then_lose_ack(session):
        await commit(session)
        raise RuntimeError("synthetic lost acknowledgement")

    async def unavailable_outcome(session):
        raise RuntimeError("synthetic outcome lookup unavailable")

    monkeypatch.setattr(AsyncSession, "commit", commit_then_lose_ack)
    monkeypatch.setattr(owner, "_outcome_bounds", unavailable_outcome)
    with pytest.raises(owner.AuthSecretOutcomeUnknown, match="Keep both key files"):
        await owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW)
    assert root_resources.read_text().strip() == NEW
    assert auth_secret.previous_auth_secret_path().read_text().strip() == OLD
    monkeypatch.setattr(AsyncSession, "commit", commit)
    monkeypatch.setattr(owner, "_outcome_bounds", bounds)
    with pytest.raises(owner.AuthSecretRotationError, match="recovery is unfinished"):
        await owner.rotate_auth_secret(user=user, confirmed=True)
    assert await owner.migrate_encrypted_payloads_for_active_auth_secret() == {"settings": 0}
    assert not auth_secret.previous_auth_secret_path().exists()
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth_secret.rotate")) == 1
    await assert_no_root_lock_or_pool_checkout()


async def test_rotation_blocks_settings_writer_and_reloads_stale_process_cache(root_resources, monkeypatch):
    user = await actor()
    await secret_row()
    written, release = threading.Event(), threading.Event()
    replace = owner._replace_root_files
    entered = asyncio.Event()
    reload_root = setting_owner.reload_auth_root_for_transaction

    def blocked_replace(*args):
        replace(*args)
        written.set()
        assert release.wait(5)

    async def observed_reload(session):
        entered.set()
        return await reload_root(session)

    monkeypatch.setattr(owner, "_replace_root_files", blocked_replace)
    monkeypatch.setattr(setting_owner, "reload_auth_root_for_transaction", observed_reload)
    assert auth_secret.get_auth_secret() == OLD  # A stale process cache.
    rotation = asyncio.create_task(owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW))
    assert await asyncio.to_thread(written.wait, 2)
    update = asyncio.create_task(setting_owner.update_settings({"lpr_webhook_token": "after"}, user=user))
    await asyncio.wait_for(entered.wait(), 2)
    assert not update.done()
    release.set()
    assert (await rotation)["rotated"]
    await update
    async with AsyncSessionLocal() as session:
        row = await session.get(SystemSetting, "lpr_webhook_token")
        assert decrypt_secret(row.value["encrypted"], secret=NEW) == "after"
    await assert_no_root_lock_or_pool_checkout()


async def test_settings_writer_finishes_before_rotation_reads_its_new_ciphertext(root_resources, monkeypatch):
    user = await actor()
    await secret_row()
    staged, release = asyncio.Event(), asyncio.Event()
    original = setting_owner.write_audit_log

    async def block_audit(session, **kwargs):
        result = await original(session, **kwargs)
        staged.set()
        await release.wait()
        return result

    monkeypatch.setattr(setting_owner, "write_audit_log", block_audit)
    update = asyncio.create_task(setting_owner.update_settings({"lpr_webhook_token": "after"}, user=user))
    await asyncio.wait_for(staged.wait(), 2)
    rotation = asyncio.create_task(owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW))
    await asyncio.sleep(0)
    assert not rotation.done()
    release.set()
    await update
    assert (await rotation)["settings_reencrypted"] == 1
    async with AsyncSessionLocal() as session:
        row = await session.get(SystemSetting, "lpr_webhook_token")
        assert decrypt_secret(row.value["encrypted"], secret=NEW) == "after"
    await assert_no_root_lock_or_pool_checkout()


async def test_seed_plain_secret_reload_and_migration_preserve_settings_ciphertext(root_resources):
    async with AsyncSessionLocal() as session:
        session.add(SystemSetting(key="lpr_webhook_token", category="lpr", is_secret=False, value="plain-value"))
        await session.commit()
    async with AsyncSessionLocal() as session:
        await setting_owner.seed_dynamic_settings_for_session(session)
    async with AsyncSessionLocal() as session:
        row = await session.get(SystemSetting, "lpr_webhook_token")
        assert row.is_secret and decrypt_secret(row.value["encrypted"], secret=OLD) == "plain-value"
    auth_secret.write_auth_secret_file(auth_secret.previous_auth_secret_path(), OLD)
    auth_secret.write_auth_secret_file(root_resources, NEW)
    # Deliberately leave the old process cache populated until fenced migration.
    assert auth_secret.get_auth_secret() == OLD
    counts = await owner.migrate_encrypted_payloads_for_active_auth_secret()
    assert counts["settings"] >= 1
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth_secret.migrate_encrypted_payloads")) == 1
    await assert_no_root_lock_or_pool_checkout()


async def test_cancelled_rotation_keeps_fence_until_actual_file_thread_and_returns_acceptance(root_resources, monkeypatch):
    user = await actor()
    started, release = threading.Event(), threading.Event()
    replace = owner._replace_root_files

    def blocked_replace(*args):
        started.set()
        assert release.wait(5)
        replace(*args)

    monkeypatch.setattr(owner, "_replace_root_files", blocked_replace)
    rotation = asyncio.create_task(owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW))
    assert await asyncio.to_thread(started.wait, 2)
    rotation.cancel()
    await asyncio.sleep(0)
    assert not rotation.done() and engine.pool.checkedout() == 1
    release.set()
    assert (await rotation)["rotated"]
    await assert_no_root_lock_or_pool_checkout()


async def test_precommit_file_replacement_then_real_connection_loss_keeps_both_keys(root_resources, monkeypatch):
    user = await actor()
    await secret_row()
    written, release = threading.Event(), threading.Event()
    replace, status = owner._replace_root_files, owner.get_auth_secret_status
    checks = 0

    def blocked_replace(*args):
        replace(*args)
        written.set()
        assert release.wait(5)

    def fail_receipt_preparation():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise auth_secret.AuthSecretError("synthetic post-file preparation failure")
        return status()

    monkeypatch.setattr(owner, "_replace_root_files", blocked_replace)
    monkeypatch.setattr(owner, "get_auth_secret_status", fail_receipt_preparation)
    rotation = asyncio.create_task(owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW))
    try:
        assert await asyncio.to_thread(written.wait, 2)
        async with AsyncSessionLocal() as session:
            pid = await session.scalar(text("SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=:high AND objid=:low AND granted"),
                                       {"high": AUTH_ROOT_LOCK_KEY >> 32, "low": AUTH_ROOT_LOCK_KEY & 0xffffffff})
            assert pid is not None
            assert await session.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}) is True
        release.set()
        with pytest.raises(owner.AuthSecretOutcomeUnknown, match="Keep both key files"):
            await rotation
    finally:
        release.set()
        await asyncio.gather(rotation, return_exceptions=True)
    assert root_resources.read_text().strip() == NEW
    assert auth_secret.previous_auth_secret_path().read_text().strip() == OLD
    async with AsyncSessionLocal() as session:
        row = await session.get(SystemSetting, "lpr_webhook_token")
        assert decrypt_secret(row.value["encrypted"], secret=OLD) == "before"
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
    monkeypatch.setattr(owner, "get_auth_secret_status", status)
    counts = await owner.migrate_encrypted_payloads_for_active_auth_secret()
    assert counts["settings"] == 1
    await assert_no_root_lock_or_pool_checkout()


async def test_two_caller_cancels_after_commit_preserve_rotation_receipt_and_drain_close(root_resources, monkeypatch):
    user = await actor()
    started, release = threading.Event(), threading.Event()
    close = AsyncSession.close

    def blocked_cleanup():
        started.set()
        assert release.wait(5)

    async def close_after_actual_cleanup(session):
        await asyncio.to_thread(blocked_cleanup)
        await close(session)

    monkeypatch.setattr(AsyncSession, "close", close_after_actual_cleanup)
    rotation = asyncio.create_task(owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        for _ in range(2):
            rotation.cancel()
            await asyncio.sleep(0)
            assert not rotation.done() and engine.pool.checkedout() == 1
        release.set()
        assert (await rotation)["rotated"]
    finally:
        release.set()
        await asyncio.gather(rotation, return_exceptions=True)
        monkeypatch.setattr(AsyncSession, "close", close)
    assert root_resources.read_text().strip() == NEW
    async with AsyncSessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth_secret.rotate")) == 1
    await assert_no_root_lock_or_pool_checkout()




async def test_real_rotation_reencrypts_settings_and_invalidates_actions(root_resources):
    user = await actor()
    await secret_row("inert-setting-value")
    context_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(NotificationActionContext(id=context_id, token_hash="a" * 64, action="gate.open",
            notify_service="notify.inert", registration_number="AB12CDE",
            expires_at=datetime.now(UTC) + timedelta(minutes=5)))
        await session.commit()
    result = await owner.rotate_auth_secret(user=user, confirmed=True, new_secret=NEW)
    assert root_resources.read_text().strip() == NEW
    assert result["settings_reencrypted"] == result["action_contexts_invalidated"] == 1
    async with AsyncSessionLocal() as session:
        setting = await session.get(SystemSetting, "lpr_webhook_token")
        context = await session.get(NotificationActionContext, context_id)
        assert decrypt_secret(setting.value["encrypted"], secret=NEW) == "inert-setting-value"
        assert context.outcome == "invalidated" and context.consumed_at is not None
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth_secret.rotate")) == 1
    await assert_no_root_lock_or_pool_checkout()


async def test_late_try_root_is_nonwaiting_and_does_not_touch_stale_cache_on_conflict(root_resources, monkeypatch):
    from app.services import auth_root
    # Cache OLD, then change the file while a real different connection owns
    # the rotation fence. A rejected late participant cannot reload that file.
    assert auth_secret.get_auth_secret() == OLD
    async with engine.connect() as connection:
        await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": AUTH_ROOT_LOCK_KEY})
        await connection.commit()
        auth_secret.write_auth_secret_file(root_resources, NEW)
        reset_calls = []
        original = auth_root.reset_auth_secret_cache
        def reset():
            reset_calls.append(True)
            original()
        monkeypatch.setattr(auth_root, "reset_auth_secret_cache", reset)
        async with AsyncSessionLocal() as session:
            # A blocking implementation would exceed this independent fixture
            # guard while the owning connection deliberately retains the lock.
            result = await asyncio.wait_for(auth_root.try_reload_auth_root_for_transaction(session), 0.5)
            assert result is None and reset_calls == []
            assert auth_secret.get_auth_secret() == OLD
        await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": AUTH_ROOT_LOCK_KEY})
        await connection.commit()
    async with AsyncSessionLocal() as session:
        assert await auth_root.try_reload_auth_root_for_transaction(session) == NEW
        assert reset_calls == [True]
        # The successful participant retains a transaction lock until cleanup.
        async with engine.connect() as other:
            assert await other.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": AUTH_ROOT_LOCK_KEY}) is False
    await assert_no_root_lock_or_pool_checkout()
