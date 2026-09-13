"""Six inert recovery diagnostics. Expected hazards fail; never xfail or replay hardware.

Run explicitly, serially, in the existing phase1 loopback-only namespace after
its schema preparation. See docs/validation/recovery-boundaries.md. This file
must fail collection before importing application code outside that namespace.
"""

import asyncio
import json
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit


def _require_isolated_environment():
    def require(condition, message):
        if not condition:
            raise RuntimeError("Recovery probe isolation refused: " + message)

    require(os.environ.get("IACS_RECOVERY_PROBES") == "synthetic-only", "explicit synthetic-only opt-in missing")
    interfaces = Path("/sys/class/net")
    require(interfaces.is_dir() and {p.name for p in interfaces.iterdir()} == {"lo"}, "loopback-only Linux namespace required")
    require(not Path("/var/run/docker.sock").exists(), "Docker socket must not be mounted")
    root = Path(__file__).resolve().parents[2]
    require(not any((root / item).exists() for item in (".env", "data", "logs")), "source snapshot contains runtime paths")
    require(not Path.cwd().joinpath(".env").exists(), "working directory contains an env file")
    require(os.environ.get("IACS_ENVIRONMENT") == "testing", "testing environment required")
    require(os.environ.get("IACS_AUTO_CREATE_SCHEMA") == "false", "automatic schema creation must be disabled")
    require(os.environ.get("IACS_SEED_DEMO_DATA") == "false", "demo seeding must be disabled")
    require(os.environ.get("IACS_AUTH_SECRET_KEY") == "phase1-synthetic-auth-root-never-production", "synthetic auth root required")
    url = urlsplit(os.environ.get("IACS_DATABASE_URL", ""))
    require(url.scheme == "postgresql+asyncpg" and url.hostname == "127.0.0.1" and url.port == 5432, "isolated PostgreSQL endpoint required")
    require(url.username == "phase1" and url.password == "synthetic-phase1-only", "synthetic PostgreSQL principal required")
    require(url.path.startswith("/iacs_p1_") or url.path.startswith("/iacs_recovery_"), "isolated database name required")
    require(not url.query and not url.fragment, "database URL overrides are not permitted")
    for key, value in os.environ.items():
        if key.startswith("IACS_") and any(word in key for word in ("TOKEN", "PASSWORD", "API_KEY")):
            require(not value, "integration credential environment is not permitted")
    return url.path[1:]


DATABASE_NAME = _require_isolated_environment()

# Third-party/application imports intentionally follow the fail-closed guard.
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text, update

from app.ai.context import set_chat_tool_context
from app.ai.tool_groups import access_incident_handlers as historical_alfred
from app.api.v1 import ai as ai_api
from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, AutomationRule, AutomationRun, ChatSession, GateCommandRecord, MovementSagaRecord, Person, Presence, User
from app.models.enums import AccessDecision, AccessDirection, GateCommandState, MovementSagaState, PresenceState, TimingClassification, UserRole
from app.modules.access_devices.base import AccessDeviceBinding, AccessDeviceCommandResult, AccessDeviceEntity
from app.modules.access_devices.home_assistant import HomeAssistantAccessDeviceProvider
from app.modules.gate import access_devices as gate_adapter
from app.modules.gate.base import GateCommandResult, GateState
from app.modules.home_assistant.client import HomeAssistantClient
from app.services import access_devices as devices_owner
from app.services import automation_integration_actions as integration_actions
from app.services import automations as automation_owner
from app.services import chat as chat_owner
from app.services import movement_reconciliation as reconciliation_owner
from app.services import restart_backfill
from app.services.access_devices import AccessDeviceOperationResult, AccessDeviceProviderAttempt, AccessDeviceService
from app.services.chat import ChatService
from app.services.chat_contracts import IntentRoute
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent
from app.services.movement_reconciliation import MovementReconciliationService

pytestmark = pytest.mark.asyncio
CASES = json.loads((Path(__file__).parent / "fixtures/recovery_boundaries/scenarios.json").read_text())
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
WAIT_SECONDS = 8


class Trace:
    trace_id = "synthetic-recovery-boundary"

    def record_span(self, *args, **kwargs):
        pass

    def finish(self, *args, **kwargs):
        pass


async def _bounded(awaitable):
    # A watchdog is not an ordering mechanism. Every race uses explicit events.
    return await asyncio.wait_for(awaitable, timeout=WAIT_SECONDS)


async def _rows(model):
    async with AsyncSessionLocal() as session:
        return list((await session.scalars(select(model))).all())


@pytest_asyncio.fixture(autouse=True)
async def isolated_resources(monkeypatch):
    """Permit only the explicitly named disposable DB; all other sockets fail."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check_address(address):
        if not (isinstance(address, tuple) and address[:2] == ("127.0.0.1", 5432)):
            raise AssertionError("Recovery probe attempted a non-PostgreSQL socket")

    def connect(sock, address):
        check_address(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check_address(address)
        return original_connect_ex(sock, address)

    async def no_http(*args, **kwargs):
        raise AssertionError("Recovery probe attempted an unmocked HTTP transport")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_http)
    monkeypatch.setattr(event_bus, "publish", AsyncMock())
    monkeypatch.setattr(automation_owner.telemetry, "start_trace", lambda *a, **kw: Trace())
    monkeypatch.setattr(automation_owner.telemetry, "flush", AsyncMock())
    monkeypatch.setattr(automation_owner, "emit_audit_log", lambda *a, **kw: None)
    monkeypatch.setattr(chat_owner, "emit_audit_log", lambda *a, **kw: None)
    monkeypatch.setattr(automation_owner, "is_maintenance_mode_active", AsyncMock(return_value=False))

    runtime = SimpleNamespace(site_timezone="Europe/London", llm_provider="local", llm_timeout_seconds=1)
    for module in (automation_owner, chat_owner, historical_alfred):
        monkeypatch.setattr(module, "get_runtime_config", AsyncMock(return_value=runtime))
    monkeypatch.setattr(chat_owner, "get_llm_provider", lambda name: SimpleNamespace(name="local"))

    try:
        async with AsyncSessionLocal() as session:
            assert await session.scalar(text("SELECT current_database()")) == DATABASE_NAME
            # Fail rather than reading any persisted runtime settings supplied by a caller.
            assert await session.scalar(text("SELECT count(*) FROM system_settings")) == 0, "Probe DB must have no persisted runtime settings"
            await session.execute(text("TRUNCATE people, users, access_events, movement_sagas, movement_sessions, gate_command_records, gate_state_observations, automation_rules, automation_runs, chat_sessions, audit_logs, visitor_pass_reservations, visitor_passes, notification_runs CASCADE"))
            await session.commit()
        yield
    finally:
        await engine.dispose()


def _device(key="synthetic_gate"):
    return AccessDeviceEntity(key=key, kind="gate", name=key, bindings={
        "home_assistant": AccessDeviceBinding(provider="home_assistant", external_id="cover.synthetic_gate"),
        "esphome": AccessDeviceBinding(provider="esphome", external_id="synthetic_gate"),
    })


def _device_outcome(key, accepted, state):
    state = GateState(state)
    verified = accepted and state in {GateState.OPEN, GateState.OPENING}
    target_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-recovery-target:{key}"))
    receipt = {
        "command_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"synthetic-recovery-command:{key}")),
        "target_device_id": target_id, "device_key": key, "action": "open",
        "accepted": accepted, "delivery": "accepted" if accepted else "rejected",
        "state": state.value, "status": "verified" if verified else "accepted" if accepted else "rejected",
        "verified": verified, "requires_reconciliation": accepted and not verified,
    }
    return AccessDeviceOperationResult(
        device=_device(key), action="open", accepted=accepted, state=state,
        detail="Synthetic provider result", primary_provider="synthetic", used_provider="synthetic",
        attempts=[AccessDeviceProviderAttempt(provider="synthetic", accepted=accepted, state=state.value,
                                              verified=verified)],
        metadata={"target_receipt": receipt, "admission_target_device_id": None,
                  "verified": verified, "requires_reconciliation": accepted and not verified},
    )


@pytest.mark.parametrize("case", CASES["aggregate"], ids=lambda case: case["id"])
async def test_probe_01_multitarget_truth_is_order_independent(monkeypatch, case):
    outcomes = [_device_outcome(str(i), item[0], item[1]) for i, item in enumerate(case["targets"])]
    service = SimpleNamespace(open_access_gates=AsyncMock(return_value=outcomes))
    monkeypatch.setattr(gate_adapter, "get_access_device_service", lambda: service)
    intent = GateCommandIntent(reason="Synthetic aggregate probe", source="synthetic-recovery",
                               idempotency_key="aggregate:" + case["id"])
    outcome = await GateCommandCoordinator(lambda name: gate_adapter.AccessDeviceGateController()).execute_open(intent)
    persisted, = await _rows(GateCommandRecord)
    assert len(outcome.metadata["access_device_outcomes"]) == len(outcomes)
    assert persisted.command_metadata["access_device_outcomes"] == outcome.metadata["access_device_outcomes"]
    assert outcome.mechanically_confirmed is case["all_verified"], (
        "Aggregate physical verification depends on target order or conceals an unverified target"
    )
    assert outcome.requires_reconciliation is case["review_needed"], (
        "Unknown or accepted-unverified targets must remain reconcilable; terminal rejection cannot be retried."
    )
    expected_receipts = [item.metadata["target_receipt"] for item in outcomes]
    assert list(outcome.target_receipts) == expected_receipts
    if len({item[0] for item in case["targets"]}) > 1:
        assert outcome.delivery == "partial"
        assert not outcome.accepted and not outcome.mechanically_confirmed

    def forbidden_provider(name):
        raise AssertionError("Replaying a partial or terminal intent must never create another hardware path")

    replay = await GateCommandCoordinator(forbidden_provider).execute_open(intent)
    assert replay.command_id == outcome.command_id
    assert replay.delivery == outcome.delivery
    assert replay.accepted == outcome.accepted
    assert replay.mechanically_confirmed == outcome.mechanically_confirmed
    assert replay.requires_reconciliation == outcome.requires_reconciliation
    assert list(replay.target_receipts) == expected_receipts
    service.open_access_gates.assert_awaited_once()


@pytest.mark.parametrize("mode", ["post_accepted_response_lost", "post_accepted_state_read_lost", "definitely_not_sent"])
async def test_probe_02_ha_response_loss_does_not_send_to_fallback(monkeypatch, mode):
    sent, fallback_calls = [], []

    async def transport(request):
        if request.method == "POST":
            if mode == "definitely_not_sent":
                raise httpx.ConnectError("Synthetic connection never established", request=request)
            sent.append("ha-accepted")
            if mode == "post_accepted_response_lost":
                raise httpx.ReadTimeout("Synthetic response lost after acceptance", request=request)
            return httpx.Response(200, json={})
        raise httpx.ReadTimeout("Synthetic state read unavailable", request=request)

    client = HomeAssistantClient()
    monkeypatch.setattr(client, "config", AsyncMock(return_value=SimpleNamespace(
        home_assistant_url="https://synthetic.invalid", home_assistant_token="synthetic-only"
    )))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport), trust_env=False) as http:
        monkeypatch.setattr(client, "_request_client", AsyncMock(return_value=http))
        primary = HomeAssistantAccessDeviceProvider(client=client)

        async def fallback_command(binding, action, reason):
            fallback_calls.append(action)
            return AccessDeviceCommandResult(True, GateState.OPENING, reason, "esphome", binding.external_id)

        fallback = SimpleNamespace(command_cover=fallback_command)
        monkeypatch.setattr(devices_owner, "get_access_device_provider", lambda name: primary if name == "home_assistant" else fallback)
        service = AccessDeviceService()
        monkeypatch.setattr(service, "_provider_order_for_device", AsyncMock(return_value=["home_assistant", "esphome"]))
        monkeypatch.setattr(service, "_remember_state", AsyncMock())

        monkeypatch.setattr(service, "_observe_target", AsyncMock(return_value=None))
        monkeypatch.setattr(devices_owner, "get_runtime_config", AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr(devices_owner, "COMMAND_CONFIRMATION_TIMEOUT_SECONDS", 0)
        outcome = await service._command_with_failover(_device(), "open", "Synthetic lost response")

    assert len(sent) == int(mode != "definitely_not_sent")
    assert len(fallback_calls) == int(mode == "definitely_not_sent"), (
        "A request accepted by the inert HA endpoint was sent again through the fallback provider"
    )
    if mode == "post_accepted_state_read_lost":
        assert outcome.accepted and not outcome.verified


@pytest.mark.parametrize("reconcile_first", [False, True], ids=["retry-before-reconciler", "reconciler-before-retry"])
async def test_probe_03_expired_lease_does_not_repeat_one_intent(monkeypatch, reconcile_first):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Gate:
        async def open_gate(self, reason, *, bypass_schedule=False, command_context=None):
            calls.append("accepted-by-inert-controller")
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return GateCommandResult(True, GateState.OPENING, "Synthetic accepted")

        async def current_state(self):
            return GateState.OPENING

    intent = GateCommandIntent(reason="Synthetic lease probe", source="synthetic-recovery", idempotency_key="synthetic-fixed-command")
    first = asyncio.create_task(GateCommandCoordinator(lambda name: Gate()).execute_open(intent))
    try:
        await _bounded(entered.wait())
        # Real independent transaction; no fake wall clock or sleep-to-expiry.
        async with AsyncSessionLocal() as session:
            row = await session.scalar(select(GateCommandRecord))
            assert row.state == GateCommandState.LEASED and row.accepted is None
            await session.execute(update(GateCommandRecord).where(GateCommandRecord.id == row.id).values(
                lease_expires_at=text("clock_timestamp() - interval '1 second'")
            ))
            await session.commit()
        if reconcile_first:
            # The real reconciler operates on fresh sessions and may publish only
            # into the autouse inert event sink. No worker loop is started.
            assert await _bounded(MovementReconciliationService().reconcile_once()) == 1
        replay = await _bounded(GateCommandCoordinator(lambda name: Gate()).execute_open(intent))
    finally:
        release.set()
        late_results = await _bounded(asyncio.gather(first, return_exceptions=True))
    records = await _rows(GateCommandRecord)
    assert len(records) == 1, "One intent should retain one durable command identity"
    assert calls == ["accepted-by-inert-controller"], (
        "An expired in-flight lease was treated as permission to repeat the same physical request"
    )

    persisted, = records
    assert persisted.state == GateCommandState.RECONCILIATION_REQUIRED
    assert persisted.accepted is None, "A late worker must not replace an unknown receipt with accepted"
    assert persisted.mechanically_confirmed is False
    assert persisted.requires_reconciliation is True
    assert persisted.lease_token is None and persisted.lease_expires_at is None
    assert persisted.command_metadata["delivery"] == "unknown"
    assert replay.accepted is False and replay.requires_reconciliation is True
    assert replay.mechanically_confirmed is False and replay.delivery == "unknown"
    late, = late_results
    assert not isinstance(late, BaseException), "A fenced late response should return the saved review outcome"
    assert late.command_id == replay.command_id
    assert late.accepted is False and late.requires_reconciliation is True
    assert late.mechanically_confirmed is False and late.delivery == "unknown"


async def test_probe_03_support_terminal_same_intent_never_replays():
    calls = []

    async def open_gate(reason, *, bypass_schedule=False, command_context=None):
        calls.append(reason)
        return GateCommandResult(True, GateState.OPENING, "Synthetic accepted")

    gate = SimpleNamespace(open_gate=open_gate)
    intent = GateCommandIntent(reason="Synthetic completed command", source="synthetic-recovery", idempotency_key="synthetic-completed")
    first = await GateCommandCoordinator(lambda name: gate).execute_open(intent)
    second = await GateCommandCoordinator(lambda name: gate).execute_open(intent)
    assert first.command_id == second.command_id
    assert len(calls) == 1


@pytest.mark.parametrize("hardware", ["gate.open", "garage_door.open", "garage_door.close"])
@pytest.mark.parametrize("hardware_first", [True, False], ids=["hardware-first", "notification-first"])
@pytest.mark.parametrize("origin", ["unknown", "known", "visitor", "phrase", "historical"])
async def test_probe_04_real_automation_intake_preserves_recognition_authority(monkeypatch, hardware, hardware_first, origin):
    from app.models import LprIngestEvent, NotificationRun, Vehicle, VisitorPass
    from app.services.access import authorization
    from app.services import notifications as notification_owner
    from sqlalchemy import func
    from app.models.enums import VisitorPassStatus

    calls, notifications = [], []

    async def open_gate(reason, *, bypass_schedule=False, command_context=None):
        async with AsyncSessionLocal() as session:
            await command_context.authorize_dispatch(session)
            await session.commit()
        calls.append("gate")
        return GateCommandResult(True, GateState.OPENING, "Synthetic accepted")

    async def command_device(key, action, reason, **kwargs):
        async with AsyncSessionLocal() as session:
            await kwargs["authorize_dispatch"](session)
            await session.commit()
        calls.append("garage")
        return _device_outcome(key, True, "opening")

    async def send_message(action, context, **kwargs):
        notifications.append(action["id"])
        return {"id": action["id"], "type": action["type"], "status": "success"}

    monkeypatch.setattr(automation_owner, "get_gate_command_coordinator", lambda: GateCommandCoordinator(lambda name: SimpleNamespace(open_gate=open_gate)))
    from app.services import automation_intake
    monkeypatch.setattr(automation_owner, "get_access_device_service", lambda: SimpleNamespace(command_device=command_device))
    monkeypatch.setattr(automation_intake, "AccessDeviceConfiguration", lambda: SimpleNamespace(
        preview_gate_open=AsyncMock(return_value={"version": 1, "action": "open", "targets": [{"device_key": "synthetic_gate"}]}),
        preview_device_command=AsyncMock(side_effect=lambda key, command, **kwargs: {"version": 1, "action": command, "target_device_key": key, "targets": [{"device_key": key}]})))
    monkeypatch.setattr(automation_intake, "automation_garage_targets", AsyncMock(return_value=[_device("synthetic_garage")]))
    async def prepare_message(action, context, **kwargs):
        return dict(action)

    fake_messaging = SimpleNamespace(
        status=AsyncMock(return_value={"configured": True}),
        prepare_notification_action=prepare_message,
        authorize_notification_action_in_session=AsyncMock(return_value=None),
        send_notification_action=send_message,
    )
    monkeypatch.setattr(integration_actions, "get_whatsapp_delivery_service", lambda: fake_messaging)
    monkeypatch.setattr(notification_owner, "get_whatsapp_delivery_service", lambda: fake_messaging)
    monkeypatch.setattr(authorization, "get_runtime_config_for_session", AsyncMock(return_value=SimpleNamespace(site_timezone="Europe/London", schedule_default_policy="allow")))
    trigger = {"known": "vehicle.known_plate", "visitor": "visitor_pass.used",
               "phrase": "ai.phrase_received"}.get(origin, "vehicle.unknown_plate")
    hardware_action = {"id": "hardware", "type": hardware, "config": {}, "reason_template": "Synthetic probe"}
    notification_action = {"id": "notify", "type": "integration.whatsapp.send_message", "config": {"target_mode": "all", "message_template": "Synthetic observation"}}
    actions = [hardware_action, notification_action] if hardware_first else [notification_action, hardware_action]
    pass_id = vehicle_id = event_id = None
    async with AsyncSessionLocal() as session:
        observed_at = await session.scalar(select(func.clock_timestamp()))
        await session.execute(text("TRUNCATE notification_runs CASCADE"))
        # Historical stored combinations intentionally bypass create-time validation
        # so future validation alone cannot make the execution probe vacuous.
        session.add(AutomationRule(name="Synthetic recognition workflow", is_active=True,
                                   triggers=[{"type": trigger, "config": {}}], trigger_keys=[trigger],
                                   conditions=[], actions=actions))
        if origin in {"known", "visitor"}:
            if origin == "known":
                vehicle = Vehicle(registration_number="SYNTH04", is_active=True)
                session.add(vehicle)
                await session.flush()
                vehicle_id = vehicle.id
            event = AccessEvent(vehicle_id=vehicle_id, registration_number="SYNTH04", direction=AccessDirection.ENTRY,
                decision=AccessDecision.GRANTED, confidence=1, source="synthetic_probe", occurred_at=observed_at, raw_payload={})
            session.add(event)
            await session.flush()
            event_id = event.id
            session.add(LprIngestEvent(idempotency_key=f"synthetic:{event.id}", source="synthetic_probe", registration_number="SYNTH04",
                captured_at=observed_at, received_at=observed_at, access_event_id=event.id, status="completed", normalized_payload={}))
            if origin == "visitor":
                visitor = VisitorPass(visitor_name="Synthetic unbound visitor", expected_time=observed_at,
                    status=VisitorPassStatus.USED, number_plate=None, arrival_event_id=event.id, arrival_time=observed_at)
                session.add(visitor)
                await session.flush()
                pass_id = visitor.id
                event.raw_payload = {"visitor_pass": {"id": str(pass_id)}}
        await session.commit()
    if origin == "known":
        # A valid configured follow-on action starts only after its core admission
        # has a durable, settled exact-target receipt. No provider is called here.
        from app.services.access_device_commands import AccessDeviceCommandJournal
        from app.modules.gate.base import CommandDelivery
        operation = str(uuid.uuid5(event_id, "automatic-gate-open"))
        target = {"target_device_id": str(uuid.uuid4()), "device_key": "synthetic_core_entry", "kind": "gate",
            "binding_fingerprint": "a" * 64,
            "binding_snapshot": {"providers": [{"provider": "home_assistant", "external_id": "cover.synthetic_core"}]}}
        journal = AccessDeviceCommandJournal()
        async with AsyncSessionLocal() as session:
            saga = MovementSagaRecord(idempotency_key=f"synthetic-core:{event_id}", source="synthetic_probe",
                occurred_at=observed_at, access_event_id=event_id, state=MovementSagaState.COMPLETED,
                direction=AccessDirection.ENTRY, decision=AccessDecision.GRANTED, admission_status="verified",
                reconciliation_required=False, intent_payload={}, decision_payload={}, state_history=[])
            session.add(saga); await session.flush()
            parent = GateCommandRecord(idempotency_key=f"gate-command:open:default:event:{event_id}",
                source="automatic_lpr_grant", controller="configured", reason="Synthetic settled admission",
                movement_saga_id=saga.id, access_event_id=event_id, state=GateCommandState.RECONCILED,
                accepted=True, mechanically_confirmed=True, requires_reconciliation=False, completed_at=observed_at,
                command_metadata={"intent_id": operation, "recovery_version": 2,
                    "target_plan": {"targets": [target], "admission_target_device_id": target["target_device_id"]},
                    "automatic_entry_precondition": {"mode": "fanout"}})
            session.add(parent); await session.flush()
            claim = await journal.claim(session, target=target, action="open", intent_id=operation,
                operation_key=operation, gate_command_id=str(parent.id), expires_at=None)
            await session.commit()
        async with AsyncSessionLocal() as session:
            assert await journal.begin_attempt(session, claim, provider="home_assistant", external_id="cover.synthetic_core")
            await session.commit()
        await journal.finish_attempt(claim, delivery=CommandDelivery.ACCEPTED, state=GateState.OPEN,
            detail="Synthetic settled receipt", observation={"provider": "home_assistant", "state": "open",
                "observed_at": datetime.now(tz=UTC)})
    payload = dict(CASES["recognition_payload"])
    event_type = "access_event.finalized"
    if origin == "known":
        payload.update(decision="granted", direction="entry", vehicle_id=str(vehicle_id), access_event_id=str(event_id), occurred_at=observed_at.isoformat())
    if origin == "visitor":
        # Valid domain visitor event without a person or vehicle row. Real
        # context construction reloads the actual plate-unbound VisitorPass.
        payload = {"visitor_pass_id": str(pass_id), "decision": "granted", "person_id": None,
                   "vehicle_id": None, "access_event_id": str(event_id), "occurred_at": observed_at.isoformat()}
        event_type = "visitor_pass.used"
    if origin == "phrase":
        payload = {"phrase": "Synthetic open", "user_id": str(uuid.uuid4()), "user_role": "admin",
                   "confirmed": True, "confirmation_id": "payload-is-not-an-approval"}
        event_type = "ai.phrase_received"
    if origin == "historical":
        payload.update(backfilled=True, skip_automation_actions=True, skip_notification_actions=True)
    try:
        # Canonical origin intake owns durable occurrence creation after the
        # realtime bridge was retired. Replay the same producer identity to
        # exercise deduplication, then dispatch through the real executor.
        from app.services.workflows.automation_definition import automation_triggers_for_origin
        identities = []
        origin_id = str(event_id or uuid.uuid4())
        async with AsyncSessionLocal() as session:
            for trigger_key, captured in automation_triggers_for_origin(event_type, payload, occurred_at=observed_at.isoformat()):
                first = await automation_intake.reserve_trigger(session, trigger_key, captured,
                    origin_kind=event_type, origin_id=origin_id)
                second = await automation_intake.reserve_trigger(session, trigger_key, captured,
                    origin_kind=event_type, origin_id=origin_id)
                assert first == second
                identities.extend(first)
            await session.commit()
        service = automation_owner.AutomationService()
        for identity in identities:
            await service.dispatcher.run_once(identity)
        notification_service = notification_owner.NotificationService()
        monkeypatch.setattr(notification_service, "delivery_config", AsyncMock(return_value=SimpleNamespace()))
        async with AsyncSessionLocal() as session:
            notices = (await session.scalars(select(NotificationRun.id))).all()
        for identity in notices:
            await notification_service.dispatcher.run_once(identity)
    finally:
        if pass_id:
            async with AsyncSessionLocal() as session:
                visitor = await session.get(VisitorPass, pass_id)
                assert visitor.number_plate is None
                await session.delete(visitor)
                await session.commit()
    runs = await _rows(AutomationRun)
    assert len(runs) == int(origin != "historical")
    assert notifications == ([] if origin == "historical" else ["notify"]), "Safe notification action was lost or bypassed"
    assert len(calls) == int(origin == "known"), "Unconfirmed or denied-unknown origin reached an automatic hardware owner"
    if origin == "visitor":
        action = next(item for item in runs[0].action_results if item["id"] == "hardware")
        assert action["status"] == "skipped" and action["command_sent"] is False
        assert action["reason_code"] == "visitor_already_admitted"
    if origin in {"unknown", "phrase"}:
        action = next(item for item in runs[0].action_results if item["id"] == "hardware")
        assert action["status"] == "skipped" and action["command_sent"] is False
        assert action["reason_code"] == ("unknown_plate_hardware_forbidden" if origin == "unknown" else "requester_confirmation_required")
        assert action["requires_confirmation"] is (origin == "phrase")
        assert runs[0].context["provenance"]["trigger_key"] == trigger


async def test_probe_04_support_dry_run_has_no_actions_or_runs(monkeypatch):
    sink = AsyncMock(side_effect=AssertionError("Dry-run reached action execution"))
    service = automation_owner.AutomationService()
    monkeypatch.setattr(service, "_execute_action", sink)
    result = await service.dry_run_rule({
        "name": "Synthetic preview", "is_active": True,
        "triggers": [{"type": "vehicle.unknown_plate", "config": {}}],
        "conditions": [], "actions": [{"id": "hardware", "type": "gate.open", "config": {}}],
    }, trigger_payload=dict(CASES["recognition_payload"]))
    assert result["dry_run"] and not result["executed"]
    assert not result["action_previews"][0]["would_execute"]
    assert result["action_previews"][0]["reason_code"] == "unknown_plate_hardware_forbidden"
    assert not await _rows(AutomationRun) and not await _rows(GateCommandRecord)
    sink.assert_not_awaited()


async def _admin():
    async with AsyncSessionLocal() as session:
        user = User(username="synthetic-admin", full_name="Synthetic Admin", password_hash="not-a-real-password-hash", role=UserRole.ADMIN, is_active=True)
        session.add(user)
        await session.commit()
        return user.id


async def _approval_setup(monkeypatch):
    user_id = await _admin()
    service = ChatService()
    calls = []

    async def inert_gate(arguments):
        if not arguments.get("confirm"):
            return {"requires_confirmation": True, "confirmation_field": "confirm", "target": "Synthetic Gate"}
        calls.append("confirmed-inert-tool")
        return {"opened": True, "accepted": True, "action": "open", "target": "Synthetic Gate"}

    service._tools["open_gate"] = replace(service._tools["open_gate"], handler=inert_gate)
    monkeypatch.setattr(service, "_update_memory", AsyncMock())  # Optional reflection is outside approval ownership.
    monkeypatch.setattr(service, "_run_provider_agent_loop", AsyncMock(side_effect=AssertionError("No LLM call is allowed")))
    session_id = await service._ensure_session(None)
    pending = await service._store_pending_agent_action(
        session_id,
        {"name": "open_gate", "arguments": {"target": "Synthetic Gate", "confirm": False},
         "output": {"requires_confirmation": True, "confirmation_field": "confirm", "target": "Synthetic Gate"}},
        [], IntentRoute(intents=("Gate_Hardware",), confidence=1, requires_entity_resolution=False, reason="Synthetic preview"),
        [service._tools["open_gate"]], provider_name="local", user_message="Synthetic open preview",
        user_id=str(user_id), actor_context={"user": {"id": str(user_id), "role": "admin"}}, iteration=0,
    )
    assert not calls
    app = FastAPI()
    app.include_router(ai_api.router, prefix="/api/v1/ai")
    monkeypatch.setattr(ai_api, "chat_service", service)

    async def current_synthetic_actor():
        # Current actor is loaded for each HTTP request, not a shared ORM instance.
        async with AsyncSessionLocal() as session:
            return await session.get(User, user_id)

    app.dependency_overrides[ai_api.require_current_user] = current_synthetic_actor
    request = {"session_id": str(session_id), "confirmation_id": pending["confirmation_id"], "decision": "confirm"}
    return service, app, request, calls


async def test_probe_05_concurrent_v3_confirmation_claims_once(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    original_decide = service._approvals.decide
    read_barrier = asyncio.Barrier(2)

    async def synchronized_real_claim(*args, **kwargs):
        pending = await original_decide(*args, **kwargs)
        # Both real database claims complete before the winning request invokes
        # a handler. The second sees the durable claim through a fresh session.
        await _bounded(read_barrier.wait())
        return pending

    monkeypatch.setattr(service._approvals, "decide", synchronized_real_claim)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid", trust_env=False) as client:
        responses = await _bounded(asyncio.gather(*(
            client.post("/api/v1/ai/chat/confirm", json=request) for _ in range(2)
        )))
    assert [response.status_code for response in responses] == [200, 200], "Probe failed before reaching the approval boundary"
    assert calls == ["confirmed-inert-tool"], "Two fresh-session confirmations invoked the same approved action twice"


async def test_probe_05_support_preview_and_sequential_replay_are_inert(monkeypatch):
    service, app, request, calls = await _approval_setup(monkeypatch)
    assert not calls
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid", trust_env=False) as client:
        first = await client.post("/api/v1/ai/chat/confirm", json=request)
        second = await client.post("/api/v1/ai/chat/confirm", json=request)
    assert first.status_code == second.status_code == 200
    assert calls == ["confirmed-inert-tool"]
    sessions = await _rows(ChatSession)
    assert "pending_agent_action" not in (sessions[0].context or {})


async def _presence_history():
    async with AsyncSessionLocal() as session:
        person = Person(display_name="Synthetic Resident", is_active=True)
        session.add(person)
        await session.flush()
        event = AccessEvent(person_id=person.id, registration_number="SYNTH01", direction=AccessDirection.EXIT,
                            decision=AccessDecision.GRANTED, confidence=.99, source="synthetic-recovery",
                            occurred_at=NOW, timing_classification=TimingClassification.NORMAL, raw_payload={})
        session.add(event)
        await session.flush()
        session.add(Presence(person_id=person.id, state=PresenceState.EXITED, last_event_id=event.id, last_changed_at=NOW))
        session.add(MovementSagaRecord(idempotency_key="synthetic-committed-exit", source="synthetic-recovery",
                                      occurred_at=NOW, access_event_id=event.id, state=MovementSagaState.COMPLETED,
                                      presence_committed=True, gate_command_required=False, decision_payload={}, intent_payload={}, state_history=[]))
        await session.commit()
        return person.id, event.id


@pytest.mark.parametrize("saga_state", [MovementSagaState.FAILED, MovementSagaState.PHYSICAL_COMMAND_PENDING, MovementSagaState.RECONCILIATION_REQUIRED])
async def test_probe_06_restart_does_not_promote_uncompleted_grant(saga_state):
    person_id, committed_event_id = await _presence_history()
    async with AsyncSessionLocal() as session:
        uncompleted = AccessEvent(person_id=person_id, registration_number="SYNTH01", direction=AccessDirection.ENTRY,
                                  decision=AccessDecision.GRANTED, confidence=.99, source="synthetic-recovery",
                                  occurred_at=NOW + timedelta(minutes=10), timing_classification=TimingClassification.NORMAL, raw_payload={})
        session.add(uncompleted)
        await session.flush()
        session.add(MovementSagaRecord(idempotency_key="synthetic-uncompleted-grant", source="synthetic-recovery",
                                      occurred_at=uncompleted.occurred_at, access_event_id=uncompleted.id, state=saga_state,
                                      presence_committed=False, gate_command_required=True, decision_payload={}, intent_payload={}, state_history=[]))
        await session.commit()
    async with AsyncSessionLocal() as session:
        from app.services.movement.admission import finalize_in_session
        historical_saga = await session.scalar(select(MovementSagaRecord).where(
            MovementSagaRecord.access_event_id == committed_event_id))
        await finalize_in_session(session, saga_id=historical_saga.id, mode="historical",
                                  historical_evidence={"source": "synthetic_restart_repair"})
        await session.commit()
    presence, = await _rows(Presence)
    assert (presence.state, presence.last_event_id) == (PresenceState.EXITED, committed_event_id), (
        "History repair treated authorization GRANTED as a completed movement despite failed/pending actuation"
    )
    assert not await _rows(GateCommandRecord)


@pytest.mark.parametrize("minutes", [-10, 10], ids=["stale-history", "newer-history-support"])
async def test_probe_06_alfred_historical_write_does_not_rewind_presence(monkeypatch, minutes):
    monkeypatch.setattr(historical_alfred, "get_runtime_config", AsyncMock(return_value=SimpleNamespace(
        site_timezone="Europe/London", lpr_debounce_max_seconds=10, lpr_vehicle_session_idle_seconds=90)))
    person_id, committed_event_id = await _presence_history()
    user_id = await _admin()
    candidate = {
        "person_id": person_id, "registration_number": "SYNTH01", "captured_at": NOW + timedelta(minutes=minutes),
        "direction": "entry", "decision": "granted", "confidence": .99, "source": "synthetic-history",
        "evidence_kind": "protect_event", "label": "Synthetic Resident", "reason": "Synthetic historical correction",
    }
    # Only the external evidence acquisition is replaced. The registered Alfred
    # handler's permission, duplicate check, event/presence/audit writes are real.
    monkeypatch.setattr(historical_alfred, "_backfill_candidate", AsyncMock(return_value=candidate))
    token = set_chat_tool_context({"user_id": str(user_id), "user_role": "admin"})
    try:
        result = await historical_alfred.backfill_access_event_from_protect({"confirm": True})
    finally:
        set_chat_tool_context({}, token=token)
    assert result["backfilled"]
    presence, = await _rows(Presence)
    if minutes < 0:
        assert (presence.state, presence.last_event_id, presence.last_changed_at) == (PresenceState.EXITED, committed_event_id, NOW), (
            "Alfred historical repair overwrote newer committed presence with stale evidence"
        )
    else:
        assert presence.state == PresenceState.PRESENT and str(presence.last_event_id) == result["access_event_id"]
    assert not await _rows(GateCommandRecord)
    assert event_bus.publish.await_args.args[1]["skip_automation_actions"] is True
    assert event_bus.publish.await_args.args[1]["skip_notification_actions"] is True
