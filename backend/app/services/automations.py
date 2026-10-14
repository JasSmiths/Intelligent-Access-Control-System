import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    AuditLog,
    AutomationRule,
    AutomationRun,
    GateCommandRecord,
    MovementSagaRecord,
    NotificationRule,
    User,
    VisitorPass,
)
from app.models.enums import AccessDecision, AccessDirection, GateCommandState
from app.services.access.authorization import (
    assert_current_recognition_authorization,
    recognition_deadline_for_event,
)
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.access_devices import get_access_device_service
from app.services.automation_actions import (
    AutomationActionExecutor,
)
from app.services.automation_authorization import (
    evaluate_automation_condition,
    preflight_current_action,
)
from app.services.automation_dispatch import AutomationDispatcher
from app.services.automation_execution import AutomationActionWait, AutomationRunStore
from app.services.automation_intake import (
    public_automation_context,
    reserve_occurrence,
    reserve_trigger,
)
from app.services.automation_policy import (
    HARDWARE_ACTION_TYPES,
    HARDWARE_DENIAL_DETAILS,
    REQUESTER_CONFIRMATION_REQUIRED,
    hardware_action_denial,
)
from app.services.automation_rules import AutomationRuleService
from app.services.automation_serialization import serialize_rule, serialize_run
from app.services.automation_time_intake import AutomationTimeIntake
from app.services.automation_webhooks import AutomationWebhookIntake
from app.services.event_bus import event_bus
from app.services.gate_commands import get_gate_command_coordinator
from app.services.maintenance import is_maintenance_mode_active
from app.services.maintenance import set_mode as set_maintenance_mode
from app.services.notification_rules import set_automation_activation
from app.services.telemetry import (
    TELEMETRY_CATEGORY_AUTOMATION,
    audit_log_event_payload,
    current_trace_id,
    payload_shape,
    publish_audit_log_hint,
    write_audit_log,
)
from app.services.type_helpers import as_dict
from app.services.visitor_passes import serialize_visitor_pass
from app.services.workflow_dispatch_ports import PreparedAutomationAction
from app.services.workflows.action_definitions import (
    ACTION_BY_TYPE,
    validate_action,
)
from app.services.workflows.automation_definition import (
    ACTION_CATALOG,
    CONDITION_CATALOG,
    TRIGGER_CATALOG,
    TRIGGER_SCOPES,
    VARIABLES,
    AutomationContext,
    build_context_variables,
    captured_automation_context,
    context_missing_references,
    normalize_rule_payload,
    restored_automation_context,
)
from app.services.workflows.context import (
    render_template,
    workflow_action_result,
)
from app.services.workflows.execution_contracts import (
    AutomationActionResult,
    AutomationExecution,
    checked_action,
    checked_execution,
)

logger = get_logger(__name__)

SCHEDULER_INTERVAL_SECONDS = 15
WEBHOOK_SIGNATURE_HEADER = "X-IACS-Webhook-Signature"
WEBHOOK_TIMESTAMP_HEADER = "X-IACS-Webhook-Timestamp"
WEBHOOK_NONCE_HEADER = "X-IACS-Webhook-Nonce"


class AutomationService:
    def __init__(self) -> None:
        self._started = False
        self._scheduler_task: asyncio.Task[None] | None = None
        self.run_store = AutomationRunStore()
        self.rules = AutomationRuleService(
            audit=lambda session, **kwargs: write_audit_log(session, **kwargs)
        )
        self.action_executor = AutomationActionExecutor(
            gates=lambda: get_gate_command_coordinator(),
            devices=lambda: get_access_device_service(),
            maintenance_active=lambda: is_maintenance_mode_active(),
            maintenance_change=lambda enabled, **kwargs: set_maintenance_mode(enabled, **kwargs),
            notification_activation=lambda session, **kwargs: set_automation_activation(
                session, **kwargs
            ),
            authorize_hardware=self.authorize_hardware_dispatch,
        )
        self.webhooks = AutomationWebhookIntake(
            sessions=lambda: AsyncSessionLocal(),
            reserve_trigger=lambda session, trigger_key, payload, **kwargs: reserve_trigger(
                session, trigger_key, payload, **kwargs
            ),
        )
        self.time_intake = AutomationTimeIntake(
            sessions=lambda: AsyncSessionLocal(),
            reserve_occurrence=lambda session, rule, context, **kwargs: reserve_occurrence(
                session, rule, context, **kwargs
            ),
        )
        self.dispatcher = AutomationDispatcher(self, self.run_store)

    async def start(self) -> None:
        if self._started:
            return
        self.dispatcher.start()
        self._scheduler_task = asyncio.create_task(
            self._run_scheduler(), name="automation-scheduler"
        )
        self._started = True
        logger.info("automation_engine_started")

    async def stop(self) -> None:
        if not self._started:
            return
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        self._scheduler_task = None
        await self.dispatcher.stop()
        self._started = False
        logger.info("automation_engine_stopped")

    async def catalog(self) -> dict[str, Any]:
        garage_devices = await get_access_device_service().list_devices(
            kind="garage_door", enabled_only=True
        )
        return {
            "triggers": TRIGGER_CATALOG,
            "conditions": CONDITION_CATALOG,
            "actions": ACTION_CATALOG,
            "variables": variable_groups(),
            "notification_rules": await self._notification_rule_catalog(),
            "garage_doors": [
                {
                    "entity_id": device.key,
                    "name": device.name,
                    "schedule_id": device.schedule_id,
                }
                for device in garage_devices
            ],
            "mock_context": build_context_variables(
                AutomationContext(
                    trigger_key="vehicle.known_plate",
                    subject="Steph arrived at the gate",
                    trigger_payload={"sample": True},
                    facts={
                        "first_name": "Steph",
                        "last_name": "Smith",
                        "display_name": "Steph Smith",
                        "person_id": "person-1",
                        "vehicle_id": "vehicle-1",
                        "registration_number": "STEPH26",
                        "vehicle_name": "2026 Tesla Model Y Dual Motor Long Range",
                        "vehicle_make": "Tesla",
                        "vehicle_colour": "Pearl white",
                        "occurred_at": datetime.now(tz=UTC).isoformat(),
                        "message": "Steph arrived in the Tesla.",
                    },
                    scopes={"person", "vehicle", "event", "time"},
                )
            ),
        }

    async def list_rules(self, session: AsyncSession) -> list[AutomationRule]:
        return await self.rules.list_rules(session)

    async def create_rule(
        self,
        session: AsyncSession,
        *,
        name: str,
        description: str | None = None,
        triggers: Any,
        conditions: Any,
        actions: Any,
        is_active: bool = True,
        created_by: User | None = None,
    ) -> AutomationRule:
        return await self.rules.create_rule(
            session,
            name=name,
            description=description,
            triggers=triggers,
            conditions=conditions,
            actions=actions,
            is_active=is_active,
            created_by=created_by,
        )

    async def update_rule(
        self,
        session: AsyncSession,
        rule: AutomationRule,
        *,
        actor: User | None = None,
        name: str | None = None,
        description: str | None = None,
        triggers: Any = None,
        conditions: Any = None,
        actions: Any = None,
        is_active: bool | None = None,
    ) -> AutomationRule:
        return await self.rules.update_rule(
            session,
            rule,
            actor=actor,
            name=name,
            description=description,
            triggers=triggers,
            conditions=conditions,
            actions=actions,
            is_active=is_active,
        )

    async def delete_rule(
        self, session: AsyncSession, rule: AutomationRule, *, actor: User | None = None
    ) -> None:
        await self.rules.delete_rule(session, rule, actor=actor)

    async def dry_run_rule(
        self,
        rule: AutomationRule | dict[str, Any],
        *,
        trigger_key: str | None = None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = (
            serialize_rule(rule)
            if isinstance(rule, AutomationRule)
            else normalize_rule_payload(rule)
        )
        if not payload["trigger_keys"]:
            return {
                "rule": payload,
                "context": {},
                "condition_results": [],
                "action_previews": [],
                "dry_run": True,
                "executed": False,
                "would_run": False,
                "error": "At least one automation trigger is required for a dry-run.",
                "message": "Dry-run preview only. No automation actions were executed.",
            }
        key = trigger_key or payload["trigger_keys"][0]
        context = await self.context_for_trigger(key, trigger_payload or {"dry_run": True})
        conditions = []
        async with AsyncSessionLocal() as session:
            for condition in payload["conditions"]:
                conditions.append(await self._evaluate_condition(session, condition, context))
        conditions_passed = all(item.get("passed") for item in conditions) if conditions else True
        action_previews = []
        for action in payload["actions"]:
            missing = context_missing_references(context, action)
            denial = hardware_action_denial(str(action["type"]), context.provenance)
            action_previews.append(
                {
                    "id": action["id"],
                    "type": action["type"],
                    "dry_run": True,
                    "executed": False,
                    "would_execute": conditions_passed and not missing and denial is None,
                    "skipped": bool(missing or denial),
                    "missing_variables": missing,
                    "rendered_reason": render_with_context(
                        action.get("reason_template") or "", context
                    ),
                    **(
                        {
                            "reason": denial,
                            "reason_code": denial,
                            "detail": HARDWARE_DENIAL_DETAILS[denial],
                            "requires_confirmation": denial == REQUESTER_CONFIRMATION_REQUIRED,
                        }
                        if denial
                        else {}
                    ),
                }
            )
        return {
            "rule": payload,
            "context": public_automation_context(context),
            "condition_results": conditions,
            "action_previews": action_previews,
            "dry_run": True,
            "executed": False,
            "would_run": conditions_passed,
            "message": "Dry-run preview only. Conditions were evaluated and actions were not executed.",
        }

    async def fire_trigger(
        self,
        trigger_key: str,
        payload: dict[str, Any] | None = None,
        *,
        actor: str = "Automation Engine",
        source: str = "event_bus",
        origin_id: str | None = None,
    ) -> list[dict[str, Any]]:
        origin_id = origin_id or str(uuid.uuid4())
        audit_payload = None
        session = AsyncSessionLocal()
        try:
            identities = await reserve_trigger(
                session,
                trigger_key,
                payload or {},
                origin_kind=source,
                origin_id=origin_id,
                actor=actor,
                source=source,
                trace_id=current_trace_id(),
            )
            if not identities:
                audit = await write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_AUTOMATION,
                    action="automation_trigger.unmatched",
                    actor=actor,
                    target_entity="AutomationTrigger",
                    target_id=trigger_key,
                    target_label=trigger_key.replace("_", " ").replace(".", " ").title(),
                    outcome="skipped",
                    level="info",
                    trace_id=current_trace_id(),
                    metadata={
                        "reason_code": "no_matching_automation",
                        "trigger_key": trigger_key,
                        "source": source,
                        "payload_shape": payload_shape(payload or {}),
                    },
                )
            await session.commit()
            if not identities:
                audit_payload = audit_log_event_payload(audit)
        finally:
            await drain_owned(session.close())
        if audit_payload is not None:
            await publish_audit_log_hint(audit_payload)
        results = []
        self.dispatcher.wake()
        for identity in identities:
            await self.dispatcher.run_once(identity)
            results.append(await self.run_response(identity))
        if trigger_key == "webhook.received" and not results:
            unrecognized_payload = {**(payload or {}), "reason": "no_matching_automation"}
            results.extend(
                await self.fire_trigger(
                    "webhook.unrecognized",
                    unrecognized_payload,
                    actor=actor,
                    source=source,
                    origin_id=origin_id,
                )
            )
        return results

    async def execute_rule(
        self,
        rule_id: str,
        *,
        trigger_key: str,
        trigger_payload: dict[str, Any],
        context: AutomationContext | None = None,
        actor: str = "Automation Engine",
        source: str = "automation",
        origin_id: str | None = None,
    ) -> dict[str, Any]:
        context = context or await self.context_for_trigger(trigger_key, trigger_payload)
        async with AsyncSessionLocal() as session:
            rule = await session.scalar(
                select(AutomationRule)
                .where(AutomationRule.id == uuid.UUID(str(rule_id)))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if rule is None or not rule.is_active:
                return {"executed": False, "status": "skipped", "reason": "rule_not_active"}
            identity = await reserve_occurrence(
                session,
                rule,
                context,
                origin_kind=source,
                origin_id=origin_id or str(uuid.uuid4()),
                actor=actor,
                source=source,
                trace_id=current_trace_id(),
            )
            await session.commit()
        self.dispatcher.wake()
        await self.dispatcher.run_once(identity)
        return await self.run_response(identity)

    async def run_response(self, identity: uuid.UUID) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            run = await session.get(AutomationRun, identity)
            if run is None:
                raise LookupError("Automation run not found.")
            payload = serialize_run(run)
        return {
            "executed": payload["status"] == "success",
            "status": payload["status"],
            "run": payload,
        }

    async def _preflight(
        self,
        session: AsyncSession,
        run: AutomationRun,
        item: dict[str, Any],
        *,
        now: datetime,
        rule: AutomationRule | None,
        deadlines: dict[str, Any] | None = None,
    ) -> dict[str, Any] | AutomationActionWait | None:
        action = item["action"]

        def skip(reason: str, *, review: bool = False, **extra: Any) -> dict[str, Any]:
            return workflow_action_result(
                action,
                "skipped",
                reason=reason,
                reason_code=reason,
                command_sent=False,
                requires_review=review,
                **extra,
            )

        authority = await preflight_current_action(
            session, run, item, rule=rule,
            maintenance_active=is_maintenance_mode_active,
            evaluate_condition=self._evaluate_condition,
        )
        if authority.condition_results is not None:
            run.condition_results = authority.condition_results
        if authority.denial is not None:
            return authority.denial
        context = restored_automation_context(run.context)
        if action["type"] in HARDWARE_ACTION_TYPES:
            if run.trigger_key == "visitor_pass.used":
                return skip("visitor_already_admitted")
            if item.get("preparation_error"):
                return skip(
                    "target_plan_unavailable", review=True, detail=item["preparation_error"]
                )
            if run.trigger_key.startswith("time."):
                deadline = automation_hardware_deadline(run)
                if deadlines is not None:
                    deadlines["expires_at"] = deadline
                if now > deadline:
                    return skip("scheduled_hardware_expired")
            if run.trigger_key.startswith("vehicle.") or run.trigger_key in {
                "visitor_pass.used",
                "visitor_pass.detected",
            }:
                try:
                    event = await session.get(
                        AccessEvent,
                        uuid.UUID(str(context.provenance.event_id)),
                        populate_existing=True,
                    )
                    if event is None:
                        return skip(
                            "recognition_authorization_changed",
                            detail="Recognition event is no longer available.",
                        )
                    deadline = await recognition_deadline_for_event(session, event)
                    if now > deadline:
                        return skip("recognition_hardware_expired", review=True)
                    checkpoint = await assert_current_recognition_authorization(
                        session,
                        event_id=context.provenance.event_id,
                        allow_vehicle_schedule_override=run.trigger_key
                        == "vehicle.outside_schedule",
                        now=now,
                    )
                    if deadlines is not None:
                        deadlines["expires_at"] = checkpoint.expires_at
                    if (
                        action["type"] == "gate.open"
                        and item.get("automatic_entry_policy") is not True
                    ):
                        return skip("recognition_entry_policy_not_captured", review=True)
                    reason = await self._primary_admission_wait_reason(session, event)
                    if reason:
                        return AutomationActionWait(
                            reason,
                            min(now + timedelta(seconds=5), deadline + timedelta(microseconds=1)),
                            deadline,
                        )
                except ValueError as exc:
                    return skip("recognition_authorization_changed", detail=str(exc))
        return None

    async def _primary_admission_wait_reason(
        self, session: AsyncSession, event: AccessEvent
    ) -> str | None:
        """Read the primary admission owner's truth; never claim or alter it.

        A rule's operation remains independent. In particular its denial or
        configuration cannot poison the primary admission's idempotency key.
        No core lock is taken while this caller holds rule/run locks.
        """
        if event.decision != AccessDecision.GRANTED or event.direction != AccessDirection.ENTRY:
            return None
        sagas = list(
            (
                await session.scalars(
                    select(MovementSagaRecord)
                    .where(MovementSagaRecord.access_event_id == event.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if len(sagas) != 1:
            return "primary_admission_not_settled"
        saga = sagas[0]
        if saga.admission_status not in {"verified", "denied"} or saga.reconciliation_required:
            return "primary_admission_not_settled"
        parent = await session.scalar(
            select(GateCommandRecord)
            .where(
                GateCommandRecord.movement_saga_id == saga.id,
                GateCommandRecord.access_event_id == event.id,
                GateCommandRecord.idempotency_key == f"gate-command:open:default:event:{event.id}",
            )
            .execution_options(populate_existing=True)
        )
        if (
            parent is None
            or parent.state
            not in {
                GateCommandState.ACCEPTED,
                GateCommandState.REJECTED,
                GateCommandState.FAILED,
                GateCommandState.RECONCILED,
            }
            or parent.requires_reconciliation
            or parent.completed_at is None
            or (parent.command_metadata or {}).get("intent_id")
            != str(uuid.uuid5(event.id, "automatic-gate-open"))
        ):
            return "primary_gate_command_not_settled"
        try:
            projection = await AccessDeviceCommandJournal().gate_command_projection(session, parent)
        except (KeyError, TypeError, ValueError):
            # Missing historical/corrupt receipt structure is not proof that
            # the primary operation settled. Expiry will leave an explicit skip.
            return "primary_gate_targets_not_settled"
        if projection is None or projection["requires_reconciliation"]:
            return "primary_gate_targets_not_settled"
        return None

    async def prepare_action_dispatch(
        self, run: AutomationRun, token: uuid.UUID, index: int
    ) -> PreparedAutomationAction | AutomationActionWait | None:
        async with AsyncSessionLocal() as session:
            rule = (
                await session.scalar(
                    select(AutomationRule)
                    .where(AutomationRule.id == run.rule_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if run.rule_id
                else None
            )
            current, now = await self.run_store.owned(session, run.id, token)
            item = current.action_plan[index]
            try:
                checked_action(item.get("action"))
                validate_action(item["action"])
            except (TypeError, ValueError):
                current.review_reason = "unsupported_action_configuration"
                await self.run_store.finish_review_in_session(
                    session, current.id, token, "unsupported_action_configuration"
                )
                await self._account_completion(session, current, rule)
                await session.commit()
                return None
            deadlines: dict[str, Any] = {}
            denial = await self._preflight(
                session, current, item, now=now, rule=rule, deadlines=deadlines
            )
            if isinstance(denial, AutomationActionWait):
                if await self.run_store.defer_action(session, run.id, token, index, denial):
                    await session.commit()
                    return denial
                # The observation can expire while the current authority was
                # checked. Finish the pending action without ever attempting it.
                denial = workflow_action_result(
                    item["action"],
                    "skipped",
                    reason="recognition_hardware_expired",
                    reason_code="recognition_hardware_expired",
                    command_sent=False,
                    requires_review=True,
                )
            action, context = item["action"], restored_automation_context(current.context)
            if denial is not None or (
                (definition := ACTION_BY_TYPE.get(action["type"])) is not None
                and definition.execution == "transactional"
            ):
                outcome = (
                    denial
                    if denial is not None
                    else await self._execute_action(
                        session, action, context, rule=rule,
                        # Identity from the currently owned pending row. This
                        # does not assert that an external attempt committed.
                        execution=checked_execution({
                            "run_id": str(current.id), "claim_token": str(token),
                            "index": index, "operation_id": item["operation_id"],
                            "idempotency_key": item["idempotency_key"],
                        }),
                    )
                )
                if outcome.get("requires_review"):
                    current.review_reason = outcome.get("reason") or "preflight_requires_review"
                await session.flush()
                await self.run_store.complete_local_action(
                    session,
                    run.id,
                    token,
                    index,
                    outcome,
                    state="failed"
                    if outcome["status"] == "failed"
                    else "skipped"
                    if outcome["status"] == "skipped"
                    else "succeeded",
                )
                current, _ = await self.run_store.owned(session, run.id, token)
                if not any(
                    part["state"] in {"pending", "attempting"} for part in current.action_plan
                ):
                    await self.run_store.finish_in_session(session, run.id, token)
                    await self._account_completion(session, current, rule)
                elif denial is None and rule is not None:
                    audit = await write_audit_log(
                        session,
                        category=TELEMETRY_CATEGORY_AUTOMATION,
                        action="automation_rule.action_success",
                        actor=current.actor,
                        target_entity="AutomationRule",
                        target_id=current.rule_id,
                        target_label=rule.name,
                        metadata={"run_id": str(run.id), "action_results": [outcome]},
                    )
                    audit.id = uuid.uuid5(run.id, f"action-audit:{index}")
                await session.commit()
                return None
            await session.flush()
            attempted = await self.run_store.begin_action(session, run.id, token, index)
            await session.commit()
        # The durable plan also contains action/state/timestamps. Pass only the
        # existing canonical execution contract; target plans and deadlines are
        # retained directly, without creating a second identity namespace.
        execution = {
            key: value for key, value in {
                **attempted, **deadlines, "run_id": str(run.id), "claim_token": str(token)
            }.items() if key in AutomationExecution.__annotations__
        }
        if rule is None:
            raise LookupError("A prepared automation action needs its current rule.")
        return PreparedAutomationAction(
            checked_action(action), context, rule, checked_execution(execution)
        )

    async def dispatch_prepared_action(
        self, prepared: PreparedAutomationAction
    ) -> AutomationActionResult:
        # Some integration/domain adapters need an explicit transaction participant;
        # it is not opened until after the durable attempted checkpoint committed.
        session = AsyncSessionLocal()
        try:
            outcome = await self._execute_action(
                session,
                dict(prepared.action),
                prepared.context,
                rule=prepared.rule,
                execution=prepared.execution,
            )
            return AutomationActionResult.from_payload(outcome, action=prepared.action)
        finally:
            await drain_owned(session.close())

    async def authorize_hardware_dispatch(
        self, session: AsyncSession, execution: AutomationExecution
    ) -> None:
        identity = uuid.UUID(execution["run_id"])
        # P04 invokes this after target/parent locks. No other path holds a rule
        # lock while taking a target lock, so this does not invert dispatch order.
        snapshot = await session.get(AutomationRun, identity)
        rule = (
            await session.scalar(
                select(AutomationRule)
                .where(AutomationRule.id == snapshot.rule_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if snapshot and snapshot.rule_id
            else None
        )
        current, now, item = await self.run_store.attempting(session, execution)
        denial = await self._preflight(session, current, item, now=now, rule=rule)
        if isinstance(denial, AutomationActionWait):
            raise ValueError(denial.reason)  # noqa: TRY004 - command owners treat policy denial as definitely not sent.
        if denial:
            raise ValueError(denial.get("detail") or denial["reason"])

    async def account_completed_run(self, identity: uuid.UUID) -> None:
        async with AsyncSessionLocal() as session:
            snapshot = await session.get(AutomationRun, identity)
            if snapshot is None or snapshot.status in {"queued", "processing"}:
                return
            rule = (
                await session.scalar(
                    select(AutomationRule)
                    .where(AutomationRule.id == snapshot.rule_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if snapshot.rule_id
                else None
            )
            current = await session.scalar(
                select(AutomationRun)
                .where(AutomationRun.id == identity)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            changed = await self._account_completion(session, current, rule)
            if rule is not None:
                # UPDATE expires the server-generated updated_at even when
                # expire_on_commit=False. Load it explicitly before synchronous
                # serialization; no ORM access may initiate implicit async I/O.
                await session.refresh(rule)
            run_payload = serialize_run(current)
            rule_payload = serialize_rule(rule) if rule else None
            await session.commit()
        if changed or current.recovery_version == 1:
            try:
                await event_bus.publish(
                    f"automation.run.{run_payload['status']}",
                    {"run": run_payload, "rule": rule_payload},
                )
            except Exception:
                logger.exception("automation_completion_publish_failed")

    async def _account_completion(
        self, session: AsyncSession, run: AutomationRun, rule: AutomationRule | None
    ) -> bool:
        if run.recovery_version != 1 or run.status in {"queued", "processing"}:
            return False
        audit_id = uuid.uuid5(run.id, "completion-audit")
        if await session.get(AuditLog, audit_id) is not None:
            return False
        error = next(
            (
                str(item.get("error") or item.get("detail") or item.get("reason"))
                for item in run.action_results
                if item.get("status") in {"failed", "unknown"}
            ),
            None,
        )
        run.error = run.error or error
        if rule:
            rule.last_fired_at = run.finished_at
            rule.run_count = int(rule.run_count or 0) + 1
            rule.last_run_status, rule.last_error = run.status, run.error
            # Exhaustion belongs to next_run_at=None. Only explicit disable
            # revokes pending effects; completion must not cancel its own handoff.
        audit = await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_AUTOMATION,
            action=f"automation_rule.{run.status}",
            actor=run.actor,
            target_entity="AutomationRule",
            target_id=run.rule_id,
            target_label=rule.name if rule else "Deleted automation rule",
            trace_id=run.trace_id,
            metadata={
                "run_id": str(run.id),
                "trigger_key": run.trigger_key,
                "condition_results": run.condition_results,
                "action_results": run.action_results,
                "review_reason": run.review_reason,
            },
            outcome="failed"
            if run.status in {"failed", "review_required"}
            else "skipped"
            if run.status == "skipped"
            else "success",
            level="error" if run.status in {"failed", "review_required"} else "info",
        )
        audit.id = audit_id
        await session.flush()
        return True

    async def recover_completion_audits(self) -> None:
        async with AsyncSessionLocal() as session:
            accounted = (
                select(AuditLog.id)
                .where(
                    AuditLog.category == TELEMETRY_CATEGORY_AUTOMATION,
                    AuditLog.action.in_(
                        [
                            "automation_rule.success",
                            "automation_rule.skipped",
                            "automation_rule.failed",
                            "automation_rule.review_required",
                        ]
                    ),
                    AuditLog.metadata_["run_id"].astext == cast(AutomationRun.id, String),
                )
                .exists()
            )
            identities = (
                await session.scalars(
                    select(AutomationRun.id)
                    .where(
                        AutomationRun.recovery_version == 1,
                        AutomationRun.status.not_in(["queued", "processing"]),
                        ~accounted,
                    )
                    .order_by(AutomationRun.finished_at, AutomationRun.id)
                    .limit(20)
                )
            ).all()
        for identity in identities:
            await self.account_completed_run(identity)

    async def _process_due_rules(self) -> None:
        await self._reserve_due_rules()
        self.dispatcher.wake()

    async def _reserve_due_rules(self) -> int:
        return await self.time_intake.reserve_due()

    async def _evaluate_rule_conditions(
        self,
        session: AsyncSession,
        conditions: list[dict[str, Any]],
        context: AutomationContext,
        *,
        trace: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        for condition in conditions:
            started_at = datetime.now(tz=UTC)
            result = await self._evaluate_condition(session, condition, context)
            results.append(result)
            trace.record_span(
                "Automation condition evaluated",
                started_at=started_at,
                ended_at=datetime.now(tz=UTC),
                attributes={
                    "condition_id": condition.get("id"),
                    "condition_type": condition.get("type"),
                    "reason_code": automation_condition_reason_code(result, condition),
                },
                input_payload={"condition": condition},
                output_payload=result,
                status="ok" if result.get("passed") else "blocked",
            )
            if not result.get("passed"):
                return "skipped", results
        return "success", results

    async def context_for_trigger(
        self, trigger_key: str, payload: dict[str, Any]
    ) -> AutomationContext:
        if trigger_key.startswith("visitor_pass."):
            payload = await self._fresh_visitor_pass_payload(payload)
        return captured_automation_context(trigger_key, payload)

    async def handle_webhook(
        self,
        webhook_key: str,
        payload: dict[str, Any],
        *,
        source_ip: str,
        raw_body: bytes = b"",
        signature: str | None = None,
        signature_timestamp: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        receipt = await self.webhooks.reserve(
            webhook_key,
            payload,
            source_ip=source_ip,
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            nonce=nonce,
        )
        self.dispatcher.wake()
        runs = []
        for identity in receipt.run_ids:
            await self.dispatcher.run_once(identity)
            runs.append(await self.run_response(identity))
        return {
            "accepted": True,
            "webhook_key": receipt.webhook_key,
            "new_sender": receipt.new_sender,
            "hmac_verified": receipt.hmac_verified,
            "runs": runs,
        }

    async def _run_scheduler(self) -> None:
        while True:
            try:
                await self._process_due_rules()
            except Exception:
                logger.exception("automation_scheduler_tick_failed")
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)

    async def _evaluate_condition(
        self,
        session: AsyncSession,
        condition: dict[str, Any],
        context: AutomationContext,
    ) -> dict[str, Any]:
        return await evaluate_automation_condition(session, condition, context)

    async def _execute_action(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
        execution: AutomationExecution | None = None,
    ) -> dict[str, Any]:
        return (
            await self.action_executor.execute(
                session, action, context, rule=rule, execution=execution
            )
        ).as_payload()

    async def _fresh_visitor_pass_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        visitor_pass = as_dict(payload.get("visitor_pass")) or payload
        pass_id = str(visitor_pass.get("id") or payload.get("visitor_pass_id") or "").strip()
        if not pass_id:
            return payload
        try:
            parsed_id = uuid.UUID(pass_id)
        except ValueError:
            return payload
        async with AsyncSessionLocal() as session:
            row = await session.get(VisitorPass, parsed_id)
            if not row:
                return payload
            return {**payload, "visitor_pass": serialize_visitor_pass(row)}

    async def _notification_rule_catalog(self) -> list[dict[str, str]]:
        async with AsyncSessionLocal() as session:
            rules = (
                await session.scalars(select(NotificationRule).order_by(NotificationRule.name))
            ).all()
        return [
            {"id": str(rule.id), "name": rule.name, "trigger_event": rule.trigger_event}
            for rule in rules
        ]


def automation_skip_reason(
    status: str,
    condition_results: list[dict[str, Any]],
    action_results: list[dict[str, Any]],
    error: str | None = None,
) -> str:
    if error:
        return error
    if status != "skipped":
        return ""
    for result in condition_results:
        if result.get("passed") is False:
            reason = automation_result_reason(result)
            if reason:
                return reason
    for result in action_results:
        if str(result.get("status") or "").lower() in {"skipped", "failed"}:
            reason = automation_result_reason(result)
            if reason:
                return reason
    return "Automation run was skipped."


def automation_condition_reason_code(
    result: dict[str, Any],
    condition: dict[str, Any] | None = None,
) -> str:
    explicit = str(result.get("reason_code") or "").strip()
    if explicit:
        return _reason_code(explicit)
    if result.get("passed") is True:
        return "condition_passed"
    condition_type = str((condition or {}).get("type") or "").strip()
    return f"{_reason_code(condition_type)}_failed" if condition_type else "condition_failed"


def _reason_code(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized[:120] or "unknown"


def automation_result_reason(result: dict[str, Any]) -> str:
    for key in ("disabled_reason", "reason", "error", "detail", "message", "description"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def variable_groups() -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    labels = {
        "person": "Person",
        "vehicle": "Vehicles",
        "visitor_pass": "Visitor Pass",
        "maintenance": "Maintenance Mode",
        "webhook": "Webhook",
        "ai": "AI Agent",
        "time": "Time & Date",
        "event": "Event",
    }
    trigger_types_by_scope: dict[str, list[str]] = {}
    for trigger_type, scopes in TRIGGER_SCOPES.items():
        for scope in scopes:
            trigger_types_by_scope.setdefault(scope, []).append(trigger_type)
    for variable in VARIABLES:
        grouped.setdefault(variable.scope, []).append(
            {
                "name": variable.name,
                "token": variable.token,
                "label": variable.label,
                "scope": variable.scope,
                "trigger_types": sorted(trigger_types_by_scope.get(variable.scope, [])),
            }
        )
    return [
        {"group": labels.get(scope, scope.title()), "scope": scope, "items": grouped[scope]}
        for scope in labels
        if scope in grouped
    ]


def render_with_context(template: str, context: AutomationContext) -> str:
    return render_template(template, context.variables)


def parse_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


_automation_service = AutomationService()


def get_automation_service() -> AutomationService:
    return _automation_service


def automation_hardware_deadline(run: AutomationRun) -> datetime:
    value = datetime.fromisoformat(run.context["dispatch"]["source_time"])
    if value.tzinfo is None:
        raise ValueError("Hardware source time must include a timezone.")
    return value + timedelta(seconds=60)
