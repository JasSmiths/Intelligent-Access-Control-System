import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, User, VisitorPass
from app.models.enums import VisitorPassStatus
from app.modules.dvla.vehicle_enquiry import normalize_registration_number
from app.services.event_bus import event_bus
from app.services.settings import get_runtime_config
from app.services.telemetry import (
    TELEMETRY_CATEGORY_ACCESS,
    TELEMETRY_CATEGORY_CRUD,
    actor_from_user,
    audit_diff,
    write_audit_log,
)

logger = get_logger(__name__)

DEFAULT_WINDOW_MINUTES = 30
MAX_WINDOW_MINUTES = 24 * 60
VISITOR_PASS_WORKER_INTERVAL_SECONDS = 30.0
VISITOR_PASS_ACTIVE_STATUSES = (VisitorPassStatus.SCHEDULED, VisitorPassStatus.ACTIVE)
VISITOR_PASS_LOCKED_STATUSES = (VisitorPassStatus.USED, VisitorPassStatus.CANCELLED)


class VisitorPassError(ValueError):
    """Raised when a requested Visitor Pass transition is not valid."""


class VisitorPassService:
    """Persistent one-shot access windows for expected unknown visitors."""

    def __init__(self) -> None:
        self._worker: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stop_event.clear()
        self._worker = asyncio.create_task(self._run_lifecycle_worker(), name="visitor-pass-lifecycle-worker")
        logger.info("visitor_pass_service_started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            await self._worker
        logger.info("visitor_pass_service_stopped")

    async def _run_lifecycle_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.refresh_statuses()
            except Exception as exc:
                logger.warning("visitor_pass_lifecycle_refresh_failed", extra={"error": str(exc)})
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=VISITOR_PASS_WORKER_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                continue

    async def refresh_statuses(
        self,
        *,
        session: AsyncSession | None = None,
        now: datetime | None = None,
        actor: str = "System",
        actor_user_id: uuid.UUID | str | None = None,
        publish: bool = True,
    ) -> list[VisitorPass]:
        checked_at = _ensure_aware(now or datetime.now(tz=UTC))
        if session is not None:
            return await self._refresh_statuses_in_session(
                session,
                now=checked_at,
                actor=actor,
                actor_user_id=actor_user_id,
                publish=publish,
            )

        async with AsyncSessionLocal() as own_session:
            changed = await self._refresh_statuses_in_session(
                own_session,
                now=checked_at,
                actor=actor,
                actor_user_id=actor_user_id,
                publish=False,
            )
            payloads = [serialize_visitor_pass(pass_) for pass_ in changed]
            await own_session.commit()

        if publish:
            for payload in payloads:
                await event_bus.publish("visitor_pass.status_changed", {"visitor_pass": payload})
        return changed

    async def _refresh_statuses_in_session(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        actor: str,
        actor_user_id: uuid.UUID | str | None,
        publish: bool,
    ) -> list[VisitorPass]:
        passes = (
            await session.scalars(
                select(VisitorPass)
                .where(VisitorPass.status.in_(VISITOR_PASS_ACTIVE_STATUSES))
                .order_by(VisitorPass.expected_time, VisitorPass.created_at)
            )
        ).all()
        changed: list[VisitorPass] = []
        for visitor_pass in passes:
            next_status = self.status_for(visitor_pass, now)
            if next_status == visitor_pass.status:
                continue
            before = visitor_pass_audit_snapshot(visitor_pass)
            visitor_pass.status = next_status
            changed.append(visitor_pass)
            await self._audit_change(
                session,
                visitor_pass,
                action="visitor_pass.status_refresh",
                actor=actor,
                actor_user_id=actor_user_id,
                before=before,
                metadata={"refreshed_at": now.isoformat(), "new_status": next_status.value},
                category=TELEMETRY_CATEGORY_ACCESS,
            )
            if publish:
                await event_bus.publish(
                    "visitor_pass.status_changed",
                    {"visitor_pass": serialize_visitor_pass(visitor_pass)},
                )
        return changed

    async def create_pass(
        self,
        session: AsyncSession,
        *,
        visitor_name: str,
        expected_time: datetime,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
        source: str = "ui",
        created_by_user_id: uuid.UUID | str | None = None,
        actor: str = "System",
    ) -> VisitorPass:
        name = _clean_visitor_name(visitor_name)
        expected = _ensure_aware(expected_time)
        window = _bounded_window_minutes(window_minutes)
        visitor_pass = VisitorPass(
            visitor_name=name,
            expected_time=expected,
            window_minutes=window,
            status=status_for_values(expected, window, datetime.now(tz=UTC)),
            creation_source=_clean_source(source),
            created_by_user_id=_coerce_uuid(created_by_user_id),
        )
        session.add(visitor_pass)
        await session.flush()
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.create",
            actor=actor,
            actor_user_id=created_by_user_id,
            before={},
            category=TELEMETRY_CATEGORY_CRUD,
        )
        return visitor_pass

    async def update_pass(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        visitor_name: str | None = None,
        expected_time: datetime | None = None,
        window_minutes: int | None = None,
        actor: str = "System",
        actor_user_id: uuid.UUID | str | None = None,
    ) -> VisitorPass:
        if visitor_pass.status in VISITOR_PASS_LOCKED_STATUSES:
            raise VisitorPassError(f"{visitor_pass.status.value.title()} visitor passes cannot be edited.")
        before = visitor_pass_audit_snapshot(visitor_pass)
        if visitor_name is not None:
            visitor_pass.visitor_name = _clean_visitor_name(visitor_name)
        if expected_time is not None:
            visitor_pass.expected_time = _ensure_aware(expected_time)
        if window_minutes is not None:
            visitor_pass.window_minutes = _bounded_window_minutes(window_minutes)
        visitor_pass.status = status_for_values(
            visitor_pass.expected_time,
            visitor_pass.window_minutes,
            datetime.now(tz=UTC),
        )
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.update",
            actor=actor,
            actor_user_id=actor_user_id,
            before=before,
            category=TELEMETRY_CATEGORY_CRUD,
        )
        return visitor_pass

    async def cancel_pass(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        actor: str = "System",
        actor_user_id: uuid.UUID | str | None = None,
        reason: str | None = None,
    ) -> VisitorPass:
        if visitor_pass.status == VisitorPassStatus.USED:
            raise VisitorPassError("Used visitor passes cannot be cancelled.")
        if visitor_pass.status == VisitorPassStatus.CANCELLED:
            return visitor_pass
        before = visitor_pass_audit_snapshot(visitor_pass)
        visitor_pass.status = VisitorPassStatus.CANCELLED
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.cancel",
            actor=actor,
            actor_user_id=actor_user_id,
            before=before,
            metadata={"reason": reason},
            category=TELEMETRY_CATEGORY_CRUD,
        )
        return visitor_pass

    async def list_passes(
        self,
        session: AsyncSession,
        *,
        statuses: list[VisitorPassStatus] | None = None,
        search: str | None = None,
        limit: int = 100,
    ) -> list[VisitorPass]:
        query = (
            select(VisitorPass)
            .options(selectinload(VisitorPass.created_by))
            .order_by(VisitorPass.expected_time.desc(), VisitorPass.created_at.desc())
            .limit(max(1, min(limit, 500)))
        )
        if statuses:
            query = query.where(VisitorPass.status.in_(statuses))
        normalized_search = str(search or "").strip()
        if normalized_search:
            like = f"%{normalized_search}%"
            normalized_plate = normalize_registration_number(normalized_search)
            query = query.where(
                or_(
                    VisitorPass.visitor_name.ilike(like),
                    VisitorPass.number_plate.ilike(f"%{normalized_plate}%") if normalized_plate else False,
                    VisitorPass.vehicle_make.ilike(like),
                    VisitorPass.vehicle_colour.ilike(like),
                )
            )
        return list((await session.scalars(query)).all())

    async def get_pass(self, session: AsyncSession, pass_id: uuid.UUID) -> VisitorPass | None:
        return await session.scalar(
            select(VisitorPass)
            .options(selectinload(VisitorPass.created_by))
            .where(VisitorPass.id == pass_id)
        )

    async def claim_active_pass(
        self,
        session: AsyncSession,
        *,
        occurred_at: datetime,
        registration_number: str,
        actor: str = "System",
    ) -> VisitorPass | None:
        checked_at = _ensure_aware(occurred_at)
        await self.refresh_statuses(session=session, now=checked_at, actor=actor, publish=False)
        rows = (
            await session.scalars(
                select(VisitorPass)
                .where(VisitorPass.status == VisitorPassStatus.ACTIVE)
                .order_by(VisitorPass.expected_time, VisitorPass.created_at)
                .with_for_update()
            )
        ).all()
        visitor_pass = self.select_best_active_match(rows, checked_at)
        if not visitor_pass:
            return None

        before = visitor_pass_audit_snapshot(visitor_pass)
        visitor_pass.status = VisitorPassStatus.USED
        visitor_pass.arrival_time = checked_at
        visitor_pass.number_plate = normalize_registration_number(registration_number)
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.claim",
            actor=actor,
            before=before,
            metadata={"registration_number": visitor_pass.number_plate},
            category=TELEMETRY_CATEGORY_ACCESS,
        )
        return visitor_pass

    async def find_departure_pass(
        self,
        session: AsyncSession,
        *,
        occurred_at: datetime,
        registration_number: str,
    ) -> VisitorPass | None:
        plate = normalize_registration_number(registration_number)
        if not plate:
            return None
        checked_at = _ensure_aware(occurred_at)
        return await session.scalar(
            select(VisitorPass)
            .where(
                VisitorPass.status == VisitorPassStatus.USED,
                VisitorPass.number_plate == plate,
                VisitorPass.departure_time.is_(None),
                VisitorPass.arrival_time.is_not(None),
                VisitorPass.arrival_time <= checked_at,
            )
            .order_by(VisitorPass.arrival_time.desc(), VisitorPass.created_at.desc())
            .with_for_update()
            .limit(1)
        )

    async def record_arrival(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        event: AccessEvent,
        dvla_enrichment: dict[str, Any] | None = None,
        visual_detection: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> VisitorPass:
        before = visitor_pass_audit_snapshot(visitor_pass)
        visitor_pass.status = VisitorPassStatus.USED
        visitor_pass.arrival_time = event.occurred_at
        visitor_pass.arrival_event_id = event.id
        visitor_pass.number_plate = normalize_registration_number(event.registration_number)
        visitor_pass.telemetry_trace_id = trace_id or visitor_pass.telemetry_trace_id
        vehicle_make = _optional_text((dvla_enrichment or {}).get("make"))
        vehicle_colour = _optional_text((dvla_enrichment or {}).get("colour"))
        if not vehicle_colour and visual_detection:
            vehicle_colour = _optional_text(
                visual_detection.get("observed_vehicle_color")
                or visual_detection.get("observed_vehicle_colour")
                or visual_detection.get("vehicle_color")
                or visual_detection.get("vehicle_colour")
            )
        if vehicle_make:
            visitor_pass.vehicle_make = vehicle_make
        if vehicle_colour:
            visitor_pass.vehicle_colour = vehicle_colour
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.arrival_linked",
            actor="System",
            before=before,
            metadata={"access_event_id": str(event.id), "trace_id": trace_id},
            category=TELEMETRY_CATEGORY_ACCESS,
        )
        return visitor_pass

    async def record_departure(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        event: AccessEvent,
    ) -> VisitorPass:
        if visitor_pass.departure_time:
            return visitor_pass
        before = visitor_pass_audit_snapshot(visitor_pass)
        visitor_pass.departure_time = event.occurred_at
        visitor_pass.departure_event_id = event.id
        if visitor_pass.arrival_time:
            duration = max(0, int((_ensure_aware(event.occurred_at) - _ensure_aware(visitor_pass.arrival_time)).total_seconds()))
            visitor_pass.duration_on_site_seconds = duration
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.departure_linked",
            actor="System",
            before=before,
            metadata={"access_event_id": str(event.id)},
            category=TELEMETRY_CATEGORY_ACCESS,
        )
        return visitor_pass

    def window_start(self, visitor_pass: VisitorPass) -> datetime:
        return _ensure_aware(visitor_pass.expected_time) - timedelta(minutes=visitor_pass.window_minutes)

    def window_end(self, visitor_pass: VisitorPass) -> datetime:
        return _ensure_aware(visitor_pass.expected_time) + timedelta(minutes=visitor_pass.window_minutes)

    def is_within_window(self, visitor_pass: VisitorPass, checked_at: datetime) -> bool:
        checked = _ensure_aware(checked_at)
        return self.window_start(visitor_pass) <= checked <= self.window_end(visitor_pass)

    def select_best_active_match(
        self,
        candidates: list[VisitorPass],
        checked_at: datetime,
    ) -> VisitorPass | None:
        checked = _ensure_aware(checked_at)
        matches = [
            visitor_pass
            for visitor_pass in candidates
            if visitor_pass.status == VisitorPassStatus.ACTIVE and self.is_within_window(visitor_pass, checked)
        ]
        if not matches:
            return None
        return sorted(
            matches,
            key=lambda pass_: (
                abs((_ensure_aware(pass_.expected_time) - checked).total_seconds()),
                _ensure_aware(pass_.created_at),
            ),
        )[0]

    def status_for(self, visitor_pass: VisitorPass, checked_at: datetime) -> VisitorPassStatus:
        if visitor_pass.status in VISITOR_PASS_LOCKED_STATUSES:
            return visitor_pass.status
        return status_for_values(
            visitor_pass.expected_time,
            visitor_pass.window_minutes,
            checked_at,
        )

    async def _audit_change(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        action: str,
        actor: str,
        before: dict[str, Any],
        actor_user_id: uuid.UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
        category: str,
    ) -> None:
        await write_audit_log(
            session,
            category=category,
            action=action,
            actor=actor,
            actor_user_id=actor_user_id,
            target_entity="VisitorPass",
            target_id=visitor_pass.id,
            target_label=visitor_pass.visitor_name,
            diff=audit_diff(before, visitor_pass_audit_snapshot(visitor_pass)),
            metadata=metadata,
        )


def status_for_values(
    expected_time: datetime,
    window_minutes: int,
    checked_at: datetime,
) -> VisitorPassStatus:
    expected = _ensure_aware(expected_time)
    checked = _ensure_aware(checked_at)
    window = timedelta(minutes=_bounded_window_minutes(window_minutes))
    if checked > expected + window:
        return VisitorPassStatus.EXPIRED
    if expected - window <= checked <= expected + window:
        return VisitorPassStatus.ACTIVE
    return VisitorPassStatus.SCHEDULED


def serialize_visitor_pass(
    visitor_pass: VisitorPass,
    *,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    timezone = _timezone(timezone_name)
    duration = visitor_pass.duration_on_site_seconds
    window_start = _ensure_aware(visitor_pass.expected_time) - timedelta(minutes=visitor_pass.window_minutes)
    window_end = _ensure_aware(visitor_pass.expected_time) + timedelta(minutes=visitor_pass.window_minutes)
    created_by = _loaded_relationship(visitor_pass, "created_by")
    return {
        "id": str(visitor_pass.id),
        "visitor_name": visitor_pass.visitor_name,
        "expected_time": _datetime_iso(visitor_pass.expected_time, timezone),
        "window_minutes": visitor_pass.window_minutes,
        "window_start": _datetime_iso(window_start, timezone),
        "window_end": _datetime_iso(window_end, timezone),
        "status": visitor_pass.status.value,
        "creation_source": visitor_pass.creation_source,
        "created_by_user_id": str(visitor_pass.created_by_user_id) if visitor_pass.created_by_user_id else None,
        "created_by": _user_label(created_by) if isinstance(created_by, User) else None,
        "arrival_time": _datetime_iso(visitor_pass.arrival_time, timezone) if visitor_pass.arrival_time else None,
        "departure_time": _datetime_iso(visitor_pass.departure_time, timezone) if visitor_pass.departure_time else None,
        "number_plate": visitor_pass.number_plate,
        "vehicle_make": visitor_pass.vehicle_make,
        "vehicle_colour": visitor_pass.vehicle_colour,
        "duration_on_site_seconds": duration,
        "duration_human": human_duration_seconds(duration),
        "arrival_event_id": str(visitor_pass.arrival_event_id) if visitor_pass.arrival_event_id else None,
        "departure_event_id": str(visitor_pass.departure_event_id) if visitor_pass.departure_event_id else None,
        "telemetry_trace_id": visitor_pass.telemetry_trace_id,
        "created_at": _datetime_iso(visitor_pass.created_at, timezone),
        "updated_at": _datetime_iso(visitor_pass.updated_at, timezone),
    }


def _loaded_relationship(instance: Any, relationship_name: str) -> Any:
    state = sqlalchemy_inspect(instance)
    if relationship_name in state.unloaded:
        return None
    return getattr(instance, relationship_name, None)


def visitor_pass_audit_snapshot(visitor_pass: VisitorPass) -> dict[str, Any]:
    return {
        "id": str(visitor_pass.id) if visitor_pass.id else None,
        "visitor_name": visitor_pass.visitor_name,
        "expected_time": _ensure_aware(visitor_pass.expected_time).isoformat() if visitor_pass.expected_time else None,
        "window_minutes": visitor_pass.window_minutes,
        "status": visitor_pass.status.value if visitor_pass.status else None,
        "creation_source": visitor_pass.creation_source,
        "created_by_user_id": str(visitor_pass.created_by_user_id) if visitor_pass.created_by_user_id else None,
        "arrival_time": _ensure_aware(visitor_pass.arrival_time).isoformat() if visitor_pass.arrival_time else None,
        "departure_time": _ensure_aware(visitor_pass.departure_time).isoformat() if visitor_pass.departure_time else None,
        "number_plate": visitor_pass.number_plate,
        "vehicle_make": visitor_pass.vehicle_make,
        "vehicle_colour": visitor_pass.vehicle_colour,
        "duration_on_site_seconds": visitor_pass.duration_on_site_seconds,
        "arrival_event_id": str(visitor_pass.arrival_event_id) if visitor_pass.arrival_event_id else None,
        "departure_event_id": str(visitor_pass.departure_event_id) if visitor_pass.departure_event_id else None,
        "telemetry_trace_id": visitor_pass.telemetry_trace_id,
    }


async def serialize_visitor_pass_with_runtime(visitor_pass: VisitorPass) -> dict[str, Any]:
    runtime = await get_runtime_config()
    return serialize_visitor_pass(visitor_pass, timezone_name=runtime.site_timezone)


def human_duration_seconds(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _datetime_iso(value: datetime, timezone: ZoneInfo) -> str:
    return _ensure_aware(value).astimezone(timezone).isoformat()


def _timezone(timezone_name: str | None) -> ZoneInfo:
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            pass
    return ZoneInfo("UTC")


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _clean_visitor_name(value: str) -> str:
    name = " ".join(str(value or "").strip().split())
    if not name:
        raise VisitorPassError("Visitor name is required.")
    if len(name) > 160:
        raise VisitorPassError("Visitor name must be 160 characters or fewer.")
    return name


def _bounded_window_minutes(value: int | None) -> int:
    try:
        minutes = int(value or DEFAULT_WINDOW_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_WINDOW_MINUTES
    return max(1, min(minutes, MAX_WINDOW_MINUTES))


def _clean_source(value: str) -> str:
    source = str(value or "ui").strip().lower().replace(" ", "_")
    return source[:80] or "ui"


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _user_label(user: User) -> str:
    return actor_from_user(user)


@lru_cache
def get_visitor_pass_service() -> VisitorPassService:
    return VisitorPassService()
