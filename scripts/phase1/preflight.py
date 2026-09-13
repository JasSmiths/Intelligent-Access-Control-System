"""Fail closed before importing IACS or executing migrations/tests."""
import asyncio
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit


def require(condition, message):
    # Do not use assert for safety boundaries: python -O must not disable them.
    if not condition:
        raise RuntimeError(message)


def inspect_isolation():
    require({p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}, 'Expected loopback-only network namespace')
    require(not Path('/var/run/docker.sock').exists(), 'Docker socket must not be mounted')
    for relative in ('.env', 'data', 'logs'):
        require(not (Path('/workspace') / relative).exists(), f'Unexpected workspace runtime path: {relative}')
    url = urlsplit(os.environ['IACS_DATABASE_URL'])
    redis = urlsplit(os.environ['IACS_REDIS_URL'])
    mode = os.environ.get('IACS_PHASE1_MODE')
    require(mode in {'db-free', 'persistence'}, 'Explicit isolated test classification is required')
    require(url.hostname == '127.0.0.1' and url.path.startswith('/iacs_p1_'), 'Unexpected test database identity')
    require(url.username == 'phase1' and url.password == 'synthetic-phase1-only', 'Synthetic database credentials required')
    require(redis.hostname == '127.0.0.1', 'Unexpected Redis host')
    require(os.environ['IACS_AUTO_CREATE_SCHEMA'] == 'false', 'Automatic schema bootstrap must be disabled')
    require(os.environ['IACS_SEED_DEMO_DATA'] == 'false', 'Demo seeding must be disabled')
    require(os.environ['IACS_ENVIRONMENT'] == 'testing', 'Testing environment is required')
    require(os.environ['IACS_AUTH_SECRET_KEY'] == 'phase1-synthetic-auth-root-never-production', 'Synthetic auth root required')
    require(url.port == (1 if mode == 'db-free' else 5432), 'Unexpected database port for test classification')
    require(redis.port == (1 if mode == 'db-free' else 6379), 'Unexpected Redis port for test classification')
    with socket.socket() as probe:
        probe.settimeout(0.2)
        try:
            probe.connect(('192.0.2.1', 443))  # Documentation-only address, never a provider.
        except OSError:
            pass
        else:
            raise RuntimeError('Unexpected outbound route')
    return mode


async def ready():
    import asyncpg
    from redis.asyncio import Redis
    from redis.exceptions import ConnectionError as RedisConnectionError
    for attempt in range(60):
        connection = redis = None
        try:
            connection = await asyncpg.connect(os.environ['IACS_DATABASE_URL'].replace('+asyncpg', ''), timeout=2)
            require((await connection.fetchval('select current_database()')).startswith('iacs_p1_'), 'Unexpected connected database')
            redis = Redis.from_url(os.environ['IACS_REDIS_URL'], socket_connect_timeout=2, socket_timeout=2)
            require(await redis.ping(), 'Redis did not respond')
            return
        except (OSError, ConnectionError, asyncio.TimeoutError, RedisConnectionError, asyncpg.CannotConnectNowError):
            if attempt == 59:
                raise
            await asyncio.sleep(0.5)
        finally:
            if redis is not None:
                await redis.aclose()
            if connection is not None:
                await connection.close(timeout=2)


def main():
    mode = inspect_isolation()
    if mode == 'persistence':
        asyncio.run(ready())
        print('PASS: isolated PostgreSQL and Redis ready; loopback only; synthetic configuration')
    else:
        print('PASS: DB-free; no database/Redis connection attempted; loopback only; synthetic configuration')


if __name__ == '__main__':
    main()
