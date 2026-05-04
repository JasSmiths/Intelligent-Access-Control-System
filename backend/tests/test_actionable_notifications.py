from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

from app.services import actionable_notifications as actionable
from app.services.actionable_notifications import (
    ActionableNotificationService,
    ActionIdentity,
    ActiveGateMalfunctionContext,
    BoundActionContext,
    GATE_FORCE_OPEN_ACTION,
    GATE_OPEN_ACTION,
    GateActionOutcome,
)
from app.models.enums import GateMalfunctionStatus


class DummySession:
    def __init__(self, row=None) -> None:
        self.row = row
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    async def scalar(self, _statement):
        return self.row

    async def commit(self) -> None:
        self.commits += 1


def bound_context() -> BoundActionContext:
    return BoundActionContext(
        id=uuid.uuid4(),
        action=GATE_OPEN_ACTION,
        notify_service="notify.mobile_app_jason",
        registration_number="AB12CDE",
        access_event_id=uuid.uuid4(),
        telemetry_trace_id="1" * 32,
        person_id=uuid.uuid4(),
        actor_user_id=None,
        parent_context_id=None,
    )


def identity(person_id: uuid.UUID | None = None) -> ActionIdentity:
    person = SimpleNamespace(id=person_id or uuid.uuid4(), display_name="Jason Smith")
    user = SimpleNamespace(id=uuid.uuid4(), username="jason", full_name="Jason Smith")
    return ActionIdentity(person=person, user=user)


def context_row(*, token: str = "token", consumed=False, expired=False):
    now = datetime.now(tz=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        token_hash=actionable._token_hash(token),
        action=GATE_OPEN_ACTION,
        notify_service="notify.mobile_app_jason",
        registration_number="AB12CDE",
        access_event_id=uuid.uuid4(),
        telemetry_trace_id="1" * 32,
        person_id=uuid.uuid4(),
        actor_user_id=None,
        parent_context_id=None,
        expires_at=now - timedelta(seconds=1) if expired else now + timedelta(minutes=5),
        consumed_at=now if consumed else None,
        outcome=None,
        outcome_detail=None,
    )


async def test_expired_gate_action_token_notifies_requesting_device(monkeypatch) -> None:
    service = ActionableNotificationService()
    row = context_row(expired=True)
    notifications = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession(row))

    async def fake_send(bound, *, title, message, actions=None):
        notifications.append((bound.notify_service, title, message, actions))

    monkeypatch.setattr(service, "_send_result_notification", fake_send)

    bound, reason = await service._consume_context("token", expected_action=GATE_OPEN_ACTION)

    assert bound is None
    assert reason == "This notification action has expired."
    assert row.outcome == "expired"
    assert notifications[0][0] == "notify.mobile_app_jason"
    assert notifications[0][1] == "Gate action expired"


async def test_consumed_gate_action_token_is_single_use(monkeypatch) -> None:
    service = ActionableNotificationService()
    row = context_row(consumed=True)
    notifications = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession(row))
    monkeypatch.setattr(
        service,
        "_send_result_notification",
        lambda *_args, **_kwargs: _async_append(notifications, _args, _kwargs),
    )

    bound, reason = await service._consume_context("token", expected_action=GATE_OPEN_ACTION)

    assert bound is None
    assert reason == "This notification action has already been used."
    assert notifications[0][1]["title"] == "Gate action already used"


async def test_normal_gate_action_failure_sends_force_follow_up(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    bound_identity = identity(bound.person_id)
    recorded = []
    audits = []
    followups = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(actionable, "_is_maintenance_mode_active", lambda: _async_value(True))
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((bound, "")))
    monkeypatch.setattr(service, "_identity_for_notify_service", lambda *_args, **_kwargs: _async_value(bound_identity))
    monkeypatch.setattr(service, "_record_outcome", lambda *_args, **_kwargs: _async_append(recorded, _args, _kwargs))
    monkeypatch.setattr(service, "_write_gate_audit", lambda *_args, **_kwargs: _async_append(audits, _args, _kwargs))
    monkeypatch.setattr(service, "_send_failure_follow_up", lambda *_args, **_kwargs: _async_append(followups, _args, _kwargs))

    outcome = await service.execute_gate_action("token", force=False)

    assert not outcome.accepted
    assert "Maintenance Mode" in outcome.detail
    assert recorded[0][0][1] == "failed"
    assert audits[0][1]["outcome"] == "failed"
    assert followups[0][0][1].detail == outcome.detail


async def test_normal_gate_action_success_sends_confirmation(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    confirmations = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(actionable, "_active_gate_malfunction", lambda: _async_value(None))
    monkeypatch.setattr(actionable, "_is_maintenance_mode_active", lambda: _async_value(False))
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((bound, "")))
    monkeypatch.setattr(service, "_identity_for_notify_service", lambda *_args, **_kwargs: _async_value(identity(bound.person_id)))
    monkeypatch.setattr(service, "_record_outcome", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_write_gate_audit", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_send_success_result", lambda *_args, **_kwargs: _async_append(confirmations, _args, _kwargs))

    class FakeGate:
        async def open_gate(self, reason, *, bypass_schedule=False):
            return SimpleNamespace(
                accepted=True,
                state=SimpleNamespace(value="open"),
                detail="Opened by Home Assistant.",
            )

    monkeypatch.setattr(actionable, "get_gate_controller", lambda _name: FakeGate())

    outcome = await service.execute_gate_action("token", force=False)

    assert outcome.accepted
    assert confirmations[0][0][0] == bound
    assert confirmations[0][0][1].detail == "Opened by Home Assistant."


async def test_force_gate_action_bypasses_maintenance_and_schedule(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    force_bound = BoundActionContext(**{**bound.__dict__, "action": GATE_FORCE_OPEN_ACTION})
    calls = []
    force_results = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(actionable, "_is_maintenance_mode_active", lambda: _async_value(True))
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((force_bound, "")))
    monkeypatch.setattr(service, "_identity_for_notify_service", lambda *_args, **_kwargs: _async_value(identity(bound.person_id)))
    monkeypatch.setattr(service, "_record_outcome", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_write_gate_audit", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_send_force_result", lambda *_args, **_kwargs: _async_append(force_results, _args, _kwargs))

    class FakeGate:
        async def open_gate(self, reason, *, bypass_schedule=False):
            calls.append((reason, bypass_schedule))
            return SimpleNamespace(
                accepted=True,
                state=SimpleNamespace(value="open"),
                detail="Opened",
            )

    monkeypatch.setattr(actionable, "get_gate_controller", lambda _name: FakeGate())

    outcome = await service.execute_gate_action("token", force=True)

    assert outcome.accepted
    assert calls[0][1] is True
    assert calls[0][0].startswith("Force Actionable notification")
    assert force_results[0][0][1].accepted is True


async def test_active_malfunction_blocks_action_and_notifies_with_duration(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    malfunction_id = uuid.uuid4()
    malfunction = ActiveGateMalfunctionContext(
        id=malfunction_id,
        gate_entity_id="cover.top_gate",
        gate_name="Top Gate",
        status=GateMalfunctionStatus.ACTIVE,
        opened_at=datetime.now(tz=UTC) - timedelta(hours=2, minutes=5),
        declared_at=datetime.now(tz=UTC) - timedelta(hours=2),
        last_gate_state="open",
        duration_seconds=2 * 60 * 60 + 5 * 60,
    )
    audits = []
    followups = []
    gate_calls = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(actionable, "_active_gate_malfunction", lambda: _async_value(malfunction))
    monkeypatch.setattr(
        service,
        "_malfunction_failure_message",
        lambda *_args, **_kwargs: _async_value(
            "I couldn't open the gate because Top Gate has been malfunctioning for 2 hours 5 minutes."
        ),
    )
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((bound, "")))
    monkeypatch.setattr(service, "_identity_for_notify_service", lambda *_args, **_kwargs: _async_value(identity(bound.person_id)))
    monkeypatch.setattr(service, "_record_outcome", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_write_gate_audit", lambda *_args, **_kwargs: _async_append(audits, _args, _kwargs))
    monkeypatch.setattr(service, "_send_failure_follow_up", lambda *_args, **_kwargs: _async_append(followups, _args, _kwargs))

    class FakeGate:
        async def open_gate(self, *_args, **_kwargs):
            gate_calls.append(True)
            raise AssertionError("Gate controller must not be called while malfunctioning.")

    monkeypatch.setattr(actionable, "get_gate_controller", lambda _name: FakeGate())

    outcome = await service.execute_gate_action("token", force=False)

    assert not outcome.accepted
    assert outcome.skipped_before_command is True
    assert outcome.malfunction_id == malfunction_id
    assert outcome.malfunction_duration_seconds == 7500
    assert "2 hours 5 minutes" in outcome.detail
    assert not gate_calls
    assert audits[0][1]["malfunction_id"] == malfunction_id
    assert audits[0][1]["malfunction_duration_seconds"] == 7500
    assert followups[0][0][1].detail == outcome.detail


async def test_malfunction_follow_up_uses_human_message_without_force_action(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    sent = []
    monkeypatch.setattr(service, "_create_force_action", lambda *_args, **_kwargs: _async_value({"action": "force"}))
    monkeypatch.setattr(service, "_send_result_notification", lambda *_args, **_kwargs: _async_append(sent, _args, _kwargs))

    await service._send_failure_follow_up(
        bound,
        GateActionOutcome(
            False,
            "Sorry, the gate was not opened for AB12CDE, the gate has been malfunctioning for 35 minutes and is currently unresolved.",
            malfunction_id=uuid.uuid4(),
            malfunction_duration_seconds=35 * 60,
        ),
    )

    assert sent[0][1]["title"] == "Gate did not open"
    assert sent[0][1]["message"].startswith("Sorry, the gate was not opened")
    assert sent[0][1]["actions"] is None


async def test_malfunction_failure_message_repairs_unhelpful_llm_output(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    person_identity = identity(bound.person_id)
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
        async def complete(self, messages):
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
    assert "1 day 3 hours" in message
    assert message.startswith("Sorry, the gate was not opened")
    assert "try again" not in message
    assert "Jason Smith" not in message


async def test_force_gate_action_failure_sends_final_failure_notification(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    force_bound = BoundActionContext(**{**bound.__dict__, "action": GATE_FORCE_OPEN_ACTION})
    force_results = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((force_bound, "")))
    monkeypatch.setattr(service, "_identity_for_notify_service", lambda *_args, **_kwargs: _async_value(identity(bound.person_id)))
    monkeypatch.setattr(service, "_record_outcome", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_write_gate_audit", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_send_force_result", lambda *_args, **_kwargs: _async_append(force_results, _args, _kwargs))

    class FakeGate:
        async def open_gate(self, _reason, *, bypass_schedule=False):
            return SimpleNamespace(
                accepted=False,
                state=SimpleNamespace(value="fault"),
                detail="Home Assistant rejected the command.",
            )

    monkeypatch.setattr(actionable, "get_gate_controller", lambda _name: FakeGate())

    outcome = await service.execute_gate_action("token", force=True)

    assert not outcome.accepted
    assert "Home Assistant rejected" in outcome.detail
    assert force_results[0][0][1].accepted is False


async def test_identity_mapping_failure_notifies_without_gate_command(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    notifications = []
    audits = []
    monkeypatch.setattr(actionable, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(service, "_consume_context", lambda *_args, **_kwargs: _async_value((bound, "")))
    monkeypatch.setattr(service, "_identity_for_notify_service", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_record_outcome", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(service, "_send_result_notification", lambda *_args, **_kwargs: _async_append(notifications, _args, _kwargs))
    monkeypatch.setattr(service, "_write_gate_audit", lambda *_args, **_kwargs: _async_append(audits, _args, _kwargs))

    outcome = await service.execute_gate_action("token", force=False)

    assert not outcome.accepted
    assert "linked to exactly one active person" in outcome.detail
    assert notifications[0][1]["title"] == "Gate action not available"
    assert audits[0][1]["outcome"] == "failed"


async def test_failed_normal_action_follow_up_includes_force_action(monkeypatch) -> None:
    service = ActionableNotificationService()
    bound = bound_context()
    sent = []
    force_action = {"action": "iacs:gate_force_open:token", "title": "Force Open Gate", "destructive": True}
    monkeypatch.setattr(service, "_create_force_action", lambda *_args, **_kwargs: _async_value(force_action))
    monkeypatch.setattr(service, "_send_result_notification", lambda *_args, **_kwargs: _async_append(sent, _args, _kwargs))

    await service._send_failure_follow_up(
        bound,
        GateActionOutcome(False, "Maintenance Mode is active."),
    )

    assert sent[0][1]["title"] == "Gate did not open"
    assert sent[0][1]["actions"] == [force_action]


async def _async_value(value):
    return value


async def _async_append(target, args, kwargs):
    target.append((args, kwargs))
    return None
