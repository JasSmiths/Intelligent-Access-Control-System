import copy
import hashlib
import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import (
    ChatMessageInput,
    ProviderNotConfiguredError,
    complete_with_provider_options,
    get_llm_provider,
)
from app.core.auth_secret import get_auth_secret
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import GateMalfunctionState, NotificationActionContext, Person, User
from app.models.enums import GateMalfunctionStatus, UserRole
from app.modules.gate.base import GateCommandDelivery
from app.modules.notifications.base import NotificationContext
from app.services.event_bus import event_bus
from app.services.gate_commands import GateCommandIntent, get_gate_command_coordinator
from app.services.mutation_context import MutationError, load_active_admin
from app.services.notification_requests import configuration_binding
from app.services.notification_runs import NotificationRunStore
from app.services.settings import get_runtime_config, get_runtime_config_for_session
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, actor_from_user, write_audit_log
from app.services.workflows.notification_payloads import (
    GATE_OPEN_ACTION,
    notification_context_payload,
)

logger = get_logger(__name__)

GATE_FORCE_OPEN_ACTION = "gate.force_open"
GATE_OPEN_PREFIX = "iacs:gate_open:"
GATE_FORCE_OPEN_PREFIX = "iacs:gate_force_open:"
NORMAL_TOKEN_TTL = timedelta(minutes=10)
FORCE_TOKEN_TTL = timedelta(minutes=5)
# This bounds intake-to-checkpoint loss without importing the access-device
# timeout owner (which would recreate an actionable-to-device dependency edge).
# Each final pre-effect checkpoint renews it, so a healthy all-gates fanout is
# not revoked between targets; a stalled worker becomes recovery-only.
ACTIONABLE_DISPATCH_LEASE = timedelta(seconds=120)
ACTIONABLE_CONTEXT_VERSION = 2
ACTIONABLE_OUTPUT_VERSION = 2
ACTIONABLE_TOKEN_PURPOSE = "iacs.actionable-notification.v2"
ACTIONABLE_OUTPUT_PURPOSE = "actionable-output:v2"
ACTIONABLE_FORCE_CHILD_PURPOSE = "actionable-force-child:v2"
_MOBILE_NOTIFY_SERVICE = re.compile(r"notify\.mobile_app_[A-Za-z0-9_]{1,192}\Z")


@dataclass(frozen=True)
class ActionIdentity:
    person: Person
    user: User


@dataclass(frozen=True)
class BoundActionContext:
    id: uuid.UUID
    action: str
    notify_service: str
    registration_number: str
    access_event_id: uuid.UUID | None
    telemetry_trace_id: str | None
    person_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    parent_context_id: uuid.UUID | None
    expires_at: datetime
    actor_auth_version: int | None
    target_plan: dict[str, Any] | None
    destination_binding: str | None
    mobile_configuration_binding: str | None
    context_version: int = 0


@dataclass(frozen=True)
class GateActionOutcome:
    accepted: bool
    detail: str
    state: str = "unknown"
    reason: str = ""
    skipped_before_command: bool = False
    malfunction_id: uuid.UUID | None = None
    malfunction_duration_seconds: int | None = None
    command_id: str | None = None
    delivery: str = "not_sent"
    mechanically_confirmed: bool = False
    requires_reconciliation: bool = False
    target_receipts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class PreparedForceChild:
    context_id: uuid.UUID
    target_plan: dict[str, Any]
    mobile_configuration_binding: str


@dataclass(frozen=True)
class FinalizationResult:
    changed: bool
    run_id: uuid.UUID | None = None
    force_context_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ActiveGateMalfunctionContext:
    id: uuid.UUID
    gate_entity_id: str
    gate_name: str | None
    status: GateMalfunctionStatus
    opened_at: datetime
    declared_at: datetime
    last_gate_state: str | None
    duration_seconds: int

    @property
    def duration_display(self) -> str:
        return _format_duration(self.duration_seconds)


class ActionableNotificationService:
    """Owns fenced Home Assistant gate actions and their durable result output."""

    def __init__(self, run_store: NotificationRunStore | None = None) -> None:
        self._run_store = run_store if run_store is not None else NotificationRunStore()

    async def create_gate_open_action(
        self,
        *,
        context: NotificationContext,
        notify_service: str,
        runtime_config: Any | None = None,
    ) -> dict[str, str] | None:
        """Bind a new one-use manual confirmation to one current IACS Admin.

        The saved plan is a literal all-gates manual preview. It carries no
        credentials and is rechecked after the access-device locks at dispatch.
        """
        if context.event_type != "unauthorized_plate" or not _is_home_assistant_mobile_notify_service(notify_service):
            return None
        registration_number = _registration_from_context(context)
        if not registration_number:
            return None
        try:
            target_plan = await get_gate_command_coordinator().preview_manual_gate_open()
        except (LookupError, ValueError) as exc:
            logger.warning(
                "actionable_notification_target_preview_unavailable",
                extra={"notify_service": notify_service, "registration_number": registration_number, "error": str(exc)},
            )
            return None
        if not _is_manual_all_gates_plan(target_plan):
            logger.warning(
                "actionable_notification_target_preview_invalid",
                extra={"notify_service": notify_service, "registration_number": registration_number},
            )
            return None

        context_id = uuid.uuid4()
        token = _derived_action_token(context_id, GATE_OPEN_ACTION)
        async with AsyncSessionLocal() as session:
            identity = await self._identity_for_notify_service(session, notify_service)
            if identity is None:
                logger.warning(
                    "actionable_notification_identity_unavailable",
                    extra={"notify_service": notify_service, "registration_number": registration_number},
                )
                return None
            current_config = await get_runtime_config_for_session(session)
            if (
                runtime_config is not None
                and _mobile_configuration_binding(runtime_config)
                != _mobile_configuration_binding(current_config)
            ):
                # The notification itself is being sent with an already-authorized
                # snapshot. Do not attach a button whose durable authority would
                # instead bind a different current mobile configuration.
                logger.warning(
                    "actionable_notification_mobile_configuration_changed",
                    extra={"notify_service": notify_service, "registration_number": registration_number},
                )
                return None
            bound_config = runtime_config if runtime_config is not None else current_config
            now = await session.scalar(select(func.clock_timestamp()))
            row = NotificationActionContext(
                id=context_id,
                token_hash=_token_hash(token),
                action=GATE_OPEN_ACTION,
                notify_service=notify_service,
                registration_number=registration_number,
                access_event_id=_optional_uuid(context.facts.get("access_event_id")),
                telemetry_trace_id=_trace_id(context.facts.get("telemetry_trace_id")),
                person_id=identity.person.id,
                actor_user_id=identity.user.id,
                expires_at=now + NORMAL_TOKEN_TTL,
                metadata_=_new_context_metadata(
                    context_id=context_id,
                    action=GATE_OPEN_ACTION,
                    identity=identity,
                    notify_service=notify_service,
                    target_plan=target_plan,
                    mobile_configuration_binding=_mobile_configuration_binding(bound_config),
                    source_event_type=context.event_type,
                    source_subject=context.subject,
                ),
            )
            session.add(row)
            await session.commit()
        return {"action": f"{GATE_OPEN_PREFIX}{token}", "title": "Open All Gates"}

    async def handle_home_assistant_action(self, action_id: str, event_data: dict[str, Any] | None = None) -> bool:
        token = _strip_prefix(action_id, GATE_OPEN_PREFIX)
        if token:
            await self.execute_gate_action(token, force=False, event_data=event_data or {})
            return True
        token = _strip_prefix(action_id, GATE_FORCE_OPEN_PREFIX)
        if token:
            await self.execute_gate_action(token, force=True, event_data=event_data or {})
            return True
        return False

    async def execute_gate_action(
        self,
        token: str,
        *,
        force: bool,
        event_data: dict[str, Any] | None = None,
    ) -> GateActionOutcome:
        """Consume one confirmation once; a recovery worker never calls this."""
        action = GATE_FORCE_OPEN_ACTION if force else GATE_OPEN_ACTION
        bound, invalid_reason = await self._consume_context(token, expected_action=action)
        if bound is None:
            if invalid_reason == "Unknown or expired notification action.":
                await self._audit_unbound_failure(action=action, reason=invalid_reason)
            return GateActionOutcome(False, invalid_reason, reason=invalid_reason, skipped_before_command=True)

        async with AsyncSessionLocal() as session:
            identity = await self._current_bound_identity(session, bound)
        if identity is None:
            detail = "This notification action is no longer linked to its active IACS Admin requester."
            outcome = GateActionOutcome(False, detail, reason=detail, skipped_before_command=True)
            await self._finalize_action_result(
                bound,
                outcome,
                force=force,
                result_kind="identity_denied",
                event_data=event_data,
            )
            return outcome

        outcome = await self._execute_gate(bound, identity, force=force)
        prepared_force = None
        if not force and _force_child_allowed(outcome):
            prepared_force = await self._prepare_force_child(bound)
        result_kind = "force_result" if force else "normal_result"
        await self._finalize_action_result(
            bound,
            outcome,
            force=force,
            result_kind=result_kind,
            prepared_force=prepared_force,
            event_data=event_data,
        )
        return outcome

    async def _consume_context(
        self,
        token: str,
        *,
        expected_action: str,
    ) -> tuple[BoundActionContext | None, str]:
        """Atomically accept a v2 request or durably queue its inert notice.

        Only the valid branch commits ``dispatch_pending``. The later gate
        checkpoint changes it to ``attempting`` in the access-device transaction,
        so recovery can revoke a delayed worker before inspecting any receipt.
        """
        token_hash = _token_hash(token)
        notice: tuple[BoundActionContext, GateActionOutcome, str, bool] | None = None
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(NotificationActionContext)
                .where(NotificationActionContext.token_hash == token_hash)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                return None, "Unknown or expired notification action."
            bound = self._bound_from_row(row)
            now = await session.scalar(select(func.clock_timestamp()))
            if not _is_v2_bound(bound):
                if row.outcome is None:
                    row.outcome = "legacy_inert"
                    row.outcome_detail = "A legacy notification action lacked the current Admin and target binding."
                    await session.commit()
                return None, "This notification action requires a new approved request."
            if row.action != expected_action:
                reason = "Notification action type did not match the stored context."
                notice = (
                    bound,
                    GateActionOutcome(False, "This notification action no longer matches the stored gate request.",
                                      reason=reason, skipped_before_command=True),
                    "wrong_action",
                    True,
                )
            elif row.consumed_at is not None:
                reason = "This notification action has already been used."
                notice = (
                    bound,
                    GateActionOutcome(False, "This gate notification action has already been used.",
                                      reason=reason, skipped_before_command=True),
                    "already_used",
                    True,
                )
            elif row.expires_at <= now:
                reason = "This notification action has expired."
                notice = (
                    bound,
                    GateActionOutcome(False, "This gate notification action has expired.",
                                      reason=reason, skipped_before_command=True),
                    "expired",
                    False,
                )
            else:
                metadata = _metadata(row)
                row.consumed_at = now
                row.outcome = "dispatch_pending"
                row.outcome_detail = "Action intake accepted; gate dispatch is fenced by the current Admin and target plan."
                row.metadata_ = {
                    **metadata,
                    "dispatch_state": "active",
                    "dispatch_lease_expires_at": _dispatch_lease_expires_at(now),
                }
                await session.commit()
                return bound, ""

        # Delivery preparation is always outside the context intake lock. A
        # duplicate replay gets the same stable literal run rather than a direct
        # provider call, while an attempted outcome remains immutable.
        if notice is not None:
            notice_bound, outcome, result_kind, preserve_outcome = notice
            await self._finalize_action_result(
                notice_bound,
                outcome,
                force=expected_action == GATE_FORCE_OPEN_ACTION,
                result_kind=result_kind,
                preserve_outcome=preserve_outcome,
                allow_any_dispatch_state=True,
            )
            return None, outcome.reason
        return None, "This notification action is no longer available."

    async def _execute_gate(
        self,
        bound: BoundActionContext,
        identity: ActionIdentity,
        *,
        force: bool,
    ) -> GateActionOutcome:
        malfunction = await _active_gate_malfunction()
        if malfunction:
            detail = await self._malfunction_failure_message(bound, identity, malfunction, force=force)
            return GateActionOutcome(
                False,
                detail,
                state=malfunction.last_gate_state or "malfunction",
                reason=(
                    "Active gate malfunction prevents actionable notification gate open "
                    f"for {bound.registration_number}."
                ),
                skipped_before_command=True,
                malfunction_id=malfunction.id,
                malfunction_duration_seconds=malfunction.duration_seconds,
            )
        if not force and await _is_maintenance_mode_active():
            detail = "Maintenance Mode is active. Automated actions are disabled."
            return GateActionOutcome(False, detail, reason=detail, skipped_before_command=True)

        reason = (
            f"{'Force ' if force else ''}Actionable notification gate open for "
            f"{bound.registration_number} by {identity.person.display_name}"
        )

        async def authorize_dispatch(session: AsyncSession, *, _bound=bound) -> None:
            await self._authorize_gate_dispatch(session, _bound)

        result = await get_gate_command_coordinator().execute_open(
            GateCommandIntent(
                reason=reason,
                source="actionable_notification",
                bypass_schedule=force,
                actor=identity.person.display_name,
                registration_number=bound.registration_number,
                idempotency_key=f"gate-command:actionable:{bound.id}:{'force' if force else 'open'}",
                intent_id=str(bound.id),
                target_device_key=(bound.target_plan or {}).get("target_device_key"),
                target_plan=copy.deepcopy(bound.target_plan),
                expires_at=_aware_datetime(bound.expires_at),
                require_admission=False,
                automatic_entry_policy=False,
                actor_user_id=str(identity.user.id),
                auth_version=bound.actor_auth_version,
                authorize_dispatch=authorize_dispatch,
                metadata={
                    "context_id": str(bound.id),
                    "force": force,
                    "actionable_context_version": ACTIONABLE_CONTEXT_VERSION,
                },
            )
        )
        if result.exception_class:
            logger.warning(
                "actionable_notification_gate_open_failed",
                extra={
                    "context_id": str(bound.id),
                    "registration_number": bound.registration_number,
                    "force": force,
                    "error": result.detail,
                    "exception_class": result.exception_class,
                },
            )
        return GateActionOutcome(
            accepted=result.accepted,
            state=result.state.value,
            detail=result.detail or ("Gate command accepted." if result.accepted else "Gate command failed."),
            reason=reason,
            command_id=result.command_id,
            delivery=str(result.delivery or GateCommandDelivery.UNKNOWN),
            mechanically_confirmed=result.mechanically_confirmed,
            requires_reconciliation=result.requires_reconciliation,
            target_receipts=tuple(
                receipt for receipt in result.target_receipts if isinstance(receipt, dict)
            ),
        )

    async def _authorize_gate_dispatch(self, session: AsyncSession, bound: BoundActionContext) -> None:
        """The final pre-effect gate checkpoint, after Admin/parent/device locks.

        AccessDeviceService calls this inside the same transaction that commits a
        target attempt. A recovery revocation therefore wins if it commits first;
        a callback that already committed remains potentially sent and is never
        reissued by recovery.
        """
        if bound.actor_user_id is None or type(bound.actor_auth_version) is not int:
            raise ValueError("Notification action requester changed; a fresh confirmation is required.")
        try:
            # AccessDeviceService already holds this actor in normal dispatch.
            # Taking the same row here preserves actor -> context -> Person order
            # for direct callers as well as the final target-attempt checkpoint.
            actor = await load_active_admin(
                session,
                bound.actor_user_id,
                auth_version=bound.actor_auth_version,
                lock=True,
            )
        except MutationError as exc:
            raise ValueError("Notification action requester changed; a fresh confirmation is required.") from exc
        row = await session.scalar(
            select(NotificationActionContext)
            .where(NotificationActionContext.id == bound.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        current = self._bound_from_row(row) if row is not None else None
        if row is None or not _is_v2_bound(current) or current != bound:
            raise ValueError("Notification action context changed; a fresh confirmation is required.")
        metadata = _metadata(row)
        if row.outcome not in {"dispatch_pending", "attempting"} or metadata.get("dispatch_state") not in {"active", "attempting"}:
            raise ValueError("Notification action dispatch was revoked; a fresh confirmation is required.")
        # Hold the existing matching recipient rows through the transaction that
        # records the target attempt. Reassigning one of those bound recipients
        # cannot commit after this current-state check but before provider I/O.
        if await self._locked_bound_identity(session, bound, actor) is None:
            raise ValueError("Notification action requester changed; a fresh confirmation is required.")
        runtime_config = await get_runtime_config_for_session(session)
        if bound.mobile_configuration_binding != _mobile_configuration_binding(runtime_config):
            raise ValueError("Notification action mobile configuration changed; a fresh confirmation is required.")
        # The recipient lock can block behind a concurrent reassignment. Refresh
        # database time after every authority lock before renewing the lease or
        # admitting the target attempt.
        now = await session.scalar(select(func.clock_timestamp()))
        if row.expires_at <= now:
            raise ValueError("Notification action expired before dispatch; a fresh confirmation is required.")
        if _dispatch_lease_expired(metadata, now):
            raise ValueError("Notification action dispatch lease expired; a fresh confirmation is required.")
        row.outcome = "attempting"
        row.outcome_detail = "Access-device dispatch checkpoint accepted; provider outcome is pending."
        row.metadata_ = {
            **metadata,
            "dispatch_state": "attempting",
            "dispatch_lease_expires_at": _dispatch_lease_expires_at(now),
        }

    async def _prepare_force_child(self, parent: BoundActionContext) -> PreparedForceChild | None:
        """Acquire the fresh force plan before any finalization context lock."""
        try:
            target_plan = await get_gate_command_coordinator().preview_manual_gate_open()
            runtime_config = await get_runtime_config()
        except (LookupError, ValueError) as exc:
            logger.warning(
                "actionable_notification_force_preview_unavailable",
                extra={"context_id": str(parent.id), "error": str(exc)},
            )
            return None
        if not _is_manual_all_gates_plan(target_plan):
            return None
        return PreparedForceChild(
            context_id=uuid.uuid5(parent.id, ACTIONABLE_FORCE_CHILD_PURPOSE),
            target_plan=copy.deepcopy(target_plan),
            mobile_configuration_binding=_mobile_configuration_binding(runtime_config),
        )

    async def _finalize_action_result(
        self,
        bound: BoundActionContext,
        outcome: GateActionOutcome,
        *,
        force: bool,
        result_kind: str,
        prepared_force: PreparedForceChild | None = None,
        preserve_outcome: bool = False,
        allow_any_dispatch_state: bool = False,
        recovery: bool = False,
        event_data: dict[str, Any] | None = None,
    ) -> FinalizationResult:
        """Commit context outcome, audit, optional child, and literal run together."""
        del event_data  # Event payloads are not durable authority or delivery input.
        async with AsyncSessionLocal() as session:
            # Access-device dispatch takes the active actor before its context
            # checkpoint. Finalization must take the same FK-compatible user
            # lock before the context, even for an outcome whose actor has since
            # become inactive: audit and an optional force child both reference
            # this actor and must remain durable.
            await self._lock_finalization_actor(session, bound)
            row = await session.scalar(
                select(NotificationActionContext)
                .where(NotificationActionContext.id == bound.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                return FinalizationResult(False)
            # The child context's person FK can otherwise acquire a conflicting
            # implicit lock after the parent context. Keep actor -> context ->
            # person ordering with the output and dispatch checkpoints.
            await self._lock_finalization_person(session, bound)
            stored = self._bound_from_row(row)
            if not _is_v2_bound(stored) or stored != bound:
                return FinalizationResult(False)
            metadata = _metadata(row)
            state = str(metadata.get("dispatch_state") or "")
            if recovery:
                if state != "revoked":
                    return FinalizationResult(False)
            elif not allow_any_dispatch_state and state not in {"active", "attempting"}:
                return FinalizationResult(False)
            finalization = await self._finalize_locked_result(
                session,
                row,
                stored,
                outcome,
                force=force,
                result_kind=result_kind,
                prepared_force=prepared_force,
                preserve_outcome=preserve_outcome,
            )
            if finalization.changed:
                await session.commit()
            else:
                await session.rollback()
        if finalization.changed:
            await self._publish_finalized_outcome(bound, outcome, force=force, result_kind=result_kind)
        return finalization

    @staticmethod
    async def _lock_finalization_actor(session: AsyncSession, bound: BoundActionContext) -> None:
        """Take the audit/child actor FK lock without reauthorizing the actor."""
        if bound.actor_user_id is None:
            return
        await session.scalar(
            select(User)
            .where(User.id == bound.actor_user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    @staticmethod
    async def _lock_finalization_person(session: AsyncSession, bound: BoundActionContext) -> None:
        """Take the optional force-child person FK lock after its parent context."""
        if bound.person_id is None:
            return
        await session.scalar(
            select(Person)
            .where(Person.id == bound.person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def _finalize_locked_result(
        self,
        session: AsyncSession,
        row: NotificationActionContext,
        bound: BoundActionContext,
        outcome: GateActionOutcome,
        *,
        force: bool,
        result_kind: str,
        prepared_force: PreparedForceChild | None,
        preserve_outcome: bool,
    ) -> FinalizationResult:
        metadata = _metadata(row)
        outputs = copy.deepcopy(metadata.get("outputs") if isinstance(metadata.get("outputs"), dict) else {})
        run_id = _output_run_id(bound.id, result_kind)
        existing = outputs.get(result_kind)
        if isinstance(existing, dict):
            try:
                if uuid.UUID(str(existing.get("run_id"))) == run_id:
                    return FinalizationResult(False, run_id=run_id, force_context_id=_optional_uuid(existing.get("force_context_id")))
            except (TypeError, ValueError):
                pass
            raise ValueError("Actionable output identity was reused with different content.")

        runtime_config = await get_runtime_config_for_session(session)
        child: NotificationActionContext | None = None
        if prepared_force is not None and not force and _force_child_allowed(outcome):
            child = await self._materialize_force_child(
                session,
                parent=row,
                bound=bound,
                prepared=prepared_force,
                runtime_config=runtime_config,
            )
        title, message = _result_content(bound, outcome, force=force)
        child_id = child.id if child is not None else None
        action = _literal_output_action(
            bound,
            title=title,
            message=message,
            result_kind=result_kind,
            force_context_id=child_id,
        )
        plan = [{
            "rule": {
                "id": "actionable-gate-output-v2",
                "name": "Actionable Gate Output",
                "trigger_event": "actionable_gate_notification",
            },
            "action": action,
            "state": "pending",
        }]
        config_binding = configuration_binding(runtime_config, plan)
        output_origin = _output_origin(bound, run_id=run_id, result_kind=result_kind,
                                       configuration_binding=config_binding, force_context_id=child_id)
        payload = notification_context_payload(
            NotificationContext(
                event_type="actionable_gate_notification",
                subject=title,
                severity="warning" if not (outcome.accepted or outcome.mechanically_confirmed) else "info",
                facts={
                    "registration_number": bound.registration_number,
                    "vehicle_registration_number": bound.registration_number,
                    "access_event_id": str(bound.access_event_id) if bound.access_event_id else "",
                    "telemetry_trace_id": bound.telemetry_trace_id or "",
                    "message": message,
                    "actionable_context_id": str(bound.id),
                    "actionable_result_kind": result_kind,
                },
            )
        )
        payload["actionable_output_origin"] = output_origin
        await self._run_store.enqueue_prepared_in_session(session, payload, run_id=run_id, plan=plan)

        recorded_outcome = _recorded_outcome(outcome)
        if not preserve_outcome:
            row.outcome = recorded_outcome
            row.outcome_detail = _bounded_detail(outcome.detail)
        outputs[result_kind] = {
            "version": ACTIONABLE_OUTPUT_VERSION,
            "run_id": str(run_id),
            "destination_binding": bound.destination_binding,
            "configuration_binding": config_binding,
            "force_context_id": str(child_id) if child_id else None,
        }
        if preserve_outcome:
            # A wrong-action or replay notice must not revoke an in-flight
            # original command. The original worker still owns its active or
            # attempting fence and later finalizes its own immutable output.
            row.metadata_ = {**metadata, "outputs": outputs}
        else:
            row.metadata_ = {
                **metadata,
                "dispatch_state": "finalized",
                "outputs": outputs,
                "finalized_result_kind": result_kind,
                "finalized_at": (await session.scalar(select(func.clock_timestamp()))).isoformat(),
            }
        identity = await self._current_bound_identity(session, bound)
        audit_outcome = _audit_outcome(outcome)
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            action=("gate.open.actionable_notification.force" if force else "gate.open.actionable_notification"),
            actor=actor_from_user(identity.user) if identity else "Home Assistant Notification",
            actor_user_id=bound.actor_user_id,
            target_entity="NotificationActionContext",
            target_id=bound.id,
            target_label="All Gates",
            outcome=audit_outcome,
            level="warning" if audit_outcome == "uncertain" else "info" if audit_outcome in {"success", "partial"} else "error",
            trace_id=bound.telemetry_trace_id,
            metadata={
                "action": bound.action,
                "context_id": str(bound.id),
                "parent_context_id": str(bound.parent_context_id) if bound.parent_context_id else None,
                "registration_number": bound.registration_number,
                "access_event_id": str(bound.access_event_id) if bound.access_event_id else None,
                "person_id": str(bound.person_id) if bound.person_id else None,
                "actor_user_id": str(bound.actor_user_id) if bound.actor_user_id else None,
                "force": force,
                "state": outcome.state,
                "delivery": str(outcome.delivery),
                "accepted": outcome.accepted,
                "mechanically_confirmed": outcome.mechanically_confirmed,
                "requires_reconciliation": outcome.requires_reconciliation,
                "target_receipts": _safe_target_receipt_truth(outcome.target_receipts),
                "detail": _bounded_detail(outcome.detail),
                "command_id": outcome.command_id,
                "result_kind": result_kind,
                "notification_run_id": str(run_id),
                "force_context_id": str(child_id) if child_id else None,
                "malfunction_id": str(outcome.malfunction_id) if outcome.malfunction_id else None,
                "malfunction_duration_seconds": outcome.malfunction_duration_seconds,
            },
        )
        return FinalizationResult(True, run_id=run_id, force_context_id=child_id)

    async def _materialize_force_child(
        self,
        session: AsyncSession,
        *,
        parent: NotificationActionContext,
        bound: BoundActionContext,
        prepared: PreparedForceChild,
        runtime_config: Any,
    ) -> NotificationActionContext | None:
        """Create only a fresh, current, definite-failure confirmation child."""
        if prepared.context_id != uuid.uuid5(bound.id, ACTIONABLE_FORCE_CHILD_PURPOSE):
            return None
        if not _is_manual_all_gates_plan(prepared.target_plan):
            return None
        # The plan/config read happened before the parent-context finalization
        # lock. This short validation never acquires a gate parent or child lock.
        identity = await self._current_bound_identity(session, bound)
        if identity is None or prepared.mobile_configuration_binding != _mobile_configuration_binding(runtime_config):
            return None
        existing = await session.get(NotificationActionContext, prepared.context_id)
        if existing is not None:
            existing_bound = self._bound_from_row(existing)
            if (
                existing.parent_context_id == parent.id
                and existing.action == GATE_FORCE_OPEN_ACTION
                and _is_v2_bound(existing_bound)
            ):
                return existing
            return None
        now = await session.scalar(select(func.clock_timestamp()))
        child = NotificationActionContext(
            id=prepared.context_id,
            token_hash=_token_hash(_derived_action_token(prepared.context_id, GATE_FORCE_OPEN_ACTION)),
            action=GATE_FORCE_OPEN_ACTION,
            notify_service=bound.notify_service,
            registration_number=bound.registration_number,
            access_event_id=bound.access_event_id,
            telemetry_trace_id=bound.telemetry_trace_id,
            person_id=bound.person_id,
            actor_user_id=bound.actor_user_id,
            parent_context_id=parent.id,
            expires_at=now + FORCE_TOKEN_TTL,
            metadata_=_new_context_metadata(
                context_id=prepared.context_id,
                action=GATE_FORCE_OPEN_ACTION,
                identity=identity,
                notify_service=bound.notify_service,
                target_plan=prepared.target_plan,
                mobile_configuration_binding=prepared.mobile_configuration_binding,
                source_event_type="actionable_gate_force_follow_up",
                source_subject="Force Open All Gates",
                parent_context_id=parent.id,
            ),
        )
        session.add(child)
        return child

    async def authorize_notification_output_in_session(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        action: dict[str, Any] | None,
        *,
        config: Any | None = None,
        final: bool = False,
    ) -> str | None:
        """Validate literal result delivery without granting another gate command."""
        origin = payload.get("actionable_output_origin")
        if not isinstance(origin, dict) or origin.get("version") != ACTIONABLE_OUTPUT_VERSION:
            return "actionable_output_origin_invalid"
        context_id = _optional_uuid(origin.get("context_id"))
        result_kind = str(origin.get("result_kind") or "")
        if context_id is None or not result_kind or run_id != _output_run_id(context_id, result_kind):
            return "actionable_output_identity_invalid"
        if str(origin.get("run_id") or "") != str(run_id):
            return "actionable_output_identity_invalid"
        if not _valid_output_action(action, context_id=context_id, result_kind=result_kind, origin=origin):
            return "actionable_output_destination_invalid"
        actor_id = _optional_uuid(origin.get("actor_user_id"))
        person_id = _optional_uuid(origin.get("person_id"))
        auth_version = origin.get("auth_version")
        if actor_id is None or person_id is None or type(auth_version) is not int:
            return "actionable_output_origin_invalid"
        if final:
            if config is None:
                return "actionable_output_configuration_unavailable"
            # The initial participant retains actor/context/recipient locks while
            # NotificationRunStore waits for the run row. A new duplicate
            # recipient can still commit during that wait, so read the current
            # service population again after the run lock before provider I/O.
            if not await self._current_unique_mobile_recipient(
                session,
                notify_service=str(origin.get("notify_service") or ""),
                person_id=person_id,
            ):
                return "actionable_output_requester_changed"
            plan = [{"action": action}]
            if origin.get("configuration_binding") != configuration_binding(config, plan):
                return "actionable_output_configuration_changed"
            return None
        try:
            # NotificationRunStore takes its claim lock after this participant.
            # Keep the actor -> context -> exact recipient lock order through
            # that wait, so a notify-service reassignment cannot commit between
            # origin validation and the literal delivery checkpoint.
            actor = await load_active_admin(session, actor_id, auth_version=auth_version, lock=True)
        except MutationError:
            return "actionable_output_actor_changed"
        row = await session.scalar(
            select(NotificationActionContext)
            .where(NotificationActionContext.id == context_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        bound = self._bound_from_row(row) if row is not None else None
        if row is None or not _is_v2_bound(bound):
            return "actionable_output_context_invalid"
        if actor.id != bound.actor_user_id or actor.person_id != bound.person_id or person_id != bound.person_id:
            return "actionable_output_requester_changed"
        if await self._locked_bound_identity(session, bound, actor) is None:
            return "actionable_output_requester_changed"
        metadata = _metadata(row)
        outputs = metadata.get("outputs") if isinstance(metadata.get("outputs"), dict) else {}
        descriptor = outputs.get(result_kind) if isinstance(outputs, dict) else None
        if not isinstance(descriptor, dict) or str(descriptor.get("run_id") or "") != str(run_id):
            return "actionable_output_not_committed"
        if (
            descriptor.get("destination_binding") != bound.destination_binding
            or origin.get("destination_binding") != bound.destination_binding
            or descriptor.get("configuration_binding") != origin.get("configuration_binding")
            or descriptor.get("force_context_id") != origin.get("force_context_id")
        ):
            return "actionable_output_destination_invalid"
        return None

    async def resolve_notification_output_actions(
        self,
        action: dict[str, Any],
        *,
        target: str,
    ) -> list[dict[str, Any]]:
        """Resolve an opaque force token only at the final mobile delivery edge."""
        descriptor = action.get("actionable_output")
        if not isinstance(descriptor, dict) or descriptor.get("version") != ACTIONABLE_OUTPUT_VERSION:
            return []
        parent_id = _optional_uuid(descriptor.get("context_id"))
        child_id = _optional_uuid(descriptor.get("force_context_id"))
        if parent_id is None or child_id is None or not _is_home_assistant_mobile_notify_service(target):
            return []
        if descriptor.get("destination_binding") != _destination_binding(parent_id, target):
            return []
        async with AsyncSessionLocal() as session:
            child = await session.get(NotificationActionContext, child_id)
            if child is None or child.parent_context_id != parent_id or child.action != GATE_FORCE_OPEN_ACTION:
                return []
            bound = self._bound_from_row(child)
            now = await session.scalar(select(func.clock_timestamp()))
            if (
                not _is_v2_bound(bound)
                or child.consumed_at is not None
                or child.expires_at <= now
                or bound.notify_service != target
                or bound.destination_binding != _destination_binding(child.id, target)
            ):
                return []
            if await self._current_bound_identity(session, bound) is None:
                return []
            runtime_config = await get_runtime_config_for_session(session)
            metadata = _metadata(child)
            if metadata.get("mobile_configuration_binding") != _mobile_configuration_binding(runtime_config):
                return []
        return [{
            "action": f"{GATE_FORCE_OPEN_PREFIX}{_derived_action_token(child_id, GATE_FORCE_OPEN_ACTION)}",
            "title": "Force Open All Gates",
            "destructive": True,
        }]

    async def reconcile_actionable_outputs(
        self,
        *,
        limit: int = 25,
        after_id: uuid.UUID | None = None,
    ) -> tuple[int, uuid.UUID | None]:
        """Recover only v2 journal output; it never invokes a gate controller."""
        if not 1 <= limit <= 100:
            raise ValueError("Actionable recovery limit must be between 1 and 100.")
        bound_rows, next_cursor = await self._revoke_recovery_batch(limit=limit, after_id=after_id)
        changed = 0
        for bound in bound_rows:
            # Revocation committed before this receipt read. If a prior callback
            # was already attempting, missing receipt truth is unknown. A failed
            # output/read remains revoked and does not hold later cursor work.
            try:
                receipt = await get_gate_command_coordinator().get_receipt(intent_id=str(bound.id))
            except Exception:
                logger.exception("actionable_notification_recovery_receipt_failed", extra={"context_id": str(bound.id)})
                continue
            outcome = _outcome_from_recovery_receipt(receipt)
            prepared_force = None
            if bound.action == GATE_OPEN_ACTION and _force_child_allowed(outcome):
                try:
                    prepared_force = await self._prepare_force_child(bound)
                except Exception:
                    logger.exception("actionable_notification_recovery_force_preview_failed", extra={"context_id": str(bound.id)})
            try:
                finalized = await self._finalize_action_result(
                    bound,
                    outcome,
                    force=bound.action == GATE_FORCE_OPEN_ACTION,
                    result_kind="force_result" if bound.action == GATE_FORCE_OPEN_ACTION else "normal_result",
                    prepared_force=prepared_force,
                    recovery=True,
                )
            except Exception:
                logger.exception("actionable_notification_recovery_output_failed", extra={"context_id": str(bound.id)})
                continue
            changed += int(finalized.changed)
        return changed, next_cursor

    async def _revoke_recovery_batch(
        self,
        *,
        limit: int,
        after_id: uuid.UUID | None,
    ) -> tuple[list[BoundActionContext], uuid.UUID | None]:
        async with AsyncSessionLocal() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            rows = await self._unfinished_recovery_rows(session, limit=limit, after_id=after_id, now=now)
            if not rows and after_id is not None:
                # Explicit rollover prevents a high UUID cursor from starving
                # older unresolved contexts after new rows arrive.
                now = await session.scalar(select(func.clock_timestamp()))
                rows = await self._unfinished_recovery_rows(session, limit=limit, after_id=None, now=now)
            bound_rows: list[BoundActionContext] = []
            changed = False
            for row in rows:
                bound = self._bound_from_row(row)
                metadata = _metadata(row)
                if (
                    not _is_v2_bound(bound)
                    or metadata.get("dispatch_state") not in {"active", "attempting", "revoked"}
                ):
                    # A malformed v2 record cannot safely be dispatched or
                    # rendered. Terminalize it for review so it cannot consume a
                    # bounded recovery cursor forever.
                    row.outcome = "pending_reconciliation"
                    row.outcome_detail = "Recovery found an invalid actionable context; review is required."
                    row.metadata_ = {
                        **metadata,
                        "dispatch_state": "invalid_review",
                        "dispatch_revoked_at": now.isoformat(),
                    }
                    changed = True
                    continue
                if row.outcome not in {"dispatch_pending", "attempting", "dispatch_revoked"}:
                    continue
                if not _dispatch_lease_expired(metadata, now):
                    continue
                row.outcome = "dispatch_revoked"
                row.outcome_detail = "Recovery revoked an expired actionable dispatch lease before receipt classification."
                row.metadata_ = {
                    **metadata,
                    "dispatch_state": "revoked",
                    "dispatch_revoked_at": now.isoformat(),
                }
                bound_rows.append(bound)
                changed = True
            if changed:
                await session.commit()
            else:
                await session.rollback()
            next_cursor = rows[-1].id if len(rows) == limit else None
        return bound_rows, next_cursor

    async def _unfinished_recovery_rows(
        self,
        session: AsyncSession,
        *,
        limit: int,
        after_id: uuid.UUID | None,
        now: datetime,
    ) -> list[NotificationActionContext]:
        lease_expires_at = NotificationActionContext.metadata_["dispatch_lease_expires_at"].astext
        valid_lease_format = lease_expires_at.op("~")(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")
        query = (
            select(NotificationActionContext)
            .where(NotificationActionContext.action.in_([GATE_OPEN_ACTION, GATE_FORCE_OPEN_ACTION]))
            .where(NotificationActionContext.consumed_at.is_not(None))
            .where(NotificationActionContext.metadata_["version"].astext == str(ACTIONABLE_CONTEXT_VERSION))
            .where(NotificationActionContext.outcome.in_(["dispatch_pending", "attempting", "dispatch_revoked"]))
            # All valid lease values are generated as fixed-width UTC ISO strings.
            # Missing or malformed values are selected for terminal review rather
            # than permanently consuming a bounded recovery cursor.
            .where(or_(
                lease_expires_at.is_(None),
                ~valid_lease_format,
                lease_expires_at <= _utc_iso_microseconds(now),
            ))
            .order_by(NotificationActionContext.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        if after_id is not None:
            query = query.where(NotificationActionContext.id > after_id)
        return list((await session.scalars(query)).all())

    async def _publish_finalized_outcome(
        self,
        bound: BoundActionContext,
        outcome: GateActionOutcome,
        *,
        force: bool,
        result_kind: str,
    ) -> None:
        try:
            await event_bus.publish(
                "gate.actionable_notification_executed",
                {
                    "action": bound.action,
                    "context_id": str(bound.id),
                    "registration_number": bound.registration_number,
                    "access_event_id": str(bound.access_event_id) if bound.access_event_id else None,
                    "person_id": str(bound.person_id) if bound.person_id else None,
                    "actor_user_id": str(bound.actor_user_id) if bound.actor_user_id else None,
                    "accepted": outcome.accepted,
                    "force": force,
                    "state": outcome.state,
                    "delivery": str(outcome.delivery),
                    "requires_reconciliation": outcome.requires_reconciliation,
                    "result_kind": result_kind,
                },
            )
        except Exception:
            logger.exception("actionable_notification_publication_failed", extra={"context_id": str(bound.id)})

    async def _audit_unbound_failure(self, *, action: str, reason: str) -> None:
        """Audit an unknown opaque token; no direct mobile result is attempted."""
        async with AsyncSessionLocal() as session:
            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action=(
                    "gate.open.actionable_notification.force"
                    if action == GATE_FORCE_OPEN_ACTION
                    else "gate.open.actionable_notification"
                ),
                actor="Home Assistant Notification",
                target_entity="NotificationActionContext",
                target_label="All Gates",
                outcome="failed",
                level="warning",
                metadata={"action": action, "reason": reason},
            )
            await session.commit()

    async def _identity_for_notify_service(
        self,
        session: AsyncSession,
        notify_service: str,
        *,
        lock: bool = False,
    ) -> ActionIdentity | None:
        people_query = select(Person).where(
            Person.home_assistant_mobile_app_notify_service == notify_service,
            Person.is_active.is_(True),
        ).order_by(Person.id)
        if lock:
            people_query = people_query.with_for_update()
        people = list((await session.scalars(people_query)).all())
        if len(people) != 1:
            return None
        person = people[0]
        users_query = select(User).where(
            User.person_id == person.id,
            User.is_active.is_(True),
        ).order_by(User.id)
        if lock:
            users_query = users_query.with_for_update()
        users = list((await session.scalars(users_query)).all())
        if len(users) != 1 or users[0].role != UserRole.ADMIN:
            return None
        return ActionIdentity(person=person, user=users[0])

    async def _locked_bound_identity(
        self,
        session: AsyncSession,
        bound: BoundActionContext,
        actor: User,
    ) -> ActionIdentity | None:
        """Lock the exact mobile recipient after its actor/context authority.

        The actor row is already locked by the caller. Lock every active Person
        with this service name so a duplicate/reassigned recipient cannot become
        authoritative while a final action checkpoint waits on later locks.
        We deliberately do not take a second broad User lock after the Person
        locks; the locked actor is the authoritative Admin row.
        """
        if actor.id != bound.actor_user_id or actor.person_id != bound.person_id:
            return None
        people = list((await session.scalars(
            select(Person)
            .where(
                Person.home_assistant_mobile_app_notify_service == bound.notify_service,
                Person.is_active.is_(True),
            )
            .order_by(Person.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )).all())
        if len(people) != 1 or people[0].id != bound.person_id:
            return None
        users = list((await session.scalars(
            select(User)
            .where(User.person_id == bound.person_id, User.is_active.is_(True))
            .order_by(User.id)
            .execution_options(populate_existing=True)
        )).all())
        if len(users) != 1 or users[0].id != actor.id or users[0].role != UserRole.ADMIN:
            return None
        return ActionIdentity(person=people[0], user=actor)

    @staticmethod
    async def _current_unique_mobile_recipient(
        session: AsyncSession,
        *,
        notify_service: str,
        person_id: uuid.UUID,
    ) -> bool:
        people = list((await session.scalars(
            select(Person)
            .where(
                Person.home_assistant_mobile_app_notify_service == notify_service,
                Person.is_active.is_(True),
            )
            .order_by(Person.id)
            .execution_options(populate_existing=True)
        )).all())
        return len(people) == 1 and people[0].id == person_id


    async def _current_bound_identity(
        self,
        session: AsyncSession,
        bound: BoundActionContext,
        *,
        lock: bool = False,
    ) -> ActionIdentity | None:
        if bound.actor_user_id is None or bound.person_id is None or type(bound.actor_auth_version) is not int:
            return None
        try:
            actor = await load_active_admin(
                session,
                bound.actor_user_id,
                auth_version=bound.actor_auth_version,
                lock=lock,
            )
        except MutationError:
            return None
        if actor.person_id != bound.person_id:
            return None
        people_query = select(Person).where(
            Person.id == bound.person_id,
            Person.is_active.is_(True),
            Person.home_assistant_mobile_app_notify_service == bound.notify_service,
        )
        if lock:
            people_query = people_query.with_for_update()
        person = await session.scalar(people_query.execution_options(populate_existing=True))
        if person is None:
            return None
        users_query = select(User).where(
            User.person_id == person.id,
            User.is_active.is_(True),
        ).order_by(User.id)
        if lock:
            users_query = users_query.with_for_update()
        users = list((await session.scalars(users_query)).all())
        if len(users) != 1 or users[0].id != actor.id or users[0].role != UserRole.ADMIN:
            return None
        return ActionIdentity(person=person, user=actor)

    def _bound_from_row(self, row: NotificationActionContext) -> BoundActionContext:
        metadata = _metadata(row)
        actor = metadata.get("actor") if isinstance(metadata.get("actor"), dict) else {}
        metadata_user_id = _optional_uuid(actor.get("user_id"))
        metadata_person_id = _optional_uuid(actor.get("person_id"))
        actor_auth_version = actor.get("auth_version") if type(actor.get("auth_version")) is int else None
        if metadata_user_id != row.actor_user_id or metadata_person_id != row.person_id:
            actor_auth_version = None
        version = metadata.get("version")
        return BoundActionContext(
            id=row.id,
            action=row.action,
            notify_service=row.notify_service,
            registration_number=row.registration_number,
            access_event_id=row.access_event_id,
            telemetry_trace_id=row.telemetry_trace_id,
            person_id=row.person_id,
            actor_user_id=row.actor_user_id,
            parent_context_id=row.parent_context_id,
            expires_at=_aware_datetime(row.expires_at),
            actor_auth_version=actor_auth_version,
            target_plan=copy.deepcopy(metadata.get("target_plan")) if isinstance(metadata.get("target_plan"), dict) else None,
            destination_binding=str(metadata.get("destination_binding") or "") or None,
            mobile_configuration_binding=str(metadata.get("mobile_configuration_binding") or "") or None,
            context_version=version if type(version) is int else 0,
        )

    async def _malfunction_failure_message(
        self,
        bound: BoundActionContext,
        identity: ActionIdentity,
        malfunction: ActiveGateMalfunctionContext,
        *,
        force: bool,
    ) -> str:
        fallback = _fallback_malfunction_failure_message(bound, malfunction, force=force)
        try:
            runtime = await get_runtime_config()
            provider_name = str(runtime.llm_provider or "").strip().lower()
            if not provider_name or provider_name == "local":
                return fallback
            provider = get_llm_provider(provider_name)
            result = await complete_with_provider_options(
                provider,
                [
                    ChatMessageInput(
                        role="system",
                        content=(
                            "You write short Home Assistant mobile push notification messages for a private "
                            "gate access system. Be calm, human, and specific. Return only the notification "
                            "body, no markdown, no JSON, and no sign-off. Use natural wording, not incident "
                            "or engineering terminology."
                        ),
                    ),
                    ChatMessageInput(
                        role="user",
                        content=(
                            "A user pressed an actionable notification to open the gate. The gate was not "
                            "opened because there is already an unresolved malfunction. "
                            f"Registration: {bound.registration_number}. "
                            f"Gate: {malfunction.gate_name or malfunction.gate_entity_id}. "
                            f"Duration: {malfunction.duration_display}. "
                            "Write one concise sentence under 220 characters. Start with \"Sorry,\". It must "
                            "say the gate was not opened for the registration, that the gate has been "
                            "malfunctioning for the duration, and that it is currently unresolved. Do not "
                            "mention the requester, action type, IACS, force-open, Home Assistant, "
                            "active/FUBAR/status labels, blocking, retrying, or trying again."
                        ),
                    ),
                ],
                max_output_tokens=120,
                request_purpose="notifications.actionable_malfunction_failure",
            )
            message = _clean_llm_notification_text(result.text)
            if not _valid_malfunction_message(
                message,
                registration_number=bound.registration_number,
                duration_display=malfunction.duration_display,
                person_name=identity.person.display_name,
            ):
                message = await self._repair_malfunction_failure_message(
                    provider,
                    bad_message=message,
                    bound=bound,
                    malfunction=malfunction,
                )
            if not _valid_malfunction_message(
                message,
                registration_number=bound.registration_number,
                duration_display=malfunction.duration_display,
                person_name=identity.person.display_name,
            ):
                return fallback
            return message[:500]
        except ProviderNotConfiguredError:
            logger.info("actionable_notification_malfunction_llm_not_configured")
        except Exception as exc:  # noqa: BLE001 - optional LLM failures cannot block durable gate output.
            logger.warning(
                "actionable_notification_malfunction_message_failed",
                extra={
                    "context_id": str(bound.id),
                    "malfunction_id": str(malfunction.id),
                    "error": str(exc),
                },
            )
        return fallback

    async def _repair_malfunction_failure_message(
        self,
        provider: Any,
        *,
        bad_message: str,
        bound: BoundActionContext,
        malfunction: ActiveGateMalfunctionContext,
    ) -> str:
        result = await complete_with_provider_options(
            provider,
            [
                ChatMessageInput(
                    role="system",
                    content=(
                        "Rewrite a Home Assistant mobile notification. Return only one natural sentence. "
                        "No markdown, no JSON, no sign-off."
                    ),
                ),
                ChatMessageInput(
                    role="user",
                    content=(
                        f"The previous notification was unsuitable: {bad_message!r}. "
                        f"Write a replacement for registration {bound.registration_number}. "
                        f"The gate has been malfunctioning for {malfunction.duration_display} and is "
                        "currently unresolved. Start with \"Sorry,\" and keep it under 220 characters. "
                        "Do not mention the requester, blocked, try again, active "
                        "unresolved malfunction state, request, IACS, Home Assistant, force-open, or status labels."
                    ),
                ),
            ],
            max_output_tokens=120,
            request_purpose="notifications.actionable_malfunction_repair",
        )
        return _clean_llm_notification_text(result.text)

def _derived_action_token(context_id: uuid.UUID, action: str) -> str:
    """Opaque deterministic descriptor; only its HMAC hash is persisted."""
    material = f"{ACTIONABLE_TOKEN_PURPOSE}:{action}:{context_id}".encode()
    return hmac.new(get_auth_secret().encode(), material, hashlib.sha256).hexdigest()


def _token_hash(token: str) -> str:
    return hmac.new(get_auth_secret().encode(), token.encode(), hashlib.sha256).hexdigest()


def _destination_binding(context_id: uuid.UUID, notify_service: str) -> str:
    material = f"{ACTIONABLE_TOKEN_PURPOSE}:destination:{context_id}:{notify_service}".encode()
    return hmac.new(get_auth_secret().encode(), material, hashlib.sha256).hexdigest()


def _new_context_metadata(
    *,
    context_id: uuid.UUID,
    action: str,
    identity: ActionIdentity,
    notify_service: str,
    target_plan: dict[str, Any],
    mobile_configuration_binding: str,
    source_event_type: str,
    source_subject: str,
    parent_context_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "version": ACTIONABLE_CONTEXT_VERSION,
        "dispatch_state": "active",
        "actor": {
            "user_id": str(identity.user.id),
            "auth_version": identity.user.auth_session_version,
            "person_id": str(identity.person.id),
        },
        "target_plan": copy.deepcopy(target_plan),
        "destination_binding": _destination_binding(context_id, notify_service),
        "mobile_configuration_binding": mobile_configuration_binding,
        "source_event_type": str(source_event_type or "")[:120],
        "source_subject": str(source_subject or "")[:255],
        "parent_context_id": str(parent_context_id) if parent_context_id else None,
        "outputs": {},
    }


def _metadata(row: NotificationActionContext) -> dict[str, Any]:
    return copy.deepcopy(row.metadata_ if isinstance(row.metadata_, dict) else {})


def _dispatch_lease_expires_at(now: datetime) -> str:
    return _utc_iso_microseconds(_aware_datetime(now) + ACTIONABLE_DISPATCH_LEASE)


def _dispatch_lease_expired(metadata: dict[str, Any], now: datetime) -> bool:
    try:
        value = datetime.fromisoformat(str(metadata["dispatch_lease_expires_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    return _aware_datetime(value) <= _aware_datetime(now)


def _is_v2_bound(bound: BoundActionContext | None) -> bool:
    return bool(
        bound
        and bound.context_version == ACTIONABLE_CONTEXT_VERSION
        and bound.action in {GATE_OPEN_ACTION, GATE_FORCE_OPEN_ACTION}
        and _is_home_assistant_mobile_notify_service(bound.notify_service)
        and bound.actor_user_id
        and bound.person_id
        and type(bound.actor_auth_version) is int
        and _is_manual_all_gates_plan(bound.target_plan)
        and bound.destination_binding == _destination_binding(bound.id, bound.notify_service)
        and isinstance(bound.mobile_configuration_binding, str)
        and bool(bound.mobile_configuration_binding)
    )


def _is_manual_all_gates_plan(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("version") == 1
        and value.get("action") == "open"
        and value.get("target_device_key") is None
        and value.get("require_admission") is False
        and value.get("gate_only") is True
        and value.get("automatic_entry_policy") is False
        and isinstance(value.get("targets"), list)
        and bool(value["targets"])
    )


def _mobile_configuration_binding(config: Any) -> str:
    return configuration_binding(config, [{"action": {"type": "mobile"}}])


def _output_run_id(context_id: uuid.UUID, result_kind: str) -> uuid.UUID:
    return uuid.uuid5(context_id, f"{ACTIONABLE_OUTPUT_PURPOSE}:{result_kind}")


def _output_origin(
    bound: BoundActionContext,
    *,
    run_id: uuid.UUID,
    result_kind: str,
    configuration_binding: str,
    force_context_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "version": ACTIONABLE_OUTPUT_VERSION,
        "context_id": str(bound.id),
        "result_kind": result_kind,
        "run_id": str(run_id),
        "actor_user_id": str(bound.actor_user_id),
        "auth_version": bound.actor_auth_version,
        "person_id": str(bound.person_id),
        "notify_service": bound.notify_service,
        "destination_binding": bound.destination_binding,
        "configuration_binding": configuration_binding,
        "force_context_id": str(force_context_id) if force_context_id else None,
    }


def _literal_output_action(
    bound: BoundActionContext,
    *,
    title: str,
    message: str,
    result_kind: str,
    force_context_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "type": "mobile",
        "target": bound.notify_service,
        "title": title[:255],
        "message": message[:2000],
        "delivery_mode": "literal",
        "actionable_output": {
            "version": ACTIONABLE_OUTPUT_VERSION,
            "context_id": str(bound.id),
            "result_kind": result_kind,
            "destination_binding": bound.destination_binding,
            "force_context_id": str(force_context_id) if force_context_id else None,
        },
    }


def _valid_output_action(
    action: dict[str, Any] | None,
    *,
    context_id: uuid.UUID,
    result_kind: str,
    origin: dict[str, Any],
) -> bool:
    if not isinstance(action, dict) or action.get("type") != "mobile" or action.get("delivery_mode") != "literal":
        return False
    target = str(action.get("target") or "")
    descriptor = action.get("actionable_output")
    return bool(
        _is_home_assistant_mobile_notify_service(target)
        and isinstance(descriptor, dict)
        and descriptor.get("version") == ACTIONABLE_OUTPUT_VERSION
        and str(descriptor.get("context_id") or "") == str(context_id)
        and descriptor.get("result_kind") == result_kind
        and descriptor.get("destination_binding") == origin.get("destination_binding")
        and descriptor.get("force_context_id") == origin.get("force_context_id")
        and target == origin.get("notify_service")
        and origin.get("destination_binding") == _destination_binding(context_id, target)
    )


def _recorded_outcome(outcome: GateActionOutcome) -> str:
    if _outcome_needs_review(outcome):
        return "pending_reconciliation"
    if str(outcome.delivery) == GateCommandDelivery.PARTIAL:
        return "partial"
    # A fresh observation can prove the requested physical state before a
    # provider command begins. Keep its not_sent receipt in audit metadata, but
    # record the actionable result as satisfied and never offer a force retry.
    return "success" if outcome.accepted or outcome.mechanically_confirmed else "failed"


def _audit_outcome(outcome: GateActionOutcome) -> str:
    if _outcome_needs_review(outcome):
        return "uncertain"
    if str(outcome.delivery) == str(GateCommandDelivery.PARTIAL):
        return "partial"
    return "success" if outcome.accepted or outcome.mechanically_confirmed else "failed"


def _outcome_needs_review(outcome: GateActionOutcome) -> bool:
    delivery = str(outcome.delivery)
    if outcome.requires_reconciliation or delivery == str(GateCommandDelivery.UNKNOWN):
        return True
    if not outcome.target_receipts:
        # A bare partial aggregate or accepted response has no per-target proof
        # that it reached a terminal physical result.
        return delivery == str(GateCommandDelivery.PARTIAL) or (
            outcome.accepted and not outcome.mechanically_confirmed
        )
    for receipt in outcome.target_receipts:
        receipt_delivery = str(receipt.get("delivery") or "unknown")
        if receipt_delivery not in {"accepted", "not_sent", "rejected"}:
            return True
        if bool(receipt.get("requires_reconciliation")):
            return True
        if receipt_delivery == "accepted" and not bool(receipt.get("verified")):
            return True
    return False


def _force_child_allowed(outcome: GateActionOutcome) -> bool:
    if outcome.accepted or outcome.mechanically_confirmed or outcome.malfunction_id or outcome.requires_reconciliation:
        return False
    if str(outcome.delivery) not in {str(GateCommandDelivery.NOT_SENT), str(GateCommandDelivery.REJECTED)}:
        return False
    return not any(
        str(receipt.get("delivery") or "") in {"accepted", "unknown", "partial"}
        for receipt in outcome.target_receipts
    )


def _all_target_receipts_not_sent(outcome: GateActionOutcome) -> bool:
    return bool(outcome.target_receipts) and all(
        str(receipt.get("delivery") or "") == str(GateCommandDelivery.NOT_SENT)
        for receipt in outcome.target_receipts
    )


def _safe_target_receipt_truth(receipts: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Persist bounded per-target certainty without provider payloads or details."""
    safe: list[dict[str, Any]] = []
    allowed_deliveries = {"accepted", "not_sent", "rejected", "unknown"}
    allowed_states = {"open", "opening", "closed", "closing", "unknown"}
    for index, receipt in enumerate(receipts[:20], start=1):
        delivery = str(receipt.get("delivery") or "unknown").lower()
        state = str(receipt.get("state") or "unknown").lower()
        target: dict[str, Any] = {
            "index": index,
            "delivery": delivery if delivery in allowed_deliveries else "unknown",
            "accepted": bool(receipt.get("accepted")),
            "verified": bool(receipt.get("verified")),
            "requires_reconciliation": bool(receipt.get("requires_reconciliation")),
            "state": state if state in allowed_states else "unknown",
        }
        target_id = _optional_uuid(receipt.get("target_device_id"))
        if target_id is not None:
            target["target_device_id"] = str(target_id)
        safe.append(target)
    return safe


def _result_content(bound: BoundActionContext, outcome: GateActionOutcome, *, force: bool) -> tuple[str, str]:
    command = "force-open command" if force else "gate open command"
    if outcome.malfunction_id:
        return (
            "Gate still malfunctioning" if force else "Gate did not open",
            outcome.detail or _fallback_generic_gate_failure(bound),
        )
    if outcome.mechanically_confirmed:
        if not outcome.accepted and _all_target_receipts_not_sent(outcome):
            title = "Gate already open" if outcome.state == "open" else "Gate already opening"
            return (
                title,
                f"The gate was already {outcome.state} for {bound.registration_number}; no {command} was sent.",
            )
        title = "Gate opened" if outcome.state == "open" else "Gate opening"
        if str(outcome.delivery) == str(GateCommandDelivery.PARTIAL):
            return (
                title,
                (
                    f"The gate was confirmed {outcome.state} for {bound.registration_number}. "
                    "Some targets were already in the required state and other target commands were accepted."
                ),
            )
        if outcome.accepted:
            return title, f"The {command} for {bound.registration_number} was accepted."
        return title, f"The gate was confirmed {outcome.state} for {bound.registration_number}."
    if str(outcome.delivery) == str(GateCommandDelivery.PARTIAL) and not _outcome_needs_review(outcome):
        return (
            "Gate partially opened",
            (
                f"The {command} for {bound.registration_number} completed for some targets. "
                "Other targets were not confirmed in the requested state; inspect the per-target results."
            ),
        )
    if outcome.accepted:
        return (
            "Gate command accepted",
            f"The {command} for {bound.registration_number} was accepted, but its physical state needs reconciliation.",
        )
    if _outcome_needs_review(outcome):
        return (
            "Gate command needs review",
            (
                f"The {command} for {bound.registration_number} may have been received. "
                "Its outcome needs reconciliation before another command."
            ),
        )
    if force:
        return (
            "Force open failed",
            f"The force-open command for {bound.registration_number} failed. {outcome.detail or 'The gate command failed.'}",
        )
    return (
        "Gate did not open",
        f"The gate was not opened for {bound.registration_number}. {outcome.detail or 'The gate command failed.'}",
    )


def _outcome_from_recovery_receipt(receipt: dict[str, Any] | None) -> GateActionOutcome:
    # Absence after revocation does not prove that a delayed callback never sent.
    # Retain an unknown/review outcome so recovery cannot create another command.
    if not isinstance(receipt, dict):
        return GateActionOutcome(
            False,
            "No durable gate command receipt is available after dispatch revocation; review is required.",
            state="unknown",
            reason="actionable_command_receipt_missing",
            delivery=str(GateCommandDelivery.UNKNOWN),
            requires_reconciliation=True,
        )
    delivery = str(receipt.get("delivery") or GateCommandDelivery.UNKNOWN)
    target_receipts = tuple(item for item in receipt.get("target_receipts", []) if isinstance(item, dict))
    return GateActionOutcome(
        accepted=bool(receipt.get("accepted")),
        detail=str(receipt.get("detail") or "Recovered gate command receipt."),
        state=str(receipt.get("state") or "unknown"),
        reason="actionable_command_receipt_recovered",
        command_id=str(receipt.get("command_id") or "") or None,
        delivery=delivery,
        mechanically_confirmed=bool(receipt.get("mechanically_confirmed")),
        requires_reconciliation=bool(receipt.get("requires_reconciliation")) or delivery in {"unknown", "partial"},
        target_receipts=target_receipts,
    )


def _bounded_detail(value: Any) -> str:
    return str(value or "").strip()[:2000]


def _strip_prefix(value: str, prefix: str) -> str | None:
    if not value.startswith(prefix):
        return None
    token = value[len(prefix):].strip()
    return token or None

def _registration_from_context(context: NotificationContext) -> str:
    raw = (
        context.facts.get("registration_number")
        or context.facts.get("vehicle_registration_number")
        or context.subject
    )
    return str(raw or "").strip().upper()[:32]


def _optional_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _trace_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if len(text) == 32 else None


def _is_home_assistant_mobile_notify_service(value: str) -> bool:
    return bool(_MOBILE_NOTIFY_SERVICE.fullmatch(str(value or "")))


def _actor_from_person(person: Person | None) -> str:
    if not person:
        return "Home Assistant Notification"
    return f"{person.display_name} (Home Assistant notification)"


async def _is_maintenance_mode_active() -> bool:
    from app.services.maintenance_state import is_maintenance_mode_active

    return await is_maintenance_mode_active()


async def _active_gate_malfunction() -> ActiveGateMalfunctionContext | None:
    now = datetime.now(tz=UTC)
    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(GateMalfunctionState)
            .where(GateMalfunctionState.status.in_([GateMalfunctionStatus.ACTIVE, GateMalfunctionStatus.FUBAR]))
            .order_by(GateMalfunctionState.opened_at.desc(), GateMalfunctionState.declared_at.desc())
            .limit(1)
        )
    if not row:
        return None

    opened_at = _aware_datetime(row.opened_at)
    declared_at = _aware_datetime(row.declared_at)
    return ActiveGateMalfunctionContext(
        id=row.id,
        gate_entity_id=row.gate_entity_id,
        gate_name=row.gate_name,
        status=row.status,
        opened_at=opened_at,
        declared_at=declared_at,
        last_gate_state=row.last_gate_state,
        duration_seconds=max(0, int((now - opened_at).total_seconds())),
    )


def _aware_datetime(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _utc_iso_microseconds(value: datetime) -> str:
    return _aware_datetime(value).astimezone(UTC).isoformat(timespec="microseconds")


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 24 * 60 * 60)
    hours, remainder = divmod(remainder, 60 * 60)
    minutes = remainder // 60
    if days:
        parts = [f"{days} day{'s' if days != 1 else ''}"]
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        return " ".join(parts)
    if hours:
        parts = [f"{hours} hour{'s' if hours != 1 else ''}"]
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        return " ".join(parts)
    if minutes:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return "less than a minute"


def _fallback_malfunction_failure_message(
    bound: BoundActionContext,
    malfunction: ActiveGateMalfunctionContext,
    *,
    force: bool,
) -> str:
    return (
        f"Sorry, the gate was not opened for {bound.registration_number}, the gate has been "
        f"malfunctioning for {malfunction.duration_display} and is currently unresolved."
    )


def _fallback_generic_gate_failure(bound: BoundActionContext) -> str:
    return f"The gate was not opened for {bound.registration_number}. The gate command failed."


def _clean_llm_notification_text(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if text.startswith('"') and text.endswith('"') and len(text) > 1:
        text = text[1:-1].strip()
    return text


def _valid_malfunction_message(
    message: str,
    *,
    registration_number: str,
    duration_display: str,
    person_name: str,
) -> bool:
    text = _clean_llm_notification_text(message)
    if not text or len(text) > 500:
        return False
    lowered = text.lower()
    compact = _compact_alnum(text)
    if not lowered.startswith("sorry"):
        return False
    if _compact_alnum(registration_number) not in compact:
        return False
    if duration_display.lower() not in lowered:
        return False
    if "unresolved" not in lowered or "malfunction" not in lowered:
        return False
    if not any(phrase in lowered for phrase in ("not opened", "could not be opened", "couldn't be opened")):
        return False
    banned_terms = (
        "active unresolved malfunction state",
        "try again",
        "retry",
        "blocked",
        "request ",
        "request:",
        "request for",
        "iacs",
        "home assistant",
        "force-open",
        "force open",
        "fubar",
        "status",
        "requester",
        "action type",
    )
    if any(term in lowered for term in banned_terms):
        return False
    person = str(person_name or "").strip().lower()
    return not (person and person in lowered)


def _compact_alnum(value: str) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


@lru_cache
def get_actionable_notification_service() -> ActionableNotificationService:
    return ActionableNotificationService()
