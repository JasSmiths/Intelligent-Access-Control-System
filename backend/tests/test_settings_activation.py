import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import settings_activation as owner
from app.services.settings import SettingsUpdateResult


@pytest.mark.parametrize("keys,affected", [
    ([], []), (["app_name"], []), (["home_assistant_token"], ["home_assistant"]),
    (["esphome_devices"], ["access_devices"]),
    (["home_assistant_token", "unifi_protect_host"], ["home_assistant", "unifi_protect"]),
])
async def test_only_actual_changed_services_activate(monkeypatch, keys, affected):
    restarts = {name: AsyncMock() for name in ("home_assistant", "access_devices", "unifi_protect")}
    monkeypatch.setattr(owner, "update_settings", AsyncMock(return_value=SettingsUpdateResult([], frozenset(keys))))
    coordinator = owner.SettingsActivationCoordinator(restarts)
    receipt = await coordinator.save({"home_assistant_token": ""}, user=SimpleNamespace(), source="test")
    assert receipt["saved"] is True
    assert [name for name, restart in restarts.items() if restart.await_count] == affected


async def test_activation_failure_retains_committed_save_and_attempts_other_service(monkeypatch):
    monkeypatch.setattr(owner, "update_settings", AsyncMock(return_value=SettingsUpdateResult([{"key": "app_name", "value": "Saved"}], frozenset({"home_assistant_url", "unifi_protect_host"}))))
    restarts = {"home_assistant": AsyncMock(side_effect=RuntimeError("synthetic-private-detail")), "unifi_protect": AsyncMock()}
    receipt = await owner.SettingsActivationCoordinator(restarts).save({}, user=SimpleNamespace(), source="test")
    assert receipt["saved"] and receipt["settings"][0]["value"] == "Saved"
    assert receipt["activation"]["state"] == "partial_failure"
    assert receipt["activation"]["services"] == {"home_assistant": "failed", "unifi_protect": "activated"}
    assert "synthetic-private-detail" not in str(receipt)


async def test_concurrent_saves_cannot_activate_an_older_configuration_last(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    operations = []
    async def persist(updates, **_):
        operations.append(updates["value"])
        return SettingsUpdateResult([], frozenset({"home_assistant_url"}))
    async def restart():
        operations.append("activate")
        if len(operations) == 2:
            entered.set()
            await release.wait()
    monkeypatch.setattr(owner, "update_settings", persist)
    coordinator = owner.SettingsActivationCoordinator({"home_assistant": restart})
    first = asyncio.create_task(coordinator.save({"value": "first"}, user=SimpleNamespace(), source="test"))
    await entered.wait()
    second = asyncio.create_task(coordinator.save({"value": "second"}, user=SimpleNamespace(), source="test"))
    await asyncio.sleep(0)
    assert operations == ["first", "activate"]
    release.set()
    await asyncio.gather(first, second)
    assert operations == ["first", "activate", "second", "activate"]


async def test_shutdown_drains_activation_and_refuses_later_save_before_persistence(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def restart():
        entered.set()
        await release.wait()
    monkeypatch.setattr(owner, "update_settings", AsyncMock(return_value=SettingsUpdateResult([], frozenset({"home_assistant_url"}))))
    coordinator = owner.SettingsActivationCoordinator({"home_assistant": restart})
    save = asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test"))
    await entered.wait()
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.gather(save, stop)
    with pytest.raises(owner.SettingsActivationUnavailable):
        await coordinator.save({}, user=SimpleNamespace(), source="test")
    assert owner.update_settings.await_count == 1


async def test_shutdown_during_admitted_persistence_reports_pending_and_refuses_queued_saves(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def persist(*_, **__):
        entered.set()
        await release.wait()
        return SettingsUpdateResult([], frozenset({"home_assistant_url"}))
    store, restart = AsyncMock(side_effect=persist), AsyncMock()
    monkeypatch.setattr(owner, "update_settings", store)
    coordinator = owner.SettingsActivationCoordinator({"home_assistant": restart})
    first = asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test"))
    await entered.wait()
    queued = [asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test")) for _ in range(2)]
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.sleep(0)
    release.set()
    receipt = await first
    assert receipt["saved"] and receipt["activation"]["state"] == "pending"
    for task in queued:
        with pytest.raises(owner.SettingsActivationUnavailable):
            await task
    await stop
    assert store.await_count == 1 and restart.await_count == 0


async def test_cancellation_between_stop_and_start_drains_before_disposal(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    operations = []
    async def restart():
        operations.append("stop")
        entered.set()
        await release.wait()
        operations.append("start")
    monkeypatch.setattr(owner, "update_settings", AsyncMock(return_value=SettingsUpdateResult([], frozenset({"home_assistant_url"}))))
    coordinator = owner.SettingsActivationCoordinator({"home_assistant": restart})
    save = asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test"))
    await entered.wait()
    save.cancel()
    stop = asyncio.create_task(coordinator.stop())
    await asyncio.sleep(0)
    assert not save.done() and not stop.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await save
    await stop
    operations.append("dispose")
    assert operations == ["stop", "start", "dispose"]


async def test_cancelled_mixed_save_finishes_every_provider_before_identical_save(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def restart_ha():
        entered.set()
        await release.wait()
        calls.append("ha")
    async def restart_protect():
        calls.append("protect")
    monkeypatch.setattr(owner, "update_settings", AsyncMock(side_effect=[
        SettingsUpdateResult([], frozenset({"home_assistant_token", "unifi_protect_host"})),
        SettingsUpdateResult([], frozenset()),
    ]))
    coordinator = owner.SettingsActivationCoordinator({"home_assistant": restart_ha, "unifi_protect": restart_protect})
    first = asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test"))
    await entered.wait()
    first.cancel()
    identical = asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test"))
    await asyncio.sleep(0)
    assert not identical.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await identical)["activation"]["state"] == "not_required"
    assert calls == ["ha", "protect"]


async def test_cancel_during_admitted_persistence_finishes_activation_before_next_save(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def persist(*_, **__):
        entered.set()
        await release.wait()
        return SettingsUpdateResult([], frozenset({"home_assistant_token", "unifi_protect_host"}))
    monkeypatch.setattr(owner, "update_settings", AsyncMock(side_effect=persist))
    restarts = {name: AsyncMock() for name in ("home_assistant", "unifi_protect")}
    coordinator = owner.SettingsActivationCoordinator(restarts)
    task = asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test"))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    for restart in restarts.values():
        restart.assert_awaited_once()


async def test_cancel_while_waiting_for_save_lock_does_not_admit_persistence(monkeypatch):
    store = AsyncMock(return_value=SettingsUpdateResult([], frozenset()))
    monkeypatch.setattr(owner, "update_settings", store)
    coordinator = owner.SettingsActivationCoordinator({})
    async with coordinator._lock:
        queued = asyncio.create_task(coordinator.save({}, user=SimpleNamespace(), source="test"))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
    store.assert_not_awaited()


async def test_unconfirmed_persistence_exception_never_activates_or_returns_saved(monkeypatch):
    monkeypatch.setattr(owner, "update_settings", AsyncMock(side_effect=RuntimeError("synthetic commit acknowledgement unavailable")))
    restart = AsyncMock()
    coordinator = owner.SettingsActivationCoordinator({"home_assistant": restart})
    with pytest.raises(RuntimeError, match="acknowledgement unavailable"):
        await coordinator.save({}, user=SimpleNamespace(), source="test")
    restart.assert_not_awaited()
