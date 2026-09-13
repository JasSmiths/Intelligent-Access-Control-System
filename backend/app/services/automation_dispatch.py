"""One ordered automation executor for synchronous callers and background recovery.

Rule selection and domain operations remain with their explicit owners. This
loop commits an attempted action before calling them and retains its receipt
before optional realtime reporting. It never retries an ambiguous action.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.core.logging import get_logger
from app.services.automation_execution import AutomationActionWait, AutomationClaimLost, AutomationRunStore

logger = get_logger(__name__)
POLL_SECONDS = 2
ACTION_TIMEOUT_SECONDS = 120


class AutomationDispatcher:
    def __init__(self, service: Any, store: AutomationRunStore):
        self.service, self.store = service, store
        self._task: asyncio.Task | None = None
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

    async def run_once(self, run_id=None) -> bool:
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
                action, context, rule, execution = prepared
                try:
                    async with asyncio.timeout(ACTION_TIMEOUT_SECONDS):
                        # The owner opens only those short transactions its domain
                        # needs; no automation transaction spans provider I/O.
                        outcome = await self.service.dispatch_prepared_action(action, context, rule, execution)
                except Exception as exc:
                    outcome = {"id": action["id"], "type": action["type"], "status": "unknown",
                               "reason": "action_outcome_unknown", "requires_review": True,
                               "exception_class": type(exc).__name__}
                state = action_checkpoint_state(outcome)
                await self.store.finish_action(claimed.id, token, index, outcome, state=state)
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


def action_checkpoint_state(outcome: dict[str, Any]) -> str:
    status = str(outcome.get("status") or "unknown")
    if status == "skipped":
        return "skipped"
    # Accepted-but-unverified and partially ambiguous hardware still require
    # review even though the provider receipt itself can be retained positively.
    if (status == "unknown" or outcome.get("delivery") == "unknown"
            or outcome.get("requires_reconciliation") or outcome.get("requires_review")):
        return "unknown"
    if status == "failed":
        return "failed"
    if status in {"success", "queued"}:
        return "succeeded"
    return "unknown"
