"""Visitor conversation state and pending-consent policy, independent of transport.

This owner updates conversation metadata/history using the visitor-pass owner for
pass validity and mutations. WhatsApp/Discord parsing, LLMs and provider delivery
belong to adapters. Existing WhatsApp metadata keys preserve stored contracts.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import NotificationRun, User, Vehicle, VisitorPass
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, actor_from_user, write_audit_log
from app.services.mutation_context import load_active_admin
from app.services.notification_runs import NotificationRunStore
from app.services.event_bus import RealtimeEvent
from app.services.visitor_passes import (VisitorPassError, append_visitor_pass_whatsapp_history,
    get_visitor_pass_service, publish_pass_change, serialize_visitor_pass, visitor_pass_whatsapp_history,
    VISITOR_PASS_WHATSAPP_HISTORY_LIMIT)
from app.services.workflows.visitor_notifications import parse_datetime_value, visitor_timeframe_request_notification_context
from app.services.workflows.visitor_notifications import visitor_window_label_from_values
from app.services.workflows.visitor_notifications import visitor_pass_notification_contexts_from_event
from app.services.workflows.notification_payloads import notification_context_payload
from app.services.workflows.visitor_conversations import (
    VISITOR_ABUSE_MUTE_SECONDS, VISITOR_PLATE_CHANGE_LIMIT, VISITOR_POST_COMPLETE_REPLY_LIMIT,
    _visitor_pending_timeframe, coerce_uuid, masked_contact_phone, normalize_contact_phone,
    recent_iso_timestamps, visitor_abuse_status_detail, visitor_pending_timeframe_request,
    visitor_plate_pending_status_detail, visitor_status_metadata, visitor_vehicle_metadata_text,
    visitor_pending_plate_metadata,
    timeframe_change_within_auto_limit, visitor_timeframe_original_window,
    visitor_timeframe_request_payload, visitor_timeframe_window_payload,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class HomeAssistantTimeframeAction:
    """Narrow authority supplied only by authenticated HA event ingress.

    This is an internal capability, never an HTTP/Alfred input or a human
    approval. The owner also checks the exact persisted request and current pass.
    """

    pass_id: str
    request_id: str
    decision: str


@dataclass(frozen=True)
class VisitorConversationOutcome:
    """Committed conversation result; adapters own rendering and delivery."""

    kind: str
    visitor_pass: dict[str, Any]
    request: dict[str, Any] | None = None
    reason: str | None = None


class VisitorConversationDenied(VisitorPassError):
    def __init__(self, reason: str):
        super().__init__("This visitor conversation action is no longer valid.")
        self.reason = reason


async def _locked_pass(session: AsyncSession, identity: uuid.UUID) -> VisitorPass | None:
    return await session.scalar(
        select(VisitorPass).where(VisitorPass.id == identity).with_for_update()
        .execution_options(populate_existing=True)
    )


def _current_duration_pass(visitor_pass: VisitorPass, *, sender: str | None = None) -> None:
    if visitor_pass.pass_type != VisitorPassType.DURATION:
        raise VisitorConversationDenied("duration_pass_required")
    if sender is not None and normalize_contact_phone(visitor_pass.visitor_phone) != sender:
        raise VisitorConversationDenied("phone_mismatch")
    status = get_visitor_pass_service().status_for(visitor_pass, datetime.now(tz=UTC))
    if status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
        raise VisitorConversationDenied("pass_no_longer_valid")


def _current_request(visitor_pass: VisitorPass, key: str, request_id: str) -> dict[str, Any]:
    metadata = visitor_pass.source_metadata or {}
    pending = metadata.get(key)
    if not isinstance(pending, dict) or str(pending.get("id") or "") != request_id:
        raise VisitorConversationDenied("request_mismatch")
    if pending.get("status") != "pending":
        raise VisitorConversationDenied("request_already_handled")
    service = get_visitor_pass_service()
    if (parse_datetime_value(pending.get("current_valid_from")) != service.window_start(visitor_pass)
            or parse_datetime_value(pending.get("current_valid_until")) != service.window_end(visitor_pass)):
        raise VisitorConversationDenied("request_window_changed")
    start = parse_datetime_value(pending.get("requested_valid_from"))
    end = parse_datetime_value(pending.get("requested_valid_until"))
    if start is None or end is None or end <= start:
        raise VisitorConversationDenied("request_invalid")
    return dict(pending)

def visitor_pass_conversation_is_complete(visitor_pass: VisitorPass) -> bool:
    if not normalize_registration_number(visitor_pass.number_plate):
        return False
    metadata = visitor_pass.source_metadata if isinstance(visitor_pass.source_metadata, dict) else {}
    if metadata.get("whatsapp_pending_plate") or visitor_pending_timeframe_request(metadata) or _visitor_pending_timeframe(metadata, "whatsapp_timeframe_confirmation"):
        return False
    return True


class VisitorConversationService:
    async def plate_is_known_vehicle(self, value: Any, *, session: AsyncSession | None = None) -> bool:
        plate = normalize_registration_number(value)
        if not plate:
            return False
        if session is not None:
            return await session.scalar(select(Vehicle.id).where(Vehicle.registration_number == plate).limit(1)) is not None
        async with AsyncSessionLocal() as owned:
            return await self.plate_is_known_vehicle(plate, session=owned)

    async def confirm_plate(
        self, pass_id: str, sender: str, nonce: str, decision: str,
    ) -> VisitorConversationOutcome:
        if decision not in {"confirm", "change"}:
            raise VisitorConversationDenied("invalid_decision")
        pass_uuid = coerce_uuid(pass_id)
        if pass_uuid is None:
            raise VisitorConversationDenied("pass_not_found")
        async with AsyncSessionLocal() as session:
            stored = await _locked_pass(session, pass_uuid)
            if stored is None:
                raise VisitorConversationDenied("pass_not_found")
            _current_duration_pass(stored, sender=sender)
            metadata = dict(stored.source_metadata or {})
            plate = str(metadata.get("whatsapp_pending_plate") or "")
            if not plate or str(metadata.get("whatsapp_pending_nonce") or "") != nonce:
                raise VisitorConversationDenied("request_mismatch")
            if decision == "change":
                stored.source_metadata = visitor_pending_plate_metadata(
                    metadata, whatsapp_awaiting_change=True,
                    whatsapp_concierge_status="awaiting_visitor_reply",
                    whatsapp_concierge_status_detail="Visitor asked to change the parsed registration.",
                )
                kind = "plate_change_requested"
            elif await self.plate_is_known_vehicle(plate, session=session):
                stored.source_metadata = visitor_pending_plate_metadata(
                    metadata, whatsapp_concierge_status="awaiting_visitor_reply",
                    whatsapp_concierge_status_detail="Visitor tried to confirm a privileged registration; awaiting the visitor vehicle registration.",
                    whatsapp_last_privileged_plate=normalize_registration_number(plate),
                )
                kind = "privileged_plate"
            else:
                arranged = not normalize_registration_number(stored.number_plate)
                await get_visitor_pass_service().update_visitor_plate(
                    session, stored, new_plate=plate,
                    vehicle_make=visitor_vehicle_metadata_text(metadata.get("whatsapp_pending_vehicle_make")),
                    vehicle_colour=visitor_vehicle_metadata_text(metadata.get("whatsapp_pending_vehicle_colour")),
                    actor="Visitor Concierge", metadata={"source": "whatsapp", "phone": masked_contact_phone(sender)},
                )
                stored.source_metadata = visitor_pending_plate_metadata(
                    stored.source_metadata or {}, whatsapp_awaiting_change=False,
                    whatsapp_last_confirmed_at=datetime.now(tz=UTC).isoformat(),
                    whatsapp_concierge_status="complete",
                    whatsapp_concierge_status_detail="Vehicle registration confirmed by visitor.",
                )
                kind = "plate_arranged" if arranged else "plate_saved"
                if arranged:
                    await session.flush()
                    await session.refresh(stored, attribute_names=["updated_at"])
                    origin = RealtimeEvent("visitor_pass.arranged", {"visitor_pass": serialize_visitor_pass(stored),
                        "source": "whatsapp_visitor"}, datetime.now(tz=UTC).isoformat())
                    for context in visitor_pass_notification_contexts_from_event(origin):
                        await NotificationRunStore().enqueue_in_session(session, notification_context_payload(context),
                            run_id=uuid.uuid5(stored.id, f"visitor-arranged:{nonce}:{context.event_type}"))
            payload = await self.commit_visitor_update(session, stored, source="whatsapp_visitor")
        return VisitorConversationOutcome(kind, payload, {"plate": plate})

    async def reserve_terminal_notice(self, pass_id: uuid.UUID, sender: str) -> VisitorConversationOutcome | None:
        async with AsyncSessionLocal() as session:
            stored = await _locked_pass(session, pass_id)
            if stored is None or normalize_contact_phone(stored.visitor_phone) != sender:
                return None
            status = get_visitor_pass_service().status_for(stored, datetime.now(tz=UTC))
            if status in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
                return None
            metadata = dict(stored.source_metadata or {})
            if metadata.get("whatsapp_terminal_notice_sent_at") or metadata.get("whatsapp_terminal_notice_reserved_at"):
                return None
            now = datetime.now(tz=UTC).isoformat()
            stored.source_metadata = {
                **metadata, "whatsapp_terminal_notice_reserved_at": now,
                "whatsapp_terminal_notice_status": status.value,
                "whatsapp_concierge_status_detail": "A terminal-pass notice was reserved for delivery.",
                "whatsapp_status_updated_at": now,
            }
            payload = await self.commit_visitor_update(session, stored, source="whatsapp_visitor")
        return VisitorConversationOutcome("terminal_pass", payload, {"status": status.value})

    async def request_timeframe_change(
        self, pass_id: uuid.UUID, sender: str, text: str, proposed: dict[str, Any],
    ) -> VisitorConversationOutcome:
        """Parse results suggest windows; they never grant access authority."""
        async with AsyncSessionLocal() as session:
            stored = await _locked_pass(session, pass_id)
            if stored is None:
                raise VisitorConversationDenied("pass_not_found")
            _current_duration_pass(stored, sender=sender)
            metadata = dict(stored.source_metadata or {})
            if visitor_pending_timeframe_request(metadata):
                return VisitorConversationOutcome("approval_pending", serialize_visitor_pass(stored))
            service = get_visitor_pass_service()
            current = (service.window_start(stored), service.window_end(stored))
            requested = (
                parse_datetime_value(proposed.get("valid_from")) or current[0],
                parse_datetime_value(proposed.get("valid_until")) or current[1],
            )
            if requested[1] <= requested[0]:
                return VisitorConversationOutcome("invalid_window", serialize_visitor_pass(stored))
            original = visitor_timeframe_original_window(metadata, *current)
            # An LLM direct_apply flag or freeform operator message is not a
            # structured, requester-bound proposal. All suggestions use this
            # existing bounded consent policy until such a producer exists.
            automatic = timeframe_change_within_auto_limit(*original, *requested)
            request = visitor_timeframe_request_payload(
                uuid.uuid4().hex[:12], text, proposed.get("summary"), current, original, requested,
            )
            key = "whatsapp_timeframe_confirmation" if automatic else "whatsapp_timeframe_request"
            stored.source_metadata = {
                **metadata,
                "whatsapp_timeframe_original_window": visitor_timeframe_window_payload(*original),
                key: request,
                "whatsapp_concierge_status": "timeframe_confirmation_pending" if automatic else "timeframe_approval_pending",
                "whatsapp_concierge_status_detail": (
                    "Awaiting visitor confirmation for the requested timeframe change." if automatic
                    else "Visitor requested a timeframe change that needs Admin approval."
                ),
                "whatsapp_status_updated_at": datetime.now(tz=UTC).isoformat(),
            }
            if not automatic:
                await write_audit_log(
                    session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                    action="visitor_pass.timeframe_change_requested", actor="Visitor Concierge",
                    target_entity="VisitorPass", target_id=stored.id, target_label=stored.visitor_name,
                    metadata={"request": request, "phone": masked_contact_phone(sender)},
                )
                await NotificationRunStore().enqueue_in_session(
                    session,
                    notification_context_payload(visitor_timeframe_request_notification_context(serialize_visitor_pass(stored), request)),
                    run_id=uuid.uuid5(stored.id, f"timeframe-request:{request['id']}"),
                )
            payload = await self.commit_visitor_update(session, stored, source="whatsapp_visitor")
        return VisitorConversationOutcome("confirmation_required" if automatic else "approval_required", payload, request)

    async def confirm_timeframe_change(
        self, pass_id: str, sender: str, request_id: str, decision: str,
    ) -> VisitorConversationOutcome:
        if decision not in {"confirm", "change"}:
            raise VisitorConversationDenied("invalid_decision")
        pass_uuid = coerce_uuid(pass_id)
        if pass_uuid is None:
            raise VisitorConversationDenied("pass_not_found")
        async with AsyncSessionLocal() as session:
            stored = await _locked_pass(session, pass_uuid)
            if stored is None:
                raise VisitorConversationDenied("pass_not_found")
            _current_duration_pass(stored, sender=sender)
            pending = _current_request(stored, "whatsapp_timeframe_confirmation", request_id)
            metadata = dict(stored.source_metadata or {})
            now = datetime.now(tz=UTC).isoformat()
            pending.update(status="visitor_requested_change" if decision == "change" else "confirmed", decided_at=now)
            next_metadata = {
                **metadata, "whatsapp_timeframe_confirmation": pending,
                "whatsapp_concierge_status": "awaiting_visitor_reply",
                "whatsapp_concierge_status_detail": (
                    "Visitor asked to change the requested timeframe." if decision == "change"
                    else "Visitor confirmed the requested timeframe change."
                ),
                "whatsapp_status_updated_at": now,
            }
            if decision == "confirm":
                service = get_visitor_pass_service()
                requested = (
                    parse_datetime_value(pending["requested_valid_from"]),
                    parse_datetime_value(pending["requested_valid_until"]),
                )
                original = visitor_timeframe_original_window(metadata, service.window_start(stored), service.window_end(stored))
                if not timeframe_change_within_auto_limit(*original, *requested):
                    raise VisitorConversationDenied("request_requires_admin")
                next_metadata["whatsapp_timeframe_last_change"] = {
                    "status": "visitor_confirmed", "confirmed_at": now,
                    "valid_from": requested[0].isoformat(), "valid_until": requested[1].isoformat(),
                }
                await service.update_pass(
                    session, stored, valid_from=requested[0], valid_until=requested[1],
                    source_metadata=next_metadata, actor="Visitor Concierge",
                )
            else:
                stored.source_metadata = next_metadata
            payload = await self.commit_visitor_update(session, stored, source="whatsapp_visitor")
        return VisitorConversationOutcome("window_updated" if decision == "confirm" else "change_requested", payload, pending)

    async def decide_timeframe_request(
        self, pass_id: str, request_id: str, decision: str, *,
        actor_user: User | None = None, actor_label: str | None = None,
        admin_phone: str | None = None, integration_action: HomeAssistantTimeframeAction | None = None,
    ) -> VisitorConversationOutcome:
        decision = str(decision or "").strip().lower()
        if decision not in {"allow", "deny"}:
            raise VisitorConversationDenied("invalid_decision")
        pass_uuid = coerce_uuid(pass_id)
        if pass_uuid is None:
            raise VisitorConversationDenied("pass_not_found")
        async with AsyncSessionLocal() as session:
            # Actor before domain locks matches all interactive mutations. Labels
            # only describe events; they cannot replace current actor authority.
            current_actor = None
            if actor_user is not None:
                current_actor = await load_active_admin(
                    session, actor_user.id, auth_version=actor_user.auth_session_version, lock=True,
                )
                actor = actor_from_user(current_actor)
            elif (isinstance(integration_action, HomeAssistantTimeframeAction)
                    and integration_action == HomeAssistantTimeframeAction(str(pass_uuid), str(request_id), decision)):
                actor = "Home Assistant Notification"
            else:
                raise VisitorConversationDenied("current_actor_required")
            stored = await _locked_pass(session, pass_uuid)
            if stored is None:
                raise VisitorConversationDenied("pass_not_found")
            _current_duration_pass(stored)
            pending = _current_request(stored, "whatsapp_timeframe_request", str(request_id))
            metadata = dict(stored.source_metadata or {})
            now = datetime.now(tz=UTC).isoformat()
            pending.update(
                status="approved" if decision == "allow" else "denied", decided_at=now,
                decided_by_user_id=str(current_actor.id) if current_actor else None,
                decided_by_phone=masked_contact_phone(admin_phone),
                decision_authority={"version": 1, "kind": "admin" if current_actor else "home_assistant",
                    "user_id": str(current_actor.id) if current_actor else None,
                    "auth_version": current_actor.auth_session_version if current_actor else None},
            )
            next_metadata = {
                **metadata, "whatsapp_timeframe_request": pending,
                "whatsapp_concierge_status": "timeframe_approved" if decision == "allow" else "timeframe_denied",
                "whatsapp_concierge_status_detail": (
                    "Admin approved the visitor's requested timeframe change." if decision == "allow"
                    else "Admin denied the visitor's requested timeframe change."
                ),
                "whatsapp_status_updated_at": now,
            }
            if decision == "allow":
                await get_visitor_pass_service().update_pass(
                    session, stored,
                    valid_from=parse_datetime_value(pending["requested_valid_from"]),
                    valid_until=parse_datetime_value(pending["requested_valid_until"]),
                    source_metadata=next_metadata, actor=actor,
                    actor_user_id=current_actor.id if current_actor else None,
                )
            else:
                stored.source_metadata = next_metadata
            await write_audit_log(
                session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="visitor_pass.timeframe_change_approved" if decision == "allow" else "visitor_pass.timeframe_change_denied",
                actor=actor, actor_user_id=current_actor.id if current_actor else None,
                target_entity="VisitorPass", target_id=stored.id, target_label=stored.visitor_name,
                metadata={"request": pending},
            )
            await self._reserve_timeframe_decision_notice(session, stored, pending, decision)
            payload = await self.commit_visitor_update(session, stored, source="whatsapp_admin")
        return VisitorConversationOutcome("approved" if decision == "allow" else "denied", payload, pending)

    async def _reserve_timeframe_decision_notice(self, session: AsyncSession, visitor: VisitorPass,
        request: dict[str, Any], decision: str) -> None:
        recipient = normalize_contact_phone(visitor.visitor_phone)
        if not recipient:
            return
        service = get_visitor_pass_service()
        window = visitor_window_label_from_values(service.window_start(visitor), service.window_end(visitor))
        body = (f"Your requested timeframe change has been approved. Your pass is now valid for {window}."
            if decision == "allow" else f"Sorry, your requested timeframe change was not approved. Your existing allowed timeframe remains {window}.")
        origin = {"version": 1, "pass_id": str(visitor.id), "request_id": request["id"],
            "decision": decision, "recipient": recipient, "authority": request["decision_authority"]}
        context = {"event_type": "visitor_timeframe_decision", "subject": "Visitor timeframe decision",
            "severity": "info", "facts": {}, "visitor_conversation_origin": origin}
        plan = [{"rule": {"id": "visitor-timeframe-decision", "name": "Visitor timeframe decision",
            "trigger_event": "visitor_timeframe_decision"},
            "action": {"type": "whatsapp", "delivery_mode": "literal", "target": recipient,
                "title": "", "message": body}, "state": "pending"}]
        await NotificationRunStore().enqueue_prepared_in_session(session, context,
            run_id=uuid.uuid5(visitor.id, f"timeframe-decision:{request['id']}:{decision}"), plan=plan)

    async def prepare_manual_notification_origin(
        self, session: AsyncSession, pass_id, *, actor_user_id, auth_version: int,
        kind: str, source: str | None = None,
    ) -> tuple[VisitorPass, dict[str, Any]]:
        """Read and lock the exact visitor scope before reserving its output."""
        if kind not in {"custom", "outreach"}:
            raise VisitorConversationDenied("notification_kind_invalid")
        actor = await load_active_admin(session, actor_user_id, auth_version=auth_version, lock=True)
        identity = coerce_uuid(str(pass_id))
        visitor = await _locked_pass(session, identity) if identity is not None else None
        if visitor is None:
            raise VisitorConversationDenied("pass_not_found")
        if kind == "custom":
            _current_duration_pass(visitor)
        recipient = normalize_contact_phone(visitor.visitor_phone)
        if not recipient and kind == "custom":
            raise VisitorConversationDenied("phone_required")
        if kind == "outreach" and (source not in {"ui", "alfred"}
                or visitor.creation_source != source or visitor.created_by_user_id != actor.id):
            raise VisitorConversationDenied("outreach_creator_changed")
        return visitor, {"version": 1, "kind": kind, "pass_id": str(visitor.id), "recipient": recipient,
            "source": source, "actor_label": actor_from_user(actor), "authority": {"version": 1, "kind": "admin", "user_id": str(actor.id),
                "auth_version": actor.auth_session_version}}

    async def _authorize_manual_notification(self, session, origin, run_id):
        from app.services.mutation_context import MutationError

        identity = coerce_uuid(origin.get("pass_id"))
        authority = origin.get("authority")
        if (identity is None or not isinstance(authority, dict) or authority.get("version") != 1
                or authority.get("kind") != "admin" or type(authority.get("auth_version")) is not int):
            return "visitor_notification_origin_invalid"
        if origin.get("kind") == "custom":
            operation = coerce_uuid(origin.get("operation_id"))
            if operation is None or run_id != uuid.uuid5(operation, "notification-delivery"):
                return "visitor_notification_identity_invalid"
        elif run_id != uuid.uuid5(identity, "visitor-outreach"):
            return "visitor_notification_identity_invalid"
        try:
            visitor, current = await self.prepare_manual_notification_origin(
                session, identity, actor_user_id=authority.get("user_id"), auth_version=authority["auth_version"],
                kind=origin["kind"], source=origin.get("source"),
            )
        except (VisitorConversationDenied, MutationError):
            return "visitor_notification_no_longer_authorized"
        if not current["recipient"] or current["recipient"] != origin.get("recipient") or current["authority"] != authority:
            return "visitor_notification_recipient_changed"
        try:
            _current_duration_pass(visitor)
        except VisitorConversationDenied:
            return "visitor_notification_pass_invalid"
        return None

    async def authorize_notification_in_session(self, session: AsyncSession, origin: dict[str, Any],
        run_id: uuid.UUID) -> str | None:
        """Only the exact committed timeframe decision may produce this notice.

        Return a stable skip reason; this grants neither pass mutation nor
        hardware authority. Historical label-based decisions have no versioned
        authority binding and cannot authorize a prepared notification.
        """
        if not isinstance(origin, dict) or origin.get("version") != 1:
            return "visitor_decision_origin_invalid"
        if origin.get("kind") in {"custom", "outreach"}:
            return await self._authorize_manual_notification(session, origin, run_id)
        identity = coerce_uuid(origin.get("pass_id"))
        request_id, decision = str(origin.get("request_id") or ""), origin.get("decision")
        authority = origin.get("authority")
        if (identity is None or not request_id or decision not in {"allow", "deny"}
                or run_id != uuid.uuid5(identity, f"timeframe-decision:{request_id}:{decision}")
                or not isinstance(authority, dict) or authority.get("version") != 1):
            return "visitor_decision_origin_invalid"
        if authority.get("kind") == "admin":
            from app.services.mutation_context import MutationError

            try:
                await load_active_admin(session, authority.get("user_id"), auth_version=authority.get("auth_version"), lock=True)
            except MutationError:
                return "visitor_decision_actor_changed"
        elif authority != {"version": 1, "kind": "home_assistant", "user_id": None, "auth_version": None}:
            return "visitor_decision_origin_invalid"
        visitor = await _locked_pass(session, identity)
        if visitor is None:
            return "visitor_decision_pass_missing"
        try:
            _current_duration_pass(visitor)
        except VisitorConversationDenied:
            return "visitor_decision_pass_invalid"
        if normalize_contact_phone(visitor.visitor_phone) != origin.get("recipient"):
            return "visitor_decision_recipient_changed"
        pending = (visitor.source_metadata or {}).get("whatsapp_timeframe_request")
        if (not isinstance(pending, dict) or str(pending.get("id")) != request_id
                or pending.get("status") != ("approved" if decision == "allow" else "denied")
                or pending.get("decision_authority") != authority):
            return "visitor_decision_request_changed"
        service = get_visitor_pass_service()
        prefix = "requested" if decision == "allow" else "current"
        if (parse_datetime_value(pending.get(f"{prefix}_valid_from")) != service.window_start(visitor)
                or parse_datetime_value(pending.get(f"{prefix}_valid_until")) != service.window_end(visitor)):
            return "visitor_decision_window_changed"
        return None

    async def prepare_notification_output(self, session, row_snapshot, index: int, outcome: dict[str, Any]):
        """Prepare a exact-pass projection; the notification owner commits it.

        Accepted delivery is recorded even if a later revocation or phone edit
        prevents another send. The journal remains the authority for certainty.
        """
        origin = (row_snapshot.context or {}).get("visitor_conversation_origin")
        if not isinstance(origin, dict) or origin.get("version") != 1:
            return None
        identity = coerce_uuid(origin.get("pass_id"))
        visitor = await _locked_pass(session, identity) if identity is not None else None
        if visitor is None:
            return None
        plan = row_snapshot.delivery_plan or []
        if index < 0 or index >= len(plan) or not isinstance(plan[index].get("action"), dict):
            return None
        action = plan[index]["action"]
        if action.get("type") != "whatsapp" or action.get("target") != origin.get("recipient"):
            return None
        state = outcome.get("state")
        if state not in {"accepted", "unknown", "skipped", "failed"}:
            raise ValueError("Visitor notification output requires explicit delivery certainty")
        output_key = f"{row_snapshot.id}:{index}"
        if any((entry.get("metadata") or {}).get("notification_output") == output_key
                for entry in visitor_pass_whatsapp_history(visitor)):
            return None
        kind = origin.get("kind") or "timeframe_decision"
        body = (str(action.get("message") or "") if action.get("delivery_mode") != "whatsapp_template"
            else f"Template {action.get('template_name') or ''}: " + " · ".join(str(value) for value in action.get("body_parameters", [])))

        async def apply():
            metadata = dict(visitor.source_metadata or {})
            timestamp = datetime.now(tz=UTC).isoformat()
            queued_at = row_snapshot.queued_at
            previous = parse_datetime_value(metadata.get("whatsapp_last_notification_queued_at"))
            if previous is None or previous <= queued_at:
                changes = {"whatsapp_last_notification_run_id": str(row_snapshot.id),
                    "whatsapp_last_notification_queued_at": queued_at.isoformat(),
                    "whatsapp_last_message_status": "sent" if state == "accepted" else state,
                    "whatsapp_last_message_status_at": timestamp}
                current_status = metadata.get("whatsapp_concierge_status") or ""
                if kind == "custom" or kind == "outreach" and current_status in {
                        "", "awaiting_visitor_reply", "welcome_message_sent", "message_received", "message_read"}:
                    changes.update(whatsapp_status_updated_at=timestamp,
                        whatsapp_concierge_status=("awaiting_visitor_reply" if kind == "custom" else "welcome_message_sent")
                            if state == "accepted" else f"whatsapp_delivery_{state}",
                        whatsapp_concierge_status_detail=("WhatsApp delivery accepted; awaiting visitor reply."
                            if state == "accepted" else f"WhatsApp delivery {state}; inspect the delivery record before sending again."))
                if outcome.get("provider_message_id"):
                    changes["whatsapp_last_message_id"] = outcome["provider_message_id"]
                visitor.source_metadata = {**metadata, **changes}
            entry = append_visitor_pass_whatsapp_history(visitor, direction="outbound" if state == "accepted" else "status",
                body=body, kind="template" if action.get("delivery_mode") == "whatsapp_template" else "text",
                actor_label="IACS", provider_message_id=outcome.get("provider_message_id"), status=state,
                metadata={"origin": "dashboard_custom" if kind == "custom" else kind,
                    "notification_output": output_key, "notification_run_id": str(row_snapshot.id), "delivery": state,
                    "sender_user_id": (origin.get("authority") or {}).get("user_id"),
                    "sender_label": origin.get("actor_label"), "phone": masked_contact_phone(origin.get("recipient"))})
            if entry is not None:
                entry["id"] = uuid.uuid5(row_snapshot.id, f"visitor-history:{index}").hex
            await write_audit_log(session, category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="visitor_pass.whatsapp_custom_message_sent" if kind == "custom" and state == "accepted"
                    else f"visitor_pass.whatsapp_delivery.{state}",
                actor=origin.get("actor_label") or "Visitor Conversation", target_entity="VisitorPass", target_id=visitor.id,
                target_label=visitor.visitor_name, outcome="succeeded" if state == "accepted" else state,
                metadata={"notification_run_id": str(row_snapshot.id), "action_index": index, "delivery": state,
                    "message_id": outcome.get("provider_message_id")})
        return apply

    async def get_notification_result(self, pass_id, run_id, index: int = 0) -> dict[str, Any]:
        """Read the canonical journal and exact-pass history without mutation."""
        pass_identity, run_identity = coerce_uuid(str(pass_id)), coerce_uuid(str(run_id))
        async with AsyncSessionLocal() as session:
            visitor = await session.get(VisitorPass, pass_identity) if pass_identity is not None else None
            run = await session.get(NotificationRun, run_identity) if run_identity is not None else None
            origin = (run.context or {}).get("visitor_conversation_origin") if run is not None else None
            if visitor is None or not isinstance(origin, dict) or origin.get("pass_id") != str(visitor.id):
                raise LookupError("Visitor notification result not found.")
            key = f"{run.id}:{index}"
            entry = next((item for item in visitor_pass_whatsapp_history(visitor)
                if (item.get("metadata") or {}).get("notification_output") == key), None)
            if entry is None:
                entry = self._notification_history_entry(run, index, await session.scalar(select(func.clock_timestamp())))
            return {"visitor_pass": serialize_visitor_pass(visitor), "message": entry,
                "notification_run_id": str(run.id), "notification_status": run.status}

    @staticmethod
    def _notification_history_entry(run: NotificationRun, index: int, now: datetime) -> dict[str, Any]:
        plan = run.delivery_plan or []
        if index < 0 or index >= len(plan) or not isinstance(plan[index].get("action"), dict):
            raise LookupError("Visitor notification action not found.")
        item, action = plan[index], plan[index]["action"]
        origin = (run.context or {}).get("visitor_conversation_origin") or {}
        state = item.get("state")
        if state == "attempting" and (run.lease_expires_at is None or run.lease_expires_at <= now):
            state = "unknown"
        elif state == "pending" and run.status == "review_required":
            state = "review_required"
        template = action.get("delivery_mode") == "whatsapp_template"
        body = (f"Template {action.get('template_name') or ''}: " + " · ".join(str(value) for value in action.get("body_parameters", []))
            if template else str(action.get("message") or ""))
        return {"id": uuid.uuid5(run.id, f"visitor-history:{index}").hex,
            "direction": "outbound" if state == "accepted" else "status", "kind": "template" if template else "text",
            "body": body, "actor_label": "IACS", "provider_message_id": item.get("provider_message_id"), "status": state,
            "created_at": run.updated_at.isoformat(), "metadata": {"notification_output": f"{run.id}:{index}",
                "notification_run_id": str(run.id), "delivery": state, "projection": "notification_journal",
                "review_reason": run.review_reason, "origin": "dashboard_custom" if origin.get("kind") == "custom"
                    else origin.get("kind") or "timeframe_decision"}}

    async def notification_history(self, pass_id) -> list[dict[str, Any]]:
        """Merge retained legacy history with read-only durable output truth.

        This administrative projection is not conversation input for the LLM.
        It neither claims work nor changes pass metadata when a worker died.
        """
        identity = coerce_uuid(str(pass_id))
        async with AsyncSessionLocal() as session:
            visitor = await session.get(VisitorPass, identity) if identity is not None else None
            if visitor is None:
                raise LookupError("Visitor Pass not found.")
            history = visitor_pass_whatsapp_history(visitor)
            keys = {(entry.get("metadata") or {}).get("notification_output") for entry in history}
            runs = list((await session.scalars(select(NotificationRun).where(NotificationRun.recovery_version == 1,
                NotificationRun.context["visitor_conversation_origin"]["pass_id"].astext == str(identity))
                .order_by(NotificationRun.queued_at.desc(), NotificationRun.id.desc())
                .limit(VISITOR_PASS_WHATSAPP_HISTORY_LIMIT))).all())
            now = await session.scalar(select(func.clock_timestamp()))
            for run in runs:
                for index, item in enumerate((run.delivery_plan or [])[:100]):
                    if not isinstance(item, dict) or not isinstance(item.get("action"), dict):
                        continue
                    if item["action"].get("type") == "whatsapp" and f"{run.id}:{index}" not in keys:
                        history.append(self._notification_history_entry(run, index, now))
            return sorted(history, key=lambda entry: str(entry.get("created_at") or ""))[-VISITOR_PASS_WHATSAPP_HISTORY_LIMIT:]

    async def record_inbound_visitor_message(
        self, pass_id: uuid.UUID, *, sender: str, body: str, kind: str,
        provider_message_id: str, occurred_at: datetime,
    ) -> None:
        if not body:
            return
        async with AsyncSessionLocal() as session:
            stored = await _locked_pass(session, pass_id)
            if stored is None or normalize_contact_phone(stored.visitor_phone) != sender:
                return
            append_visitor_pass_whatsapp_history(
                stored, direction="inbound", kind=kind, body=body,
                actor_label=stored.visitor_name or "Visitor", provider_message_id=provider_message_id,
                occurred_at=occurred_at, metadata={"phone": masked_contact_phone(sender)},
            )
            await self.commit_visitor_update(session, stored, source="whatsapp_message")

    async def get_pass_details(self, phone_number: str) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            visitor_pass, state = await get_visitor_pass_service().messaging_pass_for_phone(
                session,
                normalize_contact_phone(phone_number),
                refresh_status=False,
            )
            if not visitor_pass:
                return {"found": False, "state": state}
            return {"found": True, "state": state, "visitor_pass": serialize_visitor_pass(visitor_pass)}


    async def clear_visitor_abuse_mute(
        self,
        pass_id: uuid.UUID | str,
        *,
        actor_user: User,
    ) -> dict[str, Any]:
        pass_uuid = coerce_uuid(pass_id)
        if not pass_uuid:
            raise VisitorPassError("Visitor Pass not found.")
        async with AsyncSessionLocal() as session:
            actor_user = await load_active_admin(session, actor_user.id,
                auth_version=actor_user.auth_session_version, lock=True)
            actor_label = actor_from_user(actor_user)
            visitor_pass = await _locked_pass(session, pass_uuid)
            if not visitor_pass:
                raise VisitorPassError("Visitor Pass not found.")
            if visitor_pass.pass_type != VisitorPassType.DURATION:
                raise VisitorPassError("WhatsApp controls are only available for duration Visitor Passes.")
            metadata = dict(visitor_pass.source_metadata or {})
            muted_until = str(metadata.get("whatsapp_abuse_muted_until") or "").strip()
            muted_reason = str(metadata.get("whatsapp_abuse_muted_reason") or "").strip()
            if muted_until or muted_reason:
                metadata.pop("whatsapp_abuse_muted_until", None)
                metadata.pop("whatsapp_abuse_muted_reason", None)
                metadata["whatsapp_concierge_status_detail"] = f"Visitor abuse cooldown was cleared by {actor_label}."
                metadata["whatsapp_status_updated_at"] = datetime.now(tz=UTC).isoformat()
                visitor_pass.source_metadata = metadata
                append_visitor_pass_whatsapp_history(
                    visitor_pass,
                    direction="status",
                    kind="operator_action",
                    body=f"{actor_label} unblocked Visitor Concierge replies for this pass.",
                    actor_label="IACS",
                    metadata={
                        "origin": "dashboard_unblock",
                        "sender_user_id": str(actor_user.id),
                        "muted_reason": muted_reason or None,
                        "muted_until": muted_until or None,
                    },
                )
                await write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_INTEGRATIONS,
                    action="visitor_pass.whatsapp_abuse_cooldown_cleared",
                    actor=actor_label,
                    actor_user_id=actor_user.id,
                    target_entity="VisitorPass",
                    target_id=visitor_pass.id,
                    target_label=visitor_pass.visitor_name,
                    metadata={
                        "muted_reason": muted_reason or None,
                        "muted_until": muted_until or None,
                    },
                )
            visitor_pass_payload = await self.commit_visitor_update(session, visitor_pass, source="whatsapp_unblock")
        return visitor_pass_payload


    async def visitor_pass_for_phone(self, sender: str) -> tuple[VisitorPass | None, str]:
        async with AsyncSessionLocal() as session:
            service = get_visitor_pass_service()
            visitor_pass, state = await service.messaging_pass_for_phone(session, sender, refresh_status=False)
            if visitor_pass:
                # Detach scalar data from the short-lived session for the webhook worker.
                _ = visitor_pass.id, visitor_pass.visitor_name, visitor_pass.visitor_phone
            return visitor_pass, state


    async def store_pending_visitor_plate(
        self,
        pass_id: uuid.UUID,
        sender: str,
        plate: str,
        nonce: str,
        *,
        vehicle_make: str | None = None,
        vehicle_colour: str | None = None,
        dvla_error: str | None = None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            visitor_pass = await _locked_pass(session, pass_id)
            if not visitor_pass:
                return
            if normalize_contact_phone(visitor_pass.visitor_phone) != sender:
                return
            visitor_pass.source_metadata = visitor_status_metadata(
                visitor_pass.source_metadata or {},
                "visitor_replied",
                detail=visitor_plate_pending_status_detail(vehicle_make, vehicle_colour),
                extra={
                    "whatsapp_pending_plate": plate,
                    "whatsapp_pending_nonce": nonce,
                    "whatsapp_pending_vehicle_make": visitor_vehicle_metadata_text(vehicle_make),
                    "whatsapp_pending_vehicle_colour": visitor_vehicle_metadata_text(vehicle_colour),
                    "whatsapp_pending_vehicle_lookup_error": str(dvla_error or "")[:500] or None,
                    "whatsapp_pending_at": datetime.now(tz=UTC).isoformat(),
                    "whatsapp_awaiting_change": False,
                },
            )
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")


    async def visitor_reply_is_muted(self, pass_id: uuid.UUID | str, sender: str) -> bool:
        pass_uuid = coerce_uuid(str(pass_id))
        if not pass_uuid:
            return False
        async with AsyncSessionLocal() as session:
            visitor_pass = await _locked_pass(session, pass_uuid)
            if not visitor_pass or normalize_contact_phone(visitor_pass.visitor_phone) != sender:
                return False
            metadata = dict(visitor_pass.source_metadata or {})
            muted_until = parse_datetime_value(metadata.get("whatsapp_abuse_muted_until"))
            if not muted_until:
                return False
            if muted_until > datetime.now(tz=UTC):
                return True
            metadata.pop("whatsapp_abuse_muted_until", None)
            metadata.pop("whatsapp_abuse_muted_reason", None)
            visitor_pass.source_metadata = metadata
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
        return False


    async def set_visitor_abuse_mute(self, pass_id: uuid.UUID | str, sender: str, *, reason: str) -> None:
        pass_uuid = coerce_uuid(str(pass_id))
        if not pass_uuid:
            return
        muted_until = datetime.now(tz=UTC) + timedelta(seconds=VISITOR_ABUSE_MUTE_SECONDS)
        async with AsyncSessionLocal() as session:
            visitor_pass = await _locked_pass(session, pass_uuid)
            if not visitor_pass or normalize_contact_phone(visitor_pass.visitor_phone) != sender:
                return
            metadata = dict(visitor_pass.source_metadata or {})
            visitor_pass.source_metadata = {
                **metadata,
                "whatsapp_abuse_muted_until": muted_until.isoformat(),
                "whatsapp_abuse_muted_reason": reason,
                "whatsapp_concierge_status_detail": visitor_abuse_status_detail(reason),
                "whatsapp_status_updated_at": datetime.now(tz=UTC).isoformat(),
            }
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")


    async def record_visitor_plate_change_attempt(self, pass_id: uuid.UUID | str, sender: str, plate: str) -> bool:
        return await self.record_visitor_reply_limit(
            pass_id,
            sender,
            "whatsapp_plate_change_attempts",
            VISITOR_PLATE_CHANGE_LIMIT,
            extra=lambda visitor_pass: (
                {"whatsapp_last_plate_change_attempt": normalize_registration_number(plate)}
                if normalize_registration_number(visitor_pass.number_plate)
                and normalize_registration_number(plate)
                and normalize_registration_number(visitor_pass.number_plate) != normalize_registration_number(plate)
                else None
            ),
        )


    async def record_visitor_post_complete_reply(self, pass_id: uuid.UUID | str, sender: str) -> bool:
        return await self.record_visitor_reply_limit(
            pass_id,
            sender,
            "whatsapp_post_complete_reply_times",
            VISITOR_POST_COMPLETE_REPLY_LIMIT,
            extra=lambda visitor_pass: {} if visitor_pass_conversation_is_complete(visitor_pass) else None,
        )


    async def record_visitor_reply_limit(
        self,
        pass_id: uuid.UUID | str,
        sender: str,
        metadata_key: str,
        limit: int,
        *,
        extra: Callable[[VisitorPass], dict[str, Any] | None],
    ) -> bool:
        pass_uuid = coerce_uuid(str(pass_id))
        if not pass_uuid:
            return False
        now = datetime.now(tz=UTC)
        async with AsyncSessionLocal() as session:
            visitor_pass = await _locked_pass(session, pass_uuid)
            if not visitor_pass or normalize_contact_phone(visitor_pass.visitor_phone) != sender:
                return False
            extra_metadata = extra(visitor_pass)
            if extra_metadata is None:
                return False
            metadata = dict(visitor_pass.source_metadata or {})
            timestamps = [*recent_iso_timestamps(metadata.get(metadata_key), now=now), now.isoformat()]
            visitor_pass.source_metadata = {
                **metadata,
                **extra_metadata,
                metadata_key: timestamps[-limit:],
                "whatsapp_status_updated_at": now.isoformat(),
            }
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
        return len(timestamps) >= limit


    async def record_unverified_visitor_plate(
        self,
        pass_id: uuid.UUID | str,
        sender: str,
        plate: str,
        error: str | None,
    ) -> None:
        await self.record_visitor_plate_rejection(
            pass_id,
            sender,
            {
                "whatsapp_last_unverified_plate": normalize_registration_number(plate),
                "whatsapp_last_unverified_plate_error": str(error or "")[:500] or None,
                "whatsapp_concierge_status_detail": (
                    "Visitor sent a registration that could not be found; awaiting a corrected registration."
                ),
            },
        )


    async def record_privileged_visitor_plate(
        self,
        pass_id: uuid.UUID | str,
        sender: str,
        plate: str,
    ) -> None:
        await self.record_visitor_plate_rejection(
            pass_id,
            sender,
            {
                "whatsapp_last_privileged_plate": normalize_registration_number(plate),
                "whatsapp_concierge_status_detail": (
                    "Visitor sent a privileged registration that cannot be used; awaiting the visitor vehicle registration."
                ),
            },
        )


    async def record_visitor_plate_rejection(
        self,
        pass_id: uuid.UUID | str,
        sender: str,
        metadata_update: dict[str, Any],
    ) -> None:
        pass_uuid = coerce_uuid(str(pass_id))
        if not pass_uuid:
            return
        async with AsyncSessionLocal() as session:
            visitor_pass = await _locked_pass(session, pass_uuid)
            if not visitor_pass or normalize_contact_phone(visitor_pass.visitor_phone) != sender:
                return
            metadata = dict(visitor_pass.source_metadata or {})
            visitor_pass.source_metadata = visitor_status_metadata(metadata, "awaiting_visitor_reply", extra=metadata_update)
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")


    async def commit_visitor_update(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        source: str,
    ) -> dict[str, Any]:
        await session.commit()
        await session.refresh(visitor_pass)
        payload = serialize_visitor_pass(visitor_pass)
        await publish_pass_change("visitor_pass.updated", {"visitor_pass": payload, "source": source})
        return payload


    async def update_visitor_concierge_status(
        self,
        pass_id: uuid.UUID | str,
        status: str,
        *,
        detail: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        pass_uuid = coerce_uuid(pass_id)
        if not pass_uuid:
            return
        async with AsyncSessionLocal() as session:
            visitor_pass = await _locked_pass(session, pass_uuid)
            if not visitor_pass:
                return
            visitor_pass.source_metadata = visitor_status_metadata(
                visitor_pass.source_metadata or {},
                status,
                detail=detail,
                error=error,
                extra=extra,
            )
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")


    async def update_visitor_concierge_status_for_phone(
        self,
        phone_number: str,
        status: str,
        *,
        detail: str | None = None,
        error: str | None = None,
    ) -> None:
        phone = normalize_contact_phone(phone_number)
        if not phone:
            return
        async with AsyncSessionLocal() as session:
            visitor_pass, state = await get_visitor_pass_service().messaging_pass_for_phone(session, phone, refresh_status=False)
            if not visitor_pass or state not in {"active", "scheduled"}:
                await session.commit()
                return
            visitor_pass = await _locked_pass(session, visitor_pass.id)
            if not visitor_pass or normalize_contact_phone(visitor_pass.visitor_phone) != phone:
                return
            visitor_pass.source_metadata = visitor_status_metadata(
                visitor_pass.source_metadata or {},
                status,
                detail=detail,
                error=error,
            )
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_status")


    async def update_visitor_delivery_status_for_phone(
        self,
        phone_number: str,
        status: str,
        *,
        message_id: str | None = None,
    ) -> None:
        phone = normalize_contact_phone(phone_number)
        if not phone or status not in {"message_received", "message_read"}:
            return
        async with AsyncSessionLocal() as session:
            visitor_pass, state = await get_visitor_pass_service().messaging_pass_for_phone(session, phone, refresh_status=False)
            if not visitor_pass or state not in {"active", "scheduled"}:
                await session.commit()
                return
            visitor_pass = await _locked_pass(session, visitor_pass.id)
            if not visitor_pass or normalize_contact_phone(visitor_pass.visitor_phone) != phone:
                return
            metadata = dict(visitor_pass.source_metadata or {})
            current_status = str(metadata.get("whatsapp_concierge_status") or "").strip()
            stored_message_id = str(metadata.get("whatsapp_last_message_id") or "").strip()
            if stored_message_id and message_id and stored_message_id != message_id:
                await session.commit()
                return
            now = datetime.now(tz=UTC).isoformat()
            next_metadata = {
                **metadata,
                "whatsapp_last_message_status": status,
                "whatsapp_last_message_status_at": now,
            }
            if message_id:
                next_metadata["whatsapp_last_message_id"] = message_id
            can_update_concierge_status = current_status in {
                "",
                "awaiting_visitor_reply",
                "welcome_message_sent",
                "message_received",
                "message_read",
            }
            if current_status == "message_read" and status == "message_received":
                can_update_concierge_status = False
            if can_update_concierge_status:
                next_metadata["whatsapp_concierge_status"] = status
                next_metadata["whatsapp_concierge_status_detail"] = (
                    "WhatsApp reported the visitor message as read."
                    if status == "message_read"
                    else "WhatsApp reported the visitor message as received."
                )
                next_metadata["whatsapp_status_updated_at"] = now
            visitor_pass.source_metadata = next_metadata
            await self.commit_visitor_update(session, visitor_pass, source="whatsapp_status")


    async def record_outbound_visitor_message(
        self,
        recipient: str,
        body: str,
        *,
        kind: str,
        provider_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not str(body or "").strip():
            return
        try:
            async with AsyncSessionLocal() as session:
                visitor_pass, _state = await get_visitor_pass_service().messaging_pass_for_phone(session, recipient, refresh_status=False)
                if not visitor_pass:
                    return
                visitor_pass = await _locked_pass(session, visitor_pass.id)
                if not visitor_pass or normalize_contact_phone(visitor_pass.visitor_phone) != recipient:
                    return
                append_visitor_pass_whatsapp_history(
                    visitor_pass,
                    direction="outbound",
                    kind=kind,
                    body=body,
                    actor_label="IACS",
                    provider_message_id=provider_message_id,
                    metadata=metadata,
                )
                await self.commit_visitor_update(session, visitor_pass, source="whatsapp_message")
        except Exception as exc:
            logger.debug("visitor_pass_whatsapp_history_record_failed", extra={"error": str(exc)[:180]})



def get_visitor_conversation_service() -> VisitorConversationService:
    return VisitorConversationService()
