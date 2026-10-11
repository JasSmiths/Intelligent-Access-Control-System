"""Explicit optional access stage owners; no hardware/admission capability.

All input is prepared under current Event/checkpoint authority. Provider/file
work happens with no open session; result participants preserve domain lock order.
The core cutover, not importing this module, activates these owners.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import AccessCompletionWork, AccessEvent, Anomaly
from app.models.enums import AnomalyType
from app.modules.unifi_protect.client import ProtectMedia
from app.services.access.completion_database import access_session
from app.services.access.completion_results import accept_visual_in_session
from app.services.access.completion_store import (
    assert_completion_before_commit,
    assert_completion_in_session,
    finish_completion_in_session,
)
from app.services.access.pipeline_contracts import CompletionClaim, PipelineLeaseLost
from app.services.access.pipeline_store import database_now
from app.services.access.publication import mark_public_event_changed_in_session
from app.services.access.visitor_link import apply_visitor_link_in_session
from app.services.event_bus import event_bus
from app.services.lpr_zone_shadow import (
    record_zone_shadow_in_session,
    serialize_lpr_zone_shadow_observation,
    zone_shadow_read_from_payload,
)
from app.services.realtime_contracts import PublicationTransport
from app.services.snapshots import (
    ACCESS_EVENT_SNAPSHOT_CAMERA,
    SNAPSHOT_HEIGHT,
    SNAPSHOT_WIDTH,
    SnapshotManager,
    SnapshotMetadata,
    access_event_snapshot_relative_path,
    access_event_snapshot_url,
    apply_snapshot_to_access_event,
)
from app.services.vehicle_visual_detections import VehicleVisualDetectionRecorder

logger = get_logger(__name__)


class CompletionSnapshotProvider(Protocol):
    async def configured(self) -> bool: ...
    async def snapshot(
        self, camera_id: str, *, width: int | None = None, height: int | None = None
    ) -> ProtectMedia: ...
    async def event_thumbnail(
        self, event_id: str, *, width: int | None = None, height: int | None = None
    ) -> ProtectMedia: ...


@dataclass(frozen=True)
class SnapshotInput:
    event_id: uuid.UUID
    registration_number: str
    occurred_at: datetime
    protect_event_id: str | None
    camera_budget: float
    sampled_monotonic: float
    operation_deadline: float

    def remaining_camera_budget(self) -> float:
        return max(0.0, self.camera_budget - (
            asyncio.get_running_loop().time() - self.sampled_monotonic
        ))


@dataclass(frozen=True)
class PreparedAccessSnapshot:
    event_id: uuid.UUID
    claim_token: uuid.UUID
    metadata: SnapshotMetadata
    source: str


async def prepare_snapshot_in_session(session: AsyncSession, claim: CompletionClaim, *, operation_deadline: float | None = None) -> SnapshotInput:
    if claim.stage != "snapshot":
        raise ValueError("Snapshot preparation requires its explicit stage")
    row = (await session.execute(select(
        AccessEvent.id, AccessEvent.registration_number, AccessEvent.occurred_at
    ).where(AccessEvent.id == claim.event_id).with_for_update())).one_or_none()
    if row is None:
        raise PipelineLeaseLost("completion_event_unavailable")
    work = await assert_completion_in_session(session, claim)
    value = work.result.get("input")
    if (not isinstance(value, dict) or set(value) != {"protect_event_id"}
            or value["protect_event_id"] is not None
            and not isinstance(value["protect_event_id"], str)):
        raise ValueError("Snapshot input unavailable")
    sampled = asyncio.get_running_loop().time()
    now = await database_now(session)
    if claim.origin_at is None or claim.deadline is None or work.lease_until is None:
        raise PipelineLeaseLost("completion_origin_changed")
    return SnapshotInput(row.id, row.registration_number, row.occurred_at,
                         value["protect_event_id"],
                         max(0.0, 5.0 - (now - claim.origin_at).total_seconds()),
                         sampled, min(operation_deadline if operation_deadline is not None else float("inf"),
                                      sampled + min((claim.deadline - now).total_seconds(),
                                                    (work.lease_until - now).total_seconds())))


async def accept_snapshot_in_session(
    session: AsyncSession, claim: CompletionClaim, prepared: PreparedAccessSnapshot
) -> None:
    expected = access_event_snapshot_relative_path(claim.event_id, claim_token=claim.token)
    if (claim.stage != "snapshot" or prepared.event_id != claim.event_id
            or prepared.claim_token != claim.token or prepared.metadata.relative_path != expected
            or prepared.metadata.url != access_event_snapshot_url(claim.event_id)):
        raise ValueError("Snapshot artifact identity differs from its owned claim")
    event = await session.get(AccessEvent, claim.event_id, with_for_update=True, populate_existing=True)
    if event is None:
        raise PipelineLeaseLost("completion_event_unavailable")
    anomalies = list(await session.scalars(select(Anomaly).where(
        Anomaly.event_id == event.id, Anomaly.anomaly_type == AnomalyType.UNAUTHORIZED_PLATE
    ).order_by(Anomaly.id).with_for_update()))
    work = await assert_completion_in_session(session, claim)
    apply_snapshot_to_access_event(event, prepared.metadata)
    snapshot = {
        "url": prepared.metadata.url, "camera": prepared.metadata.camera,
        "captured_at": prepared.metadata.captured_at.isoformat(),
        "content_type": prepared.metadata.content_type, "bytes": prepared.metadata.bytes,
        "width": prepared.metadata.width, "height": prepared.metadata.height,
    }
    for anomaly in anomalies:
        anomaly.context = {**(anomaly.context or {}), "snapshot": snapshot}
    work.result = {**work.result, "snapshot": {
        "claim_token": str(claim.token), "path": expected,
        "source": prepared.source, "accepted_at": (await database_now(session)).isoformat(),
    }}
    await finish_completion_in_session(session, claim, state="succeeded", outcome="snapshot_accepted")
    await mark_public_event_changed_in_session(session, event.id)
    await assert_completion_before_commit(session, claim, finished=True)


async def apply_zone_shadow_in_session(
    session: AsyncSession, claim: CompletionClaim
) -> list[dict[str, Any]]:
    if claim.stage != "zone_shadow":
        raise ValueError("Zone participant requires its explicit stage")
    row = (await session.execute(select(
        AccessEvent.id, AccessEvent.registration_number, AccessEvent.decision,
        AccessEvent.direction, AccessEvent.person_id, AccessEvent.vehicle_id,
        AccessEvent.raw_payload["visitor_pass"]["id"].astext.label("visitor_id"),
    ).where(AccessEvent.id == claim.event_id).with_for_update())).one_or_none()
    if row is None:
        raise PipelineLeaseLost("completion_event_unavailable")
    work = await assert_completion_in_session(session, claim)
    value = work.result.get("input")
    read = zone_shadow_read_from_payload(value.get("read")) if isinstance(value, dict) else None
    mode = value.get("mode") if isinstance(value, dict) else None
    if read is None or read.registration_number != row.registration_number or not isinstance(mode, str):
        await finish_completion_in_session(session, claim, state="held", outcome="zone_input_unavailable")
        return []
    try:
        visitor_id = uuid.UUID(row.visitor_id) if row.visitor_id else None
    except (TypeError, ValueError):
        await finish_completion_in_session(session, claim, state="held", outcome="zone_input_unavailable")
        return []
    rows = await record_zone_shadow_in_session(
        session, read, access_event_id=row.id, actual_decision=row.decision.value,
        actual_direction=row.direction.value, person_id=row.person_id, vehicle_id=row.vehicle_id,
        visitor_pass_id=visitor_id, mode=mode,
    )
    work.result = {**work.result, "recorded_count": len(rows)}
    await finish_completion_in_session(session, claim, state="succeeded", outcome="zone_shadow_recorded")
    await assert_completion_before_commit(session, claim, finished=True)
    return [serialize_lpr_zone_shadow_observation(row) for row in rows]


class AccessCompletionStages:
    def __init__(
        self,
        *,
        visual: VehicleVisualDetectionRecorder,
        snapshots: SnapshotManager,
        protect: CompletionSnapshotProvider,
        session_factory: Callable[[], AsyncSession] = AsyncSessionLocal,
        publisher: PublicationTransport = event_bus,
    ) -> None:
        self.visual_recorder, self.snapshots, self.protect = visual, snapshots, protect
        self.session_factory, self.publisher = session_factory, publisher

    @staticmethod
    def _check_start(deadline: float) -> None:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("completion_start_deadline_expired")

    async def _skip(self, claim: CompletionClaim, reason: str, *, held: bool = False) -> None:
        async with access_session(self.session_factory) as session:
            identity = await session.scalar(select(AccessEvent.id).where(
                AccessEvent.id == claim.event_id
            ).with_for_update())
            if identity is None:
                raise PipelineLeaseLost("completion_event_unavailable")
            await finish_completion_in_session(session, claim,
                                               state="held" if held else "skipped", outcome=reason)
            await assert_completion_before_commit(session, claim, finished=True)
            await session.commit()

    async def visual(self, claim: CompletionClaim, deadline: float) -> None:
        self._check_start(deadline)
        if claim.stage != "visual":
            raise ValueError("Visual owner requires its explicit stage")
        async with access_session(self.session_factory) as session:
            row = (await session.execute(select(
                AccessEvent.registration_number, AccessEvent.occurred_at, AccessEvent.vehicle_id
            ).where(AccessEvent.id == claim.event_id).with_for_update())).one_or_none()
            if row is None:
                raise PipelineLeaseLost("completion_event_unavailable")
            work = await assert_completion_in_session(session, claim)
            sampled = asyncio.get_running_loop().time()
            now = await database_now(session)
            if claim.deadline is None or work.lease_until is None:
                raise PipelineLeaseLost("completion_origin_changed")
            deadline = min(deadline, sampled + min((claim.deadline - now).total_seconds(),
                                                   (work.lease_until - now).total_seconds()))
            plate, occurred_at, attempts = row.registration_number, row.occurred_at, 1 if row.vehicle_id else 5
        visual: dict[str, Any] | None = None
        for attempt in range(attempts):
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("visual_start_deadline_expired")
            visual = await self._visual_match(plate, occurred_at, deadline)
            if visual is not None:
                break
            if attempt + 1 < attempts:
                await asyncio.sleep(0.25)
        if visual is None:
            await self._skip(claim, "visual_match_unavailable")
            return
        async with access_session(self.session_factory) as session:
            await accept_visual_in_session(session, claim, visual)
            await assert_completion_before_commit(session, claim, finished=True)
            self._check_start(deadline)
            await session.commit()

    async def _visual_match(self, plate: str, occurred_at: datetime, deadline: float) -> dict[str, Any] | None:
        async def invoke() -> dict[str, Any] | None:
            self._check_start(deadline)
            return await self.visual_recorder.recent_match(plate, occurred_at=occurred_at, max_age_seconds=45.0)
        child = asyncio.create_task(invoke(), name="access-completion-visual-match")
        try:
            async with asyncio.timeout_at(deadline):
                return await asyncio.shield(child)
        finally:
            if not child.done() and not child.cancelling():
                child.cancel()
            await drain_owned(asyncio.gather(child, return_exceptions=True))

    async def visitor_link(self, claim: CompletionClaim, deadline: float) -> None:
        self._check_start(deadline)
        async with access_session(self.session_factory) as session:
            await apply_visitor_link_in_session(session, claim)
            await assert_completion_before_commit(session, claim, finished=True)
            self._check_start(deadline)
            await session.commit()

    async def zone_shadow(self, claim: CompletionClaim, deadline: float) -> None:
        self._check_start(deadline)
        async with access_session(self.session_factory) as session:
            payloads = await apply_zone_shadow_in_session(session, claim)
            await assert_completion_before_commit(session, claim, finished=True)
            self._check_start(deadline)
            await session.commit()
        # This existing feature invalidation is observational. Its failure never
        # reopens the succeeded checkpoint or inserts the domain rows again.
        for payload in payloads:
            try:
                await self.publisher.publish("lpr_zone_shadow.observed", payload)
            except Exception as exc:  # noqa: BLE001 - domain receipt already committed
                logger.warning("zone_shadow_publication_failed", extra={
                    "event_id": str(claim.event_id), "exception_class": type(exc).__name__
                })

    async def _camera_returned_in_time(self, claim: CompletionClaim) -> bool:
        async with access_session(self.session_factory) as session:
            await session.scalar(select(AccessEvent.id).where(AccessEvent.id == claim.event_id).with_for_update())
            await assert_completion_in_session(session, claim)
            now = await database_now(session)
            return claim.origin_at is not None and (now - claim.origin_at).total_seconds() < 5.0

    async def _current_snapshot(self, claim: CompletionClaim, deadline: float) -> SnapshotInput:
        self._check_start(deadline)
        async with access_session(self.session_factory) as session:
            return await prepare_snapshot_in_session(session, claim, operation_deadline=deadline)

    async def _provider_media(
        self, operation: Callable[[], Awaitable[ProtectMedia]], *, deadline: float
    ) -> ProtectMedia:
        async def invoke() -> ProtectMedia:
            # A due timeout callback may run after this child on a stalled loop.
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("snapshot_start_deadline_expired")
            return await operation()

        child = asyncio.create_task(invoke(), name="access-completion-provider")
        try:
            async with asyncio.timeout_at(deadline):
                return await asyncio.shield(child)
        finally:
            if not child.done() and not child.cancelling():
                child.cancel()
            await drain_owned(asyncio.gather(child, return_exceptions=True))

    async def _capture(self, claim: CompletionClaim, prepared: SnapshotInput) -> PreparedAccessSnapshot | None:
        if not await self.protect.configured():
            return None
        # Configuration may have awaited its own DB/client boundary. Recheck the
        # current event/claim, then account for session-close elapsed time before I/O.
        prepared = await self._current_snapshot(claim, prepared.operation_deadline)
        media: ProtectMedia | None = None
        source = "camera_snapshot"
        captured_at = prepared.occurred_at
        camera_budget = prepared.remaining_camera_budget()
        if camera_budget > 0:
            camera_end = min(prepared.operation_deadline,
                             prepared.sampled_monotonic + prepared.camera_budget)
            try:
                media = await self._provider_media(lambda: self.protect.snapshot(
                    ACCESS_EVENT_SNAPSHOT_CAMERA, width=SNAPSHOT_WIDTH, height=SNAPSHOT_HEIGHT
                ), deadline=camera_end)
                captured_at = datetime.now(UTC)
                if (asyncio.get_running_loop().time() >= camera_end
                        or not await self._camera_returned_in_time(claim)):
                    media = None
            except PipelineLeaseLost:
                raise  # Lost authority must never begin even the optional fallback.
            except Exception:  # noqa: BLE001 - exact event thumbnail is the existing optional fallback
                media = None
        if media is None and prepared.protect_event_id:
            current = await self._current_snapshot(claim, prepared.operation_deadline)
            if current.protect_event_id != prepared.protect_event_id:
                raise PipelineLeaseLost("snapshot_input_changed")
            protect_id = prepared.protect_event_id
            media = await self._provider_media(lambda: self.protect.event_thumbnail(
                protect_id, width=SNAPSHOT_WIDTH, height=SNAPSHOT_HEIGHT
            ), deadline=current.operation_deadline)
            source = "protect_event_thumbnail"
            captured_at = prepared.occurred_at
        if media is None:
            return None
        self._check_start(prepared.operation_deadline)
        metadata = await self.snapshots.store_image(
            media.content,
            relative_path=access_event_snapshot_relative_path(claim.event_id, claim_token=claim.token),
            url=access_event_snapshot_url(claim.event_id), camera=ACCESS_EVENT_SNAPSHOT_CAMERA,
            captured_at=captured_at,
        )
        return PreparedAccessSnapshot(claim.event_id, claim.token, metadata, source)

    async def _cleanup_unaccepted(self, claim: CompletionClaim) -> None:
        """Only called after a settled operation that never invoked commit.

        A commit exception/cancellation is an uncertain outcome: that caller
        retains files without using pointer absence as proof of rejection.
        """
        expected = access_event_snapshot_relative_path(claim.event_id, claim_token=claim.token)
        try:
            async with asyncio.timeout(5):
                async with access_session(self.session_factory) as session:
                    current_path = await session.scalar(select(AccessEvent.snapshot_path).where(
                        AccessEvent.id == claim.event_id
                    ).with_for_update())
                    work = await session.get(AccessCompletionWork, claim.work_id, with_for_update=True,
                                             populate_existing=True)
                    snapshot = work.result.get("snapshot") if work is not None else None
                    if current_path == expected or isinstance(snapshot, dict) and snapshot.get("path") == expected:
                        return
                await self.snapshots.delete_access_snapshot_claim(claim.event_id, claim.token)
        except Exception:  # noqa: BLE001 - unknown cleanup authority retains this exact artifact
            return

    async def snapshot(self, claim: CompletionClaim, deadline: float) -> None:
        self._check_start(deadline)
        commit_invoked = False
        try:
            try:
                async with access_session(self.session_factory) as session:
                    prepared_input = await prepare_snapshot_in_session(session, claim, operation_deadline=deadline)
            except ValueError:
                await self._skip(claim, "snapshot_input_unavailable", held=True)
                return
            prepared = await self._capture(claim, prepared_input)
            if prepared is None:
                await self._skip(claim, "snapshot_unavailable")
                return
            async with access_session(self.session_factory) as session:
                await accept_snapshot_in_session(session, claim, prepared)
                await assert_completion_before_commit(session, claim, finished=True)
                # Any failure/cancellation from here may be an accepted commit;
                # do not delete on an unlocked absence or lost connection.
                self._check_start(deadline)
                commit_invoked = True
                await session.commit()
        finally:
            if not commit_invoked:
                await self._cleanup_unaccepted(claim)
