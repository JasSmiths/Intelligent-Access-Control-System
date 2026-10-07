import asyncio
import copy
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import NotificationRule, NotificationRun, User
from app.modules.announcements.home_assistant_tts import (
    AnnouncementTarget,
    HomeAssistantTtsAnnouncer,
)
from app.modules.home_assistant.client import HomeAssistantClient as DefaultHomeAssistantClient
from app.modules.home_assistant.client import get_home_assistant_client
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
from app.modules.notifications.home_assistant_mobile import (
    HomeAssistantMobileAppNotifier,
    HomeAssistantMobileAppTarget,
)
from app.services.action_confirmations import consume_action_confirmation
from app.services.actionable_notifications import (
    get_actionable_notification_service,
)
from app.services.event_bus import RealtimeEvent, event_bus
from app.services.mutation_context import load_active_admin
from app.services.notification_authorization import NotificationAuthorization
from app.services.notification_dispatch import NotificationDispatcher
from app.services.notification_planning import (
    NotificationPlanner,
    rule_id,
    rule_name,
    rule_trigger_event,
)
from app.services.notification_recipients import NotificationRecipients
from app.services.notification_rendering import (
    composed_from_context,
    context_variables,
)
from app.services.notification_requests import (
    configuration_binding,
    confirmed_origin,
)
from app.services.notification_runs import NotificationRunStore
from app.services.settings import RuntimeConfig, get_runtime_config, get_runtime_config_for_session
from app.services.snapshots import get_snapshot_manager
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    telemetry,
    write_audit_log,
)
from app.services.tts_phonetics import apply_vehicle_tts_phonetics
from app.services.type_helpers import as_dict
from app.services.unifi_protect import get_unifi_protect_service
from app.services.vehicle_information import get_vehicle_information_service
from app.services.workflow_dispatch_ports import NotificationPolicy
from app.services.workflows import notification_payloads
from app.services.workflows.catalog import (
    GATE_MALFUNCTION_EVENT_TYPE,
    INTEGRATION_DEGRADED_EVENT_TYPE,
    notification_actionable_catalog,
    notification_trigger_catalog,
    notification_variable_groups,
)
from app.services.workflows.execution_contracts import (
    NotificationActionOutcome,
    NotificationPlanItem,
    checked_notification_plan,
)
from app.services.workflows.notification_payloads import (
    notification_context_payload,
    trigger_severity,
)
from app.services.workflows.template_recipients import content_for_recipient
from app.services.workflows.visitor_notifications import (
    visitor_pass_notification_contexts_from_event,
)

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
class NotificationSnapshotAttachment:
    path: str
    content_type: str
    public_url: str | None


TRIGGER_CATALOG = notification_trigger_catalog()
ACTIONABLE_NOTIFICATION_CATALOG = notification_actionable_catalog(
    notification_payloads.GATE_OPEN_ACTION
)
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
    "vehicle_time_away_seconds": "8400",
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
}


class NotificationService:
    """DB-backed notification workflow engine.

    DB notification_rules are the only runtime workflow source. This service can
    be invoked directly for tests, and listens for normalized `notification.trigger`
    events for normal runtime delivery.
    """

    def __init__(self, *, run_store: NotificationRunStore | None = None) -> None:
        self._started = False
        self.run_store = run_store if run_store is not None else NotificationRunStore()
        self.planner = NotificationPlanner(
            sessions=lambda: AsyncSessionLocal(), config=lambda: get_runtime_config()
        )
        self.authorization = NotificationAuthorization(
            config=lambda session: get_runtime_config_for_session(session),
            actionable=lambda: get_actionable_notification_service(),
        )
        self.recipients = NotificationRecipients(
            sessions=lambda: AsyncSessionLocal(), clients=lambda: _home_assistant_client()
        )
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

    async def available_integrations(self, config: RuntimeConfig) -> list[dict[str, Any]]:
        apprise_urls = [
            normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)
        ]
        apprise_endpoints = [
            summarize_apprise_url(index, url) for index, url in enumerate(apprise_urls)
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
        home_assistant_mobile_endpoints = (
            await self.recipients.home_assistant_mobile_endpoint_catalog(config)
        )
        mobile_endpoints.extend(home_assistant_mobile_endpoints)

        voice_endpoints = await self.recipients.voice_endpoint_catalog(config)
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
        self,
        session: AsyncSession,
        context: NotificationContext,
        *,
        dispatch_id: uuid.UUID,
    ) -> uuid.UUID:
        """Reserve required delivery in the caller's mutation transaction."""
        return await self.run_store.enqueue_in_session(
            session,
            notification_context_payload(context),
            run_id=dispatch_id,
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
        self,
        session: AsyncSession,
        *,
        user: User,
        action: str,
        payload: dict[str, Any],
        confirmation_token: str,
        context: NotificationContext,
        direct_action: dict[str, Any] | None = None,
        rules_override: list[dict[str, Any]] | None = None,
    ) -> tuple[uuid.UUID, NotificationRun | None]:
        """Confirmation, required request audit and delivery are one transaction.

        The API commits once and then calls dispatch_reserved. A disconnected
        caller cannot erase the accepted output; polling recovers ordinary work.
        """
        prepared = await self.prepare_confirmed_delivery(
            context, action=action, direct_action=direct_action, rules_override=rules_override
        )
        current = await load_active_admin(
            session, user.id, auth_version=user.auth_session_version, lock=True
        )
        confirmation = await consume_action_confirmation(
            session,
            user=current,
            action=action,
            payload=payload,
            confirmation_token=confirmation_token,
            commit=False,
        )
        return await self.reserve_confirmed_in_session(
            session,
            actor_user_id=current.id,
            auth_version=current.auth_session_version,
            operation_id=confirmation.id,
            action=action,
            context=context,
            direct_action=direct_action,
            rules_override=rules_override,
            prepared=prepared,
        )

    async def prepare_confirmed_delivery(
        self,
        context: NotificationContext,
        *,
        action: str,
        direct_action: dict[str, Any] | None = None,
        rules_override: list[dict[str, Any]] | None = None,
    ) -> tuple[list[NotificationPlanItem], str]:
        """Resolve content/audience before acquiring confirmation or actor locks."""
        async with self.run_store.sessions() as read_session:
            config = await get_runtime_config_for_session(read_session)
        if direct_action is not None:
            if direct_action.get("delivery_mode") != "literal":
                raise ValueError("A concrete literal delivery mode is required")
            if (
                direct_action.get("configured_default")
                and direct_action.get("type") == "voice"
                and direct_action.get("target") != config.home_assistant_default_media_player
            ):
                raise NotificationDeliveryError(
                    "The default announcement destination changed. Create a fresh confirmation.",
                    delivery="not_sent",
                )
            plan = checked_notification_plan(
                [
                    {
                        "rule": {"id": action, "name": action, "trigger_event": context.event_type},
                        "action": copy.deepcopy(direct_action),
                        "state": "pending",
                    }
                ]
            )
        else:
            row = NotificationRun(
                context=notification_context_payload(context), rules_override=rules_override
            )
            plan = await self.prepare_delivery_plan(row)
            for item in plan:
                if item.get("state") != "pending":
                    continue
                value = item["action"]
                if value["type"] == "voice":
                    value["frozen_voice_targets"] = await self.recipients.select_voice_targets(
                        config, value
                    )
                elif value["type"] == "mobile":
                    value[
                        "frozen_mobile_targets"
                    ] = await self.recipients.select_home_assistant_mobile_targets(config, value)
                    # Endpoint indexes are safe to retain; URLs may contain secrets.
                    configured = [
                        normalize_apprise_url(url)
                        for url in split_apprise_urls(config.apprise_urls)
                    ]
                    selected = self.recipients.select_apprise_urls(config.apprise_urls, value)
                    value["frozen_apprise_indexes"] = [configured.index(url) for url in selected]
        return plan, configuration_binding(config, plan)

    async def reserve_confirmed_in_session(
        self,
        session: AsyncSession,
        *,
        actor_user_id: uuid.UUID,
        auth_version: int,
        operation_id: uuid.UUID,
        action: str,
        context: NotificationContext,
        direct_action: dict[str, Any] | None = None,
        rules_override: list[dict[str, Any]] | None = None,
        prepared: tuple[list[NotificationPlanItem], str] | None = None,
    ) -> tuple[uuid.UUID, NotificationRun | None]:
        plan, prepared_binding = (
            prepared
            if prepared is not None
            else await self.prepare_confirmed_delivery(
                context,
                action=action,
                direct_action=direct_action,
                rules_override=rules_override,
            )
        )
        user, origin = await confirmed_origin(
            session,
            actor_user_id=actor_user_id,
            auth_version=auth_version,
            operation_id=operation_id,
            action=action,
        )
        run_id = uuid.uuid5(uuid.UUID(str(operation_id)), "notification-delivery")
        payload = notification_context_payload(context)
        config = await get_runtime_config_for_session(session)
        if configuration_binding(config, plan) != prepared_binding:
            raise ValueError("Notification configuration changed while preparing the request")
        origin["configuration_binding"] = configuration_binding(config, plan)
        payload["confirmed_delivery"] = origin
        identity, claimed = await self.run_store.reserve_prepared_in_session(
            session,
            payload,
            run_id=run_id,
            plan=plan,
        )
        if claimed is not None:
            await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action=action + ".requested",
                actor=actor_from_user(user),
                actor_user_id=user.id,
                target_entity="NotificationRun",
                target_id=str(identity),
                metadata={"operation_id": str(operation_id), "authority": "api"},
            )
        return identity, claimed

    async def dispatch_reserved(
        self, run_id: uuid.UUID, claimed: NotificationRun | None = None
    ) -> NotificationWorkflowResult:
        await self.dispatcher.run_once(run_id, claimed=claimed)
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
            notification_context_payload(context),
            rules_override=rules_override,
            run_id=dispatch_id,
        )
        await self.dispatcher.run_once(run_id, claimed=claimed)
        row = await self.run_store.get(run_id)
        result = self.result_from_run(row)
        if raise_on_failure and (result.status != "sent" or result.failed_count):
            raise NotificationDeliveryError(
                row.review_reason
                or "; ".join(result.failures or result.skipped_reasons)
                or "Notification was not delivered."
            )
        return result

    @staticmethod
    def result_from_run(row: NotificationRun) -> NotificationWorkflowResult:
        context = notification_context_from_payload(row.context)
        first = next((x for x in row.delivery_plan or [] if x.get("action")), None)
        notification = (
            ComposedNotification(title=first["action"]["title"], body=first["action"]["message"])
            if first
            else composed_from_context(context)
        )
        return NotificationWorkflowResult(
            notification=notification,
            run_id=str(row.id),
            recovery_status=row.status,
            delivered_count=row.delivered_count,
            failed_count=row.failed_count,
            skipped_count=row.skipped_count,
            failures=list(row.failures),
            skipped_reasons=list(row.skipped_reasons),
        )

    async def _enrich_unknown_vehicle_notification(self, row: NotificationRun) -> None:
        """Enrich the claimed notice independently of post-commit access work.

        The dispatcher checkpoints these facts with the rendered plan. A failed
        optional lookup must not suppress the original unknown-vehicle alert.
        """
        facts = dict(row.context.get("facts") or {})
        registration = facts.get("registration_number")
        if (
            row.context.get("event_type") != "unauthorized_plate"
            or not facts.get("access_event_id")
            or not registration
            or (facts.get("vehicle_make") and facts.get("vehicle_colour") and facts.get("vehicle_model"))
        ):
            return
        try:
            async with asyncio.timeout(3):
                vehicle = (await get_vehicle_information_service().lookup(registration)).information
        except Exception as exc:  # noqa: BLE001 - optional enrichment cannot block an alert.
            logger.warning(
                "notification_vehicle_enrichment_failed",
                extra={"exception_class": type(exc).__name__},
            )
            return
        if not facts.get("vehicle_make") and vehicle.make:
            facts["vehicle_make"] = vehicle.make
        if not facts.get("vehicle_model") and vehicle.model:
            facts["vehicle_model"] = vehicle.model
        colour = facts.get("vehicle_colour") or facts.get("vehicle_color") or vehicle.colour
        if colour:
            facts["vehicle_colour"] = colour
            facts["vehicle_color"] = colour
        row.context = {**row.context, "facts": facts}

    async def prepare_delivery_plan(self, row: NotificationRun) -> list[NotificationPlanItem]:
        await self._enrich_unknown_vehicle_notification(row)
        context = notification_context_from_payload(row.context)
        if row.id is not None:
            context = self._context_with_notification_run_id(context, row.id)
        return await self.planner.build(context, row.rules_override)

    async def authorize_attempt(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        run_id: uuid.UUID,
        *,
        action: dict[str, Any] | None = None,
        item: NotificationPlanItem | None = None,
    ) -> NotificationPolicy:
        return await self.authorization.authorize_attempt(
            session, payload, run_id, action=action, item=item
        )

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
        return await self.authorization.authorize_attempt_with_config(
            session, payload, run_id, action=action, item=item, final=final
        )

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
        return await self.authorization.authorize_confirmed_attempt(
            session, payload, run_id, plan=plan, action=action, item=item
        )

    async def delivery_config(self) -> RuntimeConfig:
        return await get_runtime_config()

    async def deliver_planned_action(
        self, item: NotificationPlanItem, row: NotificationRun, config: RuntimeConfig
    ) -> NotificationActionOutcome:
        context = self._context_with_notification_run_id(
            notification_context_from_payload(row.context), row.id
        )
        action = item["action"]
        if action.get("delivery_mode") == "literal":
            return await self._deliver_literal(action, context, config)
        return await self._deliver_action(item["action"], context, config, item["rule"])

    async def _deliver_literal(
        self, action: dict[str, Any], context: NotificationContext, config: RuntimeConfig
    ) -> NotificationActionOutcome:
        """Preserve manual native bodies; workflow formatting does not apply here."""
        target, body = action["target"], action["message"]
        metadata: dict[str, Any] = {}
        if action["type"] == "voice":
            await HomeAssistantTtsAnnouncer().announce(
                AnnouncementTarget(target), body, runtime_config=config
            )
        elif action["type"] == "mobile":
            if action.get("resident_recovery_output") is not None:
                from app.services.resident_recovery import resolve_mobile_actions

                output_actions = await resolve_mobile_actions(action)
            else:
                output_actions = (
                    await get_actionable_notification_service().resolve_notification_output_actions(
                        action, target=target
                    )
                )
            await HomeAssistantMobileAppNotifier().send(
                HomeAssistantMobileAppTarget(target),
                action["title"],
                body,
                context,
                runtime_config=config,
                actions=output_actions or None,
            )
        else:
            raise NotificationDeliveryError("Unsupported literal notification channel")
        return NotificationActionOutcome(delivered=True, metadata=metadata)

    async def publish_planned_outcome(
        self, item: NotificationPlanItem, row: NotificationRun, outcome: NotificationActionOutcome
    ) -> None:
        context = self._context_with_notification_run_id(
            notification_context_from_payload(row.context), row.id
        )
        payload = {
            **self._event_payload(item["rule"], item["action"], context, outcome.delivered, ""),
            **outcome.metadata,
            "reason": outcome.reason,
            "message": outcome.message,
        }
        if outcome.delivered:
            try:
                identity = uuid.UUID(str(item["rule"].get("id")))
            except ValueError:
                identity = None
            if identity:
                await self._mark_rule_fired(NotificationRule(id=identity))
        self._record_notification_span(
            "Notification Action Suppressed" if outcome.skipped else "Notification Action Sent",
            context,
            output_payload=payload,
        )
        await event_bus.publish(
            "notification.skipped" if outcome.skipped else "notification.sent", payload
        )

    async def publish_planned_failure(
        self,
        item: NotificationPlanItem,
        row: NotificationRun,
        *,
        reason: str = "provider_outcome_unknown",
        requires_review: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        context = self._context_with_notification_run_id(
            notification_context_from_payload(row.context), row.id
        )
        error = (
            "Provider outcome unknown; review required. Automatic retry is disabled."
            if requires_review
            else "The notification provider definitely did not accept this action."
        )
        payload = {
            **self._event_payload(item["rule"], item["action"], context, False, error),
            **(metadata or {}),
            "reason": reason,
            "requires_review": requires_review,
        }
        self._record_notification_span(
            "Notification Action Failed",
            context,
            status="error",
            error=error,
            output_payload=payload,
        )
        await event_bus.publish("notification.failed", payload)

    async def publish_plan_completion(self, row: NotificationRun) -> None:
        context = self._context_with_notification_run_id(
            notification_context_from_payload(row.context), row.id
        )
        for item in row.delivery_plan or []:
            if item["state"] == "skipped" and "action" not in item:
                await self._publish_workflow_skip(context, item["reason"], rule=item.get("rule"))

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
        self._record_notification_span(
            "Notification Workflow Skipped", context, output_payload=payload
        )
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
        except Exception as exc:  # noqa: BLE001 - optional accounting cannot reverse committed delivery.
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
        self, rule: NotificationRule | dict[str, Any], context: NotificationContext
    ) -> bool:
        return await self.planner.conditions_match(rule, context)

    def render_rule(
        self, rule: NotificationRule | dict[str, Any], context: NotificationContext | None = None
    ) -> dict[str, Any]:
        return self.planner.render_rule(
            rule, context or sample_notification_context(rule_trigger_event(rule))
        )

    async def preview_rule(
        self,
        rule: NotificationRule | dict[str, Any],
        context: NotificationContext | None = None,
    ) -> dict[str, Any]:
        return self.render_rule(
            rule, context or sample_notification_context(rule_trigger_event(rule))
        )

    async def _handle_realtime_event(self, event: RealtimeEvent) -> None:
        if event.type == "notification.trigger":
            # Event contents never override persisted work. Unknown/legacy IDs cannot send.
            self.dispatcher.wake()
            return
        # These visitor transitions reserve notification intents in the pass
        # mutation transaction; realtime cannot produce another delivery run.
        if event.type in {
            "visitor_pass.created",
            "visitor_pass.cancelled",
            "visitor_pass.status_changed",
            "visitor_pass.used",
            "visitor_pass.departure_recorded",
        }:
            return
        for context in visitor_pass_notification_contexts_from_event(event):
            await self.enqueue_notification(context)

    async def _deliver_action(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        config: RuntimeConfig,
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
                },
            )
            return NotificationActionOutcome(delivered=True)
        if action_type == "voice":
            return await self._send_voice(action, config)
        raise NotificationDeliveryError(f"Unsupported notification action: {action_type}")

    async def _send_mobile(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        config: RuntimeConfig,
    ) -> NotificationActionOutcome:
        if "frozen_apprise_indexes" in action:
            configured = [
                normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)
            ]
            urls = [configured[index] for index in action["frozen_apprise_indexes"]]
            home_assistant_targets = action["frozen_mobile_targets"]
        else:
            urls = self.recipients.select_apprise_urls(config.apprise_urls, action)
            home_assistant_targets = await self.recipients.select_home_assistant_mobile_targets(
                config, action
            )
        if not urls and not home_assistant_targets:
            raise NotificationDeliveryError(
                "No mobile notification endpoints are configured or selected."
            )
        snapshot = await self._snapshot_attachment(action.get("media") or {})
        attachments = [snapshot.path] if snapshot else []
        failures: list[str] = []
        receipts: list[dict[str, str]] = []
        delivered_any = False
        try:
            if "recipient_content" in action:
                configured = [
                    normalize_apprise_url(url) for url in split_apprise_urls(config.apprise_urls)
                ]
                for url in urls:
                    endpoint = f"apprise:{configured.index(url)}"
                    scoped_action = {
                        **action,
                        **content_for_recipient(action, endpoint, context.subject),
                    }
                    delivered_any = (
                        await self._send_mobile_apprise(
                            scoped_action,
                            context,
                            [url],
                            attachments,
                            failures,
                            receipts=receipts,
                            receipt_target=endpoint,
                        )
                        or delivered_any
                    )
            else:
                delivered_any = await self._send_mobile_apprise(
                    action, context, urls, attachments, failures, receipts=receipts
                )
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
        *,
        receipts: list[dict[str, str]] | None = None,
        receipt_target: str = "apprise",
    ) -> bool:
        if not urls:
            return False
        sender = AppriseNotificationSender(urls="\n".join(urls))
        try:
            await sender.send(
                content_for_recipient(action, receipt_target, context.subject)["title"],
                str(action.get("message") or ""),
                context,
                attachments=attachments,
            )
            if receipts is not None:
                receipts.append({"target": receipt_target, "delivery": "accepted"})
            return True
        except NotificationDeliveryError as exc:
            if receipts is not None:
                receipts.append({"target": receipt_target, "delivery": exc.delivery})
            if exc.delivery == "accepted":
                return True
            failures.append(f"Apprise: {exc}")
            return False
        except Exception:  # noqa: BLE001 - no per-destination result is available.
            if receipts is not None:
                receipts.append({"target": receipt_target, "delivery": "unknown"})
            failures.append("Apprise: delivery outcome unknown")
            return False

    async def _send_mobile_home_assistant(
        self,
        action: dict[str, Any],
        context: NotificationContext,
        targets: list[str],
        snapshot: NotificationSnapshotAttachment | None,
        failures: list[str],
        *,
        runtime_config: RuntimeConfig | None = None,
        receipts: list[dict[str, str]] | None = None,
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
                copy = content_for_recipient(
                    action, f"home_assistant_mobile:{target}", context.subject
                )
                mobile_actions = await self._home_assistant_mobile_actions_for_target(
                    action,
                    context,
                    target,
                    runtime_config=runtime_config,
                )
                options = {"runtime_config": runtime_config} if runtime_config is not None else {}
                await notifier.send(
                    HomeAssistantMobileAppTarget(target),
                    copy["title"],
                    copy["message"],
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
        runtime_config: RuntimeConfig | None = None,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        actionable = notification_payloads.normalize_actionable(action.get("actionable"))
        if (
            actionable.get("enabled")
            and actionable.get("action") == notification_payloads.GATE_OPEN_ACTION
        ):
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

    async def _send_voice(
        self, action: dict[str, Any], config: RuntimeConfig
    ) -> NotificationActionOutcome:
        frozen = "frozen_voice_targets" in action
        targets = (
            action["frozen_voice_targets"]
            if frozen
            else await self.recipients.select_voice_targets(config, action)
        )
        if not targets:
            raise NotificationDeliveryError(
                "No Home Assistant media player is configured or selected."
            )
        suppression = (
            await self._voice_announcements_preflight(runtime_config=config)
            if frozen
            else await self._voice_announcements_preflight()
        )
        if suppression:
            return suppression

        announcer = HomeAssistantTtsAnnouncer()
        failures: list[str] = []
        delivered_any = False
        for target in targets:
            try:
                spoken_message = apply_vehicle_tts_phonetics(
                    content_for_recipient(action, f"home_assistant_tts:{target}")["message"]
                )
                options = {"runtime_config": config} if frozen else {}
                await announcer.announce(AnnouncementTarget(target), spoken_message, **options)
                delivered_any = True
            except Exception as exc:  # noqa: BLE001 - retain each provider failure before proceeding.
                failures.append(f"{target}: {exc}")
        if failures:
            raise NotificationDeliveryError("; ".join(failures))
        if not delivered_any:
            raise NotificationDeliveryError(
                "No Home Assistant media player endpoints were delivered."
            )
        return NotificationActionOutcome(delivered=True)

    async def _voice_announcements_preflight(
        self, *, runtime_config: RuntimeConfig | None = None
    ) -> NotificationActionOutcome | None:
        try:
            options = {"runtime_config": runtime_config} if runtime_config is not None else {}
            state = await _home_assistant_client().get_state(
                HOME_ASSISTANT_ANNOUNCEMENTS_ENTITY_ID, **options
            )
        except Exception as exc:  # noqa: BLE001 - missing current speaker authority suppresses delivery.
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

    async def _snapshot_attachment(
        self, media: dict[str, Any]
    ) -> NotificationSnapshotAttachment | None:
        if not media.get("attach_camera_snapshot") or not media.get("camera_id"):
            return None
        camera_id = str(media["camera_id"])
        try:
            snapshot = await get_unifi_protect_service().snapshot(camera_id, width=960, height=540)
        except Exception as exc:
            raise NotificationDeliveryError(
                f"Unable to capture notification snapshot: {exc}"
            ) from exc

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


def notification_context_from_payload(payload: dict[str, Any]) -> NotificationContext:
    facts = as_dict(payload.get("facts"))
    notification_run_id = str(payload.get("notification_run_id") or "").strip()
    if notification_run_id:
        facts["notification_run_id"] = notification_run_id
    return NotificationContext(
        event_type=str(
            payload.get("event_type") or payload.get("trigger_event") or "integration_test"
        ),
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


def _receipt_failure_count(receipts: list[dict[str, str]]) -> int:
    return sum(
        receipt.get("delivery") in {"not_sent", "rejected", "unknown"}
        for receipt in receipts
        if isinstance(receipt, dict)
    )


def _receipt_delivery_uncertain(receipts: list[dict[str, str]]) -> bool:
    return any(
        receipt.get("delivery") == "unknown" for receipt in receipts if isinstance(receipt, dict)
    )


def _receipt_failure_delivery(receipts: list[dict[str, str]]) -> str:
    deliveries = {receipt.get("delivery") for receipt in receipts if isinstance(receipt, dict)}
    if "unknown" in deliveries:
        return "unknown"
    if "rejected" in deliveries:
        return "rejected"
    return "not_sent"


@lru_cache
def get_notification_service() -> NotificationService:
    return NotificationService()
