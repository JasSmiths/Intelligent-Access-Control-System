"""UniFi operation admission and actual completion, independent of its SDK."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TypeVar

from app.core.task_lifecycle import drain_owned

T = TypeVar("T")
_current_operation: ContextVar["ProtectOperations | None"] = ContextVar(
    "protect_operation", default=None
)


class ProtectOperationUnavailable(RuntimeError):
    pass


class ProtectOperations:
    def __init__(self, current: Callable[[], bool]) -> None:
        self._current = current
        self._admitting = True
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active(self) -> int:
        return self._active

    def owns_current(self) -> bool:
        return _current_operation.get() is self

    def close_admission(self) -> None:
        self._admitting = False

    async def drain(self) -> None:
        await self._idle.wait()

    async def run(
        self, operation: Callable[[], Awaitable[T]], *, deadline: float | None = None
    ) -> T:
        if not self._admitting or not self._current():
            raise ProtectOperationUnavailable("UniFi Protect provider changed or is stopping.")
        self._active += 1
        self._idle.clear()
        token = _current_operation.set(self)
        try:

            async def invoke() -> T:
                # Admission can close while drain_owned schedules this child.
                # An expired timeout alone does not prevent its initial I/O.
                if not self._admitting or not self._current():
                    raise ProtectOperationUnavailable(
                        "UniFi Protect provider changed or is stopping."
                    )
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("UniFi Protect operation exceeded its original deadline.")
                async with asyncio.timeout_at(deadline):
                    result = await operation()
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("UniFi Protect operation exceeded its original deadline.")
                return result

            # A cancelled caller cannot release capacity while SDK/native cleanup
            # is still running. The operation owns its original timeout inside.
            result: T = await drain_owned(invoke())
            return result
        finally:
            _current_operation.reset(token)
            self._active -= 1
            if not self._active:
                self._idle.set()
