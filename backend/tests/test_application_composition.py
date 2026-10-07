"""Inert composition delegates and transactional maintenance handoff ordering."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import composition
from app.modules.notifications.base import NotificationContext
from app.services import (
    access_devices,
    actionable_notifications,
    home_assistant,
    maintenance,
    notifications,
)
from app.services.integration_effects import MAINTENANCE_HA_ENTITY_ID


@pytest.mark.asyncio
async def test_composition_is_inert_and_delegates_exact_callback_arguments(monkeypatch):
    bind_effects = Mock()
    bind_intake = Mock()
    bind_status = Mock()
    monkeypatch.setattr(home_assistant, "bind_home_assistant_effects", bind_effects)
    monkeypatch.setattr(maintenance, "bind_notification_intake", bind_intake)
    monkeypatch.setattr(access_devices, "bind_home_assistant_status", bind_status)

    actionable_owner = SimpleNamespace(handle_home_assistant_action=AsyncMock(return_value=True))
    notification_owner = SimpleNamespace(enqueue_notification=AsyncMock())
    status = {"connected": True}
    integration_owner = SimpleNamespace(status=AsyncMock(return_value=status))
    actionable_getter = Mock(return_value=actionable_owner)
    notification_getter = Mock(return_value=notification_owner)
    integration_getter = Mock(return_value=integration_owner)
    maintenance_change = AsyncMock()
    monkeypatch.setattr(
        actionable_notifications,
        "get_actionable_notification_service",
        actionable_getter,
    )
    monkeypatch.setattr(notifications, "get_notification_service", notification_getter)
    monkeypatch.setattr(home_assistant, "get_home_assistant_service", integration_getter)
    monkeypatch.setattr(maintenance, "set_mode", maintenance_change)

    composition.wire_application()

    for getter in (actionable_getter, notification_getter, integration_getter):
        getter.assert_not_called()
    maintenance_change.assert_not_awaited()
    assert isinstance(bind_intake.call_args.args[0], composition.MaintenanceNotificationIntake)

    effects = bind_effects.call_args.args[0]
    action_data = {"operation_id": "synthetic"}
    context = NotificationContext("integration_degraded", "Synthetic", "warning", {})
    assert await effects.mobile_action("synthetic_action", action_data) is True
    actionable_owner.handle_home_assistant_action.assert_awaited_once_with(
        "synthetic_action", action_data
    )
    await effects.maintenance_state(True)
    maintenance_change.assert_awaited_once_with(
        True,
        actor="Home Assistant Sync",
        source="Home Assistant Sync",
        reason="Synced from Home Assistant",
        sync_ha=False,
    )
    await effects.degraded_notification(context)
    notification_owner.enqueue_notification.assert_awaited_once_with(context)
    assert await bind_status.call_args.args[0](True) is status
    integration_owner.status.assert_awaited_once_with(refresh=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("commit_fails", [False, True])
async def test_maintenance_reserves_in_its_transaction_and_wakes_only_after_commit(
    monkeypatch, commit_fails
):
    events = []
    session = SimpleNamespace()

    async def flush():
        events.append("flush")

    async def commit():
        events.append("commit")
        if commit_fails:
            raise RuntimeError("Synthetic commit failure")

    session.flush = flush
    session.commit = commit

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("session_closed")

    monkeypatch.setattr(maintenance, "AsyncSessionLocal", SessionContext)
    row = SimpleNamespace(
        is_active=False, enabled_at=None, enabled_by=None, source=None, reason=None
    )
    get_state = AsyncMock(return_value=row)
    monkeypatch.setattr(maintenance, "get_maintenance_state", get_state)
    audit_id = uuid.uuid4()
    write_audit = AsyncMock(return_value=SimpleNamespace(id=audit_id))
    monkeypatch.setattr(maintenance, "_write_mode_audit", write_audit)
    monkeypatch.setattr(maintenance, "audit_log_event_payload", lambda audit: {"id": str(audit.id)})
    reserved_notifications = []

    async def reserve_notification(actual_session, context, *, dispatch_id):
        assert actual_session is session
        reserved_notifications.append((context, dispatch_id))
        events.append("notification_reserved")

    reserve_automation = AsyncMock(
        side_effect=lambda *args, **kwargs: events.append("automation_reserved")
    )
    monkeypatch.setattr(maintenance, "reserve_trigger", reserve_automation)
    notification_owner = SimpleNamespace(
        enqueue_in_session=AsyncMock(side_effect=reserve_notification),
        dispatcher=SimpleNamespace(wake=Mock(side_effect=lambda: events.append("wake"))),
    )
    monkeypatch.setattr(notifications, "get_notification_service", lambda: notification_owner)
    publish = AsyncMock(side_effect=lambda *args: events.append("publish"))
    sync = AsyncMock()
    monkeypatch.setattr(maintenance.event_bus, "publish", publish)
    monkeypatch.setattr(maintenance, "_sync_home_assistant", sync)
    composition.wire_application()

    if commit_fails:
        with pytest.raises(RuntimeError, match="Synthetic commit failure"):
            await maintenance.set_mode(
                True, actor="Synthetic Admin", source="fixture", sync_ha=False
            )
        assert events == [
            "flush",
            "notification_reserved",
            "automation_reserved",
            "commit",
            "session_closed",
        ]
        notification_owner.dispatcher.wake.assert_not_called()
        publish.assert_not_awaited()
    else:
        result = await maintenance.set_mode(
            True, actor="Synthetic Admin", source="fixture", sync_ha=False
        )
        assert result["changed"] is True
        assert result["ha_entity_id"] == MAINTENANCE_HA_ENTITY_ID
        assert events == [
            "flush",
            "notification_reserved",
            "automation_reserved",
            "commit",
            "session_closed",
            "wake",
            "publish",
            "publish",
        ]
        notification_owner.dispatcher.wake.assert_called_once_with()

    get_state.assert_awaited_once_with(session, lock=True)
    assert write_audit.await_args.args[0] is session
    assert reserve_automation.await_args.args[0] is session
    assert reserve_automation.await_args.args[1] == "maintenance_mode.enabled"
    assert reserve_automation.await_args.kwargs["origin_id"] == str(audit_id)
    context, dispatch_id = reserved_notifications[0]
    assert context.event_type == maintenance.MAINTENANCE_ENABLED_TRIGGER
    assert dispatch_id == uuid.uuid5(audit_id, "maintenance.notification")
    sync.assert_not_awaited()
