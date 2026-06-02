import asyncio
import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import ChatMessageInput, complete_with_provider_options, get_llm_provider
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import (
    AutomationRule,
    AutomationRun,
    AutomationWebhookSender,
    NotificationRule,
    Presence,
    User,
    Vehicle,
    VisitorPass,
)
from app.models.enums import PresenceState
from app.services.access_devices import get_access_device_service
from app.services.automation_integration_actions import (
    execute_integration_action,
    integration_action_catalog,
    integration_action_config,
    integration_action_for_type,
    registered_integration_action_types,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.gate_commands import GateCommandIntent, get_gate_command_coordinator
from app.services.maintenance import is_maintenance_mode_active, set_mode as set_maintenance_mode
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_AUTOMATION,
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    payload_shape,
    sanitize_payload,
    telemetry,
    write_audit_log,
)
from app.services.type_helpers import as_dict
from app.services.visitor_passes import serialize_visitor_pass
from app.services.workflows.catalog import (
    automation_action_catalog,
    automation_condition_catalog,
    automation_trigger_catalog,
    automation_variables,
)
from app.services.workflows.context import (
    canonical_key,
    normalize_string_list,
    referenced_variable_names,
    render_template,
    workflow_action_result,
)

logger = get_logger(__name__)

SCHEDULER_INTERVAL_SECONDS = 15
MAX_DUE_RULES_PER_TICK = 25
AI_SCHEDULE_CONFIDENCE_THRESHOLD = 0.65
AUTOMATION_BRIDGE_IGNORED_EVENT_TYPES = {
    "notification.trigger",
    "notification.sent",
    "notification.failed",
    "notification.skipped",
}
AUTOMATION_BRIDGE_IGNORED_EVENT_PREFIXES = ("automation.run.",)
WEBHOOK_KEY_PREFIX = "whk_"
WEBHOOK_KEY_RANDOM_BYTES = 32
WEBHOOK_HMAC_WINDOW_SECONDS = 300
WEBHOOK_RATE_WINDOW_SECONDS = 60
WEBHOOK_RATE_LIMIT_PER_MINUTE = 60
WEBHOOK_SIGNATURE_HEADER = "X-IACS-Webhook-Signature"
WEBHOOK_TIMESTAMP_HEADER = "X-IACS-Webhook-Timestamp"
WEBHOOK_NONCE_HEADER = "X-IACS-Webhook-Nonce"


@dataclass
class AutomationContext:
    trigger_key: str
    subject: str
    trigger_payload: dict[str, Any]
    facts: dict[str, Any] = field(default_factory=dict)
    entities: dict[str, str] = field(default_factory=dict)
    scopes: set[str] = field(default_factory=set)
    variables: dict[str, str] = field(default_factory=dict)
    missing_required_variables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "trigger": {
                "key": self.trigger_key,
                "subject": self.subject,
            },
            "trigger_key": self.trigger_key,
            "subject": self.subject,
            "trigger_payload": sanitize_payload(self.trigger_payload),
            "facts": sanitize_payload(self.facts),
            "entities": self.entities,
            "scopes": sorted(self.scopes),
            "variables": self.variables,
            "missing_required_variables": sorted(set(self.missing_required_variables)),
            "warnings": self.warnings,
        }


TRIGGER_CATALOG = automation_trigger_catalog()

CONDITION_CATALOG = automation_condition_catalog()

ACTION_CATALOG = automation_action_catalog()

VARIABLES = automation_variables()

VARIABLE_BY_NAME = {variable.name.lower(): variable for variable in VARIABLES}
TRIGGER_SCOPES = {
    trigger["type"]: set(trigger.get("scopes") or [])
    for group in TRIGGER_CATALOG
    for trigger in group["triggers"]
}
TIME_TRIGGER_KEYS = {"time.specific_datetime", "time.every_x", "time.cron", "time.ai_text"}


class AutomationError(RuntimeError):
    """Raised when an automation rule or action cannot be evaluated safely."""


@dataclass(frozen=True)
class ScheduledAutomationClaim:
    rule_id: str
    run_id: str
    trigger_key: str
    trigger_payload: dict[str, Any]


class AutomationService:
    def __init__(self) -> None:
        self._started = False
        self._scheduler_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._started:
            return
        event_bus.subscribe(self._handle_realtime_event)
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
        normalized_triggers = normalize_triggers(triggers, generate_webhook_keys=True)
        normalized_actions = normalize_actions(actions)
        harden_webhook_triggers_for_actions(normalized_triggers, normalized_actions)
        if not normalized_triggers:
            raise AutomationError("At least one automation trigger is required.")
        if not normalized_actions:
            raise AutomationError("At least one automation action is required.")
        now = datetime.now(tz=UTC)
        rule = AutomationRule(
            name=name.strip()[:160] or "Automation Rule",
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
        before = serialize_rule(rule)
        if name is not None:
            rule.name = name.strip()[:160] or rule.name
        if description is not None:
            rule.description = description.strip() or None
        normalized_triggers = (
            normalize_triggers(triggers, generate_webhook_keys=True)
            if triggers is not None
            else normalize_triggers(rule.triggers)
        )
        normalized_actions = normalize_actions(actions) if actions is not None else normalize_actions(rule.actions)
        harden_webhook_triggers_for_actions(normalized_triggers, normalized_actions)
        if triggers is not None:
            rule.triggers = normalized_triggers
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
            action_previews.append(
                {
                    "id": action["id"],
                    "type": action["type"],
                    "dry_run": True,
                    "executed": False,
                    "would_execute": conditions_passed and not missing,
                    "skipped": bool(missing),
                    "missing_variables": missing,
                    "rendered_reason": render_with_context(action.get("reason_template") or "", context),
                }
            )
        return {
            "rule": payload,
            "context": context.to_payload(),
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
    ) -> list[dict[str, Any]]:
        context = await self.context_for_trigger(trigger_key, payload or {})
        async with AsyncSessionLocal() as session:
            rules = (
                await session.scalars(
                    select(AutomationRule)
                    .where(AutomationRule.is_active.is_(True))
                    .where(AutomationRule.trigger_keys.contains([trigger_key]))
                    .order_by(AutomationRule.created_at)
                )
            ).all()

        results: list[dict[str, Any]] = []
        for rule in rules:
            if not any(trigger_matches(trigger, context) for trigger in normalize_triggers(rule.triggers)):
                continue
            results.append(
                await self.execute_rule(
                    str(rule.id),
                    trigger_key=trigger_key,
                    trigger_payload=payload or {},
                    context=context,
                    actor=actor,
                    source=source,
                )
            )
        if trigger_key == "webhook.received" and not results:
            unrecognized_payload = {**(payload or {}), "reason": "no_matching_automation"}
            results.extend(
                await self.fire_trigger(
                    "webhook.unrecognized",
                    unrecognized_payload,
                    actor=actor,
                    source=source,
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
        claimed_run_id: str | None = None,
    ) -> dict[str, Any]:
        rule_uuid = uuid.UUID(str(rule_id))
        async with AsyncSessionLocal() as session:
            rule = await session.get(AutomationRule, rule_uuid)
            if not rule or not rule.is_active:
                if claimed_run_id:
                    run = await session.get(AutomationRun, uuid.UUID(str(claimed_run_id)))
                    if run and run.status == "claimed":
                        run.status = "skipped"
                        run.finished_at = datetime.now(tz=UTC)
                        run.error = "rule_not_active"
                        await session.commit()
                return {"executed": False, "status": "skipped", "reason": "rule_not_active"}
            context = context or await self.context_for_trigger(trigger_key, trigger_payload)
            trace = telemetry.start_trace(
                f"Automation Rule: {rule.name}",
                category=TELEMETRY_CATEGORY_AUTOMATION,
                actor=actor,
                source=source,
                context={"rule_id": str(rule.id), "trigger_key": trigger_key},
            )
            run = None
            if claimed_run_id:
                run = await session.get(AutomationRun, uuid.UUID(str(claimed_run_id)))
                if run and run.rule_id != rule.id:
                    run = None
            if run:
                run.trigger_key = trigger_key
                run.status = "running"
                run.trigger_payload = sanitize_payload(trigger_payload)
                run.context = context.to_payload()
                run.trace_id = trace.trace_id
                run.actor = actor
                run.source = source
            else:
                run = AutomationRun(
                    rule_id=rule.id,
                    trigger_key=trigger_key,
                    status="running",
                    started_at=datetime.now(tz=UTC),
                    trigger_payload=sanitize_payload(trigger_payload),
                    context=context.to_payload(),
                    trace_id=trace.trace_id,
                    actor=actor,
                    source=source,
                )
                session.add(run)
            await session.flush()

            condition_results: list[dict[str, Any]] = []
            action_results: list[dict[str, Any]] = []
            status = "success"
            error: str | None = None
            try:
                status, condition_results = await self._evaluate_rule_conditions(
                    session,
                    normalize_conditions(rule.conditions),
                    context,
                )
                if status != "skipped":
                    status, action_results, error = await self._execute_rule_actions(
                        session,
                        normalize_actions(rule.actions),
                        context,
                        rule=rule,
                    )
            except Exception as exc:
                status = "failed"
                error = str(exc)
                logger.exception("automation_rule_execution_failed", extra={"rule_id": str(rule.id)})

            finished_at = datetime.now(tz=UTC)
            run.status = status
            run.finished_at = finished_at
            run.condition_results = condition_results
            run.action_results = action_results
            run.error = error
            rule.last_fired_at = finished_at
            rule.run_count = int(rule.run_count or 0) + 1
            rule.last_run_status = status
            rule.last_error = error
            rule.next_run_at = next_run_for_triggers(
                normalize_triggers(rule.triggers),
                now=finished_at,
                last_fired_at=finished_at,
            )
            if not rule.next_run_at and all(trigger["type"] in TIME_TRIGGER_KEYS for trigger in normalize_triggers(rule.triggers)):
                rule.is_active = False

            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_AUTOMATION,
                action=f"automation_rule.{status}",
                actor=actor,
                target_entity="AutomationRule",
                target_id=rule.id,
                target_label=rule.name,
                metadata={
                    "run_id": str(run.id),
                    "trigger_key": trigger_key,
                    "condition_results": condition_results,
                    "action_results": action_results,
                },
                outcome="failed" if status == "failed" else "success",
                level="error" if status == "failed" else "info",
                trace_id=trace.trace_id,
            )
            await session.commit()
            await session.refresh(run)
            await session.refresh(rule)
            run_payload = serialize_run(run)
            rule_payload = serialize_rule(rule)
            rule_name = str(rule_payload["name"])

        event_payload = {
            "run": run_payload,
            "rule": rule_payload,
        }
        await event_bus.publish(f"automation.run.{status}", event_payload)
        trace_context: dict[str, Any] = {
            "run_id": str(run_payload["id"]),
            "status": status,
            "condition_count": len(condition_results),
            "action_count": len(action_results),
            "condition_results": condition_results,
            "action_results": action_results,
        }
        skip_reason = automation_skip_reason(status, condition_results, action_results, error)
        if skip_reason:
            trace_context["skip_reason"] = skip_reason
        trace.finish(
            status="error" if status == "failed" else "ok",
            level="error" if status == "failed" else "info",
            summary=f"{rule_name} {status} for {trigger_key}",
            context=trace_context,
            error=error,
        )
        return {"executed": status == "success", "status": status, "run": run_payload}

    async def _evaluate_rule_conditions(
        self,
        session: AsyncSession,
        conditions: list[dict[str, Any]],
        context: AutomationContext,
    ) -> tuple[str, list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        for condition in conditions:
            result = await self._evaluate_condition(session, condition, context)
            results.append(result)
            if not result.get("passed"):
                return "skipped", results
        return "success", results

    async def _execute_rule_actions(
        self,
        session: AsyncSession,
        actions: list[dict[str, Any]],
        context: AutomationContext,
        *,
        rule: AutomationRule,
    ) -> tuple[str, list[dict[str, Any]], str | None]:
        results: list[dict[str, Any]] = []
        status = "success"
        for action in actions:
            result = await self._execute_action(session, action, context, rule=rule)
            results.append(result)
            if result.get("status") == "failed":
                error = str(result.get("error") or result.get("detail") or "Automation action failed.")
                return "failed", results, error
            if result.get("status") == "skipped":
                status = "skipped"
        return status, results, None

    async def context_for_trigger(self, trigger_key: str, payload: dict[str, Any]) -> AutomationContext:
        if trigger_key.startswith("visitor_pass."):
            payload = await self._fresh_visitor_pass_payload(payload)
        scopes = set(TRIGGER_SCOPES.get(trigger_key, {"event"}))
        if trigger_key.startswith("time."):
            scopes.update({"time", "event"})
        context = AutomationContext(
            trigger_key=trigger_key,
            subject=subject_for_trigger(trigger_key, payload),
            trigger_payload=payload,
            facts=facts_from_payload(trigger_key, payload),
            entities=entities_from_payload(payload),
            scopes=scopes,
        )
        context.variables = build_context_variables(context)
        return context

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
                    if not verify_webhook_hmac(
                        webhook_key,
                        raw_body,
                        signature=signature,
                        timestamp=signature_timestamp,
                        nonce=nonce,
                        window_seconds=min(
                            int(policy.get("replay_window_seconds") or WEBHOOK_HMAC_WINDOW_SECONDS)
                            for policy in policies
                        ),
                        now=now,
                    ):
                        await record_rejected_webhook_sender(session, webhook_key, source_ip, now=now)
                        raise AutomationError("Automation webhook signature is invalid or expired.")
                    hmac_verified = True

            sender = (
                await session.scalars(
                    select(AutomationWebhookSender)
                    .where(AutomationWebhookSender.webhook_key == webhook_key)
                    .where(AutomationWebhookSender.source_ip == source_ip)
                )
            ).first()
            new_sender = sender is None
            if sender is None:
                sender = AutomationWebhookSender(
                    webhook_key=webhook_key,
                    source_ip=source_ip,
                    first_seen_at=now,
                    last_seen_at=now,
                    event_count=1,
                    last_payload_shape=payload_shape_value,
                )
                session.add(sender)
            else:
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
                    if nonce and sender.last_nonce == nonce:
                        sender.rejected_count = int(sender.rejected_count or 0) + 1
                        await session.commit()
                        raise AutomationError("Automation webhook nonce was already used.")
                    sender.last_nonce = nonce
                    sender.last_signature_at = now
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                new_sender = False

        base_payload = {
            "webhook_key": webhook_key,
            "source_ip": source_ip,
            "payload": payload,
            "payload_shape": payload_shape_value,
            "occurred_at": now.isoformat(),
            "hmac_verified": hmac_verified,
        }
        runs = await self.fire_trigger("webhook.received", base_payload, source="webhook", actor="Webhook")
        if new_sender:
            runs.extend(await self.fire_trigger("webhook.new_sender", base_payload, source="webhook", actor="Webhook"))
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

    async def _process_due_rules(self) -> None:
        now = datetime.now(tz=UTC)
        claims = await self._claim_due_rules(now)
        for claim in claims:
            await self.execute_rule(
                claim.rule_id,
                trigger_key=claim.trigger_key,
                trigger_payload=claim.trigger_payload,
                actor="Automation Scheduler",
                source="scheduler",
                claimed_run_id=claim.run_id,
            )

    async def _claim_due_rules(self, now: datetime) -> list[ScheduledAutomationClaim]:
        claims: list[ScheduledAutomationClaim] = []
        async with AsyncSessionLocal() as session:
            rules = (
                await session.scalars(
                    select(AutomationRule)
                    .where(AutomationRule.is_active.is_(True))
                    .where(AutomationRule.next_run_at.is_not(None))
                    .where(AutomationRule.next_run_at <= now)
                    .order_by(AutomationRule.next_run_at)
                    .limit(MAX_DUE_RULES_PER_TICK)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for rule in rules:
                triggers = normalize_triggers(rule.triggers)
                scheduled_for = rule.next_run_at
                trigger = due_time_trigger(
                    triggers,
                    now=now,
                    last_fired_at=rule.last_fired_at,
                    scheduled_for=scheduled_for,
                )
                if not trigger:
                    rule.next_run_at = next_run_for_triggers(
                        triggers,
                        now=now,
                        last_fired_at=rule.last_fired_at,
                    )
                    continue
                trigger_payload = {
                    "trigger": trigger,
                    "occurred_at": now.isoformat(),
                    "scheduled_for": scheduled_for.isoformat() if scheduled_for else now.isoformat(),
                }
                run = AutomationRun(
                    rule_id=rule.id,
                    trigger_key=str(trigger["type"]),
                    status="claimed",
                    started_at=now,
                    trigger_payload=sanitize_payload(trigger_payload),
                    context={},
                    actor="Automation Scheduler",
                    source="scheduler",
                )
                session.add(run)
                rule.next_run_at = next_run_for_triggers(
                    triggers,
                    now=now,
                    last_fired_at=now,
                )
                await session.flush()
                claims.append(
                    ScheduledAutomationClaim(
                        rule_id=str(rule.id),
                        run_id=str(run.id),
                        trigger_key=str(trigger["type"]),
                        trigger_payload=trigger_payload,
                    )
                )
            await session.commit()
        return claims

    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        for trigger_key, payload in self._event_to_triggers(event):
            await self.fire_trigger(trigger_key, payload, actor="Automation Engine", source="event_bus")

    def _event_to_triggers(self, event: RealtimeEvent) -> list[tuple[str, dict[str, Any]]]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in AUTOMATION_BRIDGE_IGNORED_EVENT_TYPES or event.type.startswith(
            AUTOMATION_BRIDGE_IGNORED_EVENT_PREFIXES
        ):
            return []
        if event.type == "maintenance_mode.changed":
            return [
                (
                    "maintenance_mode.enabled" if payload.get("is_active") else "maintenance_mode.disabled",
                    {**payload, "occurred_at": event.created_at},
                )
            ]
        if event.type == "access_event.finalized":
            if payload.get("backfilled") or payload.get("skip_automation_actions"):
                return []
            return self._access_event_to_vehicle_trigger(event, payload)
        if event.type == "visitor_pass.created":
            return [("visitor_pass.created", {**payload, "occurred_at": event.created_at})]
        if event.type == "visitor_pass.used":
            return [
                ("visitor_pass.used", {**payload, "occurred_at": event.created_at}),
                ("visitor_pass.detected", {**payload, "occurred_at": event.created_at}),
            ]
        if event.type == "visitor_pass.status_changed":
            visitor_pass = as_dict(payload.get("visitor_pass"))
            if str(visitor_pass.get("status") or "").lower() == "expired":
                return [("visitor_pass.expired", {**payload, "occurred_at": event.created_at})]
        if event.type == "ai.phrase_received":
            return [("ai.phrase_received", {**payload, "occurred_at": event.created_at})]
        if event.type == "ai.issue_detected":
            return [("ai.issue_detected", {**payload, "occurred_at": event.created_at})]
        return []

    def _access_event_to_vehicle_trigger(
        self,
        event: RealtimeEvent,
        payload: dict[str, Any],
    ) -> list[tuple[str, dict[str, Any]]]:
        decision = str(payload.get("decision") or "").lower()
        vehicle_id = optional_text(payload.get("vehicle_id"))
        if decision == "granted" and vehicle_id:
            trigger_key = "vehicle.known_plate"
        elif decision == "denied" and vehicle_id:
            trigger_key = "vehicle.outside_schedule"
        elif decision == "denied" and not vehicle_id:
            trigger_key = "vehicle.unknown_plate"
        else:
            return []
        return [(trigger_key, {**payload, "occurred_at": payload.get("occurred_at") or event.created_at})]

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
        condition_type = str(condition.get("type") or "")
        config = as_dict(condition.get("config"))
        if condition_type in {"person.on_site", "person.off_site"}:
            person_id = str(config.get("person_id") or context.entities.get("person_id") or "")
            present = await person_is_present(session, person_id)
            expected = condition_type == "person.on_site"
            return condition_result(condition, present is expected, {"person_id": person_id, "present": present})
        if condition_type in {"vehicle.on_site", "vehicle.off_site"}:
            vehicle_id = str(config.get("vehicle_id") or context.entities.get("vehicle_id") or "")
            present = await vehicle_is_present(session, vehicle_id)
            expected = condition_type == "vehicle.on_site"
            return condition_result(condition, present is expected, {"vehicle_id": vehicle_id, "present": present})
        if condition_type in {"maintenance_mode.enabled", "maintenance_mode.disabled"}:
            active = await is_maintenance_mode_active()
            expected = condition_type == "maintenance_mode.enabled"
            return condition_result(condition, active is expected, {"maintenance_mode_active": active})
        return {
            "id": condition["id"],
            "type": condition_type,
            "passed": False,
            "reason": "unknown_condition",
        }

    async def _execute_action(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
    ) -> dict[str, Any]:
        missing = context_missing_references(context, action)
        if missing:
            return workflow_action_result(action, "skipped", reason="context_missing", missing_variables=missing)
        action_type = str(action["type"])
        if (
            await is_maintenance_mode_active()
            and action_type != "maintenance_mode.disable"
            and action_paused_by_maintenance_mode(action_type)
        ):
            return workflow_action_result(action, "skipped", reason="maintenance_mode")
        if action_type in {"notification.enable", "notification.disable"}:
            return await self._toggle_notification_rule(session, action, active=action_type.endswith("enable"))
        if action_type == "gate.open":
            reason = render_action_reason(action, context, rule)
            outcome = await get_gate_command_coordinator().execute_open(
                GateCommandIntent(
                    reason=reason,
                    source="automation",
                    actor="Automation Engine",
                    metadata={
                        "rule_id": str(getattr(rule, "id", "")) if getattr(rule, "id", None) else None,
                        "rule_name": getattr(rule, "name", None),
                        "trigger_key": context.trigger_key,
                    },
                )
            )
            return workflow_action_result(
                action,
                "success" if outcome.accepted else "failed",
                accepted=outcome.accepted,
                state=outcome.state.value,
                detail=outcome.detail,
                intent_id=outcome.intent.intent_id,
                command_id=outcome.command_id,
                mechanically_confirmed=outcome.mechanically_confirmed,
                requires_reconciliation=outcome.requires_reconciliation,
            )
        if action_type in {"garage_door.open", "garage_door.close"}:
            return await self._command_garage_doors(session, action, context, rule=rule)
        if integration_action_for_type(action_type):
            return await execute_integration_action(session, action, context, rule=rule)
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

    async def _toggle_notification_rule(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        *,
        active: bool,
    ) -> dict[str, Any]:
        config = as_dict(action.get("config"))
        rule = await resolve_notification_rule(session, config)
        if not rule:
            return workflow_action_result(action, "failed", error="notification_rule_not_found")
        rule.is_active = active
        return workflow_action_result(action, "success", notification_rule_id=str(rule.id), is_active=active)

    async def _command_garage_doors(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
    ) -> dict[str, Any]:
        devices = await automation_garage_targets(action)
        if not devices:
            return workflow_action_result(action, "failed", error="garage_door_not_configured")
        command = "open" if action["type"] == "garage_door.open" else "close"
        reason = render_action_reason(action, context, rule)
        outcomes = []
        for device in devices:
            outcomes.append(
                await self._garage_command_outcome(
                    session,
                    device.key,
                    command,
                    reason,
                )
            )
        failed = [outcome for outcome in outcomes if not outcome["accepted"]]
        return workflow_action_result(
            action,
            "failed" if failed else "success",
            outcomes=outcomes,
            error="; ".join(str(item.get("detail") or item["entity_id"]) for item in failed) if failed else None,
        )

    async def _garage_command_outcome(
        self,
        session: AsyncSession,
        device_key: str,
        command: str,
        reason: str,
    ) -> dict[str, Any]:
        outcome = await get_access_device_service().command_device(
            device_key,
            command,
            reason,
            schedule_source="garage_door",
        )
        return outcome.as_payload()

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


def normalize_rule_payload(value: dict[str, Any]) -> dict[str, Any]:
    triggers = normalize_triggers(value.get("triggers"))
    return {
        "id": str(value.get("id") or uuid.uuid4()),
        "name": str(value.get("name") or "Automation Rule").strip()[:160],
        "description": str(value.get("description") or "").strip(),
        "is_active": value.get("is_active", True) is not False,
        "triggers": triggers,
        "trigger_keys": trigger_keys_for_triggers(triggers),
        "conditions": normalize_conditions(value.get("conditions")),
        "actions": normalize_actions(value.get("actions")),
        "next_run_at": value.get("next_run_at"),
        "last_fired_at": value.get("last_fired_at"),
        "last_run_status": value.get("last_run_status"),
        "last_error": value.get("last_error"),
        "run_count": int(value.get("run_count") or 0),
    }


def normalize_triggers(value: Any, *, generate_webhook_keys: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {trigger_type for group in TRIGGER_CATALOG for trigger_type in [item["type"] for item in group["triggers"]]}
    normalized = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        trigger_type = str(raw.get("type") or "").strip()
        if trigger_type not in allowed:
            continue
        config = as_dict(raw.get("config"))
        normalized.append(
            {
                "id": str(raw.get("id") or f"trigger-{index + 1}"),
                "type": trigger_type,
                "config": normalize_trigger_config(
                    trigger_type,
                    config,
                    generate_webhook_key=generate_webhook_keys,
                ),
            }
        )
    return normalized


def normalize_trigger_config(
    trigger_type: str,
    config: dict[str, Any],
    *,
    generate_webhook_key: bool = False,
) -> dict[str, Any]:
    if trigger_type == "time.every_x":
        unit = str(config.get("unit") or "minutes").lower()
        if unit not in {"minutes", "hours", "days"}:
            unit = "minutes"
        return {
            "interval": safe_int(config.get("interval"), default=1, minimum=1),
            "unit": unit,
            "start_at": optional_text(config.get("start_at")),
            "end_at": optional_text(config.get("end_at")),
        }
    if trigger_type in {"time.cron", "time.ai_text"}:
        return {
            "cron_expression": optional_text(config.get("cron_expression")),
            "timezone": optional_text(config.get("timezone")) or "Europe/London",
            "start_at": optional_text(config.get("start_at")),
            "end_at": optional_text(config.get("end_at")),
            "natural_text": optional_text(config.get("natural_text")),
            "summary": optional_text(config.get("summary")),
        }
    if trigger_type == "time.specific_datetime":
        recurrence = str(config.get("recurrence") or "none").lower()
        if recurrence not in {"none", "daily", "weekly", "monthly"}:
            recurrence = "none"
        return {
            "run_at": optional_text(config.get("run_at")),
            "single_use": config.get("single_use", recurrence == "none") is not False,
            "recurrence": recurrence,
            "end_at": optional_text(config.get("end_at")),
        }
    if trigger_type == "webhook.received":
        webhook_key = optional_text(config.get("webhook_key"))
        key_was_generated = False
        if generate_webhook_key and not is_high_entropy_webhook_key(webhook_key):
            webhook_key = generate_automation_webhook_key()
            key_was_generated = True
        return {
            "webhook_key": webhook_key,
            "webhook_key_strength": "server_generated"
            if key_was_generated or is_high_entropy_webhook_key(webhook_key)
            else "legacy",
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
            "source_ip": optional_text(config.get("source_ip")),
        }
    return {
        key: item
        for key, item in config.items()
        if key
        in {
            "person_id",
            "vehicle_id",
            "registration_number",
            "visitor_pass_id",
            "phrase",
            "match_mode",
            "webhook_key",
            "source_ip",
        }
    }


def normalize_conditions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {condition["type"] for group in CONDITION_CATALOG for condition in group["conditions"]}
    conditions = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        condition_type = str(raw.get("type") or "").strip()
        if condition_type not in allowed:
            continue
        config = as_dict(raw.get("config"))
        conditions.append(
            {
                "id": str(raw.get("id") or f"condition-{index + 1}"),
                "type": condition_type,
                "config": {
                    key: item
                    for key, item in config.items()
                    if key in {"person_id", "vehicle_id"}
                },
            }
        )
    return conditions


def normalize_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {
        action["type"]
        for group in ACTION_CATALOG
        for action in group["actions"]
    } | registered_integration_action_types()
    actions = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        action_type = str(raw.get("type") or "").strip()
        if action_type not in allowed:
            continue
        config = as_dict(raw.get("config"))
        actions.append(
            {
                "id": str(raw.get("id") or f"action-{index + 1}"),
                "type": action_type,
                "config": normalize_action_config(action_type, config),
                "reason_template": str(raw.get("reason_template") or ""),
            }
        )
    return actions


def normalize_action_config(action_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if action_type.startswith("notification."):
        return {
            "notification_rule_id": optional_text(config.get("notification_rule_id")),
            "notification_rule_name": optional_text(config.get("notification_rule_name")),
        }
    if action_type.startswith("garage_door."):
        return {
            "target_entity_ids": normalize_string_list(config.get("target_entity_ids"))
        }
    if integration_action_for_type(action_type):
        return integration_action_config(action_type, config)
    return {}


async def automation_garage_targets(action: dict[str, Any]) -> list[Any]:
    action_config = as_dict(action.get("config"))
    target_ids = set(normalize_string_list(action_config.get("target_entity_ids")))
    return [
        device
        for device in await get_access_device_service().list_devices(kind="garage_door", enabled_only=True)
        if not target_ids or device.key in target_ids
    ]


def action_paused_by_maintenance_mode(action_type: str) -> bool:
    return (
        action_type.startswith("notification.")
        or action_type.startswith("gate.")
        or action_type.startswith("garage_door.")
        or action_type == "maintenance_mode.enable"
        or action_type == "integration.whatsapp.send_message"
    )


def generate_automation_webhook_key() -> str:
    return f"{WEBHOOK_KEY_PREFIX}{secrets.token_urlsafe(WEBHOOK_KEY_RANDOM_BYTES)}"


def is_high_entropy_webhook_key(value: Any) -> bool:
    text = optional_text(value)
    return text.startswith(WEBHOOK_KEY_PREFIX) and len(text) >= len(WEBHOOK_KEY_PREFIX) + 40


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
            .where(AutomationRule.trigger_keys.contains(["webhook.received"]))
            .order_by(AutomationRule.created_at)
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


def trigger_keys_for_triggers(triggers: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(trigger["type"]) for trigger in triggers if trigger.get("type")))


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
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "trigger_payload": run.trigger_payload,
        "context": run.context,
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


def facts_from_payload(trigger_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    facts = as_dict(payload.get("facts"))
    visitor_pass = as_dict(payload.get("visitor_pass"))
    body = as_dict(payload.get("payload"))
    merged = {**payload, **facts}
    if visitor_pass:
        merged.update(
            {
                "visitor_pass_id": visitor_pass.get("id"),
                "visitor_name": visitor_pass.get("visitor_name"),
                "visitor_pass_status": visitor_pass.get("status"),
                "visitor_pass_expected_time": visitor_pass.get("expected_time"),
                "visitor_pass_vehicle_registration": visitor_pass.get("number_plate"),
                "visitor_pass_vehicle_make": visitor_pass.get("vehicle_make"),
                "visitor_pass_vehicle_colour": visitor_pass.get("vehicle_colour"),
                "visitor_pass_duration_on_site": visitor_pass.get("duration_human"),
                "visitor_pass_duration_on_site_seconds": visitor_pass.get("duration_on_site_seconds"),
                "registration_number": visitor_pass.get("number_plate"),
                "vehicle_make": visitor_pass.get("vehicle_make"),
                "vehicle_colour": visitor_pass.get("vehicle_colour"),
            }
        )
    if trigger_key.startswith("webhook."):
        merged.update(
            {
                "webhook_key": payload.get("webhook_key"),
                "webhook_sender_ip": payload.get("source_ip"),
                "message": json.dumps(body)[:500] if body else payload.get("message"),
            }
        )
    if trigger_key.startswith("ai."):
        merged.update(
            {
                "alfred_phrase": payload.get("phrase") or payload.get("message"),
                "alfred_issue": payload.get("issue") or payload.get("message"),
            }
        )
    return merged


def entities_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    facts = as_dict(payload.get("facts"))
    visitor_pass = as_dict(payload.get("visitor_pass"))
    merged = {**payload, **facts}
    entities = {
        "person_id": optional_text(merged.get("person_id")),
        "vehicle_id": optional_text(merged.get("vehicle_id")),
        "visitor_pass_id": optional_text(visitor_pass.get("id") or merged.get("visitor_pass_id")),
        "access_event_id": optional_text(merged.get("access_event_id") or merged.get("event_id")),
    }
    return {key: value for key, value in entities.items() if value}


def build_context_variables(context: AutomationContext) -> dict[str, str]:
    facts = {canonical_key(key): "" if value is None else str(value) for key, value in context.facts.items()}

    def pick(*keys: str, default: str = "") -> str:
        for key in keys:
            value = facts.get(canonical_key(key))
            if value:
                return value
        return default

    occurred_at = pick("occurred_at", "created_at", default=datetime.now(tz=UTC).isoformat())
    variables = {
        "FirstName": pick("first_name"),
        "LastName": pick("last_name"),
        "DisplayName": pick("display_name", "person_name"),
        "PersonId": pick("person_id", default=context.entities.get("person_id", "")),
        "Registration": pick("registration_number", "vehicle_registration_number", "visitor_pass_vehicle_registration"),
        "VehicleRegistrationNumber": pick("vehicle_registration_number", "registration_number", "visitor_pass_vehicle_registration"),
        "VehicleId": pick("vehicle_id", default=context.entities.get("vehicle_id", "")),
        "VehicleName": pick("vehicle_name", "vehicle_display_name", "vehicle_description", "registration_number"),
        "VehicleMake": pick("vehicle_make", "make", "visitor_pass_vehicle_make"),
        "VehicleColour": pick("vehicle_colour", "vehicle_color", "colour", "color", "visitor_pass_vehicle_colour"),
        "VehicleColor": pick("vehicle_color", "vehicle_colour", "color", "colour", "visitor_pass_vehicle_colour"),
        "VisitorPassId": pick("visitor_pass_id", default=context.entities.get("visitor_pass_id", "")),
        "VisitorName": pick("visitor_name"),
        "VisitorPassVehicleRegistration": pick("visitor_pass_vehicle_registration", "number_plate", "registration_number"),
        "VisitorPassVehicleMake": pick("visitor_pass_vehicle_make", "vehicle_make"),
        "VisitorPassVehicleColour": pick("visitor_pass_vehicle_colour", "vehicle_colour", "vehicle_color"),
        "VisitorPassDurationOnSite": pick("visitor_pass_duration_on_site", "duration_human"),
        "MaintenanceModeReason": pick("maintenance_mode_reason", "reason"),
        "MaintenanceModeDuration": pick("maintenance_mode_duration", "duration_label"),
        "WebhookKey": pick("webhook_key"),
        "WebhookSenderIp": pick("webhook_sender_ip", "source_ip"),
        "AlfredPhrase": pick("alfred_phrase", "phrase", "message"),
        "AlfredIssue": pick("alfred_issue", "issue", "message"),
        "OccurredAt": occurred_at,
        "Date": date_label(occurred_at),
        "Time": time_label(occurred_at),
        "EventType": context.trigger_key.replace(".", " ").replace("_", " ").title(),
        "Subject": context.subject,
        "Message": pick("message", default=context.subject),
        "Source": pick("source"),
    }
    return {key: "" if value is None else str(value) for key, value in variables.items()}


def context_missing_references(context: AutomationContext, value: Any) -> list[str]:
    missing: list[str] = []
    for name in sorted(referenced_variable_names(value)):
        variable = VARIABLE_BY_NAME.get(name.lower())
        if not variable:
            context.warnings.append(f"Unknown variable @{name}.")
            missing.append(name)
            continue
        if variable.scope not in context.scopes:
            context.warnings.append(f"Variable @{name} is not available for {context.trigger_key}.")
            missing.append(variable.name)
            continue
        if not context.variables.get(variable.name):
            missing.append(variable.name)
    if missing:
        context.missing_required_variables = sorted(set([*context.missing_required_variables, *missing]))
    return sorted(set(missing))


def render_with_context(template: str, context: AutomationContext) -> str:
    return render_template(template, context.variables)


def render_action_reason(action: dict[str, Any], context: AutomationContext, rule: AutomationRule) -> str:
    template = str(action.get("reason_template") or "")
    rendered = render_with_context(template, context) if template else ""
    return rendered or f"Automation {rule.name}: {action['type']}"


def trigger_matches(trigger: dict[str, Any], context: AutomationContext) -> bool:
    if trigger["type"] != context.trigger_key:
        return False
    config = as_dict(trigger.get("config"))
    facts = {canonical_key(key): str(value).lower() for key, value in context.facts.items() if value is not None}
    if config.get("person_id") and str(config["person_id"]) != context.entities.get("person_id"):
        return False
    if config.get("vehicle_id") and str(config["vehicle_id"]) != context.entities.get("vehicle_id"):
        return False
    if config.get("visitor_pass_id") and str(config["visitor_pass_id"]) != context.entities.get("visitor_pass_id"):
        return False
    if config.get("registration_number"):
        expected = str(config["registration_number"]).strip().replace(" ", "").lower()
        actual = facts.get(canonical_key("registration_number"), "").replace(" ", "")
        if expected and expected != actual:
            return False
    if config.get("webhook_key") and str(config["webhook_key"]) != str(context.facts.get("webhook_key") or ""):
        return False
    if config.get("source_ip") and str(config["source_ip"]) != str(context.facts.get("source_ip") or ""):
        return False
    phrase = str(config.get("phrase") or "").strip().lower()
    if phrase:
        actual_phrase = str(context.facts.get("alfred_phrase") or context.facts.get("message") or "").lower()
        if str(config.get("match_mode") or "contains") == "exact":
            return actual_phrase == phrase
        return phrase in actual_phrase
    return True


def condition_result(condition: dict[str, Any], passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": condition["id"],
        "type": condition["type"],
        "passed": passed,
        "details": details,
    }


async def person_is_present(session: AsyncSession, person_id: str) -> bool:
    parsed = parse_uuid(person_id)
    if not parsed:
        return False
    presence = await session.get(Presence, parsed)
    return bool(presence and presence.state == PresenceState.PRESENT)


async def vehicle_is_present(session: AsyncSession, vehicle_id: str) -> bool:
    parsed = parse_uuid(vehicle_id)
    if not parsed:
        return False
    vehicle = await session.get(Vehicle, parsed)
    if not vehicle or not vehicle.person_id:
        return False
    return await person_is_present(session, str(vehicle.person_id))


async def resolve_notification_rule(session: AsyncSession, config: dict[str, Any]) -> NotificationRule | None:
    rule_id = parse_uuid(config.get("notification_rule_id"))
    if rule_id:
        return await session.get(NotificationRule, rule_id)
    name = str(config.get("notification_rule_name") or "").strip().lower()
    if not name:
        return None
    rules = (await session.scalars(select(NotificationRule).order_by(NotificationRule.name))).all()
    exact = [rule for rule in rules if rule.name.lower() == name]
    if exact:
        return exact[0]
    partial = [rule for rule in rules if name in rule.name.lower()]
    return partial[0] if len(partial) == 1 else None


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


def subject_for_trigger(trigger_key: str, payload: dict[str, Any]) -> str:
    facts = as_dict(payload.get("facts"))
    visitor_pass = as_dict(payload.get("visitor_pass"))
    return str(
        payload.get("subject")
        or facts.get("subject")
        or payload.get("message")
        or visitor_pass.get("visitor_name")
        or trigger_key.replace(".", " ").title()
    )


def date_label(value: str) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def time_label(value: str) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%H:%M") if parsed else ""


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_aware(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return ensure_aware(parsed)
    except ValueError:
        return None


def ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def timezone_for(value: Any) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or "UTC"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def optional_text(value: Any) -> str:
    return str(value or "").strip()


def bool_config(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def safe_int(value: Any, *, default: int = 1, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


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
