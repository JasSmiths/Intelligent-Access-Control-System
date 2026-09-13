"""Deployment hold is inert before startup, transport dispatch or actuator claims."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app import main
from app.core import recovery_hold as policy
from app.recovery_hold import RecoveryHoldMiddleware, allows_request
from app.services.access_devices import AccessDeviceService
from app.services.gate_commands import GateCommandCoordinator, GateCommandIntent


@pytest.mark.parametrize('method,path', [
    ('POST','/api/v1/integrations/gate/open'), ('POST','/api/v1/ai/chat'),
    ('POST','/api/v1/ai/chat/approvals/00000000-0000-0000-0000-000000000001'),
    ('POST','/api/v1/webhooks/whatsapp'), ('POST','/api/v1/webhooks/ubiquiti/lpr'),
    ('POST','/api/v1/automations/webhooks/key'), ('POST','/api/v1/auth/setup'),
    ('PATCH','/api/v1/settings'), ('DELETE','/api/v1/visitor-passes/id'),
    ('GET','/api/v1/integrations/gate/status'), ('GET','/api/v1/visitor-passes'),
    ('GET','/api/v1/ai/chat/approvals/id/confirm'), ('GET','/api/v1/notification-snapshots/token'),
    ('POST','/api/v1/integrations/whatsapp/incoming/00000000-0000-0000-0000-000000000001/retry'),
    ('GET','/api/v1/integrations/discord/incoming/00000000-0000-0000-0000-000000000001/retry'),
])
async def test_hold_blocks_mutations_ingress_and_potentially_effectful_gets(monkeypatch, method, path):
    monkeypatch.setattr(policy.settings, 'recovery_hold', True)
    inner = AsyncMock(side_effect=AssertionError('held route must not be invoked'))
    app = RecoveryHoldMiddleware(inner)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        response = await client.request(method, path)
    assert response.status_code == 503 and response.json()['recovery_hold'] is True
    inner.assert_not_awaited()


@pytest.mark.parametrize('path', [
    '/api/v1/integrations/gate/commands', '/api/v1/integrations/cover/commands/00000000-0000-0000-0000-000000000001',
    '/api/v1/ai/chat/approvals', '/api/v1/automations/runs', '/api/v1/notifications/runs',
    '/api/v1/ai/chat/approvals/confirm-00000000000000000000000000000001',
    '/api/v1/integrations/whatsapp/incoming',
    '/api/v1/integrations/discord/incoming/00000000-0000-0000-0000-000000000001',
])
async def test_hold_delegates_exact_read_paths_to_existing_auth(monkeypatch, path):
    monkeypatch.setattr(policy.settings, 'recovery_hold', True)
    guarded = FastAPI()

    @guarded.get(path)
    async def requires_auth():
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Authentication required'}, status_code=401)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=RecoveryHoldMiddleware(guarded)), base_url='http://synthetic') as client:
        response = await client.get(path)
    assert response.status_code == 401


async def test_hold_closes_websockets_before_they_create_chat_work(monkeypatch):
    monkeypatch.setattr(policy.settings, 'recovery_hold', True)
    inner, send = AsyncMock(), AsyncMock()
    await RecoveryHoldMiddleware(inner)({'type':'websocket','path':'/api/v1/ai/chat/ws'}, AsyncMock(), send)
    assert send.call_args.args[0]['code'] == 1008
    inner.assert_not_awaited()


async def test_disabled_hold_does_not_change_application_requests(monkeypatch):
    monkeypatch.setattr(policy.settings, 'recovery_hold', False)
    inner = AsyncMock()
    scope, receive, send = {'type':'http','path':'/arbitrary','method':'POST'}, AsyncMock(), AsyncMock()
    await RecoveryHoldMiddleware(inner)(scope, receive, send)
    inner.assert_awaited_once_with(scope, receive, send)


@pytest.mark.parametrize('fail_schema', [False, True])
async def test_hold_startup_skips_bootstrap_every_worker_and_cleans_database(monkeypatch, fail_schema):
    monkeypatch.setattr(policy.settings, 'recovery_hold', True)
    monkeypatch.setattr(main, 'configure_logging', lambda: None)
    monkeypatch.setattr(main, 'validate_startup_security_config', lambda: None)
    bootstrap, dispose = AsyncMock(), AsyncMock()
    monkeypatch.setattr(main, 'init_database', bootstrap)
    monkeypatch.setattr(main, 'engine', SimpleNamespace(dispose=dispose))
    verification = AsyncMock(side_effect=RuntimeError('incompatible schema') if fail_schema else None)
    monkeypatch.setattr(main, 'verify_readable_schema', verification)
    getters = []
    for name in vars(main):
        if name.startswith('get_') and name.endswith('_service'):
            getter = AsyncMock(side_effect=AssertionError('hold must not construct any worker'))
            getters.append(getter)
            monkeypatch.setattr(main, name, getter)
    app = FastAPI()
    if fail_schema:
        with pytest.raises(RuntimeError, match='incompatible schema'):
            async with main.lifespan(app):
                pytest.fail('incompatible schema accepted')
    else:
        async with main.lifespan(app):
            assert app.state.startup_complete
            await asyncio.sleep(0)
    assert not app.state.startup_complete
    bootstrap.assert_not_awaited()
    verification.assert_awaited_once()
    dispose.assert_awaited_once()
    assert not any(getter.called for getter in getters)


async def test_hold_refuses_direct_actuator_owners_before_claim_or_configuration_read(monkeypatch):
    monkeypatch.setattr(policy.settings, 'recovery_hold', True)
    coordinator = GateCommandCoordinator(ledger=SimpleNamespace())
    coordinator._claim = AsyncMock()
    with pytest.raises(policy.RecoveryHoldError):
        await coordinator.execute_open(GateCommandIntent(reason='Synthetic held command', source='test'))
    coordinator._claim.assert_not_awaited()
    devices = AccessDeviceService()
    devices.preview_device_command = AsyncMock()
    with pytest.raises(policy.RecoveryHoldError):
        await devices.command_device('synthetic','open','Synthetic held command')
    devices.preview_device_command.assert_not_awaited()


def test_only_login_and_logout_are_permitted_auth_mutations():
    assert allows_request('POST','/api/v1/auth/login')
    assert allows_request('POST','/api/v1/auth/logout')
    assert not allows_request('POST','/api/v1/auth/setup')
    assert not allows_request('PUT','/api/v1/auth/me')
