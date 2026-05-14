import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import ChatMessageInput, complete_with_provider_options, get_llm_provider
from app.core.config import settings
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
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.home_assistant.covers import command_cover, enabled_cover_entities
from app.modules.registry import get_gate_controller
from app.services.automation_integration_actions import (
    execute_integration_action,
    integration_action_catalog,
    integration_action_config,
    integration_action_for_type,
    registered_integration_action_types,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.maintenance import is_maintenance_mode_active, set_mode as set_maintenance_mode
from app.services.notifications import render_template
from app.services.schedules import evaluate_schedule_id
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
from app.services.visitor_passes import serialize_visitor_pass

logger = get_logger(__name__)

AT_TOKEN_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_]*)")
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


@dataclass(frozen=True)
class AutomationVariable:
    name: str
    token: str
    label: str
    scope: str


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


TRIGGER_CATALOG: list[dict[str, Any]] = [
    {
        "id": "time_date",
        "label": "Time & Date",
        "triggers": [
            {
                "type": "time.specific_datetime",
                "label": "Specific Date & Time",
                "description": "Run at one chosen date/time, or recur from that date/time.",
                "scopes": ["time", "event"],
            },
            {
                "type": "time.every_x",
                "label": "Every X",
                "description": "Run every configured number of minutes, hours, or days.",
                "scopes": ["time", "event"],
            },
            {
                "type": "time.cron",
                "label": "Cron Job",
                "description": "Run from a raw five-field cron expression.",
                "scopes": ["time", "event"],
            },
            {
                "type": "time.ai_text",
                "label": "AI Text Input",
                "description": "Parse natural-language schedule text into cron and optional end date.",
                "scopes": ["time", "event"],
            },
        ],
    },
    {
        "id": "vehicle_detections",
        "label": "Vehicle Detections",
        "triggers": [
            {
                "type": "vehicle.known_plate",
                "label": "Known Plate",
                "description": "A known vehicle is detected.",
                "scopes": ["person", "vehicle", "event"],
            },
            {
                "type": "vehicle.unknown_plate",
                "label": "Unknown Plate",
                "description": "An unknown plate is detected.",
                "scopes": ["vehicle", "event"],
            },
            {
                "type": "vehicle.outside_schedule",
                "label": "Outside of Schedule",
                "description": "A known vehicle is denied by its access schedule.",
                "scopes": ["person", "vehicle", "event"],
            },
        ],
    },
    {
        "id": "maintenance_mode",
        "label": "Maintenance Mode",
        "triggers": [
            {
                "type": "maintenance_mode.enabled",
                "label": "Maintenance Mode Enabled",
                "description": "The global automation kill-switch was enabled.",
                "scopes": ["maintenance", "event"],
            },
            {
                "type": "maintenance_mode.disabled",
                "label": "Maintenance Mode Disabled",
                "description": "The global automation kill-switch was disabled.",
                "scopes": ["maintenance", "event"],
            },
        ],
    },
    {
        "id": "visitor_pass",
        "label": "Visitor Pass",
        "triggers": [
            {
                "type": "visitor_pass.created",
                "label": "Visitor Pass Created",
                "description": "A Visitor Pass was created.",
                "scopes": ["visitor_pass", "vehicle", "event"],
            },
            {
                "type": "visitor_pass.detected",
                "label": "Visitor Pass Detected",
                "description": "A Visitor Pass vehicle was detected.",
                "scopes": ["visitor_pass", "vehicle", "event"],
            },
            {
                "type": "visitor_pass.used",
                "label": "Visitor Pass Used",
                "description": "A Visitor Pass was claimed by an arriving vehicle.",
                "scopes": ["visitor_pass", "vehicle", "event"],
            },
            {
                "type": "visitor_pass.expired",
                "label": "Visitor Pass Expired",
                "description": "A Visitor Pass window expired unused.",
                "scopes": ["visitor_pass", "event"],
            },
        ],
    },
    {
        "id": "ai_agent",
        "label": "AI Agent",
        "triggers": [
            {
                "type": "ai.phrase_received",
                "label": "Phrase Received",
                "description": "Alfred receives a phrase that matches this automation.",
                "scopes": ["ai", "event"],
            },
            {
                "type": "ai.issue_detected",
                "label": "Issue Detected",
                "description": "Alfred autonomously flags an anomaly.",
                "scopes": ["ai", "event"],
            },
        ],
    },
    {
        "id": "webhook",
        "label": "Webhook",
        "triggers": [
            {
                "type": "webhook.received",
                "label": "Webhook Received",
                "description": "A webhook is received on an automation endpoint.",
                "scopes": ["webhook", "event"],
            },
            {
                "type": "webhook.unrecognized",
                "label": "Unrecognised Webhook",
                "description": "A webhook key has no matching active receiver rule.",
                "scopes": ["webhook", "event"],
            },
            {
                "type": "webhook.new_sender",
                "label": "New Webhook Sender",
                "description": "A webhook key is used by an unseen source IP.",
                "scopes": ["webhook", "event"],
            },
        ],
    },
]

CONDITION_CATALOG: list[dict[str, Any]] = [
    {
        "id": "person",
        "label": "Person",
        "conditions": [
            {"type": "person.on_site", "label": "Person On Site", "scopes": ["person"]},
            {"type": "person.off_site", "label": "Person Off Site", "scopes": ["person"]},
        ],
    },
    {
        "id": "vehicles",
        "label": "Vehicles",
        "conditions": [
            {"type": "vehicle.on_site", "label": "Vehicle On Site", "scopes": ["vehicle", "person"]},
            {"type": "vehicle.off_site", "label": "Vehicle Off Site", "scopes": ["vehicle", "person"]},
        ],
    },
    {
        "id": "maintenance_mode",
        "label": "Maintenance Mode",
        "conditions": [
            {
                "type": "maintenance_mode.enabled",
                "label": "Maintenance Mode Enabled",
                "scopes": ["maintenance"],
            },
            {
                "type": "maintenance_mode.disabled",
                "label": "Maintenance Mode Disabled",
                "scopes": ["maintenance"],
            },
        ],
    },
]

ACTION_CATALOG: list[dict[str, Any]] = [
    {
        "id": "notifications",
        "label": "Notifications",
        "actions": [
            {
                "type": "notification.enable",
                "label": "Enable Notification",
                "description": "Enable an existing notification workflow.",
            },
            {
                "type": "notification.disable",
                "label": "Disable Notification",
                "description": "Disable an existing notification workflow.",
            },
        ],
    },
    {
        "id": "gate_actions",
        "label": "Gate Actions",
        "actions": [
            {
                "type": "gate.open",
                "label": "Open the Gate",
                "description": "Open configured gate entities through the gate controller.",
            },
        ],
    },
    {
        "id": "garage_door_actions",
        "label": "Garage Door Actions",
        "actions": [
            {
                "type": "garage_door.open",
                "label": "Open Garage Door",
                "description": "Open one or more configured garage door entities.",
            },
            {
                "type": "garage_door.close",
                "label": "Close Garage Door",
                "description": "Close one or more configured garage door entities.",
            },
        ],
    },
    {
        "id": "maintenance_mode",
        "label": "Maintenance Mode",
        "actions": [
            {
                "type": "maintenance_mode.enable",
                "label": "Enable Maintenance Mode",
                "description": "Enable the global automation kill-switch.",
            },
            {
                "type": "maintenance_mode.disable",
                "label": "Disable Maintenance Mode",
                "description": "Disable the global automation kill-switch.",
            },
        ],
    },
]

VARIABLES: list[AutomationVariable] = [
    AutomationVariable("FirstName", "@FirstName", "First name", "person"),
    AutomationVariable("LastName", "@LastName", "Last name", "person"),
    AutomationVariable("DisplayName", "@DisplayName", "Display name", "person"),
    AutomationVariable("PersonId", "@PersonId", "Person ID", "person"),
    AutomationVariable("Registration", "@Registration", "Registration", "vehicle"),
    AutomationVariable("VehicleRegistrationNumber", "@VehicleRegistrationNumber", "Registration number", "vehicle"),
    AutomationVariable("VehicleId", "@VehicleId", "Vehicle ID", "vehicle"),
    AutomationVariable("VehicleName", "@VehicleName", "Vehicle display name", "vehicle"),
    AutomationVariable("VehicleMake", "@VehicleMake", "Vehicle make", "vehicle"),
    AutomationVariable("VehicleColour", "@VehicleColour", "Vehicle colour", "vehicle"),
    AutomationVariable("VehicleColor", "@VehicleColor", "Vehicle colour", "vehicle"),
    AutomationVariable("VisitorPassId", "@VisitorPassId", "Visitor Pass ID", "visitor_pass"),
    AutomationVariable("VisitorName", "@VisitorName", "Visitor name", "visitor_pass"),
    AutomationVariable(
        "VisitorPassVehicleRegistration",
        "@VisitorPassVehicleRegistration",
        "Visitor Pass vehicle registration",
        "visitor_pass",
    ),
    AutomationVariable("VisitorPassVehicleMake", "@VisitorPassVehicleMake", "Visitor Pass vehicle make", "visitor_pass"),
    AutomationVariable(
        "VisitorPassVehicleColour",
        "@VisitorPassVehicleColour",
        "Visitor Pass vehicle colour",
        "visitor_pass",
    ),
    AutomationVariable("VisitorPassDurationOnSite", "@VisitorPassDurationOnSite", "Visitor Pass duration", "visitor_pass"),
    AutomationVariable("MaintenanceModeReason", "@MaintenanceModeReason", "Maintenance reason", "maintenance"),
    AutomationVariable("MaintenanceModeDuration", "@MaintenanceModeDuration", "Maintenance duration", "maintenance"),
    AutomationVariable("WebhookKey", "@WebhookKey", "Webhook key", "webhook"),
    AutomationVariable("WebhookSenderIp", "@WebhookSenderIp", "Webhook sender IP", "webhook"),
    AutomationVariable("AlfredPhrase", "@AlfredPhrase", "Alfred phrase", "ai"),
    AutomationVariable("AlfredIssue", "@AlfredIssue", "Alfred issue", "ai"),
    AutomationVariable("OccurredAt", "@OccurredAt", "Event timestamp", "event"),
    AutomationVariable("Date", "@Date", "Event date", "time"),
    AutomationVariable("Time", "@Time", "Event time", "time"),
    AutomationVariable("EventType", "@EventType", "Event type", "event"),
    AutomationVariable("Subject", "@Subject", "Subject", "event"),
    AutomationVariable("Message", "@Message", "Message", "event"),
    AutomationVariable("Source", "@Source", "Source", "event"),
]

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
        config = await get_runtime_config()
        return {
            "triggers": TRIGGER_CATALOG,
            "conditions": CONDITION_CATALOG,
            "actions": ACTION_CATALOG + await integration_action_catalog(),
            "variables": variable_groups(),
            "notification_rules": await self._notification_rule_catalog(),
            "garage_doors": [
                {
                    "entity_id": str(entity["entity_id"]),
                    "name": str(entity.get("name") or entity["entity_id"]),
                    "schedule_id": entity.get("schedule_id"),
                }
                for entity in enabled_cover_entities(
                    config.home_assistant_garage_door_entities,
                    default_open_service=config.home_assistant_gate_open_service,
                )
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
        normalized_triggers = normalize_triggers(triggers)
        normalized_actions = normalize_actions(actions)
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
        if triggers is not None:
            rule.triggers = normalize_triggers(triggers)
            if not rule.triggers:
                raise AutomationError("At least one automation trigger is required.")
            rule.trigger_keys = trigger_keys_for_triggers(rule.triggers)
            rule.next_run_at = next_run_for_triggers(rule.triggers, now=datetime.now(tz=UTC), last_fired_at=rule.last_fired_at)
        if conditions is not None:
            rule.conditions = normalize_conditions(conditions)
        if actions is not None:
            rule.actions = normalize_actions(actions)
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
        trace.finish(
            status="error" if status == "failed" else "ok",
            level="error" if status == "failed" else "info",
            summary=f"{rule_name} {status} for {trigger_key}",
            context={
                "run_id": str(run_payload["id"]),
                "status": status,
                "condition_count": len(condition_results),
                "action_count": len(action_results),
            },
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
        provider = get_llm_provider(runtime.llm_provider)
        raw_text = ""
        try:
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
            parsed = deterministic_schedule_parse(text, now=now, timezone_name=timezone_name)
            parsed["provider_error"] = str(exc)

        if not parsed:
            parsed = deterministic_schedule_parse(text, now=now, timezone_name=timezone_name)
        return validate_schedule_parse(parsed, now=now, timezone_name=timezone_name, raw_text=raw_text)

    async def handle_webhook(
        self,
        webhook_key: str,
        payload: dict[str, Any],
        *,
        source_ip: str,
    ) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        async with AsyncSessionLocal() as session:
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
                    last_payload_shape=payload_shape(payload),
                )
                session.add(sender)
            else:
                sender.last_seen_at = now
                sender.event_count = int(sender.event_count or 0) + 1
                sender.last_payload_shape = payload_shape(payload)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                new_sender = False

        base_payload = {
            "webhook_key": webhook_key,
            "source_ip": source_ip,
            "payload": payload,
            "payload_shape": payload_shape(payload),
            "occurred_at": now.isoformat(),
        }
        runs = await self.fire_trigger("webhook.received", base_payload, source="webhook", actor="Webhook")
        if new_sender:
            runs.extend(await self.fire_trigger("webhook.new_sender", base_payload, source="webhook", actor="Webhook"))
        return {
            "accepted": True,
            "webhook_key": webhook_key,
            "new_sender": new_sender,
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
            visitor_pass = payload.get("visitor_pass") if isinstance(payload.get("visitor_pass"), dict) else {}
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
        config = condition.get("config") if isinstance(condition.get("config"), dict) else {}
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
            return {
                "id": action["id"],
                "type": action["type"],
                "status": "skipped",
                "reason": "context_missing",
                "missing_variables": missing,
            }
        action_type = str(action["type"])
        if (
            await is_maintenance_mode_active()
            and action_type != "maintenance_mode.disable"
            and action_paused_by_maintenance_mode(action_type)
        ):
            return {
                "id": action["id"],
                "type": action_type,
                "status": "skipped",
                "reason": "maintenance_mode",
            }
        if action_type in {"notification.enable", "notification.disable"}:
            return await self._toggle_notification_rule(session, action, active=action_type.endswith("enable"))
        if action_type == "gate.open":
            reason = render_action_reason(action, context, rule)
            outcome = await get_gate_controller(settings.gate_controller).open_gate(reason)
            return {
                "id": action["id"],
                "type": action_type,
                "status": "success" if outcome.accepted else "failed",
                "accepted": outcome.accepted,
                "state": outcome.state.value,
                "detail": outcome.detail,
            }
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
            return {
                "id": action["id"],
                "type": action_type,
                "status": "success",
                "maintenance_mode": status,
            }
        return {
            "id": action["id"],
            "type": action_type,
            "status": "failed",
            "error": "unknown_action",
        }

    async def _toggle_notification_rule(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        *,
        active: bool,
    ) -> dict[str, Any]:
        config = action.get("config") if isinstance(action.get("config"), dict) else {}
        rule = await resolve_notification_rule(session, config)
        if not rule:
            return {
                "id": action["id"],
                "type": action["type"],
                "status": "failed",
                "error": "notification_rule_not_found",
            }
        rule.is_active = active
        return {
            "id": action["id"],
            "type": action["type"],
            "status": "success",
            "notification_rule_id": str(rule.id),
            "is_active": active,
        }

    async def _command_garage_doors(
        self,
        session: AsyncSession,
        action: dict[str, Any],
        context: AutomationContext,
        *,
        rule: AutomationRule,
    ) -> dict[str, Any]:
        config = await get_runtime_config()
        entities = automation_garage_targets(
            action,
            config.home_assistant_garage_door_entities,
            default_open_service=config.home_assistant_gate_open_service,
        )
        if not entities:
            return {
                "id": action["id"],
                "type": action["type"],
                "status": "failed",
                "error": "garage_door_not_configured",
            }
        command = "open" if action["type"] == "garage_door.open" else "close"
        reason = render_action_reason(action, context, rule)
        outcomes = []
        for entity in entities:
            outcomes.append(
                await self._garage_command_outcome(
                    session,
                    entity,
                    command,
                    reason,
                    timezone_name=config.site_timezone,
                    default_policy=config.schedule_default_policy,
                )
            )
        failed = [outcome for outcome in outcomes if not outcome["accepted"]]
        return {
            "id": action["id"],
            "type": action["type"],
            "status": "failed" if failed else "success",
            "outcomes": outcomes,
            "error": "; ".join(str(item.get("detail") or item["entity_id"]) for item in failed) if failed else None,
        }

    async def _garage_command_outcome(
        self,
        session: AsyncSession,
        entity: dict[str, Any],
        command: str,
        reason: str,
        *,
        timezone_name: str,
        default_policy: str,
    ) -> dict[str, Any]:
        if command == "open":
            schedule = await evaluate_schedule_id(
                session,
                entity.get("schedule_id"),
                datetime.now(tz=UTC),
                timezone_name=timezone_name,
                default_policy=default_policy,
                source="garage_door",
            )
            if not schedule.allowed:
                return {
                    "entity_id": str(entity["entity_id"]),
                    "name": str(entity.get("name") or entity["entity_id"]),
                    "accepted": False,
                    "state": "schedule_denied",
                    "detail": schedule.reason,
                }
        outcome = await command_cover(HomeAssistantClient(), entity, command, reason)
        return {
            "entity_id": outcome.entity_id,
            "name": outcome.name,
            "accepted": outcome.accepted,
            "state": outcome.state,
            "detail": outcome.detail,
        }

    async def _fresh_visitor_pass_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        visitor_pass = payload.get("visitor_pass") if isinstance(payload.get("visitor_pass"), dict) else payload
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
        "is_active": value.get("is_active", value.get("enabled", True)) is not False,
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


def normalize_triggers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {trigger_type for group in TRIGGER_CATALOG for trigger_type in [item["type"] for item in group["triggers"]]}
    normalized = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        trigger_type = str(raw.get("type") or raw.get("trigger_key") or "").strip()
        if trigger_type not in allowed:
            continue
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        normalized.append(
            {
                "id": str(raw.get("id") or f"trigger-{index + 1}"),
                "type": trigger_type,
                "config": normalize_trigger_config(trigger_type, config),
            }
        )
    return normalized


def normalize_trigger_config(trigger_type: str, config: dict[str, Any]) -> dict[str, Any]:
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
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
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
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
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
            "notification_rule_id": optional_text(config.get("notification_rule_id") or config.get("rule_id")),
            "notification_rule_name": optional_text(config.get("notification_rule_name") or config.get("rule_name")),
        }
    if action_type.startswith("garage_door."):
        return {
            "target_entity_ids": normalize_string_list(config.get("target_entity_ids", config.get("entity_ids", [])))
        }
    if integration_action_for_type(action_type):
        return integration_action_config(action_type, config)
    return {}


def automation_garage_targets(
    action: dict[str, Any],
    configured_entities: list[dict[str, Any]],
    *,
    default_open_service: str | None,
) -> list[dict[str, Any]]:
    action_config = action.get("config") if isinstance(action.get("config"), dict) else {}
    target_ids = set(normalize_string_list(action_config.get("target_entity_ids")))
    return [
        entity
        for entity in enabled_cover_entities(
            configured_entities,
            default_open_service=default_open_service,
        )
        if not target_ids or str(entity["entity_id"]) in target_ids
    ]


def action_paused_by_maintenance_mode(action_type: str) -> bool:
    return (
        action_type.startswith("notification.")
        or action_type.startswith("gate.")
        or action_type.startswith("garage_door.")
        or action_type == "maintenance_mode.enable"
        or action_type == "integration.whatsapp.send_message"
    )


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


def variable_groups() -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
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
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    visitor_pass = payload.get("visitor_pass") if isinstance(payload.get("visitor_pass"), dict) else {}
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
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
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    visitor_pass = payload.get("visitor_pass") if isinstance(payload.get("visitor_pass"), dict) else {}
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


def referenced_variable_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, str):
        names.update(match.group(1) for match in AT_TOKEN_PATTERN.finditer(value))
    elif isinstance(value, dict):
        for item in value.values():
            names.update(referenced_variable_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(referenced_variable_names(item))
    return names


def render_with_context(template: str, context: AutomationContext) -> str:
    return render_template(template, context.variables)


def render_action_reason(action: dict[str, Any], context: AutomationContext, rule: AutomationRule) -> str:
    template = str(action.get("reason_template") or "")
    rendered = render_with_context(template, context) if template else ""
    return rendered or f"Automation {rule.name}: {action['type']}"


def trigger_matches(trigger: dict[str, Any], context: AutomationContext) -> bool:
    if trigger["type"] != context.trigger_key:
        return False
    config = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
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
    config = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
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


def deterministic_schedule_parse(text: str, *, now: datetime, timezone_name: str) -> dict[str, Any]:
    lower = text.lower()
    day_map = {
        "monday": 1,
        "tuesday": 2,
        "wednesday": 3,
        "thursday": 4,
        "friday": 5,
        "saturday": 6,
        "sunday": 0,
    }
    day = next((value for label, value in day_map.items() if label in lower), None)
    time_match = re.search(r"\b([01]?\d|2[0-3])(?::([0-5]\d))?\s*(am|pm)?\b", lower)
    if day is not None and time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        meridiem = time_match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        end_at = None
        until_match = re.search(r"until\s+(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)", lower)
        if until_match:
            month_names = {
                "january": 1,
                "february": 2,
                "march": 3,
                "april": 4,
                "may": 5,
                "june": 6,
                "july": 7,
                "august": 8,
                "september": 9,
                "october": 10,
                "november": 11,
                "december": 12,
            }
            month_text = until_match.group(2)
            month = month_names.get(month_text)
            if not month:
                month = next((number for label, number in month_names.items() if label.startswith(month_text[:3])), None)
            if month:
                candidate = datetime(now.year, month, int(until_match.group(1)), 23, 59, 59, tzinfo=timezone_for(timezone_name))
                if candidate < now:
                    candidate = candidate.replace(year=now.year + 1)
                end_at = candidate.isoformat()
        return {
            "cron_expression": f"{minute} {hour} * * {day}",
            "run_at": None,
            "start_at": None,
            "end_at": end_at,
            "timezone": timezone_name,
            "summary": text,
            "confidence": 0.7,
            "ambiguity_notes": [],
        }
    return {
        "cron_expression": None,
        "run_at": None,
        "start_at": None,
        "end_at": None,
        "timezone": timezone_name,
        "summary": text,
        "confidence": 0.0,
        "ambiguity_notes": ["Could not parse schedule deterministically."],
    }


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
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    visitor_pass = payload.get("visitor_pass") if isinstance(payload.get("visitor_pass"), dict) else {}
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


def safe_int(value: Any, *, default: int = 1, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, (str, bytes)):
        iterable: list[Any] = [value]
    elif isinstance(value, list):
        iterable = value
    else:
        iterable = []
    return [str(item).strip() for item in iterable if str(item).strip()]


def canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


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
