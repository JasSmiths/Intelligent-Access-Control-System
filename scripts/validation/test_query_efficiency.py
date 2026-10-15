"""User media and notification presence parity on isolated PostgreSQL."""
from test_recovery_boundaries import isolated_resources as isolated_resources

import base64
import hashlib
import io
import json
import statistics
import time
import tracemalloc
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import event, inspect, select, text, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from app.api.dependencies import admin_user
from app.api.v1 import users as users_api
from app.api.v1.media import data_url_media_response
from app.db.session import AsyncSessionLocal, engine
from app.models import Person, Presence, User
from app.models.enums import PresenceState, UserRole
from app.modules.notifications.base import NotificationContext
from app.services import auth
from app.services.notification_planning import NotificationPlanner, presence_condition_matches
from app.services.settings import get_runtime_config

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
PERSON = uuid.UUID("abcdefab-abcd-abcd-abcd-abcdefabcdef")


def account(index, photo=None, **values):
    return User(id=uuid.UUID(int=index), username=f"synthetic-{index}", first_name=f"Name{index:05d}",
        last_name="Synthetic", full_name=f"Name{index:05d} Synthetic", password_hash="synthetic-unused-hash",
        profile_photo_data_url=photo, role=UserRole.STANDARD, preferences={"theme": "dark"},
        created_at=NOW, updated_at=NOW, **values)


async def save(*rows):
    async with AsyncSessionLocal() as session:
        session.add_all(rows)
        await session.commit()


def capture():
    sql, loaded = [], []
    def before(_connection, _cursor, statement, parameters, *_):
        sql.append((statement, parameters))
    def materialized(_session, row):
        if isinstance(row, User):
            loaded.append(("User", set(inspect(row).dict)))
        elif isinstance(row, Presence):
            loaded.append("Presence")
    event.listen(engine.sync_engine, "before_cursor_execute", before)
    event.listen(Session, "loaded_as_persistent", materialized)
    return sql, loaded, before, materialized


def release(before, materialized):
    event.remove(engine.sync_engine, "before_cursor_execute", before)
    event.remove(Session, "loaded_as_persistent", materialized)


def evidence(config, key, value):
    path = Path(config.getoption("basetemp")).parent / "query-efficiency.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = value
    path.write_text(json.dumps(data, indent=2) + "\n")


def png():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(stream, format="PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


@pytest.mark.parametrize("include_photo", [False, True])
async def test_user_list_response_projection_and_explicit_photo(isolated_resources, include_photo, monkeypatch):
    person = Person(id=PERSON, first_name="Linked", display_name="Linked")
    admin = account(1, png())
    admin.role = UserRole.ADMIN
    standard = account(2, person_id=PERSON, email="synthetic@example.invalid", mobile_phone_number="0000",
        last_login_at=NOW)
    external = account(3, "https://example.invalid/photo.png")
    external.first_name = ""
    external.last_name = ""
    external.full_name = "Legacy Name"
    external.preferences = None
    await save(person, admin, standard, external, account(4, ""))
    async with AsyncSessionLocal() as session:
        rows = (await session.scalars(select(User).order_by(User.first_name, User.last_name))).all()
        expected = [auth.serialize_user(row, include_photo=include_photo) for row in rows]
    tokens = {"admin": (await auth.create_access_token(admin))[0],
        "standard": (await auth.create_access_token(standard))[0]}
    app = FastAPI()
    app.include_router(users_api.router, prefix="/api/v1/users")
    serialized_fields = []
    def serialize(user, **kwargs):
        serialized_fields.append(set(inspect(user).dict))
        assert "password_hash" in inspect(user).unloaded
        with pytest.raises(InvalidRequestError):
            _ = user.password_hash
        if not include_photo:
            with pytest.raises(InvalidRequestError):
                _ = user.profile_photo_data_url
        return auth.serialize_user(user, **kwargs)
    monkeypatch.setattr(users_api, "serialize_user", serialize)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        sql, loaded, before, materialized = capture()
        try:
            result = await client.get("/api/v1/users", params={"include_photo": str(include_photo).lower()},
                headers={"Authorization": "Bearer " + tokens["admin"]})
        finally:
            release(before, materialized)
        assert result.status_code == 200
        assert result.json() == expected
        assert "password_hash" not in result.text and "synthetic-unused-hash" not in result.text
        lists = [query for query, _ in sql if "ORDER BY users.first_name" in query]
        assert len(lists) == 1
        assert "users.password_hash" not in lists[0]
        assert len(serialized_fields) == 4
        assert all("password_hash" not in fields for fields in serialized_fields)
        if not include_photo:
            assert all("profile_photo_data_url" not in fields for fields in serialized_fields)
            assert "CASE WHEN" in lists[0]  # A reference expression may read the column in PostgreSQL.
        else:
            assert all("profile_photo_data_url" in fields for fields in serialized_fields)
        assert not any(query.startswith("SELECT users.profile_photo_data_url") for query, _ in sql)
        photo = await client.get(f"/api/v1/users/{admin.id}/photo", headers={"Authorization": "Bearer " + tokens["admin"]})
        expected_photo = await data_url_media_response(admin.profile_photo_data_url)
        assert photo.status_code == 200 and photo.headers["content-type"] == expected_photo.media_type
        assert photo.content == expected_photo.body
        assert (await client.get("/api/v1/users")).status_code == 401
        assert (await client.get("/api/v1/users", headers={"Authorization": "Bearer " + tokens["standard"]})).status_code == 403
        async with AsyncSessionLocal() as session:
            await session.execute(update(User).where(User.id == admin.id).values(is_active=False))
            await session.commit()
        assert (await client.get("/api/v1/users", headers={"Authorization": "Bearer " + tokens["admin"]})).status_code == 401


async def test_user_list_empty(isolated_resources):
    # An authenticated production list cannot be empty; isolate only the Admin dependency for this edge.
    app = FastAPI()
    app.include_router(users_api.router, prefix="/api/v1/users")
    app.dependency_overrides[admin_user] = lambda: account(999)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        assert (await client.get("/api/v1/users")).json() == []


@pytest.mark.parametrize("size", [8, 300])
@pytest.mark.parametrize("include_photo", [False, True])
async def test_user_list_measurements(isolated_resources, pytestconfig, size, include_photo):
    photo = "data:image/png;base64," + "A" * (128 * 1024)
    await save(*(account(index + 1, photo if index % 2 == 0 else None) for index in range(size)))
    samples, peaks, counts, loaded_fields = [], [], [], set()
    output = b""
    for index in range(6):  # One warm-up followed by five sequential samples, fresh sessions.
        sql, loaded, before, materialized = capture()
        try:
            tracemalloc.start()
            started = time.perf_counter()
            async with AsyncSessionLocal() as session:
                result = await users_api.list_users(account(9999), session, include_photo)
                output = json.dumps([row.model_dump() for row in result], sort_keys=True).encode()
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        finally:
            release(before, materialized)
        if index:
            samples.append(elapsed * 1000)
            peaks.append(peak)
            counts.append(len(sql))
            loaded_fields.update(*(fields for _, fields in loaded))
    evidence(pytestconfig, f"users-{size}-photo-{include_photo}", {
        "rows": size, "photo_text_bytes_each": len(photo), "photo_rows": size // 2,
        "warmup": 1, "samples": 5, "query_counts": counts, "loaded_fields": sorted(loaded_fields),
        "response_bytes": len(output), "response_sha256": hashlib.sha256(output).hexdigest(),
        "python_tracemalloc_peaks_bytes": peaks, "p50_ms": statistics.median(samples),
        "p95_ms_nearest_rank": max(samples), "qualification": "Python traced allocations, not RSS or database wire bytes; timings include JSON encoding."})


def planner():
    return NotificationPlanner(sessions=AsyncSessionLocal, config=get_runtime_config)


def context():
    return NotificationContext(event_type="synthetic-query", subject="Synthetic", severity="info",
        facts={"occurred_at": NOW.isoformat()})


@pytest.mark.parametrize("states", [[], [PresenceState.EXITED], [PresenceState.UNKNOWN],
    [PresenceState.PRESENT], [PresenceState.PRESENT, PresenceState.PRESENT],
    [PresenceState.EXITED, PresenceState.PRESENT]])
async def test_presence_truth_matrix_and_scalar_queries(isolated_resources, states):
    config = await get_runtime_config()
    ids = [PERSON if i == 0 else uuid.UUID(int=100 + i) for i in range(len(states))]
    await save(*(Person(id=ids[i], first_name="Synthetic", display_name="Synthetic",
        is_active=i != 0) for i in range(len(states))))
    await save(*(Presence(person_id=ids[i], state=state) for i, state in enumerate(states)))
    present = {str(ids[i]) for i, state in enumerate(states) if state == PresenceState.PRESENT}
    conditions = [{"type": "presence", "mode": mode} for mode in ["no_one_home", "someone_home", "unknown", None]]
    conditions += [{"type": "presence", "mode": "person_home", "person_id": value} for value in
        [str(PERSON), PERSON, str(uuid.UUID(int=101)), str(uuid.UUID(int=999)), None, "invalid", 3,
         str(PERSON).replace("-", ""), str(PERSON).upper(),
         "{" + str(PERSON) + "}", [str(PERSON)], " " + str(PERSON), True]]
    sql, loaded, before, materialized = capture()
    try:
        async with AsyncSessionLocal() as session:
            for condition in conditions:
                expected = presence_condition_matches(condition, present)
                actual = await planner()._condition_matches(session, condition, context(), NOW, config)
                assert actual is expected, condition
    finally:
        release(before, materialized)
    assert not loaded, "Presence conditions must not materialize ORM rows"
    assert all("EXISTS" in query and "presence.state" in query for query, _ in sql)


async def test_presence_planning_observes_changes_between_calls(isolated_resources):
    await save(Person(id=PERSON, first_name="Synthetic", display_name="Synthetic", is_active=False))
    await save(Presence(person_id=PERSON, state=PresenceState.EXITED))
    rule = {"id": str(uuid.UUID(int=1)), "name": "Synthetic", "conditions": [
        {"type": "presence", "mode": "someone_home"}, {"type": "presence", "mode": "person_home", "person_id": str(PERSON)}],
        "actions": [{"type": "in_app", "title_template": "Synthetic", "message_template": "Test"}]}
    owner = planner()
    assert (await owner.build(context(), [rule]))[0]["state"] == "skipped"
    async with AsyncSessionLocal() as session:
        await session.execute(update(Presence).values(state=PresenceState.PRESENT))
        await session.commit()
    plan = await owner.build(context(), [rule, rule])
    assert len(plan) == 2 and all(item["state"] == "pending" for item in plan)
    async with AsyncSessionLocal() as session:
        await session.execute(update(Presence).values(state=PresenceState.UNKNOWN))
        await session.commit()
    assert (await owner.build(context(), [rule]))[0]["state"] == "skipped"


@pytest.mark.parametrize("size", [8, 4000])
async def test_presence_measurements(isolated_resources, pytestconfig, size):
    config = await get_runtime_config()
    await save(*(Person(id=uuid.UUID(int=100 + i), first_name="Synthetic", display_name="Synthetic") for i in range(size)))
    await save(*(Presence(person_id=uuid.UUID(int=100 + i), state=PresenceState.PRESENT if i == size - 1 else PresenceState.EXITED)
        for i in range(size)))
    samples, peaks, counts, materialized_counts, queries = [], [], [], [], []
    conditions = [{"type": "presence", "mode": mode} for mode in ["someone_home", "no_one_home"]]
    conditions += [{"type": "presence", "mode": "person_home", "person_id": str(uuid.UUID(int=100 + size - 1))}]
    for index in range(6):
        sql, loaded, before, materialized = capture()
        try:
            tracemalloc.start()
            started = time.perf_counter()
            async with AsyncSessionLocal() as session:
                answers = [await planner()._condition_matches(session, cond, context(), NOW, config) for cond in conditions * 3]
            elapsed = time.perf_counter() - started
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        finally:
            release(before, materialized)
        assert answers == [True, False, True] * 3
        if index:
            samples.append(elapsed * 1000); peaks.append(peak); counts.append(len(sql)); materialized_counts.append(len(loaded))
            queries = [query for query, _ in sql[:3]]
    async with AsyncSessionLocal() as session:
        indexes = (await session.execute(text("SELECT indexdef FROM pg_indexes WHERE tablename='presence' ORDER BY indexname"))).scalars().all()
        plans = {}
        for mode, query in {"all_rows": "SELECT * FROM presence", "present_exists": "SELECT EXISTS (SELECT 1 FROM presence WHERE state='PRESENT')",
            "person_exists": f"SELECT EXISTS (SELECT 1 FROM presence WHERE state='PRESENT' AND person_id='{uuid.UUID(int=100 + size - 1)}')"}.items():
            plans[mode] = (await session.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query))).scalar_one()
    evidence(pytestconfig, f"presence-{size}", {"rows": size, "warmup": 1, "samples": 5,
        "queries_per_sample": counts, "orm_rows_per_sample": materialized_counts, "answers": answers,
        "python_tracemalloc_peaks_bytes": peaks, "p50_ms": statistics.median(samples), "p95_ms_nearest_rank": max(samples),
        "sql": queries, "indexes": indexes, "plans": plans,
        "qualification": "Nine repeated conditions, no caching. EXISTS returns a scalar but may scan database rows. Python traced allocations are not RSS or wire bytes."})
