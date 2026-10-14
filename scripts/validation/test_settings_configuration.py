"""Real settings persistence and HTTP rejection on disposable PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from starlette.requests import Request
from sqlalchemy import select, text

from app.api.dependencies import admin_user
from app.api.v1 import settings as endpoint
from app.db.session import AsyncSessionLocal
from app.models import Person, ResidentRecoveryJourney, SystemSetting, User
from app.models.enums import UserRole
from app.services import settings
from app.services.auth import authenticate_request, create_access_token

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def configured(isolated_resources):
    async with AsyncSessionLocal() as session:
        await settings.seed_dynamic_settings_for_session(session)
    settings.invalidate_runtime_config_cache()
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE system_settings, audit_logs CASCADE"))
        await session.commit()
    settings.invalidate_runtime_config_cache()


async def stored():
    async with AsyncSessionLocal() as session:
        return {row.key: row.value for row in (await session.scalars(select(SystemSetting))).all()}


@pytest.mark.parametrize("updates", [
    {"auth_access_token_minutes": "invalid"}, {"auth_access_token_minutes": None},
    {"auth_access_token_minutes": True}, {"lpr_debounce_max_seconds": 1e100},
    {"schedule_default_policy": "invalid"}, {"site_timezone": "Not/AZone"},
    {"lpr_debounce_quiet_seconds": 8, "lpr_debounce_max_seconds": 6},
    {"lpr_similarity_threshold": float("nan")}, {"llm_timeout_seconds": float("inf")},
])
async def test_rejected_settings_leave_storage_and_runtime_usable(updates):
    before = await stored()
    with pytest.raises(ValueError):
        await settings.update_settings(updates)
    assert await stored() == before
    assert (await settings.get_runtime_config()).auth_cookie_name == "iacs_session"


async def test_valid_update_and_blank_secret_preserve_encrypted_storage():
    await settings.update_settings({"home_assistant_token": "synthetic-secret", "auth_access_token_minutes": "60"})
    before = await stored()
    await settings.update_settings({"home_assistant_token": "", "app_name": "Synthetic site"})
    after = await stored()
    assert after["home_assistant_token"] == before["home_assistant_token"]
    assert "synthetic-secret" not in str(after)
    assert (await settings.get_runtime_config()).auth_access_token_minutes == 60


async def test_identical_and_blank_secret_saves_report_no_changes():
    await settings.update_settings({"home_assistant_token": "synthetic-secret"})
    result = await settings.update_settings({"home_assistant_token": "", "auth_access_token_minutes": "720"})
    assert result.changed_keys == frozenset()
    result = await settings.update_settings({"home_assistant_token": "replacement-synthetic-secret"})
    assert result.changed_keys == frozenset({"home_assistant_token"})


@pytest.mark.parametrize("port", ["6053.0", "6.053e3"])
async def test_nested_numeric_conversions_remain_loadable_after_save(port):
    await settings.update_settings({"esphome_devices": [{"id": "synthetic", "host": "synthetic.invalid",
        "port": port, "timeout_seconds": "10.5", "enabled": "false"}]})
    config = await settings.get_runtime_config()
    assert config.esphome_devices[0]["port"] == 6053
    assert config.esphome_devices[0]["timeout_seconds"] == 10.5
    assert config.esphome_devices[0]["enabled"] is False


async def test_invalid_legacy_boolean_can_be_repaired_to_equal_numeric_value():
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(SystemSetting).where(SystemSetting.key == "lpr_similarity_threshold"))
        row.value = {"plain": True}
        await session.commit()
    result = await settings.update_settings({"lpr_similarity_threshold": 1})
    assert result.changed_keys == frozenset({"lpr_similarity_threshold"})
    assert (await stored())["lpr_similarity_threshold"] == {"plain": 1}
    assert type((await stored())["lpr_similarity_threshold"]["plain"]) is int
    assert (await settings.get_runtime_config()).lpr_similarity_threshold == 1


async def test_noop_recovery_settings_preserve_journey_but_actual_change_invalidates():
    identity = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        session.add(Person(id=identity, first_name="Synthetic", last_name="Person", display_name="Synthetic Person"))
        await session.flush()
        session.add(ResidentRecoveryJourney(person_id=identity, epoch=str(uuid.uuid4()), binding="synthetic",
            samples=[{"synthetic": True}]))
        await session.commit()
    result = await settings.update_settings({"missed_exit_recovery_enabled": False})
    assert result.changed_keys == frozenset()
    async with AsyncSessionLocal() as session:
        assert (await session.get(ResidentRecoveryJourney, identity)).samples == [{"synthetic": True}]
    await settings.update_settings({"missed_exit_recovery_gate_latitude": 51, "missed_exit_recovery_gate_longitude": -1})
    async with AsyncSessionLocal() as session:
        row = await session.get(ResidentRecoveryJourney, identity)
        assert row.samples == [] and row.invalid_reason == "recovery_settings_changed"


async def test_post_commit_read_failure_cannot_relabel_save(monkeypatch):
    monkeypatch.setattr(settings, "list_settings", AsyncMock(side_effect=RuntimeError("read failure")))
    result = await settings.update_settings({"app_name": "Saved before refresh"})
    assert next(row["value"] for row in result.rows if row["key"] == "app_name") == "Saved before refresh"
    assert (await stored())["app_name"] == {"plain": "Saved before refresh"}


async def test_legacy_policy_corruption_remains_fail_closed_but_cookie_auth_can_repair():
    async with AsyncSessionLocal() as session:
        user = User(id=uuid.uuid4(), username="synthetic-legacy-admin", first_name="Synthetic", last_name="Admin",
            full_name="Synthetic Admin", password_hash="unused", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        record = await session.scalar(select(SystemSetting).where(SystemSetting.key == "schedule_default_policy"))
        record.value = {"plain": "broken-legacy-policy"}
        await session.commit()
    token, _ = await create_access_token(user)
    request = Request({"type": "http", "headers": [(b"cookie", f"iacs_session={token}".encode())]})
    async with AsyncSessionLocal() as session:
        authenticated = await authenticate_request(session, request)
        assert authenticated and authenticated.id == user.id
    with pytest.raises(ValueError, match="schedule_default_policy"):
        await settings.get_runtime_config()
    with pytest.raises(ValueError, match="schedule_default_policy"):
        await settings.update_settings({"app_name": "Unrelated save"}, user=user)
    await settings.update_settings({"schedule_default_policy": "deny"}, user=user)
    assert (await settings.get_runtime_config()).schedule_default_policy == "deny"


async def test_http_invalid_setting_is_400_with_real_storage(monkeypatch):
    async with AsyncSessionLocal() as session:
        user = User(id=uuid.uuid4(), username="synthetic-admin", first_name="Synthetic", last_name="Admin",
            full_name="Synthetic Admin", password_hash="unused", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1/settings")
    app.dependency_overrides[admin_user] = lambda: user
    monkeypatch.setattr(endpoint, "require_confirmed_action", AsyncMock())
    before = await stored()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic") as client:
        response = await client.patch("/api/v1/settings", json={"values": {"schedule_default_policy": "invalid"}})
    assert response.status_code == 400
    assert await stored() == before
    assert (await settings.get_runtime_config()).schedule_default_policy == "allow"


async def test_acknowledged_save_survives_closed_session_error_and_activates_fresh_config(monkeypatch):
    from app.services.settings_activation import SettingsActivationCoordinator
    await settings.get_runtime_config()  # Prime the old process cache.
    original = settings.AsyncSessionLocal
    async with original() as session:
        user = User(username="synthetic-close-admin", first_name="Synthetic", last_name="Admin", full_name="Synthetic Admin",
            password_hash="unused", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
    class FailedAfterActualClose:
        async def __aenter__(self):
            self.session = original()
            return await self.session.__aenter__()
        async def __aexit__(self, *args):
            await self.session.__aexit__(*args)
            raise RuntimeError("synthetic error after actual cleanup")
    contexts = 0
    def sessions():
        nonlocal contexts
        contexts += 1
        return FailedAfterActualClose() if contexts == 1 else original()
    monkeypatch.setattr(settings, "AsyncSessionLocal", sessions)
    activated = []
    async def restart():
        activated.append((await settings.get_runtime_config()).home_assistant_url)
    coordinator = SettingsActivationCoordinator({"home_assistant": restart})
    receipt = await coordinator.save({"home_assistant_url": "http://synthetic.invalid"}, user=user, source="synthetic")
    assert receipt["saved"] and receipt["changed_keys"] == ["home_assistant_url"]
    assert receipt["activation"]["state"] == "activated" and activated == ["http://synthetic.invalid"]
    assert (await stored())["home_assistant_url"] == {"plain": "http://synthetic.invalid"}


async def test_unconfirmed_commit_does_not_return_accepted_or_activate(monkeypatch):
    from app.services.settings_activation import SettingsActivationCoordinator
    before = await stored()
    original = settings.AsyncSessionLocal
    async with original() as session:
        user = User(username="synthetic-commit-admin", first_name="Synthetic", last_name="Admin", full_name="Synthetic Admin",
            password_hash="unused", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
    def sessions():
        session = original()
        session.commit = AsyncMock(side_effect=RuntimeError("synthetic commit acknowledgement failure"))
        return session
    monkeypatch.setattr(settings, "AsyncSessionLocal", sessions)
    restart = AsyncMock()
    coordinator = SettingsActivationCoordinator({"home_assistant": restart})
    with pytest.raises(RuntimeError, match="acknowledgement failure"):
        await coordinator.save({"home_assistant_url": "http://synthetic.invalid"}, user=user, source="synthetic")
    restart.assert_not_awaited()
    assert await stored() == before
