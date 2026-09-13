"""Durable notification claims and checkpoints. No providers or realtime I/O."""

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.db.session import AsyncSessionLocal
from app.models import NotificationRun
from app.services.telemetry import TELEMETRY_CATEGORY_INTEGRATIONS, write_audit_log

LEASE_SECONDS = 300
MAX_DISPATCH_AGE_SECONDS = 900
MAX_PREPARATION_ATTEMPTS = 3
ACTIVE_STATUSES = ("queued", "processing")
DELIVERY_CERTAINTIES = {"accepted", "not_sent", "rejected", "unknown"}
_MOBILE_RECEIPT_TARGET = re.compile(r"notify\.mobile_app_[A-Za-z0-9_]{1,192}\Z")
_DISCORD_RECEIPT_TARGET = re.compile(r"[0-9]{1,32}\Z")


class ClaimLost(RuntimeError):
    """The worker must stop; it no longer owns this run."""


@dataclass(frozen=True)
class NotificationActionAuthorization:
    """Policy result returned by an attempt authorization participant.

    A global denial revokes the whole originating notification. An action skip
    only cancels this still-unattempted action, such as a changed saved rule.
    """

    global_denial: str | None = None
    action_skip: str | None = None


@dataclass(frozen=True)
class NotificationActionStart:
    """Whether a pending action was durably transitioned to attempting."""

    attempted: bool
    skip_reason: str = ""


def safe_destination_outcomes(value: Any) -> list[dict[str, str]]:
    """Keep only compact, non-secret destination receipts in a journal row."""
    if not isinstance(value, list):
        return []
    safe: list[dict[str, str]] = []
    for raw in value[:100]:
        if not isinstance(raw, dict):
            continue
        target = raw.get("target")
        delivery = raw.get("delivery")
        if not isinstance(target, str) or not isinstance(delivery, str):
            continue
        # Apprise is an opaque fan-out. Never retain an Apprise URL or its
        # credentials. Home Assistant mobile services and numeric Discord
        # channels are stable, non-secret endpoint labels.
        if not (
            target == "apprise"
            or _MOBILE_RECEIPT_TARGET.fullmatch(target)
            or _DISCORD_RECEIPT_TARGET.fullmatch(target)
        ):
            continue
        if delivery not in DELIVERY_CERTAINTIES:
            continue
        safe.append({"target": target[:255], "delivery": delivery})
    return safe


def destination_outcome_truth(value: Any) -> tuple[bool, int, bool]:
    """Aggregate validated delivery facts before receipt labels are redacted.

    A destination label can be unsafe to retain and the journal display is
    deliberately bounded. Neither constraint may hide a provider-reported
    rejected or unknown outcome from recovery policy.
    """
    if not isinstance(value, list):
        return False, 0, False
    accepted_any = False
    failure_count = 0
    review_required = False
    for raw in value:
        if not isinstance(raw, dict):
            continue
        delivery = raw.get("delivery")
        if not isinstance(delivery, str) or delivery not in DELIVERY_CERTAINTIES:
            continue
        if delivery == "accepted":
            accepted_any = True
            continue
        failure_count += 1
        if delivery == "unknown":
            review_required = True
    return accepted_any, failure_count, review_required


class NotificationRunStore:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.sessions = session_factory

    async def create(
        self, context: dict[str, Any], *, rules_override=None, run_id=None
    ) -> uuid.UUID:
        identity, _ = await self._create(context, rules_override, run_id, immediate=False)
        return identity

    async def reserve(self, context: dict[str, Any], *, rules_override=None, run_id=None):
        """Insert and claim atomically so polling cannot steal a synchronous send."""
        return await self._create(context, rules_override, run_id, immediate=True)

    async def enqueue_in_session(
        self, session, context: dict[str, Any], *, run_id: uuid.UUID, rules_override=None,
    ) -> uuid.UUID:
        """Join the origin transaction; the caller owns commit and any wakeup.

        A stable origin ID makes repeated reservations inert. Polling discovers
        committed work even when the producer stops before waking the dispatcher.
        """
        identity = uuid.UUID(str(run_id))
        await self._insert(session, context, rules_override, identity, immediate=False)
        return identity

    async def enqueue_prepared_in_session(self, session, context, *, run_id, plan) -> uuid.UUID:
        """Reserve a concrete domain-owned output with its originating mutation."""
        identity, _ = await self._prepared(session, context, run_id, plan, immediate=False)
        return identity

    async def reserve_prepared_in_session(self, session, context, *, run_id, plan):
        """Join confirmed intake while preventing a poller stealing the first attempt."""
        return await self._prepared(session, context, run_id, plan, immediate=True)

    async def _prepared(self, session, context, run_id, plan, *, immediate):
        identity = uuid.UUID(str(run_id))
        if not isinstance(plan, list) or not plan or len(plan) > 100:
            raise ValueError("A bounded prepared notification plan is required")
        if any(item.get("state") not in {"pending", "skipped"} for item in plan):
            raise ValueError("New notification plans cannot contain attempted actions")
        payload = copy.deepcopy(context)
        fingerprint = hashlib.sha256(json.dumps(
            {"context": payload, "plan": plan}, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        payload["prepared_plan_hash"] = fingerprint
        row = await self._insert(session, payload, None, identity, immediate=immediate, plan=plan)
        if row is None:
            existing = await session.scalar(select(NotificationRun).where(
                NotificationRun.id == identity,
            ).with_for_update().execution_options(populate_existing=True))
            if existing is None or existing.context.get("prepared_plan_hash") != fingerprint:
                raise ValueError("Notification operation identity was reused with different content or authority")
        return identity, row if immediate else None

    async def _create(self, context, rules_override, run_id, *, immediate):
        identity = run_id or uuid.uuid4()
        async with self.sessions() as session:
            row = await self._insert(session, context, rules_override, identity, immediate=immediate)
            await session.commit()
        return identity, row if immediate else None

    async def _insert(self, session, context, rules_override, identity, *, immediate, plan=None):
        now = await session.scalar(select(func.clock_timestamp()))
        return await session.scalar(
            insert(NotificationRun)
            .values(
                id=identity,
                recovery_version=1,
                status="processing" if immediate else "queued",
                claim_count=1 if immediate else 0,
                claim_token=uuid.uuid4() if immediate else None,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS) if immediate else None,
                started_at=now if immediate else None,
                trigger_event=context["event_type"],
                subject=context["subject"][:255],
                severity=context["severity"],
                context=context,
                queued_at=now,
                rules_override=rules_override,
                delivery_plan=copy.deepcopy(plan),
            )
            .on_conflict_do_nothing(index_elements=[NotificationRun.id])
            .returning(NotificationRun)
        )

    async def get(self, run_id: uuid.UUID) -> NotificationRun:
        async with self.sessions() as session:
            row = await session.get(NotificationRun, run_id)
            if row is None:
                raise LookupError("Notification run not found")
            return row

    async def claim(self, run_id: uuid.UUID | None = None) -> NotificationRun | None:
        async with self.sessions() as session:
            now = await session.scalar(select(func.clock_timestamp()))
            query = select(NotificationRun).where(
                NotificationRun.recovery_version == 1,
                NotificationRun.status.in_(ACTIVE_STATUSES),
                or_(NotificationRun.claim_token.is_(None), NotificationRun.lease_expires_at <= now),
            )
            if run_id:
                query = query.where(NotificationRun.id == run_id)
            row = await session.scalar(
                query.order_by(NotificationRun.queued_at, NotificationRun.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            plan = copy.deepcopy(row.delivery_plan)
            if plan and any(item["state"] == "attempting" for item in plan):
                for item in plan:
                    if item["state"] == "attempting":
                        item["state"] = "unknown"
                row.delivery_plan = plan
                self._review(row, "provider_outcome_unknown", now)
            elif (now - row.queued_at).total_seconds() > MAX_DISPATCH_AGE_SECONDS:
                self._review(row, "dispatch_age_exceeded", now)
            elif row.delivery_plan is None and row.claim_count >= MAX_PREPARATION_ATTEMPTS:
                self._review(row, "preparation_attempts_exhausted", now)
            else:
                row.status = "processing"
                row.claim_token = uuid.uuid4()
                row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                row.started_at = row.started_at or now
                row.claim_count += 1
                await session.commit()
                return row
            self._counts(row)
            await self._audit_checkpoint(session, row, "review", reason=row.review_reason)
            await session.commit()
            return None

    async def _owned(self, session, run_id, token):
        row = await session.scalar(
            select(NotificationRun).where(NotificationRun.id == run_id).with_for_update().execution_options(populate_existing=True)
        )
        now = await session.scalar(select(func.clock_timestamp()))
        if (
            row is None
            or row.status != "processing"
            or row.claim_token != token
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            raise ClaimLost("Notification claim expired or changed")
        return row, now

    async def save_plan(self, run_id, token, plan: list[dict[str, Any]]) -> None:
        async with self.sessions() as session:
            row, now = await self._owned(session, run_id, token)
            if row.delivery_plan is not None:
                raise ValueError("Notification plan is immutable after preparation")
            row.delivery_plan = copy.deepcopy(plan)
            row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            await session.commit()

    async def begin_action(
        self,
        run_id,
        token,
        index: int,
        *,
        authorize_origin=None,
        refresh_authorization=None,
    ) -> NotificationActionStart:
        async with self.sessions() as session:
            # Read the immutable origin first, then lock rule -> automation run
            # -> notification run. Never invert the producer handoff order.
            snapshot = await session.get(NotificationRun, run_id)
            authorization = None
            origins = ("automation_origin", "confirmed_delivery", "visitor_conversation_origin")
            if snapshot is not None and (authorize_origin is not None or any(snapshot.context.get(key) is not None for key in origins)):
                authorization = (await authorize_origin(session, snapshot.context, run_id)
                                 if authorize_origin is not None else "notification_origin_validator_missing")
            denial, action_skip = _authorization_parts(authorization)
            row, now = await self._owned(session, run_id, token)
            if not denial and row.context.get("confirmed_delivery") is not None and authorize_origin is not None:
                # Actor was locked before the run. Refresh mutable configuration
                # after the final blocking lock, not from a snapshot taken while
                # waiting behind another transaction.
                denial, action_skip = _authorization_parts(
                    await authorize_origin(session, row.context, run_id)
                )
            elif not denial and not action_skip and refresh_authorization is not None:
                # The initial policy participant acquired its domain locks before
                # this journal row. A transport-only configuration refresh may
                # now run after the final blocking lock without inverting that
                # rule-to-run lock order.
                denial, action_skip = _authorization_parts(
                    await refresh_authorization(session, row.context, run_id)
                )
            if denial:
                plan = copy.deepcopy(row.delivery_plan)
                for item in plan or []:
                    if item["state"] == "pending":
                        item.update(state="skipped", reason=denial)
                row.delivery_plan = plan
                self._counts(row)
                row.status = "provider_accepted" if row.delivered_count else "skipped"
                row.finished_at, row.claim_token, row.lease_expires_at = now, None, None
                if denial in {"ephemeral_configuration_unavailable", "whatsapp_frozen_recipients_unavailable",
                              "discord_frozen_recipients_unavailable"}:
                    self._review(row, denial, now)
                await self._audit_checkpoint(session, row, "denied", reason=denial)
                await session.commit()
                raise ClaimLost("Origin no longer authorizes this notification.")
            if (now - row.queued_at).total_seconds() > MAX_DISPATCH_AGE_SECONDS:
                self._review(row, "dispatch_age_exceeded", now)
                self._counts(row)
                await self._audit_checkpoint(session, row, "review", reason=row.review_reason)
                await session.commit()
                raise ClaimLost("Notification is too old to dispatch automatically")
            plan = copy.deepcopy(row.delivery_plan)
            if plan[index]["state"] != "pending":
                raise ClaimLost("Action was already attempted")
            if action_skip:
                plan[index].update(state="skipped", reason=action_skip)
                row.delivery_plan = plan
                self._counts(row)
                row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                await self._audit_checkpoint(session, row, "skipped", index=index, reason=action_skip)
                await session.commit()
                return NotificationActionStart(attempted=False, skip_reason=action_skip)
            plan[index]["state"] = "attempting"
            row.delivery_plan = plan
            row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            await self._audit_checkpoint(session, row, "attempting", index=index)
            await session.commit()
            return NotificationActionStart(attempted=True)

    async def finish_action(self, run_id, token, index: int, outcome: dict[str, Any], *, prepare_output=None) -> None:
        async with self.sessions() as session:
            # Domain output locks precede the journal row, as in intake. The
            # returned participant may write only after this claim is verified.
            snapshot = await session.get(NotificationRun, run_id) if prepare_output is not None else None
            record_output = await prepare_output(session, snapshot, index, outcome) if prepare_output is not None else None
            row, now = await self._owned(session, run_id, token)
            plan = copy.deepcopy(row.delivery_plan)
            if plan[index]["state"] != "attempting":
                raise ClaimLost("Action checkpoint changed")
            plan[index].update(outcome)
            row.delivery_plan = plan
            self._counts(row)
            if outcome["state"] == "unknown" or outcome.get("review_required"):
                self._review(row, "provider_outcome_unknown", now)
            else:
                row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            await self._audit_checkpoint(
                session,
                row,
                outcome["state"],
                index=index,
                reason=outcome.get("reason"),
                checkpoint=outcome,
            )
            if record_output is not None:
                await record_output()
            await session.commit()

    async def finish(self, run_id, token) -> NotificationRun:
        async with self.sessions() as session:
            row, now = await self._owned(session, run_id, token)
            if any(
                item["state"] in ("pending", "attempting", "unknown")
                for item in row.delivery_plan or []
            ):
                raise ClaimLost("Notification has unresolved actions")
            self._counts(row)
            row.status = (
                "provider_accepted"
                if row.delivered_count
                else "failed"
                if row.failed_count
                else "skipped"
            )
            row.finished_at = now
            row.claim_token = None
            row.lease_expires_at = None
            await session.commit()
            return row

    async def interrupt(self, run_id, token) -> None:
        async with self.sessions() as session:
            try:
                row, now = await self._owned(session, run_id, token)
            except ClaimLost:
                return
            plan = copy.deepcopy(row.delivery_plan)
            if plan and any(item["state"] == "attempting" for item in plan):
                for item in plan:
                    if item["state"] == "attempting":
                        item["state"] = "unknown"
                row.delivery_plan = plan
                self._review(row, "provider_outcome_unknown", now)
            else:
                row.status = "queued"
                row.claim_token = None
                row.lease_expires_at = None
            self._counts(row)
            if row.status == "review_required":
                await self._audit_checkpoint(session, row, "review", reason=row.review_reason)
            await session.commit()

    @staticmethod
    async def _audit_checkpoint(session, row, delivery, *, index=None, reason=None, checkpoint=None):
        origin = row.context.get("confirmed_delivery") or row.context.get("visitor_conversation_origin")
        if not isinstance(origin, dict):
            return
        details = checkpoint if isinstance(checkpoint, dict) else {}
        review_required = bool(details.get("review_required"))
        audit_outcome = (
            "uncertain"
            if delivery in {"unknown", "review"} or review_required
            else "failed"
            if delivery in {"denied", "failed"}
            else "success"
        )
        metadata = {
            "delivery": delivery,
            "action_index": index,
            "reason": "provider_outcome_unknown" if review_required else reason,
            "review_required": review_required,
        }
        for key in ("accepted_any", "partial_failure", "failure_count", "delivery_uncertain"):
            if key in details:
                metadata[key] = details[key]
        outcomes = safe_destination_outcomes(details.get("destination_outcomes"))
        if outcomes:
            metadata["destination_outcomes"] = outcomes
        await write_audit_log(
            session, category=TELEMETRY_CATEGORY_INTEGRATIONS, action="notification.delivery.checkpoint",
            actor="notification_dispatch", actor_user_id=origin.get("user_id"),
            target_entity="NotificationRun", target_id=str(row.id),
            outcome=audit_outcome,
            level="warning" if audit_outcome in {"uncertain", "failed"} else "info",
            metadata=metadata,
        )

    @staticmethod
    def _review(row, reason, now):
        row.status = "review_required"
        row.review_reason = reason
        row.error = reason
        row.finished_at = now
        row.claim_token = None
        row.lease_expires_at = None

    @staticmethod
    def _counts(row):
        plan = row.delivery_plan or []
        row.delivered_count = sum(x["state"] == "accepted" for x in plan)
        row.failed_count = sum(_item_failure_count(item) for item in plan)
        row.skipped_count = sum(x["state"] == "skipped" for x in plan)
        row.failures = [
            _failure_reason(item)
            for item in plan
            if _item_failure_count(item) or item.get("review_required")
        ]
        row.skipped_reasons = [x.get("reason", "skipped") for x in plan if x["state"] == "skipped"]


def review_filter():
    return or_(
        NotificationRun.status == "review_required",
        and_(
            NotificationRun.recovery_version.is_(None), NotificationRun.status.in_(ACTIVE_STATUSES)
        ),
    )


def run_summary(row: NotificationRun) -> dict[str, Any]:
    historical = row.recovery_version is None and row.status in ACTIVE_STATUSES
    return {
        "id": str(row.id),
        "status": "review_required" if historical else row.status,
        "trigger_event": row.trigger_event,
        "severity": row.severity,
        "recovery_version": row.recovery_version,
        "lease_expires_at": row.lease_expires_at,
        "stored_status": row.status,
        "review_reason": "historical_unfinished" if historical else row.review_reason,
        "queued_at": row.queued_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "delivered_count": row.delivered_count,
        "failed_count": row.failed_count,
        "skipped_count": row.skipped_count,
        "actions": [
            {
                "index": index,
                "state": item["state"],
                "rule_id": item.get("rule", {}).get("id"),
                "action_id": item.get("action", {}).get("id"),
                "partial_failure": item.get("partial_failure", False),
                "failure_count": _item_failure_count(item),
                "review_required": bool(item.get("review_required")),
                "delivery_uncertain": bool(item.get("delivery_uncertain")),
                "reason": (
                    _failure_reason(item)
                    if _item_failure_count(item) or item.get("review_required")
                    else str(item.get("reason") or "")[:120]
                ),
                "destination_outcomes": safe_destination_outcomes(item.get("destination_outcomes")),
            }
            for index, item in enumerate(row.delivery_plan or [])
        ],
    }


def _authorization_parts(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, NotificationActionAuthorization):
        return value.global_denial, value.action_skip
    return (str(value), None) if isinstance(value, str) and value else (None, None)


def _item_failure_count(item: dict[str, Any]) -> int:
    if item.get("state") not in {"accepted", "failed", "unknown"}:
        return 0
    try:
        recorded = max(0, int(item.get("failure_count", 0)))
    except (TypeError, ValueError):
        recorded = 0
    return max(
        recorded,
        1 if item.get("state") in {"failed", "unknown"} or item.get("review_required") else 0,
    )


def _failure_reason(item: dict[str, Any]) -> str:
    _, _, raw_uncertain = destination_outcome_truth(item.get("destination_outcomes"))
    if raw_uncertain or item.get("state") == "unknown" or item.get("review_required"):
        return "provider_outcome_unknown"
    reason = item.get("reason")
    if reason in {"provider_rejected", "provider_not_sent"}:
        return reason
    outcomes = safe_destination_outcomes(item.get("destination_outcomes"))
    if any(outcome["delivery"] == "rejected" for outcome in outcomes):
        return "provider_rejected"
    return "provider_not_sent"
