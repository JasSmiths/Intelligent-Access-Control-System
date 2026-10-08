import json
import stat
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.services.actionable_notifications as actionable
import app.services.auth as auth_service
import app.services.settings as settings_service
from app.core import auth_secret
from app.core.auth_secret import DEFAULT_AUTH_SECRET_KEY, AuthSecretError
from app.models import SystemSetting


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

    async def execute(self, _query, _params=None):
        return None

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


def test_lpr_webhook_token_is_registered_as_secret_setting() -> None:
    assert "lpr_webhook_token" in settings_service.SECRET_KEYS
    assert settings_service.DEFAULT_DYNAMIC_SETTINGS["lpr_webhook_token"][0] == "lpr"


@pytest.mark.asyncio
async def test_lpr_webhook_secret_lists_as_boolean_public_value(monkeypatch) -> None:
    setting = SystemSetting(
        key="lpr_webhook_token",
        category="lpr",
        value={"encrypted": "encrypted-token"},
        is_secret=True,
        description="Webhook token.",
    )
    fake_session = FakeSession([setting])

    monkeypatch.setattr(settings_service, "AsyncSessionLocal", lambda: fake_session)

    rows = await settings_service.list_settings("lpr")

    assert rows == [
        {
            "key": "lpr_webhook_token",
            "category": "lpr",
            "value": True,
            "is_secret": True,
            "description": "Webhook token.",
        }
    ]
    assert "encrypted-token" not in json.dumps(rows)


@pytest.mark.asyncio
async def test_lpr_webhook_secret_empty_update_keeps_existing_value(monkeypatch) -> None:
    setting = SystemSetting(
        key="lpr_webhook_token",
        category="lpr",
        value={"encrypted": "existing-token"},
        is_secret=True,
        description=settings_service.DEFAULT_DYNAMIC_SETTINGS["lpr_webhook_token"][2],
    )
    sessions = [FakeSession([setting]), FakeSession([setting])]

    monkeypatch.setattr(settings_service, "AsyncSessionLocal", lambda: sessions.pop(0))
    monkeypatch.setattr(settings_service, "invalidate_runtime_config_cache", lambda: None)

    rows = await settings_service.update_settings({"lpr_webhook_token": ""})

    assert setting.value == {"encrypted": "existing-token"}
    assert rows[0]["value"] is True


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


# Rotation ciphertext/action-context parity and required atomic audit are covered
# against PostgreSQL in scripts/validation/test_calendar_auth_root_persistence.py.
