"""Bounded durable debounce grouping, ordered evidence and visitor markers.

No provider I/O or implicit commits. A compact preview never supplies authority;
complete membership/tokens remain indexed database relations.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import LprDebounceWindow, LprIngestEvent, LprWindowMember
from app.modules.lpr.base import PlateRead
from app.services.access.observation_tokens import record_tokens_in_session
from app.services.access.pipeline_contracts import (
    EvidenceReference,
    IngestClaim,
    PipelineLeaseLost,
    WindowClaim,
    WindowSummary,
)
from app.services.access.pipeline_store import (
    COORDINATOR_LEASE_SECONDS,
    assert_coordinator_in_session,
    assert_ingest_in_session,
    assert_window_in_session,
    database_now,
)
from app.services.access.read_codec import classification_context, read_from_member
from app.services.access.reads import (
    _external_admission_from_read,
    _is_exact_known_vehicle_plate_match,
    _is_visitor_pass_plate_match,
    lpr_ingest_id_from_read,
)
from app.services.access.window_candidates import WindowCandidate, candidate_page_in_session
from app.services.movement.sessions import (
    datetime_from_payload,
    event_ids_from_paths,
    ocr_variants_for_reads,
    payload_values,
    plates_are_similar,
)

WINDOW_PAGE_SIZE = 50
WINDOW_PREVIEW_SIZE = 50
ReadRank = tuple[int, int, int, float, datetime]


@dataclass(frozen=True)
class StoredWindow:
    summary: WindowSummary
    first_read: PlateRead
    best_read: PlateRead
    anchor_read: PlateRead | None
    preview: tuple[WindowCandidate, ...]


def read_rank(read: PlateRead) -> ReadRank:
    return (
        int(_is_exact_known_vehicle_plate_match(read)),
        int(_is_visitor_pass_plate_match(read)),
        int(bool(_external_admission_from_read(read))),
        read.confidence,
        read.captured_at,
    )


def _rank_payload(rank: ReadRank) -> list[Any]:
    return [*rank[:4], rank[4].isoformat()]


def _rank_from_payload(value: Any) -> ReadRank:
    if not isinstance(value, list) or len(value) != 5:
        raise PipelineLeaseLost("window_rank_unavailable")
    captured_at = datetime_from_payload(value[4])
    if captured_at is None:
        raise PipelineLeaseLost("window_rank_unavailable")
    return int(value[0]), int(value[1]), int(value[2]), float(value[3]), captured_at


def _context_time(context: dict[str, Any], key: str) -> datetime:
    value = datetime_from_payload(context.get(key))
    if value is None:
        raise PipelineLeaseLost("window_time_unavailable")
    return value


def _summary(row: LprDebounceWindow) -> WindowSummary:
    if row.first_ingest_id is None or row.best_ingest_id is None:
        raise PipelineLeaseLost("window_evidence_unavailable")
    return WindowSummary(
        EvidenceReference(row.id),
        row.source,
        row.open_ordinal,
        row.member_count,
        row.first_seen,
        row.updated_at,
        row.min_received_at,
        row.first_ingest_id,
        row.best_ingest_id,
        row.anchor_ingest_id,
    )


async def _record_tokens(session: AsyncSession, ingest_id: uuid.UUID, read: PlateRead) -> None:
    await record_tokens_in_session(
        session,
        ingest_id,
        ocr=ocr_variants_for_reads((read,)),
        protect_event=sorted(
            set(payload_values(read.raw_payload, ("eventId", "event_id")))
            | event_ids_from_paths(read.raw_payload)
        ),
    )


async def _assert_preparing_read(
    session: AsyncSession,
    claim: IngestClaim,
    read: PlateRead | None = None,
) -> LprIngestEvent:
    ingest = await assert_ingest_in_session(session, claim)
    coordinator = await assert_coordinator_in_session(session, claim.coordinator)
    if (
        coordinator.boundary_ingest_id != claim.ingest_id
        or coordinator.boundary_phase != "preparing"
    ):
        raise PipelineLeaseLost("read_boundary_changed")
    if read is not None and (
        lpr_ingest_id_from_read(read) != ingest.id
        or read.source != ingest.source
        or read.captured_at != ingest.captured_at
    ):
        raise PipelineLeaseLost("classified_ingest_identity_changed")
    return ingest


class AccessWindowStore:
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self.session_factory = session_factory

    async def append_in_session(
        self,
        session: AsyncSession,
        claim: IngestClaim,
        read: PlateRead,
        *,
        threshold: float,
    ) -> WindowSummary:
        ingest = await _assert_preparing_read(session, claim, read)
        context = classification_context(read)
        existing = await session.get(LprWindowMember, ingest.id)
        if existing is not None:
            if existing.prepared_context != context:
                raise PipelineLeaseLost("member_classification_changed")
            window = await session.get(LprDebounceWindow, existing.window_id)
            if window is None:
                raise PipelineLeaseLost("member_window_unavailable")
            await _assert_preparing_read(session, claim)
            return _summary(window)
        # First matching open window in complete pending order. No time filter:
        # historical grouping happens before the subsequent flush boundary.
        after = 0
        window = None
        while True:
            page = list(
                await session.scalars(
                    select(LprDebounceWindow)
                    .where(
                        LprDebounceWindow.state == "open",
                        LprDebounceWindow.source == read.source,
                        LprDebounceWindow.open_ordinal > after,
                    )
                    .order_by(LprDebounceWindow.open_ordinal)
                    .limit(WINDOW_PAGE_SIZE)
                )
            )
            for candidate in page:
                if plates_are_similar(
                    read.registration_number,
                    str(candidate.ranking_context["best_registration"]),
                    threshold,
                ):
                    window = candidate
                    break
            if window is not None or len(page) < WINDOW_PAGE_SIZE:
                break
            after = page[-1].open_ordinal
        rank = read_rank(read)
        if window is None:
            coordinator = await assert_coordinator_in_session(session, claim.coordinator)
            window = LprDebounceWindow(
                id=uuid.uuid4(),
                open_ordinal=coordinator.next_open_ordinal,
                source=read.source,
                state="open",
                first_seen=read.captured_at,
                updated_at=read.captured_at,
                min_received_at=ingest.received_at,
                member_count=0,
                ranking_context={},
            )
            coordinator.next_open_ordinal += 1
            session.add(window)
            await session.flush()
        ordinal = window.member_count
        previous = dict(window.ranking_context)
        first_time = _context_time(previous, "first_captured_at") if previous else None
        maximum = _context_time(previous, "max_captured_at") if previous else read.captured_at
        if first_time is None or read.captured_at < first_time:
            window.first_ingest_id, window.first_ordinal = ingest.id, ordinal
            previous["first_captured_at"] = read.captured_at.isoformat()
        if not previous.get("best_rank") or rank > _rank_from_payload(previous["best_rank"]):
            window.best_ingest_id, window.best_ordinal = ingest.id, ordinal
            previous["best_rank"], previous["best_registration"] = (
                _rank_payload(rank),
                read.registration_number,
            )
        previous["max_captured_at"] = max(maximum, read.captured_at).isoformat()
        window.ranking_context = previous
        window.updated_at = read.captured_at  # last appended, including regressed time
        window.min_received_at = min(window.min_received_at, ingest.received_at)
        window.member_count += 1
        ingest.window_id = window.id
        session.add(
            LprWindowMember(
                ingest_id=ingest.id, window_id=window.id, ordinal=ordinal, prepared_context=context
            )
        )
        await _record_tokens(session, ingest.id, read)
        await session.flush()
        await _assert_preparing_read(session, claim)
        return _summary(window)

    async def merge_related_in_session(
        self,
        session: AsyncSession,
        claim: IngestClaim,
        selected_id: uuid.UUID,
        *,
        anchor_read: PlateRead,
        max_seconds: float,
    ) -> WindowSummary:
        await _assert_preparing_read(session, claim, anchor_read)
        selected = await session.get(LprDebounceWindow, selected_id, with_for_update=True)
        if selected is None or selected.state != "open" or selected.source != anchor_read.source:
            raise PipelineLeaseLost("selected_window_unavailable")
        anchor_id = lpr_ingest_id_from_read(anchor_read)
        anchor_member = (
            await session.get(LprWindowMember, anchor_id) if anchor_id is not None else None
        )
        if anchor_member is None or anchor_member.window_id != selected.id:
            raise PipelineLeaseLost("anchor_membership_unavailable")
        selected.anchor_ingest_id, selected.anchor_ordinal = anchor_id, anchor_member.ordinal
        after = 0
        merged = False
        context = dict(selected.ranking_context)
        maximum = _context_time(context, "max_captured_at")
        while True:
            page = list(
                await session.scalars(
                    select(LprDebounceWindow)
                    .where(
                        LprDebounceWindow.state == "open",
                        LprDebounceWindow.source == anchor_read.source,
                        LprDebounceWindow.id != selected.id,
                        LprDebounceWindow.open_ordinal > after,
                        LprDebounceWindow.first_seen <= anchor_read.captured_at,
                        LprDebounceWindow.first_seen
                        >= anchor_read.captured_at - timedelta(seconds=max_seconds),
                    )
                    .order_by(LprDebounceWindow.open_ordinal)
                    .limit(WINDOW_PAGE_SIZE)
                    .with_for_update()
                )
            )
            if not page:
                break
            await _assert_preparing_read(session, claim)
            offsets: dict[uuid.UUID, int] = {}
            for related in page:
                merged = True
                offset = selected.member_count
                offsets[related.id] = offset
                other = related.ranking_context
                if _context_time(other, "first_captured_at") < _context_time(
                    context, "first_captured_at"
                ):
                    selected.first_ingest_id = related.first_ingest_id
                    selected.first_ordinal = offset + int(related.first_ordinal or 0)
                    context["first_captured_at"] = other["first_captured_at"]
                if _rank_from_payload(other["best_rank"]) > _rank_from_payload(
                    context["best_rank"]
                ):
                    selected.best_ingest_id = related.best_ingest_id
                    selected.best_ordinal = offset + int(related.best_ordinal or 0)
                    context["best_rank"], context["best_registration"] = (
                        other["best_rank"],
                        other["best_registration"],
                    )
                maximum = max(maximum, _context_time(other, "max_captured_at"))
                selected.first_seen = min(
                    selected.first_seen, _context_time(other, "first_captured_at")
                )
                selected.min_received_at = min(selected.min_received_at, related.min_received_at)
                selected.member_count += related.member_count
            # One bulk reassociation per bounded window page. Deferrable unique
            # member order permits offsetting whole related windows atomically.
            await session.execute(
                update(LprWindowMember)
                .where(LprWindowMember.window_id.in_(offsets))
                .values(
                    ordinal=LprWindowMember.ordinal
                    + case(offsets, value=LprWindowMember.window_id),
                    window_id=selected.id,
                )
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                update(LprIngestEvent)
                .where(LprIngestEvent.window_id.in_(offsets))
                .values(window_id=selected.id)
                .execution_options(synchronize_session=False)
            )
            for related in page:
                related.state, related.merged_into = "merged", selected.id
            after = page[-1].open_ordinal
            await session.flush()
            await _assert_preparing_read(session, claim)
        if merged:
            selected.first_seen = min(
                selected.first_seen, _context_time(context, "first_captured_at")
            )
            selected.updated_at = maximum  # min/max only for the merged window
            context["max_captured_at"] = maximum.isoformat()
            selected.ranking_context = context
        await session.flush()
        await _assert_preparing_read(session, claim)
        return _summary(selected)

    async def load_in_session(self, session: AsyncSession, window_id: uuid.UUID) -> StoredWindow:
        window = await session.get(LprDebounceWindow, window_id)
        if window is None:
            raise PipelineLeaseLost("window_unavailable")
        summary = _summary(window)
        anchors = {summary.first_ingest_id, summary.best_ingest_id}
        if summary.anchor_ingest_id is not None:
            anchors.add(summary.anchor_ingest_id)
        rows = (
            await session.execute(
                select(LprIngestEvent, LprWindowMember.prepared_context)
                .join(LprWindowMember, LprWindowMember.ingest_id == LprIngestEvent.id)
                .where(
                    LprWindowMember.window_id == window_id, LprWindowMember.ingest_id.in_(anchors)
                )
                .limit(3)
            )
        ).all()
        decoded = {ingest.id: read_from_member(ingest, context) for ingest, context in rows}
        if not anchors <= decoded.keys():
            raise PipelineLeaseLost("window_anchor_evidence_unavailable")
        preview = await candidate_page_in_session(session, window_id)
        return StoredWindow(
            summary,
            decoded[summary.first_ingest_id],
            decoded[summary.best_ingest_id],
            decoded[summary.anchor_ingest_id] if summary.anchor_ingest_id is not None else None,
            preview,
        )

    async def activate_visitor_resolution_in_session(
        self,
        session: AsyncSession,
        claim: WindowClaim,
        *,
        max_seconds: float,
    ) -> None:
        window = await assert_window_in_session(session, claim, completed=True)
        if window.reason != "visitor_pass_departure_plate":
            raise PipelineLeaseLost("visitor_resolution_outcome_unavailable")
        if window.visitor_resolution_expires_at is not None:
            return  # immutable interval; a retry never reactivates a pruned marker
        window.visitor_resolution_expires_at = window.first_seen + timedelta(seconds=max_seconds)
        window.visitor_resolution_active = True
        await session.flush()
        await assert_window_in_session(session, claim, completed=True)

    async def suppress_after_visitor_resolution_in_session(
        self,
        session: AsyncSession,
        claim: IngestClaim,
        read: PlateRead,
    ) -> bool:
        await _assert_preparing_read(session, claim, read)
        # Called at the original classification stage, not for earlier suppressed
        # reads. Prune existing markers across ALL sources before exemptions.
        await session.execute(
            update(LprDebounceWindow)
            .where(
                LprDebounceWindow.visitor_resolution_active.is_(True),
                LprDebounceWindow.visitor_resolution_expires_at < read.captured_at,
            )
            .values(visitor_resolution_active=False)
            .execution_options(synchronize_session=False)
        )
        if _is_exact_known_vehicle_plate_match(read) or _is_visitor_pass_plate_match(read):
            result = False
        else:
            result = bool(
                await session.scalar(
                    select(LprDebounceWindow.id)
                    .where(
                        LprDebounceWindow.visitor_resolution_active.is_(True),
                        LprDebounceWindow.source == read.source,
                        LprDebounceWindow.first_seen <= read.captured_at,
                        LprDebounceWindow.visitor_resolution_expires_at >= read.captured_at,
                    )
                    .limit(1)
                )
            )
        await session.flush()
        await _assert_preparing_read(session, claim)
        return result

    async def claim_immediate_in_session(
        self,
        session: AsyncSession,
        ingest_claim: IngestClaim,
        window_id: uuid.UUID,
        *,
        reason: str,
    ) -> WindowClaim:
        ingest = await _assert_preparing_read(session, ingest_claim)
        if ingest.window_id != window_id:
            raise PipelineLeaseLost("window_membership_changed")
        coordinator = await assert_coordinator_in_session(session, ingest_claim.coordinator)
        window = await session.get(LprDebounceWindow, window_id, with_for_update=True)
        if window is None or window.state != "open":
            raise PipelineLeaseLost("window_not_open")
        now = await database_now(session)
        window.state, window.reason, window.lease_token = "finalizing", reason, uuid.uuid4()
        window.coordinator_epoch = ingest_claim.coordinator.epoch
        window.lease_until = min(
            coordinator.lease_until or now, now + timedelta(seconds=COORDINATOR_LEASE_SECONDS)
        )
        coordinator.boundary_phase, coordinator.finalizing_window_id = (
            "immediate_finalizing",
            window.id,
        )
        claim = WindowClaim(window.id, window.lease_token, ingest_claim.coordinator)
        await session.flush()
        await assert_window_in_session(session, claim)
        return claim
