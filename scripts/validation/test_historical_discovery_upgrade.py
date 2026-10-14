"""Populated forward upgrade preserves immutable historical candidate receipts."""
import json
import uuid
from datetime import UTC, datetime

import pytest
from test_feature_retirement import connection, preflight, scratch as scratch
import test_schema_contract as schema

BASE_NAME = preflight()
pytestmark = pytest.mark.asyncio


async def test_discovery_upgrade_adds_progress_without_rewriting_candidates(scratch):
    assert schema.migrate(scratch.name, 'upgrade', '20261014_0017', scratch.output, 'before')
    client = await connection(scratch.name)
    try:
        for state in ('pending', 'held', 'completed'):
            identity = uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic-discovery-upgrade:'+state)
            await client.execute('''INSERT INTO historical_access_candidates
                (id,source,protect_event_id,candidate,captured_at,event_id,state,due_at,
                 attempt_count,media_state,media_result,reason)
                VALUES($1,'unifi_protect_lpr',$2,$3,$4,$5,$6,$4,1,$7,$8,$9)''',
                identity, 'synthetic-'+state, json.dumps({'original': state}), datetime.now(UTC),
                uuid.uuid5(identity, 'event'), state, 'held' if state=='held' else 'unavailable',
                json.dumps({'status':state, 'original_receipt':True}), 'Synthetic retained reason')
        before = [dict(row) for row in await client.fetch('SELECT * FROM historical_access_candidates ORDER BY id')]
    finally:
        await client.close()
    assert schema.migrate(scratch.name, 'upgrade', '20261014_0018', scratch.output, 'upgrade')
    client = await connection(scratch.name)
    try:
        after = [dict(row) for row in await client.fetch('SELECT * FROM historical_access_candidates ORDER BY id')]
        assert after == before
        assert await client.fetchval('SELECT count(*) FROM historical_discovery_progress') == 0
        assert await client.fetchval("SELECT count(*) FROM pg_constraint WHERE conrelid='historical_discovery_progress'::regclass AND contype='c'") == 4
        (scratch.output/'discovery-upgrade.json').write_text(json.dumps({
            'candidate_states_preserved':['pending','held','completed'],
            'immutable_rows_unchanged': True, 'progress_rows':0}, indent=2))
    finally:
        await client.close()
