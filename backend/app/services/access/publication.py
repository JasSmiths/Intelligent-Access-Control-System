"""Durable committed-event publication checkpoints and compact current projections.

Domain owners reserve/dirty these checkpoints in their own transaction. Nothing
here publishes, commits a participant session, or treats realtime as audit history.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import AccessCompletionWork, AccessEvent
from app.services.access.completion_database import completion_session
from app.services.access.event_reads import public_event_payload, public_event_query
from app.services.access.pipeline_contracts import PipelineLeaseLost
from app.services.access.pipeline_store import database_now

PUBLICATION_SCAN_SIZE = 32
PUBLICATION_LEASE_SECONDS = 120
PUBLICATION_RENEWAL_SECONDS = 10
PUBLICATION_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class PublicationClaim:
    work_id: uuid.UUID
    event_id: uuid.UUID
    token: uuid.UUID


@dataclass(frozen=True)
class PublicationMessage:
    revision: int
    event_type: str
    payload: dict[str, Any]


def _published_revision(work: AccessCompletionWork) -> int:
    value = work.result.get("published_revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PipelineLeaseLost("publication_checkpoint_unavailable")
    return value


def _check_claim(
    work: AccessCompletionWork | None, claim: PublicationClaim, now: datetime
) -> AccessCompletionWork:
    if (
        work is None
        or work.event_id != claim.event_id
        or work.stage != "publication"
        or work.state != "leased"
        or work.lease_token != claim.token
        or work.lease_until is None
        or work.lease_until <= now
    ):
        raise PipelineLeaseLost("publication_lease_lost")
    return work


async def _lock_claim(session: AsyncSession, claim: PublicationClaim) -> AccessCompletionWork:
    work = await session.get(
        AccessCompletionWork, claim.work_id, with_for_update=True, populate_existing=True
    )
    return _check_claim(work, claim, await database_now(session))


async def _reserve_checkpoint(session: AsyncSession, event_id: uuid.UUID, revision: int) -> None:
    now = await database_now(session)
    identity = uuid.uuid5(event_id, "access:completion:publication")
    await session.execute(
        insert(AccessCompletionWork)
        .values(
            id=identity,
            event_id=event_id,
            stage="publication",
            origin={"kind": "committed_event", "event_id": str(event_id)},
            origin_at=None,
            deadline=None,
            readiness="committed_state",
            state="queued",
            due_at=now,
            attempt_count=0,
            expected_revision=revision,
            result={"published_revision": 0},
        )
        .on_conflict_do_nothing(
            index_elements=[AccessCompletionWork.event_id, AccessCompletionWork.stage]
        )
    )
    work = await session.get(
        AccessCompletionWork, identity, with_for_update=True, populate_existing=True
    )
    if work is None or work.stage != "publication" or work.event_id != event_id:
        raise PipelineLeaseLost("publication_checkpoint_unavailable")
    published = _published_revision(work)
    if work.expected_revision is None or work.expected_revision < revision:
        work.expected_revision = revision
    if published < revision and work.state != "leased":
        work.state, work.due_at, work.completed_at, work.outcome = "queued", now, None, None
        work.lease_token, work.lease_until = None, None
    await session.flush()


async def reserve_initial_publication_in_session(session: AsyncSession, event_id: uuid.UUID) -> int:
    """Reserve current committed facts atomically with a new event's decision.

    Lock order is event -> its publication checkpoint, shared with all mutation
    owners. Legacy0 becomes1 only when an explicit current owner publishes it.
    """
    revision = await session.scalar(
        select(AccessEvent.resource_revision).where(AccessEvent.id == event_id).with_for_update()
    )
    if revision is None:
        raise ValueError("Publication requires a persisted access event")
    if revision == 0:
        revision = 1
        await session.execute(
            update(AccessEvent).where(AccessEvent.id == event_id).values(resource_revision=revision)
        )
    await _reserve_checkpoint(session, event_id, revision)
    return revision


async def mark_public_event_changed_in_session(session: AsyncSession, event_id: uuid.UUID) -> int:
    """The caller's public-field mutation, revision and dirty mark share commit."""
    revision = await session.scalar(
        update(AccessEvent)
        .where(AccessEvent.id == event_id)
        .values(resource_revision=AccessEvent.resource_revision + 1)
        .returning(AccessEvent.resource_revision)
    )
    if revision is None:
        raise ValueError("Publication requires a persisted access event")
    await _reserve_checkpoint(session, event_id, revision)
    return revision


def _text_json(path: Any, maximum: int) -> Any:
    return case((func.jsonb_typeof(path) == "string", func.left(path.astext, maximum)), else_=None)


def _bool_json(path: Any) -> Any:
    return case((func.jsonb_typeof(path) == "boolean", path.as_boolean()), else_=False)


async def _current_projection(session: AsyncSession, event_id: uuid.UUID) -> dict[str, Any]:
    raw = AccessEvent.raw_payload
    movement, gate = raw["movement_saga"], raw["movement_saga"]["gate"]
    query = public_event_query().add_columns(
        AccessEvent.person_id,
        AccessEvent.vehicle_id,
        _text_json(movement["id"], 36).label("movement_id"),
        _text_json(movement["admission_status"], 24).label("admission_status"),
        _text_json(movement["detail"], 500).label("movement_detail"),
        _text_json(gate["command_id"], 36).label("gate_command_id"),
        case(
            (func.jsonb_typeof(gate["accepted"]) == "boolean", gate["accepted"].as_boolean()),
            else_=None,
        ).label("gate_accepted"),
        _text_json(gate["state"], 64).label("gate_state"),
        _text_json(gate["detail"], 500).label("gate_detail"),
        _bool_json(gate["mechanically_confirmed"]).label("gate_mechanically_confirmed"),
        _bool_json(gate["requires_reconciliation"]).label("gate_requires_reconciliation"),
    ).where(AccessEvent.id == event_id)
    result = (await session.execute(query)).mappings().one_or_none()
    if result is None:
        raise PipelineLeaseLost("publication_event_unavailable")
    payload: dict[str, Any] = dict(public_event_payload(result))
    payload.update(
        event_id=payload.pop("id"),
        access_event_id=str(event_id),
        person_id=str(result["person_id"]) if result["person_id"] else None,
        vehicle_id=str(result["vehicle_id"]) if result["vehicle_id"] else None,
    )
    movement_payload = payload["movement_saga"]
    # Extras are transport presentation only. Never graft a raw summary from a
    # different journal, or attach gate authority to an ambiguous legacy link.
    if (movement_payload is not None and movement_payload["id"] is not None
            and movement_payload["id"] == result["movement_id"]):
        movement_payload.update(
            admission_status=result["admission_status"],
            detail=result["movement_detail"],
            gate={
                "command_id": result["gate_command_id"],
                "accepted": result["gate_accepted"],
                "state": result["gate_state"],
                "detail": result["gate_detail"],
                "mechanically_confirmed": result["gate_mechanically_confirmed"],
                "requires_reconciliation": result["gate_requires_reconciliation"],
            } if result["gate_command_id"] or result["gate_state"] else None,
        )
    return payload


class AccessPublicationStore:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    async def claim(self) -> PublicationClaim | None:
        async with completion_session(self.session_factory) as session:
            now = await database_now(session)
            identities = list(
                await session.scalars(
                    select(AccessCompletionWork.id)
                    .where(
                        AccessCompletionWork.stage == "publication",
                        AccessCompletionWork.due_at <= now,
                        or_(
                            AccessCompletionWork.state == "queued",
                            and_(
                                AccessCompletionWork.state == "leased",
                                AccessCompletionWork.lease_until <= now,
                            ),
                        ),
                    )
                    .order_by(AccessCompletionWork.due_at, AccessCompletionWork.id)
                    .limit(PUBLICATION_SCAN_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            for identity in identities:
                work = await session.get(AccessCompletionWork, identity)
                if work is None:
                    continue
                now = await database_now(session)
                try:
                    published = _published_revision(work)
                except PipelineLeaseLost:
                    work.state, work.outcome = "held", "publication_checkpoint_unavailable"
                    continue
                if work.expected_revision is None or work.expected_revision <= published:
                    work.state, work.completed_at = "succeeded", now
                    work.lease_token, work.lease_until = None, None
                    continue
                work.state, work.lease_token, work.lease_until = (
                    "leased",
                    uuid.uuid4(),
                    now + timedelta(seconds=PUBLICATION_LEASE_SECONDS),
                )
                work.attempt_count += 1
                claim = PublicationClaim(work.id, work.event_id, work.lease_token)
                await session.flush()
                _check_claim(work, claim, await database_now(session))
                await session.commit()
                return claim
            await session.commit()
            return None

    async def renew(self, claim: PublicationClaim) -> None:
        async with completion_session(self.session_factory) as session:
            work = await session.get(
                AccessCompletionWork, claim.work_id, with_for_update=True, populate_existing=True
            )
            now = await database_now(session)
            work = _check_claim(work, claim, now)
            original_until = work.lease_until
            work.lease_until = now + timedelta(seconds=PUBLICATION_LEASE_SECONDS)
            await session.flush()
            final_now = await database_now(session)
            if original_until is None or original_until <= final_now:
                raise PipelineLeaseLost("publication_lease_lost")
            await session.commit()

    async def assert_owned(self, claim: PublicationClaim) -> float:
        """Transport start is bounded by this lease, including session close."""
        async with completion_session(self.session_factory) as session:
            work = await session.get(
                AccessCompletionWork, claim.work_id, with_for_update=True, populate_existing=True
            )
            sampled = asyncio.get_running_loop().time()
            now = await database_now(session)
            checked = _check_claim(work, claim, now)
            if checked.lease_until is None:
                raise PipelineLeaseLost("publication_lease_lost")
            return sampled + (checked.lease_until - now).total_seconds()

    async def load_message(self, claim: PublicationClaim) -> PublicationMessage:
        async with completion_session(self.session_factory) as session:
            work = await _lock_claim(session, claim)
            published = _published_revision(work)
            payload = await _current_projection(session, claim.event_id)
            revision = payload["resource_revision"]
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise PipelineLeaseLost("publication_revision_unavailable")
            if work.expected_revision is None or revision < work.expected_revision:
                raise PipelineLeaseLost("publication_revision_changed")
            work.result = {"published_revision": published, "loaded_revision": revision}
            await session.flush()
            _check_claim(work, claim, await database_now(session))
            await session.commit()
        event_type = "access_event.finalized" if published == 0 else "access_event.updated"
        payload["event_type"] = event_type
        revision = payload["resource_revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise PipelineLeaseLost("publication_revision_unavailable")
        return PublicationMessage(revision, event_type, payload)

    async def acknowledge(self, claim: PublicationClaim, revision: int) -> bool:
        if isinstance(revision, bool) or revision < 1:
            raise ValueError("Publication acknowledgement requires a positive loaded revision")
        async with completion_session(self.session_factory) as session:
            work = await _lock_claim(session, claim)
            if (
                work.expected_revision is None
                or revision > work.expected_revision
                or work.result.get("loaded_revision") != revision
            ):
                raise PipelineLeaseLost("publication_revision_changed")
            published = max(_published_revision(work), revision)
            now = await database_now(session)
            work.result = {"published_revision": published}
            trailing = work.expected_revision > published
            work.state, work.due_at = "queued" if trailing else "succeeded", now
            work.completed_at = None if trailing else now
            work.outcome = "newer_revision_pending" if trailing else "published"
            original_until = work.lease_until
            work.lease_token, work.lease_until = None, None
            await session.flush()
            if original_until is None or original_until <= await database_now(session):
                raise PipelineLeaseLost("publication_lease_lost")
            await session.commit()
            return trailing

    async def retry(self, claim: PublicationClaim) -> None:
        async with completion_session(self.session_factory) as session:
            work = await _lock_claim(session, claim)
            work.state, work.due_at = "queued", await database_now(session) + timedelta(seconds=1)
            work.outcome = "transport_retry"
            original_until = work.lease_until
            work.lease_token, work.lease_until = None, None
            work.result = {"published_revision": _published_revision(work)}
            await session.flush()
            if original_until is None or original_until <= await database_now(session):
                raise PipelineLeaseLost("publication_lease_lost")
            await session.commit()
