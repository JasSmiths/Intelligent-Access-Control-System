"""Authenticated automation webhook intake and immutable occurrence reservation.

The sender sequence, nonce and workflow occurrences commit together. Delivery
and response presentation happen after this owner returns its accepted receipt.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from typing import Any, Protocol

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutomationRule, AutomationWebhookNonce, AutomationWebhookSender
from app.services.automation_actions import action_paused_by_maintenance_mode
from app.services.automation_errors import AutomationError
from app.services.automation_integration_actions import integration_action_for_type
from app.services.telemetry import current_trace_id, payload_shape
from app.services.type_helpers import as_dict
from app.services.workflow_session import SessionFactory
from app.services.workflows.automation_definition import (
    WEBHOOK_HMAC_WINDOW_SECONDS,
    WEBHOOK_RATE_LIMIT_PER_MINUTE,
    bool_config,
    generate_automation_webhook_key,
    is_high_entropy_webhook_key,
    normalize_actions,
    normalize_triggers,
    optional_text,
    safe_int,
)
from app.services.workflows.context import normalize_string_list

WEBHOOK_RATE_WINDOW_SECONDS = 60


class TriggerReservation(Protocol):
    async def __call__(
        self, session: AsyncSession, trigger_key: str, payload: dict[str, Any], **kwargs: Any
    ) -> list[uuid.UUID]: ...


@dataclass(frozen=True)
class AcceptedWebhook:
    webhook_key: str
    new_sender: bool
    hmac_verified: bool
    run_ids: list[uuid.UUID]


class AutomationWebhookIntake:
    def __init__(self, *, sessions: SessionFactory, reserve_trigger: TriggerReservation) -> None:
        self.sessions, self.reserve_trigger = sessions, reserve_trigger

    async def reserve(
        self,
        webhook_key: str,
        payload: dict[str, Any],
        *,
        source_ip: str,
        raw_body: bytes = b"",
        signature: str | None = None,
        signature_timestamp: str | None = None,
        nonce: str | None = None,
    ) -> AcceptedWebhook:
        now = datetime.now(tz=UTC)
        payload_shape_value = payload_shape(payload)
        hmac_verified = False
        async with self.sessions() as session:
            policies = await webhook_policies_for_key(session, webhook_key)
            now = await session.scalar(select(func.clock_timestamp()))
            replay_window_seconds = WEBHOOK_HMAC_WINDOW_SECONDS
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
                    replay_window_seconds = min(
                        int(policy.get("replay_window_seconds") or WEBHOOK_HMAC_WINDOW_SECONDS)
                        for policy in policies
                    )
                    if not verify_webhook_hmac(
                        webhook_key,
                        raw_body,
                        signature=signature,
                        timestamp=signature_timestamp,
                        nonce=nonce,
                        window_seconds=replay_window_seconds,
                        now=now,
                    ):
                        await record_rejected_webhook_sender(
                            session, webhook_key, source_ip, now=now
                        )
                        raise AutomationError("Automation webhook signature is invalid or expired.")
                    try:
                        await remember_webhook_nonce(
                            session,
                            webhook_key,
                            source_ip,
                            nonce=nonce,
                            signature_timestamp=signature_timestamp,
                            window_seconds=replay_window_seconds,
                        )
                    except AutomationError:
                        await record_rejected_webhook_sender(
                            session, webhook_key, source_ip, now=now
                        )
                        raise
                    hmac_verified = True

            inserted = await session.scalar(
                pg_insert(AutomationWebhookSender)
                .values(
                    id=uuid.uuid4(),
                    webhook_key=webhook_key,
                    source_ip=source_ip,
                    first_seen_at=now,
                    last_seen_at=now,
                    event_count=0,
                    last_payload_shape=payload_shape_value,
                )
                .on_conflict_do_nothing(index_elements=["webhook_key", "source_ip"])
                .returning(AutomationWebhookSender.id)
            )
            sender = await session.scalar(
                select(AutomationWebhookSender)
                .where(
                    AutomationWebhookSender.webhook_key == webhook_key,
                    AutomationWebhookSender.source_ip == source_ip,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if sender is None:
                raise AutomationError("Webhook sender could not be reserved.")
            new_sender = inserted is not None
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
                sender.key_strength = (
                    "server_generated" if is_high_entropy_webhook_key(webhook_key) else "legacy"
                )
                sender.hmac_required = any(policy.get("require_hmac") for policy in policies)
                sender.allowed_source_ips = sorted(
                    {
                        value
                        for policy in policies
                        for value in normalize_string_list(policy.get("allowed_source_ips"))
                    }
                )
                if hmac_verified:
                    sender.last_nonce = nonce
                    sender.last_signature_at = now
            base_payload = {
                "webhook_key": webhook_key,
                "source_ip": source_ip,
                "payload": payload,
                "payload_shape": payload_shape_value,
                "occurred_at": now.isoformat(),
                "hmac_verified": hmac_verified,
            }
            # The committed sender sequence identifies this accepted request.
            # Nonce, sequence and occurrences roll back as one unit on failure.
            origin_id = f"{sender.id}:{sender.event_count}"
            identities = await self.reserve_trigger(
                session,
                "webhook.received",
                base_payload,
                origin_kind="webhook",
                origin_id=origin_id,
                actor="Webhook",
                source="webhook",
                trace_id=current_trace_id(),
                eligible_rule_ids={uuid.UUID(policy["rule_id"]) for policy in policies},
            )
            if not identities:
                identities.extend(
                    await self.reserve_trigger(
                        session,
                        "webhook.unrecognized",
                        {**base_payload, "reason": "no_matching_automation"},
                        origin_kind="webhook",
                        origin_id=origin_id,
                        actor="Webhook",
                        source="webhook",
                        trace_id=current_trace_id(),
                    )
                )
            if new_sender:
                identities.extend(
                    await self.reserve_trigger(
                        session,
                        "webhook.new_sender",
                        base_payload,
                        origin_kind="webhook",
                        origin_id=origin_id,
                        actor="Webhook",
                        source="webhook",
                        trace_id=current_trace_id(),
                    )
                )
            await session.commit()
        return AcceptedWebhook(webhook_key, new_sender, hmac_verified, identities)


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
    return action_paused_by_maintenance_mode(action_type) or bool(
        integration_action_for_type(action_type)
    )


async def webhook_policies_for_key(session: AsyncSession, webhook_key: str) -> list[dict[str, Any]]:
    rules = (
        await session.scalars(
            select(AutomationRule)
            .where(AutomationRule.is_active.is_(True))
            .where(
                or_(
                    *(
                        AutomationRule.trigger_keys.contains([key])
                        for key in (
                            "webhook.received",
                            "webhook.new_sender",
                            "webhook.unrecognized",
                        )
                    )
                )
            )
            .order_by(AutomationRule.created_at, AutomationRule.id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
                any(
                    action_requires_webhook_hardening(action)
                    for action in normalize_actions(rule.actions)
                )
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


async def remember_webhook_nonce(
    session: AsyncSession,
    webhook_key: str,
    source_ip: str,
    *,
    nonce: str | None,
    signature_timestamp: str | None,
    window_seconds: int,
) -> None:
    nonce_text = optional_text(nonce)
    signed_at = parse_webhook_timestamp(optional_text(signature_timestamp))
    if not nonce_text or not signed_at:
        raise AutomationError("Automation webhook signature is invalid or expired.")
    expires_at = signed_at + timedelta(seconds=window_seconds)
    await session.execute(
        delete(AutomationWebhookNonce).where(
            AutomationWebhookNonce.expires_at <= datetime.now(tz=UTC)
        )
    )
    existing = await session.scalar(
        select(AutomationWebhookNonce.id)
        .where(AutomationWebhookNonce.webhook_key == webhook_key)
        .where(AutomationWebhookNonce.nonce_hash == webhook_nonce_hash(nonce_text))
    )
    if existing:
        raise AutomationError("Automation webhook nonce was already used.")
    session.add(
        AutomationWebhookNonce(
            webhook_key=webhook_key,
            source_ip=source_ip,
            nonce_hash=webhook_nonce_hash(nonce_text),
            signed_at=signed_at,
            expires_at=expires_at,
        )
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AutomationError("Automation webhook nonce was already used.") from exc


def webhook_nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


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
        parsed = datetime.fromisoformat(text)
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
