from __future__ import annotations

# ruff: noqa: F403,F405

import asyncio
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import ChatMessageInput, ProviderNotConfiguredError
from app.core.logging import get_logger
from app.models import User, VisitorPass
from app.models.enums import VisitorPassStatus, VisitorPassType
from app.modules.dvla.vehicle_enquiry import DvlaVehicleEnquiryError, normalize_registration_number
from app.modules.notifications.base import NotificationContext, NotificationDeliveryError
from app.services import whatsapp_messaging as wm
from app.services.messaging.whatsapp_helpers import *  # noqa: F401,F403
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, actor_from_user
from app.services.type_helpers import as_dict, as_dict_list

logger = get_logger(__name__)

class WhatsAppVisitorConversationMixin:
    async def get_pass_details(self, phone_number: str) -> dict[str, Any]:
        async with wm.AsyncSessionLocal() as session:
            visitor_pass, state = await wm.get_visitor_pass_service().messaging_pass_for_phone(
                session,
                normalize_whatsapp_phone_number(phone_number),
            )
            if not visitor_pass:
                return {"found": False, "state": state}
            return {"found": True, "state": state, "visitor_pass": wm.serialize_visitor_pass(visitor_pass)}

    async def send_visitor_pass_custom_message(
        self,
        pass_id: uuid.UUID | str,
        body: str,
        *,
        actor_user: User,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> dict[str, Any]:
        message_body = str(body or "").strip()
        if not message_body:
            raise wm.VisitorPassError("Message is required.")
        pass_uuid = coerce_uuid(pass_id)
        if not pass_uuid:
            raise wm.VisitorPassError("Visitor Pass not found.")
        config = config or await wm.load_whatsapp_config()
        if not config.configured:
            raise NotificationDeliveryError("WhatsApp integration is not enabled or configured.")

        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_uuid)
            if not visitor_pass:
                raise wm.VisitorPassError("Visitor Pass not found.")
            if visitor_pass.pass_type != VisitorPassType.DURATION:
                raise wm.VisitorPassError("WhatsApp custom messages are only available for duration Visitor Passes.")
            recipient = normalize_whatsapp_phone_number(visitor_pass.visitor_phone)
            if not recipient:
                raise wm.VisitorPassError("This Visitor Pass does not have a WhatsApp phone number.")
            service = wm.get_visitor_pass_service()
            await service.refresh_statuses(session=session, publish=False)
            if visitor_pass.status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
                raise wm.VisitorPassError(f"{visitor_pass.status.value.title()} visitor passes cannot be messaged.")

            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": message_body[:1024]},
            }
            result = await self._post_message(config, payload)
            provider_message_id = whatsapp_response_message_id(result)
            now = datetime.now(tz=UTC).isoformat()
            actor_label = actor_from_user(actor_user)
            visitor_pass.source_metadata = visitor_status_metadata(
                visitor_pass.source_metadata or {},
                "awaiting_visitor_reply",
                detail=(
                    f"Custom WhatsApp message sent by {actor_label}; awaiting visitor reply."
                ),
                extra={
                    "whatsapp_last_message_id": provider_message_id,
                    "whatsapp_last_message_status": "sent",
                    "whatsapp_last_message_status_at": now,
                },
            )
            entry = wm.append_visitor_pass_whatsapp_history(
                visitor_pass,
                direction="outbound",
                kind="text",
                body=message_body,
                actor_label="IACS",
                provider_message_id=provider_message_id,
                metadata={
                    "origin": "dashboard_custom",
                    "sender_user_id": str(actor_user.id),
                    "sender_label": actor_label,
                    "phone": masked_phone_number(recipient),
                },
            )
            await wm.write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="visitor_pass.whatsapp_custom_message_sent",
                actor=actor_label,
                actor_user_id=actor_user.id,
                target_entity="VisitorPass",
                target_id=visitor_pass.id,
                target_label=visitor_pass.visitor_name,
                metadata={
                    "message_id": provider_message_id,
                    "message_preview": message_body[:160],
                    "phone": masked_phone_number(recipient),
                },
            )
            visitor_pass_payload = await self._commit_visitor_update(
                session,
                visitor_pass,
                source="whatsapp_custom_message",
            )
        return {"visitor_pass": visitor_pass_payload, "message": entry}

    async def clear_visitor_abuse_mute(
        self,
        pass_id: uuid.UUID | str,
        *,
        actor_user: User,
    ) -> dict[str, Any]:
        pass_uuid = coerce_uuid(pass_id)
        if not pass_uuid:
            raise wm.VisitorPassError("Visitor Pass not found.")
        actor_label = actor_from_user(actor_user)
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_uuid)
            if not visitor_pass:
                raise wm.VisitorPassError("Visitor Pass not found.")
            if visitor_pass.pass_type != VisitorPassType.DURATION:
                raise wm.VisitorPassError("WhatsApp controls are only available for duration Visitor Passes.")
            metadata = dict(visitor_pass.source_metadata or {})
            muted_until = str(metadata.get("whatsapp_abuse_muted_until") or "").strip()
            muted_reason = str(metadata.get("whatsapp_abuse_muted_reason") or "").strip()
            if muted_until or muted_reason:
                metadata.pop("whatsapp_abuse_muted_until", None)
                metadata.pop("whatsapp_abuse_muted_reason", None)
                metadata["whatsapp_concierge_status_detail"] = f"Visitor abuse cooldown was cleared by {actor_label}."
                metadata["whatsapp_status_updated_at"] = datetime.now(tz=UTC).isoformat()
                visitor_pass.source_metadata = metadata
                wm.append_visitor_pass_whatsapp_history(
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
                await wm.write_audit_log(
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
            visitor_pass_payload = await self._commit_visitor_update(session, visitor_pass, source="whatsapp_unblock")
        return visitor_pass_payload

    async def send_visitor_pass_outreach(
        self,
        visitor_pass: VisitorPass,
        *,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> dict[str, Any] | None:
        config = config or await wm.load_whatsapp_config()
        if visitor_pass.pass_type != VisitorPassType.DURATION or not visitor_pass.visitor_phone:
            return None
        template_name = config.visitor_pass_template_name
        body_parameters = [str(visitor_pass.visitor_name or "there")]
        if template_name.strip().lower() != "iacs_visitor_welcome":
            body_parameters.append(visitor_pass_window_label(visitor_pass))
        try:
            result = await self.send_template_message(
                visitor_pass.visitor_phone,
                template_name=template_name,
                language_code=config.visitor_pass_template_language,
                body_parameters=body_parameters,
                config=config,
            )
        except Exception as exc:
            status = whatsapp_send_failure_status(exc)
            await self._update_visitor_concierge_status(
                visitor_pass.id,
                status,
                detail=str(exc)[:500],
                error=str(exc)[:500],
            )
            raise
        await self._update_visitor_concierge_status(
            visitor_pass.id,
            "welcome_message_sent",
            detail="Initial Visitor Pass WhatsApp template was sent.",
            extra={
                "whatsapp_last_message_id": whatsapp_response_message_id(result),
                "whatsapp_last_message_status": "sent",
                "whatsapp_last_message_status_at": datetime.now(tz=UTC).isoformat(),
            },
        )
        return result


    async def _handle_visitor_message(
        self,
        message: dict[str, Any],
        *,
        sender: str,
        visitor_pass: VisitorPass,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        await self._record_inbound_visitor_message(visitor_pass, message, sender=sender)
        if await self._visitor_reply_is_muted(visitor_pass.id, sender):
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
            await self.send_text_message(
                sender,
                "Please reply with your vehicle registration.",
                config=config,
            )
            return
        if re.sub(r"[^a-z0-9]+", "", text.strip().lower()) in {"begin", "start"}:
            await self._update_visitor_concierge_status(
                visitor_pass.id,
                "awaiting_visitor_reply",
                detail="Conversation started; waiting for the visitor's vehicle registration.",
            )
            await self.send_text_message(
                sender,
                visitor_concierge_start_message(visitor_pass),
                config=config,
            )
            return

        if self._visitor_message_debounce_seconds > 0:
            token = await self._buffer_visitor_text_message(visitor_pass.id, sender, text)
            if token:
                self._schedule_visitor_text_processing(visitor_pass.id, sender, token, config=config)
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
        if await self._visitor_reply_is_muted(visitor_pass.id, sender):
            return
        result = await self._visitor_concierge_result(sender, visitor_pass, text, alfred_mentioned=alfred_mentioned)
        action = str(result.get("action") or "")
        if action == "unsupported":
            await self.send_text_message(sender, VISITOR_CONCIERGE_RESTRICTED_REPLY, config=config)
            return
        if action == "timeframe_change":
            await self._handle_visitor_timeframe_change(sender, visitor_pass, text, result, config=config)
            return
        plate = normalize_registration_number(str(result.get("registration_number") or ""))
        if plate:
            nonce = uuid.uuid4().hex[:12]
            if await self._visitor_plate_is_privileged(plate):
                await self._record_privileged_visitor_plate(visitor_pass.id, sender, plate)
                await self.send_text_message(
                    sender,
                    await self._visitor_privileged_plate_reply(visitor_pass, plate, text),
                    config=config,
                )
                return
            vehicle_lookup = await self._lookup_visitor_vehicle_details(plate)
            if await self._record_visitor_plate_change_attempt(visitor_pass.id, sender, plate):
                await self._trigger_visitor_abuse_mute(
                    sender,
                    visitor_pass,
                    text,
                    reason="plate_changes",
                    config=config,
                )
                return
            if not (vehicle_lookup.found or vehicle_lookup.make or vehicle_lookup.colour):
                await self._record_unverified_visitor_plate(visitor_pass.id, sender, plate, vehicle_lookup.error)
                await self.send_text_message(
                    sender,
                    visitor_registration_not_found_message(plate),
                    config=config,
                )
                return
            await self._store_pending_visitor_plate(
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

        if await self._record_visitor_post_complete_reply(visitor_pass.id, sender):
            await self._trigger_visitor_abuse_mute(
                sender,
                visitor_pass,
                text,
                reason="post_complete_replies",
                config=config,
            )
            return

        await self.send_text_message(
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

    async def _buffer_visitor_text_message(self, pass_id: uuid.UUID, sender: str, text: str) -> str | None:
        token = uuid.uuid4().hex
        body = str(text or "").strip()
        emoji_only = visitor_message_is_emoji_only(body)
        emoji_preferred = visitor_message_contains_emoji(body)
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_id)
            if not visitor_pass or normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
                return None
            metadata = as_dict(visitor_pass.source_metadata)
            existing = as_dict(metadata.get(VISITOR_TEXT_BUFFER_KEY))
            raw_messages = as_dict_list(existing.get("messages"))
            messages = [
                item
                for item in raw_messages
                if isinstance(item, dict) and str(item.get("body") or "").strip()
            ][-7:]
            if body and not emoji_only:
                messages.append(
                    {
                        "body": body[:1500],
                        "created_at": datetime.now(tz=UTC).isoformat(),
                    }
                )
            prefers_emoji = bool(metadata.get("whatsapp_visitor_uses_emoji")) or emoji_preferred
            next_metadata = {
                **metadata,
                "whatsapp_visitor_uses_emoji": True if prefers_emoji else metadata.get("whatsapp_visitor_uses_emoji"),
            }
            if messages:
                next_metadata[VISITOR_TEXT_BUFFER_KEY] = {
                    "token": token,
                    "messages": messages[-8:],
                    "emoji_preferred": prefers_emoji,
                    "updated_at": datetime.now(tz=UTC).isoformat(),
                }
                next_metadata["whatsapp_concierge_status"] = "visitor_replied"
                next_metadata["whatsapp_concierge_status_detail"] = (
                    "Visitor replied; Alfred is waiting briefly for any follow-up message."
                )
                next_metadata["whatsapp_status_updated_at"] = datetime.now(tz=UTC).isoformat()
            else:
                next_metadata.pop(VISITOR_TEXT_BUFFER_KEY, None)
            visitor_pass.source_metadata = {key: value for key, value in next_metadata.items() if value is not None}
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
        return token if messages else None

    def _schedule_visitor_text_processing(
        self,
        pass_id: uuid.UUID,
        sender: str,
        token: str,
        *,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        key = visitor_text_task_key(pass_id, sender)
        previous = self._visitor_message_tasks.get(key)
        if previous and not previous.done():
            previous.cancel()
        task = asyncio.create_task(self._delayed_process_visitor_text(pass_id, sender, token, config=config))
        self._visitor_message_tasks[key] = task

    async def _delayed_process_visitor_text(
        self,
        pass_id: uuid.UUID,
        sender: str,
        token: str,
        *,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        key = visitor_text_task_key(pass_id, sender)
        try:
            await asyncio.sleep(max(0.0, self._visitor_message_debounce_seconds))
            await self._process_buffered_visitor_text(pass_id, sender, token, config=config)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("visitor_concierge_buffered_message_failed", extra={"error": str(exc)[:180]})
        finally:
            if self._visitor_message_tasks.get(key) is asyncio.current_task():
                self._visitor_message_tasks.pop(key, None)

    async def _process_buffered_visitor_text(
        self,
        pass_id: uuid.UUID,
        sender: str,
        token: str,
        *,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        buffered = await self._consume_visitor_text_buffer(pass_id, sender, token)
        if not buffered:
            return
        if buffered.get("expired"):
            expired_pass = buffered.get("visitor_pass")
            if isinstance(expired_pass, VisitorPass):
                await self._send_terminal_visitor_pass_reply_once(expired_pass, sender, config=config)
            return
        visitor_pass = buffered.get("visitor_pass")
        text = str(buffered.get("text") or "").strip()
        if not isinstance(visitor_pass, VisitorPass) or not text:
            return
        await self._process_visitor_text(
            sender,
            visitor_pass,
            text,
            config=config,
            emoji_preferred=bool(buffered.get("emoji_preferred")),
            alfred_mentioned=visitor_message_mentions_alfred(text),
        )

    async def _consume_visitor_text_buffer(
        self,
        pass_id: uuid.UUID,
        sender: str,
        token: str,
    ) -> dict[str, Any] | None:
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_id)
            if not visitor_pass or normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
                return None
            service = wm.get_visitor_pass_service()
            await service.refresh_statuses(session=session, publish=False)
            metadata = as_dict(visitor_pass.source_metadata)
            buffer_payload = as_dict(metadata.get(VISITOR_TEXT_BUFFER_KEY))
            if str(buffer_payload.get("token") or "") != token:
                await session.commit()
                return None
            raw_messages = as_dict_list(buffer_payload.get("messages"))
            messages = [
                str(item.get("body") or "").strip()
                for item in raw_messages
                if isinstance(item, dict) and str(item.get("body") or "").strip()
            ]
            next_metadata = dict(metadata)
            next_metadata.pop(VISITOR_TEXT_BUFFER_KEY, None)
            visitor_pass.source_metadata = next_metadata
            expired = visitor_pass.status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
            _ = (
                visitor_pass.id,
                visitor_pass.visitor_name,
                visitor_pass.visitor_phone,
                visitor_pass.pass_type,
                visitor_pass.expected_time,
                visitor_pass.valid_from,
                visitor_pass.valid_until,
                visitor_pass.number_plate,
                visitor_pass.status,
                visitor_pass.source_metadata,
            )
        if expired:
            return {"expired": True, "visitor_pass": visitor_pass}
        return {
            "visitor_pass": visitor_pass,
            "text": "\n".join(messages),
            "emoji_preferred": bool(buffer_payload.get("emoji_preferred")) or bool(
                (visitor_pass.source_metadata or {}).get("whatsapp_visitor_uses_emoji")
            ),
        }

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
        await self.send_interactive_buttons(
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

    async def _visitor_pass_for_phone(self, sender: str) -> tuple[VisitorPass | None, str]:
        async with wm.AsyncSessionLocal() as session:
            service = wm.get_visitor_pass_service()
            visitor_pass, state = await service.messaging_pass_for_phone(session, sender)
            if visitor_pass:
                # Detach scalar data from the short-lived session for the webhook worker.
                _ = visitor_pass.id, visitor_pass.visitor_name, visitor_pass.visitor_phone
            return visitor_pass, state

    async def _active_interactive_visitor_pass(
        self,
        session: AsyncSession,
        pass_id: str,
        sender: str,
        *,
        reason_prefix: str,
        config: WhatsAppIntegrationConfig,
    ) -> tuple[VisitorPass, Any] | None:
        pass_uuid = coerce_uuid(pass_id)
        if not pass_uuid:
            await self._audit_denied_sender(sender, {"id": pass_id, "type": "interactive"}, reason=f"{reason_prefix}_pass_not_found")
            return None
        visitor_pass = await session.get(VisitorPass, pass_uuid)
        if not visitor_pass or visitor_pass.pass_type != VisitorPassType.DURATION:
            await self._audit_denied_sender(sender, {"id": pass_id, "type": "interactive"}, reason=f"{reason_prefix}_pass_not_found")
            return None
        if normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
            await self._audit_denied_sender(sender, {"id": pass_id, "type": "interactive"}, reason=f"{reason_prefix}_phone_mismatch")
            return None
        service = wm.get_visitor_pass_service()
        await service.refresh_statuses(session=session, publish=False)
        if visitor_pass.status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
            await session.commit()
            await self._send_terminal_visitor_pass_reply_once(visitor_pass, sender, config=config)
            return None
        return visitor_pass, service

    async def _handle_visitor_button_reply(
        self,
        button: VisitorPassButtonReply,
        sender: str,
        *,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        async with wm.AsyncSessionLocal() as session:
            loaded = await self._active_interactive_visitor_pass(
                session,
                button.pass_id,
                sender,
                reason_prefix="visitor_button",
                config=config,
            )
            if not loaded:
                return
            visitor_pass, service = loaded

            metadata = visitor_pass.source_metadata or {}
            pending = metadata.get("whatsapp_pending_plate") if isinstance(metadata, dict) else None
            pending_nonce = str(metadata.get("whatsapp_pending_nonce") or "") if isinstance(metadata, dict) else ""
            if button.decision == "change":
                visitor_pass.source_metadata = visitor_pending_plate_metadata(
                    metadata,
                    whatsapp_awaiting_change=True,
                    whatsapp_concierge_status="awaiting_visitor_reply",
                    whatsapp_concierge_status_detail="Visitor asked to change the parsed registration.",
                )
                await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
                await self.send_text_message(sender, "No problem. Please type the new registration.", config=config)
                return
            if not pending or pending_nonce != button.nonce:
                await session.commit()
                await self.send_text_message(
                    sender,
                    "That confirmation has expired. Please type your registration again.",
                    config=config,
                )
                return
            if await self._visitor_plate_is_privileged(str(pending)):
                visitor_pass.source_metadata = visitor_pending_plate_metadata(
                    metadata,
                    whatsapp_concierge_status="awaiting_visitor_reply",
                    whatsapp_concierge_status_detail="Visitor tried to confirm a privileged registration; awaiting the visitor vehicle registration.",
                    whatsapp_last_privileged_plate=normalize_registration_number(pending),
                )
                await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
                await self.send_text_message(
                    sender,
                    await self._visitor_privileged_plate_reply(visitor_pass, str(pending), "Confirm"),
                    config=config,
                )
                return

            pending_vehicle_make = visitor_vehicle_metadata_text(metadata.get("whatsapp_pending_vehicle_make"))
            pending_vehicle_colour = visitor_vehicle_metadata_text(metadata.get("whatsapp_pending_vehicle_colour"))
            should_publish_arranged = not normalize_registration_number(visitor_pass.number_plate)
            try:
                await service.update_visitor_plate(
                    session,
                    visitor_pass,
                    new_plate=str(pending),
                    vehicle_make=pending_vehicle_make,
                    vehicle_colour=pending_vehicle_colour,
                    actor="Visitor Concierge",
                    metadata={"source": "whatsapp", "phone": masked_phone_number(sender)},
                )
            except Exception as exc:
                await session.rollback()
                await self.send_text_message(sender, f"I couldn't save that registration: {exc}", config=config)
                return
            visitor_pass.source_metadata = visitor_pending_plate_metadata(
                visitor_pass.source_metadata or {},
                whatsapp_awaiting_change=False,
                whatsapp_last_confirmed_at=datetime.now(tz=UTC).isoformat(),
                whatsapp_concierge_status="complete",
                whatsapp_concierge_status_detail="Vehicle registration confirmed by visitor.",
            )
            payload = await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
        if should_publish_arranged:
            await wm.event_bus.publish("visitor_pass.arranged", {"visitor_pass": payload, "source": "whatsapp_visitor"})
        await self.send_text_message(
            sender,
            visitor_plate_saved_message(
                payload,
                fallback_plate=pending,
                emoji_preferred=bool(as_dict(payload.get("source_metadata")).get("whatsapp_visitor_uses_emoji")),
            ),
            config=config,
        )

    async def _store_pending_visitor_plate(
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
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_id)
            if not visitor_pass:
                return
            if normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
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
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")

    async def _lookup_visitor_vehicle_details(self, plate: str) -> VisitorVehicleLookup:
        normalized_plate = normalize_registration_number(plate)
        if not normalized_plate:
            return VisitorVehicleLookup()
        try:
            vehicle = await wm.lookup_normalized_vehicle_registration(normalized_plate)
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
        pass_details = await self.get_pass_details(sender)
        runtime = await wm.get_runtime_config()
        if runtime.llm_provider == "local":
            return {
                "action": "reply",
                "message": "Sorry, I can't safely process visitor chat right now. Please contact your host.",
            }
        try:
            provider = wm.get_llm_provider(runtime.llm_provider)
            result = await wm.complete_with_provider_options(
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

    async def _visitor_plate_is_privileged(self, plate: Any) -> bool:
        return await visitor_plate_is_known_vehicle(plate)

    async def _visitor_privileged_plate_reply(self, visitor_pass: VisitorPass, plate: str, text: str) -> str:
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
                "visitor_name": visitor_first_name(visitor_pass.visitor_name),
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
        runtime = await wm.get_runtime_config()
        if runtime.llm_provider == "local":
            return fallback
        try:
            provider = wm.get_llm_provider(runtime.llm_provider)
            result = await wm.complete_with_provider_options(
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

    async def _visitor_reply_is_muted(self, pass_id: uuid.UUID | str, sender: str) -> bool:
        pass_uuid = coerce_uuid(str(pass_id))
        if not pass_uuid:
            return False
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_uuid)
            if not visitor_pass or normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
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
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
        return False

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
        await self._set_visitor_abuse_mute(visitor_pass.id, sender, reason=reason)
        await self.send_text_message(sender, message, config=config)

    async def _set_visitor_abuse_mute(self, pass_id: uuid.UUID | str, sender: str, *, reason: str) -> None:
        pass_uuid = coerce_uuid(str(pass_id))
        if not pass_uuid:
            return
        muted_until = datetime.now(tz=UTC) + timedelta(seconds=VISITOR_ABUSE_MUTE_SECONDS)
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_uuid)
            if not visitor_pass or normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
                return
            metadata = dict(visitor_pass.source_metadata or {})
            visitor_pass.source_metadata = {
                **metadata,
                "whatsapp_abuse_muted_until": muted_until.isoformat(),
                "whatsapp_abuse_muted_reason": reason,
                "whatsapp_concierge_status_detail": visitor_abuse_status_detail(reason),
                "whatsapp_status_updated_at": datetime.now(tz=UTC).isoformat(),
            }
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")

    async def _record_visitor_plate_change_attempt(self, pass_id: uuid.UUID | str, sender: str, plate: str) -> bool:
        return await self._record_visitor_reply_limit(
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

    async def _record_visitor_post_complete_reply(self, pass_id: uuid.UUID | str, sender: str) -> bool:
        return await self._record_visitor_reply_limit(
            pass_id,
            sender,
            "whatsapp_post_complete_reply_times",
            VISITOR_POST_COMPLETE_REPLY_LIMIT,
            extra=lambda visitor_pass: {} if visitor_pass_conversation_is_complete(visitor_pass) else None,
        )

    async def _record_visitor_reply_limit(
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
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_uuid)
            if not visitor_pass or normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
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
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
        return len(timestamps) >= limit

    async def _record_unverified_visitor_plate(
        self,
        pass_id: uuid.UUID | str,
        sender: str,
        plate: str,
        error: str | None,
    ) -> None:
        await self._record_visitor_plate_rejection(
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

    async def _record_privileged_visitor_plate(
        self,
        pass_id: uuid.UUID | str,
        sender: str,
        plate: str,
    ) -> None:
        await self._record_visitor_plate_rejection(
            pass_id,
            sender,
            {
                "whatsapp_last_privileged_plate": normalize_registration_number(plate),
                "whatsapp_concierge_status_detail": (
                    "Visitor sent a privileged registration that cannot be used; awaiting the visitor vehicle registration."
                ),
            },
        )

    async def _record_visitor_plate_rejection(
        self,
        pass_id: uuid.UUID | str,
        sender: str,
        metadata_update: dict[str, Any],
    ) -> None:
        pass_uuid = coerce_uuid(str(pass_id))
        if not pass_uuid:
            return
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_uuid)
            if not visitor_pass or normalize_whatsapp_phone_number(visitor_pass.visitor_phone) != sender:
                return
            metadata = dict(visitor_pass.source_metadata or {})
            visitor_pass.source_metadata = visitor_status_metadata(metadata, "awaiting_visitor_reply", extra=metadata_update)
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")

    async def _send_terminal_visitor_pass_reply_once(
        self,
        visitor_pass: VisitorPass,
        sender: str,
        *,
        config: WhatsAppIntegrationConfig,
    ) -> bool:
        async with wm.AsyncSessionLocal() as session:
            stored = await session.get(VisitorPass, visitor_pass.id)
            if not stored or normalize_whatsapp_phone_number(stored.visitor_phone) != sender:
                return False
            metadata = dict(stored.source_metadata or {})
            if metadata.get("whatsapp_terminal_notice_sent_at"):
                await session.commit()
                return False
            now = datetime.now(tz=UTC).isoformat()
            message = visitor_pass_terminal_message(stored.status)
            stored.source_metadata = {
                **metadata,
                "whatsapp_terminal_notice_sent_at": now,
                "whatsapp_terminal_notice_status": str(stored.status.value if hasattr(stored.status, "value") else stored.status),
                "whatsapp_concierge_status_detail": "Visitor was told their pass is no longer valid.",
                "whatsapp_status_updated_at": now,
            }
            await self._commit_visitor_update(session, stored, source="whatsapp_visitor")
        await self.send_text_message(sender, message, config=config)
        return True

    async def _handle_visitor_timeframe_change(
        self,
        sender: str,
        visitor_pass: VisitorPass,
        text: str,
        result: dict[str, Any],
        *,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        requested_from = parse_datetime_value(result.get("valid_from"))
        requested_until = parse_datetime_value(result.get("valid_until"))
        async with wm.AsyncSessionLocal() as session:
            stored = await session.get(VisitorPass, visitor_pass.id)
            if not stored or normalize_whatsapp_phone_number(stored.visitor_phone) != sender:
                await self._audit_denied_sender(sender, {"id": str(visitor_pass.id), "type": "timeframe"}, reason="visitor_timeframe_phone_mismatch")
                return
            if stored.pass_type != VisitorPassType.DURATION:
                await session.commit()
                await self.send_text_message(sender, VISITOR_CONCIERGE_RESTRICTED_REPLY, config=config)
                return
            service = wm.get_visitor_pass_service()
            await service.refresh_statuses(session=session, publish=False)
            if stored.status not in {VisitorPassStatus.ACTIVE, VisitorPassStatus.SCHEDULED}:
                await session.commit()
                await self._send_terminal_visitor_pass_reply_once(stored, sender, config=config)
                return
            metadata = dict(stored.source_metadata or {})
            if visitor_pending_timeframe_request(metadata):
                await session.commit()
                await self.send_text_message(
                    sender,
                    await self._visitor_pending_timeframe_reply(stored, text),
                    config=config,
                )
                return
            current_start = service.window_start(stored)
            current_end = service.window_end(stored)
            requested_from = requested_from or current_start
            requested_until = requested_until or current_end
            if requested_until <= requested_from:
                await session.commit()
                await self.send_text_message(
                    sender,
                    "Please send a valid start and end time for your visitor pass.",
                    config=config,
                )
                return
            original_start, original_end = visitor_timeframe_original_window(metadata, current_start, current_end)
            original_window_payload = visitor_timeframe_window_payload(original_start, original_end)
            if truthy_value(result.get("direct_apply")):
                changed_at = datetime.now(tz=UTC).isoformat()
                last_custom = visitor_pass_whatsapp_llm_context(stored).get("latest_dashboard_custom_message")
                next_metadata = {
                    **metadata,
                    "whatsapp_timeframe_original_window": original_window_payload,
                    "whatsapp_concierge_status": "timeframe_approved",
                    "whatsapp_concierge_status_detail": (
                        "Visitor confirmed a dashboard custom message timeframe change."
                    ),
                    "whatsapp_timeframe_last_change": {
                        "status": "dashboard_custom_confirmed",
                        "confirmed_at": changed_at,
                        "visitor_message": text[:500],
                        "operator_message": (
                            str(last_custom.get("body") or "")[:500]
                            if isinstance(last_custom, dict)
                            else ""
                        ),
                        "valid_from": requested_from.isoformat(),
                        "valid_until": requested_until.isoformat(),
                    },
                    "whatsapp_status_updated_at": changed_at,
                }
                await service.update_pass(
                    session,
                    stored,
                    valid_from=requested_from,
                    valid_until=requested_until,
                    source_metadata=next_metadata,
                    actor="Visitor Concierge",
                )
                await wm.write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_INTEGRATIONS,
                    action="visitor_pass.dashboard_custom_timeframe_applied",
                    actor="Visitor Concierge",
                    target_entity="VisitorPass",
                    target_id=stored.id,
                    target_label=stored.visitor_name,
                    metadata={
                        "visitor_message": text[:500],
                        "operator_message": (
                            str(last_custom.get("body") or "")[:500]
                            if isinstance(last_custom, dict)
                            else ""
                        ),
                        "requested_valid_from": requested_from.isoformat(),
                        "requested_valid_until": requested_until.isoformat(),
                        "phone": masked_phone_number(sender),
                    },
                )
                payload = await self._commit_visitor_update(session, stored, source="whatsapp_visitor")
                await self.send_text_message(
                    sender,
                    f"I've updated your Visitor Pass. It is now valid for {visitor_pass_window_label_from_payload(payload)}.",
                    config=config,
                )
                return
            if timeframe_change_within_auto_limit(original_start, original_end, requested_from, requested_until):
                request_id = uuid.uuid4().hex[:12]
                confirmation_payload = visitor_timeframe_request_payload(
                    request_id,
                    text,
                    result.get("summary") or "Visitor requested an allowed timeframe change.",
                    (current_start, current_end),
                    (original_start, original_end),
                    (requested_from, requested_until),
                )
                stored.source_metadata = {
                    **metadata,
                    "whatsapp_timeframe_original_window": original_window_payload,
                    "whatsapp_timeframe_confirmation": confirmation_payload,
                    "whatsapp_concierge_status": "timeframe_confirmation_pending",
                    "whatsapp_concierge_status_detail": "Awaiting visitor confirmation for the requested timeframe change.",
                    "whatsapp_status_updated_at": datetime.now(tz=UTC).isoformat(),
                }
                await self._commit_visitor_update(session, stored, source="whatsapp_visitor")
                await self.send_visitor_timeframe_confirmation(
                    sender,
                    stored,
                    requested_from,
                    requested_until,
                    request_id,
                    config=config,
                )
                return

            request_id = uuid.uuid4().hex[:12]
            request_payload = visitor_timeframe_request_payload(
                request_id,
                text,
                result.get("summary"),
                (current_start, current_end),
                (original_start, original_end),
                (requested_from, requested_until),
            )
            stored.source_metadata = {
                **metadata,
                "whatsapp_timeframe_original_window": original_window_payload,
                "whatsapp_timeframe_request": request_payload,
                "whatsapp_concierge_status": "timeframe_approval_pending",
                "whatsapp_concierge_status_detail": "Visitor requested a timeframe change that needs Admin approval.",
                "whatsapp_status_updated_at": datetime.now(tz=UTC).isoformat(),
            }
            await wm.write_audit_log(
                session,
                category=TELEMETRY_CATEGORY_INTEGRATIONS,
                action="visitor_pass.timeframe_change_requested",
                actor="Visitor Concierge",
                target_entity="VisitorPass",
                target_id=stored.id,
                target_label=stored.visitor_name,
                metadata={"request": request_payload, "phone": masked_phone_number(sender)},
            )
            payload = await self._commit_visitor_update(session, stored, source="whatsapp_visitor")
        await self._notify_timeframe_change_request(payload, request_payload)
        await self.send_text_message(sender, VISITOR_TIMEFRAME_APPROVAL_REPLY, config=config)

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
        await self.send_interactive_buttons(
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
        self,
        reply: VisitorPassTimeframeReply,
        sender: str,
        *,
        config: WhatsAppIntegrationConfig,
    ) -> None:
        async with wm.AsyncSessionLocal() as session:
            loaded = await self._active_interactive_visitor_pass(
                session,
                reply.pass_id,
                sender,
                reason_prefix="visitor_timeframe",
                config=config,
            )
            if not loaded:
                return
            visitor_pass, service = loaded
            metadata = dict(visitor_pass.source_metadata or {})
            pending = metadata.get("whatsapp_timeframe_confirmation")
            if not isinstance(pending, dict) or str(pending.get("id") or "") != reply.request_id:
                await session.commit()
                await self.send_text_message(
                    sender,
                    "That timeframe confirmation has expired. Please type the new time again.",
                    config=config,
                )
                return
            if str(pending.get("status") or "") != "pending":
                await session.commit()
                await self.send_text_message(
                    sender,
                    "That timeframe confirmation has already been handled. Please type a new time if you need another change.",
                    config=config,
                )
                return
            if reply.decision == "change":
                decided_at = datetime.now(tz=UTC).isoformat()
                pending = {**pending, "status": "visitor_requested_change", "decided_at": decided_at}
                visitor_pass.source_metadata = {
                    **metadata,
                    "whatsapp_timeframe_confirmation": pending,
                    "whatsapp_concierge_status": "awaiting_visitor_reply",
                    "whatsapp_concierge_status_detail": "Visitor asked to change the requested timeframe.",
                    "whatsapp_status_updated_at": decided_at,
                }
                await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
                await self.send_text_message(
                    sender,
                    "No problem. Please type the new arrival or departure time you need.",
                    config=config,
                )
                return
            requested_from = parse_datetime_value(pending.get("requested_valid_from"))
            requested_until = parse_datetime_value(pending.get("requested_valid_until"))
            if not requested_from or not requested_until or requested_until <= requested_from:
                await session.commit()
                await self.send_text_message(sender, "That timeframe confirmation is invalid. Please type the new time again.", config=config)
                return
            decided_at = datetime.now(tz=UTC).isoformat()
            pending = {**pending, "status": "confirmed", "decided_at": decided_at}
            next_metadata = {
                **metadata,
                "whatsapp_timeframe_confirmation": pending,
                "whatsapp_concierge_status": "awaiting_visitor_reply",
                "whatsapp_concierge_status_detail": "Visitor confirmed the requested timeframe change.",
                "whatsapp_timeframe_last_change": {
                    "status": "visitor_confirmed",
                    "confirmed_at": decided_at,
                    "valid_from": requested_from.isoformat(),
                    "valid_until": requested_until.isoformat(),
                },
                "whatsapp_status_updated_at": decided_at,
            }
            await service.update_pass(
                session,
                visitor_pass,
                valid_from=requested_from,
                valid_until=requested_until,
                source_metadata=next_metadata,
                actor="Visitor Concierge",
            )
            payload = await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")
        await self.send_text_message(
            sender,
            f"I've updated your allowed timeframe. Your pass is now valid for {visitor_pass_window_label_from_payload(payload)}.",
            config=config,
        )

    async def _notify_timeframe_change_request(
        self,
        visitor_pass_payload: dict[str, Any],
        request_payload: dict[str, Any],
    ) -> None:
        from app.services.notifications import get_notification_service

        visitor_name = str(visitor_pass_payload.get("visitor_name") or "visitor")
        subject = f"Visitor Pass timeframe change requested for {visitor_name}"
        current_window = visitor_window_label_from_values(
            request_payload.get("current_valid_from"),
            request_payload.get("current_valid_until"),
        )
        original_window = visitor_window_label_from_values(
            request_payload.get("original_valid_from") or request_payload.get("current_valid_from"),
            request_payload.get("original_valid_until") or request_payload.get("current_valid_until"),
        )
        requested_window = visitor_window_label_from_values(
            request_payload.get("requested_valid_from"),
            request_payload.get("requested_valid_until"),
        )
        await get_notification_service().notify(
            NotificationContext(
                event_type="visitor_pass_timeframe_change_requested",
                subject=subject,
                severity="warning",
                facts={
                    "message": f"{visitor_name} requested changing their Visitor Pass from {current_window} to {requested_window}.",
                    "subject": subject,
                    "visitor_name": visitor_name,
                    "visitor_pass_name": visitor_name,
                    "display_name": visitor_name,
                    "visitor_pass_id": str(visitor_pass_payload.get("id") or ""),
                    "visitor_pass_status": str(visitor_pass_payload.get("status") or ""),
                    "visitor_pass_current_window": current_window,
                    "visitor_pass_original_time": original_window,
                    "visitor_pass_requested_window": requested_window,
                    "visitor_pass_requested_time": requested_window,
                    "visitor_pass_timeframe_request_id": str(request_payload.get("id") or ""),
                    "visitor_pass_current_valid_from": str(request_payload.get("current_valid_from") or ""),
                    "visitor_pass_current_valid_until": str(request_payload.get("current_valid_until") or ""),
                    "visitor_pass_requested_valid_from": str(request_payload.get("requested_valid_from") or ""),
                    "visitor_pass_requested_valid_until": str(request_payload.get("requested_valid_until") or ""),
                    "visitor_pass_visitor_message": str(request_payload.get("visitor_message") or ""),
                    "source": "whatsapp_visitor",
                },
            )
        )

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
            await self.send_text_message(sender, f"I couldn't process that Visitor Pass decision: {exc}", config=config)
            return
        admin_message = str(result.get("admin_message") or "Visitor Pass timeframe request updated.")
        await self.send_text_message(sender, admin_message, config=config)

    async def decide_visitor_timeframe_request(
        self,
        pass_id: str,
        request_id: str,
        decision: str,
        *,
        actor_user: User | None = None,
        actor_label: str | None = None,
        admin_phone: str | None = None,
        config: WhatsAppIntegrationConfig | None = None,
    ) -> dict[str, Any]:
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"allow", "deny"}:
            raise wm.VisitorPassError("Decision must be allow or deny.")
        pass_uuid = coerce_uuid(pass_id)
        if not pass_uuid:
            raise wm.VisitorPassError("Visitor Pass not found.")
        async with wm.AsyncSessionLocal() as session:
            stored = await session.get(VisitorPass, pass_uuid)
            if not stored or stored.pass_type != VisitorPassType.DURATION:
                raise wm.VisitorPassError("Visitor Pass not found.")
            metadata = dict(stored.source_metadata or {})
            pending = metadata.get("whatsapp_timeframe_request") if isinstance(metadata, dict) else None
            if not isinstance(pending, dict) or str(pending.get("id") or "") != str(request_id):
                raise wm.VisitorPassError("No matching pending timeframe request was found.")
            if str(pending.get("status") or "") != "pending":
                raise wm.VisitorPassError("This timeframe request has already been decided.")
            requested_from = parse_datetime_value(pending.get("requested_valid_from"))
            requested_until = parse_datetime_value(pending.get("requested_valid_until"))
            if not requested_from or not requested_until or requested_until <= requested_from:
                raise wm.VisitorPassError("The pending timeframe request is invalid.")
            decided_at = datetime.now(tz=UTC).isoformat()
            actor = actor_from_user(actor_user) if actor_user else (actor_label or "WhatsApp Admin")
            pending = {
                **pending,
                "status": "approved" if normalized_decision == "allow" else "denied",
                "decided_at": decided_at,
                "decided_by_user_id": str(actor_user.id) if actor_user else None,
                "decided_by_phone": masked_phone_number(admin_phone),
            }
            if normalized_decision == "allow":
                next_metadata = {
                    **metadata,
                    "whatsapp_timeframe_request": pending,
                    "whatsapp_concierge_status": "timeframe_approved",
                    "whatsapp_concierge_status_detail": "Admin approved the visitor's requested timeframe change.",
                    "whatsapp_status_updated_at": decided_at,
                }
                await wm.get_visitor_pass_service().update_pass(
                    session,
                    stored,
                    valid_from=requested_from,
                    valid_until=requested_until,
                    source_metadata=next_metadata,
                    actor=actor,
                    actor_user_id=actor_user.id if actor_user else None,
                )
                await wm.write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_INTEGRATIONS,
                    action="visitor_pass.timeframe_change_approved",
                    actor=actor,
                    actor_user_id=actor_user.id if actor_user else None,
                    target_entity="VisitorPass",
                    target_id=stored.id,
                    target_label=stored.visitor_name,
                    metadata={"request": pending},
                )
            else:
                stored.source_metadata = {
                    **metadata,
                    "whatsapp_timeframe_request": pending,
                    "whatsapp_concierge_status": "timeframe_denied",
                    "whatsapp_concierge_status_detail": "Admin denied the visitor's requested timeframe change.",
                    "whatsapp_status_updated_at": decided_at,
                }
                await wm.write_audit_log(
                    session,
                    category=TELEMETRY_CATEGORY_INTEGRATIONS,
                    action="visitor_pass.timeframe_change_denied",
                    actor=actor,
                    actor_user_id=actor_user.id if actor_user else None,
                    target_entity="VisitorPass",
                    target_id=stored.id,
                    target_label=stored.visitor_name,
                    metadata={"request": pending},
                )
            payload = await self._commit_visitor_update(session, stored, source="whatsapp_admin")
        visitor_phone = str(payload.get("visitor_phone") or "")
        if visitor_phone:
            visitor_message = (
                f"Your requested timeframe change has been approved. Your pass is now valid for {visitor_pass_window_label_from_payload(payload)}."
                if normalized_decision == "allow"
                else f"Sorry, your requested timeframe change was not approved. Your existing allowed timeframe remains {visitor_pass_window_label_from_payload(payload)}."
            )
            await self.send_text_message(visitor_phone, visitor_message, config=config)
        return {
            "ok": True,
            "decision": normalized_decision,
            "visitor_pass": payload,
            "admin_message": (
                f"Approved timeframe change for {payload.get('visitor_name') or 'visitor'}."
                if normalized_decision == "allow"
                else f"Denied timeframe change for {payload.get('visitor_name') or 'visitor'}."
            ),
        }

    async def _commit_visitor_update(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        source: str,
    ) -> dict[str, Any]:
        await session.commit()
        await session.refresh(visitor_pass)
        payload = wm.serialize_visitor_pass(visitor_pass)
        await wm.event_bus.publish("visitor_pass.updated", {"visitor_pass": payload, "source": source})
        return payload

    async def _update_visitor_concierge_status(
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
        async with wm.AsyncSessionLocal() as session:
            visitor_pass = await session.get(VisitorPass, pass_uuid)
            if not visitor_pass:
                return
            visitor_pass.source_metadata = visitor_status_metadata(
                visitor_pass.source_metadata or {},
                status,
                detail=detail,
                error=error,
                extra=extra,
            )
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_visitor")

    async def _update_visitor_concierge_status_for_phone(
        self,
        phone_number: str,
        status: str,
        *,
        detail: str | None = None,
        error: str | None = None,
    ) -> None:
        phone = normalize_whatsapp_phone_number(phone_number)
        if not phone:
            return
        async with wm.AsyncSessionLocal() as session:
            visitor_pass, state = await wm.get_visitor_pass_service().messaging_pass_for_phone(session, phone)
            if not visitor_pass or state not in {"active", "scheduled"}:
                await session.commit()
                return
            visitor_pass.source_metadata = visitor_status_metadata(
                visitor_pass.source_metadata or {},
                status,
                detail=detail,
                error=error,
            )
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_status")

    async def _update_visitor_delivery_status_for_phone(
        self,
        phone_number: str,
        status: str,
        *,
        message_id: str | None = None,
    ) -> None:
        phone = normalize_whatsapp_phone_number(phone_number)
        if not phone or status not in {"message_received", "message_read"}:
            return
        async with wm.AsyncSessionLocal() as session:
            visitor_pass, state = await wm.get_visitor_pass_service().messaging_pass_for_phone(session, phone)
            if not visitor_pass or state not in {"active", "scheduled"}:
                await session.commit()
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
            await self._commit_visitor_update(session, visitor_pass, source="whatsapp_status")

    async def _record_inbound_visitor_message(
        self,
        visitor_pass: VisitorPass,
        message: dict[str, Any],
        *,
        sender: str,
    ) -> None:
        body = extract_message_text(message) or str(message.get("type") or "WhatsApp message").replace("_", " ").title()
        if not body:
            return
        async with wm.AsyncSessionLocal() as session:
            stored = await session.get(VisitorPass, visitor_pass.id)
            if not stored or normalize_whatsapp_phone_number(stored.visitor_phone) != sender:
                return
            wm.append_visitor_pass_whatsapp_history(
                stored,
                direction="inbound",
                kind=str(message.get("type") or "message"),
                body=body,
                actor_label=stored.visitor_name or "Visitor",
                provider_message_id=str(message.get("id") or ""),
                occurred_at=parse_whatsapp_timestamp(message.get("timestamp")),
                metadata={"phone": masked_phone_number(sender)},
            )
            await self._commit_visitor_update(session, stored, source="whatsapp_message")

    async def _record_outbound_visitor_message(
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
            async with wm.AsyncSessionLocal() as session:
                visitor_pass, _state = await wm.get_visitor_pass_service().messaging_pass_for_phone(session, recipient)
                if not visitor_pass:
                    return
                wm.append_visitor_pass_whatsapp_history(
                    visitor_pass,
                    direction="outbound",
                    kind=kind,
                    body=body,
                    actor_label="IACS",
                    provider_message_id=provider_message_id,
                    metadata=metadata,
                )
                await self._commit_visitor_update(session, visitor_pass, source="whatsapp_message")
        except Exception as exc:
            logger.debug("visitor_pass_whatsapp_history_record_failed", extra={"error": str(exc)[:180]})
