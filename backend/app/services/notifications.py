import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import NotificationRule, Presence, Schedule
from app.models.enums import PresenceState
from app.modules.announcements.home_assistant_tts import AnnouncementTarget, HomeAssistantTtsAnnouncer
from app.modules.home_assistant.client import HomeAssistantClient
from app.modules.notifications.apprise_client import (
    AppriseNotificationSender,
    normalize_apprise_url,
    split_apprise_urls,
    summarize_apprise_url,
)
from app.modules.notifications.base import (
    ComposedNotification,
    NotificationContext,
    NotificationDeliveryError,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.schedules import schedule_allows_at
from app.services.settings import get_runtime_config
from app.services.unifi_protect import get_unifi_protect_service

logger = get_logger(__name__)

AT_TOKEN_PATTERN = re.compile(r"@([A-Za-z][A-Za-z0-9_]*)")
LEGACY_TOKEN_PATTERN = re.compile(r"\[([A-Za-z][A-Za-z0-9_]*)\]")

TRIGGER_CATALOG: list[dict[str, Any]] = [
    {
        "id": "events",
        "label": "Events",
        "events": [
            {
                "value": "authorized_entry",
                "label": "Authorised Vehicle Detected",
                "severity": "info",
                "description": "A known vehicle is granted entry inside its access policy.",
            },
            {
                "value": "unauthorized_plate",
                "label": "Unauthorised Vehicle Detected",
                "severity": "critical",
                "description": "A plate is denied because it is unknown or inactive.",
            },
            {
                "value": "outside_schedule",
                "label": "Outside Schedule",
                "severity": "warning",
                "description": "A known vehicle is denied by schedule or access policy.",
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
                "value": "gate_open_failed",
                "label": "Gate Open Failed",
                "severity": "critical",
                "description": "The access decision was granted but the gate command failed.",
            },
            {
                "value": "garage_door_open_failed",
                "label": "Garage Door Failed",
                "severity": "critical",
                "description": "A linked garage door command failed.",
            },
            {
                "value": "agent_anomaly_alert",
                "label": "AI Anomaly Alert",
                "severity": "critical",
                "description": "The AI agent raises an explicit anomaly alert.",
            },
            {
                "value": "integration_test",
                "label": "Integration Test",
                "severity": "info",
                "description": "A user-triggered test message.",
            },
        ],
    }
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
            {"name": "VehicleModel", "token": "@VehicleModel", "label": "Vehicle model"},
            {"name": "VehicleColor", "token": "@VehicleColor", "label": "Vehicle colour"},
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
        ],
    },
    {
        "group": "Integration",
        "items": [
            {"name": "GarageDoor", "token": "@GarageDoor", "label": "Garage door"},
            {"name": "EntityId", "token": "@EntityId", "label": "Entity ID"},
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
    "vehicle_model": "Model Y Dual Motor Long Range",
    "vehicle_color": "Pearl white",
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

        voice_endpoints = await self._voice_endpoint_catalog(config)
        return [
            {
                "id": "mobile",
                "name": "Mobile Notification",
                "provider": "Apprise",
                "configured": bool(apprise_urls),
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
        rules: list[NotificationRule | dict[str, Any]]
        if rules_override is None:
            async with AsyncSessionLocal() as session:
                rules = (
                    await session.scalars(
                        select(NotificationRule)
                        .where(
                            NotificationRule.trigger_event == context.event_type,
                            NotificationRule.is_active.is_(True),
                        )
                        .order_by(NotificationRule.created_at)
                    )
                ).all()
        else:
            rules = [normalize_rule_payload(rule) for rule in rules_override]

        if not rules:
            await event_bus.publish(
                "notification.skipped",
                {
                    "event_type": context.event_type,
                    "severity": context.severity,
                    "subject": context.subject,
                    "reason": "no_matching_workflow",
                },
            )
            if raise_on_failure:
                raise NotificationDeliveryError("No active notification workflow matched this event.")
            return composed_from_context(context)

        first_notification: ComposedNotification | None = None
        failures: list[str] = []
        delivered_any = False

        for rule in rules:
            try:
                condition_passed = await self.conditions_match(rule, context)
                if not condition_passed:
                    await event_bus.publish(
                        "notification.skipped",
                        {
                            "rule_id": rule_id(rule),
                            "rule_name": rule_name(rule),
                            "event_type": context.event_type,
                            "severity": context.severity,
                            "reason": "conditions_not_met",
                        },
                    )
                    continue
                notification = await self.execute_rule(rule, context, raise_on_failure=raise_on_failure)
                first_notification = first_notification or notification
                delivered_any = True
            except NotificationDeliveryError as exc:
                failures.append(f"{rule_name(rule)}: {exc}")
                logger.warning(
                    "notification_workflow_failed",
                    extra={"rule_id": rule_id(rule), "event_type": context.event_type, "error": str(exc)},
                )
                if raise_on_failure:
                    raise

        if delivered_any and first_notification:
            return first_notification
        if failures and raise_on_failure:
            raise NotificationDeliveryError("; ".join(failures))
        if raise_on_failure:
            raise NotificationDeliveryError("No notification workflow actions were delivered.")
        return composed_from_context(context)

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
        rendered = self.render_rule(rule, context)
        config = await get_runtime_config()
        first_notification: ComposedNotification | None = None
        failures: list[str] = []

        for action in rendered["actions"]:
            if first_notification is None:
                first_notification = ComposedNotification(
                    title=str(action.get("title") or rendered["name"]),
                    body=str(action.get("message") or ""),
                )
            try:
                await self._deliver_action(action, context, config, rendered)
            except NotificationDeliveryError as exc:
                failures.append(f"{action.get('type')}: {exc}")
                await event_bus.publish(
                    "notification.failed",
                    self._event_payload(rendered, action, context, False, str(exc)),
                )
                if raise_on_failure:
                    raise
                continue
            await event_bus.publish(
                "notification.sent",
                self._event_payload(rendered, action, context, True, ""),
            )

        if failures and raise_on_failure:
            raise NotificationDeliveryError("; ".join(failures))
        if not first_notification:
            if raise_on_failure:
                raise NotificationDeliveryError("Workflow has no notification actions.")
            return composed_from_context(context)
        return first_notification

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
        if event.type != "notification.trigger":
            return
        await self.process_context(notification_context_from_payload(event.payload))

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
    ) -> None:
        action_type = str(action.get("type") or "")
        if action_type == "mobile":
            await self._send_mobile(action, context, config)
            return
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
            return
        if action_type == "voice":
            await self._send_voice(action, config)
            return
        raise NotificationDeliveryError(f"Unsupported notification action: {action_type}")

    async def _send_mobile(self, action: dict[str, Any], context: NotificationContext, config) -> None:
        urls = self._select_apprise_urls(config.apprise_urls, action)
        if not urls:
            raise NotificationDeliveryError("No Apprise endpoints are configured or selected.")
        attachments = await self._snapshot_attachments(action.get("media") or {})
        sender = AppriseNotificationSender(urls="\n".join(urls))
        try:
            await sender.send(
                str(action.get("title") or context.subject),
                str(action.get("message") or ""),
                context,
                attachments=attachments,
            )
        finally:
            for path in attachments:
                try:
                    os.unlink(path)
                except OSError:
                    logger.debug("notification_snapshot_cleanup_failed", extra={"path": path})

    async def _send_voice(self, action: dict[str, Any], config) -> None:
        targets = await self._select_voice_targets(config, action)
        if not targets:
            raise NotificationDeliveryError("No Home Assistant media player is configured or selected.")
        announcer = HomeAssistantTtsAnnouncer()
        for target in targets:
            try:
                await announcer.announce(AnnouncementTarget(target), str(action.get("message") or ""))
            except Exception as exc:
                raise NotificationDeliveryError(str(exc)) from exc

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

    async def _all_media_player_targets(self, config) -> list[str]:
        if not (config.home_assistant_url and config.home_assistant_token):
            return []
        try:
            states = await HomeAssistantClient().list_states()
        except Exception as exc:
            logger.debug("notification_media_player_discovery_failed", extra={"error": str(exc)})
            return []
        return sorted(state.entity_id for state in states if state.entity_id.startswith("media_player."))

    async def _snapshot_attachments(self, media: dict[str, Any]) -> list[str]:
        if not media.get("attach_camera_snapshot") or not media.get("camera_id"):
            return []
        camera_id = str(media["camera_id"])
        try:
            snapshot = await get_unifi_protect_service().snapshot(camera_id, width=960, height=540)
        except Exception as exc:
            raise NotificationDeliveryError(f"Unable to capture notification snapshot: {exc}") from exc

        suffix = ".png" if "png" in snapshot.content_type else ".jpg"
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            handle.write(snapshot.content)
            return [handle.name]
        finally:
            handle.close()

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
        }


def notification_context_payload(context: NotificationContext) -> dict[str, Any]:
    return {
        "event_type": context.event_type,
        "subject": context.subject,
        "severity": context.severity,
        "facts": dict(context.facts),
    }


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
    return NotificationContext(
        event_type=event_type,
        subject="Steph arrived at the gate",
        severity=trigger_severity(event_type),
        facts=dict(MOCK_FACTS),
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

    vehicle_name = pick(
        "vehicle_name",
        "vehicle_display_name",
        "vehicle_description",
        "vehicle_make",
        "make",
        "registration_number",
        default=context.subject,
    )
    occurred_at = pick("occurred_at", "created_at")
    return {
        "FirstName": first_name,
        "FirstNamePossessive": _possessive(first_name),
        "ObjectPronoun": pick("object_pronoun", "pronoun_object", default="them"),
        "PossessiveDeterminer": pick("possessive_determiner", "pronoun_possessive", default="their"),
        "LastName": last_name,
        "DisplayName": display_name or first_name or "Unknown visitor",
        "GroupName": pick("group_name", "group"),
        "Registration": pick("vehicle_registration_number", "registration_number", "vrn", default=context.subject),
        "VehicleRegistrationNumber": pick("vehicle_registration_number", "registration_number", "vrn", default=context.subject),
        "VehicleName": vehicle_name,
        "VehicleDisplayName": vehicle_name,
        "VehicleMake": pick("vehicle_make", "make"),
        "VehicleModel": pick("vehicle_model", "model"),
        "VehicleColor": pick("vehicle_color", "color", "colour"),
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
        if action_type not in {"mobile", "voice", "in_app"}:
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


@lru_cache
def get_notification_service() -> NotificationService:
    return NotificationService()
