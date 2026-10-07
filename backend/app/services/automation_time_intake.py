"""Bounded reservation of due time-triggered automation occurrences."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule
from app.services.automation_scheduling import due_time_trigger, next_run_for_triggers
from app.services.workflow_session import SessionFactory
from app.services.workflows.automation_definition import (
    AutomationContext,
    captured_automation_context,
    normalize_triggers,
)

MAX_DUE_RULES_PER_TICK = 25


class OccurrenceReservation(Protocol):
    async def __call__(
        self, session: AsyncSession, rule: AutomationRule, context: AutomationContext, **kwargs: Any
    ) -> uuid.UUID: ...


class AutomationTimeIntake:
    def __init__(
        self, *, sessions: SessionFactory, reserve_occurrence: OccurrenceReservation
    ) -> None:
        self.sessions, self.reserve_occurrence = sessions, reserve_occurrence

    async def reserve_due(self) -> int:
        async with self.sessions() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            rules = (
                await session.scalars(
                    select(AutomationRule)
                    .where(
                        AutomationRule.is_active.is_(True),
                        AutomationRule.next_run_at.is_not(None),
                        AutomationRule.next_run_at <= now,
                    )
                    .order_by(AutomationRule.next_run_at)
                    .limit(MAX_DUE_RULES_PER_TICK)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            count = 0
            for rule in rules:
                triggers = normalize_triggers(rule.triggers)
                scheduled_for = rule.next_run_at
                trigger = due_time_trigger(
                    triggers, now=now, last_fired_at=rule.last_fired_at, scheduled_for=scheduled_for
                )
                if trigger:
                    payload = {
                        "trigger": trigger,
                        "occurred_at": now.isoformat(),
                        "scheduled_for": scheduled_for.isoformat(),
                    }
                    context = captured_automation_context(str(trigger["type"]), payload)
                    await self.reserve_occurrence(
                        session,
                        rule,
                        context,
                        origin_kind="scheduler",
                        origin_id=f"{rule.id}:{trigger['id']}:{scheduled_for.isoformat()}",
                        actor="Automation Scheduler",
                        source="scheduler",
                    )
                    count += 1
                rule.next_run_at = next_run_for_triggers(triggers, now=now, last_fired_at=now)
            await session.commit()
        return count
