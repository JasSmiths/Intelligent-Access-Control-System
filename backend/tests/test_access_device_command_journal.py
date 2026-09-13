"""PostgreSQL target-journal contracts; explicit phase1 synthetic namespace only.

The ordinary unit suite skips these checks. The recovery harness must run this
file explicitly with IACS_RECOVERY_PROBES=synthetic-only and report that outcome.
"""
import asyncio
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal
from app.models import AccessDevice, AccessDeviceCommandRecord, AccessDeviceProviderBinding, GateStateObservation
from app.modules.access_devices.base import AccessDeviceCommandResult, AccessDeviceStateObservation
from app.modules.gate.base import CommandDelivery, GateState
from app.services import access_devices as devices_module
from app.services import maintenance_state
from app.services.access_device_commands import AccessDeviceCommandJournal, device_command_receipt
from app.services.access_devices import AccessDeviceService

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.environ.get("IACS_RECOVERY_PROBES") != "synthetic-only", reason="requires explicit isolated PostgreSQL recovery run")]


@pytest_asyncio.fixture(autouse=True)
async def synthetic_namespace(monkeypatch):
    url = urlsplit(os.environ.get("IACS_DATABASE_URL", ""))
    assert os.environ.get("IACS_ENVIRONMENT") == "testing"
    assert os.environ.get("IACS_AUTH_SECRET_KEY") == "phase1-synthetic-auth-root-never-production"
    assert url.hostname == "127.0.0.1" and url.port == 5432 and url.path.startswith("/iacs_p1_")
    assert url.username == "phase1" and url.password == "synthetic-phase1-only"
    assert {item.name for item in Path("/sys/class/net").iterdir()} == {"lo"}
    assert not Path("/var/run/docker.sock").exists()
    original_connect = socket.socket.connect

    def isolated_connect(sock, address):
        assert isinstance(address, tuple) and address[:2] == ("127.0.0.1", 5432), "non-PostgreSQL socket refused"
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", isolated_connect)
    monkeypatch.setattr(devices_module, "emit_audit_log", lambda **_kwargs: None)
    monkeypatch.setattr(devices_module.telemetry, "record_span", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(devices_module, "COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(devices_module, "CLOSE_COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0)
    yield


def target(identity=None):
    identity = identity or uuid.uuid4()
    return {"target_device_id": str(identity), "device_key": f"synthetic_{identity.hex}", "kind": "gate",
            "binding_fingerprint": "a" * 64,
            "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": "cover.synthetic"},
                                                {"provider": "esphome", "external_id": "synthetic:1"}]}}


async def claim(journal, selected, *, intent=None, action="open"):
    intent = intent or str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        result = await journal.claim(session, target=selected, action=action, intent_id=intent,
                                     operation_key=intent, gate_command_id=None, expires_at=None)
        await session.commit()
        return result


async def begin(journal, owned, *, provider="home_assistant"):
    async with AsyncSessionLocal() as session:
        external_id = next(item["external_id"] for item in owned.record.binding_snapshot["providers"] if item["provider"] == provider)
        allowed = await journal.begin_attempt(session, owned, provider=provider, external_id=external_id)
        await session.commit()
        return allowed


async def row_for(identity):
    async with AsyncSessionLocal() as session:
        return await session.get(AccessDeviceCommandRecord, identity)


async def test_unknown_blocks_both_actions_on_same_target_but_not_other_target():
    journal, selected = AccessDeviceCommandJournal(), target()
    owned = await claim(journal, selected)
    assert await begin(journal, owned)
    unknown = await journal.finish_attempt(owned, delivery=CommandDelivery.UNKNOWN, state=GateState.UNKNOWN, detail="lost reply")
    assert unknown.state == "unknown" and unknown.accepted is None
    for action in ("open", "close"):
        blocked = await claim(journal, selected, action=action)
        assert blocked.acquired is False
        assert blocked.record.state == "not_sent"
        assert blocked.record.provider_receipts == []
    replay = await claim(journal, selected, intent=owned.record.intent_id)
    assert replay.record.id == owned.record.id and not replay.acquired
    independent = await claim(journal, target())
    assert independent.acquired is True
    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError, match="unresolved"):
            await journal.assert_configurable(session, uuid.UUID(selected["target_device_id"]))


async def test_concurrent_claims_have_one_physical_dispatch_owner():
    journal, selected = AccessDeviceCommandJournal(), target()
    first, second = await asyncio.gather(claim(journal, selected), claim(journal, selected))
    assert sorted([first.acquired, second.acquired]) == [False, True]
    assert sorted([first.record.state, second.record.state]) == ["not_sent", "prepared"]


async def test_expired_attempt_late_acceptance_cannot_overwrite_unknown():
    journal = AccessDeviceCommandJournal()
    owned = await claim(journal, target())
    assert await begin(journal, owned)
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, owned.record.id, with_for_update=True)
        row.lease_expires_at = await session.scalar(select(func.clock_timestamp()))
        await session.commit()
    saved = await journal.finish_attempt(owned, delivery=CommandDelivery.ACCEPTED, state=GateState.OPEN,
        detail="late response", observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)})
    assert saved.state == "unknown" and saved.accepted is None
    assert saved.verification_observation_id is None
    assert saved.verification_evidence is None
    assert saved.lease_token is None
    assert not await begin(journal, owned, provider="esphome")


async def test_stale_physical_evidence_cannot_erase_a_known_provider_acceptance():
    journal = AccessDeviceCommandJournal()
    owned = await claim(journal, target())
    assert await begin(journal, owned)
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, owned.record.id, with_for_update=True)
        now = await session.scalar(select(func.clock_timestamp()))
        row.attempted_at = now - timedelta(seconds=20)
        await session.commit()
    saved = await journal.finish_attempt(owned, delivery=CommandDelivery.ACCEPTED, state=GateState.OPEN,
        detail="Provider accepted", acceptance_basis="home_assistant_http_2xx",
        observation={"provider": "home_assistant", "state": "open", "observed_at": now - timedelta(seconds=11)})
    assert saved.state == "accepted" and saved.accepted is True
    assert saved.gate_state == "unknown" and saved.verification_evidence is None
    assert saved.provider_receipts[-1]["delivery"] == "accepted"
    assert saved.provider_receipts[-1]["acceptance_basis"] == "home_assistant_http_2xx"
    assert device_command_receipt(saved)["requires_reconciliation"] is True
    assert not await begin(journal, owned, provider="esphome")


async def test_only_proven_unsent_can_advance_to_unused_fallback():
    journal = AccessDeviceCommandJournal()
    owned = await claim(journal, target())
    assert await begin(journal, owned)
    row = await journal.finish_attempt(owned, delivery=CommandDelivery.NOT_SENT, state=GateState.UNKNOWN,
                                       detail="connect refused", has_fallback=True)
    assert row.state == "prepared"
    with pytest.raises(ValueError, match="only once"):
        await begin(journal, owned)
    assert await begin(journal, owned, provider="esphome")
    rejected = await journal.finish_attempt(owned, delivery=CommandDelivery.REJECTED, state=GateState.UNKNOWN, detail="refused")
    assert rejected.state == "rejected" and rejected.accepted is False
    assert not await begin(journal, owned, provider="home_assistant")
    assert [item["delivery"] for item in rejected.provider_receipts] == ["not_sent", "rejected"]


async def test_already_open_ends_reservation_with_evidence_and_zero_attempts():
    journal = AccessDeviceCommandJournal()
    owned = await claim(journal, target())
    verified = await journal.finish_without_send(owned, detail="already open",
        observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)})
    assert verified.state == "verified" and verified.accepted is False
    assert verified.attempted_at is None and verified.provider_receipts == []
    assert verified.verification_evidence["target_device_id"] == str(verified.target_device_id)
    assert (await claim(journal, target(verified.target_device_id))).acquired is True


async def test_stored_observation_requires_exact_target_and_attempt_window_and_survives_purge():
    journal, selected = AccessDeviceCommandJournal(), target()
    owned = await claim(journal, selected)
    assert await begin(journal, owned)
    await journal.finish_attempt(owned, delivery=CommandDelivery.UNKNOWN, state=GateState.UNKNOWN, detail="lost reply")
    async with AsyncSessionLocal() as session:
        row = await session.get(AccessDeviceCommandRecord, owned.record.id, with_for_update=True)
        now = await session.scalar(select(func.clock_timestamp()))
        row.attempted_at = now - timedelta(seconds=130)
        attempted = row.attempted_at
        identity = uuid.UUID(selected["target_device_id"])
        fingerprint = selected["binding_fingerprint"]
        for key, at, source, observed_identity, observed_fingerprint in [
            ("different_target", attempted + timedelta(seconds=1), "home_assistant", identity, fingerprint),
            (row.device_key, attempted - timedelta(seconds=1), "home_assistant", identity, fingerprint),
            (row.device_key, attempted + timedelta(seconds=121), "home_assistant", identity, fingerprint),
            (row.device_key, attempted + timedelta(seconds=1), "unrelated_provider", identity, fingerprint),
            (row.device_key, attempted + timedelta(seconds=1), "home_assistant", uuid.uuid4(), fingerprint),
            (row.device_key, attempted + timedelta(seconds=1), "home_assistant", identity, "b" * 64),
            (row.device_key, attempted + timedelta(seconds=1), "home_assistant", None, None),
        ]:
            session.add(GateStateObservation(gate_entity_id=key, gate_name="Synthetic", state="open", observed_at=at, source=source,
                access_device_id=observed_identity, binding_fingerprint=observed_fingerprint))
        await session.commit()
    held = await journal.reconcile_recorded_observation(owned.record.id)
    assert held.state == "unknown" and held.verification_observation_id is None
    async with AsyncSessionLocal() as session:
        good = GateStateObservation(gate_entity_id=selected["device_key"], gate_name="Synthetic", state="opening",
                                    observed_at=attempted + timedelta(seconds=120), source="home_assistant",
                                    access_device_id=uuid.UUID(selected["target_device_id"]), binding_fingerprint=selected["binding_fingerprint"])
        session.add(good)
        await session.commit()
        observation_id = good.id
    verified = await journal.reconcile_recorded_observation(owned.record.id)
    assert verified.state == "verified" and verified.accepted is None
    assert verified.verification_observation_id == observation_id
    async with AsyncSessionLocal() as session:
        await session.execute(delete(GateStateObservation).where(GateStateObservation.id == observation_id))
        await session.commit()
    retained = await row_for(owned.record.id)
    assert retained.verification_observation_id is None
    assert retained.verification_evidence["observation_id"] == str(observation_id)
    assert retained.verification_evidence["observed_at"] == (attempted + timedelta(seconds=120)).isoformat()
    assert device_command_receipt(retained)["delivery"] == "unknown"


class InertProvider:
    def __init__(self, *, delivery=CommandDelivery.ACCEPTED, state=GateState.CLOSED):
        self.delivery, self.state, self.calls = delivery, state, []

    async def observe_state(self, binding, *, runtime_config=None):
        return AccessDeviceStateObservation(self.state, datetime.now(tz=UTC))

    async def command_cover(self, binding, action, reason, *, runtime_config=None):
        self.calls.append((binding.external_id, action))
        if self.delivery == CommandDelivery.ACCEPTED:
            self.state = GateState.OPEN if action == "open" else GateState.CLOSED
        return AccessDeviceCommandResult(self.delivery == CommandDelivery.ACCEPTED, self.state,
                                         provider="home_assistant", delivery=self.delivery)


async def persisted_device(*, kind="gate"):
    async with AsyncSessionLocal() as session:
        identity = uuid.uuid4()
        device = AccessDevice(id=identity, key=f"synthetic_{identity.hex}", name="Synthetic", kind=kind,
                              enabled=True, open_for_access=kind == "gate")
        session.add(device)
        await session.flush()
        session.add(AccessDeviceProviderBinding(access_device_id=device.id, provider="home_assistant",
            external_id=f"cover.synthetic_{identity.hex}", enabled=True, config={}))
        await session.commit()
        return device


def wire_provider(monkeypatch, provider, admission_key):
    runtime = SimpleNamespace(home_assistant_url="http://synthetic.invalid", home_assistant_token="synthetic-only",
        esphome_devices=[], gate_control_provider="home_assistant", gate_failover_provider="none",
        gate_admission_device_key=admission_key, site_timezone="Europe/London", schedule_default_policy="allow")

    async def runtime_config():
        return runtime

    async def fresh_runtime_config(_session):
        return runtime

    monkeypatch.setattr(devices_module, "get_runtime_config", runtime_config)
    monkeypatch.setattr(devices_module, "get_runtime_config_for_session", fresh_runtime_config)
    from app.services import access_device_configuration
    monkeypatch.setattr(access_device_configuration, "get_runtime_config_for_session", fresh_runtime_config)
    monkeypatch.setattr(devices_module, "get_access_device_provider", lambda _name: provider)
    return runtime


@pytest_asyncio.fixture
async def maintenance_owner(monkeypatch):
    from app.models import MaintenanceModeState
    from app.services import maintenance

    monkeypatch.setattr(maintenance, "get_notification_service", lambda: SimpleNamespace(
        enqueue_in_session=AsyncMock(), dispatcher=SimpleNamespace(wake=Mock())))
    monkeypatch.setattr(maintenance, "_sync_home_assistant", AsyncMock())
    monkeypatch.setattr(maintenance.event_bus, "publish", AsyncMock())
    fields = ("is_active", "enabled_by", "enabled_at", "source", "reason", "created_at", "updated_at")
    async with AsyncSessionLocal() as session:
        row = await session.get(MaintenanceModeState, maintenance.MAINTENANCE_STATE_ID)
        previous = {name: getattr(row, name) for name in fields} if row else None
    await maintenance.set_mode(False, actor="Synthetic", source="synthetic", sync_ha=False)
    try:
        yield maintenance
    finally:
        async with AsyncSessionLocal() as session:
            await maintenance_state._lock_maintenance(session)
            row = await session.get(MaintenanceModeState, maintenance.MAINTENANCE_STATE_ID)
            if previous is None:
                if row is not None:
                    await session.delete(row)
            else:
                for name, value in previous.items():
                    setattr(row, name, value)
            await session.commit()


async def maintenance_subject(monkeypatch, *, kind, action):
    from app.modules.gate import access_devices as gate_adapter
    from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent

    device = await persisted_device(kind=kind)
    provider = InertProvider(state=GateState.OPEN if action == "close" else GateState.CLOSED)
    service = AccessDeviceService()
    wire_provider(monkeypatch, provider, device.key if kind == "gate" else None)
    identity = str(uuid.uuid4())
    if kind == "gate":
        plan = await service.preview_gate_open(target_device_key=device.key, require_admission=False)
        monkeypatch.setattr(gate_adapter, "get_access_device_service", lambda: service)

        async def invoke():
            return await GateCommandCoordinator(lambda _name: gate_adapter.AccessDeviceGateController()).execute_open(
                GateCommandIntent(reason="Synthetic", source="synthetic", intent_id=identity, idempotency_key=identity,
                    target_device_key=device.key, target_plan=plan, require_admission=False, bypass_schedule=True))
    else:
        plan = await service.preview_device_command(device.key, action)

        async def invoke():
            return await service.command_device(device.key, action, "Synthetic", bypass_schedule=True,
                intent_id=identity, idempotency_key=identity, target_plan=plan)
    return device, provider, service, identity, invoke


@pytest.mark.parametrize("kind,action", [("gate", "open"), ("garage_door", "open"), ("garage_door", "close")])
async def test_maintenance_committed_while_target_waits_prevents_every_hardware_path(monkeypatch, maintenance_owner, kind, action):
    device, provider, service, identity, invoke = await maintenance_subject(monkeypatch, kind=kind, action=action)
    observation_started, release_observation, target_wait_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observe = provider.observe_state

    async def pause_observation(binding, *, runtime_config=None):
        observation_started.set()
        await release_observation.wait()
        return await observe(binding, runtime_config=runtime_config)

    monkeypatch.setattr(provider, "observe_state", pause_observation)
    lock_target = service._journal.lock_target

    async def record_target_wait(session, target_id):
        if release_observation.is_set():
            target_wait_started.set()
        await lock_target(session, target_id)

    monkeypatch.setattr(service._journal, "lock_target", record_target_wait)
    command = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(observation_started.wait(), timeout=5)
        async with AsyncSessionLocal() as target_owner:
            await lock_target(target_owner, device.id)
            release_observation.set()
            await asyncio.wait_for(target_wait_started.wait(), timeout=5)
            changed = await maintenance_owner.set_mode(True, actor="Synthetic", source="synthetic", sync_ha=False)
            assert changed["is_active"] is True and provider.calls == []
            await target_owner.commit()
        outcome = await asyncio.wait_for(command, timeout=5)
        assert outcome.delivery == CommandDelivery.NOT_SENT
        assert not outcome.requires_reconciliation and provider.calls == []
        async with AsyncSessionLocal() as session:
            row = await session.scalar(select(AccessDeviceCommandRecord).where(AccessDeviceCommandRecord.intent_id == identity))
            assert row.state == "not_sent" and row.attempted_at is None and row.provider_receipts == []
    finally:
        release_observation.set()
        if not command.done():
            command.cancel()
        await asyncio.gather(command, return_exceptions=True)


@pytest.mark.parametrize("kind,action", [("gate", "open"), ("garage_door", "open"), ("garage_door", "close")])
async def test_maintenance_waits_for_winning_attempt_checkpoint_commit(monkeypatch, maintenance_owner, kind, action):
    _device, provider, service, identity, invoke = await maintenance_subject(monkeypatch, kind=kind, action=action)
    checkpoint_ready, release_checkpoint, maintenance_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    maintenance_acquired = asyncio.Event()
    begin_attempt = service._journal.begin_attempt
    committed_attempt_seen = []

    async def pause_before_checkpoint_commit(session, claim, *, provider, external_id):
        allowed = await begin_attempt(session, claim, provider=provider, external_id=external_id)
        assert allowed
        checkpoint_ready.set()
        await release_checkpoint.wait()
        return allowed

    monkeypatch.setattr(service._journal, "begin_attempt", pause_before_checkpoint_commit)
    lock_maintenance = maintenance_state._lock_maintenance
    maintenance_task = None

    async def record_maintenance_wait(session):
        is_writer = asyncio.current_task() is maintenance_task
        if is_writer:
            maintenance_waiting.set()
        await lock_maintenance(session)
        if is_writer:
            maintenance_acquired.set()
            row = await session.scalar(select(AccessDeviceCommandRecord).where(AccessDeviceCommandRecord.intent_id == identity))
            committed_attempt_seen.append(row.attempted_at is not None and row.state != "prepared")

    monkeypatch.setattr(maintenance_state, "_lock_maintenance", record_maintenance_wait)
    command = asyncio.create_task(invoke())
    try:
        await asyncio.wait_for(checkpoint_ready.wait(), timeout=5)
        maintenance_task = asyncio.create_task(maintenance_owner.set_mode(True, actor="Synthetic", source="synthetic", sync_ha=False))
        await asyncio.wait_for(maintenance_waiting.wait(), timeout=5)
        assert not maintenance_acquired.is_set() and provider.calls == []
        assert await maintenance_owner.is_maintenance_mode_active() is False
        release_checkpoint.set()
        outcome, changed = await asyncio.wait_for(asyncio.gather(command, maintenance_task), timeout=5)
        assert committed_attempt_seen == [True]
        assert changed["is_active"] is True and outcome.delivery == CommandDelivery.ACCEPTED
        assert len(provider.calls) == 1
    finally:
        release_checkpoint.set()
        tasks = [command, *([maintenance_task] if maintenance_task is not None else [])]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("kind", ["gate", "garage_door"])
async def test_assigned_schedule_writer_contention_and_committed_denial_both_prevent_send(monkeypatch, maintenance_owner, kind):
    from app.models import Schedule

    device = await persisted_device(kind=kind)
    provider, service = InertProvider(), AccessDeviceService()
    wire_provider(monkeypatch, provider, device.key if kind == "gate" else None)
    async with AsyncSessionLocal() as session:
        schedule = Schedule(name=f"Synthetic schedule {uuid.uuid4()}", time_blocks={
            str(day): [{"start": "00:00", "end": "24:00"}] for day in range(7)})
        session.add(schedule)
        await session.flush()
        saved_device = await session.get(AccessDevice, device.id)
        saved_device.schedule_id = schedule.id
        await session.commit()
        schedule_id = schedule.id
    plan = await service.preview_device_command(device.key, "open")

    async def invoke():
        identity = str(uuid.uuid4())
        return await service.command_device(device.key, "open", "Synthetic", bypass_schedule=False,
            intent_id=identity, idempotency_key=identity, target_plan=plan)

    async with AsyncSessionLocal() as schedule_writer:
        edited = await schedule_writer.get(Schedule, schedule_id, with_for_update=True)
        edited.time_blocks = {}
        await schedule_writer.flush()
        contended = await asyncio.wait_for(invoke(), timeout=5)
        assert contended.delivery == CommandDelivery.NOT_SENT
        assert "being edited" in contended.detail and provider.calls == []
        await schedule_writer.commit()
    denied = await asyncio.wait_for(invoke(), timeout=5)
    assert denied.delivery == CommandDelivery.NOT_SENT
    assert "does not allow" in denied.detail and provider.calls == []


async def test_service_replay_after_terminal_device_deletion_returns_original_receipt(monkeypatch):
    persisted = await persisted_device()
    provider, service = InertProvider(), AccessDeviceService()
    wire_provider(monkeypatch, provider, persisted.key)
    plan = await service.preview_device_command(persisted.key, "open")
    intent = str(uuid.uuid4())
    first = await service.command_device(persisted.key, "open", "synthetic", bypass_schedule=True,
        intent_id=intent, idempotency_key=intent, target_plan=plan)
    assert first.verified and first.accepted and len(provider.calls) == 1
    await service.delete_device(str(persisted.id))
    replay = await service.command_device(persisted.key, "open", "synthetic replay", bypass_schedule=True,
        intent_id=intent, idempotency_key=intent, target_plan=plan)
    assert replay.metadata["command_id"] == first.metadata["command_id"]
    assert replay.verified and len(provider.calls) == 1


async def test_controller_value_error_after_child_acceptance_retains_truth_and_never_repeats(monkeypatch):
    from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent

    persisted = await persisted_device()
    provider, service = InertProvider(), AccessDeviceService()
    wire_provider(monkeypatch, provider, persisted.key)
    plan = await service.preview_gate_open(target_device_key=persisted.key, require_admission=False)
    controller_calls = []

    class FailingProjectionController:
        async def open_gate(self, reason, *, bypass_schedule=False, command_context=None):
            controller_calls.append(command_context.intent_id)
            await service.open_access_gates(reason, bypass_schedule=bypass_schedule, command_context=command_context)
            raise ValueError("Synthetic projection failure after target receipt committed")

    identity = str(uuid.uuid4())
    intent = GateCommandIntent(reason="Synthetic", source="synthetic", intent_id=identity, idempotency_key=identity,
        target_plan=plan, target_device_key=persisted.key, require_admission=False, bypass_schedule=True)
    coordinator = GateCommandCoordinator(lambda _name: FailingProjectionController())
    first = await coordinator.execute_open(intent)
    replay = await coordinator.execute_open(intent)
    assert first.accepted and first.mechanically_confirmed
    assert first.delivery == CommandDelivery.ACCEPTED
    assert first.target_receipts[0]["verified"] is True
    assert first.exception_class == "ValueError"
    assert replay.command_id == first.command_id and replay.target_receipts == first.target_receipts
    assert len(provider.calls) == len(controller_calls) == 1


async def test_authorization_checkpoint_reads_after_waiting_for_target_lock(monkeypatch):
    from app.models import SystemSetting

    persisted = await persisted_device()
    provider, service = InertProvider(), AccessDeviceService()
    wire_provider(monkeypatch, provider, persisted.key)
    plan = await service.preview_device_command(persisted.key, "open")
    policy_key = f"synthetic_dispatch_policy_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as session:
        session.add(SystemSetting(key=policy_key, category="synthetic", value={"allowed": True}, is_secret=False))
        await session.commit()

    observation_started, release_observation, target_wait_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = provider.observe_state

    async def pause_initial_observation(binding, *, runtime_config=None):
        observation_started.set()
        await release_observation.wait()
        return await observed(binding, runtime_config=runtime_config)

    monkeypatch.setattr(provider, "observe_state", pause_initial_observation)
    lock_target = service._journal.lock_target

    async def record_target_wait(session, identity):
        if release_observation.is_set():
            target_wait_started.set()
        await lock_target(session, identity)

    monkeypatch.setattr(service._journal, "lock_target", record_target_wait)
    checkpoint_values = []

    async def authorize_dispatch(session):
        policy = await session.get(SystemSetting, policy_key, populate_existing=True)
        checkpoint_values.append(policy.value["allowed"])
        if not policy.value["allowed"]:
            raise ValueError("Synthetic authorization revoked while target lock was held")

    intent = str(uuid.uuid4())
    task = asyncio.create_task(service.command_device(persisted.key, "open", "Synthetic", bypass_schedule=True,
        intent_id=intent, idempotency_key=intent, target_plan=plan, authorize_dispatch=authorize_dispatch))
    try:
        await asyncio.wait_for(observation_started.wait(), timeout=5)
        async with AsyncSessionLocal() as blocking_writer:
            await lock_target(blocking_writer, persisted.id)
            release_observation.set()
            await asyncio.wait_for(target_wait_started.wait(), timeout=5)
            assert checkpoint_values == []
            policy = await blocking_writer.get(SystemSetting, policy_key)
            policy.value = {"allowed": False}
            await blocking_writer.commit()
        result = await asyncio.wait_for(task, timeout=5)
        assert checkpoint_values == [False]
        assert result.delivery == CommandDelivery.NOT_SENT
        assert provider.calls == []
    finally:
        release_observation.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SystemSetting).where(SystemSetting.key == policy_key))
            await session.commit()


async def test_many_old_unknowns_do_not_starve_fresh_reconcilable_target(monkeypatch):
    persisted = await persisted_device()
    provider, service = InertProvider(state=GateState.OPEN), AccessDeviceService()
    runtime = wire_provider(monkeypatch, provider, persisted.key)
    journal = service._journal
    entity = next(item for item in await service.list_devices() if item.device_id == str(persisted.id))
    selected = service._configuration.target_snapshot(entity, runtime)
    owned = await claim(journal, selected)
    assert await begin(journal, owned)
    await journal.finish_attempt(owned, delivery=CommandDelivery.UNKNOWN, state=GateState.UNKNOWN, detail="lost reply")
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        for _ in range(101):
            item = target()
            session.add(AccessDeviceCommandRecord(target_device_id=uuid.UUID(item["target_device_id"]),
                device_key=item["device_key"], action="open", intent_id=str(uuid.uuid4()),
                idempotency_key=f"synthetic-old-{uuid.uuid4()}", state="unknown", accepted=None,
                binding_snapshot=item["binding_snapshot"], binding_fingerprint=item["binding_fingerprint"],
                attempted_at=now - timedelta(days=2), created_at=now - timedelta(days=2), provider_receipts=[]))
        await session.commit()
    await service.reconcile_commands()
    assert (await row_for(owned.record.id)).state == "verified"
    assert provider.calls == []


async def test_committed_settings_change_cannot_be_hidden_by_another_workers_cached_plan(monkeypatch):
    from app.models import SystemSetting
    from app.services import settings as settings_module

    persisted = await persisted_device()
    provider, service = InertProvider(), AccessDeviceService()
    monkeypatch.setattr(devices_module, "get_access_device_provider", lambda _name: provider)
    values = {"home_assistant_url": "http://synthetic-old.invalid", "home_assistant_token": "synthetic-only",
              "gate_control_provider": "home_assistant", "gate_failover_provider": "none",
              "gate_admission_device_key": persisted.key}
    saved = {}
    async with AsyncSessionLocal() as writer:
        for key, value in values.items():
            row = await writer.get(SystemSetting, key)
            saved[key] = (row.category, row.value, row.is_secret, row.description) if row else None
            if row is None:
                row = SystemSetting(key=key, category="synthetic", value={"plain": value}, is_secret=False)
                writer.add(row)
            else:
                row.value, row.is_secret = {"plain": value}, False
        await writer.commit()
    try:
        async with AsyncSessionLocal() as worker_a:
            stale_runtime = await settings_module.get_runtime_config_for_session(worker_a)
        preview = await service.preview_device_command(persisted.key, "open")

        async def stale_worker_cache():
            return stale_runtime

        monkeypatch.setattr(devices_module, "get_runtime_config", stale_worker_cache)
        async with AsyncSessionLocal() as worker_b:
            row = await worker_b.get(SystemSetting, "home_assistant_url", with_for_update=True)
            row.value = {"plain": "http://synthetic-new.invalid"}
            await worker_b.commit()
        intent = str(uuid.uuid4())
        with pytest.raises(ValueError, match="configuration changed"):
            await service.command_device(persisted.key, "open", "synthetic", bypass_schedule=True,
                intent_id=intent, idempotency_key=intent, target_plan=preview)
        assert provider.calls == []
        assert await service.command_receipt(intent_id=intent) is None
    finally:
        async with AsyncSessionLocal() as writer:
            for key, original in saved.items():
                row = await writer.get(SystemSetting, key)
                if original is None:
                    await writer.delete(row)
                else:
                    row.category, row.value, row.is_secret, row.description = original
            await writer.commit()
        settings_module.invalidate_runtime_config_cache()


@pytest.mark.parametrize("initial,expected_sends,admitted", [
    (GateState.CLOSED, 2, True), (GateState.OPEN, 0, True), (GateState.OPENING, 0, True),
    (GateState.UNKNOWN, 0, False), (GateState.FAULT, 0, False), (GateState.CLOSING, 0, False),
])
async def test_automatic_entry_precondition_is_global_and_never_opens_on_unknown(
    monkeypatch, maintenance_owner, initial, expected_sends, admitted,
):
    from app.models import GateCommandRecord
    from app.modules.gate import access_devices as gate_adapter
    from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent

    entry, secondary = await persisted_device(), await persisted_device()
    service = AccessDeviceService()
    providers = {f"cover.{entry.key}": InertProvider(state=initial),
                 f"cover.{secondary.key}": InertProvider(state=GateState.CLOSED)}

    class PhysicalTargets:
        async def observe_state(self, binding, **kwargs):
            return await providers[binding.external_id].observe_state(binding, **kwargs)

        async def command_cover(self, binding, action, reason, **kwargs):
            return await providers[binding.external_id].command_cover(binding, action, reason, **kwargs)

    wire_provider(monkeypatch, PhysicalTargets(), entry.key)
    original_list = service._configuration.list_devices_for_session

    async def subjects(session, **kwargs):
        devices = await original_list(session, **kwargs)
        return sorted([item for item in devices if item.device_id in {str(entry.id), str(secondary.id)}],
                      key=lambda item: item.device_id != str(entry.id))

    monkeypatch.setattr(service._configuration, "list_devices_for_session", subjects)
    monkeypatch.setattr(gate_adapter, "get_access_device_service", lambda: service)
    plan = await service.preview_gate_open(require_admission=True, automatic_entry_policy=True)
    identity = str(uuid.uuid4())
    intent = GateCommandIntent(reason="Synthetic automatic entry", source="synthetic", intent_id=identity,
        idempotency_key=identity, target_plan=plan, require_admission=True, automatic_entry_policy=True,
        bypass_schedule=True, authorize_dispatch=AsyncMock())
    coordinator = GateCommandCoordinator(lambda _name: gate_adapter.AccessDeviceGateController())
    result = await coordinator.execute_open(intent)
    assert sum(len(provider.calls) for provider in providers.values()) == expected_sends
    assert result.admission_verified is admitted
    assert len(result.target_receipts) == 2
    if initial in {GateState.OPEN, GateState.OPENING}:
        assert all(receipt["delivery"] == "not_sent" for receipt in result.target_receipts)
        assert result.target_receipts[1]["verified"] is False
    async with AsyncSessionLocal() as session:
        parent = await session.get(GateCommandRecord, uuid.UUID(result.command_id))
        mode = parent.command_metadata["automatic_entry_precondition"]["mode"]
        assert mode == ("fanout" if initial == GateState.CLOSED else "observe_only" if admitted else "unresolved")
    replay = await coordinator.execute_open(intent)
    assert replay.target_receipts == result.target_receipts
    assert sum(len(provider.calls) for provider in providers.values()) == expected_sends


async def test_receipt_lists_page_equal_timestamps_without_mutation_or_child_duplicates(monkeypatch):
    from app.models import GateCommandRecord
    from app.models.enums import GateCommandState
    from app.services.gate_commands import GateCommandCoordinator

    journal = AccessDeviceCommandJournal()
    created = datetime.now(tz=UTC) + timedelta(days=36500)
    parent_ids = [uuid.uuid4() for _ in range(3)]
    direct = [await claim(journal, target()) for _ in range(3)]
    child = await claim(journal, target())
    async with AsyncSessionLocal() as session:
        for identity in parent_ids:
            session.add(GateCommandRecord(id=identity, idempotency_key=f"synthetic:{identity}",
                source="synthetic", controller="configured", reason="Synthetic receipt listing",
                state=GateCommandState.RECONCILIATION_REQUIRED, created_at=created,
                accepted=None, requires_reconciliation=True, command_metadata={"intent_id": str(identity)}))
        await session.flush()
        for owned in [*direct, child]:
            row = await session.get(AccessDeviceCommandRecord, owned.record.id)
            row.created_at = created
            row.lease_expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
            if owned is child:
                row.gate_command_id = parent_ids[0]
        await session.commit()
    before = [(row.id, row.state, row.updated_at, row.lease_expires_at)
              for row in [await row_for(owned.record.id) for owned in [*direct, child]]]
    coordinator = GateCommandCoordinator()
    first = await coordinator.list_receipts(limit=2)
    second = await coordinator.list_receipts(limit=2, before_id=uuid.UUID(first["next_cursor"]))
    assert [item["command_id"] for item in [*first["items"], *second["items"]]][:3] == [str(item) for item in sorted(parent_ids, reverse=True)]
    first = await journal.list_command_receipts(limit=2)
    second = await AccessDeviceService().list_command_receipts(limit=2, before_id=uuid.UUID(first["next_cursor"]))
    assert [item["command_id"] for item in [*first["items"], *second["items"]]][:3] == [
        str(item) for item in sorted([owned.record.id for owned in direct], reverse=True)]
    assert str(child.record.id) not in [item["command_id"] for item in [*first["items"], *second["items"]]]
    after = [(row.id, row.state, row.updated_at, row.lease_expires_at)
             for row in [await row_for(owned.record.id) for owned in [*direct, child]]]
    assert before == after  # Listing cannot expire leases or reconcile receipts.


async def test_parent_projection_refreshes_preloaded_child_after_another_worker_verifies():
    from app.models import GateCommandRecord
    from app.models.enums import GateCommandState

    journal, selected = AccessDeviceCommandJournal(), target()
    owned = await claim(journal, selected)
    assert await begin(journal, owned)
    await journal.finish_attempt(owned, delivery=CommandDelivery.UNKNOWN, state=GateState.UNKNOWN, detail="Synthetic lost response")
    async with AsyncSessionLocal() as writer:
        parent = GateCommandRecord(idempotency_key=str(uuid.uuid4()), source="synthetic", controller="configured",
            reason="Synthetic refresh", state=GateCommandState.RECONCILIATION_REQUIRED,
            command_metadata={"recovery_version": 2, "target_plan": {"targets": [selected],
                "admission_target_device_id": selected["target_device_id"]}})
        writer.add(parent)
        await writer.flush()
        row = await writer.get(AccessDeviceCommandRecord, owned.record.id)
        row.gate_command_id = parent.id
        await writer.commit()
    async with AsyncSessionLocal() as observer:
        before = await observer.get(AccessDeviceCommandRecord, owned.record.id)
        assert before.state == "unknown"
        await journal.reconcile_observation(owned.record.id, binding_fingerprint=selected["binding_fingerprint"],
            observation={"provider": "home_assistant", "state": "open", "observed_at": datetime.now(tz=UTC)})
        # The old ORM object remains loaded in this transaction; the FOR UPDATE
        # query must replace it with committed truth before deriving admission.
        projected = await journal.gate_command_projection(observer, parent, reconcile=True)
        assert projected["admission_verified"] is True
        assert projected["target_receipts"][0]["delivery"] == "unknown"
        await observer.commit()


async def test_child_reservation_locks_parent_before_target_during_expiry_projection(monkeypatch):
    from app.models import GateCommandRecord
    from app.models.enums import GateCommandState

    journal, selected = AccessDeviceCommandJournal(), target()
    identity = str(uuid.uuid4())
    async with AsyncSessionLocal() as setup:
        parent = GateCommandRecord(idempotency_key=identity, source="synthetic", controller="configured",
            reason="Synthetic lock order", state=GateCommandState.LEASED, lease_token="expired",
            lease_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
            command_metadata={"intent_id": identity, "recovery_version": 2,
                              "target_plan": {"targets": [selected], "admission_target_device_id": selected["target_device_id"]}})
        setup.add(parent)
        await setup.commit()
    parent_wait, target_acquired = asyncio.Event(), asyncio.Event()
    task = None
    async with AsyncSessionLocal() as reservation_session:
        original_get = reservation_session.get

        async def watched_get(model, key, **kwargs):
            if model is GateCommandRecord:
                parent_wait.set()
            return await original_get(model, key, **kwargs)

        monkeypatch.setattr(reservation_session, "get", watched_get)
        original_lock = journal.lock_target

        async def watched_lock(session, target_id):
            await original_lock(session, target_id)
            if session is reservation_session:
                target_acquired.set()

        monkeypatch.setattr(journal, "lock_target", watched_lock)

        async def reserve_child():
            result = await journal.claim(reservation_session, target=selected, action="open", intent_id=identity,
                operation_key=identity, gate_command_id=str(parent.id), expires_at=None)
            await reservation_session.commit()
            return result

        try:
            async with AsyncSessionLocal() as recovery:
                current = await recovery.get(GateCommandRecord, parent.id, with_for_update=True)
                task = asyncio.create_task(reserve_child())
                await asyncio.wait_for(parent_wait.wait(), 5)
                assert not target_acquired.is_set()
                # If claim held target before requesting the parent, this would
                # deadlock; recovery materializes the conclusive no-send child.
                projection = await asyncio.wait_for(journal.gate_command_projection(recovery, current, reconcile=True), 5)
                assert projection["target_receipts"][0]["delivery"] == "not_sent"
                await recovery.commit()
            result = await asyncio.wait_for(task, 5)
            assert not result.acquired and result.record.state == "not_sent"
            assert result.record.provider_receipts == []
        finally:
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
