"""One admitted export, detached reads/rendering and a fresh atomic final receipt."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import ReportExport
from app.services.auth import authenticate_token
from app.services.report_contracts import (
    REPORT_COMMIT_ATTEMPTS,
    REPORT_PIPELINE_SECONDS,
    ReportAuthority,
    ReportAuthorityError,
    ReportBusyError,
    ReportExportError,
    ReportOutcomeUnknown,
    ReportRequest,
    ReportTimeoutError,
    ReportWork,
)
from app.services.report_reads import report_read_transaction
from app.services.report_renderer import render_person_movement_report_pdf
from app.services.reports import (
    REPORT_TYPE_PERSON_MOVEMENTS,
    _ensure_aware,
    _resolve_report_path,
    _timezone,
    build_movement_report_snapshot,
    generate_report_number,
    report_export_payload,
)
from app.services.telemetry import TELEMETRY_CATEGORY_ACCESS, actor_from_user, write_audit_log

logger = logging.getLogger(__name__)


class _NumberCollision(Exception):
    pass


class ReportExportPipeline:
    """Process-local, nonwaiting capacity; actual work retains the slot to cleanup."""
    def __init__(self, session_factory: Callable[[], AsyncSession] = AsyncSessionLocal) -> None:
        self._sessions = session_factory
        self._running = False
        self._active: asyncio.Task[dict[str, Any]] | None = None
        self._work: ReportWork | None = None

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        task, work = self._active, self._work
        if task is None:
            return
        if work is not None:
            work.request_stop()
        try:
            await drain_owned(task)
        except ReportExportError:
            # Stop drains real resource cleanup; request owner reports its result.
            pass

    async def export(
        self, request: ReportRequest, authority: ReportAuthority, *,
        disconnected: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        if not self._running:
            raise ReportBusyError("Report export is stopping. Try again when the service is ready.")
        if self._active is not None:
            raise ReportBusyError("Another report is being exported. Try again shortly.")
        work = ReportWork(monotonic() + REPORT_PIPELINE_SECONDS)
        self._work = work
        task = asyncio.create_task(self._run_owned(request, authority, work, disconnected), name="report-export")
        self._active = task
        try:
            async with asyncio.timeout(work.remaining()):
                return await asyncio.shield(task)
        except TimeoutError as exc:
            work.request_stop()
            try:
                # An accepted commit wins over a late deadline. Unknown commit
                # remains unknown; neither result may be changed to safe failure.
                accepted: dict[str, Any] = await drain_owned(task)
                return accepted
            except ReportOutcomeUnknown:
                raise
            except Exception:  # noqa: BLE001 - Retrieve/drain arbitrary pipeline errors before reporting deadline.
                raise ReportTimeoutError("Report export exceeded its 120-second limit. Choose a shorter period and try again.") from exc
        except asyncio.CancelledError:
            work.request_stop()
            try:
                await drain_owned(task)
            except Exception:  # noqa: BLE001 - Retrieve actual task failure before caller cancellation.
                logger.warning("Cancelled report work finished with an error during drain")
            raise

    @staticmethod
    async def _watch_disconnect(disconnected: Callable[[], Awaitable[None]], work: ReportWork) -> None:
        try:
            await disconnected()
        except Exception:  # noqa: BLE001 - A failed request receive cannot keep starting export stages for an absent caller.
            logger.warning("Report disconnect waiter failed; stopping further stages")
        work.request_stop()

    async def _run_owned(
        self, request: ReportRequest, authority: ReportAuthority, work: ReportWork,
        disconnected: Callable[[], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        artifact_id = uuid.uuid4()
        relative_path = f"reports/{artifact_id}.pdf"
        path: Path | None = None
        retain_artifact = False
        watcher: asyncio.Task[None] | None = None
        try:
            if disconnected is not None:
                watcher = asyncio.create_task(self._watch_disconnect(disconnected, work), name="report-disconnect")
            path = _resolve_report_path(relative_path, must_exist=False)
            start, end = _ensure_aware(request.period_start), _ensure_aware(request.period_end)
            if end <= start:
                raise ReportExportError("Report end time must be after the start time.")
            work.check()
            async with self._sessions() as source, report_read_transaction(source, work) as config:
                number = await generate_report_number(source)
                snapshot = await build_movement_report_snapshot(
                    source, person_id=request.person_id, visitor_pass_id=request.visitor_pass_id,
                    report_number=number, period_start=start, period_end=end,
                    options=request.options, timezone=_timezone(config.site_timezone),
                    include_profile_source=True, verify_snapshot_availability=False,
                )
            # No source/auth transaction is held while images, template or native
            # renderer work runs. Collision retries reuse this detached snapshot.
            for attempt in range(REPORT_COMMIT_ATTEMPTS):
                work.check()
                pdf_bytes = await render_person_movement_report_pdf(snapshot, path, work=work)
                work.check()
                try:
                    receipt = await self._persist(
                        artifact_id, relative_path, pdf_bytes, request, authority, snapshot, start, end, work,
                    )
                except _NumberCollision:
                    if attempt + 1 == REPORT_COMMIT_ATTEMPTS:
                        raise ReportExportError("Could not allocate a unique report ID. Try again.") from None
                    work.check()
                    async with self._sessions() as source, report_read_transaction(source, work):
                        snapshot["report_id"] = await generate_report_number(source)
                    continue
                retain_artifact = True
                return receipt
            raise AssertionError("report attempt bound exhausted")
        except ReportOutcomeUnknown:
            retain_artifact = True
            raise
        finally:
            try:
                if not retain_artifact and path is not None:
                    # UUID identity means a rejected/colliding request cannot
                    # unlink another report's public-number artifact.
                    await drain_owned(asyncio.to_thread(path.unlink, missing_ok=True))
            finally:
                try:
                    if watcher is not None:
                        watcher.cancel()
                        try:
                            await drain_owned(watcher)
                        except asyncio.CancelledError:
                            pass  # Expected cancellation of our completed-request receive waiter.
                finally:
                    self._active = None
                    self._work = None

    async def _persist(
        self, export_id: uuid.UUID, relative_path: str, pdf_bytes: int,
        request: ReportRequest, authority: ReportAuthority, snapshot: dict[str, Any],
        start: datetime, end: datetime, work: ReportWork,
    ) -> dict[str, Any]:
        work.check()
        session = self._sessions()
        commit_started = False
        receipt: dict[str, Any] | None = None
        try:
            # Explicit connection acquisition remains owned if it completes late.
            await session.connection()
            work.check()
            try:
                async with asyncio.timeout(work.remaining()):
                    await session.execute(select(func.set_config(
                        "statement_timeout", str(work.remaining_milliseconds()), True,
                    )))
                    actor = await authenticate_token(session, authority.token)
                    if actor is None or actor.id != authority.user_id:
                        raise ReportAuthorityError("Your session is no longer valid. Sign in again before exporting.")
                    work.check()
                    subject_type = snapshot["subject_type"]
                    subject_id = snapshot["subject"]["id"]
                    person_id = uuid.UUID(subject_id) if subject_type == "person" else None
                    number = snapshot["report_id"]
                    row = ReportExport(
                        id=export_id, report_number=number, report_type=REPORT_TYPE_PERSON_MOVEMENTS,
                        person_id=person_id, period_start=start, period_end=end, options=request.options,
                        snapshot=snapshot, pdf_path=relative_path, pdf_bytes=pdf_bytes,
                        created_by_user_id=actor.id, created_at=datetime.now(UTC),
                    )
                    session.add(row)
                    await write_audit_log(
                        session, category=TELEMETRY_CATEGORY_ACCESS, action="report.export",
                        actor=actor_from_user(actor), actor_user_id=actor.id, target_entity="ReportExport",
                        target_id=number, target_label=f"{'Visitor pass' if subject_type == 'visitor_pass' else 'Person'} movement report {number}",
                        metadata={"report_number": number, "report_type": REPORT_TYPE_PERSON_MOVEMENTS,
                                  "subject_type": subject_type, "subject_id": str(subject_id),
                                  "person_id": str(person_id) if person_id else None,
                                  "visitor_pass_id": str(request.visitor_pass_id) if request.visitor_pass_id else None,
                                  "period_start": start.isoformat(), "period_end": end.isoformat(),
                                  "pdf_bytes": pdf_bytes, "options": request.options},
                    )
                    await session.flush()
                    prepared_receipt = report_export_payload(row)
                    work.check()
                    commit_started = True
                    await session.commit()
                    # Receipt was assembled before commit. No refresh or late
                    # budget check can relabel an accepted durable export.
                    receipt = prepared_receipt
            except IntegrityError as exc:
                constraint = getattr(exc.orig, "constraint_name", None) or getattr(exc.orig.__cause__, "constraint_name", None)
                if constraint == "ux_report_exports_report_number":
                    commit_started = False
                    await session.rollback()
                    raise _NumberCollision from exc
                raise
            except TimeoutError as exc:
                if not commit_started:
                    raise ReportTimeoutError("Report persistence exceeded its 120-second limit.") from exc
                raise
        except Exception:  # An uncertain commit must be resolved by its UUID, never retried blindly.
            if commit_started and receipt is None:
                try:
                    await drain_owned(session.close())
                except Exception:  # noqa: BLE001 - Close failure cannot prove commit rejection.
                    logger.warning("Report commit cleanup failed; resolving durable UUID outcome")
                receipt = await self._resolve_outcome(export_id, snapshot["report_id"], work)
                return receipt
            raise
        finally:
            try:
                await drain_owned(session.close())
            except Exception:  # Close failure cannot reclassify a known or uncertain commit.
                if not commit_started:
                    raise
                logger.warning("Report final session cleanup failed after commit began")
        assert receipt is not None
        return receipt

    async def _resolve_outcome(self, export_id: uuid.UUID, number: str, work: ReportWork) -> dict[str, Any]:
        for _ in range(REPORT_COMMIT_ATTEMPTS):
            found: dict[str, Any] | None = None
            try:
                work.check()
                async with self._sessions() as session:
                    await session.connection()
                    work.check()
                    async with asyncio.timeout(work.remaining()):
                        await session.execute(select(func.set_config(
                            "statement_timeout", str(work.remaining_milliseconds()), True,
                        )))
                        row = await session.get(ReportExport, export_id)
                        if row is not None:
                            found = report_export_payload(row)
            except Exception:  # noqa: BLE001 - Faulted lookups cannot establish durable rejection.
                logger.warning("Report durable UUID outcome lookup or cleanup failed")
            if found is not None:
                # A retrieved durable receipt survives lookup session cleanup.
                return found
        raise ReportOutcomeUnknown(f"Report {number} completion could not be confirmed. Check its report ID before trying again.")


report_exports = ReportExportPipeline()
