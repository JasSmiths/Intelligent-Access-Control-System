import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from app.ai.providers import ChatMessageInput, ProviderNotConfiguredError, get_llm_provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import GateMalfunctionState, NotificationActionContext, Person, User
from app.models.enums import GateMalfunctionStatus
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.modules.notifications.home_assistant_mobile import (
    HomeAssistantMobileAppNotifier,
    HomeAssistantMobileAppTarget,
)
from app.modules.registry import UnsupportedModuleError, get_gate_controller
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_INTEGRATIONS,
    actor_from_user,
    audit_log_event_payload,
    write_audit_log,
)

logger = get_logger(__name__)

GATE_OPEN_ACTION = "gate.open"
GATE_FORCE_OPEN_ACTION = "gate.force_open"
GATE_OPEN_PREFIX = "iacs:gate_open:"
GATE_FORCE_OPEN_PREFIX = "iacs:gate_force_open:"
ACTION_TOKEN_BYTES = 24
NORMAL_TOKEN_TTL = timedelta(minutes=10)
FORCE_TOKEN_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class ActionIdentity:
    person: Person
    user: User | None


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


@dataclass(frozen=True)
class GateActionOutcome:
    accepted: bool
    detail: str
    state: str = "unknown"
    reason: str = ""
    skipped_before_command: bool = False
    malfunction_id: uuid.UUID | None = None
    malfunction_duration_seconds: int | None = None


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
    """Creates and executes one-time Home Assistant mobile notification actions."""

    async def create_gate_open_action(
        self,
        *,
        context: NotificationContext,
        notify_service: str,
    ) -> dict[str, str] | None:
        if context.event_type != "unauthorized_plate":
            return None
        if not _is_home_assistant_mobile_notify_service(notify_service):
            return None
        registration_number = _registration_from_context(context)
        if not registration_number:
            return None

        async with AsyncSessionLocal() as session:
            identity = await self._identity_for_notify_service(session, notify_service)
            if not identity:
                logger.warning(
                    "actionable_notification_identity_unavailable",
                    extra={"notify_service": notify_service, "registration_number": registration_number},
                )
                return None

            token = _new_token()
            row = NotificationActionContext(
                token_hash=_token_hash(token),
                action=GATE_OPEN_ACTION,
                notify_service=notify_service,
                registration_number=registration_number,
                access_event_id=_optional_uuid(context.facts.get("access_event_id")),
                telemetry_trace_id=_trace_id(context.facts.get("telemetry_trace_id")),
                person_id=identity.person.id,
                actor_user_id=identity.user.id if identity.user else None,
                expires_at=datetime.now(tz=UTC) + NORMAL_TOKEN_TTL,
                metadata_={
                    "source_event_type": context.event_type,
                    "source_subject": context.subject,
                },
            )
            session.add(row)
            await session.commit()

        return {"action": f"{GATE_OPEN_PREFIX}{token}", "title": "Open Gate"}

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
        action = GATE_FORCE_OPEN_ACTION if force else GATE_OPEN_ACTION
        bound, invalid_reason = await self._consume_context(token, expected_action=action)
        if not bound:
            await self._audit_unbound_failure(action=action, reason=invalid_reason)
            return GateActionOutcome(False, invalid_reason, reason=invalid_reason, skipped_before_command=True)

        async with AsyncSessionLocal() as session:
            identity = await self._identity_for_notify_service(session, bound.notify_service)
        if not identity or (bound.person_id and identity.person.id != bound.person_id):
            detail = "This notification action is no longer linked to exactly one active person."
            await self._record_outcome(bound, "failed_identity", detail)
            await self._send_result_notification(
                bound,
                title="Gate action not available",
                message=detail,
            )
            await self._write_gate_audit(
                bound,
                action=action,
                person=identity.person if identity else None,
                user=identity.user if identity else None,
                outcome="failed",
                level="error",
                detail=detail,
                state="unknown",
                force=force,
                event_data=event_data,
            )
            return GateActionOutcome(False, detail, reason=detail, skipped_before_command=True)

        outcome = await self._execute_gate(bound, identity, force=force)
        await self._record_outcome(bound, "success" if outcome.accepted else "failed", outcome.detail)
        await self._write_gate_audit(
            bound,
            action=action,
            person=identity.person,
            user=identity.user,
            outcome="success" if outcome.accepted else "failed",
            level="info" if outcome.accepted else "error",
            detail=outcome.detail,
            state=outcome.state,
            force=force,
            event_data=event_data,
            malfunction_id=outcome.malfunction_id,
            malfunction_duration_seconds=outcome.malfunction_duration_seconds,
        )

        if force:
            await self._send_force_result(bound, outcome)
        elif outcome.accepted:
            await self._send_success_result(bound, outcome)
        elif not outcome.accepted:
            await self._send_failure_follow_up(bound, outcome)

        await event_bus.publish(
            "gate.actionable_notification_executed",
            {
                "action": action,
                "context_id": str(bound.id),
                "registration_number": bound.registration_number,
                "access_event_id": str(bound.access_event_id) if bound.access_event_id else None,
                "person_id": str(identity.person.id),
                "actor_user_id": str(identity.user.id) if identity.user else None,
                "accepted": outcome.accepted,
                "force": force,
                "state": outcome.state,
                "detail": outcome.detail,
            },
        )
        return outcome

    async def _consume_context(
        self,
        token: str,
        *,
        expected_action: str,
    ) -> tuple[BoundActionContext | None, str]:
        now = datetime.now(tz=UTC)
        token_hash = _token_hash(token)
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(NotificationActionContext).where(NotificationActionContext.token_hash == token_hash)
            )
            if not row:
                return None, "Unknown or expired notification action."
            if row.action != expected_action:
                row.outcome = "failed_action_mismatch"
                row.outcome_detail = "Notification action type did not match the stored context."
                await session.commit()
                await self._send_result_notification(
                    self._bound_from_row(row),
                    title="Gate action not available",
                    message="This notification action no longer matches the stored gate request.",
                )
                return None, "Notification action type did not match the stored context."
            if row.consumed_at:
                await self._send_result_notification(
                    self._bound_from_row(row),
                    title="Gate action already used",
                    message="This gate notification action has already been used.",
                )
                return None, "This notification action has already been used."
            if row.expires_at <= now:
                row.outcome = "expired"
                row.outcome_detail = "Notification action expired before it was used."
                await session.commit()
                await self._send_result_notification(
                    self._bound_from_row(row),
                    title="Gate action expired",
                    message="This gate notification action has expired.",
                )
                return None, "This notification action has expired."

            row.consumed_at = now
            row.outcome = "consumed"
            await session.commit()
            return self._bound_from_row(row), ""

    async def _execute_gate(
        self,
        bound: BoundActionContext,
        identity: ActionIdentity,
        *,
        force: bool,
    ) -> GateActionOutcome:
        malfunction = await _active_gate_malfunction()
        if malfunction:
            detail = await self._malfunction_failure_message(
                bound,
                identity,
                malfunction,
                force=force,
            )
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
        try:
            result = await get_gate_controller(settings.gate_controller).open_gate(
                reason,
                bypass_schedule=force,
            )
        except UnsupportedModuleError as exc:
            return GateActionOutcome(False, str(exc), reason=reason)
        except Exception as exc:
            logger.warning(
                "actionable_notification_gate_open_failed",
                extra={
                    "context_id": str(bound.id),
                    "registration_number": bound.registration_number,
                    "force": force,
                    "error": str(exc),
                },
            )
            return GateActionOutcome(False, str(exc), reason=reason)

        return GateActionOutcome(
            accepted=result.accepted,
            state=result.state.value,
            detail=result.detail or ("Gate command accepted." if result.accepted else "Gate command failed."),
            reason=reason,
        )

    async def _send_failure_follow_up(
        self,
        bound: BoundActionContext,
        outcome: GateActionOutcome,
    ) -> None:
        force_action = None if outcome.malfunction_id else await self._create_force_action(bound)
        message = outcome.detail if outcome.malfunction_id else (
            f"The gate was not opened for {bound.registration_number}. "
            f"{outcome.detail or 'The gate command failed.'}"
        )
        await self._send_result_notification(
            bound,
            title="Gate did not open",
            message=message,
            actions=[force_action] if force_action else None,
        )

    async def _send_success_result(self, bound: BoundActionContext, outcome: GateActionOutcome) -> None:
        await self._send_result_notification(
            bound,
            title="Gate opened",
            message=(
                f"The gate open command for {bound.registration_number} was accepted. "
                f"{outcome.detail or 'The gate controller accepted the request.'}"
            ),
        )

    async def _send_force_result(self, bound: BoundActionContext, outcome: GateActionOutcome) -> None:
        if outcome.malfunction_id:
            title = "Gate still malfunctioning"
            message = outcome.detail or _fallback_generic_gate_failure(bound)
        elif outcome.accepted:
            title = "Gate opened"
            message = f"The force-open command for {bound.registration_number} was accepted."
        else:
            title = "Force open failed"
            message = (
                f"The force-open command for {bound.registration_number} failed. "
                f"{outcome.detail or 'The gate command failed.'}"
            )
        await self._send_result_notification(bound, title=title, message=message)

    async def _create_force_action(self, parent: BoundActionContext) -> dict[str, str] | None:
        token = _new_token()
        async with AsyncSessionLocal() as session:
            stored_parent = await session.get(NotificationActionContext, parent.id)
            if not stored_parent:
                return None
            row = NotificationActionContext(
                token_hash=_token_hash(token),
                action=GATE_FORCE_OPEN_ACTION,
                notify_service=parent.notify_service,
                registration_number=parent.registration_number,
                access_event_id=parent.access_event_id,
                telemetry_trace_id=parent.telemetry_trace_id,
                person_id=parent.person_id,
                actor_user_id=parent.actor_user_id,
                parent_context_id=parent.id,
                expires_at=datetime.now(tz=UTC) + FORCE_TOKEN_TTL,
                metadata_={"source": "normal_action_failure"},
            )
            session.add(row)
            await session.commit()
        return {
            "action": f"{GATE_FORCE_OPEN_PREFIX}{token}",
            "title": "Force Open Gate",
            "destructive": True,
        }

    async def _send_result_notification(
        self,
        bound: BoundActionContext,
        *,
        title: str,
        message: str,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        if not _is_home_assistant_mobile_notify_service(bound.notify_service):
            return
        try:
            await HomeAssistantMobileAppNotifier().send(
                HomeAssistantMobileAppTarget(bound.notify_service),
                title,
                message,
                NotificationContext(
                    event_type="actionable_gate_notification",
                    subject=title,
                    severity="warning",
                    facts={
                        "registration_number": bound.registration_number,
                        "vehicle_registration_number": bound.registration_number,
                        "access_event_id": str(bound.access_event_id) if bound.access_event_id else "",
                        "telemetry_trace_id": bound.telemetry_trace_id or "",
                        "message": message,
                    },
                ),
                actions=actions,
            )
        except NotificationDeliveryError as exc:
            logger.warning(
                "actionable_notification_follow_up_failed",
                extra={
                    "context_id": str(bound.id),
                    "notify_service": bound.notify_service,
                    "error": str(exc),
                },
            )

    async def _record_outcome(self, bound: BoundActionContext, outcome: str, detail: str) -> None:
        async with AsyncSessionLocal() as session:
            row = await session.get(NotificationActionContext, bound.id)
            if not row:
                return
            row.outcome = outcome
            row.outcome_detail = detail[:2000] if detail else ""
            await session.commit()

    async def _write_gate_audit(
        self,
        bound: BoundActionContext,
        *,
        action: str,
        person: Person | None,
        user: User | None,
        outcome: str,
        level: str,
        detail: str,
        state: str,
        force: bool,
        event_data: dict[str, Any] | None,
        malfunction_id: uuid.UUID | None = None,
        malfunction_duration_seconds: int | None = None,
    ) -> None:
        actor = actor_from_user(user) if user else _actor_from_person(person)
        async with AsyncSessionLocal() as session:
            row = await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action=(
                    "gate.open.actionable_notification.force"
                    if force
                    else "gate.open.actionable_notification"
                ),
                actor=actor,
                actor_user_id=user.id if user else None,
                target_entity="Gate",
                target_id=bound.access_event_id,
                target_label="Home Assistant Gate",
                outcome=outcome,
                level=level,
                trace_id=bound.telemetry_trace_id,
                metadata={
                    "action": action,
                    "context_id": str(bound.id),
                    "parent_context_id": str(bound.parent_context_id) if bound.parent_context_id else None,
                    "registration_number": bound.registration_number,
                    "access_event_id": str(bound.access_event_id) if bound.access_event_id else None,
                    "person_id": str(person.id) if person else str(bound.person_id) if bound.person_id else None,
                    "person_label": person.display_name if person else None,
                    "notify_service": bound.notify_service,
                    "force": force,
                    "state": state,
                    "detail": detail,
                    "malfunction_id": str(malfunction_id) if malfunction_id else None,
                    "malfunction_duration_seconds": malfunction_duration_seconds,
                    "home_assistant_event_device_id": (event_data or {}).get("device_id"),
                },
            )
            await session.commit()
            await session.refresh(row)
            await event_bus.publish("audit.log.created", audit_log_event_payload(row))

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
            result = await provider.complete(
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
                ]
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
        except Exception as exc:
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
        result = await provider.complete(
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
            ]
        )
        return _clean_llm_notification_text(result.text)

    async def _audit_unbound_failure(self, *, action: str, reason: str) -> None:
        async with AsyncSessionLocal() as session:
            row = await write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action=(
                    "gate.open.actionable_notification.force"
                    if action == GATE_FORCE_OPEN_ACTION
                    else "gate.open.actionable_notification"
                ),
                actor="Home Assistant Notification",
                target_entity="Gate",
                target_label="Home Assistant Gate",
                outcome="failed",
                level="error",
                metadata={"action": action, "reason": reason},
            )
            await session.commit()
            await session.refresh(row)
            await event_bus.publish("audit.log.created", audit_log_event_payload(row))

    async def _identity_for_notify_service(self, session: Any, notify_service: str) -> ActionIdentity | None:
        people = (
            await session.scalars(
                select(Person).where(
                    Person.home_assistant_mobile_app_notify_service == notify_service,
                    Person.is_active.is_(True),
                )
            )
        ).all()
        if len(people) != 1:
            return None
        person = people[0]
        users = (
            await session.scalars(
                select(User).where(
                    User.person_id == person.id,
                    User.is_active.is_(True),
                )
            )
        ).all()
        return ActionIdentity(person=person, user=users[0] if len(users) == 1 else None)

    def _bound_from_row(self, row: NotificationActionContext) -> BoundActionContext:
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
        )


def _new_token() -> str:
    return secrets.token_urlsafe(ACTION_TOKEN_BYTES)


def _token_hash(token: str) -> str:
    return hmac.new(settings.auth_secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


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
    return str(value or "").startswith("notify.mobile_app_")


def _actor_from_person(person: Person | None) -> str:
    if not person:
        return "Home Assistant Notification"
    return f"{person.display_name} (Home Assistant notification)"


async def _is_maintenance_mode_active() -> bool:
    from app.services.maintenance import is_maintenance_mode_active

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
