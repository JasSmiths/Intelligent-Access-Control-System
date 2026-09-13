import re
import uuid
import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import NotificationRule, NotificationRun, Person, Presence, Schedule, User
from app.models.enums import PresenceState
from app.modules.announcements.home_assistant_tts import AnnouncementTarget, HomeAssistantTtsAnnouncer
from app.modules.home_assistant.client import HomeAssistantClient as DefaultHomeAssistantClient, get_home_assistant_client
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
from app.services.actionable_notifications import (
    get_actionable_notification_service,
)
from app.services.workflows.notification_payloads import notification_context_payload, trigger_severity, _duration_label_from_seconds
from app.services.workflows.visitor_notifications import visitor_pass_notification_contexts_from_event
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.automation_authorization import notification_origin_denial
from app.services.access.authorization import assert_current_recognition_domain_authorization
from app.services.notification_runs import NotificationActionAuthorization, NotificationRunStore
from app.services.notification_dispatch import NotificationDispatcher
from app.services.notification_requests import (
    configuration_binding, confirmed_origin, confirmed_attempt_denial, ephemeral_configuration_binding,
)
from app.services.action_confirmations import consume_action_confirmation
from app.services.mutation_context import load_active_admin
from app.services.discord_messaging import discord_config_from_runtime, get_discord_messaging_service
from app.services.snapshots import get_snapshot_manager
from app.services.schedules import schedule_allows_at
from app.services.settings import get_runtime_config, get_runtime_config_for_session
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, telemetry, write_audit_log, actor_from_user
from app.services.tts_phonetics import apply_vehicle_tts_phonetics
from app.services.type_helpers import as_dict
from app.services.unifi_protect import get_unifi_protect_service
from app.services.messaging.whatsapp_delivery import get_whatsapp_delivery_service
from app.services.messaging.whatsapp_helpers import visitor_pass_timeframe_button_id
from app.services.visitor_conversations import get_visitor_conversation_service
from app.services.workflows.catalog import (
    GATE_MALFUNCTION_EVENT_TYPE,
    INTEGRATION_DEGRADED_EVENT_TYPE,
    notification_actionable_catalog,
    notification_trigger_catalog,
    notification_variable_groups,
)
from app.services.workflows.context import canonical_key, normalize_string_list, render_template
from app.services.workflows import notification_payloads

logger = get_logger(__name__)
HomeAssistantClient = DefaultHomeAssistantClient

HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID = "input_boolean.announcements"
VOICE_ANNOUNCEMENTS_DISABLED_MESSAGE = (
    "Voice Notification suppressed: `input_boolean.announcements` is disabled."
)
def _home_assistant_client() -> DefaultHomeAssistantClient:
    if HomeAssistantClient is DefaultHomeAssistantClient:
        return get_home_assistant_client()
    return HomeAssistantClient()
GATE_MALFUNCTION_UPDATE_PREFIX = "Gate Malfunction Update:"
GATE_MALFUNCTION_VOICE_PREFIX = "Attention."


@dataclass
class NotificationWorkflowResult:
    notification: ComposedNotification
    delivered_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    failures: list[str] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)
    run_id: str | None = None
    recovery_status: str | None = None

    @property
    def status(self) -> str:
        if self.recovery_status in {"review_required", "queued", "processing"}:
            return self.recovery_status
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

TRIGGER_CATALOG = notification_trigger_catalog()
ACTIONABLE_NOTIFICATION_CATALOG = notification_actionable_catalog(notification_payloads.GATE_OPEN_ACTION)
VARIABLE_GROUPS = notification_variable_groups()

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
    "malfunction_stage": "30m",
    "last_known_vehicle": "Steph Smith exited in 2026 Tesla Model Y",
    "visitor_name": "Sarah",
    "visitor_pass_id": "visitor-pass-1",
    "visitor_pass_status": "used",
    "visitor_pass_registration": "PE70DHX",
    "visitor_pass_time_window": "01 May 2026, 10:00 to 01 May 2026, 18:00",
    "visitor_pass_vehicle_registration": "PE70DHX",
    "visitor_pass_vehicle_make": "Peugeot",
    "visitor_pass_vehicle_colour": "Silver",
    "visitor_pass_duration_on_site": "1h 25m",
    "visitor_pass_current_window": "01 May 2026, 10:00 to 01 May 2026, 18:00",
    "visitor_pass_requested_window": "01 May 2026, 10:00 to 01 May 2026, 20:00",
    "visitor_pass_original_time": "01 May 2026, 10:00 to 01 May 2026, 18:00",
    "visitor_pass_requested_time": "01 May 2026, 10:00 to 01 May 2026, 20:00",
    "visitor_pass_timeframe_request_id": "request-1",
    "visitor_pass_visitor_message": "Can I stay two hours longer?",
}


class NotificationService:
    """DB-backed notification workflow engine.

    DB notification_rules are the only runtime workflow source. This service can
    be invoked directly for tests, and listens for normalized `notification.trigger`
    events for normal runtime delivery.
    """

    def __init__(self, *, run_store=None) -> None:
        self._started = False
        self.run_store = run_store if run_store is not None else NotificationRunStore()
        self.dispatcher = NotificationDispatcher(self, self.run_store)

    async def start(self) -> None:
        if self._started:
            return
        event_bus.subscribe(self._handle_realtime_event)
        self._started = True
        self.dispatcher.start()
        logger.info("notification_workflow_service_started")

    async def stop(self) -> None:
        if not self._started:
            return
        event_bus.unsubscribe(self._handle_realtime_event)
        self._started = False
        await self.dispatcher.stop()
        logger.info("notification_workflow_service_stopped")

    async def catalog(self) -> dict[str, Any]:
        config = await get_runtime_config()
        return {
            "triggers": TRIGGER_CATALOG,
            "variables": VARIABLE_GROUPS,
            "integrations": await self.available_integrations(config),
            "actionable_notifications": ACTIONABLE_NOTIFICATION_CATALOG,
            "gate_malfunction_stages": notification_payloads.GATE_MALFUNCTION_STAGES,
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
        whatsapp_endpoints = await self._whatsapp_endpoint_catalog()
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
            {
                "id": "whatsapp",
                "name": "WhatsApp",
                "provider": "Meta WhatsApp Cloud API",
                "configured": bool(whatsapp_endpoints),
                "endpoints": whatsapp_endpoints,
            },
        ]

    async def notify(
        self,
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
        rules_override: list[dict[str, Any]] | None = None,
    ) -> ComposedNotification:
        """Dispatch wrapper.

        Use enqueue_notification() when the caller wants asynchronous workflow
        delivery, and send_notification_now() when the caller needs immediate
        delivery status or failures.
        """
        if raise_on_failure or rules_override is not None:
            return await self.send_notification_now(
                context,
                raise_on_failure=raise_on_failure,
                rules_override=rules_override,
            )
        return await self.enqueue_notification(context)

    async def enqueue_in_session(
        self, session: AsyncSession, context: NotificationContext, *, dispatch_id: uuid.UUID,
    ) -> uuid.UUID:
        """Reserve required delivery in the caller's mutation transaction."""
        return await self.run_store.enqueue_in_session(
            session, notification_context_payload(context), run_id=dispatch_id,
        )

    async def enqueue_notification(self, context: NotificationContext) -> ComposedNotification:
        """Persist before waking dispatch; realtime is independent from acceptance."""
        run_id = await self.run_store.create(notification_context_payload(context))
        self.dispatcher.wake()
        try:
            await event_bus.publish(
                "notification.trigger",
                notification_context_payload(context, notification_run_id=str(run_id)),
            )
        except Exception:
            logger.exception("notification_wakeup_publish_failed")
        return composed_from_context(context)

    async def reserve_confirmed_request(
        self, session, *, user, action, payload, confirmation_token, context,
        direct_action=None, rules_override=None, ephemeral_config=None, visitor_origin=None,
    ):
        """Confirmation, required request audit and delivery are one transaction.

        The API commits once and then calls dispatch_reserved. A disconnected
        caller cannot erase the accepted output; polling recovers ordinary work.
        """
        prepared = await self.prepare_confirmed_delivery(context, action=action,
            direct_action=direct_action, rules_override=rules_override)
        current = await load_active_admin(session, user.id, auth_version=user.auth_session_version, lock=True)
        confirmation = await consume_action_confirmation(
            session, user=current, action=action, payload=payload,
            confirmation_token=confirmation_token, commit=False,
        )
        return await self.reserve_confirmed_in_session(
            session, actor_user_id=current.id, auth_version=current.auth_session_version,
            operation_id=confirmation.id, action=action, authority="api", context=context,
            direct_action=direct_action, rules_override=rules_override, ephemeral_config=ephemeral_config,
            prepared=prepared,
            visitor_origin=visitor_origin,
        )

    async def prepare_confirmed_delivery(self, context, *, action, direct_action=None, rules_override=None):
        """Resolve content/audience before acquiring confirmation or actor locks."""
        async with self.run_store.sessions() as read_session:
            config = await get_runtime_config_for_session(read_session)
        if direct_action is not None:
            if direct_action.get("delivery_mode") not in {"literal", "whatsapp_template"}:
                raise ValueError("A concrete literal or template delivery mode is required")
            if (direct_action.get("configured_default") and direct_action.get("type") == "voice"
                    and direct_action.get("target") != config.home_assistant_default_media_player):
                raise NotificationDeliveryError("The default announcement destination changed. Create a fresh confirmation.",
                                                delivery="not_sent")
            plan = [{"rule": {"id": action, "name": action, "trigger_event": context.event_type},
                     "action": copy.deepcopy(direct_action), "state": "pending"}]
        else:
            row = NotificationRun(context=notification_context_payload(context), rules_override=rules_override)
            plan = await self.prepare_delivery_plan(row)
            for item in plan:
                if item.get("state") != "pending":
                    continue
                value = item["action"]
                if value["type"] == "voice":
                    value["frozen_voice_targets"] = await self._select_voice_targets(config, value)
                elif value["type"] == "mobile":
                    value["frozen_mobile_targets"] = await self._select_home_assistant_mobile_targets(config, value)
                    # Endpoint indexes are safe to retain; URLs may contain secrets.
                    configured = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
                    selected = self._select_apprise_urls(config.apprise_urls, value)
                    value["frozen_apprise_indexes"] = [configured.index(url) for url in selected]
        return plan, configuration_binding(config, plan)

    async def reserve_confirmed_in_session(
        self, session, *, actor_user_id, auth_version, operation_id, action, context,
        authority="api", direct_action=None, rules_override=None, ephemeral_config=None, prepared=None,
        visitor_origin=None,
    ):
        plan, prepared_binding = prepared if prepared is not None else await self.prepare_confirmed_delivery(
            context, action=action, direct_action=direct_action, rules_override=rules_override,
        )
        user, origin = await confirmed_origin(
            session, actor_user_id=actor_user_id, auth_version=auth_version,
            operation_id=operation_id, authority=authority, action=action,
        )
        run_id = uuid.uuid5(uuid.UUID(str(operation_id)), "notification-delivery")
        payload = notification_context_payload(context)
        if ephemeral_config and (direct_action is None or direct_action.get("type") != "whatsapp"):
            raise ValueError("Ephemeral configuration is only supported for a WhatsApp integration test")
        config = await get_runtime_config_for_session(session)
        if configuration_binding(config, plan) != prepared_binding:
            raise ValueError("Notification configuration changed while preparing the request")
        origin.update(ephemeral_config=ephemeral_config is not None,
                      configuration_binding=ephemeral_configuration_binding(ephemeral_config)
                      if ephemeral_config is not None else configuration_binding(config, plan))
        payload["confirmed_delivery"] = origin
        if visitor_origin is not None:
            payload["visitor_conversation_origin"] = {
                **copy.deepcopy(visitor_origin), "operation_id": str(operation_id),
            }
        identity, claimed = await self.run_store.reserve_prepared_in_session(
            session, payload, run_id=run_id, plan=plan,
        )
        if claimed is not None:
            await write_audit_log(
                session, category=TELEMETRY_CATEGORY_INTEGRATIONS, action=action + ".requested",
                actor=actor_from_user(user), actor_user_id=user.id, target_entity="NotificationRun",
                target_id=str(identity), metadata={"operation_id": str(operation_id), "authority": authority},
            )
        return identity, claimed

    async def dispatch_reserved(self, run_id, claimed=None, *, ephemeral_config=None):
        await self.dispatcher.run_once(run_id, claimed=claimed, ephemeral_config=ephemeral_config)
        return self.result_from_run(await self.run_store.get(run_id))

    async def send_notification_now(
        self,
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
        rules_override: list[dict[str, Any]] | None = None,
    ) -> ComposedNotification:
        """Process notification rules synchronously and return delivery output."""
        return (
            await self.send_notification_now_with_result(
                context,
                raise_on_failure=raise_on_failure,
                rules_override=rules_override,
            )
        ).notification

    async def send_notification_now_with_result(
        self,
        context: NotificationContext,
        *,
        raise_on_failure: bool = False,
        rules_override: list[dict[str, Any]] | None = None,
        dispatch_id: uuid.UUID | None = None,
    ) -> NotificationWorkflowResult:
        """Immediate attempt through the same durable owner as background delivery."""
        run_id, claimed = await self.run_store.reserve(
            notification_context_payload(context), rules_override=rules_override, run_id=dispatch_id,
        )
        await self.dispatcher.run_once(run_id, claimed=claimed)
        row = await self.run_store.get(run_id)
        result = self.result_from_run(row)
        if raise_on_failure and (result.status != "sent" or result.failed_count):
            raise NotificationDeliveryError(row.review_reason or "; ".join(result.failures or result.skipped_reasons)
                                            or "Notification was not delivered.")
        return result

    @staticmethod
    def result_from_run(row: NotificationRun) -> NotificationWorkflowResult:
        context = notification_context_from_payload(row.context)
        first = next((x for x in row.delivery_plan or [] if x.get("action")), None)
        notification = (ComposedNotification(title=first["action"]["title"], body=first["action"]["message"])
                        if first else composed_from_context(context))
        return NotificationWorkflowResult(
            notification=notification, run_id=str(row.id), recovery_status=row.status,
            delivered_count=row.delivered_count, failed_count=row.failed_count,
            skipped_count=row.skipped_count, failures=list(row.failures), skipped_reasons=list(row.skipped_reasons),
        )

    async def prepare_delivery_plan(self, row: NotificationRun) -> list[dict[str, Any]]:
        context = notification_context_from_payload(row.context)
        if row.id is not None:
            context = self._context_with_notification_run_id(context, row.id)
        rules = await self._rules_for_context(context, row.rules_override)
        plan = []
        for rule in rules:
            rule_origin = self._ordinary_rule_origin(rule) if row.rules_override is None and isinstance(rule, NotificationRule) else None
            rendered = self.render_rule(rule, context)
            if not await self.conditions_match(rule, context):
                plan.append({"rule": rendered, "state": "skipped", "reason": "conditions_not_met"})
                continue
            if context.event_type == GATE_MALFUNCTION_EVENT_TYPE:
                rendered["actions"] = await self._gate_malfunction_actions_for_delivery(rendered["actions"], context)
            for action in rendered["actions"]:
                if action.get("type") == "whatsapp":
                    action = await get_whatsapp_delivery_service().prepare_notification_action(
                        action, context, variables=context_variables(context),
                    )
                elif action.get("type") == "discord":
                    action = await get_discord_messaging_service().prepare_notification_action(action, context)
                item = {
                    "rule": {k: v for k, v in rendered.items() if k != "actions"},
                    "action": action,
                    "state": "pending",
                }
                if rule_origin is not None:
                    item["rule_origin"] = rule_origin
                plan.append(item)
        return plan or [{"state": "skipped", "reason": "no_matching_workflow" if not rules else "no_workflow_actions_delivered"}]

    async def authorize_attempt(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        action=None,
        item=None,
    ) -> str | NotificationActionAuthorization | None:
        """Current originating domain authority joins the durable attempt transaction."""
        if payload.get("actionable_output_origin") is not None:
            denial = await get_actionable_notification_service().authorize_notification_output_in_session(
                session,
                payload,
                run_id,
                action,
            )
            return NotificationActionAuthorization(action_skip=denial) if denial else None
        visitor_origin = payload.get("visitor_conversation_origin")
        if visitor_origin is not None:
            denial = await get_visitor_conversation_service().authorize_notification_in_session(
                session, visitor_origin, run_id,
            )
        else:
            denial = await notification_origin_denial(session, payload, run_id,
                authorize_recognition=assert_current_recognition_domain_authorization)
        if not denial and item is not None:
            action_skip = await self._ordinary_rule_action_skip(session, item)
            if action_skip:
                return NotificationActionAuthorization(action_skip=action_skip)
        if not denial and action is not None and action.get("type") == "whatsapp" and not action.get("delivery_mode"):
            denial = await get_whatsapp_delivery_service().authorize_notification_action_in_session(session, action)
        return denial

    async def authorize_attempt_with_config(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        action=None,
        item=None,
        final: bool = False,
    ) -> tuple[Any | None, str | NotificationActionAuthorization | None]:
        """Authorize normal work and retain only its checked runtime snapshot.

        The first pass owns rule/domain policy before the notification-run lock.
        The final pass refreshes only transport configuration after that lock, so
        a mutable saved rule is never re-locked in the inverse order.
        """
        if final:
            config = await get_runtime_config_for_session(session)
            return config, await self._authorize_action_config_in_session(
                session, action, config, payload=payload, run_id=run_id,
            )
        policy = await self.authorize_attempt(session, payload, run_id, action=action, item=item)
        if policy is not None:
            return None, policy
        config = await get_runtime_config_for_session(session)
        return config, await self._authorize_action_config_in_session(
            session, action, config, payload=payload, run_id=run_id,
        )

    async def _authorize_action_config_in_session(
        self,
        session: AsyncSession,
        action,
        config,
        *,
        payload: dict[str, Any] | None = None,
        run_id: uuid.UUID | None = None,
    ):
        if action is not None and action.get("actionable_output") is not None:
            if payload is None or run_id is None:
                return NotificationActionAuthorization(action_skip="actionable_output_origin_invalid")
            denial = await get_actionable_notification_service().authorize_notification_output_in_session(
                session,
                payload,
                run_id,
                action,
                config=config,
                final=True,
            )
            if denial:
                return NotificationActionAuthorization(action_skip=denial)
        if action is None or action.get("type") != "discord":
            return None
        denial = await get_discord_messaging_service().authorize_notification_action_in_session(
            session,
            action,
            config=discord_config_from_runtime(config),
        )
        return NotificationActionAuthorization(action_skip=denial) if denial else None

    async def authorize_confirmed_attempt(self, session, payload, run_id, *, plan, ephemeral_config=None, action=None, item=None):
        origin = payload.get("confirmed_delivery") or {}
        try:
            actors = {uuid.UUID(str(origin.get("user_id")))}
            for recipient in (action or {}).get("frozen_whatsapp_recipients", []):
                if recipient.get("kind") == "admin":
                    actors.add(uuid.UUID(str(recipient["user_id"])))
            await session.scalars(select(User).where(User.id.in_(actors)).order_by(User.id).with_for_update())
            await load_active_admin(session, origin.get("user_id"), auth_version=origin.get("auth_version"), lock=True)
        except (ValueError, KeyError, TypeError):
            return None, "confirmed_actor_no_longer_authorized"
        config = await get_runtime_config_for_session(session)
        denial = await confirmed_attempt_denial(
            session, payload, run_id, plan=plan, runtime_config=config, ephemeral_config=ephemeral_config,
        )
        if not denial and action is not None:
            denial = await self.authorize_attempt(session, payload, run_id, action=action, item=item)
        if not denial and action is not None:
            denial = await self._authorize_action_config_in_session(
                session, action, config, payload=payload, run_id=run_id,
            )
        return config, denial

    @staticmethod
    def _ordinary_rule_origin(rule: NotificationRule) -> dict[str, str]:
        definition = {
            "id": str(rule.id),
            "name": rule.name,
            "trigger_event": rule.trigger_event,
            "conditions": rule.conditions,
            "actions": rule.actions,
            "is_active": rule.is_active,
        }
        return {
            "rule_id": str(rule.id),
            "definition_fingerprint": notification_payloads.notification_rule_definition_fingerprint(definition),
        }

    async def _ordinary_rule_action_skip(self, session: AsyncSession, item: dict[str, Any]) -> str | None:
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
        current = self._ordinary_rule_origin(rule)["definition_fingerprint"]
        if origin.get("definition_fingerprint") != current:
            return "notification_rule_changed"
        return None

    async def delivery_config(self):
        return await get_runtime_config()

    async def deliver_planned_action(self, item, row, config, *, ephemeral_config=None) -> NotificationActionOutcome:
        context = self._context_with_notification_run_id(notification_context_from_payload(row.context), row.id)
        action = item["action"]
        if action.get("delivery_mode") in {"literal", "whatsapp_template"}:
            return await self._deliver_literal(action, context, config, ephemeral_config=ephemeral_config,
                record_history=row.context.get("visitor_conversation_origin") is None)
        return await self._deliver_action(item["action"], context, config, item["rule"])

    async def _deliver_literal(self, action, context, config, *, ephemeral_config=None, record_history=True):
        """Preserve manual native bodies; workflow formatting does not apply here."""
        target, body = action["target"], action["message"]
        metadata = {}
        if action["type"] == "voice":
            await HomeAssistantTtsAnnouncer().announce(AnnouncementTarget(target), body, runtime_config=config)
        elif action["type"] == "mobile":
            output_actions = await get_actionable_notification_service().resolve_notification_output_actions(
                action,
                target=target,
            )
            await HomeAssistantMobileAppNotifier().send(
                HomeAssistantMobileAppTarget(target),
                action["title"],
                body,
                context,
                runtime_config=config,
                actions=output_actions or None,
            )
        elif action["type"] == "whatsapp":
            from app.services.messaging.whatsapp_configuration import whatsapp_config_from_runtime
            transport_config = ephemeral_config or whatsapp_config_from_runtime(config)
            delivery = get_whatsapp_delivery_service()
            history_options = {"record_history": False} if not record_history else {}
            if action.get("delivery_mode") == "whatsapp_template":
                result = await delivery.send_template_message(
                    target, template_name=action["template_name"], language_code=action["language_code"],
                    body_parameters=action["body_parameters"], config=transport_config,
                    **history_options,
                )
            else:
                result = await delivery.send_text_message(target, body, config=transport_config, **history_options)
            from app.services.messaging.whatsapp_helpers import whatsapp_response_message_id
            identity = whatsapp_response_message_id(result)
            if identity:
                metadata["provider_message_id"] = identity
        else:
            raise NotificationDeliveryError("Unsupported literal notification channel")
        return NotificationActionOutcome(delivered=True, metadata=metadata)

    async def prepare_delivery_output(self, session, row, index, outcome):
        if row is not None and row.context.get("visitor_conversation_origin") is not None:
            return await get_visitor_conversation_service().prepare_notification_output(session, row, index, outcome)
        return None

    async def publish_planned_outcome(self, item, row, outcome) -> None:
        context = self._context_with_notification_run_id(notification_context_from_payload(row.context), row.id)
        payload = {**self._event_payload(item["rule"], item["action"], context, outcome.delivered, ""),
                   **outcome.metadata, "reason": outcome.reason, "message": outcome.message}
        if outcome.delivered:
            try:
                identity = uuid.UUID(str(item["rule"].get("id")))
            except ValueError:
                identity = None
            if identity:
                await self._mark_rule_fired(NotificationRule(id=identity))
        self._record_notification_span("Notification Action Suppressed" if outcome.skipped else "Notification Action Sent",
                                       context, output_payload=payload)
        await event_bus.publish("notification.skipped" if outcome.skipped else "notification.sent", payload)

    async def publish_planned_failure(
        self,
        item,
        row,
        *,
        reason: str = "provider_outcome_unknown",
        requires_review: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        context = self._context_with_notification_run_id(notification_context_from_payload(row.context), row.id)
        error = (
            "Provider outcome unknown; review required. Automatic retry is disabled."
            if requires_review
            else "The notification provider definitely did not accept this action."
        )
        payload = {**self._event_payload(item["rule"], item["action"], context, False, error),
                   **(metadata or {}), "reason": reason, "requires_review": requires_review}
        self._record_notification_span("Notification Action Failed", context, status="error", error=error,
                                       output_payload=payload)
        await event_bus.publish("notification.failed", payload)

    async def publish_plan_completion(self, row) -> None:
        context = self._context_with_notification_run_id(notification_context_from_payload(row.context), row.id)
        for item in row.delivery_plan or []:
            if item["state"] == "skipped" and "action" not in item:
                await self._publish_workflow_skip(context, item["reason"], rule=item.get("rule"))

    async def _rules_for_context(
        self,
        context: NotificationContext,
        rules_override: list[dict[str, Any]] | None,
    ) -> list[NotificationRule | dict[str, Any]]:
        if rules_override is not None:
            return [notification_payloads.normalize_rule_payload(rule) for rule in rules_override]
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
            "malfunction_stage": context.facts.get("malfunction_stage"),
            "notification_run_id": context.facts.get("notification_run_id"),
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

    def _context_with_notification_run_id(
        self,
        context: NotificationContext,
        run_id: uuid.UUID,
    ) -> NotificationContext:
        return replace(context, facts={**context.facts, "notification_run_id": str(run_id)})

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

    def render_rule(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext | None = None,
    ) -> dict[str, Any]:
        active_context = context or sample_notification_context(rule_trigger_event(rule))
        variables = context_variables(active_context)
        rendered_actions: list[dict[str, Any]] = []
        for action in rule_actions(rule):
            action_type = str(action.get("type") or "")
            media = notification_payloads.normalize_media(action.get("media"))
            title_template = str(action.get("title_template") or "")
            message_template = str(action.get("message_template") or "")
            gate_malfunction_stages = notification_payloads.normalize_gate_malfunction_stages(
                action.get("gate_malfunction_stages")
            )
            if active_context.event_type == GATE_MALFUNCTION_EVENT_TYPE:
                content = gate_malfunction_notification_content(
                    action_type,
                    active_context,
                    previous_notification=_context_bool(
                        active_context.facts.get("malfunction_has_previous_notification")
                    ),
                )
                rendered_title = content["title"]
                rendered_message = content["body"]
            else:
                rendered_title = render_template(title_template, variables)
                rendered_message = render_template(message_template, variables)
            rendered_actions.append(
                {
                    "id": str(action.get("id") or f"action-{len(rendered_actions) + 1}"),
                    "type": action_type,
                    "target_mode": str(action.get("target_mode") or "all"),
                    "target_ids": normalize_string_list(action.get("target_ids"), allow_scalar=False),
                    "title": rendered_title,
                    "message": rendered_message,
                    "title_template": title_template,
                    "message_template": message_template,
                    "gate_malfunction_stages": gate_malfunction_stages,
                    "media": media,
                    "actionable": notification_payloads.normalize_actionable(action.get("actionable")),
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
            # Event contents never override persisted work. Unknown/legacy IDs cannot send.
            self.dispatcher.wake()
            return
        # These visitor transitions reserve notification intents in the pass
        # mutation transaction; realtime cannot produce another delivery run.
        if event.type in {"visitor_pass.created", "visitor_pass.cancelled", "visitor_pass.status_changed",
                          "visitor_pass.used", "visitor_pass.departure_recorded"}:
            return
        for context in visitor_pass_notification_contexts_from_event(event):
            await self.enqueue_notification(context)

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

    async def _gate_malfunction_actions_for_delivery(
        self,
        actions: list[dict[str, Any]],
        context: NotificationContext,
    ) -> list[dict[str, Any]]:
        stage = notification_payloads.normalize_gate_malfunction_stage(context.facts.get("malfunction_stage"))
        selected: list[dict[str, Any]] = []
        for action in actions:
            if not gate_malfunction_action_supports_stage(action, stage):
                continue
            content = gate_malfunction_notification_content(
                str(action.get("type") or ""),
                context,
                previous_notification=_context_bool(
                    context.facts.get("malfunction_has_previous_notification")
                ),
            )
            selected.append(
                {
                    **action,
                    "title": content["title"],
                    "message": content["body"],
                }
            )
        return selected

    async def _deliver_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        config,
        rendered_rule: dict[str, Any],
    ) -> NotificationActionOutcome:
        action_type = str(action.get("type") or "")
        if action_type == "mobile":
            return await self._send_mobile(action, context, config)
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
                    "actions": notification_action_buttons(context),
                },
            )
            return NotificationActionOutcome(delivered=True)
        if action_type == "voice":
            return await self._send_voice(action, config)
        if action_type == "discord":
            return await self._send_discord(action, context, config)
        if action_type == "whatsapp":
            await self._send_whatsapp(action, context, config)
            return NotificationActionOutcome(delivered=True)
        raise NotificationDeliveryError(f"Unsupported notification action: {action_type}")

    async def _send_mobile(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        config,
    ) -> NotificationActionOutcome:
        if "frozen_apprise_indexes" in action:
            configured = [normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)]
            urls = [configured[index] for index in action["frozen_apprise_indexes"]]
            home_assistant_targets = action["frozen_mobile_targets"]
        else:
            urls = self._select_apprise_urls(config.apprise_urls, action)
            home_assistant_targets = await self._select_home_assistant_mobile_targets(config, action)
        if not urls and not home_assistant_targets:
            raise NotificationDeliveryError("No mobile notification endpoints are configured or selected.")
        snapshot = await self._snapshot_attachment(action.get("media") or {})
        attachments = [snapshot.path] if snapshot else []
        failures: list[str] = []
        receipts: list[dict[str, str]] = []
        delivered_any = False
        try:
            delivered_any = await self._send_mobile_apprise(action, context, urls, attachments, failures, receipts=receipts)
            delivered_any = (
                await self._send_mobile_home_assistant(
                    action,
                    context,
                    home_assistant_targets,
                    snapshot,
                    failures,
                    runtime_config=config,
                    receipts=receipts,
                )
                or delivered_any
            )
        finally:
            self._cleanup_mobile_snapshot(snapshot, home_assistant_targets)
        if failures:
            if delivered_any:
                logger.warning(
                    "mobile_notification_partially_delivered",
                    extra={
                        "event_type": context.event_type,
                        "failure_count": len(failures),
                        "delivery_uncertain": _receipt_delivery_uncertain(receipts),
                        "destination_outcomes": receipts,
                    },
                )
                return NotificationActionOutcome(
                    delivered=True,
                    reason="delivered_with_failures",
                    message="At least one mobile endpoint accepted the notification; other endpoints failed.",
                    metadata={
                        "partial_failure": True,
                        "failures": failures,
                        "failure_count": max(len(failures), _receipt_failure_count(receipts)),
                        "accepted_any": True,
                        "review_required": _receipt_delivery_uncertain(receipts),
                        "delivery_uncertain": _receipt_delivery_uncertain(receipts),
                        "destination_outcomes": receipts,
                    },
                )
            raise NotificationDeliveryError(
                "; ".join(failures),
                delivery=_receipt_failure_delivery(receipts),
                destination_outcomes=receipts,
            )
        if not delivered_any:
            raise NotificationDeliveryError(
                "No mobile notification endpoints were delivered.",
                delivery=_receipt_failure_delivery(receipts),
                destination_outcomes=receipts,
            )
        return NotificationActionOutcome(
            delivered=True,
            reason="delivered",
            metadata={"accepted_any": True, "destination_outcomes": receipts},
        )

    async def _send_mobile_apprise(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        urls: list[str],
        attachments: list[str],
        failures: list[str],
        *, receipts: list[dict[str, str]] | None = None,
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
            if receipts is not None:
                receipts.append({"target": "apprise", "delivery": "accepted"})
            return True
        except NotificationDeliveryError as exc:
            if receipts is not None:
                receipts.append({"target": "apprise", "delivery": exc.delivery})
            if exc.delivery == "accepted":
                return True
            failures.append(f"Apprise: {exc}")
            return False
        except Exception:  # noqa: BLE001 - no per-destination result is available.
            if receipts is not None:
                receipts.append({"target": "apprise", "delivery": "unknown"})
            failures.append("Apprise: delivery outcome unknown")
            return False

    async def _send_mobile_home_assistant(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        targets: list[str],
        snapshot: NotificationSnapshotAttachment | None,
        failures: list[str],
        *, runtime_config=None, receipts: list[dict[str, str]] | None = None,
    ) -> bool:
        if not targets:
            return False
        if snapshot and not snapshot.public_url:
            logger.warning(
                "notification_home_assistant_snapshot_omitted",
                extra={
                    "event_type": context.event_type,
                    "reason": "missing_public_base_url",
                },
            )
            self._record_notification_span(
                "Notification Snapshot Omitted",
                context,
                output_payload={
                    "event_type": context.event_type,
                    "channel": "mobile",
                    "reason": "missing_public_base_url",
                    "delivered": False,
                },
            )
        image_url = snapshot.public_url if snapshot and snapshot.public_url else None
        image_content_type = snapshot.content_type if snapshot is not None and image_url else None
        notifier = HomeAssistantMobileAppNotifier()
        delivered_any = False
        for target in targets:
            try:
                mobile_actions = await self._home_assistant_mobile_actions_for_target(
                    action,
                    context,
                    target,
                    runtime_config=runtime_config,
                )
                options = {"runtime_config": runtime_config} if runtime_config is not None else {}
                await notifier.send(
                    HomeAssistantMobileAppTarget(target),
                    str(action.get("title") or context.subject),
                    str(action.get("message") or ""),
                    context,
                    image_url=image_url,
                    image_content_type=image_content_type,
                    actions=mobile_actions,
                    **options,
                )
                delivered_any = True
                if receipts is not None:
                    receipts.append({"target": target, "delivery": "accepted"})
            except NotificationDeliveryError as exc:
                if receipts is not None:
                    receipts.append({"target": target, "delivery": exc.delivery})
                if exc.delivery == "accepted":
                    delivered_any = True
                    continue
                failures.append(f"{target}: {exc}")
            except Exception:  # noqa: BLE001 - the next endpoint may still receive the notification.
                if receipts is not None:
                    receipts.append({"target": target, "delivery": "unknown"})
                failures.append(f"{target}: delivery outcome unknown")
        return delivered_any

    async def _home_assistant_mobile_actions_for_target(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        target: str,
        *,
        runtime_config=None,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = list(home_assistant_notification_actions(context))
        actionable = notification_payloads.normalize_actionable(action.get("actionable"))
        if actionable.get("enabled") and actionable.get("action") == notification_payloads.GATE_OPEN_ACTION:
            gate_action = await get_actionable_notification_service().create_gate_open_action(
                context=context,
                notify_service=target,
                runtime_config=runtime_config,
            )
            if gate_action:
                actions.append(gate_action)
        return actions

    def _cleanup_mobile_snapshot(
        self,
        snapshot: NotificationSnapshotAttachment | None,
        home_assistant_targets: list[str],
    ) -> None:
        if snapshot and not (home_assistant_targets and snapshot.public_url):
            get_snapshot_manager().delete_snapshot_path(snapshot.path)

    async def _send_discord(self, action: dict[str, Any], context: NotificationContext, config) -> NotificationActionOutcome:
        attachments = await self._snapshot_attachments(action.get("media") or {})
        try:
            receipt = await get_discord_messaging_service().send_notification_action(
                action,
                context,
                attachment_paths=attachments,
                config=discord_config_from_runtime(config),
            )
            metadata = receipt if isinstance(receipt, dict) else {}
            outcomes = metadata.get("destination_outcomes") if isinstance(metadata.get("destination_outcomes"), list) else []
            accepted_any = any(
                isinstance(entry, dict) and entry.get("delivery") == "accepted"
                for entry in outcomes
            )
            return NotificationActionOutcome(
                delivered=accepted_any or not outcomes,
                reason="delivered_with_failures" if metadata.get("partial_failure") else "delivered",
                metadata={**metadata, "accepted_any": accepted_any or not outcomes},
            )
        finally:
            for path in attachments:
                get_snapshot_manager().delete_snapshot_path(path)

    async def _send_whatsapp(self, action: dict[str, Any], context: NotificationContext, config) -> None:
        from app.services.messaging.whatsapp_configuration import whatsapp_config_from_runtime
        await get_whatsapp_delivery_service().send_notification_action(
            action,
            context,
            variables=context_variables(context),
            config=whatsapp_config_from_runtime(config),
        )

    async def _send_voice(self, action: dict[str, Any], config) -> NotificationActionOutcome:
        frozen = "frozen_voice_targets" in action
        targets = action["frozen_voice_targets"] if frozen else await self._select_voice_targets(config, action)
        if not targets:
            raise NotificationDeliveryError("No Home Assistant media player is configured or selected.")
        spoken_message = apply_vehicle_tts_phonetics(str(action.get("message") or ""))
        suppression = await self._voice_announcements_preflight(runtime_config=config) if frozen else await self._voice_announcements_preflight()
        if suppression:
            return suppression

        announcer = HomeAssistantTtsAnnouncer()
        failures: list[str] = []
        delivered_any = False
        for target in targets:
            try:
                options = {"runtime_config": config} if frozen else {}
                await announcer.announce(AnnouncementTarget(target), spoken_message, **options)
                delivered_any = True
            except Exception as exc:
                failures.append(f"{target}: {exc}")
        if failures:
            raise NotificationDeliveryError("; ".join(failures))
        if not delivered_any:
            raise NotificationDeliveryError("No Home Assistant media player endpoints were delivered.")
        return NotificationActionOutcome(delivered=True)

    async def _voice_announcements_preflight(self, *, runtime_config=None) -> NotificationActionOutcome | None:
        try:
            options = {"runtime_config": runtime_config} if runtime_config is not None else {}
            state = await _home_assistant_client().get_state(HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID, **options)
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
        endpoint_ids = normalize_string_list(action.get("target_ids"), allow_scalar=False)
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
        endpoint_ids = normalize_string_list(action.get("target_ids"), allow_scalar=False)
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
        endpoint_ids = normalize_string_list(action.get("target_ids"), allow_scalar=False)
        if target_mode == "all" or "home_assistant_tts:*" in endpoint_ids:
            targets = await self._all_media_player_targets(config)
            if targets:
                return targets
            return [config.home_assistant_default_media_player] if config.home_assistant_default_media_player else []

        selected_targets: list[str] = []
        for endpoint_id in endpoint_ids:
            if not endpoint_id.startswith("home_assistant_tts:"):
                continue
            target = endpoint_id.split(":", 1)[1]
            if target == "default":
                target = config.home_assistant_default_media_player
            if target and target != "*":
                selected_targets.append(target)
        if not selected_targets and config.home_assistant_default_media_player:
            selected_targets.append(config.home_assistant_default_media_player)
        return selected_targets

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

    async def _whatsapp_endpoint_catalog(self) -> list[dict[str, Any]]:
        try:
            return await get_whatsapp_delivery_service().available_admin_targets()
        except Exception as exc:
            logger.debug("whatsapp_endpoint_catalog_failed", extra={"error": str(exc)})
            return []

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
            services = await _home_assistant_client().list_services()
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
            states = await _home_assistant_client().list_states()
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

        manager = get_snapshot_manager()
        stored = manager.store_notification_snapshot(snapshot.content, snapshot.content_type)
        return NotificationSnapshotAttachment(
            path=str(stored.path),
            content_type=stored.content_type,
            public_url=manager.notification_snapshot_public_url(stored),
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
            "malfunction_stage": context.facts.get("malfunction_stage"),
            "notification_run_id": context.facts.get("notification_run_id"),
            "severity": context.severity,
            "configured": True,
            "delivered": delivered,
            "error": error,
            "malfunction_id": context.facts.get("malfunction_id"),
            "telemetry_trace_id": context.facts.get("telemetry_trace_id"),
        }



def notification_action_buttons(context: NotificationContext) -> list[dict[str, str]]:
    if context.event_type != "visitor_pass_timeframe_change_requested":
        return []
    pass_id = str(context.facts.get("visitor_pass_id") or "").strip()
    request_id = str(context.facts.get("visitor_pass_timeframe_request_id") or "").strip()
    if not pass_id or not request_id:
        return []
    base_path = f"/api/v1/visitor-passes/{pass_id}/timeframe-requests/{request_id}"
    return [
        {"id": "allow", "label": "Allow", "method": "POST", "path": f"{base_path}/allow"},
        {"id": "deny", "label": "Deny", "method": "POST", "path": f"{base_path}/deny"},
    ]


def home_assistant_notification_actions(context: NotificationContext) -> list[dict[str, str | bool]]:
    if context.event_type != "visitor_pass_timeframe_change_requested":
        return []
    pass_id = str(context.facts.get("visitor_pass_id") or "").strip()
    request_id = str(context.facts.get("visitor_pass_timeframe_request_id") or "").strip()
    if not pass_id or not request_id:
        return []
    return [
        {"action": visitor_pass_timeframe_button_id("allow", pass_id, request_id), "title": "Allow"},
        {"action": visitor_pass_timeframe_button_id("deny", pass_id, request_id), "title": "Deny", "destructive": True},
    ]


def notification_context_from_payload(payload: dict[str, Any]) -> NotificationContext:
    facts = as_dict(payload.get("facts"))
    notification_run_id = str(payload.get("notification_run_id") or "").strip()
    if notification_run_id:
        facts["notification_run_id"] = notification_run_id
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
    elif event_type == GATE_MALFUNCTION_EVENT_TYPE:
        facts.update(
            {
                "message": "Top Gate has remained open long enough to be treated as a malfunction.",
                "subject": "Gate malfunction detected",
                "malfunction_stage": "initial",
                "malfunction_duration": "5m 0s",
                "gate_name": "Top Gate",
                "gate_status": "open",
                "entity_id": "cover.top_gate",
            }
        )
    elif event_type == INTEGRATION_DEGRADED_EVENT_TYPE:
        facts.update(
            {
                "message": "Home Assistant is degraded: Unable to reach Home Assistant.",
                "subject": "Home Assistant degraded",
                "integration_name": "Home Assistant",
                "integration_status": "Degraded",
                "integration_reason": "Unable to reach Home Assistant.",
                "integration_last_connected_at": "2026-05-10T18:42:00+00:00",
                "integration_last_failure_at": "2026-05-10T18:55:35+00:00",
                "source": "home_assistant",
            }
        )
    return NotificationContext(
        event_type=event_type,
        subject=(
            "AB12CDE"
            if event_type == "unauthorized_plate"
            else "Gate malfunction detected"
            if event_type == GATE_MALFUNCTION_EVENT_TYPE
            else "Home Assistant degraded"
            if event_type == INTEGRATION_DEGRADED_EVENT_TYPE
            else "Steph arrived at the gate"
        ),
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
        canonical_key(key): "" if value is None else str(value)
        for key, value in context.facts.items()
    }

    def pick(*keys: str, default: str = "") -> str:
        for key in keys:
            value = facts.get(canonical_key(key))
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
        "visitor_pass_registration",
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
    visitor_pass_time_window = pick(
        "visitor_pass_time_window",
        "visitor_pass_window_label",
        "visitor_pass_current_window",
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
        "IntegrationName": pick("integration_name", "integration", "provider_name"),
        "IntegrationStatus": pick("integration_status", "status"),
        "IntegrationReason": pick("integration_reason", "degraded_reason", "failure_reason", "reason"),
        "IntegrationLastConnectedAt": pick("integration_last_connected_at", "last_connected_at"),
        "IntegrationLastFailureAt": pick("integration_last_failure_at", "last_failure_at"),
        "GarageDoor": pick("garage_door"),
        "EntityId": pick("entity_id"),
        "VisitorName": pick("visitor_name", "visitor_pass_name", default=display_name or context.subject),
        "VisitorPassName": pick("visitor_pass_name", "visitor_name", default=display_name or context.subject),
        "VisitorPassRegistration": visitor_pass_registration,
        "VisitorPassTimeWindow": visitor_pass_time_window,
        "VisitorPassVehicleRegistration": visitor_pass_registration,
        "VisitorPassVehicleMake": visitor_pass_make,
        "VisitorPassVehicleColour": visitor_pass_colour,
        "VisitorPassDurationOnSite": visitor_pass_duration,
        "VisitorPassCurrentWindow": pick("visitor_pass_current_window"),
        "VisitorPassRequestedWindow": pick("visitor_pass_requested_window"),
        "VisitorPassOriginalTime": pick(
            "visitor_pass_original_time",
            "visitor_pass_original_window",
            "visitor_pass_current_window",
        ),
        "VisitorPassRequestedTime": pick(
            "visitor_pass_requested_time",
            "visitor_pass_requested_window",
        ),
        "VisitorPassVisitorMessage": pick("visitor_pass_visitor_message"),
        "NewWinnerName": pick("new_winner_name", "winner_name"),
        "OvertakenName": pick("overtaken_name", "previous_winner_name"),
        "ReadCount": pick("read_count", "leaderboard_read_count"),
        "MaintenanceModeReason": pick("maintenance_mode_reason", "maintenance_reason", "reason"),
        "MalfunctionDuration": pick("malfunction_duration"),
        "MalfunctionOpenedTime": pick("malfunction_opened_time"),
        "MalfunctionFixAttemptTime": pick("malfunction_fix_attempt_time"),
        "MalfunctionFixAttempts": pick("malfunction_fix_attempts"),
        "MalfunctionResolutionTime": pick("malfunction_resolution_time"),
        "MalfunctionStage": pick("malfunction_stage"),
        "LastKnownVehicle": pick("last_known_vehicle"),
    }


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


def gate_malfunction_action_supports_stage(action: dict[str, Any], stage: str) -> bool:
    stages = notification_payloads.normalize_gate_malfunction_stages(action.get("gate_malfunction_stages"))
    return not stages or notification_payloads.normalize_gate_malfunction_stage(stage) in stages


def gate_malfunction_notification_content(
    channel: str,
    context: NotificationContext,
    *,
    previous_notification: bool,
) -> dict[str, str]:
    facts = context.facts
    stage = notification_payloads.normalize_gate_malfunction_stage(facts.get("malfunction_stage"))
    stage_label = notification_payloads.GATE_MALFUNCTION_STAGE_LABELS.get(stage, stage)
    if stage == "resolved":
        title = "Gate malfunction resolved"
    elif stage == "fubar":
        title = "Gate malfunction needs attention"
    elif stage == "initial":
        title = "Gate malfunction detected"
    else:
        title = f"Gate malfunction {stage_label}"
    body = gate_malfunction_plain_body(stage)
    return {
        "title": title[:160],
        "body": postprocess_gate_malfunction_body(
            channel,
            body,
            previous_notification=previous_notification,
            default_body=body or context.subject,
        ),
    }


def gate_malfunction_plain_body(stage: str) -> str:
    normalized_stage = notification_payloads.normalize_gate_malfunction_stage(stage)
    if normalized_stage == "initial":
        return "The gate has malfunctioned and is stuck open. Alfred is trying to resolve it."
    if normalized_stage == "30m":
        return "The gate is still stuck open. Alfred is still working on it."
    if normalized_stage == "60m":
        return "The gate has been stuck open for about an hour. It is not looking good, but Alfred is still on the case."
    if normalized_stage == "2hrs":
        return "The gate has been stuck open for over two hours. Alfred has not been able to fix it yet."
    if normalized_stage == "fubar":
        return "The gate is still stuck open and Alfred has run out of automatic fixes. Please check the gate when you can."
    if normalized_stage == "resolved":
        return "The gate malfunction has been resolved and the gate is closed again."
    return "The gate has malfunctioned and is stuck open. Alfred is trying to resolve it."


def clean_notification_text(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) > 1 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    return text


def postprocess_gate_malfunction_body(
    channel: str,
    body: str,
    *,
    previous_notification: bool,
    default_body: str,
) -> str:
    text = clean_notification_text(body) or clean_notification_text(default_body)
    text = strip_gate_malfunction_prefixes(text)
    if previous_notification:
        text = f"{GATE_MALFUNCTION_UPDATE_PREFIX} {text}".strip()
    if channel == "voice":
        text = f"{GATE_MALFUNCTION_VOICE_PREFIX} {strip_attention_prefix(text)}".strip()
    return text[:500]


def strip_gate_malfunction_prefixes(value: str) -> str:
    text = clean_notification_text(value)
    while True:
        next_text = strip_attention_prefix(text)
        next_text = strip_update_prefix(next_text)
        if next_text == text:
            return text
        text = next_text


def strip_attention_prefix(value: str) -> str:
    return re.sub(r"^\s*attention\.\s*", "", value, flags=re.IGNORECASE).strip()


def strip_update_prefix(value: str) -> str:
    return re.sub(
        r"^\s*gate\s+malfunction\s+update:\s*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


def _context_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def presence_condition_matches(condition: dict[str, Any], present_person_ids: set[str]) -> bool:
    mode = str(condition.get("mode") or "")
    if mode == "no_one_home":
        return not present_person_ids
    if mode == "someone_home":
        return bool(present_person_ids)
    if mode == "person_home":
        return str(condition.get("person_id") or "") in present_person_ids
    return False


def _receipt_failure_count(receipts: list[dict[str, str]]) -> int:
    return sum(
        receipt.get("delivery") in {"not_sent", "rejected", "unknown"}
        for receipt in receipts
        if isinstance(receipt, dict)
    )


def _receipt_delivery_uncertain(receipts: list[dict[str, str]]) -> bool:
    return any(
        receipt.get("delivery") == "unknown"
        for receipt in receipts
        if isinstance(receipt, dict)
    )


def _receipt_failure_delivery(receipts: list[dict[str, str]]) -> str:
    deliveries = {receipt.get("delivery") for receipt in receipts if isinstance(receipt, dict)}
    if "unknown" in deliveries:
        return "unknown"
    if "rejected" in deliveries:
        return "rejected"
    return "not_sent"


def rule_id(rule: NotificationRule | dict[str, Any]) -> str:
    return str(rule.id if isinstance(rule, NotificationRule) else rule.get("id") or "")


def rule_name(rule: NotificationRule | dict[str, Any]) -> str:
    return str(rule.name if isinstance(rule, NotificationRule) else rule.get("name") or "Notification Workflow")


def rule_trigger_event(rule: NotificationRule | dict[str, Any]) -> str:
    return str(rule.trigger_event if isinstance(rule, NotificationRule) else rule.get("trigger_event") or "")


def rule_conditions(rule: NotificationRule | dict[str, Any]) -> list[dict[str, Any]]:
    return notification_payloads.normalize_conditions(rule.conditions if isinstance(rule, NotificationRule) else rule.get("conditions"))


def rule_actions(rule: NotificationRule | dict[str, Any]) -> list[dict[str, Any]]:
    return notification_payloads.normalize_actions(rule.actions if isinstance(rule, NotificationRule) else rule.get("actions"))


def rule_is_active(rule: NotificationRule | dict[str, Any]) -> bool:
    return bool(rule.is_active if isinstance(rule, NotificationRule) else rule.get("is_active", True))


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
