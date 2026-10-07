"""Explicit service contracts consumed by durable workflow dispatchers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule, AutomationRun, NotificationRun
from app.services.automation_execution import AutomationActionWait
from app.services.notification_runs import NotificationActionAuthorization
from app.services.settings import RuntimeConfig
from app.services.workflows.automation_definition import AutomationContext
from app.services.workflows.execution_contracts import (
    AutomationActionResult,
    AutomationExecution,
    NotificationActionOutcome,
    NotificationPlanItem,
    WorkflowAction,
)

type NotificationPolicy = str | NotificationActionAuthorization | None


@dataclass(frozen=True)
class PreparedAutomationAction:
    action: WorkflowAction
    context: AutomationContext
    rule: AutomationRule
    execution: AutomationExecution


class AutomationDispatchOwner(Protocol):
    async def recover_completion_audits(self) -> None: ...
    async def account_completed_run(self, identity: uuid.UUID) -> None: ...
    async def prepare_action_dispatch(
        self, run: AutomationRun, token: uuid.UUID, index: int
    ) -> PreparedAutomationAction | AutomationActionWait | None: ...
    async def dispatch_prepared_action(
        self, prepared: PreparedAutomationAction
    ) -> AutomationActionResult: ...


class NotificationDispatchOwner(Protocol):
    async def prepare_delivery_plan(self, row: NotificationRun) -> list[NotificationPlanItem]: ...
    async def delivery_config(self) -> RuntimeConfig: ...
    async def authorize_attempt_with_config(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        action: dict[str, Any] | None = None,
        item: NotificationPlanItem | None = None,
        final: bool = False,
    ) -> tuple[RuntimeConfig | None, NotificationPolicy]: ...
    async def authorize_confirmed_attempt(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        plan: list[NotificationPlanItem],
        action: dict[str, Any] | None = None,
        item: NotificationPlanItem | None = None,
    ) -> tuple[RuntimeConfig | None, NotificationPolicy]: ...
    async def deliver_planned_action(
        self, item: NotificationPlanItem, row: NotificationRun, config: RuntimeConfig
    ) -> NotificationActionOutcome: ...
    async def publish_planned_failure(
        self,
        item: NotificationPlanItem,
        row: NotificationRun,
        *,
        reason: str = "provider_outcome_unknown",
        requires_review: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...
    async def publish_planned_outcome(
        self, item: NotificationPlanItem, row: NotificationRun, outcome: NotificationActionOutcome
    ) -> None: ...
    async def publish_plan_completion(self, row: NotificationRun) -> None: ...
