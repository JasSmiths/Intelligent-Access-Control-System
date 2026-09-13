# mypy: disable-error-code=var-annotated
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as _SimpleNamespace
from typing import Any, cast
import uuid

import pytest

from app.models.enums import GateMalfunctionStatus
from app.modules.access_devices.base import gate_receipt_projection
from app.modules.gate.base import GateCommandDelivery, GateState
from app.services import actionable_notifications as actionable
from app.services.actionable_notifications import (
    ACTIONABLE_CONTEXT_VERSION,
    ACTIONABLE_OUTPUT_VERSION,
    ActionIdentity,
    ActionableNotificationService,
    ActiveGateMalfunctionContext,
    BoundActionContext,
    FinalizationResult,
    GATE_FORCE_OPEN_ACTION,
    GATE_OPEN_ACTION,
    GateActionOutcome,
    PreparedForceChild,
)
from app.services.gate_commands import GateCommandIntent, GateCommandOutcome

SimpleNamespace = cast(Any, _SimpleNamespace)


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
            "device_key": "synthetic_gate",
            "kind": "gate",
            "binding_fingerprint": "synthetic-binding",
        }],
    }


def bound_context(*, action: str = GATE_OPEN_ACTION) -> BoundActionContext:
    context_id = uuid.uuid4()
    notify_service = "notify.mobile_app_jason"
    return BoundActionContext(
        id=context_id,
        action=action,
        notify_service=notify_service,
        registration_number="AB12CDE",
        access_event_id=uuid.uuid4(),
        telemetry_trace_id="1" * 32,
        person_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(),
        parent_context_id=None,
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        actor_auth_version=7,
        target_plan=manual_target_plan(),
        destination_binding=actionable._destination_binding(context_id, notify_service),
        mobile_configuration_binding="synthetic-mobile-binding",
        context_version=ACTIONABLE_CONTEXT_VERSION,
    )


def identity(person_id: uuid.UUID, user_id: uuid.UUID) -> ActionIdentity:
    person = SimpleNamespace(id=person_id, display_name="Jason Smith")
    user = SimpleNamespace(id=user_id, username="jason", full_name="Jason Smith", auth_session_version=7)
    return ActionIdentity(person=person, user=user)


def gate_command_outcome(
    intent: GateCommandIntent,
    *,
    accepted: bool,
    state: GateState,
    detail: str,
    delivery: GateCommandDelivery = GateCommandDelivery.ACCEPTED,
    mechanically_confirmed: bool = False,
    requires_reconciliation: bool = False,
    target_receipts: list[dict[str, Any]] | None = None,
) -> GateCommandOutcome:
    occurred_at = datetime.now(tz=UTC)
    return GateCommandOutcome(
        intent=intent,
        accepted=accepted,
        state=state,
        detail=detail,
        mechanically_confirmed=mechanically_confirmed,
        reconciliation_required=requires_reconciliation,
        target_receipts=target_receipts or [],
        started_at=occurred_at,
        completed_at=occurred_at,
        delivery=delivery,
    )


def prepared_force(parent: BoundActionContext) -> PreparedForceChild:
    return PreparedForceChild(
        context_id=uuid.uuid5(parent.id, actionable.ACTIONABLE_FORCE_CHILD_PURPOSE),
        target_plan=manual_target_plan(),
        mobile_configuration_binding="synthetic-mobile-binding",
    )


def install_gate_command_coordinator(monkeypatch, handler) -> None:
    class FakeCoordinator:
        async def execute_open(self, intent):
            return handler(intent)

    monkeypatch.setattr(actionable, "get_gate_command_coordinator", lambda: FakeCoordinator())


async def test_normal_definite_failure_prepares_durable_force_child(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    finalizations = []
    outcome = GateActionOutcome(
        False,
        "Synthetic controller rejected the command.",
        delivery=GateCommandDelivery.REJECTED,
        target_receipts=({"delivery": "rejected"},),
    )
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((bound, "")))
    monkeypatch.setattr(
        service,
        "_current_bound_identity",
        lambda *_args, **_kwargs: _async_value(identity(bound.person_id, bound.actor_user_id)),
    )
    monkeypatch.setattr(service, "_execute_gate", lambda *_args, **_kwargs: _async_value(outcome))
    monkeypatch.setattr(service, "_prepare_force_child", lambda *_args, **_kwargs: _async_value(prepared_force(bound)))

    async def finalize(*args, **kwargs):
        finalizations.append((args, kwargs))
        return FinalizationResult(True)

    monkeypatch.setattr(service, "_finalize_action_result", finalize)

    result = await service.execute_gate_action("opaque-token", force=False)

    assert result == outcome
    assert len(finalizations) == 1
    assert finalizations[0][1]["result_kind"] == "normal_result"
    assert finalizations[0][1]["prepared_force"].context_id == uuid.uuid5(
        bound.id, actionable.ACTIONABLE_FORCE_CHILD_PURPOSE
    )


@pytest.mark.parametrize(
    "outcome",
    [
        GateActionOutcome(False, "Unknown", delivery=GateCommandDelivery.UNKNOWN, requires_reconciliation=True),
        GateActionOutcome(
            False,
            "Mixed result",
            delivery=GateCommandDelivery.PARTIAL,
            target_receipts=(
                {"delivery": "accepted"},
                {"delivery": "rejected"},
            ),
        ),
        GateActionOutcome(
            True,
            "Accepted but unverified",
            delivery=GateCommandDelivery.ACCEPTED,
            requires_reconciliation=True,
        ),
    ],
)
def test_possible_delivery_never_allows_force_child(outcome) -> None:
    assert actionable._force_child_allowed(outcome) is False


async def test_force_command_keeps_manual_all_gates_contract(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context(action=GATE_FORCE_OPEN_ACTION)
    calls = []
    monkeypatch.setattr(actionable, "_active_gate_malfunction", lambda: _async_value(None))

    def handler(intent):
        calls.append(intent)
        return gate_command_outcome(
            intent,
            accepted=True,
            state=GateState.OPEN,
            detail="Synthetic accepted",
            delivery=GateCommandDelivery.ACCEPTED,
            mechanically_confirmed=True,
        )

    install_gate_command_coordinator(monkeypatch, handler)

    outcome = await service._execute_gate(bound, identity(bound.person_id, bound.actor_user_id), force=True)

    assert outcome.accepted is True
    intent = calls[0]
    assert intent.bypass_schedule is True
    assert intent.require_admission is False
    assert intent.intent_id == str(bound.id)
    assert intent.actor_user_id == str(bound.actor_user_id)
    assert intent.auth_version == bound.actor_auth_version
    assert intent.target_plan == bound.target_plan


async def test_active_malfunction_blocks_command_before_any_transport(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    malfunction = ActiveGateMalfunctionContext(
        id=uuid.uuid4(),
        gate_entity_id="cover.top_gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.ACTIVE,
        opened_at=datetime.now(tz=UTC) - timedelta(hours=2, minutes=5),
        declared_at=datetime.now(tz=UTC) - timedelta(hours=2),
        last_gate_state="open",
        duration_seconds=2 * 60 * 60 + 5 * 60,
    )
    calls = []
    monkeypatch.setattr(actionable, "_active_gate_malfunction", lambda: _async_value(malfunction))
    monkeypatch.setattr(
        service,
        "_malfunction_failure_message",
        lambda *_args, **_kwargs: _async_value(
            "Sorry, the gate was not opened for AB12CDE, the gate has been malfunctioning for 2 hours 5 minutes "
            "and is currently unresolved."
        ),
    )

    class FailingCoordinator:
        async def execute_open(self, *_args, **_kwargs):
            calls.append(True)
            raise AssertionError("Gate transport must not run while malfunctioning.")

    monkeypatch.setattr(actionable, "get_gate_command_coordinator", lambda: FailingCoordinator())

    outcome = await service._execute_gate(bound, identity(bound.person_id, bound.actor_user_id), force=False)

    assert outcome.skipped_before_command is True
    assert outcome.malfunction_id == malfunction.id
    assert outcome.malfunction_duration_seconds == 7500
    assert "2 hours 5 minutes" in outcome.detail
    assert not calls


def test_accepted_unverified_result_never_claims_physical_open() -> None:
    bound = bound_context()
    title, message = actionable._result_content(
        bound,
        GateActionOutcome(
            True,
            "Synthetic controller accepted the request; physical state is not verified.",
            state="opening",
            delivery=GateCommandDelivery.ACCEPTED,
            requires_reconciliation=True,
        ),
        force=False,
    )

    assert title == "Gate command accepted"
    assert "accepted" in message.lower()
    assert "opened" not in f"{title} {message}".lower()
    assert "reconciliation" in message.lower()


def test_verified_not_sent_receipt_reports_already_open_without_force_child() -> None:
    """A pre-command observation is success without inventing a provider send."""
    target_id = str(uuid.uuid4())
    receipts = [{
        "target_device_id": target_id,
        "accepted": False,
        "delivery": "not_sent",
        "state": "open",
        "verified": True,
        "requires_reconciliation": False,
    }]
    projection = gate_receipt_projection(
        receipts,
        admission_target_device_id=target_id,
        expected_target_count=1,
    )
    outcome = GateActionOutcome(
        accepted=projection["accepted"],
        detail="The gate was already open.",
        state=projection["state"],
        delivery=projection["delivery"],
        mechanically_confirmed=projection["mechanically_confirmed"],
        requires_reconciliation=projection["requires_reconciliation"],
        target_receipts=tuple(projection["target_receipts"]),
    )

    title, message = actionable._result_content(bound_context(), outcome, force=False)

    assert projection["delivery"] == "not_sent"
    assert projection["mechanically_confirmed"] is True
    assert actionable._recorded_outcome(outcome) == "success"
    assert actionable._force_child_allowed(outcome) is False
    assert title == "Gate already open"
    assert "no gate open command was sent" in message.lower()


def test_v2_binding_requires_exact_actor_target_destination_and_config() -> None:
    bound = bound_context()

    assert actionable._is_v2_bound(bound)
    assert not actionable._is_v2_bound(replace(bound, actor_auth_version=None))
    assert not actionable._is_v2_bound(replace(bound, target_plan={}))
    assert not actionable._is_v2_bound(replace(bound, destination_binding="wrong"))
    assert not actionable._is_v2_bound(replace(bound, mobile_configuration_binding=None))


def test_literal_output_persists_descriptor_without_a_bearer_token() -> None:
    bound = bound_context()
    run_id = actionable._output_run_id(bound.id, "normal_result")
    origin = actionable._output_origin(
        bound,
        run_id=run_id,
        result_kind="normal_result",
        configuration_binding="synthetic-config-binding",
        force_context_id=None,
    )
    action = actionable._literal_output_action(
        bound,
        title="Gate did not open",
        message="Synthetic failure.",
        result_kind="normal_result",
        force_context_id=None,
    )
    bearer = actionable._derived_action_token(bound.id, GATE_OPEN_ACTION)

    assert actionable._valid_output_action(
        action,
        context_id=bound.id,
        result_kind="normal_result",
        origin=origin,
    )
    assert bearer not in repr(action)
    assert bearer not in repr(origin)
    assert action["actionable_output"]["version"] == ACTIONABLE_OUTPUT_VERSION
    assert action["target"] == bound.notify_service


def test_literal_output_rejects_destination_or_force_child_rebinding() -> None:
    bound = bound_context()
    child_id = uuid.uuid5(bound.id, actionable.ACTIONABLE_FORCE_CHILD_PURPOSE)
    run_id = actionable._output_run_id(bound.id, "normal_result")
    origin = actionable._output_origin(
        bound,
        run_id=run_id,
        result_kind="normal_result",
        configuration_binding="synthetic-config-binding",
        force_context_id=child_id,
    )
    action = actionable._literal_output_action(
        bound,
        title="Gate did not open",
        message="Synthetic failure.",
        result_kind="normal_result",
        force_context_id=child_id,
    )

    assert actionable._valid_output_action(action, context_id=bound.id, result_kind="normal_result", origin=origin)
    assert not actionable._valid_output_action(
        {**action, "target": "notify.mobile_app_someone_else"},
        context_id=bound.id,
        result_kind="normal_result",
        origin=origin,
    )
    tampered = {**action, "actionable_output": {**action["actionable_output"], "force_context_id": None}}
    assert not actionable._valid_output_action(tampered, context_id=bound.id, result_kind="normal_result", origin=origin)


async def test_identity_denial_records_durable_result_without_gate_transport(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    finalizations = []
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((bound, "")))
    monkeypatch.setattr(service, "_current_bound_identity", lambda *_args, **_kwargs: _async_value(None))

    async def finalize(*args, **kwargs):
        finalizations.append((args, kwargs))
        return FinalizationResult(True)

    async def unexpected_execute(*_args, **_kwargs):
        raise AssertionError("Identity-denied actions must not reach the gate coordinator.")

    monkeypatch.setattr(service, "_finalize_action_result", finalize)
    monkeypatch.setattr(service, "_execute_gate", unexpected_execute)

    outcome = await service.execute_gate_action("opaque-token", force=False)

    assert outcome.skipped_before_command is True
    assert "active IACS Admin" in outcome.detail
    assert finalizations[0][1]["result_kind"] == "identity_denied"


async def test_malfunction_failure_message_repairs_unhelpful_llm_output(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    person_identity = identity(bound.person_id, bound.actor_user_id)
    calls = []
    malfunction = ActiveGateMalfunctionContext(
        id=uuid.uuid4(),
        gate_entity_id="cover.top_gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.FUBAR,
        opened_at=datetime.now(tz=UTC) - timedelta(days=1, hours=3),
        declared_at=datetime.now(tz=UTC) - timedelta(days=1, hours=2),
        last_gate_state="open",
        duration_seconds=27 * 60 * 60,
    )

    class FakeProvider:
        async def complete(self, messages, **_options):
            calls.append([message.content for message in messages])
            if len(calls) == 1:
                return SimpleNamespace(
                    text=(
                        "Jason Smith: Top Gate could not be opened because its in an active unresolved "
                        "malfunction state. Request AB12CDE was blocked - try again in 1 day 3 hours"
                    )
                )
            return SimpleNamespace(
                text=(
                    "Sorry, the gate was not opened for AB12CDE, the gate has been malfunctioning for "
                    "1 day 3 hours and is currently unresolved."
                )
            )

    monkeypatch.setattr(actionable, "get_runtime_config", lambda: _async_value(SimpleNamespace(llm_provider="openai")))
    monkeypatch.setattr(actionable, "get_llm_provider", lambda _provider: FakeProvider())

    message = await service._malfunction_failure_message(bound, person_identity, malfunction, force=True)

    assert len(calls) == 2
    assert "1 day 3 hours" in calls[0][-1]
    assert message.startswith("Sorry, the gate was not opened")
    assert "try again" not in message
    assert "Jason Smith" not in message


async def _async_value(value):
    return value
