"""Inert frozen HA transport semantics; no provider socket or runtime startup."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.modules.home_assistant.client import HomeAssistantClient, HomeAssistantError, HomeAssistantState
from app.modules.home_assistant.input_boolean_contracts import FrozenHaConnection
from app.modules.home_assistant.input_booleans import command_frozen_input_boolean

CONNECTION = FrozenHaConnection('http://synthetic.invalid', 'synthetic-secret-must-not-leak')


class Client:
    def __init__(self):
        self.connections = []
        self.sent = 0
        self.observed = 0
        self.delivery_error = None
        self.state_error = None
        self.state = 'on'
    async def call_service(self, service_name, service_data, *, runtime_config=None, start_deadline=None):
        self.connections.append(runtime_config)
        self.sent += 1
        if self.delivery_error is not None:
            raise self.delivery_error
        return {}
    async def get_state(self, entity_id, *, runtime_config=None, start_deadline=None):
        self.connections.append(runtime_config)
        self.observed += 1
        if self.state_error is not None:
            raise self.state_error
        return HomeAssistantState(entity_id, self.state, {})


@pytest.mark.asyncio
@pytest.mark.parametrize('delivery', ['not_sent', 'rejected', 'accepted', 'unknown'])
async def test_exact_delivery_and_frozen_connection_survive_safe_projection(delivery):
    client = Client()
    client.delivery_error = HomeAssistantError('RAW URL/TOKEN RESPONSE SENTINEL', delivery=delivery)
    result = await command_frozen_input_boolean(client, 'input_boolean.fixture', 'turn_off',
        connection=CONNECTION, operation_deadline=asyncio.get_running_loop().time()+10)
    assert result.kind == delivery
    assert client.observed == (1 if delivery=='accepted' else 0)
    assert all(connection is CONNECTION for connection in client.connections)
    assert 'SENTINEL' not in repr(result) and CONNECTION.home_assistant_token not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', [[], {}, 'raw vendor metadata'])
async def test_malformed_optional_state_does_not_rewrite_accepted_post(state):
    client = Client()
    client.state = state
    result = await command_frozen_input_boolean(client, 'input_boolean.fixture', 'turn_on',
        connection=CONNECTION, operation_deadline=asyncio.get_running_loop().time()+10)
    assert result.kind == 'accepted' and result.state is None


@pytest.mark.asyncio
async def test_actual_cancelled_optional_observation_returns_known_accepted_receipt():
    client = Client()
    entered = asyncio.Event()
    async def observation(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()
    client.get_state = observation
    actual = asyncio.create_task(command_frozen_input_boolean(client, 'input_boolean.fixture', 'turn_on',
        connection=CONNECTION, operation_deadline=asyncio.get_running_loop().time()+10))
    await entered.wait()
    actual.cancel()
    result = await actual
    assert result.kind == 'accepted' and result.reason == 'accepted_observation_unavailable'
    assert client.sent == 1


@pytest.mark.asyncio
async def test_expired_actual_invocation_does_not_call_provider():
    client = Client()
    result = await command_frozen_input_boolean(client, 'input_boolean.fixture', 'turn_on',
        connection=CONNECTION, operation_deadline=asyncio.get_running_loop().time()-1)
    assert result.kind == 'not_sent' and client.sent == 0 and client.observed == 0


@pytest.mark.asyncio
async def test_actual_client_acquisition_delay_does_not_mint_new_send_time(monkeypatch):
    client = HomeAssistantClient()
    transport = AsyncMock()
    async def acquired(**_kwargs):
        await asyncio.sleep(.02)
        return transport
    monkeypatch.setattr(client, '_request_client', acquired)
    with pytest.raises(HomeAssistantError) as error:
        await client.call_service('input_boolean.turn_off', {'entity_id':'input_boolean.fixture'},
            runtime_config=CONNECTION, start_deadline=asyncio.get_running_loop().time()+.01)
    assert error.value.delivery == 'not_sent'
    transport.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_vendor_exception_never_exposes_raw_detail_or_proves_rejection():
    client = Client()
    client.delivery_error = RuntimeError('secret-response-sentinel')
    result = await command_frozen_input_boolean(client, 'input_boolean.fixture', 'turn_off',
        connection=CONNECTION, operation_deadline=asyncio.get_running_loop().time()+10)
    assert result.kind == 'unknown' and result.reason == 'provider_unknown'
    assert 'secret-response-sentinel' not in repr(result)
