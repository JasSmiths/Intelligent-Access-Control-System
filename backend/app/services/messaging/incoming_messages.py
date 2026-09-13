"""Durable received-message and reply checkpoints; no routing or provider I/O.

Received work may be claimed after restart. Processing work is never replayed:
its handler may already have committed a domain action or sent a reply. Nullable
recovery fields identify historical deduplication rows and never activate them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.session import AsyncSessionLocal
from app.models import ProcessedMessagingMessage

PROCESSING_LEASE_SECONDS = 300
MAX_BATCH_MESSAGES = 8
MAX_CANDIDATES = 64
MAX_REPLIES = 32
MAX_PAYLOAD_BYTES = 32768
REPLY_DELIVERIES = frozenset({"accepted", "rejected", "not_sent", "unknown"})


class IncomingMessageClaimLost(RuntimeError):
    """The handler must stop before any new domain operation or transmission."""


@dataclass(frozen=True)
class IncomingMessageClaim:
    batch_id: uuid.UUID
    token: uuid.UUID
    message_ids: tuple[uuid.UUID, ...]
    provider: str
    channel_id: str | None
    author_id: str | None


def _bounded_json(value: Any, *, label: str) -> Any:
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"{label} exceeds its durable size limit.")
    return json.loads(encoded)


def _peer_key(provider: str, channel: str | None, author: str | None) -> str:
    encoded = json.dumps([provider, channel, author], separators=(",", ":"))
    return "iacs:incoming-message:" + hashlib.sha256(encoded.encode()).hexdigest()


def _visitor_text(row: ProcessedMessagingMessage) -> bool:
    return (isinstance(row.routing_context, dict) and row.routing_context.get("kind") == "visitor"
        and isinstance(row.envelope, dict) and isinstance(row.envelope.get("message"), dict)
        and row.envelope.get("batchable", True) is True
        and row.envelope["message"].get("type") == "text")


class IncomingMessageStore:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.sessions = session_factory

    @staticmethod
    async def _now(session: AsyncSession) -> datetime:
        return await session.scalar(select(func.clock_timestamp()))

    @staticmethod
    async def _lock_peer(session: AsyncSession, provider, channel, author, *, wait=True) -> bool:
        function = "pg_advisory_xact_lock" if wait else "pg_try_advisory_xact_lock"
        result = await session.scalar(text(f"SELECT {function}(hashtext(:peer))"),
            {"peer": _peer_key(provider, channel, author)})
        return True if wait else bool(result)

    @staticmethod
    def _peer_filter(provider, channel, author):
        return and_(ProcessedMessagingMessage.provider == provider,
            ProcessedMessagingMessage.provider_channel_id == channel,
            ProcessedMessagingMessage.author_provider_id == author)

    async def accept_in_session(self, session: AsyncSession, *, provider: str, provider_message_id: str,
        provider_channel_id: str | None, author_provider_id: str | None,
        envelope: dict[str, Any], routing_context: dict[str, Any], received_at: datetime | None,
        debounce_seconds: float = 0) -> uuid.UUID:
        """Transaction participant: retain server-bound input before acknowledging.

        The adapter supplies normalized bounded content and server-resolved actor
        binding. No sender-supplied role is converted to execution authority here.
        Duplicate historical rows remain historical; duplicate new IDs retain
        their first binding and body. This method never commits or wakes a worker.
        Accept each peer in its own transaction; crossed webhook batches must
        never acquire sender locks in opposite orders.
        """
        if not provider.strip() or len(provider) > 40 or not provider_message_id.strip() or len(provider_message_id) > 180:
            raise ValueError("A bounded provider message identity is required.")
        if not author_provider_id or len(author_provider_id) > 180 or (provider_channel_id and len(provider_channel_id) > 180):
            raise ValueError("A bounded sender and channel are required.")
        if not 0 <= debounce_seconds <= 5:
            raise ValueError("Message debounce must be between zero and five seconds.")
        payload = _bounded_json(envelope, label="Incoming message")
        binding = _bounded_json(routing_context, label="Incoming actor binding")
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            raise ValueError("A normalized incoming message is required.")
        if not isinstance(binding, dict) or binding.get("kind") not in {"admin", "standard", "visitor", "denied"}:
            raise ValueError("An explicit server-resolved routing decision is required.")
        if received_at is not None and received_at.tzinfo is None:
            raise ValueError("Incoming message timestamps must include a timezone.")
        peer = _peer_key(provider, provider_channel_id, author_provider_id)
        previous = session.info.get("incoming_accept_scope")
        transaction = session.sync_session.get_transaction()
        if previous is not None and previous[0] is transaction and previous[1] != peer:
            raise ValueError("Accept different messaging peers in separate transactions.")
        await self._lock_peer(session, provider, provider_channel_id, author_provider_id)
        session.info["incoming_accept_scope"] = (session.sync_session.get_transaction(), peer)
        now, identity = await self._now(session), uuid.uuid4()
        available = now + timedelta(seconds=debounce_seconds)
        inserted = await session.scalar(insert(ProcessedMessagingMessage).values(id=identity, provider=provider,
            provider_message_id=provider_message_id, provider_channel_id=provider_channel_id,
            author_provider_id=author_provider_id, received_at=received_at,
            recovery_version=1, state="received", envelope=payload, routing_context=binding,
            available_at=available, reply_plan=[])
            .on_conflict_do_nothing(index_elements=[ProcessedMessagingMessage.provider, ProcessedMessagingMessage.provider_message_id])
            .returning(ProcessedMessagingMessage.id))
        if inserted is None:
            retained = await session.scalar(select(ProcessedMessagingMessage).where(
                ProcessedMessagingMessage.provider == provider,
                ProcessedMessagingMessage.provider_message_id == provider_message_id))
            if retained is None:
                raise RuntimeError("Incoming message identity could not be retained.")
            if (retained.provider_channel_id != provider_channel_id or retained.author_provider_id != author_provider_id):
                raise ValueError("A provider message identity cannot be rebound to another channel or sender.")
            return retained.id
        if debounce_seconds and binding["kind"] == "visitor" and payload["message"].get("type") == "text":
            rows = (await session.scalars(select(ProcessedMessagingMessage).where(
                self._peer_filter(provider, provider_channel_id, author_provider_id),
                ProcessedMessagingMessage.recovery_version == 1, ProcessedMessagingMessage.state == "received",
                ProcessedMessagingMessage.routing_context == binding).with_for_update())).all()
            for row in rows:
                if _visitor_text(row):
                    row.available_at = available
        await session.flush()
        return identity

    async def claim(self, message_id: uuid.UUID | None = None, *, provider: str | None = None) -> IncomingMessageClaim | None:
        """Claim only received work; terminalize expired handlers without replay.

        Lock order is sender scope then rows. A busy sender cannot stall another
        sender, and a processing batch prevents overlapping visitor handlers.
        """
        async with self.sessions() as discovery:
            now = await self._now(discovery)
            busy = aliased(ProcessedMessagingMessage)
            busy_sender = select(busy.id).where(busy.provider == ProcessedMessagingMessage.provider,
                busy.provider_channel_id.is_not_distinct_from(ProcessedMessagingMessage.provider_channel_id),
                busy.author_provider_id.is_not_distinct_from(ProcessedMessagingMessage.author_provider_id),
                busy.recovery_version == 1, busy.state == "processing", busy.lease_expires_at > now).exists()
            earlier = aliased(ProcessedMessagingMessage)
            earlier_received = select(earlier.id).where(earlier.provider == ProcessedMessagingMessage.provider,
                earlier.provider_channel_id.is_not_distinct_from(ProcessedMessagingMessage.provider_channel_id),
                earlier.author_provider_id.is_not_distinct_from(ProcessedMessagingMessage.author_provider_id),
                earlier.recovery_version == 1, earlier.state == "received",
                or_(earlier.created_at < ProcessedMessagingMessage.created_at,
                    and_(earlier.created_at == ProcessedMessagingMessage.created_at,
                         earlier.id < ProcessedMessagingMessage.id))).exists()
            statement = select(ProcessedMessagingMessage.id).where(ProcessedMessagingMessage.recovery_version == 1,
                ~busy_sender,
                or_(ProcessedMessagingMessage.state == "processing", ~earlier_received),
                or_(and_(ProcessedMessagingMessage.state == "received", ProcessedMessagingMessage.available_at <= now),
                    and_(ProcessedMessagingMessage.state == "processing",
                        or_(ProcessedMessagingMessage.lease_expires_at.is_(None), ProcessedMessagingMessage.lease_expires_at <= now))))
            if message_id is not None:
                statement = statement.where(ProcessedMessagingMessage.id == message_id)
            if provider is not None:
                statement = statement.where(ProcessedMessagingMessage.provider == provider)
            identities = list((await discovery.scalars(statement.order_by(
                ProcessedMessagingMessage.created_at, ProcessedMessagingMessage.id).limit(MAX_CANDIDATES))).all())
        for identity in identities:
            async with self.sessions() as session:
                candidate = await session.get(ProcessedMessagingMessage, identity)
                if candidate is None or not await self._lock_peer(session, candidate.provider,
                        candidate.provider_channel_id, candidate.author_provider_id, wait=False):
                    continue
                candidate = await session.get(ProcessedMessagingMessage, identity, with_for_update=True, populate_existing=True)
                now = await self._now(session)
                if (candidate.recovery_version != 1 or candidate.state not in {"received", "processing"}
                        or provider is not None and candidate.provider != provider):
                    continue
                active = list((await session.scalars(select(ProcessedMessagingMessage).where(
                    self._peer_filter(candidate.provider, candidate.provider_channel_id, candidate.author_provider_id),
                    ProcessedMessagingMessage.recovery_version == 1, ProcessedMessagingMessage.state == "processing")
                    .order_by(ProcessedMessagingMessage.id).with_for_update())).all())
                expired = [row for row in active if row.lease_expires_at is None or row.lease_expires_at <= now]
                for row in expired:
                    self._review(row, now, "processing_interrupted")
                if expired:
                    await session.flush()
                if active and len(expired) != len(active):
                    await session.commit()
                    continue
                if candidate.state != "received" or candidate.available_at is None or candidate.available_at > now:
                    await session.commit()
                    continue
                if not self._valid_received(candidate):
                    self._review(candidate, now, "incoming_snapshot_unavailable")
                    await session.commit()
                    continue
                rows = [candidate]
                if _visitor_text(candidate):
                    peers = list((await session.scalars(select(ProcessedMessagingMessage).where(
                        self._peer_filter(candidate.provider, candidate.provider_channel_id, candidate.author_provider_id),
                        ProcessedMessagingMessage.recovery_version == 1, ProcessedMessagingMessage.state == "received")
                        .order_by(ProcessedMessagingMessage.created_at, ProcessedMessagingMessage.id)
                        .limit(MAX_BATCH_MESSAGES).with_for_update())).all())
                    rows = []
                    for row in peers:
                        if (not _visitor_text(row) or not self._valid_received(row)
                            or row.routing_context != candidate.routing_context or row.available_at > now):
                            break
                        rows.append(row)
                    if not rows:
                        await session.commit()
                        continue
                leader, token = rows[0], uuid.uuid4()
                for row in rows:
                    row.state, row.batch_id, row.claim_token = "processing", leader.id, token
                    row.claimed_at, row.lease_expires_at = now, now + timedelta(seconds=PROCESSING_LEASE_SECONDS)
                await session.commit()
                return IncomingMessageClaim(leader.id, token, tuple(row.id for row in rows),
                    leader.provider, leader.provider_channel_id, leader.author_provider_id)
        return None

    async def owned(self, session: AsyncSession, claim: IncomingMessageClaim) -> tuple[list[ProcessedMessagingMessage], datetime]:
        await self._lock_peer(session, claim.provider, claim.channel_id, claim.author_id)
        rows = list((await session.scalars(select(ProcessedMessagingMessage).where(
            ProcessedMessagingMessage.batch_id == claim.batch_id).order_by(ProcessedMessagingMessage.id)
            .with_for_update().execution_options(populate_existing=True))).all())
        now = await self._now(session)
        if (not claim.message_ids or {row.id for row in rows} != set(claim.message_ids)
            or len(set(claim.message_ids)) != len(claim.message_ids) or claim.batch_id not in claim.message_ids
            or any(row.recovery_version != 1 or row.state != "processing" or row.batch_id != claim.batch_id
                or row.claim_token != claim.token or row.lease_expires_at is None or row.lease_expires_at <= now
                or (row.provider, row.provider_channel_id, row.author_provider_id) != (claim.provider, claim.channel_id, claim.author_id)
                for row in rows)):
            raise IncomingMessageClaimLost("Incoming message processing claim expired or changed.")
        return rows, now

    async def begin_reply_in_session(self, session: AsyncSession, claim: IncomingMessageClaim, *,
        index: int, recipient: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Commit before I/O, after current sender/domain authorization in this TX.

        The scoped delivery collaborator supplies an ordinal, never a fresh retry
        identity. Existing ordinals cannot be resent even after a known rejection.
        Read/typing acknowledgements are optional and are outside this journal.
        """
        rows, now = await self.owned(session, claim)
        leader = next(row for row in rows if row.id == claim.batch_id)
        plan = copy.deepcopy(leader.reply_plan or [])
        if not isinstance(plan, list) or index != len(plan) or not 0 <= index < MAX_REPLIES:
            raise IncomingMessageClaimLost("Reply identity was already used or is out of order.")
        if any(item.get("state") in {"attempting", "unknown"} for item in plan):
            raise IncomingMessageClaimLost("An earlier reply has an unresolved outcome.")
        if not recipient or len(recipient) > 180:
            raise ValueError("A bounded reply recipient is required.")
        body = _bounded_json(payload, label="Outgoing reply")
        if not isinstance(body, dict):
            raise ValueError("An outgoing reply payload is required.")
        entry = {"index": index, "operation_id": str(uuid.uuid5(claim.batch_id, f"reply:{index}")),
            "recipient": recipient, "payload": body, "state": "attempting", "attempted_at": now.isoformat()}
        leader.reply_plan = [*plan, entry]
        for row in rows:
            row.lease_expires_at = now + timedelta(seconds=PROCESSING_LEASE_SECONDS)
        await session.flush()
        return copy.deepcopy(entry)

    async def record_reply_outcome(self, claim: IncomingMessageClaim, index: int, *, delivery: str,
        result: dict[str, Any] | None = None) -> bool:
        if delivery not in REPLY_DELIVERIES:
            raise ValueError("An explicit provider delivery outcome is required.")
        retained = _bounded_json(result or {}, label="Reply receipt")
        async with self.sessions() as session:
            try:
                rows, now = await self.owned(session, claim)
            except IncomingMessageClaimLost:
                await session.rollback()
                return False
            leader = next(row for row in rows if row.id == claim.batch_id)
            plan = copy.deepcopy(leader.reply_plan or [])
            if index < 0 or index >= len(plan) or plan[index].get("state") != "attempting":
                raise IncomingMessageClaimLost("Reply checkpoint no longer owns an attempt.")
            plan[index].update(state=delivery, completed_at=now.isoformat(), result=retained)
            leader.reply_plan = plan
            if delivery == "unknown":
                leader.review_reason = "reply_outcome_unknown"
            await session.commit()
            return True

    async def finish_handling_in_session(self, session: AsyncSession, claim: IncomingMessageClaim,
        result: dict[str, Any]) -> None:
        """Retain a handled result with the caller's final domain transaction."""
        retained = _bounded_json(result, label="Incoming handling result")
        rows, now = await self.owned(session, claim)
        leader = next(row for row in rows if row.id == claim.batch_id)
        if any(item.get("state") == "attempting" for item in leader.reply_plan or []):
            raise IncomingMessageClaimLost("An attempted reply still lacks its receipt.")
        review = leader.review_reason or ("reply_outcome_unknown" if any(
            item.get("state") == "unknown" for item in leader.reply_plan or []) else None)
        for row in rows:
            row.result, row.handled_at = retained, now
            row.state = "review_required" if review else "handled"
            row.review_reason = review
            row.claim_token = row.lease_expires_at = None
        await session.flush()

    async def interrupt(self, claim: IncomingMessageClaim, reason: str, *, result: dict[str, Any] | None = None) -> bool:
        retained = _bounded_json(result, label="Interrupted handling result") if result is not None else None
        async with self.sessions() as session:
            await self._lock_peer(session, claim.provider, claim.channel_id, claim.author_id)
            rows = (await session.scalars(select(ProcessedMessagingMessage).where(
                ProcessedMessagingMessage.batch_id == claim.batch_id).order_by(ProcessedMessagingMessage.id)
                .with_for_update())).all()
            if (not claim.message_ids or {row.id for row in rows} != set(claim.message_ids)
                or len(set(claim.message_ids)) != len(claim.message_ids) or claim.batch_id not in claim.message_ids
                or any(row.recovery_version != 1 or row.state != "processing" or row.claim_token != claim.token
                    or (row.provider, row.provider_channel_id, row.author_provider_id)
                        != (claim.provider, claim.channel_id, claim.author_id) for row in rows)):
                return False
            now = await self._now(session)
            for row in rows:
                self._review(row, now, reason)
                if retained is not None:
                    row.result = retained
            await session.commit()
            return True

    async def recovery_page(self, *, provider: str, limit: int = 25, before_id: uuid.UUID | None = None) -> dict[str, Any]:
        """Read-only provider-scoped keyset; no recovery transition or wakeup."""
        if provider not in {"whatsapp", "discord"} or not 1 <= limit <= 100:
            raise ValueError("A supported provider and bounded page size are required.")
        async with self.sessions() as session:
            statement = select(ProcessedMessagingMessage).where(
                ProcessedMessagingMessage.recovery_version == 1, ProcessedMessagingMessage.provider == provider)
            if before_id is not None:
                cursor = await session.get(ProcessedMessagingMessage, before_id)
                if cursor is None or cursor.recovery_version != 1 or cursor.provider != provider:
                    raise LookupError("Incoming message cursor not found.")
                statement = statement.where(or_(ProcessedMessagingMessage.created_at < cursor.created_at,
                    and_(ProcessedMessagingMessage.created_at == cursor.created_at, ProcessedMessagingMessage.id < cursor.id)))
            rows = list((await session.scalars(statement.order_by(
                ProcessedMessagingMessage.created_at.desc(), ProcessedMessagingMessage.id.desc()).limit(limit + 1))).all())
            visible = rows[:limit]
            owners = await self._reply_owners(session, visible)
            now = await self._now(session)
            return {"items": [incoming_recovery_view(row, reply_owner=owners.get(row.batch_id), now=now) for row in visible],
                "next_cursor": str(visible[-1].id) if len(rows) > limit else None}

    async def recovery_detail(self, identity: uuid.UUID, *, provider: str) -> dict[str, Any]:
        async with self.sessions() as session:
            row = await session.get(ProcessedMessagingMessage, identity)
            if row is None or row.recovery_version != 1 or row.provider != provider:
                raise LookupError("Incoming message not found.")
            owners = await self._reply_owners(session, [row])
            return incoming_recovery_view(row, reply_owner=owners.get(row.batch_id), now=await self._now(session))

    @staticmethod
    async def _reply_owners(session: AsyncSession, rows) -> dict[uuid.UUID, ProcessedMessagingMessage]:
        identities = {row.batch_id for row in rows if row.batch_id is not None}
        if not identities:
            return {}
        return {row.id: row for row in (await session.scalars(select(ProcessedMessagingMessage).where(
            ProcessedMessagingMessage.id.in_(identities)))).all()}

    async def get(self, identity: uuid.UUID) -> ProcessedMessagingMessage | None:
        """Internal read only; an API must authorize and redact its projection."""
        async with self.sessions() as session:
            return await session.get(ProcessedMessagingMessage, identity)

    @staticmethod
    def _valid_received(row: ProcessedMessagingMessage) -> bool:
        return (row.state == "received" and row.claim_token is None and row.batch_id is None
            and isinstance(row.envelope, dict) and isinstance(row.envelope.get("message"), dict)
            and isinstance(row.routing_context, dict) and row.routing_context.get("kind") in {"admin", "standard", "visitor", "denied"}
            and row.reply_plan == [] and row.author_provider_id is not None)

    @staticmethod
    def _review(row: ProcessedMessagingMessage, now: datetime, reason: str) -> None:
        plan = copy.deepcopy(row.reply_plan or [])
        for entry in plan if isinstance(plan, list) else []:
            if isinstance(entry, dict) and entry.get("state") == "attempting":
                entry.update(state="unknown", completed_at=now.isoformat(), result={"reason": "processing_interrupted"})
        row.reply_plan = plan
        row.state, row.handled_at, row.review_reason = "review_required", now, str(reason)[:500]
        row.claim_token = row.lease_expires_at = None


def _public_uuid(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _public_time(value: Any) -> str | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return parsed.isoformat() if parsed.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def incoming_recovery_view(row: ProcessedMessagingMessage, *, reply_owner: ProcessedMessagingMessage | None,
    now: datetime) -> dict[str, Any]:
    """Allowlisted operator truth, deliberately excluding message/recipient bodies."""
    binding = row.routing_context if isinstance(row.routing_context, dict) else {}
    owner = reply_owner if reply_owner is not None and reply_owner.provider == row.provider else row
    plan = owner.reply_plan if isinstance(owner.reply_plan, list) else []
    result = row.result if isinstance(row.result, dict) else {}
    reasons = {"processing_interrupted", "incoming_snapshot_unavailable", "reply_outcome_unknown",
        "sender_binding_changed", "handler_interrupted", "handler_failed", "reply_receipt_lost",
        "interaction_reply_unavailable", "channel_unavailable", "discord_admission_changed",
        "discord_membership_unavailable"}
    expired = row.state == "processing" and (row.lease_expires_at is None or row.lease_expires_at <= now)
    reason = row.review_reason
    replies = [{"index": entry.get("index") if type(entry.get("index")) is int else None,
        "operation_id": _public_uuid(entry.get("operation_id")),
        "delivery": entry.get("state") if entry.get("state") in REPLY_DELIVERIES | {"attempting"} else "unknown",
        "attempted_at": _public_time(entry.get("attempted_at")), "completed_at": _public_time(entry.get("completed_at"))}
        for entry in plan[:MAX_REPLIES] if isinstance(entry, dict)]
    return {
        "id": str(row.id), "provider": row.provider,
        # WhatsApp IDs can encode recipient information. The IACS id is the
        # recovery identity; only Discord's opaque numeric message id is exposed.
        "provider_message_id": row.provider_message_id if row.provider == "discord" and row.provider_message_id.isdecimal() else None,
        "batch_id": _public_uuid(row.batch_id), "state": row.state,
        "origin_kind": binding.get("kind") if binding.get("kind") in {"admin", "standard", "visitor", "denied"} else "unavailable",
        "user_id": _public_uuid(binding.get("user_id")), "visitor_pass_id": _public_uuid(binding.get("pass_id")),
        "created_at": _public_time(row.created_at), "received_at": _public_time(row.received_at),
        "available_at": _public_time(row.available_at), "claimed_at": _public_time(row.claimed_at),
        "handled_at": _public_time(row.handled_at), "lease_expired": expired,
        "requires_review": row.state == "review_required" or expired or any(item["delivery"] == "unknown" for item in replies),
        "review_reason": reason if reason in reasons else "delivery_requires_review" if reason else None,
        "replies": replies,
        "result_ids": {key: identity for key in ("session_id", "confirmation_id", "visitor_pass_id", "notification_run_id", "feedback_id")
            if (identity := (str(result.get(key)) if key == "confirmation_id" and
                re.fullmatch(r"confirm-[0-9a-f]{32}", str(result.get(key))) else _public_uuid(result.get(key)))) is not None},
    }
