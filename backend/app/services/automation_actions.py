"""Supported automation actions and their domain-owned execution paths.

Adding an action means registering one handler. The executor has no queue,
transaction commit, scheduling or provider implementation of its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule
from app.services.automation_integration_actions import registered_integration_action_types
from app.services.automation_policy import (
    HARDWARE_ACTION_TYPES,
    HARDWARE_DENIAL_DETAILS,
    REQUESTER_CONFIRMATION_REQUIRED,
    hardware_action_denial,
)
from app.services.gate_commands import GateCommandIntent
from app.services.mutation_context import MutationError
from app.services.type_helpers import as_dict
from app.services.workflows.automation_definition import (
    AutomationContext,
    context_missing_references,
)
from app.services.workflows.context import render_template, workflow_action_result
from app.services.workflows.execution_contracts import (
    AutomationActionResult,
    AutomationExecution,
    checked_action,
)

if TYPE_CHECKING:
    from app.services.access_devices import AccessDeviceService
    from app.services.gate_commands import GateCommandCoordinator


class NotificationActivation(Protocol):
    async def __call__(
        self, session: AsyncSession, *, reference: dict[str, Any], active: bool
    ) -> dict[str, Any]: ...


class MaintenanceChange(Protocol):
    async def __call__(
        self, enabled: bool, *, actor: str, source: str, reason: str
    ) -> dict[str, Any]: ...


class IntegrationExecution(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
        operation_id: str | None = None,
    ) -> dict[str, Any]: ...


ActionHandler = Callable[
    [AsyncSession, dict[str, Any], AutomationContext, AutomationRule, AutomationExecution | None],
    Awaitable[dict[str, Any]],
]
HardwareAuthorization = Callable[[AsyncSession, AutomationExecution], Awaitable[None]]


def action_paused_by_maintenance_mode(action_type: str) -> bool:
    return (
        action_type.startswith(("notification.", "gate.", "garage_door."))
        or action_type == "maintenance_mode.enable"
    )


def render_action_reason(
    action: dict[str, Any], context: AutomationContext, rule: AutomationRule
) -> str:
    template = str(action.get("reason_template") or "")
    rendered = render_template(template, context.variables) if template else ""
    return rendered or f"Automation {rule.name}: {action['type']}"


class AutomationActionExecutor:
    def __init__(
        self,
        *,
        gates: Callable[[], GateCommandCoordinator],
        devices: Callable[[], AccessDeviceService],
        maintenance_active: Callable[[], Awaitable[bool]],
        maintenance_change: MaintenanceChange,
        notification_activation: NotificationActivation,
        integrations: IntegrationExecution,
        authorize_hardware: HardwareAuthorization,
    ) -> None:
        self.gates, self.devices = gates, devices
        self.maintenance_active, self.maintenance_change = maintenance_active, maintenance_change
        self.notification_activation, self.integrations = notification_activation, integrations
        self.authorize_hardware = authorize_hardware
        self.handlers: dict[str, ActionHandler] = {
            "notification.enable": self._notification_activation,
            "notification.disable": self._notification_activation,
            "gate.open": self._gate_open,
            "garage_door.open": self._garage_doors,
            "garage_door.close": self._garage_doors,
            "maintenance_mode.enable": self._maintenance,
            "maintenance_mode.disable": self._maintenance,
            **{key: self._integration for key in registered_integration_action_types()},
        }

    async def execute(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
        execution: AutomationExecution | None = None,
    ) -> AutomationActionResult:
        validated = checked_action(action)
        outcome = await self._execute_checked(
            session, action, context, rule=rule, execution=execution
        )
        return AutomationActionResult.from_payload(outcome, action=validated)

    async def _execute_checked(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
        execution: AutomationExecution | None,
    ) -> dict[str, Any]:
        action_type = str(action["type"])
        denial = hardware_action_denial(action_type, context.provenance)
        if denial:
            return workflow_action_result(
                action,
                "skipped",
                reason=denial,
                reason_code=denial,
                detail=HARDWARE_DENIAL_DETAILS[denial],
                command_sent=False,
                requires_confirmation=denial == REQUESTER_CONFIRMATION_REQUIRED,
            )
        missing = context_missing_references(context, action)
        if missing:
            return workflow_action_result(
                action, "skipped", reason="context_missing", missing_variables=missing
            )
        if (
            await self.maintenance_active()
            and action_type != "maintenance_mode.disable"
            and action_paused_by_maintenance_mode(action_type)
        ):
            return workflow_action_result(action, "skipped", reason="maintenance_mode")
        if action_type in HARDWARE_ACTION_TYPES and execution is None:
            raise ValueError("Hardware actions require a claimed durable automation action.")
        handler = self.handlers.get(action_type)
        if handler is None:
            return workflow_action_result(action, "failed", error="unknown_action")
        return await handler(session, action, context, rule, execution)

    async def _notification_activation(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        _context: AutomationContext,
        _rule: AutomationRule,
        _execution: AutomationExecution | None,
    ) -> dict[str, Any]:
        try:
            receipt = await self.notification_activation(
                session,
                reference=as_dict(action.get("config")),
                active=action["type"].endswith("enable"),
            )
        except MutationError as exc:
            if exc.code != "not_found":
                raise
            return workflow_action_result(action, "failed", error="notification_rule_not_found")
        return workflow_action_result(action, "success", **receipt)

    async def _gate_open(
        self,
        _session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        rule: AutomationRule,
        execution: AutomationExecution | None,
    ) -> dict[str, Any]:
        if execution is None:
            raise ValueError("Gate action is missing its durable execution checkpoint.")

        async def authorize(session: AsyncSession) -> None:
            await self.authorize_hardware(session, execution)

        outcome = await self.gates().execute_open(
            GateCommandIntent(
                reason=render_action_reason(action, context, rule),
                source="automation",
                actor="Automation Engine",
                intent_id=execution["operation_id"],
                idempotency_key=execution["idempotency_key"],
                target_plan=execution["target_plan"],
                expires_at=execution.get("expires_at"),
                require_admission=True,
                automatic_entry_policy=execution.get("automatic_entry_policy") is True,
                event_id=context.provenance.event_id,
                authorize_dispatch=authorize,
                metadata={
                    "rule_id": str(rule.id),
                    "rule_name": rule.name,
                    "trigger_key": context.trigger_key,
                    "automation_run_id": execution["run_id"],
                },
            )
        )
        return {
            **workflow_action_result(action, "success" if outcome.accepted else "failed"),
            **outcome.as_payload(),
        }

    async def _garage_doors(
        self,
        _session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        rule: AutomationRule,
        execution: AutomationExecution | None,
    ) -> dict[str, Any]:
        if execution is None:
            raise ValueError("Garage action is missing its durable execution checkpoint.")
        plans = execution.get("target_plans") or []
        if not plans:
            return workflow_action_result(action, "failed", error="garage_door_not_configured")
        command = "open" if action["type"] == "garage_door.open" else "close"
        reason = render_action_reason(action, context, rule)
        outcomes = []

        async def authorize(session: AsyncSession) -> None:
            await self.authorize_hardware(session, execution)

        for plan in plans:
            device_key = plan["target_device_key"]
            identity = str(uuid.uuid5(uuid.UUID(execution["operation_id"]), device_key))
            result = await self.devices().command_device(
                device_key,
                command,
                reason,
                schedule_source="garage_door",
                intent_id=identity,
                idempotency_key=identity,
                target_plan=plan,
                expires_at=execution.get("expires_at"),
                authorize_dispatch=authorize,
            )
            outcomes.append(result.as_payload())
            if result.requires_reconciliation or not result.accepted:
                break
        unresolved = any(
            item.get("requires_reconciliation") or item.get("delivery") == "unknown"
            for item in outcomes
        )
        failed = any(not item["accepted"] for item in outcomes)
        return workflow_action_result(
            action,
            "unknown" if unresolved else "failed" if failed else "success",
            outcomes=outcomes,
            requires_reconciliation=unresolved,
            skipped_target_count=len(plans) - len(outcomes),
            error="garage_target_outcome_unknown"
            if unresolved
            else "garage_target_failed"
            if failed
            else None,
        )

    async def _maintenance(
        self,
        _session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        rule: AutomationRule,
        _execution: AutomationExecution | None,
    ) -> dict[str, Any]:
        status = await self.maintenance_change(
            action["type"].endswith("enable"),
            actor="Automation Engine",
            source=f"Automation: {rule.name}",
            reason=render_action_reason(action, context, rule),
        )
        return workflow_action_result(action, "success", maintenance_mode=status)

    async def _integration(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        rule: AutomationRule,
        execution: AutomationExecution | None,
    ) -> dict[str, Any]:
        return await self.integrations(
            session,
            action,
            context,
            rule=rule,
            operation_id=execution["operation_id"] if execution else None,
        )
