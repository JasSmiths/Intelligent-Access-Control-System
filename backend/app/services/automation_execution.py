"""Durable automation occurrences and ordered action checkpoints.

This owner performs no provider I/O and imports no rule service or transport.
Origin transactions reserve an immutable plan; one leased worker advances it.
An attempting action is never returned to pending, including after a restart.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AutomationRun

LEASE_SECONDS = 300
MAX_CLAIM_SCAN = 50
ACTIVE_STATUSES = ("queued", "processing")
ACTION_TERMINAL_STATES = frozenset({"succeeded", "failed", "skipped", "unknown"})
# These may continue after an uncertain earlier hardware action. Their own
# attempting checkpoint still prevents an ambiguous delivery from being retried.
NOTIFICATION_ACTION_TYPES = frozenset({
    "notification.enable", "notification.disable", "integration.whatsapp.send_message",
})


class AutomationClaimLost(RuntimeError):
    """No current claim exists; the caller must stop before any new I/O."""


@dataclass(frozen=True)
class AutomationActionWait:
    """A pending hardware action awaits its primary admission, without an attempt."""

    reason: str
    check_after: datetime
    deadline: datetime


def occurrence_key(origin_kind: str, origin_id: str, rule_id: uuid.UUID, trigger: str) -> str:
    """Server-origin identity, never a value copied from a webhook payload."""
    if not origin_kind.strip() or not origin_id.strip() or not trigger.strip():
        raise ValueError("A stable automation origin and trigger are required.")
    encoded = json.dumps([origin_kind, origin_id, str(rule_id), trigger], separators=(",", ":"))
    return "automation:" + hashlib.sha256(encoded.encode()).hexdigest()


def frozen_action_plan(run_id: uuid.UUID, planned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign stable operation identities once to the caller's resolved actions.

    ``planned`` contains action input and optional resolved target plans. Runtime
    state and operation IDs are owned here, rather than accepted from that input.
    JSON round-tripping rejects nonpersistent values without telemetry truncation.
    """
    plan = json.loads(json.dumps(planned, allow_nan=False))
    if not isinstance(plan, list):
        raise ValueError("Automation actions must be an ordered list.")
    for index, item in enumerate(plan):
        action = item.get("action") if isinstance(item, dict) else None
        if (not isinstance(action, dict) or not isinstance(action.get("id"), str) or not action["id"].strip()
                or not isinstance(action.get("type"), str) or not action["type"].strip()):
            raise ValueError("Every planned automation action needs its configured ID and type.")
        identity = uuid.uuid5(run_id, f"action:{index}:{action['id']}")
        for key in ("attempted_at", "completed_at", "result", "reason", "wait_reason", "wait_started_at", "wait_until"):
            item.pop(key, None)
        item.update(index=index, operation_id=str(identity), idempotency_key=str(identity), state="pending")
    return plan


class AutomationRunStore:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.sessions = session_factory

    async def reserve(
        self, session: AsyncSession, *, rule_id: uuid.UUID, trigger_key: str,
        occurrence: str, context: dict[str, Any], planned: list[dict[str, Any]],
        trigger_payload: dict[str, Any], actor: str, source: str,
        trace_id: str | None = None, run_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Origin transaction participant: flush only; never commit or dispatch.

        The context is an explicit versioned execution snapshot assembled by the
        admitted origin, not sanitized logging output or a later live rule read.
        A duplicate occurrence returns its retained plan without replacing it.
        """
        if not occurrence or len(occurrence) > 255:
            raise ValueError("A bounded stable occurrence key is required.")
        identity = run_id or uuid.uuid4()
        snapshot = json.loads(json.dumps(context, allow_nan=False))
        if snapshot.get("version") != 1:
            raise ValueError("A versioned automation execution snapshot is required.")
        plan = frozen_action_plan(identity, planned)
        now = await self._now(session)
        inserted = await session.scalar(insert(AutomationRun).values(
            id=identity, rule_id=rule_id, trigger_key=trigger_key, recovery_version=1,
            occurrence_key=occurrence, queued_at=now, started_at=now,
            status="queued", claim_count=0, context=snapshot, action_plan=plan,
            trigger_payload=trigger_payload, condition_results=[], action_results=[],
            actor=actor, source=source, trace_id=trace_id,
        ).on_conflict_do_nothing(index_elements=[AutomationRun.occurrence_key]).returning(AutomationRun.id))
        if inserted is not None:
            return inserted
        retained = await session.scalar(select(AutomationRun).where(AutomationRun.occurrence_key == occurrence))
        if retained is None or retained.rule_id != rule_id or retained.trigger_key != trigger_key:
            raise ValueError("An occurrence identity cannot be rebound to a different automation rule or trigger.")
        return retained.id

    async def get(self, run_id: uuid.UUID) -> AutomationRun:
        async with self.sessions() as session:
            row = await session.get(AutomationRun, run_id)
            if row is None:
                raise LookupError("Automation run not found.")
            return row

    async def read_page(
        self, session: AsyncSession, *, limit: int = 25, before_id: uuid.UUID | None = None,
    ) -> tuple[list[AutomationRun], uuid.UUID | None]:
        """Read stable history without claiming, reconciling or dispatching work."""
        if not 1 <= limit <= 100:
            raise ValueError("Automation history limit must be between 1 and 100.")
        query = select(AutomationRun)
        if before_id is not None:
            cursor = await session.get(AutomationRun, before_id)
            if cursor is None:
                raise LookupError("Automation history cursor not found.")
            query = query.where(or_(AutomationRun.created_at < cursor.created_at,
                (AutomationRun.created_at == cursor.created_at) & (AutomationRun.id < cursor.id)))
        rows = list((await session.scalars(query.order_by(AutomationRun.created_at.desc(), AutomationRun.id.desc())
            .limit(limit + 1).execution_options(populate_existing=True))).all())
        return rows[:limit], rows[limit - 1].id if len(rows) > limit else None

    async def read_detail(self, session: AsyncSession, run_id: uuid.UUID) -> AutomationRun:
        row = await session.get(AutomationRun, run_id, populate_existing=True)
        if row is None:
            raise LookupError("Automation run not found.")
        return row

    async def claim(self, run_id: uuid.UUID | None = None) -> AutomationRun | None:
        """Find actionable pending work, passing over rows terminalized on recovery.

        Each unsuccessful candidate is removed from the queue in its transaction.
        Bounded scanning cannot repeatedly return the same ineligible first row.
        """
        for _ in range(MAX_CLAIM_SCAN):
            async with self.sessions() as session:
                now = await self._now(session)
                query = select(AutomationRun).where(
                    AutomationRun.recovery_version == 1,
                    AutomationRun.status.in_(ACTIVE_STATUSES),
                    # A queued row may carry a durable not-before deadline.
                    # Tokenless does not mean immediately eligible in that state.
                    or_(AutomationRun.lease_expires_at.is_(None), AutomationRun.lease_expires_at <= now,
                        (AutomationRun.status == "processing") & AutomationRun.claim_token.is_(None)),
                )
                if run_id is not None:
                    query = query.where(AutomationRun.id == run_id)
                row = await session.scalar(query.order_by(AutomationRun.queued_at, AutomationRun.id)
                                           .limit(1).with_for_update(skip_locked=True))
                if row is None:
                    return None
                if (row.queued_at is None or not self._valid_plan(row.id, row.action_plan)
                        or not isinstance(row.context, dict) or row.context.get("version") != 1):
                    self._terminal(row, "review_required", now, "execution_snapshot_unavailable")
                    await session.commit()
                    if run_id is not None:
                        return None
                    continue
                self._recover_attempts(row, now)
                if not any(item["state"] == "pending" for item in row.action_plan):
                    self._finish(row, now)
                    await session.commit()
                    if run_id is not None:
                        return None
                    continue
                row.status = "processing"
                row.claim_token = uuid.uuid4()
                row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                row.claim_count += 1
                await session.commit()
                return row
        return None

    async def owned(self, session: AsyncSession, run_id: uuid.UUID, token: uuid.UUID) -> tuple[AutomationRun, datetime]:
        """Lock after the caller's actor/rule preflight locks, before action state."""
        row = await session.scalar(select(AutomationRun).where(AutomationRun.id == run_id)
                                   .with_for_update().execution_options(populate_existing=True))
        now = await self._now(session)
        if (row is None or row.recovery_version != 1 or row.status != "processing" or not token
                or row.claim_token != token or row.lease_expires_at is None or row.lease_expires_at <= now):
            raise AutomationClaimLost("Automation claim expired or changed.")
        return row, now

    async def begin_action(self, session: AsyncSession, run_id: uuid.UUID, token: uuid.UUID, index: int) -> dict[str, Any]:
        """Participant: caller commits this checkpoint before provider transmission.

        Call the rule/domain preflight in this transaction first. Local mutations
        may instead use complete_local_action in their shared transaction.
        """
        row, now = await self.owned(session, run_id, token)
        plan = copy.deepcopy(row.action_plan)
        item = self._next_pending(plan, index)
        item.update(state="attempting", attempted_at=now.isoformat())
        row.action_plan = plan
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        await session.flush()
        return copy.deepcopy(item)

    async def defer_action(self, session: AsyncSession, run_id: uuid.UUID, token: uuid.UUID,
                           index: int, wait: AutomationActionWait) -> bool:
        """Release only unattempted work; queued_at remains its original age.

        In queued state lease_expires_at is the next eligibility time, with no
        owner token. Processing rows retain the existing worker-lease semantics.
        This never recovers or retries an attempted action.
        """
        row, now = await self.owned(session, run_id, token)
        plan = copy.deepcopy(row.action_plan)
        item = self._next_pending(plan, index)
        if any(part["state"] == "attempting" for part in plan) or item.get("attempted_at"):
            raise AutomationClaimLost("An attempted action cannot be deferred.")
        if now > wait.deadline:
            return False
        check_after = min(max(wait.check_after, now + timedelta(microseconds=1)),
                          wait.deadline + timedelta(microseconds=1), now + timedelta(seconds=5))
        item.update(wait_reason=wait.reason, wait_started_at=item.get("wait_started_at") or now.isoformat(),
                    wait_until=check_after.isoformat())
        row.action_plan = plan
        row.status, row.claim_token, row.lease_expires_at = "queued", None, check_after
        await session.flush()
        return True

    async def finish_action(self, run_id: uuid.UUID, token: uuid.UUID, index: int,
                            result: dict[str, Any], *, state: str) -> AutomationRun:
        """A definitive/unknown receipt commits independently of later presentation."""
        async with self.sessions() as session:
            row, now = await self.owned(session, run_id, token)
            self._checkpoint(row, index, result, state=state, expected="attempting", now=now)
            await session.commit()
            return row

    async def complete_local_action(self, session: AsyncSession, run_id: uuid.UUID, token: uuid.UUID,
                                    index: int, result: dict[str, Any], *, state: str) -> None:
        """Mutation + action receipt + required machine audit share the caller commit.

        This entry point never claims external work completed without its committed
        attempting checkpoint. It is exclusively for local transactional effects
        and preflight skips, which cannot have transmitted anything.
        """
        if state not in {"succeeded", "failed", "skipped"}:
            raise ValueError("Local actions cannot report uncertain external delivery.")
        row, now = await self.owned(session, run_id, token)
        item = self._next_pending(row.action_plan, index)
        if state == "succeeded" and item["action"]["type"] not in {"notification.enable", "notification.disable", "integration.whatsapp.send_message"}:
            raise ValueError("Only transactional notification activation or durable delivery handoff can complete without an external attempt.")
        self._checkpoint(row, index, result, state=state, expected="pending", now=now)
        await session.flush()

    async def finish(self, run_id: uuid.UUID, token: uuid.UUID) -> AutomationRun:
        async with self.sessions() as session:
            row = await self.finish_in_session(session, run_id, token)
            await session.commit()
            return row

    async def finish_in_session(self, session: AsyncSession, run_id: uuid.UUID, token: uuid.UUID) -> AutomationRun:
        """Allow final local mutation, completion receipt and required audit to commit together."""
        row, now = await self.owned(session, run_id, token)
        if any(item["state"] in {"pending", "attempting"} for item in row.action_plan):
            raise AutomationClaimLost("Automation has unfinished actions.")
        self._finish(row, now)
        await session.flush()
        return row

    async def interrupt(self, run_id: uuid.UUID, token: uuid.UUID) -> None:
        async with self.sessions() as session:
            try:
                row, now = await self.owned(session, run_id, token)
            except AutomationClaimLost:
                return
            self._recover_attempts(row, now)
            if any(item["state"] == "pending" for item in row.action_plan):
                row.status, row.claim_token, row.lease_expires_at = "queued", None, None
            else:
                self._finish(row, now)
            await session.commit()

    @staticmethod
    async def _now(session: AsyncSession) -> datetime:
        return await session.scalar(select(func.clock_timestamp()))

    @staticmethod
    def _valid_plan(run_id: uuid.UUID, plan: Any) -> bool:
        if not isinstance(plan, list):
            return False
        unfinished = False
        uncertain = False
        attempts = 0
        failed = False
        for index, item in enumerate(plan):
            if not isinstance(item, dict) or not isinstance(item.get("action"), dict):
                return False
            action = item["action"]
            state = item.get("state")
            if (not isinstance(action.get("id"), str) or not action["id"].strip()
                    or not isinstance(action.get("type"), str) or not action["type"].strip()
                    or not isinstance(state, str)
                    or type(item.get("index")) is not int or item["index"] != index
                    or state not in {"pending", "attempting", *ACTION_TERMINAL_STATES}):
                return False
            expected = str(uuid.uuid5(run_id, f"action:{index}:{action['id']}"))
            if item.get("operation_id") != expected or item.get("idempotency_key") != expected:
                return False
            if state in ACTION_TERMINAL_STATES and not isinstance(item.get("result"), dict):
                return False
            if state == "attempting":
                attempts += 1
                if attempts > 1 or unfinished or failed or not item.get("attempted_at"):
                    return False
                unfinished = True
            elif state == "pending":
                if failed:
                    return False
                unfinished = True
            elif unfinished:
                # Recovery may preemptively withhold later hardware while an
                # earlier independent notification is still pending. No later
                # successful/failed/unknown send may overtake pending work.
                if not (uncertain and state == "skipped"
                        and item["result"].get("reason") == "earlier_action_unknown"):
                    return False
            uncertain = uncertain or state == "unknown"
            failed = failed or state == "failed"
        return True

    @staticmethod
    def _next_pending(plan: list[dict[str, Any]], index: int) -> dict[str, Any]:
        if index < 0 or index >= len(plan) or plan[index]["state"] != "pending":
            raise AutomationClaimLost("Action was already attempted or is absent.")
        if any(item["state"] in {"pending", "attempting"} for item in plan[:index]):
            raise AutomationClaimLost("Earlier automation action is unfinished.")
        return plan[index]

    @classmethod
    def _checkpoint(cls, row: AutomationRun, index: int, result: dict[str, Any], *,
                    state: str, expected: str, now: datetime) -> None:
        if state not in ACTION_TERMINAL_STATES:
            raise ValueError("Invalid automation action outcome.")
        plan = copy.deepcopy(row.action_plan)
        if index < 0 or index >= len(plan) or plan[index]["state"] != expected:
            raise AutomationClaimLost("Action checkpoint changed.")
        plan[index].update(state=state, result=json.loads(json.dumps(result, allow_nan=False)), completed_at=now.isoformat())
        row.action_plan = plan
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        if state == "unknown":
            row.review_reason = row.review_reason or "action_outcome_unknown"
            cls._withhold_after_unknown(row, now)
        elif state == "failed":
            cls._skip_remaining(row, now, "earlier_action_failed")
        cls._results(row)

    @classmethod
    def _recover_attempts(cls, row: AutomationRun, now: datetime) -> None:
        plan = copy.deepcopy(row.action_plan)
        interrupted = False
        for item in plan:
            if item["state"] == "attempting":
                interrupted = True
                item.update(state="unknown", completed_at=now.isoformat(), result={
                    "id": item["action"]["id"], "type": item["action"]["type"],
                    "status": "unknown", "reason": "action_outcome_unknown", "requires_review": True,
                })
        row.action_plan = plan
        if interrupted or any(item["state"] == "unknown" for item in plan):
            row.review_reason = row.review_reason or "action_outcome_unknown"
            cls._withhold_after_unknown(row, now)
        cls._results(row)

    @classmethod
    def _withhold_after_unknown(cls, row: AutomationRun, now: datetime) -> None:
        plan = copy.deepcopy(row.action_plan)
        uncertain = False
        for item in plan:
            if item["state"] == "unknown":
                uncertain = True
            elif uncertain and item["state"] == "pending" and item["action"]["type"] not in NOTIFICATION_ACTION_TYPES:
                cls._skip(item, now, "earlier_action_unknown")
        row.action_plan = plan

    @classmethod
    def _skip_remaining(cls, row: AutomationRun, now: datetime, reason: str) -> None:
        plan = copy.deepcopy(row.action_plan)
        for item in plan:
            if item["state"] == "pending":
                cls._skip(item, now, reason)
        row.action_plan = plan

    @staticmethod
    def _skip(item: dict[str, Any], now: datetime, reason: str) -> None:
        item.update(state="skipped", completed_at=now.isoformat(), result={
            "id": item["action"]["id"], "type": item["action"]["type"],
            "status": "skipped", "reason": reason, "command_sent": False,
        })

    @staticmethod
    def _results(row: AutomationRun) -> None:
        row.action_results = [copy.deepcopy(item["result"]) for item in row.action_plan if "result" in item]

    @classmethod
    def _finish(cls, row: AutomationRun, now: datetime) -> None:
        states = {item["state"] for item in row.action_plan}
        if row.review_reason or "unknown" in states:
            cls._terminal(row, "review_required", now, row.review_reason or "action_outcome_unknown")
        elif "failed" in states:
            cls._terminal(row, "failed", now)
        elif "skipped" in states or not states:
            cls._terminal(row, "skipped", now)
        else:
            cls._terminal(row, "success", now)
        cls._results(row)

    @staticmethod
    def _terminal(row: AutomationRun, status: str, now: datetime, reason: str | None = None) -> None:
        row.status, row.finished_at = status, now
        row.claim_token = row.lease_expires_at = None
        if reason:
            row.review_reason, row.error = reason, reason
