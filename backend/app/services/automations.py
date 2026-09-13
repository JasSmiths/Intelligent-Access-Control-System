import asyncio
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import ChatMessageInput, complete_with_provider_options, get_llm_provider
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    AccessEvent,
    AuditLog,
    AutomationRule,
    AutomationRun,
    AutomationWebhookNonce,
    AutomationWebhookSender,
    GateCommandRecord,
    MovementSagaRecord,
    NotificationRule,
    User,
    VisitorPass,
)
from app.models.enums import AccessDecision, AccessDirection, GateCommandState
from app.services.mutation_context import MutationError, require_active_admin
from app.services.access_devices import get_access_device_service
from app.services.access.authorization import assert_current_recognition_authorization, recognition_deadline_for_event
from app.services.access_device_commands import AccessDeviceCommandJournal
from app.services.automation_authorization import automation_rule_fingerprint, current_rule_denial, evaluate_current_condition
from app.services.automation_dispatch import AutomationDispatcher
from app.services.automation_intake import public_automation_context, reserve_occurrence, reserve_trigger
from app.services.workflows.automation_definition import (
    ACTION_CATALOG, CONDITION_CATALOG, TIME_TRIGGER_KEYS, TRIGGER_CATALOG, TRIGGER_SCOPES,
    VARIABLES, WEBHOOK_HMAC_WINDOW_SECONDS, WEBHOOK_RATE_LIMIT_PER_MINUTE,
    AutomationContext, automation_triggers_for_origin, bool_config, build_context_variables, captured_automation_context, context_missing_references,
    ensure_aware, generate_automation_webhook_key, is_high_entropy_webhook_key,
    normalize_actions, normalize_conditions, normalize_rule_payload, normalize_triggers,
    optional_text, parse_datetime, restored_automation_context, safe_int, trigger_keys_for_triggers,
)
from app.services.automation_execution import AutomationActionWait, AutomationRunStore
from app.services.automation_integration_actions import (
    execute_integration_action,
    integration_action_catalog,
    integration_action_for_type,
)
from app.services.automation_policy import (
    HARDWARE_ACTION_TYPES,
    HARDWARE_DENIAL_DETAILS,
    REQUESTER_CONFIRMATION_REQUIRED,
    hardware_action_denial,
    hardware_configuration_error,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_commands import GateCommandIntent, get_gate_command_coordinator
from app.services.maintenance import is_maintenance_mode_active, set_mode as set_maintenance_mode
from app.services.notification_rules import set_automation_activation
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_AUTOMATION,
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    current_trace_id,
    emit_audit_log,
    payload_shape,
    sanitize_payload,
    telemetry,
    write_audit_log,
)
from app.services.type_helpers import as_dict
from app.services.visitor_passes import serialize_visitor_pass

from app.services.workflows.context import (
    normalize_string_list,
    render_template,
    workflow_action_result,
)

logger = get_logger(__name__)

SCHEDULER_INTERVAL_SECONDS = 15
MAX_DUE_RULES_PER_TICK = 25
AI_SCHEDULE_CONFIDENCE_THRESHOLD = 0.65
WEBHOOK_RATE_WINDOW_SECONDS = 60
WEBHOOK_SIGNATURE_HEADER = "X-IACS-Webhook-Signature"
WEBHOOK_TIMESTAMP_HEADER = "X-IACS-Webhook-Timestamp"
WEBHOOK_NONCE_HEADER = "X-IACS-Webhook-Nonce"


class AutomationError(RuntimeError):
    """Raised when an automation rule or action cannot be evaluated safely."""


class AutomationService:
    def __init__(self) -> None:
        self._started = False
        self._scheduler_task: asyncio.Task | None = None
        self.run_store = AutomationRunStore()
        self.dispatcher = AutomationDispatcher(self, self.run_store)

    async def start(self) -> None:
        if self._started:
            return
        event_bus.subscribe(self._handle_realtime_event)
        self.dispatcher.start()
        self._scheduler_task = asyncio.create_task(self._run_scheduler(), name="automation-scheduler")
        self._started = True
        logger.info("automation_engine_started")

    async def stop(self) -> None:
        if not self._started:
            return
        event_bus.unsubscribe(self._handle_realtime_event)
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
        garage_devices = await get_access_device_service().list_devices(kind="garage_door", enabled_only=True)
        return {
            "triggers": TRIGGER_CATALOG,
            "conditions": CONDITION_CATALOG,
            "actions": ACTION_CATALOG + await integration_action_catalog(),
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
        return (
            await session.scalars(
                select(AutomationRule).order_by(AutomationRule.created_at.desc(), AutomationRule.name)
            )
        ).all()

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
        created_by = require_active_admin(created_by)
        name = validated_rule_name(name)
        normalized_triggers = normalize_triggers(triggers, generate_webhook_keys=True)
        normalized_actions = normalize_actions(actions)
        if error := hardware_configuration_error(
            (trigger["type"] for trigger in normalized_triggers),
            (action["type"] for action in normalized_actions),
        ):
            raise AutomationError(error)
        harden_webhook_triggers_for_actions(normalized_triggers, normalized_actions)
        if not normalized_triggers:
            raise AutomationError("At least one automation trigger is required.")
        if not normalized_actions:
            raise AutomationError("At least one automation action is required.")
        now = datetime.now(tz=UTC)
        rule = AutomationRule(
            name=name,
            description=(description or "").strip() or None,
            is_active=is_active,
            triggers=normalized_triggers,
            trigger_keys=trigger_keys_for_triggers(normalized_triggers),
            conditions=normalize_conditions(conditions),
            actions=normalized_actions,
            next_run_at=next_run_for_triggers(normalized_triggers, now=now),
            created_by_user_id=getattr(created_by, "id", None),
        )
        session.add(rule)
        await session.flush()
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="automation_rule.create",
            actor=actor_from_user(created_by),
            actor_user_id=getattr(created_by, "id", None),
            target_entity="AutomationRule",
            target_id=rule.id,
            target_label=rule.name,
            diff={"old": {}, "new": serialize_rule(rule)},
        )
        return rule

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
        actor = require_active_admin(actor)
        await session.refresh(rule, with_for_update=True)
        before = serialize_rule(rule)
        normalized_triggers = (
            normalize_triggers(triggers, generate_webhook_keys=True)
            if triggers is not None
            else normalize_triggers(rule.triggers)
        )
        normalized_actions = normalize_actions(actions) if actions is not None else normalize_actions(rule.actions)
        error = hardware_configuration_error(
            (trigger["type"] for trigger in normalized_triggers),
            (action["type"] for action in normalized_actions),
        )
        # Keep an existing unsafe row readable and allow disabling it without
        # rewriting history. New or edited trigger/action combinations still
        # require valid policy; re-enabling also checks the merged configuration.
        disabling_existing = is_active is False and triggers is None and actions is None
        if error and not disabling_existing:
            raise AutomationError(error)
        if name is not None:
            rule.name = validated_rule_name(name)
        if description is not None:
            rule.description = description.strip() or None
        harden_webhook_triggers_for_actions(normalized_triggers, normalized_actions)
        rule.triggers = normalized_triggers
        if triggers is not None:
            if not rule.triggers:
                raise AutomationError("At least one automation trigger is required.")
            rule.trigger_keys = trigger_keys_for_triggers(rule.triggers)
            rule.next_run_at = next_run_for_triggers(rule.triggers, now=datetime.now(tz=UTC), last_fired_at=rule.last_fired_at)
        if conditions is not None:
            rule.conditions = normalize_conditions(conditions)
        if actions is not None:
            rule.actions = normalized_actions
            if not rule.actions:
                raise AutomationError("At least one automation action is required.")
        if is_active is not None:
            rule.is_active = is_active
            if is_active:
                rule.next_run_at = next_run_for_triggers(
                    rule.triggers,
                    now=datetime.now(tz=UTC),
                    last_fired_at=rule.last_fired_at,
                )
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="automation_rule.update",
            actor=actor_from_user(actor),
            actor_user_id=getattr(actor, "id", None),
            target_entity="AutomationRule",
            target_id=rule.id,
            target_label=rule.name,
            diff=audit_diff(before, serialize_rule(rule)),
        )
        return rule

    async def delete_rule(self, session: AsyncSession, rule: AutomationRule, *, actor: User | None = None) -> None:
        actor = require_active_admin(actor)
        await session.refresh(rule, with_for_update=True)
        before = serialize_rule(rule)
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="automation_rule.delete",
            actor=actor_from_user(actor),
            actor_user_id=getattr(actor, "id", None),
            target_entity="AutomationRule",
            target_id=rule.id,
            target_label=rule.name,
            diff={"old": before, "new": {}},
        )
        await session.delete(rule)

    async def dry_run_rule(
        self,
        rule: AutomationRule | dict[str, Any],
        *,
        trigger_key: str | None = None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = serialize_rule(rule) if isinstance(rule, AutomationRule) else normalize_rule_payload(rule)
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
                    "rendered_reason": render_with_context(action.get("reason_template") or "", context),
                    **({"reason": denial, "reason_code": denial,
                        "detail": HARDWARE_DENIAL_DETAILS[denial],
                        "requires_confirmation": denial == REQUESTER_CONFIRMATION_REQUIRED}
                       if denial else {}),
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
        async with AsyncSessionLocal() as session:
            identities = await reserve_trigger(session, trigger_key, payload or {},
                origin_kind=source, origin_id=origin_id, actor=actor, source=source, trace_id=current_trace_id())
            await session.commit()
        results = []
        self.dispatcher.wake()
        for identity in identities:
            await self.dispatcher.run_once(identity)
            results.append(await self.run_response(identity))
        if not identities:
            emit_audit_log(
                category=TELEMETRY_CATEGORY_AUTOMATION,
                action="automation_trigger.unmatched", actor=actor,
                target_entity="AutomationTrigger", target_id=trigger_key,
                target_label=trigger_key.replace("_", " ").replace(".", " ").title(),
                outcome="skipped", level="info", trace_id=current_trace_id(),
                metadata={"reason_code": "no_matching_automation", "trigger_key": trigger_key,
                          "source": source, "payload_shape": payload_shape(payload or {})},
            )
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
        self, rule_id: str, *, trigger_key: str, trigger_payload: dict[str, Any],
        context: AutomationContext | None = None, actor: str = "Automation Engine", source: str = "automation",
        origin_id: str | None = None,
    ) -> dict[str, Any]:
        context = context or await self.context_for_trigger(trigger_key, trigger_payload)
        async with AsyncSessionLocal() as session:
            rule = await session.scalar(select(AutomationRule).where(AutomationRule.id == uuid.UUID(str(rule_id)))
                                        .with_for_update().execution_options(populate_existing=True))
            if rule is None or not rule.is_active:
                return {"executed": False, "status": "skipped", "reason": "rule_not_active"}
            identity = await reserve_occurrence(session, rule, context, origin_kind=source,
                origin_id=origin_id or str(uuid.uuid4()), actor=actor, source=source, trace_id=current_trace_id())
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
        return {"executed": payload["status"] == "success", "status": payload["status"], "run": payload}

    async def _preflight(self, session: AsyncSession, run: AutomationRun, item: dict[str, Any], *,
                         now: datetime, rule: AutomationRule | None, deadlines: dict[str, Any] | None = None) -> dict[str, Any] | AutomationActionWait | None:
        action = item["action"]
        def skip(reason: str, *, review: bool = False, **extra):
            return workflow_action_result(action, "skipped", reason=reason, reason_code=reason,
                                          command_sent=False, requires_review=review, **extra)
        rule_denial = current_rule_denial(rule, run.context.get("rule_fingerprint"))
        if rule_denial:
            return skip("rule_not_active" if rule_denial == "rule_inactive" else "rule_changed_since_occurrence",
                        review=rule_denial == "rule_changed")
        context = restored_automation_context(run.context)
        denial = hardware_action_denial(action["type"], context.provenance)
        if denial:
            return skip(denial, detail=HARDWARE_DENIAL_DETAILS[denial],
                        requires_confirmation=denial == REQUESTER_CONFIRMATION_REQUIRED)
        missing = context_missing_references(context, action)
        if missing:
            return skip("context_missing", missing_variables=missing)
        if (await is_maintenance_mode_active() and action["type"] != "maintenance_mode.disable"
                and action_paused_by_maintenance_mode(action["type"])):
            return skip("maintenance_mode")
        conditions = [await self._evaluate_condition(session, condition, context)
                      for condition in normalize_conditions(rule.conditions)]
        run.condition_results = conditions
        if any(not result.get("passed") for result in conditions):
            return skip("condition_failed")
        if action["type"] in HARDWARE_ACTION_TYPES:
            if run.trigger_key == "visitor_pass.used":
                return skip("visitor_already_admitted")
            if item.get("preparation_error"):
                return skip("target_plan_unavailable", review=True, detail=item["preparation_error"])
            if run.trigger_key.startswith("time."):
                deadline = automation_hardware_deadline(run)
                if deadlines is not None:
                    deadlines["expires_at"] = deadline
                if now > deadline:
                    return skip("scheduled_hardware_expired")
            if run.trigger_key.startswith("vehicle.") or run.trigger_key in {"visitor_pass.used", "visitor_pass.detected"}:
                try:
                    event = await session.get(AccessEvent, uuid.UUID(str(context.provenance.event_id)), populate_existing=True)
                    if event is None:
                        return skip("recognition_authorization_changed", detail="Recognition event is no longer available.")
                    deadline = await recognition_deadline_for_event(session, event)
                    if now > deadline:
                        return skip("recognition_hardware_expired", review=True)
                    checkpoint = await assert_current_recognition_authorization(session, event_id=context.provenance.event_id,
                        allow_vehicle_schedule_override=run.trigger_key == "vehicle.outside_schedule", now=now)
                    if deadlines is not None:
                        deadlines["expires_at"] = checkpoint.expires_at
                    if action["type"] == "gate.open" and item.get("automatic_entry_policy") is not True:
                        return skip("recognition_entry_policy_not_captured", review=True)
                    reason = await self._primary_admission_wait_reason(session, event)
                    if reason:
                        return AutomationActionWait(reason, min(now + timedelta(seconds=5),
                            deadline + timedelta(microseconds=1)), deadline)
                except ValueError as exc:
                    return skip("recognition_authorization_changed", detail=str(exc))
        return None

    async def _primary_admission_wait_reason(self, session: AsyncSession, event: AccessEvent) -> str | None:
        """Read the primary admission owner's truth; never claim or alter it.

        A rule's operation remains independent. In particular its denial or
        configuration cannot poison the primary admission's idempotency key.
        No core lock is taken while this caller holds rule/run locks.
        """
        if event.decision != AccessDecision.GRANTED or event.direction != AccessDirection.ENTRY:
            return None
        sagas = list((await session.scalars(select(MovementSagaRecord).where(
            MovementSagaRecord.access_event_id == event.id).execution_options(populate_existing=True))).all())
        if len(sagas) != 1:
            return "primary_admission_not_settled"
        saga = sagas[0]
        if saga.admission_status not in {"verified", "denied"} or saga.reconciliation_required:
            return "primary_admission_not_settled"
        parent = await session.scalar(select(GateCommandRecord).where(
            GateCommandRecord.movement_saga_id == saga.id, GateCommandRecord.access_event_id == event.id,
            GateCommandRecord.idempotency_key == f"gate-command:open:default:event:{event.id}",
        ).execution_options(populate_existing=True))
        if (parent is None or parent.state not in {GateCommandState.ACCEPTED, GateCommandState.REJECTED,
                GateCommandState.FAILED, GateCommandState.RECONCILED} or parent.requires_reconciliation
                or parent.completed_at is None
                or (parent.command_metadata or {}).get("intent_id") != str(uuid.uuid5(event.id, "automatic-gate-open"))):
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

    async def prepare_action_dispatch(self, run: AutomationRun, token: uuid.UUID, index: int):
        async with AsyncSessionLocal() as session:
            rule = await session.scalar(select(AutomationRule).where(AutomationRule.id == run.rule_id)
                                        .with_for_update().execution_options(populate_existing=True)) if run.rule_id else None
            current, now = await self.run_store.owned(session, run.id, token)
            item = current.action_plan[index]
            deadlines = {}
            denial = await self._preflight(session, current, item, now=now, rule=rule, deadlines=deadlines)
            if isinstance(denial, AutomationActionWait):
                if await self.run_store.defer_action(session, run.id, token, index, denial):
                    await session.commit()
                    return denial
                # The observation can expire while the current authority was
                # checked. Finish the pending action without ever attempting it.
                denial = workflow_action_result(item["action"], "skipped", reason="recognition_hardware_expired",
                    reason_code="recognition_hardware_expired", command_sent=False, requires_review=True)
            action, context = item["action"], restored_automation_context(current.context)
            if denial is not None or action["type"] in {"notification.enable", "notification.disable", "integration.whatsapp.send_message"}:
                outcome = denial if denial is not None else await self._execute_action(session, action, context, rule=rule, execution={**item, "run_id": str(current.id), "rule_fingerprint": current.context["rule_fingerprint"]})
                if outcome.get("requires_review"):
                    current.review_reason = outcome.get("reason") or "preflight_requires_review"
                await session.flush()
                await self.run_store.complete_local_action(session, run.id, token, index, outcome,
                    state="failed" if outcome["status"] == "failed" else "skipped" if outcome["status"] == "skipped" else "succeeded")
                current, _ = await self.run_store.owned(session, run.id, token)
                if not any(part["state"] in {"pending", "attempting"} for part in current.action_plan):
                    await self.run_store.finish_in_session(session, run.id, token)
                    await self._account_completion(session, current, rule)
                elif denial is None:
                    audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_AUTOMATION,
                        action="automation_rule.action_success", actor=current.actor, target_entity="AutomationRule",
                        target_id=current.rule_id, target_label=rule.name, metadata={"run_id": str(run.id), "action_results": [outcome]})
                    audit.id = uuid.uuid5(run.id, f"action-audit:{index}")
                await session.commit()
                return None
            await session.flush()
            attempted = await self.run_store.begin_action(session, run.id, token, index)
            await session.commit()
        execution = {**attempted, **deadlines, "run_id": str(run.id), "claim_token": str(token)}
        return action, context, rule, execution

    async def dispatch_prepared_action(self, action, context, rule, execution):
        # Some integration/domain adapters need an explicit transaction participant;
        # it is not opened until after the durable attempted checkpoint committed.
        async with AsyncSessionLocal() as session:
            return await self._execute_action(session, action, context, rule=rule, execution=execution)

    async def authorize_hardware_dispatch(self, session: AsyncSession, execution: dict[str, Any]) -> None:
        identity, token = uuid.UUID(execution["run_id"]), uuid.UUID(execution["claim_token"])
        # P04 invokes this after target/parent locks. No other path holds a rule
        # lock while taking a target lock, so this does not invert dispatch order.
        snapshot = await session.get(AutomationRun, identity)
        rule = await session.scalar(select(AutomationRule).where(AutomationRule.id == snapshot.rule_id)
            .with_for_update().execution_options(populate_existing=True)) if snapshot and snapshot.rule_id else None
        current, now = await self.run_store.owned(session, identity, token)
        item = current.action_plan[execution["index"]]
        if item["state"] != "attempting" or item["operation_id"] != execution["operation_id"]:
            raise ValueError("Automation action no longer owns this hardware attempt.")
        denial = await self._preflight(session, current, item, now=now, rule=rule)
        if isinstance(denial, AutomationActionWait):
            raise ValueError(denial.reason)
        if denial:
            raise ValueError(denial.get("detail") or denial["reason"])

    async def account_completed_run(self, identity: uuid.UUID) -> None:
        async with AsyncSessionLocal() as session:
            snapshot = await session.get(AutomationRun, identity)
            if snapshot is None or snapshot.status in {"queued", "processing"}:
                return
            rule = await session.scalar(select(AutomationRule).where(AutomationRule.id == snapshot.rule_id)
                .with_for_update().execution_options(populate_existing=True)) if snapshot.rule_id else None
            current = await session.scalar(select(AutomationRun).where(AutomationRun.id == identity).with_for_update()
                                           .execution_options(populate_existing=True))
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
                await event_bus.publish(f"automation.run.{run_payload['status']}", {"run": run_payload, "rule": rule_payload})
            except Exception:
                logger.exception("automation_completion_publish_failed")

    async def _account_completion(self, session: AsyncSession, run: AutomationRun, rule: AutomationRule | None) -> bool:
        if run.recovery_version != 1 or run.status in {"queued", "processing"}:
            return False
        audit_id = uuid.uuid5(run.id, "completion-audit")
        if await session.get(AuditLog, audit_id) is not None:
            return False
        error = next((str(item.get("error") or item.get("detail") or item.get("reason"))
                      for item in run.action_results if item.get("status") in {"failed", "unknown"}), None)
        run.error = run.error or error
        if rule:
            rule.last_fired_at = run.finished_at
            rule.run_count = int(rule.run_count or 0) + 1
            rule.last_run_status, rule.last_error = run.status, run.error
            # Exhaustion belongs to next_run_at=None. Only explicit disable
            # revokes pending effects; completion must not cancel its own handoff.
        audit = await write_audit_log(session, category=TELEMETRY_CATEGORY_AUTOMATION,
            action=f"automation_rule.{run.status}", actor=run.actor, target_entity="AutomationRule", target_id=run.rule_id,
            target_label=rule.name if rule else "Deleted automation rule", trace_id=run.trace_id,
            metadata={"run_id": str(run.id), "trigger_key": run.trigger_key, "condition_results": run.condition_results,
                      "action_results": run.action_results, "review_reason": run.review_reason},
            outcome="failed" if run.status in {"failed", "review_required"} else "skipped" if run.status == "skipped" else "success",
            level="error" if run.status in {"failed", "review_required"} else "info")
        audit.id = audit_id
        await session.flush()
        return True

    async def recover_completion_audits(self) -> None:
        async with AsyncSessionLocal() as session:
            accounted = select(AuditLog.id).where(AuditLog.category == TELEMETRY_CATEGORY_AUTOMATION,
                AuditLog.action.in_(["automation_rule.success", "automation_rule.skipped", "automation_rule.failed", "automation_rule.review_required"]),
                AuditLog.metadata_["run_id"].astext == cast(AutomationRun.id, String)).exists()
            identities = (await session.scalars(select(AutomationRun.id).where(AutomationRun.recovery_version == 1,
                AutomationRun.status.not_in(["queued", "processing"]), ~accounted)
                .order_by(AutomationRun.finished_at, AutomationRun.id).limit(20))).all()
        for identity in identities:
            await self.account_completed_run(identity)

    async def _process_due_rules(self) -> None:
        await self._reserve_due_rules()
        self.dispatcher.wake()

    async def _reserve_due_rules(self) -> int:
        async with AsyncSessionLocal() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            rules = (await session.scalars(select(AutomationRule).where(AutomationRule.is_active.is_(True),
                AutomationRule.next_run_at.is_not(None), AutomationRule.next_run_at <= now)
                .order_by(AutomationRule.next_run_at).limit(MAX_DUE_RULES_PER_TICK).with_for_update(skip_locked=True))).all()
            count = 0
            for rule in rules:
                triggers = normalize_triggers(rule.triggers)
                scheduled_for = rule.next_run_at
                trigger = due_time_trigger(triggers, now=now, last_fired_at=rule.last_fired_at, scheduled_for=scheduled_for)
                if trigger:
                    payload = {"trigger": trigger, "occurred_at": now.isoformat(), "scheduled_for": scheduled_for.isoformat()}
                    context = captured_automation_context(str(trigger["type"]), payload)
                    await reserve_occurrence(session, rule, context, origin_kind="scheduler",
                        origin_id=f"{rule.id}:{trigger['id']}:{scheduled_for.isoformat()}", actor="Automation Scheduler", source="scheduler")
                    count += 1
                rule.next_run_at = next_run_for_triggers(triggers, now=now, last_fired_at=now)
            await session.commit()
        return count

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


    async def context_for_trigger(self, trigger_key: str, payload: dict[str, Any]) -> AutomationContext:
        if trigger_key.startswith("visitor_pass."):
            payload = await self._fresh_visitor_pass_payload(payload)
        return captured_automation_context(trigger_key, payload)

    async def parse_ai_schedule(self, text: str) -> dict[str, Any]:
        runtime = await get_runtime_config()
        timezone_name = runtime.site_timezone or "Europe/London"
        now = datetime.now(ZoneInfo(timezone_name))
        prompt = (
            "Convert this natural-language automation schedule into JSON only. "
            "Return compact JSON with keys: cron_expression, run_at, start_at, end_at, timezone, "
            "summary, confidence, ambiguity_notes. Use a five-field cron expression or null. "
            "Use ISO-8601 datetimes with timezone offsets. Do not include markdown."
        )
        raw_text = ""
        try:
            provider = get_llm_provider(runtime.llm_provider)
            result = await complete_with_provider_options(
                provider,
                [
                    ChatMessageInput("system", prompt),
                    ChatMessageInput(
                        "user",
                        json.dumps(
                            {
                                "schedule_text": text,
                                "current_datetime": now.isoformat(),
                                "site_timezone": timezone_name,
                            }
                        ),
                    ),
                ],
                max_output_tokens=500,
                request_purpose="automations.parse_schedule",
            )
            raw_text = result.text
            parsed = json_object_from_text(raw_text)
        except Exception as exc:
            parsed = {
                "summary": text,
                "confidence": 0.0,
                "ambiguity_notes": [f"Schedule parser failed: {exc}"],
            }

        if not parsed:
            parsed = {
                "summary": text,
                "confidence": 0.0,
                "ambiguity_notes": ["Schedule parser returned no usable JSON."],
            }
        return validate_schedule_parse(parsed, now=now, timezone_name=timezone_name, raw_text=raw_text)

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
        now = datetime.now(tz=UTC)
        payload_shape_value = payload_shape(payload)
        hmac_verified = False
        async with AsyncSessionLocal() as session:
            policies = await webhook_policies_for_key(session, webhook_key)
            now = await session.scalar(select(func.clock_timestamp()))
            replay_window_seconds = WEBHOOK_HMAC_WINDOW_SECONDS
            if policies:
                allowed_by_source = [
                    policy
                    for policy in policies
                    if webhook_source_allowed(source_ip, policy.get("allowed_source_ips", []))
                ]
                if not allowed_by_source:
                    raise AutomationError("Automation webhook source is not allowed.")
                policies = allowed_by_source
                if any(policy.get("require_hmac") for policy in policies):
                    replay_window_seconds = min(
                        int(policy.get("replay_window_seconds") or WEBHOOK_HMAC_WINDOW_SECONDS)
                        for policy in policies
                    )
                    if not verify_webhook_hmac(
                        webhook_key,
                        raw_body,
                        signature=signature,
                        timestamp=signature_timestamp,
                        nonce=nonce,
                        window_seconds=replay_window_seconds,
                        now=now,
                    ):
                        await record_rejected_webhook_sender(session, webhook_key, source_ip, now=now)
                        raise AutomationError("Automation webhook signature is invalid or expired.")
                    try:
                        await remember_webhook_nonce(
                            session,
                            webhook_key,
                            source_ip,
                            nonce=nonce,
                            signature_timestamp=signature_timestamp,
                            window_seconds=replay_window_seconds,
                        )
                    except AutomationError:
                        await record_rejected_webhook_sender(session, webhook_key, source_ip, now=now)
                        raise
                    hmac_verified = True

            inserted = await session.scalar(pg_insert(AutomationWebhookSender).values(
                id=uuid.uuid4(), webhook_key=webhook_key, source_ip=source_ip,
                first_seen_at=now, last_seen_at=now, event_count=0, last_payload_shape=payload_shape_value,
            ).on_conflict_do_nothing(index_elements=["webhook_key", "source_ip"]).returning(AutomationWebhookSender.id))
            sender = await session.scalar(select(AutomationWebhookSender)
                .where(AutomationWebhookSender.webhook_key == webhook_key, AutomationWebhookSender.source_ip == source_ip)
                .with_for_update().execution_options(populate_existing=True))
            if sender is None:
                raise AutomationError("Webhook sender could not be reserved.")
            new_sender = inserted is not None
            sender.last_seen_at = now
            sender.event_count = int(sender.event_count or 0) + 1
            sender.last_payload_shape = payload_shape_value
            if policies:
                strictest_rate_limit = min(
                    int(policy.get("rate_limit_per_minute") or WEBHOOK_RATE_LIMIT_PER_MINUTE)
                    for policy in policies
                )
                if not apply_webhook_rate_limit(sender, now=now, limit=strictest_rate_limit):
                    sender.rejected_count = int(sender.rejected_count or 0) + 1
                    await session.commit()
                    raise AutomationError("Automation webhook rate limit exceeded.")
                sender.key_strength = "server_generated" if is_high_entropy_webhook_key(webhook_key) else "legacy"
                sender.hmac_required = any(policy.get("require_hmac") for policy in policies)
                sender.allowed_source_ips = sorted(
                    {
                        value
                        for policy in policies
                        for value in normalize_string_list(policy.get("allowed_source_ips"))
                    }
                )
                if hmac_verified:
                    sender.last_nonce = nonce
                    sender.last_signature_at = now
            base_payload = {
                "webhook_key": webhook_key, "source_ip": source_ip, "payload": payload,
                "payload_shape": payload_shape_value, "occurred_at": now.isoformat(), "hmac_verified": hmac_verified,
            }
            # The committed sender sequence identifies this accepted request.
            # Nonce, sequence and occurrences roll back as one unit on failure.
            origin_id = f"{sender.id}:{sender.event_count}"
            identities = await reserve_trigger(session, "webhook.received", base_payload,
                origin_kind="webhook", origin_id=origin_id, actor="Webhook", source="webhook", trace_id=current_trace_id(),
                eligible_rule_ids={uuid.UUID(policy["rule_id"]) for policy in policies})
            if not identities:
                identities.extend(await reserve_trigger(session, "webhook.unrecognized",
                    {**base_payload, "reason": "no_matching_automation"}, origin_kind="webhook", origin_id=origin_id,
                    actor="Webhook", source="webhook", trace_id=current_trace_id()))
            if new_sender:
                identities.extend(await reserve_trigger(session, "webhook.new_sender", base_payload,
                    origin_kind="webhook", origin_id=origin_id, actor="Webhook", source="webhook", trace_id=current_trace_id()))
            await session.commit()
        self.dispatcher.wake()
        runs = []
        for identity in identities:
            await self.dispatcher.run_once(identity)
            runs.append(await self.run_response(identity))
        return {
            "accepted": True,
            "webhook_key": webhook_key,
            "new_sender": new_sender,
            "hmac_verified": hmac_verified,
            "runs": runs,
        }

    async def _run_scheduler(self) -> None:
        while True:
            try:
                await self._process_due_rules()
            except Exception:
                logger.exception("automation_scheduler_tick_failed")
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        # Their canonical mutation owners already committed matching occurrences.
        # Realtime delivery may repeat or disappear and never creates a second run.
        if event.type in {"access_event.finalized", "maintenance_mode.changed", "visitor_pass.created", "visitor_pass.used", "visitor_pass.status_changed"}:
            return
        for trigger_key, payload in automation_triggers_for_origin(event.type, event.payload if isinstance(event.payload, dict) else {}, occurred_at=event.created_at):
            await self.fire_trigger(trigger_key, payload, actor="Automation Engine", source="event_bus",
                origin_id=str(payload.get("access_event_id") or payload.get("event_id") or f"{event.type}:{event.created_at}"))



    async def _evaluate_condition(
        self,
        session: AsyncSession,
        condition: dict[str, Any],
        context: AutomationContext,
    ) -> dict[str, Any]:
        missing = context_missing_references(context, condition)
        if missing:
            return {
                "id": condition["id"],
                "type": condition["type"],
                "passed": False,
                "reason": "context_missing",
                "missing_variables": missing,
            }
        return await evaluate_current_condition(session, condition, context.entities)

    async def _execute_action(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action_type = str(action["type"])
        denial = hardware_action_denial(action_type, context.provenance)
        if denial:
            return workflow_action_result(
                action, "skipped", reason=denial, reason_code=denial,
                detail=HARDWARE_DENIAL_DETAILS[denial], command_sent=False,
                requires_confirmation=denial == REQUESTER_CONFIRMATION_REQUIRED,
            )
        missing = context_missing_references(context, action)
        if missing:
            return workflow_action_result(action, "skipped", reason="context_missing", missing_variables=missing)
        if (
            await is_maintenance_mode_active()
            and action_type != "maintenance_mode.disable"
            and action_paused_by_maintenance_mode(action_type)
        ):
            return workflow_action_result(action, "skipped", reason="maintenance_mode")
        if action_type in {"notification.enable", "notification.disable"}:
            try:
                receipt = await set_automation_activation(
                    session, reference=as_dict(action.get("config")), active=action_type.endswith("enable"),
                )
            except MutationError as exc:
                if exc.code != "not_found":
                    raise
                return workflow_action_result(action, "failed", error="notification_rule_not_found")
            return workflow_action_result(action, "success", **receipt)
        if action_type in HARDWARE_ACTION_TYPES and execution is None:
            raise ValueError("Hardware actions require a claimed durable automation action.")
        if action_type == "gate.open":
            async def authorize(session):
                await self.authorize_hardware_dispatch(session, execution)
            outcome = await get_gate_command_coordinator().execute_open(GateCommandIntent(
                reason=render_action_reason(action, context, rule), source="automation", actor="Automation Engine",
                intent_id=execution["operation_id"], idempotency_key=execution["idempotency_key"],
                target_plan=execution["target_plan"], expires_at=execution.get("expires_at"), require_admission=True,
                automatic_entry_policy=execution.get("automatic_entry_policy") is True,
                event_id=context.provenance.event_id, authorize_dispatch=authorize,
                metadata={"rule_id": str(rule.id), "rule_name": rule.name, "trigger_key": context.trigger_key,
                          "automation_run_id": execution["run_id"]},
            ))
            return {**workflow_action_result(action, "success" if outcome.accepted else "failed"), **outcome.as_payload()}
        if action_type in {"garage_door.open", "garage_door.close"}:
            return await self._command_garage_doors(action, context, rule=rule, execution=execution)
        if integration_action_for_type(action_type):
            return await execute_integration_action(session, action, context, rule=rule,
                operation_id=execution["operation_id"] if execution else None,
                origin={key: execution[key] for key in ("run_id", "rule_fingerprint")} if execution and action_type == "integration.whatsapp.send_message" else None)
        if action_type in {"maintenance_mode.enable", "maintenance_mode.disable"}:
            reason = render_action_reason(action, context, rule)
            status = await set_maintenance_mode(
                action_type.endswith("enable"),
                actor="Automation Engine",
                source=f"Automation: {rule.name}",
                reason=reason,
            )
            return workflow_action_result(action, "success", maintenance_mode=status)
        return workflow_action_result(action, "failed", error="unknown_action")

    async def _command_garage_doors(self, action: dict[str, Any], context: AutomationContext, *,
                                    rule: AutomationRule, execution: dict[str, Any]) -> dict[str, Any]:
        plans = execution.get("target_plans") or []
        if not plans:
            return workflow_action_result(action, "failed", error="garage_door_not_configured")
        command = "open" if action["type"] == "garage_door.open" else "close"
        reason = render_action_reason(action, context, rule)
        outcomes = []
        async def authorize(session):
            await self.authorize_hardware_dispatch(session, execution)
        for plan in plans:
            # One configured action may address several devices. Each existing
            # device key has one stable child operation beneath this action.
            device_key = plan["target_device_key"]
            identity = str(uuid.uuid5(uuid.UUID(execution["operation_id"]), device_key))
            result = await get_access_device_service().command_device(device_key, command, reason,
                schedule_source="garage_door", intent_id=identity, idempotency_key=identity,
                target_plan=plan, expires_at=execution.get("expires_at"), authorize_dispatch=authorize)
            outcomes.append(result.as_payload())
            if result.requires_reconciliation or not result.accepted:
                # An ambiguous earlier target cannot be followed by another
                # hardware transmission within the same configured action.
                break
        unresolved = any(item.get("requires_reconciliation") or item.get("delivery") == "unknown" for item in outcomes)
        failed = any(not item["accepted"] for item in outcomes)
        return workflow_action_result(action, "unknown" if unresolved else "failed" if failed else "success",
            outcomes=outcomes, requires_reconciliation=unresolved,
            skipped_target_count=len(plans) - len(outcomes),
            error="garage_target_outcome_unknown" if unresolved else "garage_target_failed" if failed else None)

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
            rules = (await session.scalars(select(NotificationRule).order_by(NotificationRule.name))).all()
        return [{"id": str(rule.id), "name": rule.name, "trigger_event": rule.trigger_event} for rule in rules]


def action_paused_by_maintenance_mode(action_type: str) -> bool:
    return (
        action_type.startswith("notification.")
        or action_type.startswith("gate.")
        or action_type.startswith("garage_door.")
        or action_type == "maintenance_mode.enable"
        or action_type == "integration.whatsapp.send_message"
    )


def harden_webhook_triggers_for_actions(
    triggers: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> None:
    if not any(action_requires_webhook_hardening(action) for action in actions):
        return
    for trigger in triggers:
        if trigger.get("type") != "webhook.received":
            continue
        config = as_dict(trigger.get("config"))
        if not normalize_string_list(config.get("allowed_source_ips")):
            config["require_hmac"] = True
        if not is_high_entropy_webhook_key(config.get("webhook_key")):
            config["webhook_key"] = generate_automation_webhook_key()
            config["webhook_key_strength"] = "server_generated"
        config.setdefault("rate_limit_per_minute", WEBHOOK_RATE_LIMIT_PER_MINUTE)
        config.setdefault("replay_window_seconds", WEBHOOK_HMAC_WINDOW_SECONDS)
        trigger["config"] = config


def action_requires_webhook_hardening(action: dict[str, Any]) -> bool:
    action_type = str(action.get("type") or "")
    return action_paused_by_maintenance_mode(action_type) or bool(integration_action_for_type(action_type))


async def webhook_policies_for_key(session: AsyncSession, webhook_key: str) -> list[dict[str, Any]]:
    rules = (
        await session.scalars(
            select(AutomationRule)
            .where(AutomationRule.is_active.is_(True))
            .where(or_(*(AutomationRule.trigger_keys.contains([key]) for key in
                         ("webhook.received", "webhook.new_sender", "webhook.unrecognized"))))
            .order_by(AutomationRule.created_at, AutomationRule.id)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).all()
    policies: list[dict[str, Any]] = []
    for rule in rules:
        for trigger in normalize_triggers(rule.triggers):
            if trigger.get("type") != "webhook.received":
                continue
            config = as_dict(trigger.get("config"))
            if str(config.get("webhook_key") or "") != webhook_key:
                continue
            policy = {
                "rule_id": str(rule.id),
                "require_hmac": bool_config(config.get("require_hmac")),
                "allowed_source_ips": normalize_string_list(config.get("allowed_source_ips")),
                "rate_limit_per_minute": safe_int(
                    config.get("rate_limit_per_minute"),
                    default=WEBHOOK_RATE_LIMIT_PER_MINUTE,
                    minimum=1,
                ),
                "replay_window_seconds": safe_int(
                    config.get("replay_window_seconds"),
                    default=WEBHOOK_HMAC_WINDOW_SECONDS,
                    minimum=30,
                ),
            }
            if (
                any(action_requires_webhook_hardening(action) for action in normalize_actions(rule.actions))
                and not policy["require_hmac"]
                and not policy["allowed_source_ips"]
            ):
                policy["require_hmac"] = True
            policies.append(policy)
    return policies


def webhook_source_allowed(source_ip: str, allowed_source_ips: Any) -> bool:
    networks = []
    for raw_value in normalize_string_list(allowed_source_ips):
        try:
            networks.append(ip_network(raw_value, strict=False))
        except ValueError:
            continue
    if not networks:
        return True
    try:
        address = ip_address(source_ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def verify_webhook_hmac(
    webhook_key: str,
    raw_body: bytes,
    *,
    signature: str | None,
    timestamp: str | None,
    nonce: str | None,
    window_seconds: int,
    now: datetime | None = None,
) -> bool:
    signature_hex = normalize_webhook_signature(signature)
    timestamp_text = optional_text(timestamp)
    nonce_text = optional_text(nonce)
    if not signature_hex or not timestamp_text or not nonce_text:
        return False
    signed_at = parse_webhook_timestamp(timestamp_text)
    if not signed_at:
        return False
    now = now or datetime.now(tz=UTC)
    if abs((now - signed_at).total_seconds()) > window_seconds:
        return False
    message = b".".join([timestamp_text.encode(), nonce_text.encode(), raw_body or b""])
    expected = hmac.new(webhook_key.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_hex, expected)


async def remember_webhook_nonce(
    session: AsyncSession,
    webhook_key: str,
    source_ip: str,
    *,
    nonce: str | None,
    signature_timestamp: str | None,
    window_seconds: int,
) -> None:
    nonce_text = optional_text(nonce)
    signed_at = parse_webhook_timestamp(optional_text(signature_timestamp))
    if not nonce_text or not signed_at:
        raise AutomationError("Automation webhook signature is invalid or expired.")
    expires_at = signed_at + timedelta(seconds=window_seconds)
    await session.execute(delete(AutomationWebhookNonce).where(AutomationWebhookNonce.expires_at <= datetime.now(tz=UTC)))
    existing = await session.scalar(
        select(AutomationWebhookNonce.id)
        .where(AutomationWebhookNonce.webhook_key == webhook_key)
        .where(AutomationWebhookNonce.nonce_hash == webhook_nonce_hash(nonce_text))
    )
    if existing:
        raise AutomationError("Automation webhook nonce was already used.")
    session.add(
        AutomationWebhookNonce(
            webhook_key=webhook_key,
            source_ip=source_ip,
            nonce_hash=webhook_nonce_hash(nonce_text),
            signed_at=signed_at,
            expires_at=expires_at,
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AutomationError("Automation webhook nonce was already used.") from exc


def webhook_nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def normalize_webhook_signature(value: str | None) -> str:
    text = optional_text(value)
    if text.lower().startswith("sha256="):
        text = text.split("=", 1)[1].strip()
    return text.lower()


def parse_webhook_timestamp(value: str) -> datetime | None:
    text = optional_text(value)
    if not text:
        return None
    try:
        return datetime.fromtimestamp(int(text), tz=UTC)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def apply_webhook_rate_limit(
    sender: AutomationWebhookSender,
    *,
    now: datetime,
    limit: int,
) -> bool:
    window_started = sender.rate_window_started_at
    if not window_started or (now - window_started).total_seconds() >= WEBHOOK_RATE_WINDOW_SECONDS:
        sender.rate_window_started_at = now
        sender.rate_window_count = 1
        return True
    if int(sender.rate_window_count or 0) >= limit:
        return False
    sender.rate_window_count = int(sender.rate_window_count or 0) + 1
    return True


async def record_rejected_webhook_sender(
    session: AsyncSession,
    webhook_key: str,
    source_ip: str,
    *,
    now: datetime,
) -> None:
    sender = (
        await session.scalars(
            select(AutomationWebhookSender)
            .where(AutomationWebhookSender.webhook_key == webhook_key)
            .where(AutomationWebhookSender.source_ip == source_ip)
        )
    ).first()
    if sender:
        sender.last_seen_at = now
        sender.rejected_count = int(sender.rejected_count or 0) + 1
        await session.commit()


def serialize_rule(rule: AutomationRule | dict[str, Any]) -> dict[str, Any]:
    if isinstance(rule, dict):
        return normalize_rule_payload(rule)
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description or "",
        "is_active": rule.is_active,
        "triggers": normalize_triggers(rule.triggers),
        "trigger_keys": trigger_keys_for_triggers(normalize_triggers(rule.triggers)),
        "conditions": normalize_conditions(rule.conditions),
        "actions": normalize_actions(rule.actions),
        "next_run_at": rule.next_run_at.isoformat() if rule.next_run_at else None,
        "last_fired_at": rule.last_fired_at.isoformat() if rule.last_fired_at else None,
        "run_count": rule.run_count,
        "last_run_status": rule.last_run_status,
        "last_error": rule.last_error,
        "created_by_user_id": str(rule.created_by_user_id) if rule.created_by_user_id else None,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


def serialize_run(run: AutomationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "rule_id": str(run.rule_id) if run.rule_id else None,
        "trigger_key": run.trigger_key,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "trigger_payload": run.trigger_payload,
        "context": {key: value for key, value in (run.context or {}).items() if key not in {"dispatch", "rule_fingerprint"}},
        "recovery_version": run.recovery_version,
        "review_reason": ("historical_unfinished" if run.recovery_version is None and run.status in {"claimed", "running", "queued", "processing"} else run.review_reason),
        "requires_review": bool(run.review_reason) or (run.recovery_version is None and run.status in {"claimed", "running", "queued", "processing"}),
        "action_states": [{"index": item.get("index"), "id": as_dict(item.get("action")).get("id"), "operation_id": item.get("operation_id"), "state": item.get("state")} for item in (run.action_plan or []) if isinstance(item, dict)],
        "condition_results": run.condition_results,
        "action_results": run.action_results,
        "trace_id": run.trace_id,
        "error": run.error,
        "actor": run.actor,
        "source": run.source,
    }


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


def automation_action_reason_code(result: dict[str, Any]) -> str:
    explicit = str(result.get("reason_code") or "").strip()
    if explicit:
        return _reason_code(explicit)
    for outcome in result.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        metadata = as_dict(outcome.get("metadata"))
        schedule = as_dict(metadata.get("schedule_evaluation"))
        if metadata.get("schedule_denied") or schedule.get("allowed") is False:
            return str(schedule.get("reason_code") or "schedule_denied")
        if outcome.get("accepted") and not outcome.get("verified"):
            return "device_state_unverified"
        if outcome.get("accepted") is False and outcome.get("attempts"):
            return "integration_rejected"
    status = str(result.get("status") or "").strip().lower()
    if status == "success":
        return "action_succeeded"
    if status == "skipped":
        return "action_skipped"
    if status == "failed":
        return "action_failed"
    return "action_outcome_unknown"


def automation_action_dispatch_state(result: dict[str, Any]) -> str:
    outcomes = [item for item in result.get("outcomes") or [] if isinstance(item, dict)]
    if outcomes:
        if any(
            as_dict(item.get("metadata")).get("schedule_denied")
            or as_dict(as_dict(item.get("metadata")).get("schedule_evaluation")).get("allowed") is False
            for item in outcomes
        ):
            return "withheld"
        if any(item.get("accepted") and item.get("verified") for item in outcomes):
            return "verified"
        if any(item.get("accepted") for item in outcomes):
            return "accepted"
        if any(item.get("attempts") for item in outcomes):
            return "attempted"
    if result.get("command_id"):
        if result.get("mechanically_confirmed"):
            return "verified"
        return "accepted" if result.get("accepted") else "attempted"
    if str(result.get("status") or "").lower() == "skipped":
        return "withheld"
    return "not_applicable"


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


def render_action_reason(action: dict[str, Any], context: AutomationContext, rule: AutomationRule) -> str:
    template = str(action.get("reason_template") or "")
    rendered = render_with_context(template, context) if template else ""
    return rendered or f"Automation {rule.name}: {action['type']}"


def next_run_for_triggers(
    triggers: list[dict[str, Any]],
    *,
    now: datetime,
    last_fired_at: datetime | None = None,
) -> datetime | None:
    candidates = [
        next_run_for_trigger(trigger, now=now, last_fired_at=last_fired_at)
        for trigger in triggers
        if str(trigger.get("type") or "") in TIME_TRIGGER_KEYS
    ]
    valid = [candidate for candidate in candidates if candidate is not None]
    return min(valid) if valid else None


def due_time_trigger(
    triggers: list[dict[str, Any]],
    *,
    now: datetime,
    last_fired_at: datetime | None = None,
    scheduled_for: datetime | None = None,
) -> dict[str, Any] | None:
    scheduled_for = ensure_aware(scheduled_for) if scheduled_for else None
    due = []
    for trigger in triggers:
        if trigger["type"] not in TIME_TRIGGER_KEYS:
            continue
        if trigger["type"] == "time.every_x" and scheduled_for and scheduled_for <= now:
            due.append((scheduled_for, trigger))
            continue
        baseline = (last_fired_at or scheduled_for or now) - timedelta(seconds=1)
        next_run = next_run_for_trigger(trigger, now=baseline, last_fired_at=last_fired_at)
        if next_run and next_run <= now:
            due.append((next_run, trigger))
    due.sort(key=lambda item: item[0])
    return due[0][1] if due else None


def next_run_for_trigger(
    trigger: dict[str, Any],
    *,
    now: datetime,
    last_fired_at: datetime | None = None,
) -> datetime | None:
    trigger_type = str(trigger.get("type") or "")
    config = as_dict(trigger.get("config"))
    now = ensure_aware(now)
    end_at = parse_datetime(config.get("end_at"))
    if end_at and end_at <= now:
        return None
    if trigger_type == "time.specific_datetime":
        run_at = parse_datetime(config.get("run_at"))
        if not run_at:
            return None
        recurrence = str(config.get("recurrence") or "none")
        if recurrence == "none" or config.get("single_use", True):
            return run_at if run_at > now and not last_fired_at else None
        expression = cron_from_recurrence(run_at, recurrence)
        candidate = cron_next(expression, now, run_at.tzinfo or UTC)
    elif trigger_type == "time.every_x":
        interval = safe_int(config.get("interval"), default=1, minimum=1)
        unit = str(config.get("unit") or "minutes")
        delta = timedelta(**{unit: interval}) if unit in {"minutes", "hours", "days"} else timedelta(minutes=interval)
        start_at = parse_datetime(config.get("start_at")) or now
        candidate = start_at if start_at > now else ((last_fired_at or now) + delta)
        while candidate <= now:
            candidate += delta
    elif trigger_type in {"time.cron", "time.ai_text"}:
        expression = str(config.get("cron_expression") or "").strip()
        if not expression or not croniter.is_valid(expression):
            return None
        timezone = timezone_for(config.get("timezone"))
        candidate = cron_next(expression, now, timezone)
    else:
        return None
    if end_at and candidate and candidate > end_at:
        return None
    return candidate.astimezone(UTC) if candidate else None


def cron_from_recurrence(run_at: datetime, recurrence: str) -> str:
    local = ensure_aware(run_at)
    if recurrence == "daily":
        return f"{local.minute} {local.hour} * * *"
    if recurrence == "weekly":
        return f"{local.minute} {local.hour} * * {(local.weekday() + 1) % 7}"
    if recurrence == "monthly":
        return f"{local.minute} {local.hour} {local.day} * *"
    return f"{local.minute} {local.hour} * * *"


def cron_next(expression: str, now: datetime, timezone: Any) -> datetime:
    localized = ensure_aware(now).astimezone(timezone)
    return croniter(expression, localized).get_next(datetime)


def validate_schedule_parse(
    parsed: dict[str, Any],
    *,
    now: datetime,
    timezone_name: str,
    raw_text: str,
) -> dict[str, Any]:
    cron_expression = optional_text(parsed.get("cron_expression"))
    run_at = optional_text(parsed.get("run_at"))
    timezone = optional_text(parsed.get("timezone")) or timezone_name
    try:
        tz = timezone_for(timezone)
    except Exception:
        tz = timezone_for(timezone_name)
        timezone = timezone_name
    end_at = parse_datetime(parsed.get("end_at"))
    confidence = float(parsed.get("confidence") or 0)
    errors: list[str] = []
    next_run = None
    if cron_expression:
        if croniter.is_valid(cron_expression):
            next_run = cron_next(cron_expression, now, tz)
        else:
            errors.append("Cron expression is invalid.")
    elif run_at:
        next_run = parse_datetime(run_at)
        if not next_run:
            errors.append("run_at is invalid.")
    else:
        errors.append("No cron_expression or run_at was returned.")
    if next_run and next_run <= now:
        errors.append("Next run is not in the future.")
    if end_at and next_run and end_at <= next_run:
        errors.append("End date is before the first run.")
    requires_review = bool(errors) or confidence < AI_SCHEDULE_CONFIDENCE_THRESHOLD
    return {
        "cron_expression": cron_expression,
        "run_at": run_at,
        "start_at": optional_text(parsed.get("start_at")),
        "end_at": end_at.isoformat() if end_at else None,
        "timezone": timezone,
        "summary": optional_text(parsed.get("summary")),
        "confidence": confidence,
        "ambiguity_notes": parsed.get("ambiguity_notes") if isinstance(parsed.get("ambiguity_notes"), list) else [],
        "next_run_at": next_run.astimezone(UTC).isoformat() if next_run else None,
        "requires_review": requires_review,
        "errors": errors,
        "raw_text": raw_text,
    }


def timezone_for(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or "UTC"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def parse_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def json_object_from_text(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


_automation_service = AutomationService()


def get_automation_service() -> AutomationService:
    return _automation_service


def validated_rule_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
        raise AutomationError("Automation name must contain 1–160 characters.")
    return name.strip()


def automation_hardware_deadline(run: AutomationRun) -> datetime:
    value = datetime.fromisoformat(run.context["dispatch"]["source_time"])
    if value.tzinfo is None:
        raise ValueError("Hardware source time must include a timezone.")
    return value + timedelta(seconds=60)
