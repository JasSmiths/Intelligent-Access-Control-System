"""PostgreSQL recovery acceptance. Run only in the isolated harness."""

import asyncio
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI

assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
assert "@127.0.0.1:5432/iacs_p1_" in os.environ.get("IACS_DATABASE_URL", "")

from app.db.session import AsyncSessionLocal, engine
from app.models import NotificationRun
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services import notifications as owner
from app.services.notifications import NotificationService, NotificationActionOutcome
from app.services.notification_runs import NotificationRunStore, ClaimLost, run_summary
from app.api.v1 import notifications as api
from app.api.dependencies import current_user
from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def isolated(monkeypatch):
    async with AsyncSessionLocal() as s:
        await s.execute(delete(NotificationRun))
        await s.commit()

    async def config():
        return SimpleNamespace()

    async def publish(*args, **kwargs):
        pass

    monkeypatch.setattr(owner, "get_runtime_config", config)
    monkeypatch.setattr(owner.event_bus, "publish", publish)
    yield
    await engine.dispose()


def context():
    return NotificationContext(
        event_type="integration_test", subject="Synthetic recovery check", severity="info", facts={}
    )


def rules(count=2):
    return [
        {
            "id": "synthetic-rule",
            "name": "Synthetic",
            "trigger_event": "integration_test",
            "conditions": [],
            "is_active": True,
            "actions": [
                {
                    "id": f"a{i}",
                    "type": "in_app",
                    "title_template": "Synthetic",
                    "message_template": "test",
                }
                for i in range(count)
            ],
        }
    ]


async def queued(store, count=2, identity=None):
    return await store.create(
        owner.notification_context_payload(context()), rules_override=rules(count), run_id=identity
    )


async def expire(identity):
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(NotificationRun)
            .where(NotificationRun.id == identity)
            .values(lease_expires_at=text("clock_timestamp() - interval '1 second'"))
        )
        await s.commit()


async def test_lost_wakeup_recovers_from_database(monkeypatch):
    service = NotificationService()

    async def failed_publish(*args):
        raise RuntimeError("fake realtime unavailable")

    monkeypatch.setattr(owner.event_bus, "publish", failed_publish)
    # Creation does not need a working realtime callback.
    result = await service.enqueue_notification(context())
    assert result.title == context().subject
    async with AsyncSessionLocal() as s:
        row = await s.scalar(select(NotificationRun))
    assert row.status == "queued"
    assert await service.dispatcher.run_once()
    row = await service.run_store.get(row.id)
    assert row.status == "skipped"
    assert row.finished_at is not None


async def test_competing_workers_and_duplicate_identity_send_once(monkeypatch):
    store = NotificationRunStore()
    service = NotificationService(run_store=store)
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def deliver(action, *_args):
        calls.append(action["id"])
        entered.set()
        await release.wait()
        return NotificationActionOutcome(delivered=True)

    monkeypatch.setattr(service, "_deliver_action", deliver)
    identity = await queued(store, 1)
    first = asyncio.create_task(service.dispatcher.run_once(identity))
    await entered.wait()
    assert not await service.dispatcher.run_once(identity)
    assert await queued(store, 1, identity) == identity
    release.set()
    await first
    assert not await service.dispatcher.run_once(identity)
    assert calls == ["a0"]
    row = await store.get(identity)
    assert row.status == "provider_accepted" and row.delivered_count == 1


async def test_claim_lock_is_exclusive():
    store = NotificationRunStore()
    identity = await queued(store)
    claims = await asyncio.gather(*(store.claim(identity) for _ in range(6)))
    assert sum(row is not None for row in claims) == 1


async def test_expired_preparation_resumes_and_fences_old_worker():
    store = NotificationRunStore()
    identity = await queued(store, 1)
    first = await store.claim(identity)
    await expire(identity)
    second = await store.claim(identity)
    assert second.claim_token != first.claim_token
    with pytest.raises(ClaimLost):
        await store.save_plan(identity, first.claim_token, [])
    await store.interrupt(identity, second.claim_token)
    assert await NotificationService(run_store=store).dispatcher.run_once(identity)
    assert (await store.get(identity)).delivered_count == 1


async def test_cancelled_preparation_requeues_without_calls(monkeypatch):
    service = NotificationService()
    identity = await queued(service.run_store)
    entered = asyncio.Event()

    async def prepare(row):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "prepare_delivery_plan", prepare)
    task = asyncio.create_task(service.dispatcher.run_once(identity))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await service.run_store.get(identity)
    assert row.status == "queued" and row.delivery_plan is None


async def test_cancelled_provider_is_unknown_and_never_retried(monkeypatch):
    service = NotificationService()
    identity = await queued(service.run_store)
    entered = asyncio.Event()
    calls = []

    async def deliver(action, *_args):
        calls.append(action["id"])
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_deliver_action", deliver)
    task = asyncio.create_task(service.dispatcher.run_once(identity))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await service.run_store.get(identity)
    assert row.status == "review_required"
    assert [x["state"] for x in row.delivery_plan] == ["unknown", "pending"]
    assert not await service.dispatcher.run_once(identity)
    assert calls == ["a0"]


async def test_crash_during_attempt_is_unknown_on_lease_expiry():
    store = NotificationRunStore()
    service = NotificationService(run_store=store)
    identity = await queued(store)
    row = await store.claim(identity)
    await store.save_plan(identity, row.claim_token, await service.prepare_delivery_plan(row))
    await store.begin_action(identity, row.claim_token, 0)
    await expire(identity)
    assert await store.claim(identity) is None
    row = await store.get(identity)
    assert row.status == "review_required" and row.failed_count == 1


async def test_accepted_checkpoint_survives_cancelled_enrichment_and_plan_edit(monkeypatch):
    service = NotificationService()
    identity = await queued(service.run_store)
    entered = asyncio.Event()
    calls = []

    async def deliver(action, *_args):
        calls.append((action["id"], action["message"]))
        return NotificationActionOutcome(delivered=True)

    async def publish(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_deliver_action", deliver)
    monkeypatch.setattr(service, "publish_planned_outcome", publish)
    task = asyncio.create_task(service.dispatcher.run_once(identity))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(NotificationRun)
            .where(NotificationRun.id == identity)
            .values(rules_override=rules(4))
        )
        await s.commit()

    async def noop(*args):
        pass

    monkeypatch.setattr(service, "publish_planned_outcome", noop)
    assert await service.dispatcher.run_once(identity)
    assert calls == [("a0", "test"), ("a1", "test")]
    row = await service.run_store.get(identity)
    assert row.delivered_count == 2 and len(row.delivery_plan) == 2


async def test_checkpoint_failure_after_send_never_retries(monkeypatch):
    service = NotificationService()
    identity = await queued(service.run_store, 1)
    calls = []

    async def deliver(*args):
        calls.append(1)
        return NotificationActionOutcome(delivered=True)

    async def failed_finish(*args, prepare_output=None):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(service, "_deliver_action", deliver)
    monkeypatch.setattr(service.run_store, "finish_action", failed_finish)
    with pytest.raises(RuntimeError, match="commit failure"):
        await service.dispatcher.run_once(identity)
    assert (await service.run_store.get(identity)).status == "review_required"
    assert not await service.dispatcher.run_once(identity)
    assert calls == [1]


async def test_partial_delivery_metadata_retained_without_retry(monkeypatch):
    service = NotificationService()
    calls = []

    async def deliver(action, *_args):
        calls.append(action["id"])
        return NotificationActionOutcome(
            delivered=True,
            reason="delivered_with_failures",
            metadata={
                "partial_failure": True,
                "failure_count": 1,
                "failures": ["private provider error must not enter journal"],
            },
        )

    monkeypatch.setattr(service, "_deliver_action", deliver)
    result = await service.send_notification_now_with_result(context(), rules_override=rules(1))
    assert result.status == "sent"
    row = await service.run_store.get(uuid.UUID(result.run_id))
    assert row.delivered_count == 1 and row.delivery_plan[0]["partial_failure"]
    assert row.delivery_plan[0]["failure_count"] == 1
    assert "private provider error" not in str(row.delivery_plan)
    await service.dispatcher.run_once(row.id)
    assert calls == ["a0"]


@pytest.mark.parametrize(
    "exception", [NotificationDeliveryError("provider uncertain"), ValueError("unexpected")]
)
async def test_provider_errors_are_one_attempt_and_preserve_prior_success(monkeypatch, exception):
    service = NotificationService()
    calls = []

    async def deliver(action, *_args):
        calls.append(action["id"])
        if action["id"] == "a1":
            raise exception
        return NotificationActionOutcome(delivered=True)

    monkeypatch.setattr(service, "_deliver_action", deliver)
    result = await service.send_notification_now_with_result(context(), rules_override=rules(3))
    assert result.status == "review_required"
    assert result.delivered_count == 1 and result.failed_count == 1
    row = await service.run_store.get(uuid.UUID(result.run_id))
    assert [x["state"] for x in row.delivery_plan] == ["accepted", "unknown", "pending"]
    await service.dispatcher.run_once(row.id)
    assert calls == ["a0", "a1"]


async def test_action_timeout_is_unknown(monkeypatch):
    from app.services import notification_dispatch

    monkeypatch.setattr(notification_dispatch, "ACTION_TIMEOUT_SECONDS", 0.01)
    service = NotificationService()

    async def deliver(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_deliver_action", deliver)
    with pytest.raises(NotificationDeliveryError, match="provider_outcome_unknown"):
        await service.send_notification_now_with_result(
            context(), rules_override=rules(1), raise_on_failure=True
        )


async def test_stale_and_repeated_preparation_require_review():
    store = NotificationRunStore()
    stale = await queued(store)
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(NotificationRun)
            .where(NotificationRun.id == stale)
            .values(queued_at=text("clock_timestamp() - interval '16 minutes'"))
        )
        await s.commit()
    assert await store.claim(stale) is None
    assert (await store.get(stale)).review_reason == "dispatch_age_exceeded"
    repeated = await queued(store)
    for _ in range(3):
        row = await store.claim(repeated)
        await store.interrupt(repeated, row.claim_token)
    assert await store.claim(repeated) is None
    assert (await store.get(repeated)).review_reason == "preparation_attempts_exhausted"


async def test_historical_rows_are_visible_but_never_claimed():
    store = NotificationRunStore()
    identity = await queued(store)
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(NotificationRun)
            .where(NotificationRun.id == identity)
            .values(recovery_version=None)
        )
        await s.commit()
    assert await store.claim(identity) is None
    row = await store.get(identity)
    assert row.status == "queued"
    assert run_summary(row)["review_reason"] == "historical_unfinished"


async def test_readonly_recovery_routes_require_admin_and_omit_payloads():
    store = NotificationRunStore()
    identity = await queued(store)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/notifications")
    actor = SimpleNamespace(id=uuid.uuid4(), role=UserRole.STANDARD, is_active=True)
    app.dependency_overrides[current_user] = lambda: actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        assert (await client.get("/api/v1/notifications/runs")).status_code == 403
        actor.role = UserRole.ADMIN
        response = await client.get(f"/api/v1/notifications/runs/{identity}")
        assert response.status_code == 200
        assert "context" not in response.json() and "rules_override" not in response.json()
        assert (await client.get("/api/v1/notifications/runs?limit=101")).status_code == 422


async def test_worker_start_stop_cleans_task_and_recovers_queued_work(monkeypatch):
    from app.services import notification_dispatch

    monkeypatch.setattr(notification_dispatch, "POLL_SECONDS", 0.01)
    service = NotificationService()
    identity = await queued(service.run_store, 1)
    await service.start()
    try:
        async with asyncio.timeout(5):
            while (await service.run_store.get(identity)).status in ("queued", "processing"):
                await asyncio.sleep(0.01)
    finally:
        await service.stop()
    assert service.dispatcher._task is None
    assert (await service.run_store.get(identity)).status == "provider_accepted"


async def test_real_old_schema_upgrade_preserves_history_and_guards_downgrade():
    spec = importlib.util.spec_from_file_location(
        "recovery_migration",
        "/workspace/backend/alembic/versions/20260912_0001_notification_recovery.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with engine.begin() as conn:
        schema = "migration_" + uuid.uuid4().hex
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
        await conn.execute(text(f"SET LOCAL search_path TO {schema}"))
        await conn.execute(
            text("CREATE TABLE notification_runs (id integer, status text, context jsonb)")
        )
        await conn.execute(
            text("CREATE TABLE gate_malfunction_notification_outbox (id integer, status text)")
        )
        await conn.execute(
            text(
                "INSERT INTO notification_runs VALUES (1, 'queued', jsonb_build_object('history', true)), (2, 'processing', '{}')"
            )
        )

        def upgrade(c):
            with Operations.context(MigrationContext.configure(c)):
                migration.upgrade()

        def downgrade(c):
            with Operations.context(MigrationContext.configure(c)):
                migration.downgrade()

        await conn.run_sync(upgrade)
        rows = (
            await conn.execute(
                text("SELECT status, context, recovery_version FROM notification_runs ORDER BY id")
            )
        ).all()
        assert rows == [("queued", {"history": True}, None), ("processing", {}, None)]
        await conn.run_sync(downgrade)
        await conn.run_sync(upgrade)
        await conn.execute(text("UPDATE notification_runs SET recovery_version=1 WHERE id=1"))
        with pytest.raises(Exception, match="Notification recovery records exist"):
            async with conn.begin_nested():
                await conn.run_sync(downgrade)
        assert (await conn.execute(text("SELECT count(*) FROM notification_runs"))).scalar() == 2
        await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))


@pytest.mark.parametrize("historical", [False, True])
async def test_gate_notification_adapter_holds_history_and_never_retries_unknown(
    monkeypatch, historical
):
    from datetime import UTC, datetime
    from app.models import GateMalfunctionState, GateMalfunctionNotificationOutbox
    from app.services import gate_malfunctions as gates

    now = datetime.now(UTC)
    malfunction = GateMalfunctionState(
        gate_entity_id="cover.synthetic_" + uuid.uuid4().hex, opened_at=now, declared_at=now
    )
    async with AsyncSessionLocal() as s:
        s.add(malfunction)
        await s.flush()
        outbox = GateMalfunctionNotificationOutbox(
            malfunction_id=malfunction.id,
            trigger=owner.GATE_MALFUNCTION_EVENT_TYPE,
            stage="initial",
            subject="Synthetic",
            severity="warning",
            occurred_at=now,
            status="pending",
            recovery_version=None if historical else 1,
        )
        s.add(outbox)
        await s.commit()
    gate_service = gates.GateMalfunctionService()
    service = NotificationService()
    calls = []

    async def facts(*args, **kw):
        return {}

    async def prepare(row):
        return [
            {
                "state": "pending",
                "rule": {"id": "synthetic"},
                "action": {"id": "one", "type": "in_app", "title": "", "message": ""},
            }
        ]

    async def deliver(*args):
        calls.append(1)
        raise NotificationDeliveryError("ambiguous")

    monkeypatch.setattr(gate_service, "_notification_facts", facts)
    monkeypatch.setattr(service, "prepare_delivery_plan", prepare)
    monkeypatch.setattr(service, "_deliver_action", deliver)
    monkeypatch.setattr(gates, "get_notification_service", lambda: service)
    await gate_service._process_notification_id(outbox.id)
    await gate_service._process_notification_id(outbox.id)
    async with AsyncSessionLocal() as s:
        stored = await s.get(GateMalfunctionNotificationOutbox, outbox.id)
    assert stored.status == ("pending" if historical else "review_required")
    assert calls == ([] if historical else [1])


async def test_sync_reservation_cannot_be_claimed_by_polling():
    store = NotificationRunStore()
    identity, claimed = await store.reserve(
        owner.notification_context_payload(context()), rules_override=rules(1)
    )
    assert claimed.status == "processing"
    assert not await store.claim(identity)
    assert await NotificationService(run_store=store).dispatcher.run_once(identity, claimed=claimed)
    assert (await store.get(identity)).status == "provider_accepted"


async def test_failure_event_follows_durable_unknown_checkpoint(monkeypatch):
    service = NotificationService()
    identity = await queued(service.run_store, 1)
    observed = []

    async def deliver(*args):
        raise NotificationDeliveryError("fake uncertainty")

    async def publish(event_type, payload):
        if event_type == "notification.failed":
            row = await service.run_store.get(identity)
            observed.append((row.status, payload["requires_review"]))

    monkeypatch.setattr(service, "_deliver_action", deliver)
    monkeypatch.setattr(owner.event_bus, "publish", publish)
    await service.dispatcher.run_once(identity)
    assert observed == [("review_required", True)]


async def test_outbox_events_cannot_override_durable_delivery_state(monkeypatch):
    from datetime import UTC, datetime
    from app.models import GateMalfunctionState, GateMalfunctionNotificationOutbox
    from app.services import gate_malfunctions as gates

    now = datetime.now(UTC)
    malfunction = GateMalfunctionState(
        gate_entity_id="cover.synthetic_" + uuid.uuid4().hex, opened_at=now, declared_at=now
    )
    async with AsyncSessionLocal() as s:
        s.add(malfunction)
        await s.flush()
        outbox = GateMalfunctionNotificationOutbox(
            malfunction_id=malfunction.id,
            trigger=owner.GATE_MALFUNCTION_EVENT_TYPE,
            stage="initial",
            subject="Synthetic",
            severity="warning",
            occurred_at=now,
            status="review_required",
            recovery_version=1,
        )
        s.add(outbox)
        await s.commit()
    service = gates.GateMalfunctionService()

    async def noop(*args, **kw):
        return {}

    for name in ("_add_timeline_event", "_update_trace", "_serialize_malfunction"):
        monkeypatch.setattr(service, name, noop)
    await service._record_notification_dispatch(
        "notification.sent",
        {
            "malfunction_id": str(malfunction.id),
            "malfunction_stage": "initial",
            "event_type": owner.GATE_MALFUNCTION_EVENT_TYPE,
            "delivered": True,
        },
    )
    async with AsyncSessionLocal() as s:
        assert (
            await s.get(GateMalfunctionNotificationOutbox, outbox.id)
        ).status == "review_required"


async def test_late_worker_cannot_finalize_or_start_next_action(monkeypatch):
    service = NotificationService()
    identity = await queued(service.run_store, 2)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def deliver(action, *_args):
        calls.append(action["id"])
        entered.set()
        await release.wait()
        return NotificationActionOutcome(delivered=True)

    monkeypatch.setattr(service, "_deliver_action", deliver)
    first = asyncio.create_task(service.dispatcher.run_once(identity))
    await entered.wait()
    await expire(identity)
    assert not await service.dispatcher.run_once(identity)
    release.set()
    await first
    row = await service.run_store.get(identity)
    assert row.status == "review_required" and row.delivered_count == 0
    assert calls == ["a0"]


async def test_failed_attempt_checkpoint_makes_zero_provider_calls(monkeypatch):
    service = NotificationService()
    identity = await queued(service.run_store, 1)
    calls = []

    async def begin(*args, **kwargs):
        raise RuntimeError("synthetic checkpoint unavailable")

    async def deliver(*args):
        calls.append(1)
        return NotificationActionOutcome(delivered=True)

    monkeypatch.setattr(service.run_store, "begin_action", begin)
    monkeypatch.setattr(service, "_deliver_action", deliver)
    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        await service.dispatcher.run_once(identity)
    row = await service.run_store.get(identity)
    assert row.status == "queued" and row.delivery_plan[0]["state"] == "pending"
    assert calls == []
