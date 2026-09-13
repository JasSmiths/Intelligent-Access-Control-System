from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.messaging.whatsapp_delivery import WhatsAppDeliveryService, get_whatsapp_delivery_service
from app.services.messaging.identities import WhatsAppIdentityService, get_whatsapp_identity_service
from app.services.visitor_conversations import (HomeAssistantTimeframeAction, VisitorConversationService,
    VisitorConversationDenied, get_visitor_conversation_service)

from app.ai.providers import ChatMessageInput, ProviderNotConfiguredError
from app.core.logging import get_logger
from app.models import User, VisitorPass
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, normalize_registration_number
from app.modules.notifications.base import NotificationContext
from app.services.visitor_passes import VisitorPassError
from app.ai.providers import complete_with_provider_options
from app.services.event_bus import event_bus
from app.ai.providers import get_llm_provider
from app.services.settings import get_runtime_config
from app.services.visitor_passes import get_visitor_pass_service
from app.services.messaging.whatsapp_configuration import load_whatsapp_config
from app.services.dvla import lookup_normalized_vehicle_registration
from app.modules.messaging.whatsapp import WhatsAppIntegrationConfig
from app.services.messaging.whatsapp_helpers import (
    VISITOR_CONCIERGE_PROMPT,
    VISITOR_CONCIERGE_RESTRICTED_REPLY,
    VISITOR_CONCIERGE_TOOL_NAMES,
    VISITOR_PENDING_TIMEFRAME_REPLY,
    VISITOR_TIMEFRAME_APPROVAL_REPLY,
    VisitorPassButtonReply,
    VisitorPassTimeframeDecision,
    VisitorPassTimeframeReply,
    VisitorVehicleLookup,
    extract_message_text,
    first_json_object,
    format_registration_for_display,
    masked_plate_value,
    normalize_llm_timeframe_change_payload,
    parse_button_message,
    parse_datetime_value,
    parse_visitor_pass_button_id,
    parse_visitor_pass_timeframe_confirmation_button_id,
    parse_whatsapp_timestamp,
    sanitize_visitor_abuse_reply,
    sanitize_visitor_alfred_nod,
    sanitize_visitor_privileged_plate_reply,
    strip_visitor_alfred_name_sentences,
    style_visitor_freeform_reply,
    visitor_abuse_fallback_reply,
    visitor_concierge_non_action_reply,
    visitor_concierge_start_message,
    visitor_first_name,
    visitor_message_contains_emoji,
    visitor_message_mentions_alfred,
    visitor_pass_button_id,
    visitor_pass_terminal_message,
    visitor_pass_timeframe_confirmation_button_id,
    visitor_pass_timeframe_llm_context,
    visitor_pass_whatsapp_llm_context,
    visitor_pass_window_label,
    visitor_pass_window_label_from_payload,
    visitor_plate_appears_in_message,
    visitor_plate_confirmation_message,
    visitor_plate_detection_allowed,
    visitor_plate_saved_message,
    visitor_privileged_plate_fallback_reply,
    visitor_registration_not_found_message,
    visitor_reply_requests_registration,
    visitor_window_label_from_values,
)
from app.services.type_helpers import as_dict

logger = get_logger(__name__)

class WhatsAppVisitorConversationService:
    def __init__(self, *, delivery: WhatsAppDeliveryService | None = None,
        conversations: VisitorConversationService | None = None,
        identities: WhatsAppIdentityService | None = None):
        self._delivery = delivery or get_whatsapp_delivery_service()
        self._state = conversations or get_visitor_conversation_service()
        self._identities = identities or get_whatsapp_identity_service()
        self._transport = self._delivery._transport


    async def reserve_custom_message_in_session(
        self, session: AsyncSession, pass_id: uuid.UUID | str, body: str, *,
        actor_user: User, confirmation_token: str | None,
    ):
        """Join the API confirmation transaction; the common dispatcher sends."""
        from app.services.notifications import get_notification_service

        message = str(body or "").strip()
        if not message:
            raise VisitorPassError("Message is required.")
        visitor, origin = await self._state.prepare_manual_notification_origin(
            session, pass_id, actor_user_id=actor_user.id,
            auth_version=actor_user.auth_session_version, kind="custom",
        )
        return await get_notification_service().reserve_confirmed_request(
            session, user=actor_user, action="visitor_pass.whatsapp_send",
            payload={"pass_id": str(visitor.id), "message": body},
            confirmation_token=confirmation_token,
            context=NotificationContext("visitor_custom_message", "Visitor message", "info", {}),
            direct_action={"type": "whatsapp", "delivery_mode": "literal", "target": origin["recipient"],
                "title": "", "message": message[:1024]}, visitor_origin=origin,
        )


    async def _handle_visitor_message(
        self,
        message: dict[str, Any],
        *,
        sender: str,
        visitor_pass: VisitorPass,
        config: WhatsAppIntegrationConfig,
        history_recorded: bool = False,
    ) -> None:
        if not history_recorded:
            await self._record_inbound_visitor_message(visitor_pass, message, sender=sender)
        if await self._state.visitor_reply_is_muted(visitor_pass.id, sender):
            return
        button = parse_button_message(message, parse_visitor_pass_button_id)
        if button:
            await self._handle_visitor_button_reply(button, sender, config=config)
            return
        timeframe_reply = parse_button_message(message, parse_visitor_pass_timeframe_confirmation_button_id)
        if timeframe_reply:
            await self._handle_visitor_timeframe_confirmation_reply(timeframe_reply, sender, config=config)
            return

        text = extract_message_text(message)
        if not text:
            await self._delivery.send_text_message(
                sender,
                "Please reply with your vehicle registration.",
                config=config,
            )
            return
        if re.sub(r"[^a-z0-9]+", "", text.strip().lower()) in {"begin", "start"}:
            await self._state.update_visitor_concierge_status(
                visitor_pass.id,
                "awaiting_visitor_reply",
                detail="Conversation started; waiting for the visitor's vehicle registration.",
            )
            await self._delivery.send_text_message(
                sender,
                visitor_concierge_start_message(visitor_pass),
                config=config,
            )
            return

        await self._process_visitor_text(
            sender,
            visitor_pass,
            text,
            config=config,
            emoji_preferred=visitor_message_contains_emoji(text),
            alfred_mentioned=visitor_message_mentions_alfred(text),
        )

    async def _process_visitor_text(
        self,
        sender: str,
        visitor_pass: VisitorPass,
        text: str,
        *,
        config: WhatsAppIntegrationConfig,
        emoji_preferred: bool = False,
        alfred_mentioned: bool = False,
    ) -> None:
        if await self._state.visitor_reply_is_muted(visitor_pass.id, sender):
            return
        result = await self._visitor_concierge_result(sender, visitor_pass, text, alfred_mentioned=alfred_mentioned)
        action = str(result.get("action") or "")
        if action == "unsupported":
            await self._delivery.send_text_message(sender, VISITOR_CONCIERGE_RESTRICTED_REPLY, config=config)
            return
        if action == "timeframe_change":
            await self._handle_visitor_timeframe_change(sender, visitor_pass, text, result, config=config)
            return
        plate = normalize_registration_number(str(result.get("registration_number") or ""))
        if plate:
            nonce = uuid.uuid4().hex[:12]
            if await self._state.plate_is_known_vehicle(plate):
                await self._state.record_privileged_visitor_plate(visitor_pass.id, sender, plate)
                await self._delivery.send_text_message(
                    sender,
                    await self._visitor_privileged_plate_reply(visitor_pass.visitor_name, plate, text),
                    config=config,
                )
                return
            vehicle_lookup = await self._lookup_visitor_vehicle_details(plate)
            if await self._state.record_visitor_plate_change_attempt(visitor_pass.id, sender, plate):
                await self._trigger_visitor_abuse_mute(
                    sender,
                    visitor_pass,
                    text,
                    reason="plate_changes",
                    config=config,
                )
                return
            if not (vehicle_lookup.found or vehicle_lookup.make or vehicle_lookup.colour):
                await self._state.record_unverified_visitor_plate(visitor_pass.id, sender, plate, vehicle_lookup.error)
                await self._delivery.send_text_message(
                    sender,
                    visitor_registration_not_found_message(plate),
                    config=config,
                )
                return
            await self._state.store_pending_visitor_plate(
                visitor_pass.id,
                sender,
                plate,
                nonce,
                vehicle_make=vehicle_lookup.make,
                vehicle_colour=vehicle_lookup.colour,
                dvla_error=vehicle_lookup.error,
            )
            await self.send_visitor_plate_confirmation(
                sender,
                visitor_pass,
                plate,
                nonce,
                vehicle_make=vehicle_lookup.make,
                vehicle_colour=vehicle_lookup.colour,
                emoji_preferred=emoji_preferred,
                alfred_mentioned=alfred_mentioned,
                alfred_nod=await self._visitor_alfred_name_nod(visitor_pass, text) if alfred_mentioned else "",
                config=config,
            )
            return

        reply_message = str(result.get("message") or "Please reply with your vehicle registration.")
        if alfred_mentioned and not visitor_message_mentions_alfred(reply_message):
            nod = await self._visitor_alfred_name_nod(visitor_pass, text)
            if nod:
                reply_message = f"{reply_message.rstrip()} {nod}"

        if await self._state.record_visitor_post_complete_reply(visitor_pass.id, sender):
            await self._trigger_visitor_abuse_mute(
                sender,
                visitor_pass,
                text,
                reason="post_complete_replies",
                config=config,
            )
            return

        await self._delivery.send_text_message(
            sender,
            style_visitor_freeform_reply(
                reply_message,
                visitor_pass,
                text,
                emoji_preferred=emoji_preferred,
                alfred_mentioned=alfred_mentioned,
            )[:1024],
            config=config,
        )


    async def send_visitor_plate_confirmation(
        self,
        to: str,
        visitor_pass: VisitorPass,
        plate: str,
        nonce: str,
        *,
        vehicle_make: str | None = None,
        vehicle_colour: str | None = None,
        emoji_preferred: bool = False,
        alfred_mentioned: bool = False,
        alfred_nod: str | None = None,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> None:
        body = visitor_plate_confirmation_message(
            visitor_pass,
            plate,
            vehicle_make=vehicle_make,
            vehicle_colour=vehicle_colour,
            emoji_preferred=emoji_preferred,
            alfred_mentioned=alfred_mentioned,
            alfred_nod=alfred_nod,
        )
        await self._delivery.send_interactive_buttons(
            to,
            body,
            [
                {
                    "id": visitor_pass_button_id("confirm", str(visitor_pass.id), nonce),
                    "title": "Confirm",
                },
                {
                    "id": visitor_pass_button_id("change", str(visitor_pass.id), nonce),
                    "title": "Change",
                },
            ],
            config=config,
        )


    async def _handle_visitor_button_reply(
        self, button: VisitorPassButtonReply, sender: str, *, config: WhatsAppIntegrationConfig,
    ) -> None:
        try:
            outcome = await self._state.confirm_plate(button.pass_id, sender, button.nonce, button.decision)
        except VisitorConversationDenied as exc:
            await self._conversation_denied(button.pass_id, sender, exc, config=config)
            return
        except VisitorPassError as exc:
            await self._delivery.send_text_message(sender, f"I couldn't save that registration: {exc}", config=config)
            return
        payload, plate = outcome.visitor_pass, (outcome.request or {}).get("plate")
        if outcome.kind == "plate_change_requested":
            body = "No problem. Please type the new registration."
        elif outcome.kind == "privileged_plate":
            body = await self._visitor_privileged_plate_reply(str(payload.get("visitor_name") or ""), str(plate), "Confirm")
        else:
            if outcome.kind == "plate_arranged":
                await event_bus.publish("visitor_pass.arranged", {"visitor_pass": payload, "source": "whatsapp_visitor"})
            body = visitor_plate_saved_message(payload, fallback_plate=plate,
                emoji_preferred=bool(as_dict(payload.get("source_metadata")).get("whatsapp_visitor_uses_emoji")))
        await self._delivery.send_text_message(sender, body, config=config)


    async def _lookup_visitor_vehicle_details(self, plate: str) -> VisitorVehicleLookup:
        normalized_plate = normalize_registration_number(plate)
        if not normalized_plate:
            return VisitorVehicleLookup()
        try:
            vehicle = await lookup_normalized_vehicle_registration(normalized_plate)
        except DvlaVehicleEnquiryError as exc:
            detail = str(exc)[:500]
            logger.info(
                "visitor_concierge_dvla_lookup_failed",
                extra={
                    "plate": masked_plate_value(normalized_plate),
                    "status_code": exc.status_code,
                    "error": detail[:180],
                },
            )
            return VisitorVehicleLookup(error=detail)
        except Exception as exc:
            detail = str(exc)[:500]
            logger.info(
                "visitor_concierge_dvla_lookup_failed",
                extra={"plate": masked_plate_value(normalized_plate), "error": detail[:180]},
            )
            return VisitorVehicleLookup(error=detail)
        return VisitorVehicleLookup(found=True, make=vehicle.make, colour=vehicle.colour)

    async def _visitor_concierge_result(
        self,
        sender: str,
        visitor_pass: VisitorPass,
        text: str,
        *,
        alfred_mentioned: bool = False,
    ) -> dict[str, str]:
        pass_details = await self._state.get_pass_details(sender)
        runtime = await get_runtime_config()
        if runtime.llm_provider == "local":
            return {
                "action": "reply",
                "message": "Sorry, I can't safely process visitor chat right now. Please contact your host.",
            }
        try:
            provider = get_llm_provider(runtime.llm_provider)
            result = await complete_with_provider_options(
                provider,
                [
                    ChatMessageInput("system", VISITOR_CONCIERGE_PROMPT),
                    ChatMessageInput(
                        "user",
                        json.dumps(
                            {
                                "message": text,
                                "pass": pass_details,
                                "site_timezone": runtime.site_timezone,
                                "current_window": visitor_pass_timeframe_llm_context(visitor_pass, runtime.site_timezone),
                                "conversation_context": visitor_pass_whatsapp_llm_context(visitor_pass),
                                "alfred_mentioned": alfred_mentioned,
                                "allowed_tools": VISITOR_CONCIERGE_TOOL_NAMES,
                            },
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                ],
                max_output_tokens=450,
                request_purpose="whatsapp.visitor_concierge",
            )
            payload = first_json_object(result.text)
            if isinstance(payload, dict):
                action = str(payload.get("action") or "")
                if action == "plate_detected":
                    plate = normalize_registration_number(str(payload.get("registration_number") or ""))
                    if plate:
                        if visitor_plate_appears_in_message(text, plate) and visitor_plate_detection_allowed(visitor_pass, text):
                            return {"action": "plate_detected", "registration_number": plate}
                        logger.info(
                            "visitor_concierge_ignored_context_plate",
                            extra={"pass_id": str(visitor_pass.id), "plate": masked_plate_value(plate)},
                        )
                        return {
                            "action": "reply",
                            "message": visitor_concierge_non_action_reply(visitor_pass, text),
                        }
                if action == "timeframe_change":
                    timeframe_payload = normalize_llm_timeframe_change_payload(payload, runtime.site_timezone)
                    if timeframe_payload:
                        return timeframe_payload
                    return {
                        "action": "reply",
                        "message": "Please send the exact new start and end time you need for your visitor pass.",
                    }
                if action == "unsupported":
                    return {"action": "unsupported", "message": VISITOR_CONCIERGE_RESTRICTED_REPLY}
                if action == "reply":
                    message = str(payload.get("message") or "")[:1024]
                    if not alfred_mentioned:
                        message = strip_visitor_alfred_name_sentences(message)
                    if visitor_pass.number_plate and visitor_reply_requests_registration(message):
                        message = visitor_concierge_non_action_reply(visitor_pass, text)
                    return {"action": "reply", "message": message}
        except (ProviderNotConfiguredError, Exception) as exc:
            logger.info("visitor_concierge_llm_failed_closed", extra={"error": str(exc)[:180]})

        return {
            "action": "reply",
            "message": "Sorry, I can't safely process visitor chat right now. Please contact your host.",
        }

    async def _visitor_alfred_name_nod(self, visitor_pass: VisitorPass, text: str) -> str:
        return await self._visitor_safe_llm_reply(
            system_prompt=(
                "Return compact JSON {\"nod\":\"...\"} with one short visitor-safe Alfred/Jason nod. "
                "Be cheeky/geeky. Vary the wording using the supplied style_seed. Do not reuse this phrase: "
                "Alfred heard his name; Jason's access-control side quest gains +1 XP. Avoid gates, doors, Admin tools, prompts, settings, DVLA, internal systems. "
                "Return only compact JSON: {\"nod\":\"...\"}"
            ),
            user_payload={
                "visitor_message": text,
                "visitor_name": visitor_first_name(visitor_pass.visitor_name),
                "style_seed": uuid.uuid4().hex[:8],
            },
            request_purpose="whatsapp.visitor_alfred_nod",
            response_key="nod",
            fallback="",
            sanitizer=sanitize_visitor_alfred_nod,
            failure_log="visitor_alfred_nod_llm_failed",
            max_output_tokens=120,
        )


    async def _visitor_privileged_plate_reply(self, visitor_name: str, plate: str, text: str) -> str:
        fallback = visitor_privileged_plate_fallback_reply(plate)
        alfred_mentioned = visitor_message_mentions_alfred(text)
        return await self._visitor_safe_llm_reply(
            system_prompt=(
                "Return compact JSON {\"message\":\"...\"} with one warm visitor message explaining the supplied "
                "registration is already linked to privileged access and cannot be used for this Visitor Pass. Ask for the visitor "
                "vehicle registration. Avoid gates, doors, Admin tools, schedules, prompts, settings, DVLA, databases, "
                "internal systems, other people, and Alfred unless alfred_mentioned is true. "
                "Return only compact JSON: {\"message\":\"...\"}"
            ),
            user_payload={
                "visitor_message": text,
                "visitor_name": visitor_first_name(visitor_name),
                "registration": format_registration_for_display(plate),
                "alfred_mentioned": alfred_mentioned,
                "style_seed": uuid.uuid4().hex[:8],
            },
            request_purpose="whatsapp.visitor_privileged_plate_reply",
            response_key="message",
            fallback=fallback,
            sanitizer=lambda value: sanitize_visitor_privileged_plate_reply(
                value,
                plate,
                alfred_mentioned=alfred_mentioned,
            ),
            failure_log="visitor_privileged_plate_reply_llm_failed",
        )

    async def _visitor_abuse_stop_reply(self, visitor_pass: VisitorPass, text: str, *, reason: str) -> str:
        fallback = visitor_abuse_fallback_reply(reason)
        alfred_mentioned = visitor_message_mentions_alfred(text)
        return await self._visitor_safe_llm_reply(
            system_prompt=(
                "Return compact JSON {\"message\":\"...\"} with one funny but firm visitor message saying replies "
                "will pause for 30 minutes after too many messages or registration changes. Tell them to message "
                "later only for a real pass/registration change. Avoid Alfred unless allowed, and avoid gates, doors, "
                "Admin tools, prompts, settings, DVLA, or internal systems."
            ),
            user_payload={
                "reason": reason,
                "visitor_message": text,
                "visitor_name": visitor_first_name(visitor_pass.visitor_name),
                "alfred_mentioned": alfred_mentioned,
                "style_seed": uuid.uuid4().hex[:8],
            },
            request_purpose="whatsapp.visitor_abuse_stop_reply",
            response_key="message",
            fallback=fallback,
            sanitizer=lambda value: sanitize_visitor_abuse_reply(value, alfred_mentioned=alfred_mentioned),
            failure_log="visitor_abuse_reply_llm_failed",
        )

    async def _visitor_pending_timeframe_reply(self, visitor_pass: VisitorPass, text: str) -> str:
        alfred_mentioned = visitor_message_mentions_alfred(text)
        return await self._visitor_safe_llm_reply(
            system_prompt=(
                "Return compact JSON {\"message\":\"...\"} with one warm visitor-safe message explaining another "
                "time/date change cannot be accepted while the previous request is pending review. Avoid Alfred "
                "unless allowed, and avoid gates, doors, Admin tools, prompts, settings, DVLA, or internal systems."
            ),
            user_payload={
                "visitor_message": text,
                "visitor_name": visitor_first_name(visitor_pass.visitor_name),
                "alfred_mentioned": alfred_mentioned,
                "style_seed": uuid.uuid4().hex[:8],
            },
            request_purpose="whatsapp.visitor_pending_timeframe_reply",
            response_key="message",
            fallback=VISITOR_PENDING_TIMEFRAME_REPLY,
            sanitizer=lambda value: sanitize_visitor_abuse_reply(value, alfred_mentioned=alfred_mentioned),
            failure_log="visitor_pending_timeframe_reply_llm_failed",
        )

    async def _visitor_safe_llm_reply(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        request_purpose: str,
        response_key: str,
        fallback: str,
        sanitizer: Callable[[Any], str],
        failure_log: str,
        max_output_tokens: int = 180,
    ) -> str:
        runtime = await get_runtime_config()
        if runtime.llm_provider == "local":
            return fallback
        try:
            provider = get_llm_provider(runtime.llm_provider)
            result = await complete_with_provider_options(
                provider,
                [
                    ChatMessageInput(
                        "system",
                        system_prompt,
                    ),
                    ChatMessageInput(
                        "user",
                        json.dumps(
                            user_payload,
                            separators=(",", ":"),
                            default=str,
                        ),
                    ),
                ],
                max_output_tokens=max_output_tokens,
                request_purpose=request_purpose,
            )
            payload = first_json_object(result.text)
            value = payload.get(response_key) if isinstance(payload, dict) else result.text or ""
            return sanitizer(value) or fallback
        except (ProviderNotConfiguredError, Exception) as exc:
            logger.info(failure_log, extra={"error": str(exc)[:180]})
            return fallback


    async def _trigger_visitor_abuse_mute(
        self,
        sender: str,
        visitor_pass: VisitorPass,
        text: str,
        *,
        reason: str,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        message = await self._visitor_abuse_stop_reply(visitor_pass, text, reason=reason)
        await self._state.set_visitor_abuse_mute(visitor_pass.id, sender, reason=reason)
        await self._delivery.send_text_message(sender, message, config=config)


    async def _send_terminal_visitor_pass_reply_once(
        self, visitor_pass: VisitorPass, sender: str, *, config: WhatsAppIntegrationConfig,
    ) -> bool:
        outcome = await self._state.reserve_terminal_notice(visitor_pass.id, sender)
        if outcome is None:
            return False
        await self._delivery.send_text_message(sender, visitor_pass_terminal_message(outcome.request["status"]),
            config=config, terminal_notice=True, visitor_pass_id=str(visitor_pass.id))
        return True

    async def _handle_visitor_timeframe_change(
        self, sender: str, visitor_pass: VisitorPass, text: str, result: dict[str, Any], *, config: WhatsAppIntegrationConfig,
    ) -> None:
        try:
            outcome = await self._state.request_timeframe_change(visitor_pass.id, sender, text, result)
        except VisitorConversationDenied as exc:
            await self._conversation_denied(str(visitor_pass.id), sender, exc, config=config)
            return
        if outcome.kind == "approval_pending":
            await self._delivery.send_text_message(sender, await self._visitor_pending_timeframe_reply(visitor_pass, text), config=config)
        elif outcome.kind == "invalid_window":
            await self._delivery.send_text_message(sender, "Please send a valid start and end time for your visitor pass.", config=config)
        elif outcome.kind == "confirmation_required":
            pending = outcome.request
            await self.send_visitor_timeframe_confirmation(sender, visitor_pass,
                parse_datetime_value(pending["requested_valid_from"]), parse_datetime_value(pending["requested_valid_until"]),
                pending["id"], config=config)
        elif outcome.kind == "approval_required":
            # The required Admin notice was committed with the pending request.
            await self._delivery.send_text_message(sender, VISITOR_TIMEFRAME_APPROVAL_REPLY, config=config)

    async def send_visitor_timeframe_confirmation(
        self,
        to: str,
        visitor_pass: VisitorPass,
        requested_from: datetime,
        requested_until: datetime,
        request_id: str,
        *,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> None:
        requested_window = visitor_window_label_from_values(requested_from, requested_until)
        body = (
            f"I can update your Visitor Pass to {requested_window}. "
            "Please confirm this change."
        )
        await self._delivery.send_interactive_buttons(
            to,
            body,
            [
                {
                    "id": visitor_pass_timeframe_confirmation_button_id("confirm", str(visitor_pass.id), request_id),
                    "title": "Confirm",
                },
                {
                    "id": visitor_pass_timeframe_confirmation_button_id("change", str(visitor_pass.id), request_id),
                    "title": "Change",
                },
            ],
            config=config,
        )

    async def _handle_visitor_timeframe_confirmation_reply(
        self, reply: VisitorPassTimeframeReply, sender: str, *, config: WhatsAppIntegrationConfig,
    ) -> None:
        try:
            outcome = await self._state.confirm_timeframe_change(reply.pass_id, sender, reply.request_id, reply.decision)
        except VisitorConversationDenied as exc:
            await self._conversation_denied(reply.pass_id, sender, exc, config=config)
            return
        body = ("No problem. Please type the new arrival or departure time you need." if outcome.kind == "change_requested"
            else f"I've updated your allowed timeframe. Your pass is now valid for {visitor_pass_window_label_from_payload(outcome.visitor_pass)}.")
        await self._delivery.send_text_message(sender, body, config=config)


    async def _handle_visitor_timeframe_admin_decision(
        self,
        decision: VisitorPassTimeframeDecision,
        sender: str,
        admin: User,
        *,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        try:
            result = await self.decide_visitor_timeframe_request(
                decision.pass_id,
                decision.request_id,
                decision.decision,
                actor_user=admin,
                admin_phone=sender,
                config=config,
            )
        except Exception as exc:
            await self._delivery.send_text_message(sender, f"I couldn't process that Visitor Pass decision: {exc}", config=config)
            return
        admin_message = str(result.get("admin_message") or "Visitor Pass timeframe request updated.")
        await self._delivery.send_text_message(sender, admin_message, config=config)

    async def decide_visitor_timeframe_request(
        self, pass_id: str, request_id: str, decision: str, *, actor_user: User | None = None,
        actor_label: str | None = None, admin_phone: str | None = None,
        integration_action: HomeAssistantTimeframeAction | None = None,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> dict[str, Any]:
        outcome = await self._state.decide_timeframe_request(pass_id, request_id, decision,
            actor_user=actor_user, actor_label=actor_label, admin_phone=admin_phone, integration_action=integration_action)
        payload = outcome.visitor_pass
        allowed = outcome.kind == "approved"
        # The visitor notice is a required prepared output in the decision
        # transaction. This adapter only returns the decision to its caller.
        return {"ok": True, "decision": "allow" if allowed else "deny", "visitor_pass": payload,
            "admin_message": f"{'Approved' if allowed else 'Denied'} timeframe change for {payload.get('visitor_name') or 'visitor'}."}


    async def _record_inbound_visitor_message(
        self, visitor_pass: VisitorPass, message: dict[str, Any], *, sender: str,
    ) -> None:
        body = extract_message_text(message) or str(message.get("type") or "WhatsApp message").replace("_", " ").title()
        await self._state.record_inbound_visitor_message(visitor_pass.id, sender=sender, body=body,
            kind=str(message.get("type") or "message"), provider_message_id=str(message.get("id") or ""),
            occurred_at=parse_whatsapp_timestamp(message.get("timestamp")))


    async def _conversation_denied(self, pass_id: str, sender: str, error: VisitorConversationDenied, *, config: WhatsAppIntegrationConfig) -> None:
        if error.reason in {"pass_not_found", "phone_mismatch"}:
            await self._identities.audit_denied_sender(sender, {"id": pass_id, "type": "interactive"}, reason=error.reason)
            return
        if error.reason == "pass_no_longer_valid":
            visitor, _state = await self._state.visitor_pass_for_phone(sender)
            if visitor and str(visitor.id) == pass_id:
                await self._send_terminal_visitor_pass_reply_once(visitor, sender, config=config)
            return
        await self._delivery.send_text_message(sender,
            "That confirmation is no longer valid. Please type your registration or new time again.", config=config)


_visitor_service: WhatsAppVisitorConversationService | None = None

def get_whatsapp_visitor_conversation_service() -> WhatsAppVisitorConversationService:
    global _visitor_service
    if _visitor_service is None:
        _visitor_service = WhatsAppVisitorConversationService()
    return _visitor_service
