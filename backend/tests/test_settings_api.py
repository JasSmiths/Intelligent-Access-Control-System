import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import admin_user
from app.api.v1 import settings as settings_api
from app.models import User
from app.models.enums import UserRole
from app.services import settings as settings_service


def admin() -> User:
    return User(
        id=uuid.uuid4(),
        username="admin",
        first_name="Admin",
        last_name="User",
        full_name="Admin User",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
    )


def app_for_admin() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_api.router, prefix="/api/v1/settings")

    async def override_admin_user() -> User:
        return admin()

    app.dependency_overrides[admin_user] = override_admin_user
    return app


@pytest.mark.asyncio
async def test_update_settings_rejects_unknown_keys_before_db() -> None:
    with pytest.raises(settings_service.UnknownDynamicSettingsError) as exc:
        await settings_service.update_settings({"definitely_unknown": "value"})

    assert exc.value.unknown_keys == ["definitely_unknown"]
    assert "app_name" in exc.value.allowed_keys


@pytest.mark.asyncio
async def test_patch_settings_reports_unknown_keys(monkeypatch) -> None:
    async def fake_list_settings(*_args, **_kwargs):
        return [{"key": "app_name", "value": "IACS"}]

    monkeypatch.setattr(settings_api, "list_settings", fake_list_settings)
    transport = httpx.ASGITransport(app=app_for_admin())

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/api/v1/settings",
            json={"values": {"definitely_unknown": "value"}},
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["unknown_keys"] == ["definitely_unknown"]
    assert "app_name" in detail["allowed_keys"]
