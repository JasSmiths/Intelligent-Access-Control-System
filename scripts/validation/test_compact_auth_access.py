"""Compact reads retain identity/photo contracts and fresh authority."""
from test_recovery_boundaries import isolated_resources as isolated_resources

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import event as sql_event, select
from sqlalchemy.exc import InvalidRequestError
from starlette.requests import Request

from app import main
from app.api.v1 import auth as endpoint
from app.db.session import AsyncSessionLocal, engine
from app.models import Person, Presence, User, Vehicle
from app.models.enums import PresenceState, UserRole
from app.modules.lpr.base import PlateRead
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.reads import GATE_OBSERVATION_PAYLOAD_KEY
from app.services.auth import _encode_jwt, authenticate_token, serialize_user
from app.services.settings import DEFAULT_DYNAMIC_SETTINGS, runtime_config_from_values

pytestmark = pytest.mark.asyncio
NOW = datetime.now(UTC)
PHOTO = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=="


class Trace:
    def start_span(self, *_, **__):
        return SimpleNamespace(finish=lambda **_: None)


def config(**updates):
    return runtime_config_from_values({**{key: value for key, (_, value, _) in DEFAULT_DYNAMIC_SETTINGS.items()}, **updates})


async def subjects(photo=PHOTO):
    async with AsyncSessionLocal() as session:
        person = Person(display_name="Synthetic", profile_photo_data_url=photo, is_active=True)
        user = User(username="synthetic-compact", full_name="Synthetic Compact", password_hash="unused", role=UserRole.ADMIN,
            is_active=True, auth_session_version=1, profile_photo_data_url=photo)
        session.add_all([person, user])
        await session.flush()
        vehicle = Vehicle(registration_number="AB12CDE", person_id=person.id, is_active=True, vehicle_photo_data_url=photo)
        session.add(vehicle)
        session.add(Presence(person_id=person.id, state=PresenceState.EXITED))
        await session.commit()
        return person, vehicle, user


def token(user):
    return _encode_jwt({"sub": str(user.id), "ver": 1, "exp": int(datetime.now(UTC).timestamp())+60, "jti": "synthetic-compact"})


@pytest.mark.parametrize("photo", [None, PHOTO, "https://synthetic.invalid/photo.png"])
async def test_auth_projection_preserves_profile_url_without_loading_blob_or_password(isolated_resources, photo):
    _, _, user = await subjects(photo)
    async with AsyncSessionLocal() as session:
        row = await authenticate_token(session, token(user))
        assert "profile_photo_data_url" not in row.__dict__ and "password_hash" not in row.__dict__
        serialized = serialize_user(row)
        expected = f"/api/v1/users/{user.id}/photo?v={int(user.updated_at.timestamp())}" if photo == PHOTO else photo
        assert serialized["profile_photo_url"] == expected and serialized["profile_photo_data_url"] is None
        with pytest.raises(InvalidRequestError):
            _ = row.profile_photo_data_url
        response = await endpoint.auth_status(Request({"type": "http", "headers": [(b"authorization", f"Bearer {token(user)}".encode())]}), session, True)
        assert response.user.profile_photo_data_url == photo


async def test_vehicle_and_owner_photos_stay_unloaded_and_no_camera_probe_is_narrow(isolated_resources, monkeypatch):
    person, vehicle, _ = await subjects()
    resolver = AccessEvidenceResolver(config())
    read = PlateRead("AB12CDE", 1, "synthetic", NOW, {GATE_OBSERVATION_PAYLOAD_KEY: {"state": "closed"}})
    async with AsyncSessionLocal() as session:
        row = await resolver._lookup_active_vehicle(session, read, Trace())
        assert row.id == vehicle.id and row.owner.id == person.id
        assert "vehicle_photo_data_url" not in row.__dict__ and "profile_photo_data_url" not in row.owner.__dict__
        with pytest.raises(InvalidRequestError):
            _ = row.owner.profile_photo_data_url
    probe = AsyncMock(side_effect=AssertionError("No-camera preflight must not run the full resolver"))
    monkeypatch.setattr(resolver, "resolve", probe)
    statements = []
    def capture(_connection, _cursor, sql, *_):
        statements.append(sql)
    sql_event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        assert await resolver.prepare_camera_evidence(read, read, None, Trace()) is None
    finally:
        sql_event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert len(statements) == 1 and "photo_data_url" not in statements[0]
    probe.assert_not_awaited()


async def test_successful_authenticated_request_does_not_query_setup_count(isolated_resources, monkeypatch):
    _, _, user = await subjects()
    count = AsyncMock(side_effect=AssertionError("Successful auth must not query setup state"))
    monkeypatch.setattr(main, "count_users", count)
    request = Request({"type": "http", "method": "GET", "path": "/api/v1/settings", "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token(user)}".encode())]})
    async def next_request(current):
        assert current.state.user.id == user.id
        return "accepted"
    assert await main.auth_guard(request, next_request) == "accepted"
    count.assert_not_awaited()


@pytest.mark.parametrize("users,expected", [(0, 428), (1, 401)])
async def test_unauthenticated_setup_and_existing_installation_still_differ(isolated_resources, users, expected):
    if users:
        await subjects()
    request = Request({"type": "http", "method": "GET", "path": "/api/v1/settings", "query_string": b"", "headers": []})
    response = await main.auth_guard(request, AsyncMock(side_effect=AssertionError("Unauthenticated request reached route")))
    assert response.status_code == expected


async def test_authenticated_admin_can_read_own_deferred_photo(isolated_resources):
    from fastapi import FastAPI
    import httpx
    from app.api.v1 import users
    from app.db.session import get_db_session
    from PIL import Image
    from io import BytesIO
    import base64
    image = BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(image, format="PNG")
    _, _, user = await subjects("data:image/png;base64," + base64.b64encode(image.getvalue()).decode())
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1/users")
    async with AsyncSessionLocal() as session:
        async def shared_session():
            yield session
        app.dependency_overrides[get_db_session] = shared_session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test") as client:
            response = await client.get(f"/api/v1/users/{user.id}/photo", headers={"Authorization": f"Bearer {token(user)}"})
        assert response.status_code == 200 and response.headers["content-type"].startswith("image/png")
        assert response.content
