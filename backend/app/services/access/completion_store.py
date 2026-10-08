"""Optional completion reservations and fenced short database transactions.

Domain owners reserve these rows in their event transaction. Provider results are
applied by explicit stage owners under domain -> checkpoint lock order; this
repository has no provider port, dispatcher, or participant commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import (
    AccessCompletionWork,
    AccessEvent,
    LprDebounceWindow,
    LprIngestEvent,
    LprWindowMember,
)
from app.services.access.completion_contracts import (
    COMPLETION_STAGES,
    CompletionStage,
    ExternalCompletionOrigin,
    RetainedResultReference,
    VisitorCompletionTarget,
)
from app.services.access.pipeline_contracts import CompletionClaim, PipelineLeaseLost
from app.services.access.pipeline_store import database_now

COMPLETION_SCAN_SIZE = 32
COMPLETION_ACTIVE_SLOTS = 2
COMPLETION_LEASE_SECONDS = 120
COMPLETION_RENEWAL_SECONDS = 10
COMPLETION_TIMEOUT_SECONDS = 90
COMPLETION_LINK_TIMEOUT_SECONDS = 5
OPTIONAL_SECONDS = 300


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None


def _valid_origin(work: AccessCompletionWork) -> bool:
    origin = work.origin
    if (
        not isinstance(origin, dict)
        or type(origin.get("version")) is not int
        or origin.get("version") != 1
        or not _aware(work.origin_at)
        or not _aware(work.deadline)
    ):
        return False
    if work.origin_at is None or work.deadline != work.origin_at + timedelta(
        seconds=OPTIONAL_SECONDS
    ):
        return False
    if (
        origin.get("event_id") != str(work.event_id)
        or not isinstance(origin.get("registration_number"), str)
        or not origin["registration_number"]
    ):
        return False
    common = {"version", "kind", "event_id", "registration_number"}
    if origin.get("kind") == "window":
        if (
            set(origin) != common | {"window_id", "ingest_id"}
            or work.window_id is None
            or origin.get("window_id") != str(work.window_id)
        ):
            return False
        identity = origin.get("ingest_id")
    elif origin.get("kind") == "external_gate_observation":
        if set(origin) != common | {"observation_id"} or work.window_id is not None:
            return False
        identity = origin.get("observation_id")
    else:
        return False
    try:
        return isinstance(identity, str) and str(uuid.UUID(identity)) == identity
    except ValueError:
        return False


def _origin_identity(work: AccessCompletionWork) -> tuple[str, ...]:
    # Exact bounded identity fields, not a digest used as sole identity authority.
    origin = work.origin
    names = (
        ("kind", "event_id", "registration_number", "window_id", "ingest_id")
        if origin["kind"] == "window"
        else ("kind", "event_id", "registration_number", "observation_id")
    )
    return tuple(cast(str, origin[name]) for name in names)


def _link_references(work: AccessCompletionWork) -> bool:
    """Only accepted results may make a link runnable; no fresh provider I/O."""
    requested = work.result.get("requested") if isinstance(work.result, dict) else None
    target = work.result.get("visitor_id") if isinstance(work.result, dict) else None
    if (
        not isinstance(target, str)
        or not isinstance(requested, dict)
        or not requested
        or set(requested) - {"visual", "information"}
    ):
        return False
    try:
        if str(uuid.UUID(target)) != target:
            return False
    except ValueError:
        return False
    for kind, value in requested.items():
        if not isinstance(value, dict) or set(value) != {"result_id", "revision", "accepted_at"}:
            return False
        if not isinstance(value.get("result_id"), str) or not isinstance(
            value.get("accepted_at"), str
        ):
            return False
        try:
            reference = RetainedResultReference(
                cast(Literal["visual", "information"], kind),
                uuid.UUID(value["result_id"]),
                value["revision"],
                datetime.fromisoformat(value["accepted_at"]),
            )
            if (
                work.origin_at is None
                or work.deadline is None
                or not work.origin_at <= reference.accepted_at <= work.deadline
            ):
                return False
        except (ValueError, TypeError, KeyError):
            return False
    return True


def _check_claim(
    work: AccessCompletionWork | None,
    claim: CompletionClaim,
    now: datetime,
    *,
    finished: bool = False,
) -> AccessCompletionWork:
    states = {"succeeded", "skipped", "held"} if finished else {"leased"}
    if (
        work is None
        or work.id != claim.work_id
        or work.event_id != claim.event_id
        or work.stage != claim.stage
        or work.state not in states
        or work.lease_token != claim.token
        or work.lease_until is None
        or work.lease_until <= now
    ):
        raise PipelineLeaseLost("completion_lease_lost")
    if (
        not _valid_origin(work)
        or work.origin_at != claim.origin_at
        or work.deadline != claim.deadline
        or _origin_identity(work) != claim.origin_identity
    ):
        raise PipelineLeaseLost("completion_origin_changed")
    if work.expected_revision != claim.expected_revision:
        raise PipelineLeaseLost("completion_revision_changed")
    if claim.stage != "visitor_link" and work.readiness != "committed_decision":
        raise PipelineLeaseLost("completion_readiness_unavailable")
    if claim.stage != "visitor_link" and (work.deadline is None or work.deadline <= now):
        raise PipelineLeaseLost("completion_deadline_expired")
    if claim.stage == "visitor_link" and (
        work.readiness != "retained_result_link" or not _link_references(work)
    ):
        raise PipelineLeaseLost("completion_link_unavailable")
    return work


async def lock_completion_event_in_session(
    session: AsyncSession, claim: CompletionClaim
) -> AccessEvent:
    """Acquire origin first; stage owner then takes its domain locks before work."""
    event = await session.get(
        AccessEvent, claim.event_id, with_for_update=True, populate_existing=True
    )
    if event is None:
        raise PipelineLeaseLost("completion_event_unavailable")
    return event


async def assert_completion_in_session(
    session: AsyncSession, claim: CompletionClaim, *, finished: bool = False
) -> AccessCompletionWork:
    """Caller already owns event/domain rows. Recheck after every blocking write."""
    work = await session.get(
        AccessCompletionWork, claim.work_id, with_for_update=True, populate_existing=True
    )
    event_plate = await session.scalar(
        select(AccessEvent.registration_number).where(AccessEvent.id == claim.event_id)
    )
    checked = _check_claim(work, claim, await database_now(session), finished=finished)
    if event_plate != checked.origin.get("registration_number"):
        raise PipelineLeaseLost("completion_registration_changed")
    return checked


async def assert_completion_before_commit(
    session: AsyncSession, claim: CompletionClaim, *, finished: bool = False
) -> None:
    """Flush every domain/audit/result write before the final fresh clock sample."""
    await session.flush()
    await assert_completion_in_session(session, claim, finished=finished)


async def _reserve(
    session: AsyncSession,
    event: AccessEvent,
    *,
    origin: dict[str, Any],
    origin_at: datetime | None,
    window_id: uuid.UUID | None,
    visitor_target: VisitorCompletionTarget | None = None,
) -> None:
    now = await database_now(session)
    visitor = (event.raw_payload or {}).get("visitor_pass")
    visitor_id = visitor.get("id") if isinstance(visitor, dict) else None
    visitor_mode = visitor.get("mode") if isinstance(visitor, dict) else None
    for stage in COMPLETION_STAGES:
        valid = origin_at is not None
        waiting = (
            stage == "visitor_link" and visitor_mode == "arrival" and isinstance(visitor_id, str)
        )
        state = "queued" if valid else "held"
        outcome = None if valid else "original_origin_unavailable"
        readiness = "committed_decision"
        result: dict[str, Any] = {}
        if stage == "visitor_link" and valid:
            state = "waiting" if waiting else "skipped"
            readiness = "canonical_arrival_and_result" if waiting else "no_arrival_target"
            outcome = None if waiting else "no_arrival_target"
            result = {"visitor_id": visitor_id, "requested": {}, "applied": {}} if waiting else {}
            if waiting and visitor_target is not None:
                if str(visitor_target.visitor_id) != visitor_id:
                    raise ValueError("Frozen visitor target differs from event annotation")
                result["fields"] = {
                    "make": {"state": "never_owned", "baseline": visitor_target.make},
                    "colour": {"state": "never_owned", "baseline": visitor_target.colour},
                }
        await session.execute(
            insert(AccessCompletionWork)
            .values(
                id=uuid.uuid5(event.id, "access:completion:" + stage),
                event_id=event.id,
                window_id=window_id,
                stage=stage,
                origin=origin,
                origin_at=origin_at,
                deadline=origin_at + timedelta(seconds=OPTIONAL_SECONDS)
                if origin_at is not None
                else None,
                readiness=readiness,
                state=state,
                due_at=now,
                attempt_count=0,
                expected_revision=1,
                outcome=outcome,
                result=result,
                completed_at=now if state in {"held", "skipped"} else None,
            )
            .on_conflict_do_nothing(
                index_elements=[AccessCompletionWork.event_id, AccessCompletionWork.stage]
            )
        )
    await session.flush()


async def reserve_window_completion_in_session(
    session: AsyncSession,
    *,
    event_id: uuid.UUID,
    window_id: uuid.UUID,
    ingest_id: uuid.UUID,
    visitor_target: VisitorCompletionTarget | None = None,
) -> None:
    event = await session.get(AccessEvent, event_id, with_for_update=True, populate_existing=True)
    if event is None:
        raise ValueError("Completion requires a persisted access event")
    # The serial finalization owner already owns its window. Read only its immutable
    # receipt and exact chosen member; no event -> window blocking lock inversion.
    window = (
        await session.execute(
            select(
                LprDebounceWindow.id,
                LprDebounceWindow.min_received_at,
                LprDebounceWindow.state,
                LprDebounceWindow.access_event_id,
            ).where(LprDebounceWindow.id == window_id)
        )
    ).one_or_none()
    member = (
        await session.execute(
            select(
                LprWindowMember.ingest_id,
                LprWindowMember.prepared_context["registration_number"].astext.label(
                    "registration_number"
                ),
            ).where(LprWindowMember.window_id == window_id, LprWindowMember.ingest_id == ingest_id)
        )
    ).one_or_none()
    valid = (
        window is not None
        and member is not None
        and _aware(window.min_received_at)
        and member.registration_number == event.registration_number
        and window.state in {"finalizing", "completed"}
        and (
            window.access_event_id is None
            if window.state == "finalizing"
            else window.access_event_id == event_id
        )
    )
    origin = {
        "version": 1,
        "kind": "window" if valid else "unavailable",
        "event_id": str(event_id),
        "window_id": str(window_id),
        "ingest_id": str(ingest_id),
        "registration_number": event.registration_number,
    }
    await _reserve(
        session,
        event,
        origin=origin,
        origin_at=window.min_received_at if valid and window is not None else None,
        window_id=window_id if window is not None else None,
        visitor_target=visitor_target,
    )


async def reserve_external_completion_in_session(
    session: AsyncSession,
    *,
    event_id: uuid.UUID,
    origin: ExternalCompletionOrigin,
    visitor_target: VisitorCompletionTarget | None = None,
) -> None:
    event = await session.get(AccessEvent, event_id, with_for_update=True, populate_existing=True)
    if event is None:
        raise ValueError("Completion requires a persisted access event")
    linked_ingest = await session.scalar(
        select(LprIngestEvent.id).where(LprIngestEvent.access_event_id == event_id).limit(1)
    )
    if linked_ingest is not None:
        await _reserve(
            session,
            event,
            origin={
                "version": 1,
                "kind": "unavailable",
                "event_id": str(event_id),
                "registration_number": event.registration_number,
            },
            origin_at=None,
            window_id=None,
        )
        return
    await _reserve(
        session,
        event,
        origin={
            "version": 1,
            "kind": "external_gate_observation",
            "event_id": str(event_id),
            "observation_id": str(origin.observation_id),
            "registration_number": event.registration_number,
        },
        origin_at=origin.observed_at,
        window_id=None,
        visitor_target=visitor_target,
    )


class AccessCompletionStore:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    async def claim(self, available_slots: int) -> list[CompletionClaim]:
        if (
            isinstance(available_slots, bool)
            or not isinstance(available_slots, int)
            or not 0 <= available_slots <= COMPLETION_ACTIVE_SLOTS
        ):
            raise ValueError("Completion leases only its currently free two slots")
        if available_slots == 0:
            return []
        while True:
            async with self.session_factory() as session:
                now = await database_now(session)
                ids = list(
                    await session.scalars(
                        select(AccessCompletionWork.id)
                        .where(
                            AccessCompletionWork.stage.in_(COMPLETION_STAGES),
                            AccessCompletionWork.due_at <= now,
                            or_(
                                AccessCompletionWork.state == "queued",
                                and_(
                                    AccessCompletionWork.state == "leased",
                                    AccessCompletionWork.lease_until <= now,
                                ),
                                and_(
                                    AccessCompletionWork.stage != "visitor_link",
                                    AccessCompletionWork.state == "waiting",
                                    AccessCompletionWork.deadline <= now,
                                ),
                            ),
                        )
                        .order_by(AccessCompletionWork.due_at, AccessCompletionWork.id)
                        .limit(COMPLETION_SCAN_SIZE)
                        .with_for_update(skip_locked=True)
                    )
                )
                claims: list[CompletionClaim] = []
                for identity in ids:
                    work = await session.get(AccessCompletionWork, identity)
                    if work is None:
                        continue
                    now = await database_now(session)
                    if not _valid_origin(work):
                        work.state, work.outcome, work.completed_at = (
                            "held",
                            "original_origin_unavailable",
                            now,
                        )
                        work.lease_token, work.lease_until = None, None
                        continue
                    if (
                        work.stage != "visitor_link"
                        and work.deadline is not None
                        and work.deadline <= now
                    ):
                        work.state, work.outcome, work.completed_at = (
                            "expired",
                            "original_deadline_expired",
                            now,
                        )
                        work.lease_token, work.lease_until = None, None
                        continue
                    if work.stage != "visitor_link" and work.readiness != "committed_decision":
                        work.state, work.outcome, work.completed_at = (
                            "held",
                            "completion_readiness_unavailable",
                            now,
                        )
                        work.lease_token, work.lease_until = None, None
                        continue
                    if work.stage == "visitor_link" and (
                        work.readiness != "retained_result_link" or not _link_references(work)
                    ):
                        work.state, work.outcome, work.completed_at = (
                            "held",
                            "retained_result_unavailable",
                            now,
                        )
                        work.lease_token, work.lease_until = None, None
                        continue
                    if (
                        work.stage not in COMPLETION_STAGES
                        or isinstance(work.expected_revision, bool)
                        or not isinstance(work.expected_revision, int)
                        or work.expected_revision < 1
                    ):
                        work.state, work.outcome, work.completed_at = (
                            "held",
                            "completion_revision_unavailable",
                            now,
                        )
                        continue
                    work.state, work.lease_token, work.lease_until = (
                        "leased",
                        uuid.uuid4(),
                        now + timedelta(seconds=COMPLETION_LEASE_SECONDS),
                    )
                    work.attempt_count += 1
                    claims.append(
                        CompletionClaim(
                            work.id,
                            work.event_id,
                            cast(CompletionStage, work.stage),
                            work.lease_token,
                            work.origin_at,
                            work.deadline,
                            work.expected_revision,
                            _origin_identity(work),
                        )
                    )
                    if len(claims) == available_slots:
                        break
                await session.flush()
                final_now = await database_now(session)
                for claim in claims:
                    work = await session.get(AccessCompletionWork, claim.work_id)
                    _check_claim(work, claim, final_now)
                await session.commit()
                if claims or len(ids) < COMPLETION_SCAN_SIZE:
                    return claims
                # A full expired/held prefix is terminalized in a short transaction;
                # continue to real ready work without retaining a large lock set.

    async def renew(self, claim: CompletionClaim) -> None:
        async with self.session_factory() as session:
            work = await session.get(
                AccessCompletionWork, claim.work_id, with_for_update=True, populate_existing=True
            )
            now = await database_now(session)
            work = _check_claim(work, claim, now)
            previous_until = work.lease_until
            work.lease_until = now + timedelta(seconds=COMPLETION_LEASE_SECONDS)
            await session.flush()
            final_now = await database_now(session)
            if previous_until is None or previous_until <= final_now:
                raise PipelineLeaseLost("completion_lease_lost")
            _check_claim(work, claim, final_now)
            await session.commit()

    async def assert_owned(self, claim: CompletionClaim) -> float:
        """Returns remaining operation budget; no open connection survives I/O."""
        async with self.session_factory() as session:
            work = await session.get(
                AccessCompletionWork, claim.work_id, with_for_update=True, populate_existing=True
            )
            now = await database_now(session)
            _check_claim(work, claim, now)
            if claim.stage == "visitor_link":
                return float(COMPLETION_LINK_TIMEOUT_SECONDS)
            if claim.deadline is None:
                raise PipelineLeaseLost("completion_origin_changed")
            return min(float(COMPLETION_TIMEOUT_SECONDS), (claim.deadline - now).total_seconds())

    async def retry(self, claim: CompletionClaim, *, delay_seconds: int = 1) -> None:
        if (
            isinstance(delay_seconds, bool)
            or not isinstance(delay_seconds, int)
            or not 0 <= delay_seconds <= COMPLETION_TIMEOUT_SECONDS
        ):
            raise ValueError("Completion retry delay must stay bounded")
        async with self.session_factory() as session:
            work = await session.get(
                AccessCompletionWork, claim.work_id, with_for_update=True, populate_existing=True
            )
            now = await database_now(session)
            work = _check_claim(work, claim, now)
            old_until = work.lease_until
            due_at = now + timedelta(seconds=delay_seconds)
            if claim.stage != "visitor_link" and claim.deadline is not None:
                due_at = min(due_at, claim.deadline)
            work.state, work.due_at, work.outcome = "queued", due_at, "retry_pending"
            work.lease_token, work.lease_until = None, None
            await session.flush()
            final_now = await database_now(session)
            if (
                old_until is None
                or old_until <= final_now
                or (
                    claim.stage != "visitor_link"
                    and (claim.deadline is None or claim.deadline <= final_now)
                )
            ):
                raise PipelineLeaseLost("completion_lease_lost")
            await session.commit()


async def finish_completion_in_session(
    session: AsyncSession,
    claim: CompletionClaim,
    *,
    state: Literal["succeeded", "skipped", "held"],
    outcome: str,
) -> AccessCompletionWork:
    """Stage-owned domain/result/audit mutations and this receipt share caller commit.

    Retain the terminal token/lease as final-write fencing evidence. It cannot be
    reclaimed/renewed because state is terminal. Caller must recheck with
    assert_completion_before_commit(finished=True) after all later writes.
    """
    if not outcome or len(outcome) > 120:
        raise ValueError("Completion outcome requires a bounded code")
    work = await assert_completion_in_session(session, claim)
    work.state, work.outcome, work.completed_at = state, outcome, await database_now(session)
    await assert_completion_before_commit(session, claim, finished=True)
    return work
