from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

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
from app.db.session import AsyncSessionLocal
from app.models import ICloudCalendarAccount, NotificationActionContext, SystemSetting, User
from app.services.settings import invalidate_runtime_config_cache
from app.services.telemetry import TELEMETRY_CATEGORY_CRUD, actor_from_user, emit_audit_log


class AuthSecretRotationError(RuntimeError):
    """Raised when the auth secret cannot be rotated safely."""


@dataclass(frozen=True)
class DecryptedPayload:
    value: str
    source: str


async def migrate_encrypted_payloads_for_active_auth_secret() -> dict[str, int]:
    active_secret = get_auth_secret()
    candidates = _decryption_candidates(active_secret)
    migrated_settings = 0
    migrated_icloud_accounts = 0

    async with AsyncSessionLocal() as session:
        settings_rows = (await session.scalars(select(SystemSetting).order_by(SystemSetting.key))).all()
        for row in settings_rows:
            encrypted = _encrypted_setting_value(row)
            if not encrypted:
                continue
            decrypted = _decrypt_with_candidates(encrypted, candidates, f"system setting {row.key}")
            if decrypted.source != "active":
                row.value = {"encrypted": encrypt_secret(decrypted.value, secret=active_secret)}
                migrated_settings += 1

        icloud_accounts = (
            await session.scalars(
                select(ICloudCalendarAccount).where(ICloudCalendarAccount.encrypted_session_bundle.is_not(None))
            )
        ).all()
        for account in icloud_accounts:
            if not account.encrypted_session_bundle:
                continue
            decrypted = _decrypt_with_candidates(
                account.encrypted_session_bundle,
                candidates,
                f"iCloud calendar account {account.apple_id}",
            )
            if decrypted.source != "active":
                account.encrypted_session_bundle = encrypt_secret(decrypted.value, secret=active_secret)
                migrated_icloud_accounts += 1

        if migrated_settings or migrated_icloud_accounts:
            await session.commit()
            invalidate_runtime_config_cache()
        else:
            await session.rollback()

    clear_previous_auth_secret()
    return {
        "settings": migrated_settings,
        "icloud_accounts": migrated_icloud_accounts,
    }


async def auth_secret_security_status() -> dict[str, object]:
    return get_auth_secret_status()


async def rotate_auth_secret(
    *,
    user: User,
    confirmed: bool,
    new_secret: str | None = None,
) -> dict[str, object]:
    if not confirmed:
        raise AuthSecretRotationError("Auth secret rotation requires explicit confirmation.")

    status = get_auth_secret_status()
    if not status["ui_rotation_available"]:
        raise AuthSecretRotationError("Auth secret rotation is only available when using IACS_AUTH_SECRET_FILE.")

    old_secret = get_auth_secret()
    next_secret = validate_new_auth_secret(new_secret or generate_auth_secret_value())
    if next_secret == old_secret:
        raise AuthSecretRotationError("New auth secret must be different from the current secret.")

    file_path = Path(str(status["file_path"]))
    prepared_settings: list[tuple[SystemSetting, str]] = []
    prepared_icloud: list[tuple[ICloudCalendarAccount, str]] = []
    invalidated_contexts = 0
    file_replaced = False

    async with AsyncSessionLocal() as session:
        try:
            settings_rows = (await session.scalars(select(SystemSetting).order_by(SystemSetting.key))).all()
            for row in settings_rows:
                encrypted = _encrypted_setting_value(row)
                if not encrypted:
                    continue
                prepared_settings.append((row, decrypt_secret(encrypted, secret=old_secret)))

            icloud_accounts = (
                await session.scalars(
                    select(ICloudCalendarAccount).where(ICloudCalendarAccount.encrypted_session_bundle.is_not(None))
                )
            ).all()
            for account in icloud_accounts:
                if account.encrypted_session_bundle:
                    prepared_icloud.append(
                        (account, decrypt_secret(account.encrypted_session_bundle, secret=old_secret))
                    )

            now = datetime.now(tz=UTC)
            action_contexts = (
                await session.scalars(
                    select(NotificationActionContext).where(NotificationActionContext.consumed_at.is_(None))
                )
            ).all()
            for row, value in prepared_settings:
                row.value = {"encrypted": encrypt_secret(value, secret=next_secret)}
            for account, value in prepared_icloud:
                account.encrypted_session_bundle = encrypt_secret(value, secret=next_secret)
            for context in action_contexts:
                context.consumed_at = now
                context.outcome = "invalidated"
                context.outcome_detail = "Auth secret rotated."
                invalidated_contexts += 1

            write_auth_secret_file(previous_auth_secret_path(file_path), old_secret, allow_default=True)
            write_auth_secret_file(file_path, next_secret)
            reset_auth_secret_cache()
            file_replaced = True
            await session.commit()
        except Exception:
            await session.rollback()
            if file_replaced:
                write_auth_secret_file(file_path, old_secret)
                reset_auth_secret_cache()
            raise

    clear_previous_auth_secret(file_path)
    invalidate_runtime_config_cache()
    result = {
        **get_auth_secret_status(),
        "rotated": True,
        "settings_reencrypted": len(prepared_settings),
        "icloud_accounts_reencrypted": len(prepared_icloud),
        "action_contexts_invalidated": invalidated_contexts,
    }
    emit_audit_log(
        category=TELEMETRY_CATEGORY_CRUD,
        action="auth_secret.rotate",
        actor=actor_from_user(user),
        actor_user_id=user.id,
        target_entity="AuthSecret",
        target_label="Auth root secret",
        metadata={
            "source": result["source"],
            "file_path": result["file_path"],
            "custom_secret_supplied": bool(new_secret),
            "settings_reencrypted": len(prepared_settings),
            "icloud_accounts_reencrypted": len(prepared_icloud),
            "action_contexts_invalidated": invalidated_contexts,
        },
    )
    return result


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
