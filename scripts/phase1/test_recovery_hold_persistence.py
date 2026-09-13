"""Real hold application/authentication contracts on disposable PostgreSQL only.

Collection fails before app imports outside the phase1 synthetic namespace. The
application lifespan and routes are real; migrations, workers, telemetry writes
and provider calls are forbidden. Schema-negative cases own separate databases
and never alter the harness database's revision. No runtime execution on import.
"""
from test_recovery_boundaries import DATABASE_NAME, isolated_resources as isolated_resources

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import os
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

import asyncpg
import httpx
import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main, recovery_hold
from app.core import recovery_hold as hold_policy
from app.db.session import AsyncSessionLocal, engine
from app.models import (
    AccessDeviceCommandRecord, AlfredApproval, AlfredFeedback, AutomationRun,
    ChatSession, DependencyUpdateJob, GateCommandRecord, NotificationRun,
    ProcessedMessagingMessage, RevokedAuthToken, User,
)
from app.models.enums import GateCommandState, UserRole
from app.services import auth, settings
from app.services.access_devices import AccessDeviceService
from app.services.gate_commands import GateCommandCoordinator

pytestmark = pytest.mark.asyncio
PASSWORD = "Synthetic-hold-password-only-123!"
EXTRA_TABLES = "processed_messaging_messages, dependency_update_jobs, alfred_feedback"


@pytest_asyncio.fixture(autouse=True)
async def hold_resources(isolated_resources, monkeypatch):
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE " + EXTRA_TABLES + " CASCADE"))
        await session.commit()
    settings.invalidate_runtime_config_cache()
    monkeypatch.setattr(hold_policy.settings, "recovery_hold", True)
    assert not main.app.dependency_overrides, "Real hold auth requires no dependency overrides"

    forbidden_sync = Mock(side_effect=AssertionError("Hold invoked a worker, migration or telemetry producer"))
    forbidden_async = AsyncMock(side_effect=AssertionError("Hold invoked bootstrap, worker or external effects"))
    monkeypatch.setattr(main, "init_database", forbidden_async)
    for name in vars(main):
        if name.startswith("get_") and name.endswith("_service"):
            monkeypatch.setattr(main, name, forbidden_sync)
    monkeypatch.setattr(main.event_bus, "start", forbidden_async)
    monkeypatch.setattr(main.event_bus, "publish", forbidden_async)
    monkeypatch.setattr(main.alfred_feedback_service, "start", forbidden_async)
    monkeypatch.setattr(main.telemetry, "start_trace", forbidden_sync)
    monkeypatch.setattr(main.telemetry, "_schedule", forbidden_sync)
    monkeypatch.setattr(GateCommandCoordinator, "execute_open", forbidden_async)
    monkeypatch.setattr(AccessDeviceService, "command_device", forbidden_async)
    monkeypatch.setattr(AccessDeviceService, "_observe_target", forbidden_async)
    for name in ("upgrade", "downgrade", "stamp"):
        monkeypatch.setattr(alembic_command, name, forbidden_sync)
    try:
        yield
        forbidden_sync.assert_not_called()
        forbidden_async.assert_not_called()
    finally:
        settings.invalidate_runtime_config_cache()
        async with AsyncSessionLocal() as session:
            await session.execute(text("TRUNCATE " + EXTRA_TABLES + " CASCADE"))
            await session.commit()


async def snapshot(*, auth_mutations=False):
    """All public table rows, including exact timestamps; never log payloads."""
    result = {}
    async with AsyncSessionLocal() as session:
        tables = (await session.scalars(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ))).all()
        for name in tables:
            assert re.fullmatch(r"[a-z_][a-z0-9_]*", name), "Unexpected synthetic table identifier"
            if auth_mutations and name == "revoked_auth_tokens":
                continue
            projection = "to_jsonb(t)"
            if auth_mutations and name == "users":
                projection += " - 'last_login_at' - 'updated_at'"
            result[name] = await session.scalar(text(
                f'SELECT COALESCE(jsonb_agg(payload ORDER BY payload::text), \'[]\'::jsonb) '
                f'FROM (SELECT {projection} AS payload FROM public."{name}" t) snapshot_rows'
            ))
    return result


def unchanged(before, after):
    assert before.keys() == after.keys(), "Hold changed the public table set"
    for name in before:
        assert before[name] == after[name], f"Hold changed rows or timestamps in {name}"


@asynccontextmanager
async def sql_read_boundary(*, allow_auth=False):
    """Catch even rolled-back domain writes during lifespan/request handling."""
    mutations = []

    def inspect_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        verb = statement.lstrip().split(None, 1)[0].upper()
        if verb in {"SELECT", "SHOW"}:
            return
        auth_write = re.match(
            r"\s*(?:UPDATE\s+users\b|INSERT\s+INTO\s+revoked_auth_tokens\b|DELETE\s+FROM\s+revoked_auth_tokens\b)",
            statement, re.IGNORECASE,
        )
        assert allow_auth and auth_write, "Hold attempted non-read SQL outside explicit authentication"
        mutations.append(verb)

    event.listen(engine.sync_engine, "before_cursor_execute", inspect_sql)
    try:
        yield mutations
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", inspect_sql)


@pytest_asyncio.fixture
async def retained():
    now = datetime.now(UTC)
    old = now - timedelta(days=1)
    password_hash = await auth.hash_password_async(PASSWORD)
    users = {name: User(username="synthetic-hold-" + name, full_name="Synthetic Hold User",
        password_hash=password_hash, role=UserRole.STANDARD if name == "standard" else UserRole.ADMIN,
        is_active=True) for name in ("admin", "other_admin", "standard", "revoked", "stale", "inactive")}
    chat = ChatSession(title="Synthetic retained confirmation", context={})
    async with AsyncSessionLocal() as session:
        session.add_all([*users.values(), chat])
        await session.flush()
        admin = users["admin"]
        approvals = {}
        for status in ("pending", "claimed", "completed", "unknown"):
            approvals[status] = AlfredApproval(id="confirm-" + uuid.uuid4().hex,
                operation_id=uuid.uuid4(), session_id=chat.id, requester_user_id=admin.id,
                requester_auth_session_version=admin.auth_session_version, status=status,
                payload={"tool_name": "open_gate", "provider": "local", "preview_output": {"target": "Synthetic gate"}},
                result={"turn": {"session_id": str(chat.id), "provider": "local", "text": "Retained synthetic result",
                    "tool_results": [], "attachments": []}} if status in {"completed", "unknown"} else None,
                created_at=old, updated_at=old, claimed_at=old if status == "claimed" else None,
                expires_at=now + timedelta(hours=1))
        target = {"target_device_id": str(uuid.uuid4()), "device_key": "synthetic_entry",
            "kind": "gate", "binding_fingerprint": "a" * 64, "binding_snapshot": {"providers": []}}
        gate = GateCommandRecord(id=uuid.uuid4(), idempotency_key="synthetic-hold-gate", source="manual_admin",
            controller="access_devices", reason="Synthetic interrupted request", state=GateCommandState.LEASED,
            accepted=None, gate_state="unknown", lease_token="synthetic-retained-token", lease_expires_at=old,
            leased_at=old, started_at=old, requires_reconciliation=True, created_at=old, updated_at=old,
            command_metadata={"intent_id": str(uuid.uuid4()), "recovery_version": 2,
                "target_plan": {"targets": [target], "admission_target_device_id": target["target_device_id"]}})
        session.add(gate)
        await session.flush()
        commands = {}
        for kind in ("gate", "cover"):
            commands[kind] = AccessDeviceCommandRecord(id=uuid.uuid4(),
                gate_command_id=gate.id if kind == "gate" else None,
                target_device_id=uuid.UUID(target["target_device_id"]) if kind == "gate" else uuid.uuid4(),
                device_key=target["device_key"] if kind == "gate" else "synthetic_garage", action="open",
                intent_id=gate.command_metadata["intent_id"] if kind == "gate" else str(uuid.uuid4()),
                idempotency_key="synthetic-hold-" + kind, state="unknown", binding_snapshot=target["binding_snapshot"],
                binding_fingerprint="a" * 64, lease_token="synthetic-retained-token", lease_expires_at=old,
                attempted_at=old, accepted=None, gate_state="unknown", created_at=old, updated_at=old)
        notifications, automations = [], []
        for state in ("queued", "processing"):
            notifications.append(NotificationRun(trigger_event="synthetic.hold", subject="Synthetic retained notification",
                severity="info", status=state, recovery_version=1, context={}, delivery_plan=[], queued_at=old,
                started_at=old if state == "processing" else None, claim_token=uuid.uuid4() if state == "processing" else None,
                lease_expires_at=old if state == "processing" else None, created_at=old, updated_at=old))
            automations.append(AutomationRun(trigger_key="time.once", status=state, recovery_version=1,
                occurrence_key="synthetic-hold-" + state, context={"version": 1}, started_at=old, queued_at=old,
                claim_token=uuid.uuid4() if state == "processing" else None, lease_expires_at=old if state == "processing" else None,
                action_plan=[{"index": 0, "action": {"id": "synthetic-action", "type": "gate.open"},
                    "operation_id": str(uuid.uuid4()), "state": "attempting" if state == "processing" else "pending"}],
                created_at=old, updated_at=old))
        incoming = [ProcessedMessagingMessage(provider="whatsapp", provider_message_id="synthetic-hold-" + state,
            provider_channel_id="synthetic-channel", author_provider_id="synthetic-author", received_at=old,
            recovery_version=1, state=state, envelope={"message": {"type": "text", "text": {"body": "Synthetic retained input"}}},
            routing_context={"kind": "admin", "user_id": str(admin.id), "auth_version": admin.auth_session_version},
            available_at=old, claim_token=uuid.uuid4() if state == "processing" else None,
            claimed_at=old if state == "processing" else None, lease_expires_at=old if state == "processing" else None,
            created_at=old, updated_at=old) for state in ("received", "processing")]
        feedback = [AlfredFeedback(rating="down", actor_user_id=admin.id, actor_role="admin", session_id=chat.id,
            status=state, original_user_prompt="Synthetic question", original_assistant_response="Synthetic answer",
            reason="Synthetic retained review", created_at=old, updated_at=old) for state in ("queued", "analyzing")]
        jobs = [DependencyUpdateJob(kind="apply", status=state, actor="Synthetic Admin", actor_user_id=admin.id,
            phase="retained", started_at=old if state == "running" else None,
            result={"manifest_transaction_id": "synthetic-retained-promotion"},
            created_at=old, updated_at=old) for state in ("queued", "running")]
        session.add_all([*approvals.values(), *commands.values(), *notifications, *automations, *incoming, *feedback, *jobs])
        await session.commit()
    tokens = {name: (await auth.create_access_token(user))[0] for name, user in users.items()}
    async with AsyncSessionLocal() as session:
        await auth.revoke_access_token(session, tokens["revoked"])
        stale = await session.get(User, users["stale"].id)
        stale.auth_session_version += 1
        inactive = await session.get(User, users["inactive"].id)
        inactive.is_active = False
        await session.commit()
    return SimpleNamespace(users=users, tokens=tokens, chat=chat, approvals=approvals, gate=gate,
        commands=commands, notifications=notifications, automations=automations)


def recovery_paths(data):
    return [
        "/api/v1/integrations/gate/commands",
        f"/api/v1/integrations/gate/commands/{data.gate.id}",
        f"/api/v1/integrations/gate/commands?intent_id={data.gate.command_metadata['intent_id']}",
        "/api/v1/integrations/cover/commands",
        f"/api/v1/integrations/cover/commands/{data.commands['cover'].id}",
        f"/api/v1/integrations/cover/commands?intent_id={data.commands['cover'].intent_id}",
        "/api/v1/automations/runs", f"/api/v1/automations/runs/{data.automations[1].id}",
        "/api/v1/notifications/runs", f"/api/v1/notifications/runs/{data.notifications[1].id}",
        "/api/v1/notifications/recovery/gate-outbox",
        "/api/v1/ai/training/feedback", "/api/v1/ai/training/lessons",
        "/api/v1/ai/training/eval-examples", "/api/v1/ai/training/eval-export",
        "/api/v1/ai/chat/approvals",
        *[f"/api/v1/ai/chat/approvals/{row.id}?session_id={data.chat.id}" for row in data.approvals.values()],
    ]


async def test_actual_hold_lifespan_authenticated_recovery_matrix_preserves_every_retained_row(retained):
    before = await snapshot()
    async with sql_read_boundary(), main.lifespan(main.app):
        assert main.app.state.startup_complete
        for identity in ("admin", "other_admin", "standard", "anonymous", "revoked", "stale", "inactive"):
            headers = {"Authorization": "Bearer " + retained.tokens[identity]} if identity != "anonymous" else {}
            valid = identity in {"admin", "other_admin", "standard"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://synthetic",
                                         trust_env=False, headers=headers) as client:
                for path in recovery_paths(retained):
                    response = await client.get(path)
                    approval = "/chat/approvals" in path
                    expected = 401 if not valid else 403 if identity == "standard" and not approval else 200
                    assert response.status_code == expected, (identity, path, response.status_code)
                    if valid and approval:
                        result = response.json()
                        if "?session_id=" not in path:
                            assert len(result["items"]) == (4 if identity == "admin" else 0)
                        elif identity != "admin":
                            assert result == {"status": "unavailable", "pending_action": None, "result": None}
                        elif retained.approvals["pending"].id in path:
                            assert result["status"] == "pending"
                            assert result["pending_action"]["confirmation_id"] == retained.approvals["pending"].id
                        elif retained.approvals["claimed"].id in path:
                            assert result["status"] == "unknown" and result["result"] is None
                        else:
                            assert result["result"]["text"] == "Retained synthetic result"
                    if expected == 200 and (f"/gate/commands/{retained.gate.id}" in path or
                                            f"/cover/commands/{retained.commands['cover'].id}" in path):
                        assert response.json()["delivery"] == "unknown"
                        assert response.json()["requires_reconciliation"] is True
                me = await client.get("/api/v1/auth/me")
                assert me.status_code == (200 if valid else 401)
                for path in ("/docs", "/openapi.json", "/redoc"):
                    response = await client.get(path)
                    assert response.status_code == (200 if valid else 401)
    assert main.app.state.startup_complete is False
    unchanged(before, await snapshot())


async def test_hold_login_logout_use_real_password_cookie_and_revocation_without_background_writes(retained):
    before = await snapshot(auth_mutations=True)
    async with sql_read_boundary(allow_auth=True) as writes, main.lifespan(main.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://synthetic",
                                     trust_env=False) as client:
            bad = await client.post("/api/v1/auth/login", json={"username": retained.users["admin"].username,
                "password": "Incorrect synthetic password"})
            assert bad.status_code == 401
            login = await client.post("/api/v1/auth/login", json={"username": retained.users["admin"].username,
                "password": PASSWORD})
            assert login.status_code == 200
            cookie_name = (await settings.get_runtime_config()).auth_cookie_name
            token = client.cookies.get(cookie_name)
            assert token
            assert (await client.get("/api/v1/integrations/gate/commands")).status_code == 200
            assert (await client.post("/api/v1/auth/logout")).status_code == 200
            assert client.cookies.get(cookie_name) is None
            # A copied token is revoked too, not merely removed from one browser.
            assert (await client.get("/api/v1/integrations/gate/commands",
                headers={"Authorization": "Bearer " + token})).status_code == 401
            assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert "UPDATE" in writes and "INSERT" in writes
    async with AsyncSessionLocal() as session:
        user = await session.get(User, retained.users["admin"].id)
        assert user.last_login_at is not None
        assert len((await session.scalars(select(RevokedAuthToken))).all()) == 2
    unchanged(before, await snapshot(auth_mutations=True))


async def test_hold_blocks_actual_ingress_mutations_and_websockets_without_changing_retained_work(retained):
    before = await snapshot()
    async with sql_read_boundary(), main.lifespan(main.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://synthetic",
                trust_env=False, headers={"Authorization": "Bearer " + retained.tokens["admin"]}) as client:
            for method, path in (
                ("POST", "/api/v1/integrations/gate/open"), ("POST", "/api/v1/integrations/cover/command"),
                ("POST", "/api/v1/ai/chat/confirm"), ("POST", "/api/v1/ai/feedback"),
                ("POST", "/api/v1/auth/setup"), ("PATCH", "/api/v1/settings"),
                ("POST", "/api/v1/webhooks/ubiquiti/lpr"), ("POST", "/api/v1/webhooks/whatsapp"),
                ("POST", "/api/v1/automations/webhooks/synthetic"),
                ("GET", "/api/v1/integrations/gate/status"), ("GET", "/api/v1/visitor-passes"),
                ("GET", "/api/v1/ai/agent/status"), ("GET", "/api/v1/dependency-updates/status"),
            ):
                response = await client.request(method, path, json={})
                assert response.status_code == 503 and response.json()["recovery_hold"] is True
            for path in ("/", "/health", "/api/v1/health", "/api/v1/health/ready"):
                response = await client.get(path)
                assert response.status_code == (503 if path.endswith("/ready") else 200)
                assert response.json()["ready"] is False and response.json()["recovery_readable"] is True
        for path in ("/api/v1/realtime/ws", "/api/v1/ai/chat/ws"):
            sent = []
            async def send(message):
                sent.append(message)
            receive = AsyncMock(return_value={"type": "websocket.connect"})
            await main.app({"type": "websocket", "asgi": {"version": "3.0"}, "path": path,
                "raw_path": path.encode(), "query_string": b"", "headers": [], "scheme": "ws",
                "server": ("synthetic", 80), "client": ("127.0.0.1", 1), "subprotocols": [], "root_path": ""},
                receive, send)
            assert len(sent) == 1 and sent[0]["type"] == "websocket.close" and sent[0]["code"] == 1008
            receive.assert_not_awaited()
    unchanged(before, await snapshot())


@pytest_asyncio.fixture
async def revision_database(monkeypatch):
    """Own an empty scratch DB; no migrations, production names or forced drops."""
    name = "iacs_p1_hold_" + uuid.uuid4().hex[:16]
    base_url = make_url(os.environ["IACS_DATABASE_URL"])
    admin = await asyncpg.connect(base_url.set(drivername="postgresql").render_as_string(hide_password=False), timeout=10)
    scratch_engine = None
    created = False
    try:
        assert await admin.fetchval("SELECT current_database()") == DATABASE_NAME
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        created = True
        scratch_engine = create_async_engine(base_url.set(database=name))
        factory = async_sessionmaker(scratch_engine, expire_on_commit=False)
        monkeypatch.setattr(recovery_hold, "AsyncSessionLocal", factory)
        yield factory
    finally:
        try:
            if scratch_engine is not None:
                await scratch_engine.dispose()
            if created:
                await admin.execute(f'DROP DATABASE "{name}"')
        finally:
            await admin.close()


@pytest.mark.parametrize("case", ["matching", "mismatched", "empty", "missing_table"])
async def test_actual_source_head_gate_is_read_only_and_refuses_incompatible_schema(revision_database, case):
    root = recovery_hold.Path(recovery_hold.__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    assert heads
    factory = revision_database
    async with factory() as session:
        if case != "missing_table":
            await session.execute(text("CREATE TABLE alembic_version (version_num varchar(64) NOT NULL PRIMARY KEY)"))
            values = heads if case == "matching" else {"synthetic_incompatible_revision"} if case == "mismatched" else set()
            for value in values:
                await session.execute(text("INSERT INTO alembic_version VALUES (:value)"), {"value": value})
            await session.commit()
    # A read-only database transaction refuses hidden DDL/DML without substituting
    # the production schema-verification function or its source-head resolver.
    @asynccontextmanager
    async def readonly_session():
        async with factory() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            yield session

    original = recovery_hold.AsyncSessionLocal
    recovery_hold.AsyncSessionLocal = readonly_session
    try:
        if case == "matching":
            async with main.lifespan(main.app):
                assert main.app.state.startup_complete
        else:
            with pytest.raises(ProgrammingError if case == "missing_table" else RuntimeError):
                async with main.lifespan(main.app):
                    pytest.fail("Incompatible hold source/schema entered request-serving lifespan")
        assert main.app.state.startup_complete is False
    finally:
        recovery_hold.AsyncSessionLocal = original
    async with factory() as session:
        tables = (await session.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))).all()
        assert tables == ([] if case == "missing_table" else ["alembic_version"])
        if case != "missing_table":
            assert set((await session.scalars(text("SELECT version_num FROM alembic_version"))).all()) == values
