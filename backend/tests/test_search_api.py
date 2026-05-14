from datetime import UTC, datetime
import json
import uuid

import httpx
from fastapi import FastAPI

from app.api.dependencies import current_user
from app.api.v1 import search as search_api
from app.db.session import get_db_session
from app.models import (
    AutomationRule,
    Group,
    NotificationRule,
    Person,
    Schedule,
    User,
    Vehicle,
)
from app.models.enums import GroupCategory, UserRole


NOW = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)


def make_user(role: UserRole, *, username: str | None = None, full_name: str | None = None) -> User:
    name = username or role.value
    return User(
        id=uuid.uuid4(),
        username=name,
        first_name=(full_name or name.title()).split(" ", 1)[0],
        last_name=(full_name or "User").split(" ", 1)[-1],
        full_name=full_name or f"{name.title()} User",
        email=f"{name}@example.com",
        mobile_phone_number="+447700900123",
        password_hash="not-used",
        role=role,
        is_active=True,
        last_login_at=NOW,
        preferences={},
        created_at=NOW,
        updated_at=NOW,
    )


class FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class FakeSearchSession:
    def __init__(self, rows_by_model: dict[type[object], list[object]]) -> None:
        self.rows_by_model = rows_by_model
        self.calls = 0

    async def scalars(self, statement: object) -> FakeScalarResult:
        self.calls += 1
        entity = statement.column_descriptions[0]["entity"]
        return FakeScalarResult(list(self.rows_by_model.get(entity, [])))


def app_for_search(user: User, session: FakeSearchSession) -> FastAPI:
    app = FastAPI()
    app.include_router(search_api.router, prefix="/api/v1/search")

    async def override_current_user() -> User:
        return user

    async def override_db_session():
        yield session

    app.dependency_overrides[current_user] = override_current_user
    app.dependency_overrides[get_db_session] = override_db_session
    return app


def directory_rows() -> tuple[Person, Vehicle]:
    group = Group(
        id=uuid.uuid4(),
        name="Family",
        category=GroupCategory.FAMILY,
        subtype=None,
        description=None,
        created_at=NOW,
        updated_at=NOW,
    )
    schedule = Schedule(
        id=uuid.uuid4(),
        name="Always Allow",
        description=None,
        time_blocks={},
        created_at=NOW,
        updated_at=NOW,
    )
    person = Person(
        id=uuid.uuid4(),
        first_name="Jason",
        last_name="Smith",
        display_name="Jason Smith",
        group=group,
        schedule=schedule,
        garage_door_entity_ids=[],
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    vehicle = Vehicle(
        id=uuid.uuid4(),
        registration_number="MD25VNO",
        owner=person,
        schedule=schedule,
        make="Tesla",
        model="Model Y",
        color="Blue",
        description=None,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    person.vehicles = [vehicle]
    person.vehicle_assignments = []
    vehicle.person_assignments = []
    return person, vehicle


async def test_search_vehicle_plate_autocompletes_registration_prefix() -> None:
    person, vehicle = directory_rows()
    session = FakeSearchSession({Person: [person], Vehicle: [vehicle]})
    app = app_for_search(make_user(UserRole.STANDARD), session)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/search", params={"q": "MD2"})

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["type"] == "vehicle"
    assert payload[0]["label"] == "MD25VNO"
    assert payload[0]["filter_value"] == "MD25VNO"


async def test_search_person_name_autocompletes_prefix() -> None:
    person, vehicle = directory_rows()
    session = FakeSearchSession({Person: [person], Vehicle: [vehicle]})
    app = app_for_search(make_user(UserRole.STANDARD), session)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/search", params={"q": "Jas"})

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["type"] == "person"
    assert payload[0]["label"] == "Jason Smith"
    assert payload[0]["target"]["view"] == "people"


async def test_search_empty_query_returns_no_backend_data() -> None:
    session = FakeSearchSession({})
    app = app_for_search(make_user(UserRole.STANDARD), session)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/search", params={"q": "  "})

    assert response.status_code == 200
    assert response.json() == []
    assert session.calls == 0


async def test_search_hides_admin_only_results_from_standard_users() -> None:
    automation = AutomationRule(
        id=uuid.uuid4(),
        name="Garage Lockdown",
        description="Close everything",
        is_active=True,
        triggers=[],
        trigger_keys=["gate.opened"],
        conditions=[],
        actions=[],
        created_at=NOW,
        updated_at=NOW,
    )
    notification = NotificationRule(
        id=uuid.uuid4(),
        name="Garage Alert",
        trigger_event="gate.opened",
        conditions=[],
        actions=[],
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    admin_row = make_user(UserRole.ADMIN, username="garage-admin", full_name="Garage Admin")
    session = FakeSearchSession({
        User: [admin_row],
        AutomationRule: [automation],
        NotificationRule: [notification],
    })
    app = app_for_search(make_user(UserRole.STANDARD), session)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/search", params={"q": "Garage"})

    assert response.status_code == 200
    assert response.json() == []


async def test_search_includes_admin_results_without_exposing_action_secrets() -> None:
    secret = "sk_live_never_expose_this"
    automation = AutomationRule(
        id=uuid.uuid4(),
        name="Garage Lockdown",
        description="Close everything",
        is_active=True,
        triggers=[],
        trigger_keys=["gate.opened"],
        conditions=[],
        actions=[{"type": "webhook", "config": {"token": secret}}],
        created_at=NOW,
        updated_at=NOW,
    )
    notification = NotificationRule(
        id=uuid.uuid4(),
        name="Garage Alert",
        trigger_event="gate.opened",
        conditions=[],
        actions=[{"type": "discord", "config": {"bot_token": secret}}],
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    admin_row = make_user(UserRole.ADMIN, username="garage-admin", full_name="Garage Admin")
    session = FakeSearchSession({
        User: [admin_row],
        AutomationRule: [automation],
        NotificationRule: [notification],
    })
    app = app_for_search(make_user(UserRole.ADMIN), session)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/search", params={"q": "Garage"})

    assert response.status_code == 200
    payload = response.json()
    assert {"user", "automation_rule", "notification_rule"} <= {item["type"] for item in payload}
    assert secret not in json.dumps(payload)
