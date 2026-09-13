"""UniFi lifecycle uses inert clients, callbacks and explicitly controlled tasks."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import unifi_protect as module
from app.modules.unifi_protect.client import UnifiProtectError


@pytest.fixture
def owner(monkeypatch):
    service = module.UnifiProtectIntegrationService()
    client = SimpleNamespace()
    build, close = AsyncMock(return_value=client), AsyncMock()
    monkeypatch.setattr(module, "get_runtime_config", AsyncMock(return_value=object()))
    monkeypatch.setattr(module, "build_unifi_protect_client", build)
    monkeypatch.setattr(module, "close_unifi_protect_client", close)
    monkeypatch.setattr(module, "load_unifi_protect_bootstrap", AsyncMock())
    monkeypatch.setattr(module, "subscribe_unifi_protect", lambda *_args: [])
    monkeypatch.setattr(module, "list_bootstrap_cameras", lambda _api: [])
    monkeypatch.setattr(module.event_bus, "publish", AsyncMock())
    return service, client, build, close


async def test_stopped_owner_cannot_be_lazily_reopened_or_restarted(owner):
    service, _client, build, _close = owner
    await service.stop()
    with pytest.raises(UnifiProtectError, match="shutting down"):
        await service._ensure_api(subscribe=True)
    with pytest.raises(UnifiProtectError, match="shutting down"):
        await service.restart()
    build.assert_not_awaited()
    assert service._api is None and not service._background_tasks


async def test_stop_during_client_bootstrap_closes_local_client_without_subscribing(owner, monkeypatch):
    service, client, _build, close = owner
    entered, release = asyncio.Event(), asyncio.Event()
    subscribed = []
    async def bootstrap(_api):
        entered.set()
        await release.wait()
    monkeypatch.setattr(module, "load_unifi_protect_bootstrap", bootstrap)
    monkeypatch.setattr(module, "subscribe_unifi_protect", lambda *_args: subscribed.append(True) or [])
    opening = asyncio.create_task(service._ensure_api(subscribe=True))
    stop = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        stop = asyncio.create_task(service.stop())
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert service._stopped
        release.set()
        with pytest.raises(UnifiProtectError, match="shutting down"):
            await opening
        await stop
        close.assert_awaited_once_with(client)
        assert not subscribed and service._api is None
    finally:
        release.set()
        await asyncio.gather(opening, *([stop] if stop else []), return_exceptions=True)


async def test_stop_closes_callback_admission_before_draining_tasks(owner):
    service, client, _build, close = owner
    service._api = client
    entered = asyncio.Event()
    events = []
    async def late_work():
        events.append("late work executed")
    async def existing_work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("existing drained")
            service._spawn_background(late_work(), name="must-not-start")
    service._unsubscribers = [lambda: events.append("unsubscribed")]
    service._spawn_background(existing_work(), name="synthetic-existing")
    await asyncio.wait_for(entered.wait(), timeout=2)
    await service.stop()
    assert events == ["unsubscribed", "existing drained"]
    assert not service._background_tasks
    close.assert_awaited_once_with(client)


async def test_failed_bootstrap_closes_unpublished_client(owner, monkeypatch):
    service, client, _build, close = owner
    monkeypatch.setattr(module, "load_unifi_protect_bootstrap", AsyncMock(side_effect=UnifiProtectError("Synthetic bootstrap failure")))
    with pytest.raises(UnifiProtectError):
        await service._ensure_api(subscribe=True)
    close.assert_awaited_once_with(client)
    assert service._api is None


async def test_cancelled_stop_waits_for_client_close(owner, monkeypatch):
    service, client, _build, _close = owner
    service._api = client
    entered, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def close(_client):
        entered.set()
        await release.wait()
        closed.set()
    monkeypatch.setattr(module, "close_unifi_protect_client", close)
    stopping = asyncio.create_task(service.stop())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        stopping.cancel()
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert not stopping.done() and not closed.is_set()
        stopping.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert closed.is_set() and service._api is None
    finally:
        release.set()
        await asyncio.gather(stopping, return_exceptions=True)


async def test_restart_closes_callback_intake_and_yields_to_concurrent_stop(owner):
    service, client, build, close = owner
    service._api = client
    entered, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []
    async def late_work():
        events.append("must not run")
    async def callback():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            draining.set()
            await release.wait()
    service._spawn_background(callback(), name="synthetic-restart-callback")
    restarting = stopping = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        restarting = asyncio.create_task(service.restart())
        await asyncio.wait_for(draining.wait(), timeout=2)
        assert not service._stopped and not service._accept_background
        service._spawn_background(late_work(), name="late-restart-callback")
        stopping = asyncio.create_task(service.stop())
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert service._stopped
        release.set()
        with pytest.raises(UnifiProtectError, match="shutting down"):
            await restarting
        await stopping
        assert not events and not service._background_tasks and service._api is None
        build.assert_not_awaited()
        close.assert_awaited_once_with(client)
    finally:
        release.set()
        await asyncio.gather(*[task for task in (restarting, stopping) if task], return_exceptions=True)
        await service.stop()
