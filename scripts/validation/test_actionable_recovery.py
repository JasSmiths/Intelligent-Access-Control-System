"""Guarded actionable-output recovery contracts on disposable PostgreSQL.

All contexts, commands, and notification runs are synthetic. The imported fixture
runs the loopback-only guard before any application import. Gate and mobile
providers are inert doubles; no hardware, worker, or network path starts here.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text, update

from app.db.session import AsyncSessionLocal
from app.models import AuditLog, NotificationActionContext, NotificationRun, Person, User
from app.models.enums import UserRole
from app.modules.access_devices.base import gate_receipt_projection
from app.modules.gate.base import GateCommandDelivery, GateState
from app.modules.notifications.home_assistant_mobile import HomeAssistantMobileAppNotifier
from app.services import actionable_notifications as actionable
from app.services import notifications as notification_owner
from app.services.actionable_notifications import (
    ActionIdentity,
    ActionableNotificationService,
    GATE_FORCE_OPEN_ACTION,
    GATE_OPEN_ACTION,
    GateActionOutcome,
    PreparedForceChild,
)
from app.services.gate_commands import GateCommandOutcome
from app.services.notification_runs import NotificationRunStore
from app.services.notifications import NotificationService


pytestmark = pytest.mark.asyncio
WAIT_SECONDS = 8
NOTIFY_SERVICE = "notify.mobile_app_synthetic_recovery"
REGISTRATION_NUMBER = "SYNTHETIC"


def runtime_config(label: str) -> SimpleNamespace:
    return SimpleNamespace(
        home_assistant_url=f"https://{label}.invalid",
        home_assistant_token=f"{label}-synthetic-only",
        apprise_urls="",
        site_timezone="Europe/London",
        schedule_default_policy="deny",
        llm_provider="local",
    )


def manual_target_plan() -> dict[str, Any]:
    return {
        "version": 1,
        "action": "open",
        "target_device_key": None,
        "require_admission": False,
        "gate_only": True,
        "automatic_entry_policy": False,
        "targets": [{
            "target_device_id": str(uuid.uuid4()),
            "device_key": "synthetic_esp_home_gate",
            "kind": "gate",
            "binding_fingerprint": "synthetic-esp-home-binding",
        }],
    }


def canonical_gate_projection(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "backend/tests/contracts/fixtures/gates/command_receipts.json"
    )
    fixture = json.loads(fixture_path.read_text())
    case = next(candidate for candidate in fixture["cases"] if candidate["name"] == name)
    expected = case["outcome"]
    return (
        gate_receipt_projection(
            expected["target_receipts"],
            admission_target_device_id=case["admission_target_device_id"],
            expected_target_count=case["expected_target_count"],
        ),
        expected,
    )


async def bounded(awaitable):
    """Bound a wait without using time as a race or ordering primitive."""
    return await asyncio.wait_for(awaitable, timeout=WAIT_SECONDS)


@pytest_asyncio.fixture(autouse=True)
async def actionable_resources(isolated_resources, monkeypatch):
    initial = runtime_config("initial")

    async def clear_rows() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(text(
                "TRUNCATE notification_runs, notification_action_contexts, audit_logs, people, users CASCADE"
            ))
            await session.commit()

    await clear_rows()
    actionable.get_actionable_notification_service.cache_clear()
    monkeypatch.setattr(actionable, "get_runtime_config", AsyncMock(return_value=initial))
    monkeypatch.setattr(actionable, "get_runtime_config_for_session", AsyncMock(return_value=initial))
    monkeypatch.setattr(notification_owner, "get_runtime_config", AsyncMock(return_value=initial))
    monkeypatch.setattr(notification_owner, "get_runtime_config_for_session", AsyncMock(return_value=initial))
    yield initial
    actionable.get_actionable_notification_service.cache_clear()
    await clear_rows()


async def synthetic_context(
    runtime: Any,
    *,
    context_id: uuid.UUID | None = None,
    action: str = GATE_OPEN_ACTION,
    consumed_at: datetime | None = None,
    outcome: str | None = None,
    dispatch_state: str = "active",
    lease_expires_at: datetime | None = None,
) -> SimpleNamespace:
    context_id = context_id or uuid.uuid4()
    token = actionable._derived_action_token(context_id, action)
    target_plan = manual_target_plan()
    async with AsyncSessionLocal() as session:
        now = await session.scalar(select(func.clock_timestamp()))
        person = Person(
            first_name="Synthetic",
            last_name="Recovery",
            display_name="Synthetic Recovery",
            home_assistant_mobile_app_notify_service=NOTIFY_SERVICE,
            is_active=True,
        )
        session.add(person)
        await session.flush()
        user = User(
            username="synthetic-actionable-" + uuid.uuid4().hex,
            first_name="Synthetic",
            last_name="Recovery",
            full_name="Synthetic Recovery",
            password_hash="inert-unused",
            role=UserRole.ADMIN,
            is_active=True,
            auth_session_version=7,
            person_id=person.id,
        )
        session.add(user)
        await session.flush()
        identity = ActionIdentity(person=person, user=user)
        metadata = actionable._new_context_metadata(
            context_id=context_id,
            action=action,
            identity=identity,
            notify_service=NOTIFY_SERVICE,
            target_plan=target_plan,
            mobile_configuration_binding=actionable._mobile_configuration_binding(runtime),
            source_event_type="synthetic_actionable_recovery",
            source_subject="Synthetic actionable recovery",
        )
        if consumed_at is not None:
            metadata["dispatch_state"] = dispatch_state
            metadata["dispatch_lease_expires_at"] = actionable._utc_iso_microseconds(
                lease_expires_at or now + timedelta(minutes=5)
            )
        row = NotificationActionContext(
            id=context_id,
            token_hash=actionable._token_hash(token),
            action=action,
            notify_service=NOTIFY_SERVICE,
            registration_number=REGISTRATION_NUMBER,
            telemetry_trace_id="1" * 32,
            person_id=person.id,
            actor_user_id=user.id,
            expires_at=now + timedelta(minutes=10),
            consumed_at=consumed_at,
            outcome=outcome if outcome is not None else "dispatch_pending" if consumed_at is not None else None,
            outcome_detail=None,
            metadata_=metadata,
        )
        session.add(row)
        await session.commit()
    return SimpleNamespace(
        token=token,
        context_id=context_id,
        person_id=person.id,
        user_id=user.id,
        target_plan=target_plan,
    )


async def load_context(context_id: uuid.UUID) -> NotificationActionContext:
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationActionContext, context_id)
        assert row is not None
        return row


async def load_bound(service: ActionableNotificationService, context_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationActionContext, context_id)
        assert row is not None
        return service._bound_from_row(row)


async def output_run(context_id: uuid.UUID, result_kind: str) -> NotificationRun:
    run_id = actionable._output_run_id(context_id, result_kind)
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationRun, run_id)
        assert row is not None
        return row


def gate_result(
    intent,
    *,
    accepted: bool,
    state: GateState,
    detail: str,
    delivery: GateCommandDelivery,
    mechanically_confirmed: bool,
    requires_reconciliation: bool,
    target_receipts: list[dict[str, Any]] | None = None,
) -> GateCommandOutcome:
    now = datetime.now(tz=UTC)
    return GateCommandOutcome(
        intent=intent,
        accepted=accepted,
        state=state,
        detail=detail,
        delivery=delivery,
        mechanically_confirmed=mechanically_confirmed,
        reconciliation_required=requires_reconciliation,
        target_receipts=target_receipts or [],
        started_at=now,
        completed_at=now,
    )


def install_gate_transport(monkeypatch, *, execute=None, receipt=None, preview=None):
    execute_calls = []
    receipt_calls = []

    class InertCoordinator:
        async def execute_open(self, intent):
            execute_calls.append(intent)
            if execute is None:
                raise AssertionError("Synthetic recovery must never execute a gate command.")
            result = execute(intent)
            return await result if inspect.isawaitable(result) else result

        async def get_receipt(self, *, intent_id):
            receipt_calls.append(intent_id)
            if receipt is None:
                return None
            result = receipt(intent_id)
            return await result if inspect.isawaitable(result) else result

        async def preview_manual_gate_open(self):
            if preview is None:
                return manual_target_plan()
            result = preview()
            return await result if inspect.isawaitable(result) else result

    monkeypatch.setattr(actionable, "get_gate_command_coordinator", lambda: InertCoordinator())
    return execute_calls, receipt_calls


class InertMobileClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_service(self, service_name, payload, *, runtime_config=None):
        self.calls.append({
            "service_name": service_name,
            "payload": payload,
            "runtime_config": runtime_config,
        })
        return {}


def install_real_mobile_notifier(monkeypatch, client: InertMobileClient) -> None:
    monkeypatch.setattr(
        notification_owner,
        "HomeAssistantMobileAppNotifier",
        lambda: HomeAssistantMobileAppNotifier(client),
    )


async def finalize_output(
    service: ActionableNotificationService,
    context_id: uuid.UUID,
    outcome: GateActionOutcome,
    *,
    result_kind: str = "normal_result",
) -> uuid.UUID:
    bound = await load_bound(service, context_id)
    result = await service._finalize_action_result(
        bound,
        outcome,
        force=bound.action == GATE_FORCE_OPEN_ACTION,
        result_kind=result_kind,
    )
    assert result.changed and result.run_id is not None
    return result.run_id


async def test_create_action_binds_exact_admin_target_and_mobile_configuration(actionable_resources, monkeypatch):
    target_plan = manual_target_plan()

    class PreviewCoordinator:
        async def preview_manual_gate_open(self):
            return target_plan

    monkeypatch.setattr(actionable, "get_gate_command_coordinator", lambda: PreviewCoordinator())
    data = await synthetic_context(actionable_resources)
    # The helper already created an Admin linked to the fixed service. Create a
    # new button through the public creation path and inspect only durable facts.
    context = notification_owner.NotificationContext(
        event_type="unauthorized_plate",
        subject=REGISTRATION_NUMBER,
        severity="warning",
        facts={"registration_number": REGISTRATION_NUMBER},
    )
    action = await ActionableNotificationService().create_gate_open_action(
        context=context,
        notify_service=NOTIFY_SERVICE,
    )

    assert action is not None and action["title"] == "Open All Gates"
    token = action["action"].removeprefix(actionable.GATE_OPEN_PREFIX)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(select(NotificationActionContext).where(
            NotificationActionContext.token_hash == actionable._token_hash(token)
        ))
    assert row is not None
    assert row.actor_user_id == data.user_id and row.person_id == data.person_id
    assert row.metadata_["target_plan"] == target_plan
    assert row.metadata_["mobile_configuration_binding"] == actionable._mobile_configuration_binding(actionable_resources)
    assert row.metadata_["destination_binding"] == actionable._destination_binding(row.id, NOTIFY_SERVICE)
    assert token not in json.dumps(row.metadata_, sort_keys=True)


async def test_changed_mobile_configuration_omits_a_button_bound_to_an_old_delivery_snapshot(
    actionable_resources,
    monkeypatch,
):
    class PreviewCoordinator:
        async def preview_manual_gate_open(self):
            return manual_target_plan()

    monkeypatch.setattr(actionable, "get_gate_command_coordinator", lambda: PreviewCoordinator())
    data = await synthetic_context(actionable_resources)
    changed = runtime_config("changed-before-button-creation")
    monkeypatch.setattr(actionable, "get_runtime_config_for_session", AsyncMock(return_value=changed))
    context = notification_owner.NotificationContext(
        event_type="unauthorized_plate",
        subject=REGISTRATION_NUMBER,
        severity="warning",
        facts={"registration_number": REGISTRATION_NUMBER},
    )

    action = await ActionableNotificationService().create_gate_open_action(
        context=context,
        notify_service=NOTIFY_SERVICE,
        runtime_config=actionable_resources,
    )

    assert action is None
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count()).select_from(NotificationActionContext).where(
                NotificationActionContext.actor_user_id == data.user_id
            )
        )
    # The fixture's unrelated synthetic context is retained; no second token was
    # created under the mismatched configuration snapshot.
    assert count == 1


async def test_inactive_admin_still_gets_a_durable_identity_denied_result(actionable_resources, monkeypatch):
    data = await synthetic_context(actionable_resources)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.id == data.user_id)
            .values(is_active=False)
        )
        await session.commit()

    class FailingCoordinator:
        async def execute_open(self, *_args, **_kwargs):
            raise AssertionError("An inactive requester must be denied before gate transport.")

    monkeypatch.setattr(actionable, "get_gate_command_coordinator", lambda: FailingCoordinator())

    outcome = await ActionableNotificationService().execute_gate_action(data.token, force=False)

    row = await load_context(data.context_id)
    run = await output_run(data.context_id, "identity_denied")
    async with AsyncSessionLocal() as session:
        audit = await session.scalar(
            select(AuditLog)
            .where(AuditLog.target_id == str(data.context_id))
            .order_by(AuditLog.timestamp.desc())
        )

    assert outcome.skipped_before_command is True
    assert row.outcome == "failed"
    assert row.metadata_["dispatch_state"] == "finalized"
    assert run.status == "queued"
    assert audit is not None and audit.actor_user_id == data.user_id


async def test_concurrent_token_consumption_keeps_original_dispatch_fence(actionable_resources):
    data = await synthetic_context(actionable_resources)
    services = [ActionableNotificationService(), ActionableNotificationService()]
    ready = asyncio.Event()

    async def attempt(service):
        await ready.wait()
        return await service._consume_context(data.token, expected_action=GATE_OPEN_ACTION)

    tasks = [asyncio.create_task(attempt(service)) for service in services]
    try:
        ready.set()
        results = await bounded(asyncio.gather(*tasks))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    bound_results = [result for result, _ in results if result is not None]
    assert len(bound_results) == 1
    assert sorted(reason for _, reason in results) == ["", "This notification action has already been used."]
    row = await load_context(data.context_id)
    assert row.consumed_at is not None
    assert row.outcome == "dispatch_pending"
    assert row.metadata_["dispatch_state"] == "active"
    assert "dispatch_lease_expires_at" in row.metadata_
    assert "already_used" in row.metadata_["outputs"]
    replay = await output_run(data.context_id, "already_used")
    assert replay.status == "queued"
    assert replay.delivery_plan[0]["action"]["delivery_mode"] == "literal"


async def test_wrong_action_replay_preserves_an_attempting_context(actionable_resources):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC) - timedelta(seconds=1),
        outcome="attempting",
        dispatch_state="attempting",
    )
    service = ActionableNotificationService()

    bound, reason = await service._consume_context(data.token, expected_action=GATE_FORCE_OPEN_ACTION)

    assert bound is None
    assert reason == "Notification action type did not match the stored context."
    row = await load_context(data.context_id)
    assert row.outcome == "attempting"
    assert row.metadata_["dispatch_state"] == "attempting"
    assert "wrong_action" in row.metadata_["outputs"]
    run = await output_run(data.context_id, "wrong_action")
    assert data.token not in json.dumps({"plan": run.delivery_plan, "context": run.context}, sort_keys=True)


async def test_expiry_is_checked_against_database_time_and_queues_literal_result(actionable_resources):
    data = await synthetic_context(actionable_resources)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(NotificationActionContext)
            .where(NotificationActionContext.id == data.context_id)
            .values(expires_at=text("clock_timestamp() - interval '1 second'"))
        )
        await session.commit()

    bound, reason = await ActionableNotificationService()._consume_context(data.token, expected_action=GATE_OPEN_ACTION)

    assert bound is None
    assert reason == "This notification action has expired."
    row = await load_context(data.context_id)
    assert row.consumed_at is None
    assert row.outcome == "failed"
    assert row.metadata_["dispatch_state"] == "finalized"
    assert (await output_run(data.context_id, "expired")).delivery_plan[0]["action"]["delivery_mode"] == "literal"


async def test_expiry_is_rechecked_after_a_real_blocked_context_lock(actionable_resources):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()

    async def wait_for_blocked_context_select():
        async with AsyncSessionLocal() as probe:
            while True:
                blocked = await probe.scalar(text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'active' "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%notification_action_contexts%' "
                    "AND query ILIKE '%FOR UPDATE%'"
                    ")"
                ))
                if blocked:
                    return
                await probe.rollback()

    async with AsyncSessionLocal() as holder:
        await holder.scalar(
            select(NotificationActionContext)
            .where(NotificationActionContext.id == data.context_id)
            .with_for_update()
        )
        consumer = asyncio.create_task(service._consume_context(data.token, expected_action=GATE_OPEN_ACTION))
        try:
            await bounded(wait_for_blocked_context_select())
            await holder.execute(
                update(NotificationActionContext)
                .where(NotificationActionContext.id == data.context_id)
                .values(expires_at=text("clock_timestamp()"))
            )
            await holder.commit()
            bound, reason = await bounded(consumer)
        finally:
            if not consumer.done():
                consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    assert bound is None
    assert reason == "This notification action has expired."
    row = await load_context(data.context_id)
    assert row.consumed_at is None and row.outcome == "failed"


async def test_duplicate_notice_finalization_runs_after_context_lock_release(actionable_resources, monkeypatch):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()
    lock_released = asyncio.Event()

    async def finalizer(*_args, **_kwargs):
        async with AsyncSessionLocal() as probe:
            await probe.scalar(
                select(NotificationActionContext)
                .where(NotificationActionContext.id == data.context_id)
                .with_for_update(nowait=True)
            )
            await probe.rollback()
        lock_released.set()
        return actionable.FinalizationResult(True)

    monkeypatch.setattr(service, "_finalize_action_result", finalizer)
    bound, reason = await service._consume_context(data.token, expected_action=GATE_FORCE_OPEN_ACTION)

    assert bound is None
    assert reason == "Notification action type did not match the stored context."
    assert lock_released.is_set()


@pytest.mark.parametrize("kind", ["wrong_action", "already_used"])
async def test_actor_held_checkpoint_does_not_deadlock_notice_finalization(
    actionable_resources,
    kind,
):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC) if kind == "already_used" else None,
        outcome="dispatch_pending" if kind == "already_used" else None,
    )
    service = ActionableNotificationService()

    async def wait_for_blocked_actor_lock() -> None:
        async with AsyncSessionLocal() as probe:
            while True:
                blocked = await probe.scalar(text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'active' "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%FROM users%' "
                    "AND query ILIKE '%FOR UPDATE%'"
                    ")"
                ))
                if blocked:
                    return
                await probe.rollback()

    expected_action = GATE_OPEN_ACTION if kind == "already_used" else GATE_FORCE_OPEN_ACTION
    expected_reason = (
        "This notification action has already been used."
        if kind == "already_used"
        else "Notification action type did not match the stored context."
    )
    consumer = None
    async with AsyncSessionLocal() as actor_holder:
        await actor_holder.scalar(
            select(User)
            .where(User.id == data.user_id)
            .with_for_update()
        )
        consumer = asyncio.create_task(
            service._consume_context(data.token, expected_action=expected_action)
        )
        try:
            await bounded(wait_for_blocked_actor_lock())
            # If result finalization had taken context first, this lock would
            # complete the actor/context cycle and deadlock the checkpoint.
            await actor_holder.scalar(
                select(NotificationActionContext)
                .where(NotificationActionContext.id == data.context_id)
                .with_for_update(nowait=True)
            )
        finally:
            await actor_holder.rollback()

    try:
        assert consumer is not None
        bound, reason = await bounded(consumer)
    finally:
        if consumer is not None and not consumer.done():
            consumer.cancel()
        if consumer is not None:
            await asyncio.gather(consumer, return_exceptions=True)

    assert bound is None
    assert reason == expected_reason
    row = await load_context(data.context_id)
    assert kind in row.metadata_["outputs"]


async def test_unknown_gate_receipt_queues_review_without_force_context(actionable_resources, monkeypatch):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()
    monkeypatch.setattr(actionable, "_active_gate_malfunction", AsyncMock(return_value=None))
    monkeypatch.setattr(actionable, "_is_maintenance_mode_active", AsyncMock(return_value=False))
    execute_calls, _ = install_gate_transport(
        monkeypatch,
        execute=lambda intent: gate_result(
            intent,
            accepted=False,
            state=GateState.UNKNOWN,
            detail="Synthetic provider response was lost.",
            delivery=GateCommandDelivery.UNKNOWN,
            mechanically_confirmed=False,
            requires_reconciliation=True,
        ),
    )

    outcome = await service.execute_gate_action(data.token, force=False)

    assert execute_calls and outcome.delivery == GateCommandDelivery.UNKNOWN
    row = await load_context(data.context_id)
    assert row.outcome == "pending_reconciliation"
    assert row.metadata_["dispatch_state"] == "finalized"
    assert (await output_run(data.context_id, "normal_result")).delivery_plan[0]["action"]["title"] == "Gate command needs review"
    async with AsyncSessionLocal() as session:
        force_count = await session.scalar(
            select(func.count()).select_from(NotificationActionContext).where(
                NotificationActionContext.action == GATE_FORCE_OPEN_ACTION
            )
        )
    assert force_count == 0


async def test_partial_gate_receipt_queues_review_without_force_context(actionable_resources, monkeypatch):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()
    monkeypatch.setattr(actionable, "_active_gate_malfunction", AsyncMock(return_value=None))
    monkeypatch.setattr(actionable, "_is_maintenance_mode_active", AsyncMock(return_value=False))
    execute_calls, _ = install_gate_transport(
        monkeypatch,
        execute=lambda intent: gate_result(
            intent,
            accepted=False,
            state=GateState.UNKNOWN,
            detail="Synthetic aggregate contained accepted and rejected target receipts.",
            delivery=GateCommandDelivery.PARTIAL,
            mechanically_confirmed=False,
            requires_reconciliation=False,
            target_receipts=[
                {"delivery": "accepted", "device_key": "first"},
                {"delivery": "rejected", "device_key": "second"},
            ],
        ),
    )

    outcome = await service.execute_gate_action(data.token, force=False)

    assert execute_calls and outcome.delivery == GateCommandDelivery.PARTIAL
    row = await load_context(data.context_id)
    assert row.outcome == "pending_reconciliation"
    async with AsyncSessionLocal() as session:
        force_count = await session.scalar(
            select(func.count()).select_from(NotificationActionContext).where(
                NotificationActionContext.action == GATE_FORCE_OPEN_ACTION
            )
        )
    assert force_count == 0


async def test_accepted_unverified_result_uses_accepted_wording_without_force_context(actionable_resources, monkeypatch):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()
    monkeypatch.setattr(actionable, "_active_gate_malfunction", AsyncMock(return_value=None))
    monkeypatch.setattr(actionable, "_is_maintenance_mode_active", AsyncMock(return_value=False))
    execute_calls, _ = install_gate_transport(
        monkeypatch,
        execute=lambda intent: gate_result(
            intent,
            accepted=True,
            state=GateState.OPENING,
            detail="Synthetic controller accepted the request; physical state is not verified.",
            delivery=GateCommandDelivery.ACCEPTED,
            mechanically_confirmed=False,
            requires_reconciliation=True,
        ),
    )

    outcome = await service.execute_gate_action(data.token, force=False)

    assert execute_calls and outcome.accepted is True
    row = await load_context(data.context_id)
    assert row.outcome == "pending_reconciliation"
    action = (await output_run(data.context_id, "normal_result")).delivery_plan[0]["action"]
    assert action["title"] == "Gate command accepted"
    assert "opened" not in f"{action['title']} {action['message']}".lower()
    async with AsyncSessionLocal() as session:
        force_count = await session.scalar(
            select(func.count()).select_from(NotificationActionContext).where(
                NotificationActionContext.action == GATE_FORCE_OPEN_ACTION
            )
        )
    assert force_count == 0


async def test_verified_not_sent_gate_receipt_is_satisfied_without_a_force_child(actionable_resources):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    target_id = str(uuid.uuid4())
    projection = gate_receipt_projection(
        [{
            "target_device_id": target_id,
            "accepted": False,
            "delivery": "not_sent",
            "state": "opening",
            "verified": True,
            "requires_reconciliation": False,
        }],
        admission_target_device_id=target_id,
        expected_target_count=1,
    )
    outcome = GateActionOutcome(
        accepted=projection["accepted"],
        detail="A current target observation showed the gate was already opening.",
        state=projection["state"],
        delivery=projection["delivery"],
        mechanically_confirmed=projection["mechanically_confirmed"],
        requires_reconciliation=projection["requires_reconciliation"],
        target_receipts=tuple(projection["target_receipts"]),
    )
    service = ActionableNotificationService()
    run_id = await finalize_output(service, data.context_id, outcome)

    row = await load_context(data.context_id)
    run = await output_run(data.context_id, "normal_result")
    async with AsyncSessionLocal() as session:
        audit = await session.scalar(
            select(AuditLog)
            .where(AuditLog.target_id == str(data.context_id))
            .order_by(AuditLog.timestamp.desc())
        )
        force_count = await session.scalar(
            select(func.count()).select_from(NotificationActionContext).where(
                NotificationActionContext.action == GATE_FORCE_OPEN_ACTION
            )
        )

    assert run.id == run_id
    assert projection["delivery"] == "not_sent"
    assert projection["mechanically_confirmed"] is True
    assert row.outcome == "success"
    assert run.delivery_plan[0]["action"]["title"] == "Gate already opening"
    assert audit is not None and audit.outcome == "success"
    assert audit.metadata_["delivery"] == "not_sent"
    assert force_count == 0


@pytest.mark.parametrize(
    ("case_name", "expected_context_outcome", "expected_audit_outcome", "needs_review"),
    [
        ("partial_already_open_other_accepted", "partial", "partial", False),
        ("partial_entry_verified_other_rejected", "partial", "partial", False),
        ("partial_entry_verified_other_not_sent", "partial", "partial", False),
    ],
)
async def test_canonical_partial_receipts_preserve_per_target_truth(
    actionable_resources,
    case_name,
    expected_context_outcome,
    expected_audit_outcome,
    needs_review,
):
    projection, expected = canonical_gate_projection(case_name)
    assert projection == expected
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    outcome = GateActionOutcome(
        accepted=projection["accepted"],
        detail="Canonical mixed target receipt projection.",
        state=projection["state"],
        delivery=projection["delivery"],
        mechanically_confirmed=projection["mechanically_confirmed"],
        requires_reconciliation=projection["requires_reconciliation"],
        target_receipts=tuple(projection["target_receipts"]),
    )

    await finalize_output(ActionableNotificationService(), data.context_id, outcome)

    row = await load_context(data.context_id)
    run = await output_run(data.context_id, "normal_result")
    async with AsyncSessionLocal() as session:
        audit = await session.scalar(
            select(AuditLog)
            .where(AuditLog.target_id == str(data.context_id))
            .order_by(AuditLog.timestamp.desc())
        )
        force_count = await session.scalar(
            select(func.count()).select_from(NotificationActionContext).where(
                NotificationActionContext.action == GATE_FORCE_OPEN_ACTION
            )
        )

    assert projection["delivery"] == "partial"
    assert actionable._outcome_needs_review(outcome) is needs_review
    assert actionable._recorded_outcome(outcome) == expected_context_outcome
    assert actionable._force_child_allowed(outcome) is False
    assert row.outcome == expected_context_outcome
    assert audit is not None and audit.outcome == expected_audit_outcome
    assert audit.metadata_["delivery"] == "partial"
    assert [receipt["delivery"] for receipt in audit.metadata_["target_receipts"]] == [
        receipt["delivery"] for receipt in projection["target_receipts"]
    ]
    assert force_count == 0
    action = run.delivery_plan[0]["action"]
    assert audit.level == "info"
    assert "needs reconciliation" not in action["message"].lower()
    if case_name == "partial_already_open_other_accepted":
        assert action["title"] == "Gate opened"
        assert "no gate open command was sent" not in action["message"].lower()
    else:
        assert action["title"] == "Gate partially opened"
        assert "were not confirmed in the requested state" in action["message"].lower()
        assert "rejected" not in action["message"].lower()


async def test_changed_mobile_configuration_blocks_the_final_gate_checkpoint(actionable_resources, monkeypatch):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()
    bound, reason = await service._consume_context(data.token, expected_action=GATE_OPEN_ACTION)
    assert bound is not None and not reason
    changed = runtime_config("changed-ha-endpoint-and-token")
    monkeypatch.setattr(actionable, "get_runtime_config_for_session", AsyncMock(return_value=changed))
    effect_calls = []

    async def execute(intent):
        try:
            async with AsyncSessionLocal() as session:
                await intent.authorize_dispatch(session)
                await session.commit()
        except ValueError:
            effect_calls.append("checkpoint-blocked")
            return gate_result(
                intent,
                accepted=False,
                state=GateState.UNKNOWN,
                detail="Synthetic checkpoint denied before the ESPHome target could receive a command.",
                delivery=GateCommandDelivery.UNKNOWN,
                mechanically_confirmed=False,
                requires_reconciliation=True,
            )
        effect_calls.append("provider-effect")
        raise AssertionError("Changed mobile configuration must prevent a target effect.")

    monkeypatch.setattr(actionable, "_active_gate_malfunction", AsyncMock(return_value=None))
    monkeypatch.setattr(actionable, "_is_maintenance_mode_active", AsyncMock(return_value=False))
    install_gate_transport(monkeypatch, execute=execute)

    # Intake is already committed above. Exercise the normal post-intake path
    # directly so the test reaches the final target-attempt authorization rather
    # than consuming the one-use token a second time.
    async with AsyncSessionLocal() as session:
        identity = await service._current_bound_identity(session, bound)
    assert identity is not None
    outcome = await service._execute_gate(bound, identity, force=False)
    await service._finalize_action_result(
        bound,
        outcome,
        force=False,
        result_kind="normal_result",
    )

    assert outcome.delivery == GateCommandDelivery.UNKNOWN
    assert effect_calls == ["checkpoint-blocked"]
    row = await load_context(data.context_id)
    assert row.outcome == "pending_reconciliation"


async def test_gate_checkpoint_holds_mobile_recipient_through_attempt_commit(
    actionable_resources,
):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()
    bound, reason = await service._consume_context(data.token, expected_action=GATE_OPEN_ACTION)
    assert bound is not None and not reason
    rebind_started = asyncio.Event()

    async def rebind() -> None:
        rebind_started.set()
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Person)
                .where(Person.id == data.person_id)
                .values(home_assistant_mobile_app_notify_service="notify.mobile_app_rebound")
            )
            await session.commit()

    async def wait_for_blocked_person_rebind() -> None:
        async with AsyncSessionLocal() as probe:
            while True:
                blocked = await probe.scalar(text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'active' "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%UPDATE people%'"
                    ")"
                ))
                if blocked:
                    return
                await probe.rollback()

    rebind_task = None
    async with AsyncSessionLocal() as checkpoint:
        await service._authorize_gate_dispatch(checkpoint, bound)
        rebind_task = asyncio.create_task(rebind())
        try:
            await bounded(rebind_started.wait())
            await bounded(wait_for_blocked_person_rebind())
            assert not rebind_task.done()
        finally:
            await checkpoint.commit()

    try:
        assert rebind_task is not None
        await bounded(rebind_task)
    finally:
        if rebind_task is not None and not rebind_task.done():
            rebind_task.cancel()
        if rebind_task is not None:
            await asyncio.gather(rebind_task, return_exceptions=True)

    row = await load_context(data.context_id)
    assert row.outcome == "attempting"
    assert row.metadata_["dispatch_state"] == "attempting"


@pytest.mark.parametrize(
    ("deadline_kind", "expected_reason"),
    [
        ("action", "Notification action expired before dispatch; a fresh confirmation is required."),
        ("lease", "Notification action dispatch lease expired; a fresh confirmation is required."),
    ],
)
async def test_gate_checkpoint_rechecks_deadlines_after_a_blocked_person_lock(
    actionable_resources,
    deadline_kind,
    expected_reason,
):
    data = await synthetic_context(actionable_resources)
    service = ActionableNotificationService()
    bound, reason = await service._consume_context(data.token, expected_action=GATE_OPEN_ACTION)
    assert bound is not None and not reason
    async with AsyncSessionLocal() as session:
        row = await session.get(NotificationActionContext, data.context_id)
        assert row is not None
        deadline = await session.scalar(select(text("clock_timestamp() + interval '1 second'")))
        assert isinstance(deadline, datetime)
        metadata = dict(row.metadata_)
        if deadline_kind == "action":
            row.expires_at = deadline
            metadata["dispatch_lease_expires_at"] = actionable._utc_iso_microseconds(
                deadline + timedelta(minutes=5)
            )
        else:
            row.expires_at = deadline + timedelta(minutes=5)
            metadata["dispatch_lease_expires_at"] = actionable._utc_iso_microseconds(deadline)
        row.metadata_ = metadata
        await session.commit()
    # The durable deadline is part of the binding checked at the final
    # checkpoint, so model the normal consumer with the committed current row.
    bound = await load_bound(service, data.context_id)

    async def wait_for_blocked_person_lock() -> None:
        async with AsyncSessionLocal() as probe:
            while True:
                blocked = await probe.scalar(text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'active' "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%FROM people%' "
                    "AND query ILIKE '%FOR UPDATE%'"
                    ")"
                ))
                if blocked:
                    return
                await probe.rollback()

    async def wait_until_database_deadline() -> None:
        while True:
            async with AsyncSessionLocal() as probe:
                elapsed = await probe.scalar(select(func.clock_timestamp() >= deadline))
                await probe.rollback()
            if elapsed:
                return
            await asyncio.sleep(0.01)

    async def checkpoint() -> str:
        async with AsyncSessionLocal() as session:
            try:
                await service._authorize_gate_dispatch(session, bound)
            except ValueError as exc:
                await session.rollback()
                return str(exc)
            await session.commit()
            return "accepted"

    checkpoint_task = None
    async with AsyncSessionLocal() as holder:
        await holder.scalar(
            select(Person)
            .where(Person.id == data.person_id)
            .with_for_update()
        )
        checkpoint_task = asyncio.create_task(checkpoint())
        try:
            await bounded(wait_for_blocked_person_lock())
            await bounded(wait_until_database_deadline())
        finally:
            await holder.rollback()

    try:
        assert checkpoint_task is not None
        assert await bounded(checkpoint_task) == expected_reason
    finally:
        if checkpoint_task is not None and not checkpoint_task.done():
            checkpoint_task.cancel()
        if checkpoint_task is not None:
            await asyncio.gather(checkpoint_task, return_exceptions=True)

    row = await load_context(data.context_id)
    assert row.outcome == "dispatch_pending"
    assert row.metadata_["dispatch_state"] == "active"


async def test_finalization_rolls_back_context_audit_force_child_and_run_together(actionable_resources):
    class FailingRunStore:
        async def enqueue_prepared_in_session(self, *_args, **_kwargs):
            raise RuntimeError("synthetic prepared-output failure")

    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    service = ActionableNotificationService(run_store=FailingRunStore())
    bound = await load_bound(service, data.context_id)
    prepared = PreparedForceChild(
        context_id=uuid.uuid5(bound.id, actionable.ACTIONABLE_FORCE_CHILD_PURPOSE),
        target_plan=manual_target_plan(),
        mobile_configuration_binding=actionable._mobile_configuration_binding(actionable_resources),
    )
    outcome = GateActionOutcome(
        False,
        "Synthetic definite provider rejection.",
        delivery=GateCommandDelivery.REJECTED,
        target_receipts=({"delivery": "rejected"},),
    )

    with pytest.raises(RuntimeError, match="synthetic prepared-output failure"):
        await service._finalize_action_result(
            bound,
            outcome,
            force=False,
            result_kind="normal_result",
            prepared_force=prepared,
        )

    row = await load_context(data.context_id)
    assert row.outcome == "dispatch_pending"
    assert row.metadata_["dispatch_state"] == "active"
    assert row.metadata_["outputs"] == {}
    async with AsyncSessionLocal() as session:
        force_count = await session.scalar(
            select(func.count()).select_from(NotificationActionContext).where(
                NotificationActionContext.action == GATE_FORCE_OPEN_ACTION
            )
        )
        run_count = await session.scalar(select(func.count()).select_from(NotificationRun))
        audit_count = await session.scalar(select(func.count()).select_from(AuditLog))
    assert force_count == 0 and run_count == 0 and audit_count == 0


async def test_fresh_dispatch_lease_survives_recovery_without_receipt_read(actionable_resources, monkeypatch):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
        lease_expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
    )
    _execute_calls, receipt_calls = install_gate_transport(monkeypatch)

    changed, cursor = await ActionableNotificationService().reconcile_actionable_outputs(limit=25)

    assert changed == 0 and cursor is None
    assert receipt_calls == []
    row = await load_context(data.context_id)
    assert row.outcome == "dispatch_pending"
    assert row.metadata_["dispatch_state"] == "active"


async def test_expired_lease_is_revoked_before_receipt_read_and_never_resends(actionable_resources, monkeypatch):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
        lease_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
    )
    execute_calls = []
    receipt_calls = []

    class RecoveryCoordinator:
        async def execute_open(self, *_args, **_kwargs):
            execute_calls.append(True)
            raise AssertionError("Recovery must never call execute_open.")

        async def get_receipt(self, *, intent_id):
            receipt_calls.append(intent_id)
            async with AsyncSessionLocal() as session:
                row = await session.get(NotificationActionContext, data.context_id)
                assert row is not None
                assert row.outcome == "dispatch_revoked"
                assert row.metadata_["dispatch_state"] == "revoked"
            return None

        async def preview_manual_gate_open(self):
            raise AssertionError("Missing receipt is unknown and must not create a force child.")

    monkeypatch.setattr(actionable, "get_gate_command_coordinator", lambda: RecoveryCoordinator())
    service = ActionableNotificationService()

    changed, cursor = await service.reconcile_actionable_outputs(limit=25)

    assert changed == 1 and cursor is None
    assert receipt_calls == [str(data.context_id)]
    assert execute_calls == []
    row = await load_context(data.context_id)
    assert row.outcome == "pending_reconciliation"
    assert row.metadata_["dispatch_state"] == "finalized"
    run = await output_run(data.context_id, "normal_result")
    assert data.token not in json.dumps({"plan": run.delivery_plan, "context": run.context}, sort_keys=True)
    changed_again, _ = await service.reconcile_actionable_outputs(limit=25)
    assert changed_again == 0 and receipt_calls == [str(data.context_id)]


async def test_one_recovery_output_failure_does_not_block_later_expired_contexts(actionable_resources, monkeypatch):
    first_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    second_id = uuid.UUID("00000000-0000-0000-0000-000000000102")
    first = await synthetic_context(
        actionable_resources,
        context_id=first_id,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
        lease_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
    )
    second = await synthetic_context(
        actionable_resources,
        context_id=second_id,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
        lease_expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
    )
    _execute_calls, receipt_calls = install_gate_transport(monkeypatch)
    service = ActionableNotificationService()
    original = service._finalize_action_result

    async def fail_first(bound, *args, **kwargs):
        if bound.id == first.context_id:
            raise RuntimeError("synthetic first-output failure")
        return await original(bound, *args, **kwargs)

    monkeypatch.setattr(service, "_finalize_action_result", fail_first)

    changed, cursor = await service.reconcile_actionable_outputs(limit=2)

    assert changed == 1 and cursor == second.context_id
    assert receipt_calls == [str(first.context_id), str(second.context_id)]
    first_row = await load_context(first.context_id)
    second_row = await load_context(second.context_id)
    assert first_row.outcome == "dispatch_revoked"
    assert second_row.outcome == "pending_reconciliation"


async def test_literal_output_uses_final_authorized_mobile_snapshot(actionable_resources, monkeypatch):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    service = ActionableNotificationService()
    run_id = await finalize_output(
        service,
        data.context_id,
        GateActionOutcome(
            True,
            "Synthetic controller accepted and verified the command.",
            state="open",
            delivery=GateCommandDelivery.ACCEPTED,
            mechanically_confirmed=True,
        ),
    )
    client = InertMobileClient()
    install_real_mobile_notifier(monkeypatch, client)
    notification_service = NotificationService(run_store=NotificationRunStore())
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(notification_service, name, AsyncMock())

    assert await notification_service.dispatcher.run_once(run_id)
    row = await output_run(data.context_id, "normal_result")
    assert row.status == "provider_accepted"
    assert row.delivery_plan[0]["state"] == "accepted"
    assert len(client.calls) == 1
    assert client.calls[0]["service_name"] == NOTIFY_SERVICE
    assert client.calls[0]["runtime_config"] is actionable_resources
    assert client.calls[0]["payload"]["data"].get("actions") is None


async def test_duplicate_active_mobile_binding_skips_output_delivery(actionable_resources, monkeypatch):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    service = ActionableNotificationService()
    run_id = await finalize_output(
        service,
        data.context_id,
        GateActionOutcome(
            True,
            "Synthetic controller accepted and verified the command.",
            state="open",
            delivery=GateCommandDelivery.ACCEPTED,
            mechanically_confirmed=True,
        ),
    )
    async with AsyncSessionLocal() as session:
        session.add(Person(
            first_name="Duplicate",
            last_name="Recipient",
            display_name="Duplicate Recipient",
            home_assistant_mobile_app_notify_service=NOTIFY_SERVICE,
            is_active=True,
        ))
        await session.commit()

    client = InertMobileClient()
    install_real_mobile_notifier(monkeypatch, client)
    notification_service = NotificationService(run_store=NotificationRunStore())
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(notification_service, name, AsyncMock())

    assert await notification_service.dispatcher.run_once(run_id)
    row = await output_run(data.context_id, "normal_result")
    assert row.status == "skipped"
    assert row.delivery_plan[0]["state"] == "skipped"
    assert row.delivery_plan[0]["reason"] == "actionable_output_requester_changed"
    assert client.calls == []


async def test_held_run_row_fences_recipient_rebind_until_output_checkpoint(
    actionable_resources,
    monkeypatch,
):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    service = ActionableNotificationService()
    run_id = await finalize_output(
        service,
        data.context_id,
        GateActionOutcome(
            True,
            "Synthetic controller accepted and verified the command.",
            state="open",
            delivery=GateCommandDelivery.ACCEPTED,
            mechanically_confirmed=True,
        ),
    )
    client = InertMobileClient()
    install_real_mobile_notifier(monkeypatch, client)
    notification_service = NotificationService(run_store=NotificationRunStore())
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(notification_service, name, AsyncMock())
    claimed = await notification_service.run_store.claim(run_id)
    assert claimed is not None

    identity_locked = asyncio.Event()
    rebind_started = asyncio.Event()
    original_identity_lock = ActionableNotificationService._locked_bound_identity

    async def mark_identity_lock(self, session, bound, actor):
        identity = await original_identity_lock(self, session, bound, actor)
        identity_locked.set()
        return identity

    async def rebind() -> None:
        rebind_started.set()
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Person)
                .where(Person.id == data.person_id)
                .values(home_assistant_mobile_app_notify_service="notify.mobile_app_rebound")
            )
            await session.commit()

    async def wait_for_blocked_person_rebind() -> None:
        async with AsyncSessionLocal() as probe:
            while True:
                blocked = await probe.scalar(text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'active' "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE '%UPDATE people%'"
                    ")"
                ))
                if blocked:
                    return
                await probe.rollback()

    monkeypatch.setattr(ActionableNotificationService, "_locked_bound_identity", mark_identity_lock)
    dispatcher_task = None
    rebind_task = None
    async with AsyncSessionLocal() as holder:
        await holder.scalar(
            select(NotificationRun)
            .where(NotificationRun.id == run_id)
            .with_for_update()
        )
        dispatcher_task = asyncio.create_task(
            notification_service.dispatcher.run_once(run_id, claimed=claimed)
        )
        try:
            await bounded(identity_locked.wait())
            rebind_task = asyncio.create_task(rebind())
            await bounded(rebind_started.wait())
            await bounded(wait_for_blocked_person_rebind())
            assert not rebind_task.done()
        finally:
            await holder.rollback()

    try:
        assert dispatcher_task is not None and await bounded(dispatcher_task)
        assert rebind_task is not None
        await bounded(rebind_task)
    finally:
        for task in (dispatcher_task, rebind_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (dispatcher_task, rebind_task) if task is not None),
            return_exceptions=True,
        )

    row = await output_run(data.context_id, "normal_result")
    context = await load_context(data.context_id)
    assert row.status == "provider_accepted"
    assert context.notify_service == NOTIFY_SERVICE
    assert len(client.calls) == 1
    async with AsyncSessionLocal() as session:
        rebound = await session.get(Person, data.person_id)
        assert rebound is not None
        assert rebound.home_assistant_mobile_app_notify_service == "notify.mobile_app_rebound"


async def test_final_output_checkpoint_detects_duplicate_added_while_waiting_for_run(
    actionable_resources,
    monkeypatch,
):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    run_id = await finalize_output(
        ActionableNotificationService(),
        data.context_id,
        GateActionOutcome(
            True,
            "Synthetic controller accepted and verified the command.",
            state="open",
            delivery=GateCommandDelivery.ACCEPTED,
            mechanically_confirmed=True,
        ),
    )
    client = InertMobileClient()
    install_real_mobile_notifier(monkeypatch, client)
    notification_service = NotificationService(run_store=NotificationRunStore())
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(notification_service, name, AsyncMock())
    claimed = await notification_service.run_store.claim(run_id)
    assert claimed is not None

    identity_locked = asyncio.Event()
    original_identity_lock = ActionableNotificationService._locked_bound_identity

    async def mark_identity_lock(self, session, bound, actor):
        identity = await original_identity_lock(self, session, bound, actor)
        identity_locked.set()
        return identity

    monkeypatch.setattr(ActionableNotificationService, "_locked_bound_identity", mark_identity_lock)
    dispatcher_task = None
    async with AsyncSessionLocal() as holder:
        await holder.scalar(
            select(NotificationRun)
            .where(NotificationRun.id == run_id)
            .with_for_update()
        )
        dispatcher_task = asyncio.create_task(
            notification_service.dispatcher.run_once(run_id, claimed=claimed)
        )
        try:
            await bounded(identity_locked.wait())
            async with AsyncSessionLocal() as inserter:
                inserter.add(Person(
                    first_name="Concurrent",
                    last_name="Duplicate",
                    display_name="Concurrent Duplicate",
                    home_assistant_mobile_app_notify_service=NOTIFY_SERVICE,
                    is_active=True,
                ))
                await inserter.commit()
        finally:
            await holder.rollback()

    try:
        assert dispatcher_task is not None and await bounded(dispatcher_task)
    finally:
        if dispatcher_task is not None and not dispatcher_task.done():
            dispatcher_task.cancel()
        if dispatcher_task is not None:
            await asyncio.gather(dispatcher_task, return_exceptions=True)

    row = await output_run(data.context_id, "normal_result")
    assert row.status == "skipped"
    assert row.delivery_plan[0]["state"] == "skipped"
    assert row.delivery_plan[0]["reason"] == "actionable_output_requester_changed"
    assert client.calls == []


async def test_output_recipient_rebind_skips_delivery_but_keeps_the_journaled_result(actionable_resources, monkeypatch):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    service = ActionableNotificationService()
    run_id = await finalize_output(
        service,
        data.context_id,
        GateActionOutcome(
            False,
            "Synthetic controller rejected the command.",
            delivery=GateCommandDelivery.REJECTED,
        ),
    )
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Person)
            .where(Person.id == data.person_id)
            .values(home_assistant_mobile_app_notify_service="notify.mobile_app_rebound")
        )
        await session.commit()

    client = InertMobileClient()
    install_real_mobile_notifier(monkeypatch, client)
    notification_service = NotificationService(run_store=NotificationRunStore())
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(notification_service, name, AsyncMock())

    assert await notification_service.dispatcher.run_once(run_id)
    row = await output_run(data.context_id, "normal_result")
    assert row.status == "skipped"
    assert row.delivery_plan[0]["state"] == "skipped"
    assert row.delivery_plan[0]["reason"] == "actionable_output_requester_changed"
    assert row.context["actionable_output_origin"]["context_id"] == str(data.context_id)
    assert client.calls == []


async def test_output_configuration_rebind_skips_delivery_without_changing_its_journal(actionable_resources, monkeypatch):
    data = await synthetic_context(
        actionable_resources,
        consumed_at=datetime.now(tz=UTC),
        outcome="dispatch_pending",
    )
    service = ActionableNotificationService()
    run_id = await finalize_output(
        service,
        data.context_id,
        GateActionOutcome(
            True,
            "Synthetic accepted.",
            state="open",
            delivery=GateCommandDelivery.ACCEPTED,
            mechanically_confirmed=True,
        ),
    )
    changed = runtime_config("changed-output")
    monkeypatch.setattr(notification_owner, "get_runtime_config", AsyncMock(return_value=changed))
    monkeypatch.setattr(notification_owner, "get_runtime_config_for_session", AsyncMock(return_value=changed))
    client = InertMobileClient()
    install_real_mobile_notifier(monkeypatch, client)
    notification_service = NotificationService(run_store=NotificationRunStore())
    for name in ("publish_planned_outcome", "publish_planned_failure", "publish_plan_completion"):
        monkeypatch.setattr(notification_service, name, AsyncMock())

    assert await notification_service.dispatcher.run_once(run_id)
    row = await output_run(data.context_id, "normal_result")
    assert row.status == "skipped"
    assert row.delivery_plan[0]["reason"] == "actionable_output_configuration_changed"
    assert row.context["actionable_output_origin"]["run_id"] == str(run_id)
    assert client.calls == []
