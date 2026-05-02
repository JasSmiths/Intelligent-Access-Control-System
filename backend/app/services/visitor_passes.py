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
from app.models.enums import VisitorPassStatus, VisitorPassType
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
VISITOR_PASS_WHATSAPP_HISTORY_KEY = "whatsapp_chat_history"
VISITOR_PASS_WHATSAPP_HISTORY_LIMIT = 250
VISITOR_PASS_WHATSAPP_STATUS_LABELS = {
    "welcome_message_sent": "Welcome Message Sent",
    "message_received": "Message Received",
    "message_read": "Message Read",
    "visitor_replied": "Visitor Replied",
    "awaiting_visitor_reply": "Awaiting Visitor Reply",
    "message_sending_failed": "Message Sending Failed",
    "user_not_on_whatsapp": "User Not On WhatsApp",
    "timeframe_approval_pending": "Awaiting Time Change Approval",
    "timeframe_confirmation_pending": "Requested Time Change",
    "timeframe_approved": "Timeframe Change Approved",
    "timeframe_denied": "Timeframe Change Denied",
}


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
        expected_time: datetime | None = None,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
        pass_type: VisitorPassType | str = VisitorPassType.ONE_TIME,
        visitor_phone: str | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        source: str = "ui",
        source_reference: str | None = None,
        source_metadata: dict[str, Any] | None = None,
        created_by_user_id: uuid.UUID | str | None = None,
        actor: str = "System",
    ) -> VisitorPass:
        name = _clean_visitor_name(visitor_name)
        normalized_pass_type = _visitor_pass_type(pass_type)
        normalized_phone = _normalize_phone_number(visitor_phone)
        window = _bounded_window_minutes(window_minutes)
        explicit_valid_from, explicit_valid_until = _valid_window(valid_from, valid_until)
        if normalized_pass_type == VisitorPassType.DURATION:
            if not normalized_phone:
                raise VisitorPassError("Visitor phone is required for duration Visitor Passes.")
            if explicit_valid_from is None or explicit_valid_until is None:
                raise VisitorPassError("Duration Visitor Passes require valid_from and valid_until.")
            expected = _ensure_aware(expected_time) if expected_time else explicit_valid_from
        else:
            if expected_time is None:
                raise VisitorPassError("Expected time is required for one-time Visitor Passes.")
            expected = _ensure_aware(expected_time)
        metadata = dict(source_metadata or {})
        if normalized_pass_type == VisitorPassType.DURATION and normalized_phone:
            metadata.setdefault("whatsapp_concierge_status", "awaiting_visitor_reply")
            metadata.setdefault(
                "whatsapp_concierge_status_detail",
                "Waiting for the visitor to reply with their vehicle registration.",
            )
        visitor_pass = VisitorPass(
            visitor_name=name,
            pass_type=normalized_pass_type,
            visitor_phone=normalized_phone,
            expected_time=expected,
            window_minutes=window,
            valid_from=explicit_valid_from,
            valid_until=explicit_valid_until,
            status=status_for_values(
                expected,
                window,
                datetime.now(tz=UTC),
                valid_from=explicit_valid_from,
                valid_until=explicit_valid_until,
            ),
            creation_source=_clean_source(source),
            source_reference=_optional_text(source_reference),
            source_metadata=metadata or None,
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
        pass_type: VisitorPassType | str | None = None,
        visitor_phone: str | None = None,
        visitor_phone_provided: bool | None = None,
        valid_from: datetime | None = None,
        valid_from_provided: bool | None = None,
        valid_until: datetime | None = None,
        valid_until_provided: bool | None = None,
        source_metadata: dict[str, Any] | None = None,
        actor: str = "System",
        actor_user_id: uuid.UUID | str | None = None,
    ) -> VisitorPass:
        if visitor_pass.status in VISITOR_PASS_LOCKED_STATUSES:
            raise VisitorPassError(f"{visitor_pass.status.value.title()} visitor passes cannot be edited.")
        before = visitor_pass_audit_snapshot(visitor_pass)
        next_pass_type = _visitor_pass_type(pass_type) if pass_type is not None else visitor_pass.pass_type
        visitor_phone_was_provided = (
            visitor_phone is not None if visitor_phone_provided is None else visitor_phone_provided
        )
        valid_from_was_provided = valid_from is not None if valid_from_provided is None else valid_from_provided
        valid_until_was_provided = valid_until is not None if valid_until_provided is None else valid_until_provided
        next_phone = (
            _normalize_phone_number(visitor_phone)
            if visitor_phone_was_provided
            else visitor_pass.visitor_phone
        )
        next_valid_from = valid_from if valid_from_was_provided else visitor_pass.valid_from
        next_valid_until = valid_until if valid_until_was_provided else visitor_pass.valid_until
        if pass_type is not None and next_pass_type == VisitorPassType.ONE_TIME and visitor_pass.pass_type == VisitorPassType.DURATION:
            next_phone = _normalize_phone_number(visitor_phone) if visitor_phone_was_provided else None
            next_valid_from = valid_from if valid_from_was_provided else None
            next_valid_until = valid_until if valid_until_was_provided else None
        next_valid_from, next_valid_until = _valid_window(
            next_valid_from,
            next_valid_until,
            require_pair=next_pass_type == VisitorPassType.DURATION,
        )
        if next_pass_type == VisitorPassType.DURATION:
            if not next_phone:
                raise VisitorPassError("Visitor phone is required for duration Visitor Passes.")
            if next_valid_from is None or next_valid_until is None:
                raise VisitorPassError("Duration Visitor Passes require valid_from and valid_until.")
        visitor_pass.pass_type = next_pass_type
        visitor_pass.visitor_phone = next_phone
        visitor_pass.valid_from = next_valid_from
        visitor_pass.valid_until = next_valid_until
        if visitor_name is not None:
            visitor_pass.visitor_name = _clean_visitor_name(visitor_name)
        if expected_time is not None:
            visitor_pass.expected_time = _ensure_aware(expected_time)
        elif visitor_pass.pass_type == VisitorPassType.DURATION and visitor_pass.valid_from:
            visitor_pass.expected_time = _ensure_aware(visitor_pass.valid_from)
        if window_minutes is not None:
            visitor_pass.window_minutes = _bounded_window_minutes(window_minutes)
        if source_metadata is not None:
            visitor_pass.source_metadata = source_metadata or None
        visitor_pass.status = status_for_values(
            visitor_pass.expected_time,
            visitor_pass.window_minutes,
            datetime.now(tz=UTC),
            valid_from=visitor_pass.valid_from,
            valid_until=visitor_pass.valid_until,
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

    async def delete_pass(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        actor: str = "System",
        actor_user_id: uuid.UUID | str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        before = visitor_pass_audit_snapshot(visitor_pass)
        await write_audit_log(
            session,
            category=TELEMETRY_CATEGORY_CRUD,
            action="visitor_pass.delete",
            actor=actor,
            actor_user_id=_coerce_uuid(actor_user_id),
            target_entity="VisitorPass",
            target_id=visitor_pass.id,
            target_label=visitor_pass.visitor_name,
            diff=audit_diff(before, {}),
            metadata={"reason": reason},
        )
        await session.delete(visitor_pass)
        return before

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
            normalized_phone = _normalize_phone_number(normalized_search)
            query = query.where(
                or_(
                    VisitorPass.visitor_name.ilike(like),
                    VisitorPass.number_plate.ilike(f"%{normalized_plate}%") if normalized_plate else False,
                    VisitorPass.visitor_phone.ilike(f"%{normalized_phone}%") if normalized_phone else False,
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

    async def messaging_pass_for_phone(
        self,
        session: AsyncSession,
        phone_number: str,
        *,
        now: datetime | None = None,
    ) -> tuple[VisitorPass | None, str]:
        phone = _normalize_phone_number(phone_number)
        if not phone:
            return None, "not_found"
        checked_at = _ensure_aware(now or datetime.now(tz=UTC))
        await self.refresh_statuses(session=session, now=checked_at, publish=False)
        rows = (
            await session.scalars(
                select(VisitorPass)
                .where(
                    VisitorPass.pass_type == VisitorPassType.DURATION,
                    VisitorPass.visitor_phone == phone,
                )
                .order_by(
                    VisitorPass.valid_from.asc().nulls_last(),
                    VisitorPass.expected_time.asc(),
                    VisitorPass.created_at.desc(),
                )
            )
        ).all()
        eligible = [
            visitor_pass
            for visitor_pass in rows
            if visitor_pass.status in VISITOR_PASS_ACTIVE_STATUSES
        ]
        active = [
            visitor_pass
            for visitor_pass in eligible
            if self.is_within_window(visitor_pass, checked_at)
        ]
        if active:
            return self.select_best_active_match(active, checked_at), "active"
        if eligible:
            return sorted(eligible, key=lambda pass_: _ensure_aware(pass_.expected_time))[0], "scheduled"
        if rows:
            return rows[-1], "expired"
        return None, "not_found"

    async def update_visitor_plate(
        self,
        session: AsyncSession,
        visitor_pass: VisitorPass,
        *,
        new_plate: str,
        vehicle_make: str | None = None,
        vehicle_colour: str | None = None,
        actor: str = "Visitor Concierge",
        metadata: dict[str, Any] | None = None,
    ) -> VisitorPass:
        if visitor_pass.status not in VISITOR_PASS_ACTIVE_STATUSES:
            raise VisitorPassError(f"{visitor_pass.status.value.title()} visitor passes cannot be updated.")
        plate = normalize_registration_number(new_plate)
        if not plate:
            raise VisitorPassError("A valid vehicle registration is required.")
        before = visitor_pass_audit_snapshot(visitor_pass)
        previous_plate = visitor_pass.number_plate
        plate_changed = bool(previous_plate and previous_plate != plate)
        make = _optional_text(vehicle_make)
        colour = _optional_text(vehicle_colour)
        visitor_pass.number_plate = plate
        if make:
            visitor_pass.vehicle_make = make
        elif plate_changed:
            visitor_pass.vehicle_make = None
        if colour:
            visitor_pass.vehicle_colour = colour
        elif plate_changed:
            visitor_pass.vehicle_colour = None
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.vehicle_plate_update",
            actor=actor,
            before=before,
            metadata=metadata or {"source": "visitor_concierge"},
            category=TELEMETRY_CATEGORY_CRUD,
        )
        return visitor_pass

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
        normalized_registration = normalize_registration_number(registration_number)
        rows = [
            visitor_pass
            for visitor_pass in rows
            if (
                visitor_pass.pass_type != VisitorPassType.DURATION
                or (
                    bool(visitor_pass.number_plate)
                    and visitor_pass.number_plate == normalized_registration
                )
            )
        ]
        visitor_pass = self.select_best_active_match(rows, checked_at)
        if not visitor_pass:
            return None

        before = visitor_pass_audit_snapshot(visitor_pass)
        self._apply_arrival_state(visitor_pass, checked_at)
        if not visitor_pass.number_plate:
            visitor_pass.number_plate = normalized_registration
        await self._audit_change(
            session,
            visitor_pass,
            action="visitor_pass.claim",
            actor=actor,
            before=before,
            metadata={"registration_number": normalized_registration},
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
                or_(
                    VisitorPass.status == VisitorPassStatus.USED,
                    (
                        (VisitorPass.pass_type == VisitorPassType.DURATION)
                        & (VisitorPass.status == VisitorPassStatus.ACTIVE)
                    ),
                ),
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
        arrival_linked = self._apply_arrival_state(visitor_pass, event.occurred_at, arrival_event_id=event.id)
        if not visitor_pass.number_plate:
            visitor_pass.number_plate = normalize_registration_number(event.registration_number)
        if arrival_linked:
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

    def _apply_arrival_state(
        self,
        visitor_pass: VisitorPass,
        occurred_at: datetime,
        *,
        arrival_event_id: uuid.UUID | None = None,
    ) -> bool:
        occurred_at = _ensure_aware(occurred_at)
        if visitor_pass.pass_type != VisitorPassType.DURATION:
            visitor_pass.status = VisitorPassStatus.USED
            visitor_pass.arrival_time = occurred_at
            if arrival_event_id is not None:
                visitor_pass.arrival_event_id = arrival_event_id
            return True

        visit_is_open = visitor_pass.arrival_time is not None and visitor_pass.departure_time is None
        if visit_is_open:
            if visitor_pass.arrival_event_id is None and arrival_event_id is not None:
                visitor_pass.arrival_event_id = arrival_event_id
                return True
            return False

        visitor_pass.arrival_time = occurred_at
        if arrival_event_id is not None:
            visitor_pass.arrival_event_id = arrival_event_id
        visitor_pass.departure_time = None
        visitor_pass.departure_event_id = None
        visitor_pass.duration_on_site_seconds = None
        return True

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
        if visitor_pass.valid_from:
            return _ensure_aware(visitor_pass.valid_from)
        return _ensure_aware(visitor_pass.expected_time) - timedelta(minutes=visitor_pass.window_minutes)

    def window_end(self, visitor_pass: VisitorPass) -> datetime:
        if visitor_pass.valid_until:
            return _ensure_aware(visitor_pass.valid_until)
        return _ensure_aware(visitor_pass.expected_time) + timedelta(minutes=visitor_pass.window_minutes)

    def is_within_window(self, visitor_pass: VisitorPass, checked_at: datetime) -> bool:
        checked = _ensure_aware(checked_at)
        if visitor_pass.valid_until:
            return self.window_start(visitor_pass) <= checked < self.window_end(visitor_pass)
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
            valid_from=visitor_pass.valid_from,
            valid_until=visitor_pass.valid_until,
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
    *,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> VisitorPassStatus:
    expected = _ensure_aware(expected_time)
    checked = _ensure_aware(checked_at)
    explicit_valid_from, explicit_valid_until = _valid_window(valid_from, valid_until, require_pair=False)
    if explicit_valid_from and explicit_valid_until:
        if checked >= explicit_valid_until:
            return VisitorPassStatus.EXPIRED
        if explicit_valid_from <= checked < explicit_valid_until:
            return VisitorPassStatus.ACTIVE
        return VisitorPassStatus.SCHEDULED
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
    window_start = _window_start_for_pass(visitor_pass)
    window_end = _window_end_for_pass(visitor_pass)
    created_by = _loaded_relationship(visitor_pass, "created_by")
    whatsapp_status = visitor_pass_whatsapp_status_payload(visitor_pass)
    return {
        "id": str(visitor_pass.id),
        "visitor_name": visitor_pass.visitor_name,
        "pass_type": visitor_pass.pass_type.value,
        "visitor_phone": visitor_pass.visitor_phone,
        "expected_time": _datetime_iso(visitor_pass.expected_time, timezone),
        "window_minutes": visitor_pass.window_minutes,
        "window_start": _datetime_iso(window_start, timezone),
        "window_end": _datetime_iso(window_end, timezone),
        "valid_from": _datetime_iso(visitor_pass.valid_from, timezone) if visitor_pass.valid_from else None,
        "valid_until": _datetime_iso(visitor_pass.valid_until, timezone) if visitor_pass.valid_until else None,
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
        "source_reference": visitor_pass.source_reference,
        "source_metadata": visitor_pass.source_metadata or None,
        "whatsapp_status": whatsapp_status["status"],
        "whatsapp_status_label": whatsapp_status["label"],
        "whatsapp_status_detail": whatsapp_status["detail"],
        "created_at": _datetime_iso(visitor_pass.created_at, timezone),
        "updated_at": _datetime_iso(visitor_pass.updated_at, timezone),
    }


def visitor_pass_whatsapp_status_payload(visitor_pass: VisitorPass) -> dict[str, str | None]:
    if visitor_pass.pass_type != VisitorPassType.DURATION or not visitor_pass.visitor_phone:
        return {"status": None, "label": None, "detail": None}
    metadata = visitor_pass.source_metadata if isinstance(visitor_pass.source_metadata, dict) else {}
    raw_status = str(metadata.get("whatsapp_concierge_status") or "").strip()
    detail = str(metadata.get("whatsapp_concierge_status_detail") or "").strip()
    error = str(metadata.get("whatsapp_last_error") or "").strip()
    status = raw_status or "awaiting_visitor_reply"
    if visitor_pass.number_plate and status not in {
        "message_sending_failed",
        "user_not_on_whatsapp",
        "failed",
        "timeframe_approval_pending",
        "timeframe_confirmation_pending",
        "timeframe_denied",
    }:
        status = "complete"
    if status == "complete":
        time_updated = visitor_pass_whatsapp_time_was_updated(metadata)
        suffix = " Time Updated" if time_updated else ""
        label = f"Complete - Vehicle Registration: {visitor_pass.number_plate or 'Pending'}{suffix}"
    elif status == "failed":
        failure = error or detail or "Unknown error"
        label = f"Failed: {failure}"
    else:
        label = VISITOR_PASS_WHATSAPP_STATUS_LABELS.get(status, status.replace("_", " ").title())
    return {"status": status, "label": label, "detail": detail or error or None}


def visitor_pass_whatsapp_time_was_updated(metadata: dict[str, Any]) -> bool:
    confirmation = metadata.get("whatsapp_timeframe_confirmation")
    if isinstance(confirmation, dict) and str(confirmation.get("status") or "") == "confirmed":
        return True
    last_change = metadata.get("whatsapp_timeframe_last_change")
    if isinstance(last_change, dict) and str(last_change.get("status") or "") in {"visitor_confirmed", "admin_approved"}:
        return True
    request = metadata.get("whatsapp_timeframe_request")
    return isinstance(request, dict) and str(request.get("status") or "") == "approved"


def visitor_pass_whatsapp_history(visitor_pass: VisitorPass) -> list[dict[str, Any]]:
    metadata = visitor_pass.source_metadata if isinstance(visitor_pass.source_metadata, dict) else {}
    raw_history = metadata.get(VISITOR_PASS_WHATSAPP_HISTORY_KEY)
    if not isinstance(raw_history, list):
        return []
    messages = [_normalize_whatsapp_history_entry(item) for item in raw_history]
    return sorted(
        [message for message in messages if message],
        key=lambda item: str(item.get("created_at") or ""),
    )[-VISITOR_PASS_WHATSAPP_HISTORY_LIMIT:]


def append_visitor_pass_whatsapp_history(
    visitor_pass: VisitorPass,
    *,
    direction: str,
    body: str,
    kind: str = "text",
    actor_label: str | None = None,
    provider_message_id: str | None = None,
    status: str | None = None,
    occurred_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized_body = str(body or "").strip()
    if not normalized_body:
        return None
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"inbound", "outbound", "status"}:
        normalized_direction = "status"
    entry = {
        "id": uuid.uuid4().hex,
        "direction": normalized_direction,
        "kind": str(kind or "text").strip().lower()[:40] or "text",
        "body": normalized_body[:4096],
        "actor_label": str(actor_label or ("Visitor" if normalized_direction == "inbound" else "IACS")).strip()[:120],
        "provider_message_id": _optional_text(provider_message_id),
        "status": _optional_text(status),
        "created_at": _ensure_aware(occurred_at or datetime.now(tz=UTC)).isoformat(),
        "metadata": metadata or None,
    }
    history = [*visitor_pass_whatsapp_history(visitor_pass), entry][-VISITOR_PASS_WHATSAPP_HISTORY_LIMIT:]
    visitor_pass.source_metadata = {
        **(visitor_pass.source_metadata if isinstance(visitor_pass.source_metadata, dict) else {}),
        VISITOR_PASS_WHATSAPP_HISTORY_KEY: history,
    }
    return entry


def _normalize_whatsapp_history_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    body = str(value.get("body") or "").strip()
    created_at = str(value.get("created_at") or "").strip()
    if not body or not created_at:
        return None
    direction = str(value.get("direction") or "").strip().lower()
    if direction not in {"inbound", "outbound", "status"}:
        direction = "status"
    return {
        "id": str(value.get("id") or uuid.uuid4().hex),
        "direction": direction,
        "kind": str(value.get("kind") or "text").strip().lower()[:40] or "text",
        "body": body[:4096],
        "actor_label": str(value.get("actor_label") or ("Visitor" if direction == "inbound" else "IACS")).strip()[:120],
        "provider_message_id": str(value.get("provider_message_id") or "").strip() or None,
        "status": str(value.get("status") or "").strip() or None,
        "created_at": created_at,
        "metadata": value.get("metadata") if isinstance(value.get("metadata"), dict) else None,
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
        "pass_type": visitor_pass.pass_type.value if visitor_pass.pass_type else None,
        "visitor_phone": visitor_pass.visitor_phone,
        "expected_time": _ensure_aware(visitor_pass.expected_time).isoformat() if visitor_pass.expected_time else None,
        "window_minutes": visitor_pass.window_minutes,
        "valid_from": _ensure_aware(visitor_pass.valid_from).isoformat() if visitor_pass.valid_from else None,
        "valid_until": _ensure_aware(visitor_pass.valid_until).isoformat() if visitor_pass.valid_until else None,
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
        "source_reference": visitor_pass.source_reference,
        "source_metadata": visitor_pass.source_metadata,
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


def _window_start_for_pass(visitor_pass: VisitorPass) -> datetime:
    if visitor_pass.valid_from:
        return _ensure_aware(visitor_pass.valid_from)
    return _ensure_aware(visitor_pass.expected_time) - timedelta(minutes=visitor_pass.window_minutes)


def _window_end_for_pass(visitor_pass: VisitorPass) -> datetime:
    if visitor_pass.valid_until:
        return _ensure_aware(visitor_pass.valid_until)
    return _ensure_aware(visitor_pass.expected_time) + timedelta(minutes=visitor_pass.window_minutes)


def _valid_window(
    valid_from: datetime | None,
    valid_until: datetime | None,
    *,
    require_pair: bool = True,
) -> tuple[datetime | None, datetime | None]:
    if valid_from is None and valid_until is None:
        return None, None
    if valid_from is None or valid_until is None:
        if require_pair:
            raise VisitorPassError("Both valid_from and valid_until are required for explicit Visitor Pass windows.")
        return None, None
    starts_at = _ensure_aware(valid_from)
    ends_at = _ensure_aware(valid_until)
    if ends_at <= starts_at:
        raise VisitorPassError("Visitor Pass valid_until must be after valid_from.")
    return starts_at, ends_at


def _visitor_pass_type(value: VisitorPassType | str | None) -> VisitorPassType:
    if isinstance(value, VisitorPassType):
        return value
    normalized = str(value or VisitorPassType.ONE_TIME.value).strip().lower().replace("_", "-")
    try:
        return VisitorPassType(normalized)
    except ValueError as exc:
        raise VisitorPassError("Visitor Pass type must be one-time or duration.") from exc


def _normalize_phone_number(value: Any) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits or None


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
