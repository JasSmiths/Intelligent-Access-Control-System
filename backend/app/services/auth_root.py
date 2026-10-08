"""Specific cross-process fence for durable auth-root ciphertext writers."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core.auth_secret import get_auth_secret, reset_auth_secret_cache
from app.core.task_lifecycle import drain_owned

# Signed PostgreSQL bigint, stable across every participating writer/process.
AUTH_ROOT_LOCK_KEY = 0x4941435341524F4F


async def reload_auth_root_for_transaction(session: AsyncSession) -> str:
    """Call at transaction start, before actor/domain/setting locks or crypto."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": AUTH_ROOT_LOCK_KEY})
    reset_auth_secret_cache()
    return get_auth_secret()


async def try_reload_auth_root_for_transaction(session: AsyncSession) -> str | None:
    """Late domain participants must fail busy instead of reversing lock order.

    A failed try never resets/reads the process cache. Success owns the same
    transaction fence as ordinary root-first writers and loads the current key.
    """
    acquired = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"),
                                    {"key": AUTH_ROOT_LOCK_KEY})
    if acquired is not True:
        return None
    reset_auth_secret_cache()
    return get_auth_secret()


@asynccontextmanager
async def own_auth_root_connection(
    engine: AsyncEngine, *, recovery: bool = False
) -> AsyncIterator[AsyncConnection]:
    """Keep the session fence across transaction and real file outcome cleanup.

    Call inside an owned/drained child. A transport failure can lose this lock;
    neither a failed unlock nor a possibly held lock returns to the pool.
    """
    connection = await engine.connect()
    primary: BaseException | None = None
    lock_attempted = False
    try:
        if recovery:
            await connection.execute(text("SELECT set_config('statement_timeout', '5000', true), set_config('lock_timeout', '1000', true)"))
        lock_attempted = True
        await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": AUTH_ROOT_LOCK_KEY})
        await connection.commit()  # Session-level fence survives transactions.
        reset_auth_secret_cache()
        yield connection
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup: BaseException | None = None
        try:
            if not connection.closed and not connection.invalidated and connection.in_transaction():
                await drain_owned(connection.rollback())
            if lock_attempted and not connection.closed and not connection.invalidated:
                released = await drain_owned(connection.scalar(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": AUTH_ROOT_LOCK_KEY}
                ))
                if released is not True:
                    raise RuntimeError("Auth-root advisory lock release could not be confirmed.")
                await drain_owned(connection.commit())
        except BaseException as exc:  # noqa: BLE001 - Never return a possibly locked connection to the pool.
            cleanup = exc
            try:
                await drain_owned(connection.invalidate())
            except BaseException:  # noqa: BLE001 - Preserve the primary/cleanup outcome if invalidation also fails.
                cleanup.add_note("Auth-root connection invalidation also failed.")
        finally:
            try:
                await drain_owned(connection.close())
            except BaseException as exc:  # noqa: BLE001 - Preserve primary/accepted outcome while observing real cleanup.
                cleanup = cleanup or exc
                try:
                    await drain_owned(connection.invalidate())
                except BaseException:  # noqa: BLE001 - Preserve the primary/cleanup outcome if invalidation also fails.
                    cleanup.add_note("Auth-root connection invalidation also failed.")
        if cleanup is not None and primary is None:
            raise cleanup
