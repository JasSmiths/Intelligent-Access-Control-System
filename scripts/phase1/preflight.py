"""Fail closed before importing IACS or executing migrations/tests."""
import asyncio
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit

assert {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}
assert not Path('/var/run/docker.sock').exists()
assert not Path('/workspace/.env').exists()
assert not Path('/workspace/data').exists()
assert not Path('/workspace/logs').exists()
url = urlsplit(os.environ['IACS_DATABASE_URL'])
assert url.hostname == '127.0.0.1' and url.path.startswith('/iacs_p1_')
assert urlsplit(os.environ['IACS_REDIS_URL']).hostname == '127.0.0.1'
assert os.environ['IACS_AUTO_CREATE_SCHEMA'] == 'false'
assert os.environ['IACS_SEED_DEMO_DATA'] == 'false'
with socket.socket() as probe:
    probe.settimeout(0.2)
    try:
        probe.connect(('192.0.2.1', 443))  # Documentation-only address, never a provider.
    except OSError:
        pass
    else:
        raise AssertionError('Unexpected outbound route')

async def ready():
    import asyncpg
    from redis.asyncio import Redis
    for attempt in range(60):
        try:
            conn = await asyncpg.connect(os.environ['IACS_DATABASE_URL'].replace('+asyncpg', ''))
            assert (await conn.fetchval('select current_database()')).startswith('iacs_p1_')
            await conn.close()
            redis = Redis.from_url(os.environ['IACS_REDIS_URL'])
            assert await redis.ping()
            await redis.aclose()
            break
        except (OSError, ConnectionError):
            if attempt == 59:
                raise
            await asyncio.sleep(0.5)

asyncio.run(ready())
print('PASS: loopback only; no Docker socket, production source data, logs or .env; isolated PostgreSQL and Redis ready')
