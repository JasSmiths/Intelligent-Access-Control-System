"""Independent transaction/receipt contracts on disposable PostgreSQL only.

Collection reuses the synthetic-only, loopback-only guard before app imports.
No startup/lifespan, dispatcher worker, provider transport, migration or live data.
Failures are ordinary failures: no xfail, retry, or diagnostic suppression.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime
import json
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import current_user
from app.api.v1 import integrations as integration_api
from app.db.session import AsyncSessionLocal, get_db_session
from app.models import (AccessDeviceCommandRecord, ActionConfirmation, AuditLog,
                        GateCommandRecord, MaintenanceModeState, NotificationRun,
                        SystemSetting, User)
from app.models.enums import GateCommandState, UserRole
from app.modules.gate.base import CommandDelivery, GateState
from app.services import action_confirmations as confirmations
from app.services import maintenance, settings
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.access_devices import AccessDeviceService
from app.services.auth import AuthError
from app.services.gate_commands import GateCommandCoordinator
from app.services.notification_runs import NotificationRunStore
from app.services.notifications import NotificationService

pytestmark = pytest.mark.asyncio
WATCHDOG_SECONDS = 8


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, WATCHDOG_SECONDS)


async def rows(model):
    async with AsyncSessionLocal() as session:
        return list((await session.scalars(select(model))).all())


async def new_user(*, role=UserRole.ADMIN, active=True):
    user = User(username=f"synthetic-{uuid.uuid4().hex}", full_name="Synthetic Operator",
                password_hash="unused-synthetic-password-hash", role=role, is_active=active)
    async with AsyncSessionLocal() as session:
        session.add(user)
        await session.commit()
    return user


async def seed_setting(key, value):
    category, _, description = settings.DEFAULT_DYNAMIC_SETTINGS[key]
    async with AsyncSessionLocal() as session:
        session.add(SystemSetting(key=key, category=category, value=settings.setting_payload(key, value),
                                  is_secret=key in settings.SECRET_KEYS, description=description))
        await session.commit()


async def seed_maintenance(active):
    async with AsyncSessionLocal() as session:
        session.add(MaintenanceModeState(id=1, is_active=active,
                    enabled_at=datetime.now(UTC) if active else None,
                    enabled_by="Synthetic Operator" if active else None, source="synthetic"))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def owned_resources(isolated_resources, monkeypatch):
    # The shared guard checks an empty settings table before this fixture starts.
    # This file owns its persisted settings and singleton rows and removes them
    # before the next guarded test. All operations remain on the verified DB.
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE maintenance_mode_state, action_confirmations, notification_runs, access_device_command_records CASCADE"))
        await session.commit()
    settings.invalidate_runtime_config_cache()
    service = NotificationService(run_store=NotificationRunStore())
    monkeypatch.setattr(maintenance, "get_notification_service", lambda: service)
    monkeypatch.setattr(confirmations, "emit_audit_log", lambda **_kwargs: None)
    # set_mode(sync_ha=False) must never need this sink; unexpected calls fail.
    monkeypatch.setattr(maintenance, "_sync_home_assistant", AsyncMock(side_effect=AssertionError("Hardware sync is forbidden")))
    try:
        yield service
        assert service.dispatcher._task is None, "Notification worker must never start in these contracts"
    finally:
        settings.invalidate_runtime_config_cache()
        async with AsyncSessionLocal() as session:
            await session.execute(text("TRUNCATE system_settings, maintenance_mode_state, action_confirmations, notification_runs, access_device_command_records CASCADE"))
            await session.commit()


@pytest.mark.parametrize("existing", [True, False])
async def test_settings_audit_failure_rolls_back_value_and_audit(monkeypatch, existing):
    actor = await new_user()
    if existing:
        await seed_setting("app_name", "Before")
    write = settings.write_audit_log

    async def fail_after_flush(session, **kwargs):
        await write(session, **kwargs)
        await session.flush()
        raise RuntimeError("Synthetic audit persistence failure")

    monkeypatch.setattr(settings, "write_audit_log", fail_after_flush)
    with pytest.raises(RuntimeError, match="Synthetic audit persistence"):
        await settings.update_settings({"app_name": "After"}, user=actor, source="authority_contract")
    persisted = await rows(SystemSetting)
    assert [settings.decrypted_value(row) for row in persisted] == (["Before"] if existing else [])
    assert await rows(AuditLog) == []


@pytest.mark.parametrize("existing", [True, False])
async def test_settings_concurrent_admins_serialize_before_values_and_absent_creation(monkeypatch, existing):
    first, second = await new_user(), await new_user()
    if existing:
        await seed_setting("app_name", "Before")
    first_at_audit, release_first, second_started, second_at_audit = (asyncio.Event() for _ in range(4))
    pids = {}
    load_admin, write = settings.load_active_admin, settings.write_audit_log

    async def current_actor(session, user_id, **kwargs):
        actor = await load_admin(session, user_id, **kwargs)
        pids[actor.id] = await session.scalar(select(func.pg_backend_pid()))
        if actor.id == second.id:
            second_started.set()
        return actor

    async def held_audit(session, **kwargs):
        if kwargs["actor_user_id"] == first.id:
            first_at_audit.set()
            await bounded(release_first.wait())
        else:
            second_at_audit.set()
        return await write(session, **kwargs)

    async def observe_real_contention():
        # PostgreSQL's blocking graph is the barrier, not a timing delay.
        async with AsyncSessionLocal() as observer:
            while not second_at_audit.is_set():
                blockers = await observer.scalar(select(func.pg_blocking_pids(pids[second.id])))
                if pids[first.id] in blockers:
                    return True
                await asyncio.sleep(0)
        return False

    monkeypatch.setattr(settings, "load_active_admin", current_actor)
    monkeypatch.setattr(settings, "write_audit_log", held_audit)
    tasks = [asyncio.create_task(settings.update_settings({"app_name": "First"}, user=first, source="authority_contract"))]
    try:
        await bounded(first_at_audit.wait())
        tasks.append(asyncio.create_task(settings.update_settings({"app_name": "Second"}, user=second, source="authority_contract")))
        await bounded(second_started.wait())
        assert await bounded(observe_real_contention()), "The second writer reached its audit using an unserialized before-value"
    finally:
        release_first.set()
        results = await bounded(asyncio.gather(*tasks, return_exceptions=True))
    assert not any(isinstance(result, BaseException) for result in results), results
    persisted = await rows(SystemSetting)
    assert len(persisted) == 1 and settings.decrypted_value(persisted[0]) == "Second"
    audits = {row.actor_user_id: row for row in await rows(AuditLog)}
    assert audits[first.id].diff == {"old": {"app_name": "Before" if existing else None}, "new": {"app_name": "First"}}
    assert audits[second.id].diff == {"old": {"app_name": "First"}, "new": {"app_name": "Second"}}


async def test_settings_secret_stays_encrypted_and_absent_from_public_response_and_audit():
    actor = await new_user()
    secret = "synthetic-dvla-token-never-production"
    response = await settings.update_settings({"dvla_api_key": secret}, user=actor, source="authority_contract")
    record = (await rows(SystemSetting))[0]
    assert record.is_secret and settings.decrypted_value(record) == secret
    assert record.value.get("encrypted") and "plain" not in record.value
    audits = await rows(AuditLog)
    assert len(audits) == 1 and audits[0].metadata_["keys"] == ["dvla_api_key"]
    public = json.dumps({"response": response, "diff": audits[0].diff, "metadata": audits[0].metadata_})
    assert secret not in public and record.value["encrypted"] not in public
    assert next(row["value"] for row in response if row["key"] == "dvla_api_key") is True


async def create_confirmation(actor):
    async with AsyncSessionLocal() as session:
        return await confirmations.create_action_confirmation(session, user=actor, action="gate.open",
                                                               payload={"reason": "Synthetic command"})


async def test_confirmation_creation_audit_failure_leaves_no_usable_confirmation(monkeypatch):
    actor = await new_user()
    write = confirmations.write_audit_log

    async def fail(session, **kwargs):
        await write(session, **kwargs)
        await session.flush()
        raise RuntimeError("Synthetic confirmation audit failure")

    monkeypatch.setattr(confirmations, "write_audit_log", fail)
    with pytest.raises(RuntimeError, match="Synthetic confirmation audit"):
        await create_confirmation(actor)
    assert await rows(ActionConfirmation) == []
    assert await rows(AuditLog) == []


@pytest.mark.parametrize("payload_matches", [True, False])
async def test_confirmation_consumption_or_rejection_audit_failure_rolls_back(monkeypatch, payload_matches):
    actor = await new_user()
    confirmation = await create_confirmation(actor)
    write = confirmations.write_audit_log

    async def fail(session, **kwargs):
        await write(session, **kwargs)
        await session.flush()
        raise RuntimeError("Synthetic confirmation audit failure")

    monkeypatch.setattr(confirmations, "write_audit_log", fail)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError, match="Synthetic confirmation audit"):
            await confirmations.consume_action_confirmation(session, user=actor, action="gate.open",
                payload={"reason": "Synthetic command" if payload_matches else "Changed command"},
                confirmation_token=confirmation["confirmation_token"])
        await session.rollback()
    row = (await rows(ActionConfirmation))[0]
    assert row.consumed_at is None and row.outcome is None
    assert [audit.action for audit in await rows(AuditLog)] == ["real_world_action.confirmation.created"]


async def test_confirmation_success_has_durable_creation_consumption_and_no_plain_token():
    actor = await new_user()
    confirmation = await create_confirmation(actor)
    async with AsyncSessionLocal() as session:
        await confirmations.consume_action_confirmation(session, user=actor, action="gate.open",
            payload={"reason": "Synthetic command"}, confirmation_token=confirmation["confirmation_token"])
    row = (await rows(ActionConfirmation))[0]
    audits = await rows(AuditLog)
    assert row.consumed_at is not None and row.outcome == "consumed"
    assert {audit.action for audit in audits} == {"real_world_action.confirmation.created", "real_world_action.confirmation.consumed"}
    assert all(audit.actor_user_id == actor.id for audit in audits)
    assert all(audit.metadata_["confirmation_id"] == str(row.id) for audit in audits)
    assert confirmation["confirmation_token"] not in json.dumps([audit.metadata_ for audit in audits])
    assert row.token_hash != confirmation["confirmation_token"]


@pytest.mark.parametrize("active", [True, False])
async def test_maintenance_audit_failure_rolls_back_mode_and_notification(monkeypatch, active):
    actor = await new_user()
    await seed_maintenance(not active)
    write = maintenance.write_audit_log

    async def fail(session, **kwargs):
        await write(session, **kwargs)
        await session.flush()
        raise RuntimeError("Synthetic maintenance audit failure")

    monkeypatch.setattr(maintenance, "write_audit_log", fail)
    with pytest.raises(RuntimeError, match="Synthetic maintenance audit"):
        await maintenance.set_mode(active, actor="Synthetic Operator", actor_user_id=str(actor.id),
                                   source="authority_contract", reason="Synthetic change", sync_ha=False)
    assert (await rows(MaintenanceModeState))[0].is_active is not active
    assert await rows(AuditLog) == [] and await rows(NotificationRun) == []


@pytest.mark.parametrize("active", [True, False])
async def test_maintenance_notification_insert_failure_rolls_back_every_participant(monkeypatch, owned_resources, active):
    actor = await new_user()
    await seed_maintenance(not active)
    enqueue = owned_resources.enqueue_in_session

    async def fail_after_insert(session, context, *, dispatch_id):
        await enqueue(session, context, dispatch_id=dispatch_id)
        await session.flush()
        raise RuntimeError("Synthetic notification handoff failure")

    monkeypatch.setattr(owned_resources, "enqueue_in_session", fail_after_insert)
    with pytest.raises(RuntimeError, match="Synthetic notification handoff"):
        await maintenance.set_mode(active, actor="Synthetic Operator", actor_user_id=str(actor.id),
                                   source="authority_contract", reason="Synthetic change", sync_ha=False)
    assert (await rows(MaintenanceModeState))[0].is_active is not active
    assert await rows(AuditLog) == [] and await rows(NotificationRun) == []


@pytest.mark.parametrize("active", [True, False])
async def test_maintenance_commits_notification_with_mode_and_survives_lost_wakeup(monkeypatch, owned_resources, active):
    actor = await new_user()
    await seed_maintenance(not active)
    enqueue = owned_resources.enqueue_in_session
    observed_uncommitted = []

    async def inspect_transaction(session, context, *, dispatch_id):
        result = await enqueue(session, context, dispatch_id=dispatch_id)
        # Another transaction must still see the prior mode and no audit/run.
        observed_uncommitted.append((await rows(MaintenanceModeState))[0].is_active)
        assert await rows(AuditLog) == [] and await rows(NotificationRun) == []
        return result

    monkeypatch.setattr(owned_resources, "enqueue_in_session", inspect_transaction)
    monkeypatch.setattr(owned_resources.dispatcher, "wake", lambda: (_ for _ in ()).throw(RuntimeError("Synthetic lost wakeup")))
    monkeypatch.setattr(maintenance.event_bus, "publish", AsyncMock(side_effect=RuntimeError("Synthetic lost realtime")))
    result = await maintenance.set_mode(active, actor="Synthetic Operator", actor_user_id=str(actor.id),
                                       source="authority_contract", reason="Synthetic change", sync_ha=False)
    assert result["changed"] and observed_uncommitted == [not active]
    assert (await rows(MaintenanceModeState))[0].is_active is active
    audit, run = (await rows(AuditLog))[0], (await rows(NotificationRun))[0]
    assert audit.actor_user_id == actor.id
    assert run.id == uuid.uuid5(audit.id, "maintenance.notification")
    assert run.status == "queued" and run.claim_count == 0 and run.recovery_version == 1
    assert run.trigger_event == ("maintenance_mode_enabled" if active else "maintenance_mode_disabled")
    unchanged = await maintenance.set_mode(active, actor="Synthetic Operator", actor_user_id=str(actor.id),
                                          source="authority_contract", reason="Synthetic repeat", sync_ha=False)
    assert unchanged["changed"] is False
    assert len(await rows(AuditLog)) == len(await rows(NotificationRun)) == 1


async def persisted_command(kind, actor):
    confirmation = await create_confirmation(actor)
    intent = confirmation["confirmation_id"]
    target_id, parent_id = uuid.uuid4(), uuid.uuid4() if kind == "gate" else None
    target = {"target_device_id": str(target_id), "device_key": "synthetic-entry" if kind == "gate" else "synthetic-garage",
              "kind": "gate" if kind == "gate" else "garage_door", "binding_fingerprint": "a" * 64,
              "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": "cover.synthetic"}]}}
    async with AsyncSessionLocal() as session:
        if parent_id:
            session.add(GateCommandRecord(id=parent_id, idempotency_key=f"synthetic:{intent}", source="manual_admin",
                controller="access_devices", reason="Synthetic lost browser response", actor="Synthetic Operator",
                state=GateCommandState.RECONCILIATION_REQUIRED, accepted=False, gate_state="unknown",
                requires_reconciliation=True, command_metadata={"intent_id": intent, "recovery_version": 2,
                    "target_plan": {"targets": [target], "admission_target_device_id": str(target_id)}}))
            await session.flush()
        journal = AccessDeviceCommandJournal()
        claim = await journal.claim(session, target=target, action="open", intent_id=intent,
                                    operation_key=intent, gate_command_id=str(parent_id) if parent_id else None, expires_at=None)
        await session.commit()
    async with AsyncSessionLocal() as session:
        assert await journal.begin_attempt(session, claim, provider="home_assistant", external_id="cover.synthetic")
        await session.commit()
    child = await journal.finish_attempt(claim, delivery=CommandDelivery.UNKNOWN, state=GateState.UNKNOWN,
                                         detail="Synthetic provider response unavailable")
    return intent, parent_id or child.id, child.id


async def receipt_snapshot():
    async with AsyncSessionLocal() as session:
        result = []
        for model in (GateCommandRecord, AccessDeviceCommandRecord, ActionConfirmation, AuditLog):
            data = (await session.execute(select(model.__table__).order_by(model.id))).mappings().all()
            result.append([dict(row) for row in data])
        return result


def inert_receipt_app(actor):
    app = FastAPI()
    app.include_router(integration_api.router, prefix="/api/v1/integrations")

    async def authenticated_user(session: AsyncSession = Depends(get_db_session)):
        user = await session.get(User, actor.id)
        if user is None or not user.is_active:
            raise AuthError()
        return user

    # Authentication transport is replaced with a real persisted synthetic user;
    # the actual current-Admin dependency, route and receipt owners all run.
    app.dependency_overrides[current_user] = authenticated_user
    return app


@pytest.mark.parametrize("kind", ["gate", "cover"])
async def test_actual_asgi_intent_lookup_recovers_unknown_receipt_without_writes_or_provider_io(monkeypatch, kind):
    actor = await new_user()
    intent, command_id, child_id = await persisted_command(kind, actor)
    before = await receipt_snapshot()
    forbidden = AsyncMock(side_effect=AssertionError("Receipt reads must never execute or observe hardware"))
    monkeypatch.setattr(GateCommandCoordinator, "execute_open", forbidden)
    monkeypatch.setattr(AccessDeviceService, "command_device", forbidden)
    monkeypatch.setattr(AccessDeviceService, "_observe_target", forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=inert_receipt_app(actor)),
                                base_url="http://synthetic.invalid", trust_env=False) as client:
        response = await client.get(f"/api/v1/integrations/{kind}/commands", params={"intent_id": intent})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["delivery"] == "unknown" and payload["requires_reconciliation"] is True
        assert payload["command_id"] == str(command_id)
        if kind == "gate":
            assert payload["target_receipts"][0]["command_id"] == str(child_id)
            assert payload["admission_verified"] is False
        direct = await client.get(f"/api/v1/integrations/{kind}/commands/{command_id}")
        assert direct.status_code == 200 and direct.json() == payload
        absent = await client.get(f"/api/v1/integrations/{kind}/commands", params={"intent_id": str(uuid.uuid4())})
        assert absent.status_code == 404
    assert await receipt_snapshot() == before
    forbidden.assert_not_awaited()


@pytest.mark.parametrize("kind", ["gate", "cover"])
@pytest.mark.parametrize("active,role,expected", [(True, UserRole.STANDARD, 403), (False, UserRole.ADMIN, 401)])
async def test_receipt_routes_require_current_active_admin(kind, active, role, expected):
    owner = await new_user()
    intent, _, _ = await persisted_command(kind, owner)
    reader = await new_user(role=role, active=active)
    before = await receipt_snapshot()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=inert_receipt_app(reader)),
                                base_url="http://synthetic.invalid", trust_env=False) as client:
        response = await client.get(f"/api/v1/integrations/{kind}/commands", params={"intent_id": intent})
    assert response.status_code == expected
    assert await receipt_snapshot() == before
