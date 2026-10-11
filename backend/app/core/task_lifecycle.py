"""Drain owned resource cleanup and journal children before closing resources."""

import asyncio
from collections.abc import Awaitable


async def drain_owned[T](awaitable: Awaitable[T]) -> T:
    """Cancellation may stop intake, but cannot abandon a journal/cleanup child."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 - Cleanup must drain every owned resource despite arbitrary task failures.
            break  # Retrieve the owned task's failure below.
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
