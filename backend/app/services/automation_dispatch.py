"""One ordered automation executor for synchronous callers and background recovery.

Rule selection and domain operations remain with their explicit owners. This
loop commits an attempted action before calling them and retains its receipt
before optional realtime reporting. It never retries an ambiguous action.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from app.core.logging import get_logger
from app.services.automation_execution import (
    AutomationActionWait,
    AutomationClaimLost,
    AutomationRunStore,
)
from app.services.workflow_dispatch_ports import AutomationDispatchOwner
from app.services.workflows.execution_contracts import AutomationActionResult, checked_action

logger = get_logger(__name__)
POLL_SECONDS = 2
ACTION_TIMEOUT_SECONDS = 120


class AutomationDispatcher:
    def __init__(self, service: AutomationDispatchOwner, store: AutomationRunStore):
        self.service, self.store = service, store
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._worker(), name="automation-dispatch")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def _worker(self) -> None:
        while True:
            self._wake.clear()
            try:
                for _ in range(20):
                    if not await self.run_once():
                        break
                await self.service.recover_completion_audits()
            except Exception:
                logger.exception("automation_dispatch_tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS)

    async def run_once(self, run_id: uuid.UUID | None = None) -> bool:
        claimed = await self.store.claim(run_id)
        if claimed is None:
            if run_id is not None:
                await self.service.account_completed_run(run_id)
            return False
        token = claimed.claim_token
        try:
            for index in range(len(claimed.action_plan)):
                current = await self.store.get(claimed.id)
                if current.action_plan[index]["state"] != "pending":
                    continue
                prepared = await self.service.prepare_action_dispatch(current, token, index)
                if isinstance(prepared, AutomationActionWait):
                    # Its not-before checkpoint is already committed. Stop here
                    # so later actions retain their configured order.
                    return True
                if prepared is None:
                    continue
                action = checked_action(prepared.action)
                try:
                    async with asyncio.timeout(ACTION_TIMEOUT_SECONDS):
                        # The owner opens only those short transactions its domain
                        # needs; no automation transaction spans provider I/O.
                        result = await self.service.dispatch_prepared_action(prepared)
                except Exception as exc:  # noqa: BLE001 - any post-attempt adapter failure is an uncertain outcome.
                    result = AutomationActionResult(
                        action["id"],
                        action["type"],
                        "unknown",
                        {
                            "reason": "action_outcome_unknown",
                            "requires_review": True,
                            "exception_class": type(exc).__name__,
                        },
                    )
                await self.store.finish_action(
                    claimed.id, token, index, result.as_payload(), state=result.checkpoint_state
                )
            current = await self.store.get(claimed.id)
            if current.status == "processing":
                await self.store.finish(claimed.id, token)
            await self.service.account_completed_run(claimed.id)
            return True
        except AutomationClaimLost:
            return True
        except BaseException:
            await self.store.interrupt(claimed.id, token)
            raise
