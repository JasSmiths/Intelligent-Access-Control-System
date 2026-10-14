"""Auth-root file and ciphertext outcomes under one dedicated session fence."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.auth_secret import (
    DEFAULT_AUTH_SECRET_KEY,
    INSECURE_AUTH_SECRET_VALUES,
    AuthSecretError,
    clear_previous_auth_secret,
    generate_auth_secret_value,
    get_auth_secret,
    get_auth_secret_status,
    previous_auth_secret_path,
    read_previous_auth_secret,
    reset_auth_secret_cache,
    validate_new_auth_secret,
    write_auth_secret_file,
)
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import engine
from app.models import (
    AuditLog,
    NotificationActionContext,
    SystemSetting,
    User,
)
from app.services.auth_root import own_auth_root_connection
from app.services.mutation_context import load_active_admin
from app.services.settings import invalidate_runtime_config_cache
from app.services.telemetry import TELEMETRY_CATEGORY_CRUD, actor_from_user, write_audit_log

logger = get_logger(__name__)


class AuthSecretRotationError(RuntimeError):
    """Raised when the auth secret cannot be rotated safely."""


class AuthSecretOutcomeUnknown(AuthSecretRotationError):
    """Both keys remain available; never blindly restore or repeat rotation."""


@dataclass(frozen=True)
class DecryptedPayload:
    value: str
    source: str


async def auth_secret_security_status() -> dict[str, object]:
    return get_auth_secret_status()


async def rotate_auth_secret(
    *, user: User, confirmed: bool, new_secret: str | None = None,
) -> dict[str, object]:
    if not confirmed:
        raise AuthSecretRotationError("Auth secret rotation requires explicit confirmation.")
    # Cancellation cannot abandon a file replacement, transaction or held lock.
    child = asyncio.create_task(_rotate_owned(user, new_secret))
    try:
        return await asyncio.shield(child)
    except asyncio.CancelledError:
        try:
            await drain_owned(child)
        except asyncio.CancelledError:
            # Repeated caller cancellation cannot replace the fully drained
            # child's established durable result. Child failure still propagates.
            pass
        return child.result()


async def _rotate_owned(user: User, requested_secret: str | None) -> dict[str, object]:
    accepted: dict[str, object] | None = None
    try:
        async with own_auth_root_connection(engine) as connection:
            status = get_auth_secret_status()
            if not status["ui_rotation_available"]:
                raise AuthSecretRotationError("Auth secret rotation is only available when using IACS_AUTH_SECRET_FILE.")
            if read_previous_auth_secret():
                raise AuthSecretRotationError("Previous auth-key recovery is unfinished. Complete guarded encrypted-data recovery before rotating again.")
            old_secret = get_auth_secret()
            next_secret = validate_new_auth_secret(requested_secret or generate_auth_secret_value())
            if next_secret == old_secret:
                raise AuthSecretRotationError("New auth secret must be different from the current secret.")
            path = Path(str(status["file_path"]))
            session = AsyncSession(bind=connection, expire_on_commit=False)
            commit_started = file_started = False
            audit_id = uuid.uuid4()
            prepared: dict[str, object] | None = None
            failure: BaseException | None = None
            try:
                actor = await load_active_admin(session, user.id, auth_version=user.auth_session_version, lock=True)
                settings_rows = list((await session.scalars(select(SystemSetting).order_by(SystemSetting.key).with_for_update())).all())
                settings_count = 0
                for row in settings_rows:
                    encrypted = _encrypted_setting_value(row)
                    if encrypted:
                        row.value = {"encrypted": encrypt_secret(decrypt_secret(encrypted, secret=old_secret), secret=next_secret)}
                        settings_count += 1
                contexts = list((await session.scalars(select(NotificationActionContext).where(NotificationActionContext.consumed_at.is_(None)).order_by(NotificationActionContext.id).with_for_update())).all())
                now = datetime.now(UTC)
                for context in contexts:
                    context.consumed_at, context.outcome = now, "invalidated"
                    context.outcome_detail = "Auth secret rotated."
                audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD,
                    action="auth_secret.rotate", actor=actor_from_user(actor), actor_user_id=actor.id,
                    target_entity="AuthSecret", target_label="Auth root secret",
                    metadata={"source": status["source"], "file_path": status["file_path"],
                              "custom_secret_supplied": bool(requested_secret),
                              "settings_reencrypted": settings_count,
                              "action_contexts_invalidated": len(contexts)})
                audit.id = audit_id
                await session.flush()
                file_started = True
                await asyncio.to_thread(_replace_root_files, path, old_secret, next_secret)
                reset_auth_secret_cache()
                prepared = {**get_auth_secret_status(), "rotated": True,
                            "settings_reencrypted": settings_count,
                            "action_contexts_invalidated": len(contexts)}
                commit_started = True
                await session.commit()
                accepted = prepared
            except Exception as exc:
                failure = exc
                if commit_started:
                    outcome = await _settle_original_commit(session, connection, audit_id)
                    if outcome == "accepted":
                        accepted = prepared
                    elif outcome == "unknown":
                        if await _positive_recovery(audit_id):
                            accepted = prepared
                        else:
                            raise AuthSecretOutcomeUnknown(f"Auth secret rotation outcome is unknown (audit {audit_id}). Keep both key files and use guarded encrypted-data recovery before trying again.") from exc
                    else:
                        if file_started:
                            await asyncio.to_thread(_restore_root_files, path, old_secret)
                        raise
                else:
                    try:
                        if file_started and (connection.closed or connection.invalidated):
                            raise RuntimeError("Original auth-root file fence was lost.")
                        await session.rollback()
                        if file_started and (connection.closed or connection.invalidated):
                            raise RuntimeError("Original auth-root file fence was lost.")
                    except Exception as rollback_error:
                        if file_started:
                            raise AuthSecretOutcomeUnknown("Auth secret recovery is required after the original file fence was lost. Keep both key files and use guarded encrypted-data recovery before trying again.") from rollback_error
                        raise
                    if file_started:
                        await asyncio.to_thread(_restore_root_files, path, old_secret)
                    raise
            finally:
                try:
                    await session.close()
                except Exception:
                    if accepted is None and failure is None:
                        raise
                    logger.warning("Auth-root session cleanup failed; preserving established outcome")
            if accepted is not None:
                # Only clear under an intact original session fence. A recovered
                # positive UUID after lost connection leaves both keys for the
                # existing guarded recovery path; it cannot claim exclusivity.
                if not connection.closed and not connection.invalidated:
                    await asyncio.to_thread(clear_previous_auth_secret, path)
                invalidate_runtime_config_cache()
    except Exception:
        if accepted is None:
            raise
        logger.warning("Auth-root cleanup needs attention after accepted rotation")
    assert accepted is not None
    return accepted


async def migrate_encrypted_payloads_for_active_auth_secret() -> dict[str, int]:
    return await drain_owned(_migrate_owned())


async def _migrate_owned() -> dict[str, int]:
    accepted: dict[str, int] | None = None
    try:
        async with own_auth_root_connection(engine) as connection:
            active_secret = get_auth_secret()
            candidates = _decryption_candidates(active_secret)
            session = AsyncSession(bind=connection, expire_on_commit=False)
            commit_started = False
            audit_id = uuid.uuid4()
            counts = {"settings": 0}
            failure: BaseException | None = None
            try:
                rows = list((await session.scalars(select(SystemSetting).order_by(SystemSetting.key).with_for_update())).all())
                for row in rows:
                    encrypted = _encrypted_setting_value(row)
                    if encrypted:
                        decrypted = _decrypt_with_candidates(encrypted, candidates, f"system setting {row.key}")
                        if decrypted.source != "active":
                            row.value = {"encrypted": encrypt_secret(decrypted.value, secret=active_secret)}
                            counts["settings"] += 1
                if any(counts.values()):
                    audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_CRUD,
                        action="auth_secret.migrate_encrypted_payloads", actor="system",
                        target_entity="AuthSecret", target_label="Auth root secret", metadata=counts)
                    audit.id = audit_id
                    await session.flush()
                    commit_started = True
                    await session.commit()
                    accepted = counts
                else:
                    await session.rollback()
                    accepted = counts  # Every durable ciphertext was checked.
            except Exception as exc:
                failure = exc
                if commit_started:
                    outcome = await _settle_original_commit(session, connection, audit_id)
                    if outcome == "accepted" or (outcome == "unknown" and await _positive_recovery(audit_id)):
                        accepted = counts
                    elif outcome == "unknown":
                        raise AuthSecretOutcomeUnknown(f"Encrypted-data migration outcome is unknown (audit {audit_id}). Keep both key files and retry guarded recovery only after the database is available.") from exc
                    else:
                        raise
                else:
                    await session.rollback()
                    raise
            finally:
                try:
                    await session.close()
                except Exception:
                    if accepted is None and failure is None:
                        raise
                    logger.warning("Auth-root session cleanup failed; preserving established outcome")
            if accepted is not None:
                if not connection.closed and not connection.invalidated:
                    await asyncio.to_thread(clear_previous_auth_secret)
                invalidate_runtime_config_cache()
    except Exception:
        if accepted is None:
            raise
        logger.warning("Auth-root cleanup needs attention after accepted migration")
    assert accepted is not None
    return accepted


async def _settle_original_commit(
    session: AsyncSession, connection: AsyncConnection, audit_id: uuid.UUID,
) -> Literal["accepted", "rejected", "unknown"]:
    found = False
    try:
        if connection.closed or connection.invalidated:
            raise RuntimeError("Original auth-root connection lost its fence.")
        # An acknowledged rollback on the same fenced server connection proves
        # the original transaction settled. Only THEN can absence prove rejection.
        await session.rollback()
        if connection.closed or connection.invalidated:
            raise RuntimeError("Original auth-root connection lost its fence.")
        await _outcome_bounds(session)
        found = await session.scalar(select(AuditLog.id).where(AuditLog.id == audit_id)) is not None
        await session.rollback()
        return "accepted" if found else "rejected"
    except Exception:  # noqa: BLE001 - Missing/faulted outcome reads never authorize blind file restoration.
        if found:
            return "accepted"
        try:
            await drain_owned(connection.invalidate())
            await drain_owned(connection.close())
        except Exception:  # noqa: BLE001 - Cleanup failure cannot prove rejection or authorize file restoration.
            logger.warning("Auth-root uncertain original connection cleanup failed")
        return "unknown"


async def _positive_recovery(audit_id: uuid.UUID) -> bool:
    found = False
    try:
        async with asyncio.timeout(5):
            async with (
                own_auth_root_connection(engine, recovery=True) as connection,
                AsyncSession(bind=connection) as session,
            ):
                # Acquisition bounds ended at its commit; install fresh
                # short native bounds on this actual lookup transaction.
                await _outcome_bounds(session)
                found = await session.scalar(select(AuditLog.id).where(AuditLog.id == audit_id)) is not None
    except Exception:  # noqa: BLE001 - Positive durable evidence survives close; absence alone is never rollback proof here.
        logger.warning("Auth-root durable audit recovery lookup or cleanup failed")
    return found


async def _outcome_bounds(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('statement_timeout', '5000', true), set_config('lock_timeout', '1000', true)"))


def _replace_root_files(path: Path, old: str, new: str) -> None:
    write_auth_secret_file(previous_auth_secret_path(path), old, allow_default=True)
    write_auth_secret_file(path, new)


def _restore_root_files(path: Path, old: str) -> None:
    write_auth_secret_file(path, old, allow_default=True)
    reset_auth_secret_cache()
    clear_previous_auth_secret(path)


def _decryption_candidates(active_secret: str) -> list[tuple[str, str]]:
    candidates = [("active", active_secret)]
    for secret in sorted(INSECURE_AUTH_SECRET_VALUES):
        if secret != active_secret:
            source = "legacy_default" if secret == DEFAULT_AUTH_SECRET_KEY else "legacy_placeholder"
            candidates.append((source, secret))
    previous = read_previous_auth_secret()
    if previous and previous not in {secret for _, secret in candidates}:
        candidates.append(("previous_file", previous))
    return candidates


def _decrypt_with_candidates(encrypted: str, candidates: list[tuple[str, str]], label: str) -> DecryptedPayload:
    for source, secret in candidates:
        try:
            return DecryptedPayload(decrypt_secret(encrypted, secret=secret), source)
        except ValueError:
            continue
    raise AuthSecretError(
        f"Unable to decrypt {label} with the active auth secret, legacy default secret, "
        "legacy placeholder secret, or previous file secret. Restore the correct auth secret before starting IACS."
    )


def _encrypted_setting_value(row: SystemSetting) -> str:
    if not row.is_secret or not isinstance(row.value, dict):
        return ""
    return str(row.value.get("encrypted") or "")
