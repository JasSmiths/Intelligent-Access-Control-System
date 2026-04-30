import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import NotificationRule, Person, Presence, Schedule
from app.models.enums import PresenceState
from app.modules.announcements.home_assistant_tts import AnnouncementTarget, HomeAssistantTtsAnnouncer
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.notifications.apprise_client import (
    AppriseNotificationSender,
    normalize_apprise_url,
    split_apprise_urls,
    summarize_apprise_url,
)
from app.modules.notifications.home_assistant_mobile import (
    HomeAssistantMobileAppNotifier,
    HomeAssistantMobileAppTarget,
)
from app.modules.notifications.base import (
    ComposedNotification,
    NotificationContext,
    NotificationDeliveryError,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.discord_messaging import get_discord_messaging_service
from app.services.notification_snapshots import (
    delete_notification_snapshot,
    notification_snapshot_absolute_url,
    store_notification_snapshot,
)
from app.services.schedules import schedule_allows_at
from app.services.settings import get_runtime_config
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, telemetry
from app.services.tts_phonetics import apply_vehicle_tts_phonetics
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

AT_TOKEN_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_]*)")
LEGACY_TOKEN_PATTERN = re.compile(r"\[([A-Za-z][A-Za-z0-9_]*)\]")
HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID = "input_boolean.announcements"
VOICE_ANNOUNCEMENTS_DISABLED_MESSAGE = (
    "Voice Notification suppressed: `input_boolean.announcements` is disabled."
)


@dataclass
class NotificationWorkflowResult:
    notification: ComposedNotification
    delivered_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    failures: list[str] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.delivered_count > 0:
            return "sent"
        if self.failed_count > 0 or self.failures:
            return "failed"
        return "skipped"


@dataclass(frozen=True)
class NotificationActionOutcome:
    delivered: bool
    skipped: bool = False
    reason: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationSnapshotAttachment:
    path: str
    content_type: str
    public_url: str | None

TRIGGER_CATALOG: list[dict[str, Any]] = [
    {
        "id": "ai_agents",
        "label": "AI Agents",
        "events": [
            {
                "value": "agent_anomaly_alert",
                "label": "AI Anomaly Alert",
                "severity": "critical",
                "description": "The AI agent raises an explicit anomaly alert.",
            },
        ],
    },
    {
        "id": "compliance",
        "label": "Compliance",
        "events": [
            {
                "value": "expired_mot_detected",
                "label": "Expired MOT Detected",
                "severity": "warning",
                "description": "DVLA reports a vehicle MOT status other than Valid or Not Required on arrival.",
            },
            {
                "value": "expired_tax_detected",
                "label": "Expired Tax Detected",
                "severity": "warning",
                "description": "DVLA reports a vehicle tax status other than Taxed or SORN on arrival.",
            },
        ],
    },
    {
        "id": "gate_actions",
        "label": "Gate Actions",
        "events": [
            {
                "value": "garage_door_open_failed",
                "label": "Garage Door Failed",
                "severity": "critical",
                "description": "A linked garage door command failed.",
            },
            {
                "value": "gate_open_failed",
                "label": "Gate Open Failed",
                "severity": "critical",
                "description": "The access decision was granted but the gate command failed.",
            },
        ],
    },
    {
        "id": "gate_malfunctions",
        "label": "Gate Malfunctions",
        "events": [
            {
                "value": "gate_malfunction_2hrs",
                "label": "Gate Malfunction - 2hrs",
                "severity": "critical",
                "description": "The primary gate malfunction has been active for at least two hours.",
            },
            {
                "value": "gate_malfunction_30m",
                "label": "Gate Malfunction - 30m",
                "severity": "warning",
                "description": "The primary gate malfunction has been active for at least 30 minutes.",
            },
            {
                "value": "gate_malfunction_60m",
                "label": "Gate Malfunction - 60m",
                "severity": "critical",
                "description": "The primary gate malfunction has been active for at least 60 minutes.",
            },
            {
                "value": "gate_malfunction_fubar",
                "label": "Gate Malfunction - FUBAR",
                "severity": "critical",
                "description": "Automated gate malfunction recovery attempts have been exhausted.",
            },
            {
                "value": "gate_malfunction_initial",
                "label": "Gate Malfunction - Initial",
                "severity": "warning",
                "description": "The primary gate has remained open for more than five minutes.",
            },
        ],
    },
    {
        "id": "leaderboard",
        "label": "Leaderboard",
        "events": [
            {
                "value": "leaderboard_overtake",
                "label": "Leaderboard Overtake",
                "severity": "info",
                "description": "A known vehicle takes the top spot on Top Charts.",
            },
        ],
    },
    {
        "id": "maintenance_mode",
        "label": "Maintenance Mode",
        "events": [
            {
                "value": "maintenance_mode_disabled",
                "label": "Maintenance Mode Disabled",
                "severity": "info",
                "description": "The global automation kill-switch was disabled.",
            },
            {
                "value": "maintenance_mode_enabled",
                "label": "Maintenance Mode Enabled",
                "severity": "warning",
                "description": "The global automation kill-switch was enabled.",
            },
        ],
    },
    {
        "id": "vehicle_detections",
        "label": "Vehicle Detections",
        "events": [
            {
                "value": "authorized_entry",
                "label": "Authorised Vehicle Detected",
                "severity": "info",
                "description": "A known vehicle is granted entry inside its access policy.",
            },
            {
                "value": "duplicate_entry",
                "label": "Duplicate Entry",
                "severity": "warning",
                "description": "A person already marked home is detected entering again.",
            },
            {
                "value": "duplicate_exit",
                "label": "Duplicate Exit",
                "severity": "info",
                "description": "A person already marked away is detected exiting again.",
            },
            {
                "value": "outside_schedule",
                "label": "Outside Schedule",
                "severity": "warning",
                "description": "A known vehicle is denied by schedule or access policy.",
            },
            {
                "value": "unauthorized_plate",
                "label": "Unknown Vehicle Detected",
                "severity": "warning",
                "description": "An unknown or inactive vehicle plate is denied.",
            },
            {
                "value": "visitor_pass_vehicle_arrived",
                "label": "Visitor Pass Vehicle Arrived",
                "severity": "info",
                "description": "A vehicle matched to a Visitor Pass has arrived on site.",
            },
            {
                "value": "visitor_pass_vehicle_exited",
                "label": "Visitor Pass Vehicle Exited",
                "severity": "info",
                "description": "A vehicle matched to a Visitor Pass has left the site.",
            },
        ],
    },
    {
        "id": "visitor_pass",
        "label": "Visitor Pass",
        "events": [
            {
                "value": "visitor_pass_cancelled",
                "label": "Visitor Pass Cancelled",
                "severity": "info",
                "description": "A scheduled or active Visitor Pass was cancelled.",
            },
            {
                "value": "visitor_pass_created",
                "label": "Visitor Pass Created",
                "severity": "info",
                "description": "A new Visitor Pass was created.",
            },
            {
                "value": "visitor_pass_expired",
                "label": "Visitor Pass Expired",
                "severity": "warning",
                "description": "A Visitor Pass window elapsed without being used.",
            },
            {
                "value": "visitor_pass_used",
                "label": "Visitor Pass Used",
                "severity": "info",
                "description": "A Visitor Pass was matched to an arriving vehicle.",
            },
        ],
    },
]

VARIABLE_GROUPS: list[dict[str, Any]] = [
    {
        "group": "Person",
        "items": [
            {"name": "FirstName", "token": "@FirstName", "label": "First name"},
            {"name": "LastName", "token": "@LastName", "label": "Last name"},
            {"name": "DisplayName", "token": "@DisplayName", "label": "Display name"},
            {"name": "GroupName", "token": "@GroupName", "label": "Group name"},
            {"name": "FirstNamePossessive", "token": "@FirstNamePossessive", "label": "First name possessive"},
            {"name": "ObjectPronoun", "token": "@ObjectPronoun", "label": "Object pronoun"},
            {"name": "PossessiveDeterminer", "token": "@PossessiveDeterminer", "label": "Possessive determiner"},
        ],
    },
    {
        "group": "Vehicle",
        "items": [
            {"name": "Registration", "token": "@Registration", "label": "Registration"},
            {"name": "VehicleRegistrationNumber", "token": "@VehicleRegistrationNumber", "label": "Registration number"},
            {"name": "VehicleName", "token": "@VehicleName", "label": "Friendly vehicle name"},
            {"name": "VehicleDisplayName", "token": "@VehicleDisplayName", "label": "Vehicle display name"},
            {"name": "VehicleMake", "token": "@VehicleMake", "label": "Vehicle make"},
            {"name": "VehicleType", "token": "@VehicleType", "label": "Vehicle type"},
            {"name": "VehicleModel", "token": "@VehicleModel", "label": "Vehicle model"},
            {"name": "VehicleColor", "token": "@VehicleColor", "label": "Vehicle colour"},
            {"name": "VehicleColour", "token": "@VehicleColour", "label": "Vehicle colour"},
            {"name": "MotStatus", "token": "@MotStatus", "label": "MOT status"},
            {"name": "MotExpiry", "token": "@MotExpiry", "label": "MOT expiry"},
            {"name": "TaxStatus", "token": "@TaxStatus", "label": "Tax status"},
            {"name": "TaxExpiry", "token": "@TaxExpiry", "label": "Tax expiry"},
        ],
    },
    {
        "group": "Event",
        "items": [
            {"name": "Time", "token": "@Time", "label": "Event time"},
            {"name": "OccurredAt", "token": "@OccurredAt", "label": "Event timestamp"},
            {"name": "GateStatus", "token": "@GateStatus", "label": "Gate status"},
            {"name": "Direction", "token": "@Direction", "label": "Entry or exit"},
            {"name": "Decision", "token": "@Decision", "label": "Access decision"},
            {"name": "TimingClassification", "token": "@TimingClassification", "label": "Timing classification"},
            {"name": "Source", "token": "@Source", "label": "Event source"},
            {"name": "Severity", "token": "@Severity", "label": "Severity"},
            {"name": "EventType", "token": "@EventType", "label": "Event type"},
            {"name": "Subject", "token": "@Subject", "label": "Subject"},
            {"name": "Message", "token": "@Message", "label": "Message"},
            {"name": "MaintenanceModeReason", "token": "@MaintenanceModeReason", "label": "Maintenance mode reason"},
        ],
    },
    {
        "group": "Integration",
        "items": [
            {"name": "GarageDoor", "token": "@GarageDoor", "label": "Garage door"},
            {"name": "EntityId", "token": "@EntityId", "label": "Entity ID"},
        ],
    },
    {
        "group": "Visitor Pass",
        "items": [
            {
                "name": "VisitorPassVehicleRegistration",
                "token": "@VisitorPassVehicleRegistration",
                "label": "Visitor Pass vehicle registration",
            },
            {
                "name": "VisitorPassVehicleMake",
                "token": "@VisitorPassVehicleMake",
                "label": "Visitor Pass vehicle make",
            },
            {
                "name": "VisitorPassVehicleColour",
                "token": "@VisitorPassVehicleColour",
                "label": "Visitor Pass vehicle colour",
            },
            {
                "name": "VisitorPassDurationOnSite",
                "token": "@VisitorPassDurationOnSite",
                "label": "Visitor Pass duration on site",
            },
        ],
    },
    {
        "group": "Malfunction",
        "items": [
            {"name": "MalfunctionDuration", "token": "@MalfunctionDuration", "label": "Malfunction duration"},
            {"name": "MalfunctionOpenedTime", "token": "@MalfunctionOpenedTime", "label": "Gate opened time"},
            {"name": "MalfunctionFixAttemptTime", "token": "@MalfunctionFixAttemptTime", "label": "Latest fix attempt time"},
            {"name": "MalfunctionFixAttempts", "token": "@MalfunctionFixAttempts", "label": "Fix attempt count"},
            {"name": "MalfunctionResolutionTime", "token": "@MalfunctionResolutionTime", "label": "Resolution time"},
            {"name": "LastKnownVehicle", "token": "@LastKnownVehicle", "label": "Last known vehicle"},
        ],
    },
    {
        "group": "Leaderboard",
        "items": [
            {"name": "NewWinnerName", "token": "@NewWinnerName", "label": "New winner"},
            {"name": "OvertakenName", "token": "@OvertakenName", "label": "Overtaken person"},
            {"name": "ReadCount", "token": "@ReadCount", "label": "Read count"},
        ],
    },
]

MOCK_FACTS = {
    "message": "Steph arrived in the 2026 Tesla Model Y Dual Motor Long Range.",
    "first_name": "Steph",
    "last_name": "Smith",
    "display_name": "Steph Smith",
    "group_name": "Family",
    "vehicle_registration_number": "STEPH26",
    "registration_number": "STEPH26",
    "vehicle_display_name": "2026 Tesla Model Y Dual Motor Long Range",
    "vehicle_name": "2026 Tesla Model Y Dual Motor Long Range",
    "vehicle_make": "Tesla",
    "vehicle_type": "Car",
    "vehicle_model": "Model Y Dual Motor Long Range",
    "vehicle_color": "Pearl white",
    "vehicle_colour": "Pearl white",
    "detected_vehicle_type": "Car",
    "detected_vehicle_color": "Pearl white",
    "detected_vehicle_colour": "Pearl white",
    "mot_status": "Valid",
    "mot_expiry": "2026-10-14",
    "tax_status": "Taxed",
    "tax_expiry": "2027-01-01",
    "object_pronoun": "her",
    "possessive_determiner": "her",
    "direction": "entry",
    "decision": "granted",
    "source": "Driveway LPR",
    "timing_classification": "normal",
    "occurred_at": "2026-04-26T18:42:00+01:00",
    "gate_status": "opening",
    "garage_door": "Main garage door",
    "entity_id": "cover.main_garage_door",
    "new_winner_name": "Steph Smith",
    "overtaken_name": "Jason Smith",
    "read_count": "42",
    "maintenance_mode_reason": "Enabled by Jason from UI",
    "maintenance_mode_duration": "2 hours and 14 minutes",
    "malfunction_duration": "30 minutes",
    "malfunction_opened_time": "2026-04-26T07:30:00+01:00",
    "malfunction_fix_attempt_time": "2026-04-26T07:35:45+01:00",
    "malfunction_fix_attempts": "2",
    "malfunction_resolution_time": "",
    "last_known_vehicle": "Steph Smith exited in 2026 Tesla Model Y",
    "visitor_name": "Sarah",
    "visitor_pass_id": "visitor-pass-1",
    "visitor_pass_status": "used",
    "visitor_pass_vehicle_registration": "PE70DHX",
    "visitor_pass_vehicle_make": "Peugeot",
    "visitor_pass_vehicle_colour": "Silver",
    "visitor_pass_duration_on_site": "1h 25m",
}


class NotificationService:
    """DB-backed notification workflow engine.

    Legacy dynamic-setting rules are intentionally ignored. This service can be
    invoked directly for tests, and listens for normalized `notification.trigger`
    events for normal runtime delivery.
    """

    def __init__(self) -> None:
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        event_bus.subscribe(self._handle_realtime_event)
        self._started = True
        logger.info("notification_workflow_service_started")

    async def stop(self) -> None:
        if not self._started:
            return
        event_bus.unsubscribe(self._handle_realtime_event)
        self._started = False
        logger.info("notification_workflow_service_stopped")

    async def catalog(self) -> dict[str, Any]:
        config = await get_runtime_config()
        return {
            "triggers": TRIGGER_CATALOG,
            "variables": VARIABLE_GROUPS,
            "integrations": await self.available_integrations(config),
            "mock_context": context_variables(sample_notification_context()),
        }

    async def available_integrations(self, config) -> list[dict[str, Any]]:
        apprise_urls = [
            normalize_apprise_url(url)
            for url in split_apprise_urls(config.apprise_urls)
        ]
        apprise_endpoints = [
            summarize_apprise_url(index, url)
            for index, url in enumerate(apprise_urls)
        ]
        mobile_endpoints: list[dict[str, Any]] = []
        if apprise_endpoints:
            mobile_endpoints.append(
                {
                    "id": "apprise:*",
                    "provider": "Apprise",
                    "label": "All Apprise endpoints",
                    "detail": f"{len(apprise_endpoints)} configured destinations",
                }
            )
        mobile_endpoints.extend(
            {
                "id": str(endpoint["id"]),
                "provider": "Apprise",
                "label": str(endpoint["type"]),
                "detail": str(endpoint["preview"]),
            }
            for endpoint in apprise_endpoints
        )
        home_assistant_mobile_endpoints = await self._home_assistant_mobile_endpoint_catalog(config)
        mobile_endpoints.extend(home_assistant_mobile_endpoints)

        voice_endpoints = await self._voice_endpoint_catalog(config)
        discord_endpoints = await self._discord_endpoint_catalog()
        return [
            {
                "id": "mobile",
                "name": "Mobile Notification",
                "provider": "Apprise / Home Assistant",
                "configured": bool(apprise_urls or home_assistant_mobile_endpoints),
                "endpoints": mobile_endpoints,
            },
            {
                "id": "in_app",
                "name": "In-App Notification",
                "provider": "Dashboard realtime",
                "configured": True,
                "endpoints": [
                    {
                        "id": "dashboard",
                        "provider": "Dashboard",
                        "label": "All signed-in dashboards",
                        "detail": "Realtime in-app notification stream",
                    }
                ],
            },
            {
                "id": "voice",
                "name": "Voice Notification",
                "provider": "Home Assistant TTS",
                "configured": bool(voice_endpoints),
                "endpoints": voice_endpoints,
            },
            {
                "id": "discord",
                "name": "Discord",
                "provider": "Discord",
                "configured": bool(discord_endpoints),
                "endpoints": discord_endpoints,
            },
        ]

    async def notify(
        self,
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
        rules_override: list[dict[str, Any]] | None = None,
    ) -> ComposedNotification:
        if raise_on_failure or rules_override is not None:
            return await self.process_context(
                context,
                raise_on_failure=raise_on_failure,
                rules_override=rules_override,
            )

        await event_bus.publish("notification.trigger", notification_context_payload(context))
        return composed_from_context(context)

    async def process_context(
        self,
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
        rules_override: list[dict[str, Any]] | None = None,
    ) -> ComposedNotification:
        result = await self.process_context_with_result(
            context,
            raise_on_failure=raise_on_failure,
            rules_override=rules_override,
        )
        return result.notification

    async def process_context_with_result(
        self,
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
        rules_override: list[dict[str, Any]] | None = None,
    ) -> NotificationWorkflowResult:
        rules = await self._rules_for_context(context, rules_override)

        if not rules:
            await self._publish_workflow_skip(context, "no_matching_workflow")
            if raise_on_failure:
                raise NotificationDeliveryError("No active notification workflow matched this event.")
            return NotificationWorkflowResult(
                notification=composed_from_context(context),
                skipped_count=1,
                skipped_reasons=["no_matching_workflow"],
            )

        first_notification: ComposedNotification | None = None
        failures: list[str] = []
        delivered_count = 0
        failed_count = 0
        skipped_count = 0
        skipped_reasons: list[str] = []

        for rule in rules:
            try:
                condition_passed = await self.conditions_match(rule, context)
                if not condition_passed:
                    skipped_count += 1
                    skipped_reasons.append("conditions_not_met")
                    await self._publish_workflow_skip(context, "conditions_not_met", rule=rule)
                    continue
                result = await self.execute_rule_with_result(
                    rule,
                    context,
                    raise_on_failure=raise_on_failure,
                )
                first_notification = first_notification or result.notification
                delivered_count += result.delivered_count
                failed_count += result.failed_count
                skipped_count += result.skipped_count
                failures.extend(result.failures)
                skipped_reasons.extend(result.skipped_reasons)
                if result.delivered_count > 0:
                    await self._mark_rule_fired(rule)
            except NotificationDeliveryError as exc:
                failed_count += 1
                failures.append(f"{rule_name(rule)}: {exc}")
                logger.warning(
                    "notification_workflow_failed",
                    extra={"rule_id": rule_id(rule), "event_type": context.event_type, "error": str(exc)},
                )
                if raise_on_failure:
                    raise

        if delivered_count > 0 and first_notification:
            return NotificationWorkflowResult(
                notification=first_notification,
                delivered_count=delivered_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                failures=failures,
                skipped_reasons=skipped_reasons,
            )
        if not failures:
            await self._publish_workflow_skip(context, "no_workflow_actions_delivered")
            skipped_count += 1
            skipped_reasons.append("no_workflow_actions_delivered")
        if failures and raise_on_failure:
            raise NotificationDeliveryError("; ".join(failures))
        if raise_on_failure:
            raise NotificationDeliveryError("No notification workflow actions were delivered.")
        return NotificationWorkflowResult(
            notification=first_notification or composed_from_context(context),
            delivered_count=delivered_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            failures=failures,
            skipped_reasons=skipped_reasons,
        )

    async def _rules_for_context(
        self,
        context: NotificationContext,
        rules_override: list[dict[str, Any]] | None,
    ) -> list[NotificationRule | dict[str, Any]]:
        if rules_override is not None:
            return [normalize_rule_payload(rule) for rule in rules_override]
        async with AsyncSessionLocal() as session:
            return (
                await session.scalars(
                    select(NotificationRule)
                    .where(
                        NotificationRule.trigger_event == context.event_type,
                        NotificationRule.is_active.is_(True),
                    )
                    .order_by(NotificationRule.created_at)
                )
            ).all()

    async def _publish_workflow_skip(
        self,
        context: NotificationContext,
        reason: str,
        *,
        rule: NotificationRule | dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "event_type": context.event_type,
            "severity": context.severity,
            "subject": context.subject,
            "reason": reason,
            "delivered": False,
        }
        if rule is not None:
            payload.update({"rule_id": rule_id(rule), "rule_name": rule_name(rule)})
        self._record_notification_span("Notification Workflow Skipped", context, output_payload=payload)
        await event_bus.publish(
            "notification.skipped",
            {
                **payload,
                "malfunction_id": context.facts.get("malfunction_id"),
                "telemetry_trace_id": context.facts.get("telemetry_trace_id"),
            },
        )

    async def _mark_rule_fired(self, rule: NotificationRule | dict[str, Any]) -> None:
        if isinstance(rule, dict):
            return
        rule_id_value = getattr(rule, "id", None)
        if not rule_id_value:
            return
        fired_at = datetime.now(UTC)
        try:
            async with AsyncSessionLocal() as session:
                stored = await session.get(NotificationRule, rule_id_value)
                if not stored:
                    return
                stored.last_fired_at = fired_at
                await session.commit()
            rule.last_fired_at = fired_at
        except Exception as exc:
            logger.warning(
                "notification_last_fired_update_failed",
                extra={"rule_id": str(rule_id_value), "error": str(exc)},
            )

    async def conditions_match(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext,
    ) -> bool:
        conditions = rule_conditions(rule)
        if not conditions:
            return True
        occurred_at = context_occurred_at(context)
        config = await get_runtime_config()
        async with AsyncSessionLocal() as session:
            for condition in conditions:
                if not await self._condition_matches(session, condition, context, occurred_at, config):
                    return False
        return True

    async def execute_rule(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
    ) -> ComposedNotification:
        return (
            await self.execute_rule_with_result(
                rule,
                context,
                raise_on_failure=raise_on_failure,
            )
        ).notification

    async def execute_rule_with_result(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
    ) -> NotificationWorkflowResult:
        rendered = self.render_rule(rule, context)
        config = await get_runtime_config()
        first_notification: ComposedNotification | None = None
        failures: list[str] = []
        delivered_count = 0
        failed_count = 0
        skipped_count = 0
        skipped_reasons: list[str] = []

        for action in rendered["actions"]:
            if first_notification is None:
                first_notification = ComposedNotification(
                    title=str(action.get("title") or rendered["name"]),
                    body=str(action.get("message") or ""),
                )
            try:
                outcome = await self._deliver_action(action, context, config, rendered)
            except NotificationDeliveryError as exc:
                failed_count += 1
                failures.append(f"{action.get('type')}: {exc}")
                self._record_notification_span(
                    "Notification Action Failed",
                    context,
                    status="error",
                    error=str(exc),
                    output_payload={
                        **self._event_payload(rendered, action, context, False, str(exc)),
                        "reason": "delivery_failed",
                    },
                )
                await event_bus.publish(
                    "notification.failed",
                    self._event_payload(rendered, action, context, False, str(exc)),
                )
                if raise_on_failure:
                    raise
                continue
            if outcome.skipped:
                skipped_count += 1
                skipped_reasons.append(outcome.reason)
                skipped_payload = {
                    **self._event_payload(rendered, action, context, False, ""),
                    **outcome.metadata,
                    "reason": outcome.reason,
                    "message": outcome.message,
                }
                self._record_notification_span(
                    "Notification Action Suppressed",
                    context,
                    output_payload=skipped_payload,
                )
                await event_bus.publish("notification.skipped", skipped_payload)
                continue
            self._record_notification_span(
                "Notification Action Sent",
                context,
                output_payload={
                    **self._event_payload(rendered, action, context, True, ""),
                    "reason": "delivered",
                },
            )
            await event_bus.publish(
                "notification.sent",
                self._event_payload(rendered, action, context, True, ""),
            )
            delivered_count += 1

        if failures and raise_on_failure:
            raise NotificationDeliveryError("; ".join(failures))
        if not first_notification:
            if raise_on_failure:
                raise NotificationDeliveryError("Workflow has no notification actions.")
            first_notification = composed_from_context(context)
        return NotificationWorkflowResult(
            notification=first_notification,
            delivered_count=delivered_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            failures=failures,
            skipped_reasons=skipped_reasons,
        )

    def render_rule(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext | None = None,
    ) -> dict[str, Any]:
        active_context = context or sample_notification_context(rule_trigger_event(rule))
        variables = context_variables(active_context)
        rendered_actions = []
        for action in rule_actions(rule):
            action_type = str(action.get("type") or "")
            media = normalize_media(action.get("media"))
            title_template = str(action.get("title_template") or "")
            message_template = str(action.get("message_template") or "")
            rendered_actions.append(
                {
                    "id": str(action.get("id") or f"action-{len(rendered_actions) + 1}"),
                    "type": action_type,
                    "target_mode": str(action.get("target_mode") or "all"),
                    "target_ids": normalize_string_list(action.get("target_ids")),
                    "title": render_template(title_template, variables),
                    "message": render_template(message_template, variables),
                    "title_template": title_template,
                    "message_template": message_template,
                    "media": media,
                    "snapshot": snapshot_payload(media),
                }
            )
        return {
            "id": rule_id(rule),
            "name": rule_name(rule),
            "trigger_event": rule_trigger_event(rule),
            "is_active": rule_is_active(rule),
            "conditions": rule_conditions(rule),
            "actions": rendered_actions,
        }

    async def preview_rule(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext | None = None,
    ) -> dict[str, Any]:
        return self.render_rule(rule, context or sample_notification_context(rule_trigger_event(rule)))

    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        if event.type == "notification.trigger":
            await self.process_context(notification_context_from_payload(event.payload))
            return
        for context in visitor_pass_notification_contexts_from_event(event):
            await self.process_context(context)

    async def _condition_matches(
        self,
        session: AsyncSession,
        condition: dict[str, Any],
        context: NotificationContext,
        occurred_at: datetime,
        config,
    ) -> bool:
        condition_type = str(condition.get("type") or "")
        if condition_type == "schedule":
            schedule_id = str(condition.get("schedule_id") or "")
            try:
                parsed_schedule_id = uuid.UUID(schedule_id)
            except ValueError:
                return False
            schedule = await session.get(Schedule, parsed_schedule_id)
            if not schedule:
                return False
            return schedule_allows_at(schedule, occurred_at, config.site_timezone)

        if condition_type == "presence":
            rows = (
                await session.scalars(select(Presence))
            ).all()
            present_ids = {
                str(row.person_id)
                for row in rows
                if row.state == PresenceState.PRESENT
            }
            return presence_condition_matches(condition, present_ids)

        logger.warning(
            "notification_condition_unknown",
            extra={"condition_type": condition_type, "event_type": context.event_type},
        )
        return False

    async def _deliver_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        config,
        rendered_rule: dict[str, Any],
    ) -> NotificationActionOutcome:
        action_type = str(action.get("type") or "")
        if action_type == "mobile":
            await self._send_mobile(action, context, config)
            return NotificationActionOutcome(delivered=True)
        if action_type == "in_app":
            await event_bus.publish(
                "notification.in_app",
                {
                    "rule_id": rendered_rule["id"],
                    "title": action.get("title") or rendered_rule["name"],
                    "body": action.get("message") or "",
                    "event_type": context.event_type,
                    "severity": context.severity,
                    "snapshot": action.get("snapshot") or None,
                },
            )
            return NotificationActionOutcome(delivered=True)
        if action_type == "voice":
            return await self._send_voice(action, config)
        if action_type == "discord":
            await self._send_discord(action, context)
            return NotificationActionOutcome(delivered=True)
        raise NotificationDeliveryError(f"Unsupported notification action: {action_type}")

    async def _send_mobile(self, action: dict[str, Any], context: NotificationContext, config) -> None:
        urls = self._select_apprise_urls(config.apprise_urls, action)
        home_assistant_targets = await self._select_home_assistant_mobile_targets(config, action)
        if not urls and not home_assistant_targets:
            raise NotificationDeliveryError("No mobile notification endpoints are configured or selected.")
        snapshot = await self._snapshot_attachment(action.get("media") or {})
        attachments = [snapshot.path] if snapshot else []
        failures: list[str] = []
        delivered_any = False
        try:
            delivered_any = await self._send_mobile_apprise(action, context, urls, attachments, failures)
            delivered_any = (
                await self._send_mobile_home_assistant(
                    action,
                    context,
                    home_assistant_targets,
                    snapshot,
                    failures,
                )
                or delivered_any
            )
        finally:
            self._cleanup_mobile_snapshot(snapshot, home_assistant_targets)
        if failures:
            raise NotificationDeliveryError("; ".join(failures))
        if not delivered_any:
            raise NotificationDeliveryError("No mobile notification endpoints were delivered.")

    async def _send_mobile_apprise(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        urls: list[str],
        attachments: list[str],
        failures: list[str],
    ) -> bool:
        if not urls:
            return False
        sender = AppriseNotificationSender(urls="\n".join(urls))
        try:
            await sender.send(
                str(action.get("title") or context.subject),
                str(action.get("message") or ""),
                context,
                attachments=attachments,
            )
            return True
        except NotificationDeliveryError as exc:
            failures.append(f"Apprise: {exc}")
            return False

    async def _send_mobile_home_assistant(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        targets: list[str],
        snapshot: NotificationSnapshotAttachment | None,
        failures: list[str],
    ) -> bool:
        if not targets:
            return False
        if snapshot and not snapshot.public_url:
            failures.append("Home Assistant: IACS_PUBLIC_BASE_URL must be configured to attach camera snapshots.")
            return False
        image_url = snapshot.public_url if snapshot else None
        image_content_type = snapshot.content_type if snapshot else None
        notifier = HomeAssistantMobileAppNotifier()
        delivered_any = False
        for target in targets:
            try:
                await notifier.send(
                    HomeAssistantMobileAppTarget(target),
                    str(action.get("title") or context.subject),
                    str(action.get("message") or ""),
                    context,
                    image_url=image_url,
                    image_content_type=image_content_type,
                )
                delivered_any = True
            except NotificationDeliveryError as exc:
                failures.append(f"{target}: {exc}")
        return delivered_any

    def _cleanup_mobile_snapshot(
        self,
        snapshot: NotificationSnapshotAttachment | None,
        home_assistant_targets: list[str],
    ) -> None:
        if snapshot and not (home_assistant_targets and snapshot.public_url):
            delete_notification_snapshot(snapshot.path)

    async def _send_discord(self, action: dict[str, Any], context: NotificationContext) -> None:
        attachments = await self._snapshot_attachments(action.get("media") or {})
        try:
            await get_discord_messaging_service().send_notification_action(
                action,
                context,
                attachment_paths=attachments,
            )
        finally:
            for path in attachments:
                delete_notification_snapshot(path)

    async def _send_voice(self, action: dict[str, Any], config) -> NotificationActionOutcome:
        targets = await self._select_voice_targets(config, action)
        if not targets:
            raise NotificationDeliveryError("No Home Assistant media player is configured or selected.")
        spoken_message = apply_vehicle_tts_phonetics(str(action.get("message") or ""))
        suppression = await self._voice_announcements_preflight()
        if suppression:
            return suppression

        announcer = HomeAssistantTtsAnnouncer()
        failures: list[str] = []
        delivered_any = False
        for target in targets:
            try:
                await announcer.announce(AnnouncementTarget(target), spoken_message)
                delivered_any = True
            except Exception as exc:
                failures.append(f"{target}: {exc}")
        if failures:
            raise NotificationDeliveryError("; ".join(failures))
        if not delivered_any:
            raise NotificationDeliveryError("No Home Assistant media player endpoints were delivered.")
        return NotificationActionOutcome(delivered=True)

    async def _voice_announcements_preflight(self) -> NotificationActionOutcome | None:
        try:
            state = await HomeAssistantClient().get_state(HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID)
        except Exception as exc:
            logger.warning(
                "voice_notification_announcements_state_unavailable",
                extra={"entity_id": HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID, "error": str(exc)},
            )
            return NotificationActionOutcome(
                delivered=False,
                skipped=True,
                reason="announcements_state_unavailable",
                message=(
                    "Voice Notification suppressed: "
                    f"`{HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID}` state could not be verified."
                ),
                metadata={
                    "home_assistant_entity_id": HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID,
                    "home_assistant_state": None,
                    "fail_safe": True,
                },
            )

        normalized_state = str(state.state or "").strip().lower()
        if normalized_state == "on":
            return None
        if normalized_state == "off":
            logger.info(
                "voice_notification_suppressed",
                extra={
                    "entity_id": HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID,
                    "state": normalized_state,
                    "suppression_message": VOICE_ANNOUNCEMENTS_DISABLED_MESSAGE,
                },
            )
            return NotificationActionOutcome(
                delivered=False,
                skipped=True,
                reason="announcements_disabled",
                message=VOICE_ANNOUNCEMENTS_DISABLED_MESSAGE,
                metadata={
                    "home_assistant_entity_id": HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID,
                    "home_assistant_state": normalized_state,
                },
            )
        logger.info(
            "voice_notification_suppressed",
            extra={
                "entity_id": HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID,
                "state": normalized_state or "unknown",
            },
        )
        return NotificationActionOutcome(
            delivered=False,
            skipped=True,
            reason="announcements_not_enabled",
            message=(
                "Voice Notification suppressed: "
                f"`{HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID}` is not enabled."
            ),
            metadata={
                "home_assistant_entity_id": HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID,
                "home_assistant_state": normalized_state or "unknown",
                "fail_safe": True,
            },
        )

    def _select_apprise_urls(self, configured: str, action: dict[str, Any]) -> list[str]:
        urls = [normalize_apprise_url(url) for url in split_apprise_urls(configured)]
        if not urls:
            return []
        target_mode = str(action.get("target_mode") or "all")
        endpoint_ids = normalize_string_list(action.get("target_ids"))
        if target_mode == "all" or "apprise:*" in endpoint_ids or not endpoint_ids:
            return urls
        chosen: list[str] = []
        for endpoint_id in endpoint_ids:
            if not endpoint_id.startswith("apprise:"):
                continue
            try:
                index = int(endpoint_id.split(":", 1)[1])
            except ValueError:
                continue
            if 0 <= index < len(urls):
                chosen.append(urls[index])
        return chosen

    async def _select_home_assistant_mobile_targets(self, config, action: dict[str, Any]) -> list[str]:
        target_mode = str(action.get("target_mode") or "all")
        endpoint_ids = normalize_string_list(action.get("target_ids"))
        if target_mode == "all" or "home_assistant_mobile:*" in endpoint_ids:
            return await self._all_home_assistant_mobile_targets(config)

        targets: list[str] = []
        for endpoint_id in endpoint_ids:
            if not endpoint_id.startswith("home_assistant_mobile:"):
                continue
            target = endpoint_id.split(":", 1)[1]
            if target and target != "*":
                if not target.startswith("notify.mobile_app_"):
                    raise NotificationDeliveryError(
                        "Home Assistant mobile targets must be notify.mobile_app_* services."
                    )
                targets.append(target)
        return list(dict.fromkeys(targets))

    async def _select_voice_targets(self, config, action: dict[str, Any]) -> list[str]:
        target_mode = str(action.get("target_mode") or "all")
        endpoint_ids = normalize_string_list(action.get("target_ids"))
        if target_mode == "all" or "home_assistant_tts:*" in endpoint_ids:
            targets = await self._all_media_player_targets(config)
            if targets:
                return targets
            return [config.home_assistant_default_media_player] if config.home_assistant_default_media_player else []

        targets: list[str] = []
        for endpoint_id in endpoint_ids:
            if not endpoint_id.startswith("home_assistant_tts:"):
                continue
            target = endpoint_id.split(":", 1)[1]
            if target == "default":
                target = config.home_assistant_default_media_player
            if target and target != "*":
                targets.append(target)
        if not targets and config.home_assistant_default_media_player:
            targets.append(config.home_assistant_default_media_player)
        return targets

    async def _voice_endpoint_catalog(self, config) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        targets = await self._all_media_player_targets(config)
        if targets:
            endpoints.append(
                {
                    "id": "home_assistant_tts:*",
                    "provider": "Home Assistant",
                    "label": "All media players",
                    "detail": f"{len(targets)} media_player entities",
                }
            )
            endpoints.extend(
                {
                    "id": f"home_assistant_tts:{target}",
                    "provider": "Home Assistant",
                    "label": target.split(".", 1)[-1].replace("_", " ").title(),
                    "detail": target,
                }
                for target in targets
            )
        elif config.home_assistant_default_media_player:
            endpoints.append(
                {
                    "id": f"home_assistant_tts:{config.home_assistant_default_media_player}",
                    "provider": "Home Assistant",
                    "label": "Default media player",
                    "detail": config.home_assistant_default_media_player,
                }
            )
        return endpoints

    async def _discord_endpoint_catalog(self) -> list[dict[str, Any]]:
        try:
            channels = await get_discord_messaging_service().available_channels()
        except Exception as exc:
            logger.debug("discord_endpoint_catalog_failed", extra={"error": str(exc)})
            return []
        endpoints = [
            {
                "id": "discord:*",
                "provider": "Discord",
                "label": "Default Discord channel",
                "detail": "Configured Discord default notification channel",
            }
        ] if channels else []
        endpoints.extend(
            {
                "id": f"discord:{channel['id']}",
                "provider": "Discord",
                "label": channel.get("label") or channel.get("name") or channel["id"],
                "detail": f"Channel ID {channel['id']}",
            }
            for channel in channels
        )
        return endpoints

    async def _home_assistant_mobile_endpoint_catalog(self, config) -> list[dict[str, Any]]:
        targets = await self._all_home_assistant_mobile_targets(config)
        if not targets:
            return []

        endpoints: list[dict[str, Any]] = [
            {
                "id": "home_assistant_mobile:*",
                "provider": "Home Assistant",
                "label": "All Home Assistant mobile apps",
                "detail": f"{len(targets)} notify.mobile_app services",
            }
        ]
        person_labels = await self._home_assistant_mobile_person_labels()
        for target in targets:
            endpoints.append(
                {
                    "id": f"home_assistant_mobile:{target}",
                    "provider": "Home Assistant",
                    "label": person_labels.get(target) or target.split(".", 1)[-1].replace("_", " ").title(),
                    "detail": target,
                }
            )
        return endpoints

    async def _home_assistant_mobile_person_labels(self) -> dict[str, str]:
        async with AsyncSessionLocal() as session:
            people = (
                await session.scalars(
                    select(Person).where(Person.home_assistant_mobile_app_notify_service.is_not(None))
                )
            ).all()
        return {
            str(person.home_assistant_mobile_app_notify_service): person.display_name
            for person in people
            if person.home_assistant_mobile_app_notify_service
        }

    async def _all_home_assistant_mobile_targets(self, config) -> list[str]:
        configured_targets = await self._configured_home_assistant_mobile_targets()
        if not (config.home_assistant_url and config.home_assistant_token):
            return configured_targets
        try:
            services = await HomeAssistantClient().list_services()
        except Exception as exc:
            logger.debug("notification_mobile_app_discovery_failed", extra={"error": str(exc)})
            return configured_targets
        discovered_targets = sorted(
            service.service_id
            for service in services
            if service.service_id.startswith("notify.mobile_app_")
        )
        return list(dict.fromkeys([*configured_targets, *discovered_targets]))

    async def _configured_home_assistant_mobile_targets(self) -> list[str]:
        async with AsyncSessionLocal() as session:
            people = (
                await session.scalars(
                    select(Person).where(Person.home_assistant_mobile_app_notify_service.is_not(None))
                )
            ).all()
        return list(
            dict.fromkeys(
                str(person.home_assistant_mobile_app_notify_service)
                for person in people
                if person.home_assistant_mobile_app_notify_service
            )
        )

    async def _all_media_player_targets(self, config) -> list[str]:
        if not (config.home_assistant_url and config.home_assistant_token):
            return []
        try:
            states = await HomeAssistantClient().list_states()
        except Exception as exc:
            logger.debug("notification_media_player_discovery_failed", extra={"error": str(exc)})
            return []
        return sorted(state.entity_id for state in states if state.entity_id.startswith("media_player."))

    async def _snapshot_attachment(self, media: dict[str, Any]) -> NotificationSnapshotAttachment | None:
        if not media.get("attach_camera_snapshot") or not media.get("camera_id"):
            return None
        camera_id = str(media["camera_id"])
        try:
            snapshot = await get_unifi_protect_service().snapshot(camera_id, width=960, height=540)
        except Exception as exc:
            raise NotificationDeliveryError(f"Unable to capture notification snapshot: {exc}") from exc

        stored = store_notification_snapshot(snapshot.content, snapshot.content_type)
        return NotificationSnapshotAttachment(
            path=str(stored.path),
            content_type=stored.content_type,
            public_url=notification_snapshot_absolute_url(stored),
        )

    async def _snapshot_attachments(self, media: dict[str, Any]) -> list[str]:
        snapshot = await self._snapshot_attachment(media)
        return [snapshot.path] if snapshot else []

    def _record_notification_span(
        self,
        name: str,
        context: NotificationContext,
        *,
        output_payload: dict[str, Any],
        status: str = "ok",
        error: str | Exception | None = None,
    ) -> None:
        trace_id = str(context.facts.get("telemetry_trace_id") or "").strip()
        if not trace_id:
            return
        telemetry.record_span(
            name,
            trace_id=trace_id,
            category=TELEMETRY_CATEGORY_INTEGRATIONS,
            status=status,
            attributes={
                "event_type": context.event_type,
                "subject": context.subject,
                "severity": context.severity,
                "access_event_id": context.facts.get("access_event_id"),
            },
            output_payload=output_payload,
            error=error,
        )

    def _event_payload(
        self,
        rule: dict[str, Any],
        action: dict[str, Any],
        context: NotificationContext,
        delivered: bool,
        error: str,
    ) -> dict[str, Any]:
        return {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "channel": action.get("type"),
            "title": action.get("title") or rule["name"],
            "body": action.get("message") or "",
            "event_type": context.event_type,
            "severity": context.severity,
            "configured": True,
            "delivered": delivered,
            "error": error,
            "malfunction_id": context.facts.get("malfunction_id"),
            "telemetry_trace_id": context.facts.get("telemetry_trace_id"),
        }


def notification_context_payload(context: NotificationContext) -> dict[str, Any]:
    return {
        "event_type": context.event_type,
        "subject": context.subject,
        "severity": context.severity,
        "facts": dict(context.facts),
    }


def visitor_pass_notification_contexts_from_event(event: RealtimeEvent) -> list[NotificationContext]:
    if not event.type.startswith("visitor_pass."):
        return []
    payload = event.payload if isinstance(event.payload, dict) else {}
    visitor_pass = payload.get("visitor_pass") if isinstance(payload.get("visitor_pass"), dict) else None
    if not visitor_pass:
        return []

    if event.type == "visitor_pass.created":
        event_types = ["visitor_pass_created"]
    elif event.type == "visitor_pass.cancelled":
        event_types = ["visitor_pass_cancelled"]
    elif event.type == "visitor_pass.status_changed":
        if str(visitor_pass.get("status") or "").strip().lower() != "expired":
            return []
        event_types = ["visitor_pass_expired"]
    elif event.type == "visitor_pass.used":
        event_types = ["visitor_pass_used", "visitor_pass_vehicle_arrived"]
    elif event.type == "visitor_pass.departure_recorded":
        event_types = ["visitor_pass_vehicle_exited"]
    else:
        return []

    source = str(payload.get("source") or visitor_pass.get("creation_source") or "visitor_pass")
    return [
        NotificationContext(
            event_type=event_type,
            subject=_visitor_pass_notification_subject(event_type, visitor_pass),
            severity=trigger_severity(event_type),
            facts=_visitor_pass_notification_facts(event_type, visitor_pass, source=source),
        )
        for event_type in event_types
    ]


def _visitor_pass_notification_facts(
    event_type: str,
    visitor_pass: dict[str, Any],
    *,
    source: str,
) -> dict[str, str]:
    plate = _visitor_pass_text(visitor_pass.get("number_plate"))
    make = _visitor_pass_text(visitor_pass.get("vehicle_make"))
    colour = _visitor_pass_text(visitor_pass.get("vehicle_colour"))
    duration = _visitor_pass_text(visitor_pass.get("duration_human")) or _duration_label_from_seconds(
        visitor_pass.get("duration_on_site_seconds")
    )
    occurred_at = _visitor_pass_occurred_at(event_type, visitor_pass)
    access_event_id = (
        _visitor_pass_text(visitor_pass.get("departure_event_id"))
        if event_type == "visitor_pass_vehicle_exited"
        else _visitor_pass_text(visitor_pass.get("arrival_event_id"))
    )
    return {
        "message": _visitor_pass_notification_message(event_type, visitor_pass),
        "subject": _visitor_pass_notification_subject(event_type, visitor_pass),
        "visitor_name": _visitor_pass_name(visitor_pass),
        "display_name": _visitor_pass_name(visitor_pass),
        "visitor_pass_id": _visitor_pass_text(visitor_pass.get("id")),
        "visitor_pass_status": _visitor_pass_text(visitor_pass.get("status")),
        "visitor_pass_creation_source": _visitor_pass_text(visitor_pass.get("creation_source")),
        "visitor_pass_source": source,
        "visitor_pass_expected_time": _visitor_pass_text(visitor_pass.get("expected_time")),
        "visitor_pass_window_start": _visitor_pass_text(visitor_pass.get("window_start")),
        "visitor_pass_window_end": _visitor_pass_text(visitor_pass.get("window_end")),
        "visitor_pass_valid_from": _visitor_pass_text(visitor_pass.get("valid_from")),
        "visitor_pass_valid_until": _visitor_pass_text(visitor_pass.get("valid_until")),
        "visitor_pass_vehicle_registration": plate,
        "visitor_pass_vehicle_make": make,
        "visitor_pass_vehicle_colour": colour,
        "visitor_pass_duration_on_site": duration,
        "visitor_pass_duration_on_site_seconds": _visitor_pass_text(visitor_pass.get("duration_on_site_seconds")),
        "vehicle_registration_number": plate,
        "registration_number": plate,
        "vehicle_make": make,
        "vehicle_color": colour,
        "vehicle_colour": colour,
        "duration_human": duration,
        "duration_on_site_seconds": _visitor_pass_text(visitor_pass.get("duration_on_site_seconds")),
        "access_event_id": access_event_id,
        "arrival_event_id": _visitor_pass_text(visitor_pass.get("arrival_event_id")),
        "departure_event_id": _visitor_pass_text(visitor_pass.get("departure_event_id")),
        "telemetry_trace_id": _visitor_pass_text(visitor_pass.get("telemetry_trace_id")),
        "occurred_at": occurred_at,
        "source": source,
    }


def _visitor_pass_notification_subject(event_type: str, visitor_pass: dict[str, Any]) -> str:
    visitor_name = _visitor_pass_name(visitor_pass)
    if event_type == "visitor_pass_created":
        return f"Visitor Pass created for {visitor_name}"
    if event_type == "visitor_pass_cancelled":
        return f"Visitor Pass cancelled for {visitor_name}"
    if event_type == "visitor_pass_expired":
        return f"Visitor Pass expired for {visitor_name}"
    if event_type in {"visitor_pass_used", "visitor_pass_vehicle_arrived"}:
        return f"Visitor Pass vehicle arrived for {visitor_name}"
    if event_type == "visitor_pass_vehicle_exited":
        return f"Visitor Pass vehicle exited for {visitor_name}"
    return f"Visitor Pass update for {visitor_name}"


def _visitor_pass_notification_message(event_type: str, visitor_pass: dict[str, Any]) -> str:
    visitor_name = _visitor_pass_name(visitor_pass)
    vehicle = _visitor_pass_vehicle_label(visitor_pass)
    duration = _visitor_pass_text(visitor_pass.get("duration_human")) or _duration_label_from_seconds(
        visitor_pass.get("duration_on_site_seconds")
    )
    if event_type == "visitor_pass_created":
        return f"Visitor Pass created for {visitor_name}."
    if event_type == "visitor_pass_cancelled":
        return f"Visitor Pass for {visitor_name} was cancelled."
    if event_type == "visitor_pass_expired":
        return f"Visitor Pass for {visitor_name} expired without a matching vehicle detection."
    if event_type == "visitor_pass_used":
        return f"Visitor Pass for {visitor_name} was used{f' by {vehicle}' if vehicle else ''}."
    if event_type == "visitor_pass_vehicle_arrived":
        return f"{visitor_name} arrived{f' in {vehicle}' if vehicle else ''}."
    if event_type == "visitor_pass_vehicle_exited":
        duration_suffix = f" after {duration}" if duration else ""
        return f"{visitor_name} exited{f' in {vehicle}' if vehicle else ''}{duration_suffix}."
    return f"Visitor Pass updated for {visitor_name}."


def _visitor_pass_occurred_at(event_type: str, visitor_pass: dict[str, Any]) -> str:
    if event_type == "visitor_pass_created":
        return _visitor_pass_text(visitor_pass.get("created_at"))
    if event_type == "visitor_pass_cancelled":
        return _visitor_pass_text(visitor_pass.get("updated_at"))
    if event_type == "visitor_pass_expired":
        return _visitor_pass_text(
            visitor_pass.get("window_end")
            or visitor_pass.get("valid_until")
            or visitor_pass.get("updated_at")
        )
    if event_type in {"visitor_pass_used", "visitor_pass_vehicle_arrived"}:
        return _visitor_pass_text(visitor_pass.get("arrival_time") or visitor_pass.get("updated_at"))
    if event_type == "visitor_pass_vehicle_exited":
        return _visitor_pass_text(visitor_pass.get("departure_time") or visitor_pass.get("updated_at"))
    return _visitor_pass_text(visitor_pass.get("updated_at") or visitor_pass.get("created_at"))


def _visitor_pass_vehicle_label(visitor_pass: dict[str, Any]) -> str:
    plate = _visitor_pass_text(visitor_pass.get("number_plate"))
    make = _visitor_pass_text(visitor_pass.get("vehicle_make"))
    colour = _visitor_pass_text(visitor_pass.get("vehicle_colour"))
    description = " ".join(part for part in [colour, make] if part)
    if description and plate:
        return f"{description} with registration {plate}"
    return description or plate


def _visitor_pass_name(visitor_pass: dict[str, Any]) -> str:
    return _visitor_pass_text(visitor_pass.get("visitor_name")) or "Unknown visitor"


def _visitor_pass_text(value: Any) -> str:
    return "" if value is None else str(value)


def notification_context_from_payload(payload: dict[str, Any]) -> NotificationContext:
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    return NotificationContext(
        event_type=str(payload.get("event_type") or payload.get("trigger_event") or "integration_test"),
        subject=str(payload.get("subject") or facts.get("subject") or "Notification event"),
        severity=str(payload.get("severity") or facts.get("severity") or "info"),
        facts={str(key): "" if value is None else str(value) for key, value in facts.items()},
    )


def sample_notification_context(trigger_event: str | None = None) -> NotificationContext:
    event_type = trigger_event or "authorized_entry"
    facts = dict(MOCK_FACTS)
    if event_type == "unauthorized_plate":
        facts.update(
            {
                "message": "An unknown Grey Tesla car with registration AB12CDE was detected at the gate.",
                "first_name": "",
                "last_name": "",
                "display_name": "",
                "group_name": "",
                "vehicle_registration_number": "AB12CDE",
                "registration_number": "AB12CDE",
                "vehicle_display_name": "AB12CDE",
                "vehicle_name": "AB12CDE",
                "vehicle_make": "Tesla",
                "vehicle_type": "Car",
                "vehicle_model": "",
                "vehicle_color": "Grey",
                "vehicle_colour": "Grey",
                "detected_vehicle_type": "Car",
                "detected_vehicle_color": "Grey",
                "detected_vehicle_colour": "Grey",
                "decision": "denied",
            }
        )
    return NotificationContext(
        event_type=event_type,
        subject="AB12CDE" if event_type == "unauthorized_plate" else "Steph arrived at the gate",
        severity=trigger_severity(event_type),
        facts=facts,
    )


def composed_from_context(context: NotificationContext) -> ComposedNotification:
    variables = context_variables(context)
    return ComposedNotification(
        title=context.subject,
        body=variables.get("Message") or context.subject,
    )


def context_variables(context: NotificationContext) -> dict[str, str]:
    facts = {
        _canonical_key(key): "" if value is None else str(value)
        for key, value in context.facts.items()
    }

    def pick(*keys: str, default: str = "") -> str:
        for key in keys:
            value = facts.get(_canonical_key(key))
            if value:
                return value
        return default

    display_name = pick("display_name", "person", "person_name")
    first_name = pick("first_name", "person_first_name")
    last_name = pick("last_name", "person_last_name")
    if display_name and not first_name:
        first_name = display_name.split(" ", 1)[0]
    if display_name and not last_name and " " in display_name:
        last_name = display_name.split(" ", 1)[1]

    visitor_pass_registration = pick(
        "visitor_pass_vehicle_registration",
        "visitor_pass_registration_number",
        "number_plate",
        "vehicle_registration_number",
        "registration_number",
    )
    visitor_pass_make = pick("visitor_pass_vehicle_make", "visitor_pass_make", "vehicle_make", "make")
    visitor_pass_colour = pick(
        "visitor_pass_vehicle_colour",
        "visitor_pass_vehicle_color",
        "visitor_pass_colour",
        "visitor_pass_color",
        "vehicle_colour",
        "vehicle_color",
        "colour",
        "color",
    )
    visitor_pass_duration = pick("visitor_pass_duration_on_site", "duration_human", "duration_on_site")
    if not visitor_pass_duration:
        visitor_pass_duration = _duration_label_from_seconds(
            pick("visitor_pass_duration_on_site_seconds", "duration_on_site_seconds")
        )

    vehicle_name = pick(
        "vehicle_name",
        "vehicle_display_name",
        "vehicle_description",
        "visitor_pass_vehicle_make",
        "visitor_pass_vehicle_registration",
        "vehicle_make",
        "make",
        "registration_number",
        default=context.subject,
    )
    occurred_at = pick("occurred_at", "created_at")
    if context.event_type == "unauthorized_plate":
        vehicle_color = pick(
            "detected_vehicle_colour",
            "detected_vehicle_color",
            "observed_vehicle_colour",
            "observed_vehicle_color",
            "vehicle_colour",
            "vehicle_color",
            "colour",
            "color",
        )
    else:
        vehicle_color = pick(
            "visitor_pass_vehicle_colour",
            "visitor_pass_vehicle_color",
            "vehicle_color",
            "vehicle_colour",
            "detected_vehicle_color",
            "detected_vehicle_colour",
            "color",
            "colour",
        )
    return {
        "FirstName": first_name,
        "FirstNamePossessive": _possessive(first_name),
        "ObjectPronoun": pick("object_pronoun", "pronoun_object", default="them"),
        "PossessiveDeterminer": pick("possessive_determiner", "pronoun_possessive", default="their"),
        "LastName": last_name,
        "DisplayName": display_name or first_name or "Unknown visitor",
        "GroupName": pick("group_name", "group"),
        "Registration": pick(
            "visitor_pass_vehicle_registration",
            "vehicle_registration_number",
            "registration_number",
            "vrn",
            default=context.subject,
        ),
        "VehicleRegistrationNumber": pick(
            "visitor_pass_vehicle_registration",
            "vehicle_registration_number",
            "registration_number",
            "vrn",
            default=context.subject,
        ),
        "VehicleName": vehicle_name,
        "VehicleDisplayName": vehicle_name,
        "VehicleMake": pick("visitor_pass_vehicle_make", "vehicle_make", "make"),
        "VehicleType": pick("vehicle_type", "detected_vehicle_type", "observed_vehicle_type"),
        "VehicleModel": pick("vehicle_model", "model"),
        "VehicleColor": vehicle_color,
        "VehicleColour": vehicle_color,
        "MotStatus": pick("mot_status", "motStatus"),
        "MotExpiry": pick("mot_expiry", "motExpiry", "mot_expiry_date"),
        "TaxStatus": pick("tax_status", "taxStatus"),
        "TaxExpiry": pick("tax_expiry", "taxExpiry", "tax_due_date", "taxDueDate"),
        "Direction": pick("direction"),
        "Decision": pick("decision"),
        "TimingClassification": pick("timing_classification"),
        "Source": pick("source"),
        "Severity": context.severity.title(),
        "EventType": context.event_type.replace("_", " ").title(),
        "Subject": context.subject,
        "Message": pick("message", default=context.subject),
        "OccurredAt": occurred_at,
        "Time": _time_label(occurred_at),
        "GateStatus": pick("gate_status", "gate_state"),
        "GarageDoor": pick("garage_door"),
        "EntityId": pick("entity_id"),
        "VisitorPassVehicleRegistration": visitor_pass_registration,
        "VisitorPassVehicleMake": visitor_pass_make,
        "VisitorPassVehicleColour": visitor_pass_colour,
        "VisitorPassDurationOnSite": visitor_pass_duration,
        "NewWinnerName": pick("new_winner_name", "winner_name"),
        "OvertakenName": pick("overtaken_name", "previous_winner_name"),
        "ReadCount": pick("read_count", "leaderboard_read_count"),
        "MaintenanceModeReason": pick("maintenance_mode_reason", "maintenance_reason", "reason"),
        "MalfunctionDuration": pick("malfunction_duration"),
        "MalfunctionOpenedTime": pick("malfunction_opened_time"),
        "MalfunctionFixAttemptTime": pick("malfunction_fix_attempt_time"),
        "MalfunctionFixAttempts": pick("malfunction_fix_attempts"),
        "MalfunctionResolutionTime": pick("malfunction_resolution_time"),
        "LastKnownVehicle": pick("last_known_vehicle"),
    }


def render_template(template: str, variables: dict[str, str]) -> str:
    by_canonical = {_canonical_key(key): value for key, value in variables.items()}

    def replace_token(match: re.Match[str]) -> str:
        return by_canonical.get(_canonical_key(match.group(1)), "")

    rendered = AT_TOKEN_PATTERN.sub(replace_token, template)
    rendered = LEGACY_TOKEN_PATTERN.sub(replace_token, rendered)
    return rendered.strip()


def context_occurred_at(context: NotificationContext) -> datetime:
    raw = context.facts.get("occurred_at") or context.facts.get("created_at") or ""
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(tz=UTC)


def snapshot_payload(media: dict[str, Any]) -> dict[str, str | bool] | None:
    if not media.get("attach_camera_snapshot") or not media.get("camera_id"):
        return None
    camera_id = str(media["camera_id"])
    return {
        "enabled": True,
        "camera_id": camera_id,
        "image_url": f"/api/v1/integrations/unifi-protect/cameras/{camera_id}/snapshot?width=960&height=540",
    }


def normalize_rule_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(value.get("id") or uuid.uuid4()),
        "name": str(value.get("name") or "Notification Workflow").strip()[:160],
        "trigger_event": str(value.get("trigger_event") or value.get("event_type") or "").strip(),
        "conditions": normalize_conditions(value.get("conditions")),
        "actions": normalize_actions(value.get("actions")),
        "is_active": value.get("is_active", value.get("enabled", True)) is not False,
    }


def normalize_conditions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    conditions: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        condition_type = str(raw.get("type") or "")
        if condition_type == "schedule":
            conditions.append(
                {
                    "id": str(raw.get("id") or f"condition-{index + 1}"),
                    "type": "schedule",
                    "schedule_id": str(raw.get("schedule_id") or ""),
                }
            )
        elif condition_type == "presence":
            conditions.append(
                {
                    "id": str(raw.get("id") or f"condition-{index + 1}"),
                    "type": "presence",
                    "mode": str(raw.get("mode") or "someone_home"),
                    "person_id": str(raw.get("person_id") or ""),
                }
            )
    return conditions


def normalize_actions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        action_type = str(raw.get("type") or "")
        if action_type not in {"mobile", "voice", "in_app", "discord"}:
            continue
        actions.append(
            {
                "id": str(raw.get("id") or f"action-{index + 1}"),
                "type": action_type,
                "target_mode": str(raw.get("target_mode") or "all"),
                "target_ids": normalize_string_list(raw.get("target_ids")),
                "title_template": str(raw.get("title_template") or ""),
                "message_template": str(raw.get("message_template") or ""),
                "media": normalize_media(raw.get("media")),
            }
        )
    return actions


def normalize_media(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "attach_camera_snapshot": bool(raw.get("attach_camera_snapshot") or raw.get("enabled")),
        "camera_id": str(raw.get("camera_id") or ""),
    }


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def presence_condition_matches(condition: dict[str, Any], present_person_ids: set[str]) -> bool:
    mode = str(condition.get("mode") or "")
    if mode == "no_one_home":
        return not present_person_ids
    if mode == "someone_home":
        return bool(present_person_ids)
    if mode == "person_home":
        return str(condition.get("person_id") or "") in present_person_ids
    return False


def rule_id(rule: NotificationRule | dict[str, Any]) -> str:
    return str(rule.id if isinstance(rule, NotificationRule) else rule.get("id") or "")


def rule_name(rule: NotificationRule | dict[str, Any]) -> str:
    return str(rule.name if isinstance(rule, NotificationRule) else rule.get("name") or "Notification Workflow")


def rule_trigger_event(rule: NotificationRule | dict[str, Any]) -> str:
    return str(rule.trigger_event if isinstance(rule, NotificationRule) else rule.get("trigger_event") or "")


def rule_conditions(rule: NotificationRule | dict[str, Any]) -> list[dict[str, Any]]:
    return normalize_conditions(rule.conditions if isinstance(rule, NotificationRule) else rule.get("conditions"))


def rule_actions(rule: NotificationRule | dict[str, Any]) -> list[dict[str, Any]]:
    return normalize_actions(rule.actions if isinstance(rule, NotificationRule) else rule.get("actions"))


def rule_is_active(rule: NotificationRule | dict[str, Any]) -> bool:
    return bool(rule.is_active if isinstance(rule, NotificationRule) else rule.get("is_active", True))


def trigger_severity(trigger_event: str) -> str:
    for group in TRIGGER_CATALOG:
        for event in group["events"]:
            if event["value"] == trigger_event:
                return str(event["severity"])
    return "info"


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _possessive(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    return f"{cleaned}'" if cleaned.lower().endswith("s") else f"{cleaned}'s"


def _time_label(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return value


def _duration_label_from_seconds(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        total_seconds = max(0, int(value))
    except (TypeError, ValueError):
        return str(value)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


@lru_cache
def get_notification_service() -> NotificationService:
    return NotificationService()
