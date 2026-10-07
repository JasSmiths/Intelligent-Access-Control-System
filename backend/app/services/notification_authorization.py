"""Notification authority at the durable pre-delivery checkpoint.

Checks run in the run store's transaction and refresh transport configuration
only after its final lock. This owner performs no provider delivery.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationRule, User
from app.services.access.authorization import assert_current_recognition_domain_authorization
from app.services.automation_authorization import notification_origin_denial
from app.services.mutation_context import load_active_admin
from app.services.notification_planning import notification_rule_origin
from app.services.notification_requests import confirmed_attempt_denial
from app.services.notification_runs import NotificationActionAuthorization
from app.services.settings import RuntimeConfig
from app.services.workflow_dispatch_ports import NotificationPolicy
from app.services.workflows.execution_contracts import NotificationPlanItem


class ActionableOutputAuthority(Protocol):
    async def authorize_notification_output_in_session(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        action: dict[str, Any] | None,
        *,
        config: RuntimeConfig | None = None,
        final: bool = False,
    ) -> str | None: ...


class NotificationAuthorization:
    def __init__(
        self,
        *,
        config: Callable[[AsyncSession], Awaitable[RuntimeConfig]],
        actionable: Callable[[], ActionableOutputAuthority],
    ) -> None:
        self.config = config
        self.actionable = actionable

    async def authorize_attempt(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        action: dict[str, Any] | None = None,
        item: NotificationPlanItem | None = None,
    ) -> str | NotificationActionAuthorization | None:
        """Current originating domain authority joins the durable attempt transaction."""
        if payload.get("resident_recovery_origin") is not None:
            from app.services.resident_recovery import authorize_mobile_output

            denial = await authorize_mobile_output(session, payload, run_id, action)
            return NotificationActionAuthorization(action_skip=denial) if denial else None
        if payload.get("actionable_output_origin") is not None:
            denial = await self.actionable().authorize_notification_output_in_session(
                session,
                payload,
                run_id,
                action,
            )
            return NotificationActionAuthorization(action_skip=denial) if denial else None
        denial = await notification_origin_denial(
            session,
            payload,
            run_id,
            authorize_recognition=assert_current_recognition_domain_authorization,
        )
        if not denial and item is not None:
            action_skip = await self._ordinary_rule_action_skip(session, item)
            if action_skip:
                return NotificationActionAuthorization(action_skip=action_skip)
        return denial

    async def authorize_attempt_with_config(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        action: dict[str, Any] | None = None,
        item: NotificationPlanItem | None = None,
        final: bool = False,
    ) -> tuple[RuntimeConfig | None, NotificationPolicy]:
        """Authorize normal work and retain only its checked runtime snapshot.

        The first pass owns rule/domain policy before the notification-run lock.
        The final pass refreshes only transport configuration after that lock, so
        a mutable saved rule is never re-locked in the inverse order.
        """
        if final:
            config = await self.config(session)
            return config, await self._authorize_action_config_in_session(
                session,
                action,
                config,
                payload=payload,
                run_id=run_id,
            )
        policy = await self.authorize_attempt(session, payload, run_id, action=action, item=item)
        if policy is not None:
            return None, policy
        config = await self.config(session)
        return config, await self._authorize_action_config_in_session(
            session,
            action,
            config,
            payload=payload,
            run_id=run_id,
        )

    async def _authorize_action_config_in_session(
        self,
        session: AsyncSession,
        action: dict[str, Any] | None,
        config: RuntimeConfig,
        *,
        payload: dict[str, Any] | None = None,
        run_id: uuid.UUID | None = None,
    ) -> NotificationPolicy:
        if action is not None and action.get("resident_recovery_output") is not None:
            from app.services.resident_recovery import authorize_mobile_output

            denial = await authorize_mobile_output(session, payload or {}, run_id, action)
            if denial:
                return NotificationActionAuthorization(action_skip=denial)
        if action is not None and action.get("actionable_output") is not None:
            if payload is None or run_id is None:
                return NotificationActionAuthorization(
                    action_skip="actionable_output_origin_invalid"
                )
            denial = await self.actionable().authorize_notification_output_in_session(
                session,
                payload,
                run_id,
                action,
                config=config,
                final=True,
            )
            if denial:
                return NotificationActionAuthorization(action_skip=denial)
        return None

    async def authorize_confirmed_attempt(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        plan: list[NotificationPlanItem],
        action: dict[str, Any] | None = None,
        item: NotificationPlanItem | None = None,
    ) -> tuple[RuntimeConfig | None, NotificationPolicy]:
        origin = payload.get("confirmed_delivery") or {}
        try:
            actor_id = uuid.UUID(str(origin.get("user_id")))
            actors = {actor_id}
            await session.scalars(
                select(User).where(User.id.in_(actors)).order_by(User.id).with_for_update()
            )
            await load_active_admin(
                session, actor_id, auth_version=origin.get("auth_version"), lock=True
            )
        except (ValueError, KeyError, TypeError):
            return None, "confirmed_actor_no_longer_authorized"
        config = await self.config(session)
        denial = await confirmed_attempt_denial(
            session,
            payload,
            run_id,
            plan=plan,
            runtime_config=config,
        )
        if not denial and action is not None:
            denial = await self.authorize_attempt(
                session, payload, run_id, action=action, item=item
            )
        if not denial and action is not None:
            denial = await self._authorize_action_config_in_session(
                session,
                action,
                config,
                payload=payload,
                run_id=run_id,
            )
        return config, denial

    async def _ordinary_rule_action_skip(
        self, session: AsyncSession, item: NotificationPlanItem
    ) -> str | None:
        """Check a saved workflow at the final pre-effect checkpoint.

        Explicit confirmed/preview rules are represented by ``rules_override``
        and intentionally have no ordinary-rule origin, so their accepted
        confirmation remains their authority.
        """
        origin = item.get("rule_origin")
        if origin is None:
            return None
        if not isinstance(origin, dict):
            return "notification_rule_origin_invalid"
        try:
            identity = uuid.UUID(str(origin.get("rule_id")))
        except (TypeError, ValueError, AttributeError):
            return "notification_rule_origin_invalid"
        rule = await session.scalar(
            select(NotificationRule)
            .where(NotificationRule.id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if rule is None:
            return "notification_rule_deleted"
        if not rule.is_active:
            return "notification_rule_inactive"
        current = notification_rule_origin(rule)["definition_fingerprint"]
        if origin.get("definition_fingerprint") != current:
            return "notification_rule_changed"
        return None
