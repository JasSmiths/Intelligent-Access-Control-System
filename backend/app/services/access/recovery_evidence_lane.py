"""Bounded external evidence for parked windows. Only the coordinator commits decisions."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.services.access.authorization import recognition_deadline
from app.services.access.completion_database import access_session
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.execution import CollectedRecoveryEvidence
from app.services.access.pipeline_contracts import CoordinatorClaim
from app.services.access.pipeline_store import assert_window_in_session
from app.services.access.recovery_queue import RecoveryJob, claim_evidence_jobs
from app.services.access.window_store import AccessWindowStore
from app.services.resident_location_refresh import resume_location
from app.services.settings import get_runtime_config_for_session
from app.services.telemetry import TELEMETRY_CATEGORY_LPR, telemetry

RECOVERY_CAPACITY = 2


async def collect_recovery_evidence(job: RecoveryJob) -> CollectedRecoveryEvidence | None:
    if job.following:
        return None
    async with access_session(AsyncSessionLocal) as session:
        await assert_window_in_session(session, job.claim)
        window = await AccessWindowStore().load_in_session(session, job.claim.window_id)
        runtime = await get_runtime_config_for_session(session)
    direction = window.anchor_read or window.first_read
    trace = telemetry.start_trace("Parked resident recovery evidence", category=TELEMETRY_CATEGORY_LPR,
        registration_number=window.best_read.registration_number)
    try:
        location = await resume_location(job.claim, direction, epoch=job.phone_epoch) if job.phone_epoch else None
        expires = recognition_deadline(min(direction.captured_at, window.best_read.captured_at,
            window.first_read.captured_at), window.summary.min_received_at)
        camera = None
        if (remaining := (expires - datetime.now(UTC)).total_seconds()) > 0:
            try:
                async with asyncio.timeout(remaining):
                    camera = await AccessEvidenceResolver(runtime).prepare_camera_evidence(
                        window.best_read, direction, None, trace, location_refresh=location, recovery_only=True)
            except TimeoutError:
                pass
        return CollectedRecoveryEvidence(camera, location)
    finally:
        trace.finish()


class RecoveryEvidenceLane:
    def __init__(self, wake: Callable[[], None]) -> None:
        self._wake = wake
        self._jobs: dict[uuid.UUID, tuple[RecoveryJob, asyncio.Task[CollectedRecoveryEvidence | None]]] = {}
        self._refill = False

    def discover(self) -> None:
        self._refill = True

    def changed(self) -> None:
        self.discover()
        self._wake()

    @property
    def pending(self) -> bool:
        return self._refill or bool(self._jobs)

    @property
    def runnable(self) -> bool:
        return ((self._refill and len(self._jobs) < RECOVERY_CAPACITY)
                or any(task.done() for _, task in self._jobs.values()))

    async def next_completed(self, coordinator: CoordinatorClaim) -> tuple[RecoveryJob, asyncio.Task[CollectedRecoveryEvidence | None]] | None:
        if self._refill and (capacity := RECOVERY_CAPACITY - len(self._jobs)) > 0:
            jobs, self._refill = await claim_evidence_jobs(coordinator, active=self._jobs.keys(), capacity=capacity)
            for job in jobs:
                task = asyncio.create_task(collect_recovery_evidence(job), name="resident-recovery-evidence")
                task.add_done_callback(lambda _: self._wake())
                self._jobs[job.claim.window_id] = job, task
        for identity, (job, task) in self._jobs.items():
            if task.done():
                del self._jobs[identity]
                self.changed()
                return job, task
        if self.runnable:
            self._wake()
        return None

    async def stop(self) -> None:
        tasks = [task for _, task in self._jobs.values()]
        for task in tasks:
            task.cancel()
        await drain_owned(asyncio.gather(*tasks, return_exceptions=True))
        self._jobs.clear()
        self._refill = False
