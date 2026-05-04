import json
import stat
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.core.auth_secret as auth_secret
import app.services.auth as auth_service
import app.services.actionable_notifications as actionable
import app.services.auth_secret_management as auth_secret_management
from app.core.auth_secret import DEFAULT_AUTH_SECRET_KEY, AuthSecretError
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models import ICloudCalendarAccount, NotificationActionContext, SystemSetting, User


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, *row_sets):
        self._row_sets = list(row_sets)
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalars(self, _query):
        return FakeScalars(self._row_sets.pop(0))

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.fixture(autouse=True)
def reset_auth_secret_cache():
    auth_secret.reset_auth_secret_cache()
    yield
    auth_secret.reset_auth_secret_cache()


def test_production_rejects_default_auth_secret_without_file_or_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("IACS_AUTH_SECRET_KEY", raising=False)
    monkeypatch.setattr(auth_secret.settings, "environment", "production")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_key", "")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_file", tmp_path / "missing.key")

    with pytest.raises(AuthSecretError, match="does not exist"):
        auth_secret.get_auth_secret()


def test_development_generates_file_backed_auth_secret(tmp_path, monkeypatch) -> None:
    path = tmp_path / "auth-secret.key"
    monkeypatch.delenv("IACS_AUTH_SECRET_KEY", raising=False)
    monkeypatch.setattr(auth_secret.settings, "environment", "development")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_key", "")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_file", path)

    secret = auth_secret.get_auth_secret()
    status = auth_secret.get_auth_secret_status()

    assert secret != DEFAULT_AUTH_SECRET_KEY
    assert path.read_text().strip() == secret
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert status["source"] == "generated"
    assert status["ui_rotation_available"] is True


def test_env_backed_auth_secret_status_hides_value_and_disables_ui_rotation(tmp_path, monkeypatch) -> None:
    secret = "x" * 48
    monkeypatch.setenv("IACS_AUTH_SECRET_KEY", secret)
    monkeypatch.setattr(auth_secret.settings, "environment", "production")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_file", tmp_path / "missing.key")

    status = auth_secret.get_auth_secret_status()

    assert status["source"] == "env"
    assert status["ui_rotation_available"] is False
    assert secret not in json.dumps(status)


@pytest.mark.asyncio
async def test_startup_migration_reencrypts_legacy_default_settings_and_icloud(monkeypatch) -> None:
    active_secret = "a" * 48
    setting = SystemSetting(
        key="openai_api_key",
        category="llm",
        value={"encrypted": encrypt_secret("openai-secret", secret=DEFAULT_AUTH_SECRET_KEY)},
        is_secret=True,
        description="OpenAI API key.",
    )
    account = ICloudCalendarAccount(
        apple_id="owner@example.com",
        encrypted_session_bundle=encrypt_secret('{"session":"legacy"}', secret=DEFAULT_AUTH_SECRET_KEY),
    )
    fake_session = FakeSession([setting], [account])

    monkeypatch.setattr(auth_secret_management, "get_auth_secret", lambda: active_secret)
    monkeypatch.setattr(auth_secret_management, "read_previous_auth_secret", lambda: None)
    monkeypatch.setattr(auth_secret_management, "clear_previous_auth_secret", lambda *_args: None)
    monkeypatch.setattr(auth_secret_management, "invalidate_runtime_config_cache", lambda: None)
    monkeypatch.setattr(auth_secret_management, "AsyncSessionLocal", lambda: fake_session)

    result = await auth_secret_management.migrate_encrypted_payloads_for_active_auth_secret()

    assert result == {"settings": 1, "icloud_accounts": 1}
    assert decrypt_secret(setting.value["encrypted"], secret=active_secret) == "openai-secret"
    assert decrypt_secret(account.encrypted_session_bundle, secret=active_secret) == '{"session":"legacy"}'
    assert fake_session.committed is True


def test_jwts_and_actionable_hashes_are_bound_to_current_auth_secret(tmp_path, monkeypatch) -> None:
    path = tmp_path / "auth-secret.key"
    monkeypatch.delenv("IACS_AUTH_SECRET_KEY", raising=False)
    monkeypatch.setattr(auth_secret.settings, "environment", "development")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_key", "")
    monkeypatch.setattr(auth_secret.settings, "auth_secret_file", path)
    auth_secret.write_auth_secret_file(path, "a" * 48)
    auth_secret.reset_auth_secret_cache()

    expires_at = int((datetime.now(tz=UTC) + timedelta(minutes=5)).timestamp())
    token = auth_service._encode_jwt({"sub": str(uuid.uuid4()), "exp": expires_at})
    old_action_hash = actionable._token_hash("token")

    assert auth_service._decode_jwt(token) is not None

    auth_secret.write_auth_secret_file(path, "b" * 48)
    auth_secret.reset_auth_secret_cache()

    assert auth_service._decode_jwt(token) is None
    assert actionable._token_hash("token") != old_action_hash


@pytest.mark.asyncio
async def test_rotation_reencrypts_payloads_and_invalidates_action_contexts(tmp_path, monkeypatch) -> None:
    old_secret = "a" * 48
    new_secret = "b" * 48
    path = tmp_path / "auth-secret.key"
    auth_secret.write_auth_secret_file(path, old_secret)
    setting = SystemSetting(
        key="gemini_api_key",
        category="llm",
        value={"encrypted": encrypt_secret("gemini-secret", secret=old_secret)},
        is_secret=True,
        description="Gemini API key.",
    )
    account = ICloudCalendarAccount(
        apple_id="owner@example.com",
        encrypted_session_bundle=encrypt_secret('{"session":"old"}', secret=old_secret),
    )
    context = NotificationActionContext(
        token_hash="a" * 64,
        action="gate.open",
        notify_service="notify.mobile_app_owner",
        registration_number="AB12CDE",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    fake_session = FakeSession([setting], [account], [context])

    monkeypatch.setattr(auth_secret_management, "get_auth_secret", lambda: old_secret)
    monkeypatch.setattr(
        auth_secret_management,
        "get_auth_secret_status",
        lambda: {
            "source": "file",
            "environment": "development",
            "file_path": str(path),
            "env_configured": False,
            "env_default_configured": False,
            "rotation_required": False,
            "ui_rotation_available": True,
            "detail": "file",
        },
    )
    monkeypatch.setattr(auth_secret_management, "reset_auth_secret_cache", lambda: None)
    monkeypatch.setattr(auth_secret_management, "clear_previous_auth_secret", lambda *_args: None)
    monkeypatch.setattr(auth_secret_management, "invalidate_runtime_config_cache", lambda: None)
    monkeypatch.setattr(auth_secret_management, "emit_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(auth_secret_management, "AsyncSessionLocal", lambda: fake_session)

    result = await auth_secret_management.rotate_auth_secret(
        user=User(id=uuid.uuid4(), username="admin", full_name="Admin"),
        confirmed=True,
        new_secret=new_secret,
    )

    assert path.read_text().strip() == new_secret
    assert decrypt_secret(setting.value["encrypted"], secret=new_secret) == "gemini-secret"
    assert decrypt_secret(account.encrypted_session_bundle, secret=new_secret) == '{"session":"old"}'
    assert context.outcome == "invalidated"
    assert context.consumed_at is not None
    assert result["settings_reencrypted"] == 1
    assert result["icloud_accounts_reencrypted"] == 1
    assert result["action_contexts_invalidated"] == 1
