"""Inert current policy parity and closed HA receipt identities."""
import hashlib
import json
import uuid

import pytest

from app.models.enums import PresenceState
from app.modules.home_assistant.input_boolean_contracts import (
    FrozenHaConnection, HaDelivery, home_assistant_connection_fingerprint,
)
from app.services.ha_presence_contracts import HaCurrentPerson
from app.services.ha_presence_policy import (
    current_binding, normalize_input_boolean_action,
    normalize_input_boolean_entity_ids, physical_target_key,
)


def test_order_trim_dedup_and_both_unspecified_actions_preserve_current_policy():
    person = HaCurrentPerson(uuid.uuid4(), True,
        [' input_boolean.second ', 'input_boolean.first', 'input_boolean.second'], '', '')
    for state in [PresenceState.PRESENT, PresenceState.EXITED]:
        binding = current_binding(person, state)
        assert binding.entity_ids == ('input_boolean.second', 'input_boolean.first')
        assert binding.action == 'turn_off'
    assert normalize_input_boolean_entity_ids(None) == []
    assert normalize_input_boolean_action(None) == 'turn_off'


@pytest.mark.parametrize('state, action', [(PresenceState.PRESENT,'turn_on'),(PresenceState.EXITED,'turn_off')])
def test_current_final_state_selects_configured_action(state, action):
    person = HaCurrentPerson(uuid.uuid4(), True, ['input_boolean.fixture'], 'turn_on', 'turn_off')
    assert current_binding(person, state).action == action


def test_single_canonical_connection_fingerprint_retains_whitespace_and_secret_truth():
    connection = FrozenHaConnection(' http://synthetic.invalid///', ' exact token ')
    expected = hashlib.sha256(json.dumps([' http://synthetic.invalid',' exact token '], separators=(',', ':')).encode()).hexdigest()
    assert home_assistant_connection_fingerprint(connection) == expected
    assert home_assistant_connection_fingerprint(FrozenHaConnection(connection.home_assistant_url, 'exact token')) != expected
    assert physical_target_key(connection, 'input_boolean.fixture') == hashlib.sha256(json.dumps(
        [' http://synthetic.invalid', 'input_boolean.fixture'], separators=(',', ':')).encode()).hexdigest()
    assert 'token' not in repr(connection)


@pytest.mark.parametrize('kind, state', [('not_sent','rejected'),('rejected','rejected'),('accepted','accepted'),('unknown','unknown')])
def test_effect_state_mapping_preserves_not_sent_without_migration(kind, state):
    delivery = HaDelivery(kind, 'accepted' if kind=='accepted' else 'provider_'+kind)
    assert delivery.kind == kind and delivery.effect_state == state


@pytest.mark.parametrize('bad', [[], {}, None, 'raw vendor secret', 'unbounded exception detail'])
def test_unsafe_reason_is_not_a_durable_contract(bad):
    with pytest.raises(ValueError):
        HaDelivery('unknown', bad)


@pytest.mark.parametrize('bad', [[], {}, 'arbitrary vendor state'])
def test_unhashable_or_raw_state_is_rejected(bad):
    with pytest.raises(ValueError):
        HaDelivery('accepted', 'accepted', bad)


@pytest.mark.asyncio
async def test_actual_ha_session_close_is_retained_through_repeated_parent_cancellation(monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.ha_presence_database import ha_session
    entered, release = asyncio.Event(), asyncio.Event()
    session = AsyncSession()
    actual_close = session.close
    async def close():
        entered.set()
        await release.wait()
        await actual_close()
    monkeypatch.setattr(session, 'close', close)
    async def operation():
        async with ha_session(lambda: session):
            pass
    parent = asyncio.create_task(operation())
    await entered.wait()
    parent.cancel()
    await asyncio.sleep(0)
    parent.cancel()
    await asyncio.sleep(0)
    assert not parent.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await parent
