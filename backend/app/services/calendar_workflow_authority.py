"""Calendar's participant in the existing checked workflow attempt authority."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule, AutomationRun
from app.services.automation_authorization import (
    evaluate_automation_condition,
    preflight_current_action,
)
from app.services.automation_execution import AutomationRunStore
from app.services.maintenance_state import is_maintenance_mode_active
from app.services.workflows.action_definitions import validate_action
from app.services.workflows.execution_contracts import (
    AutomationExecution,
    WorkflowAction,
    checked_action,
    checked_execution,
)


class CalendarWorkflowAuthority:
    def __init__(
        self, execution: AutomationExecution, expected_action: WorkflowAction,
        *, runs: AutomationRunStore,
    ) -> None:
        self.execution = checked_execution(execution)
        self.action = checked_action(expected_action)
        if self.action["type"] != "integration.icloud_calendar.sync":
            raise ValueError("This workflow attempt does not target calendar synchronization.")
        validate_action(self.action)
        self.runs = runs

    async def authorize(self, session: AsyncSession, final: bool) -> datetime | None:
        """Early read only; final Rule→Run NOWAIT after calendar/domain locks.

        A contended final lock aborts the whole account transaction. It never
        waits while holding VisitorPass/intents locks or mutates the workflow's
        action/condition receipts. Its original dispatcher owns those outcomes.
        """
        identity = uuid.UUID(self.execution["run_id"])
        snapshot = await session.get(AutomationRun, identity, populate_existing=True)
        rule_id = snapshot.rule_id if snapshot is not None else None
        rule = None
        if rule_id is not None:
            query = select(AutomationRule).where(AutomationRule.id == rule_id)
            if final:
                query = query.with_for_update(nowait=True)
            rule = await session.scalar(query.execution_options(populate_existing=True))
        current, _, item = await self.runs.attempting(session, self.execution, lock=final, nowait=final)
        if current.rule_id != rule_id:
            raise ValueError("The checked workflow attempt's rule identity changed.")
        action = checked_action(item["action"])
        if action != self.action or action["type"] != "integration.icloud_calendar.sync":
            raise ValueError("The checked workflow attempt's calendar target changed.")
        validate_action(action)
        authority = await preflight_current_action(session, current, item, rule=rule,
            maintenance_active=is_maintenance_mode_active,
            evaluate_condition=evaluate_automation_condition)
        if authority.denial is not None:
            raise ValueError(str(authority.denial.get("detail") or authority.denial["reason"]))
        # Final rows remain locked; the account owner compares this exact expiry
        # to its post-flush DB clock rather than trusting an earlier policy time.
        return current.lease_expires_at if final else None
