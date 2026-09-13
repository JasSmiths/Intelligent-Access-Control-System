"""Requester-bound Alfred approvals with a committed, non-reclaimable claim.

This store owns approval transitions. Chat memory is not an execution ledger,
and transport disconnects, expired leases or repeated clicks cannot grant a
second attempt. Every method uses a fresh, short database transaction.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, tuple_, update

from app.db.session import AsyncSessionLocal
from app.models import AlfredApproval, ChatSession, User
from app.services.mutation_context import MutationError, load_active_admin


class ApprovalNotAllowed(ValueError):
    """The requester cannot create or use an executable approval."""


@dataclass(frozen=True)
class Approval:
    id: str
    operation_id: uuid.UUID
    session_id: uuid.UUID | None
    requester_user_id: uuid.UUID | None
    requester_auth_session_version: int
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None
    expires_at: datetime

    def pending_payload(self) -> dict[str, Any]:
        return {
            **deepcopy(self.payload),
            "id": self.id,
            "operation_id": str(self.operation_id),
            "session_id": str(self.session_id),
            "user_id": str(self.requester_user_id),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class ApprovalDecision:
    status: str
    approval: Approval | None = None


def _snapshot(row: AlfredApproval) -> Approval:
    return Approval(
        row.id, row.operation_id, row.session_id, row.requester_user_id, row.requester_auth_session_version,
        row.status, deepcopy(row.payload), deepcopy(row.result), row.expires_at,
    )


# A missing response remains non-reclaimable. After this bounded observation
# period the read API tells an operator to review it; a late definitive result
# may still finish the original claim. This is not an execution lease or retry.
CLAIM_REVIEW_AFTER = timedelta(minutes=10)


def approval_status(row: AlfredApproval, now: datetime) -> str:
    if row.status == "claimed":
        started = row.claimed_at or row.updated_at
        return "unknown" if started + CLAIM_REVIEW_AFTER <= now else "in_progress"
    if row.status == "pending" and row.expires_at <= now:
        return "expired"
    return row.status


class AlfredApprovalStore:
    async def _active_admin(self, session, requester_id: str | uuid.UUID | None, *, lock=False) -> User | None:
        try:
            return await load_active_admin(session, requester_id or "", lock=lock)
        except MutationError:
            return None

    async def create(
        self, session_id: uuid.UUID, requester_id: str | None, payload: dict[str, Any],
        *, operation_id: uuid.UUID | None = None,
    ) -> Approval:
        async with AsyncSessionLocal() as session:
            user = await self._active_admin(session, requester_id, lock=True)
            if not user:
                raise ApprovalNotAllowed("A current active Admin must request this action.")
            chat_session = await session.scalar(
                select(ChatSession).where(ChatSession.id == session_id).with_for_update()
            )
            if not chat_session:
                raise ApprovalNotAllowed("Ask Alfred to prepare this action in a current conversation.")
            now = await session.scalar(select(func.clock_timestamp()))
            # A new preview supersedes only this requester's previous button.
            # Claimed/completed operations remain durable and are never reset.
            await session.execute(
                update(AlfredApproval)
                .where(AlfredApproval.session_id == session_id,
                       AlfredApproval.requester_user_id == user.id,
                       AlfredApproval.status == "pending")
                .values(status="cancelled", finished_at=now, updated_at=now)
            )
            row = AlfredApproval(
                id=f"confirm-{uuid.uuid4().hex}", operation_id=operation_id or uuid.uuid4(),
                session_id=session_id, requester_user_id=user.id,
                requester_auth_session_version=user.auth_session_version,
                status="pending", payload=deepcopy(payload), result=None,
                created_at=now, updated_at=now, expires_at=now + timedelta(minutes=10),
            )
            session.add(row)
            await session.flush()
            result = _snapshot(row)
            await session.commit()
            return result

    async def pending(self, session_id: uuid.UUID, requester_id: str | None) -> Approval | None:
        async with AsyncSessionLocal() as session:
            user = await self._active_admin(session, requester_id)
            if not user:
                return None
            row = await session.scalar(
                select(AlfredApproval)
                .where(AlfredApproval.session_id == session_id,
                       AlfredApproval.requester_user_id == user.id,
                       AlfredApproval.requester_auth_session_version == user.auth_session_version,
                       AlfredApproval.status == "pending",
                       AlfredApproval.expires_at > func.clock_timestamp())
                .order_by(AlfredApproval.created_at.desc())
                .limit(1)
            )
            return _snapshot(row) if row else None

    async def decide(
        self, session_id: uuid.UUID, confirmation_id: str, requester_id: str | None,
        *, confirm: bool,
    ) -> ApprovalDecision:
        async with AsyncSessionLocal() as session:
            # Consistent lock order with create: user before approval. Role and
            # auth-version changes cannot commit between validation and claim.
            user = await self._active_admin(session, requester_id, lock=True)
            if not user:
                return ApprovalDecision("unavailable")
            row = await session.scalar(
                select(AlfredApproval)
                .where(AlfredApproval.id == confirmation_id,
                       AlfredApproval.session_id == session_id,
                       AlfredApproval.requester_user_id == user.id)
                .with_for_update()
            )
            if not row:
                return ApprovalDecision("unavailable")
            now = await session.scalar(select(func.clock_timestamp()))
            if row.requester_auth_session_version != user.auth_session_version:
                if row.status == "pending":
                    row.status, row.finished_at, row.updated_at = "expired", now, now
                    await session.commit()
                return ApprovalDecision("unavailable")
            if row.status != "pending":
                return ApprovalDecision(approval_status(row, now), _snapshot(row))
            if row.expires_at <= now:
                row.status, row.finished_at, row.updated_at = "expired", now, now
            elif not confirm:
                row.status, row.finished_at, row.updated_at = "cancelled", now, now
            else:
                row.status, row.claimed_at, row.updated_at = "claimed", now, now
            snapshot = _snapshot(row)
            await session.commit()
            return ApprovalDecision(snapshot.status, snapshot)

    async def inspect(
        self, session_id: uuid.UUID, confirmation_id: str, requester_id: str | None,
    ) -> ApprovalDecision:
        """Read recovery state without claiming, expiring or otherwise mutating it."""
        async with AsyncSessionLocal() as session:
            user = await self._active_admin(session, requester_id)
            if not user:
                return ApprovalDecision("unavailable")
            row = await session.scalar(
                select(AlfredApproval)
                .where(AlfredApproval.id == confirmation_id,
                       AlfredApproval.session_id == session_id,
                       AlfredApproval.requester_user_id == user.id,
                       AlfredApproval.requester_auth_session_version == user.auth_session_version)
            )
            if not row:
                return ApprovalDecision("unavailable")
            now = await session.scalar(select(func.clock_timestamp()))
            return ApprovalDecision(approval_status(row, now), _snapshot(row))

    async def list_for_requester(
        self, requester_id: str | None, *, session_id: uuid.UUID | None = None,
        before_id: str | None = None, limit: int = 25,
    ) -> dict[str, Any]:
        """Discover retained identities after browser storage is lost; no claims."""
        if not 1 <= limit <= 100:
            raise ValueError("Approval page size must be between 1 and 100.")
        async with AsyncSessionLocal() as session:
            user = await self._active_admin(session, requester_id)
            if not user:
                return {"items": [], "next_cursor": None}
            query = select(AlfredApproval).where(
                AlfredApproval.requester_user_id == user.id,
                AlfredApproval.requester_auth_session_version == user.auth_session_version)
            if session_id is not None:
                query = query.where(AlfredApproval.session_id == session_id)
            if before_id:
                cursor = await session.scalar(query.where(AlfredApproval.id == before_id))
                if cursor is None:
                    raise ValueError("Approval page cursor is unavailable.")
                query = query.where(tuple_(AlfredApproval.created_at, AlfredApproval.id) <
                                    tuple_(cursor.created_at, cursor.id))
            rows = list((await session.scalars(query.order_by(AlfredApproval.created_at.desc(), AlfredApproval.id.desc())
                                               .limit(limit + 1))).all())
            now = await session.scalar(select(func.clock_timestamp()))
            def reference(row):
                state = approval_status(row, now)
                return {"confirmation_id": row.id, "operation_id": str(row.operation_id),
                        "session_id": str(row.session_id) if row.session_id else None,
                        "status": state, "created_at": row.created_at.isoformat(),
                        "expires_at": row.expires_at.isoformat()}
            return {"items": [reference(row) for row in rows[:limit]],
                    "next_cursor": rows[limit - 1].id if len(rows) > limit else None}

    async def finish(self, approval: Approval, result: dict[str, Any], *, unknown=False) -> bool:
        async with AsyncSessionLocal() as session:
            changed = await session.execute(
                update(AlfredApproval)
                .where(AlfredApproval.id == approval.id,
                       AlfredApproval.operation_id == approval.operation_id,
                       AlfredApproval.status == "claimed")
                .values(status="unknown" if unknown else "completed", result=deepcopy(result),
                        finished_at=func.clock_timestamp(), updated_at=func.clock_timestamp())
            )
            await session.commit()
            return changed.rowcount == 1

    async def record_turn(self, approval: Approval, turn: dict[str, Any]) -> None:
        """Attach presentation after the definitive tool result is already safe."""
        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(AlfredApproval)
                .where(AlfredApproval.id == approval.id,
                       AlfredApproval.operation_id == approval.operation_id,
                       AlfredApproval.status.in_(["completed", "unknown"]))
                .with_for_update()
            )
            if row:
                row.result = {**(row.result or {}), "turn": deepcopy(turn)}
                row.updated_at = await session.scalar(select(func.clock_timestamp()))
                await session.commit()


alfred_approval_store = AlfredApprovalStore()
