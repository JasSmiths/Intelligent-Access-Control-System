"""Calendar event-to-pass policy and coverage-limited account reconciliation."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ICloudCalendarAccount, VisitorPass
from app.models.enums import VisitorPassStatus
from app.modules.icloud_calendar.contracts import (
    CalendarSnapshot,
    CalendarWork,
    ICloudCalendarEvent,
)
from app.services.calendar_sync_commit import CalendarAccountApply
from app.services.calendar_sync_contracts import (
    CalendarAccountCheckpoint,
    CalendarAccountClaim,
    CalendarRunContext,
)
from app.services.calendar_sync_store import calendar_sql_budget
from app.services.visitor_passes import (
    DEFAULT_WINDOW_MINUTES,
    VISITOR_PASS_ACTIVE_STATUSES,
    VisitorPassError,
    VisitorPassService,
    get_visitor_pass_service,
    serialize_visitor_pass,
    visitor_pass_audit_snapshot,
)

ICLOUD_CALENDAR_SOURCE = "icloud_calendar"
ICLOUD_OPEN_GATE_MARKER = "Open Gate"
ICLOUD_VISITOR_NAME_MAX_CHARS = 160

@dataclass(frozen=True)
class CalendarVisitorName:
    visitor_name: str
    source: str


def event_contains_open_gate(event: ICloudCalendarEvent) -> bool:
    text = f"{event.notes}\n{event.description}"
    return bool(re.search(r"\bopen\s+gate\b", text, flags=re.IGNORECASE))


def visitor_window_for_event(event: ICloudCalendarEvent) -> tuple[datetime, datetime]:
    return event.starts_at - timedelta(minutes=DEFAULT_WINDOW_MINUTES), event.ends_at


def source_reference_for_event(account_id: uuid.UUID | str, event: ICloudCalendarEvent) -> str:
    return f"icloud:{account_id}:{event.calendar_id}:{event.event_id}"[:255]


async def calendar_visitor_name_for_event(event: ICloudCalendarEvent) -> CalendarVisitorName:
    return CalendarVisitorName(
        visitor_name=fallback_visitor_name_from_calendar_title(event.title), source="fallback"
    )


def fallback_visitor_name_from_calendar_title(title: str) -> str:
    clean_title = _clean_calendar_visitor_name(title)
    if not clean_title:
        return "Calendar Visitor"

    separator_patterns = (
        r":",
        r"\s+-\s+",
        r"\s+[\u2013\u2014]\s+",
        r"\s+\|\s+",
        r"\s+/\s+",
    )
    for pattern in separator_patterns:
        parts = [
            _clean_calendar_visitor_name(part)
            for part in re.split(pattern, clean_title)
            if part.strip()
        ]
        if len(parts) < 2:
            continue
        candidate = parts[-1]
        if _looks_like_calendar_visitor_name(candidate):
            return candidate

    for pattern in (
        r"\b(?:for|with|visitor)\s+(.+)$",
        r"\b(?:appointment|meeting|clinic)\s+(.+)$",
    ):
        match = re.search(pattern, clean_title, flags=re.IGNORECASE)
        if match:
            candidate = _clean_calendar_visitor_name(match.group(1))
            if _looks_like_calendar_visitor_name(candidate):
                return candidate

    return clean_title[:ICLOUD_VISITOR_NAME_MAX_CHARS]


def source_metadata_for_event(
    account: ICloudCalendarAccount,
    event: ICloudCalendarEvent,
    *,
    visitor_name: str | None = None,
    visitor_name_source: str | None = None,
) -> dict[str, Any]:
    metadata = {
        "account_id": str(account.id),
        "apple_id": account.apple_id,
        "calendar_id": event.calendar_id,
        "event_id": event.event_id,
        "event_title": event.title,
        "event_start": event.starts_at.isoformat(),
        "event_end": event.ends_at.isoformat(),
        "marker": ICLOUD_OPEN_GATE_MARKER,
    }
    if visitor_name:
        metadata["visitor_name"] = visitor_name
    if visitor_name_source:
        metadata["visitor_name_source"] = visitor_name_source
    return metadata


def calendar_pass_can_be_reconciled(visitor_pass: VisitorPass) -> bool:
    return visitor_pass.creation_source == ICLOUD_CALENDAR_SOURCE and visitor_pass.status in {
        VisitorPassStatus.ACTIVE,
        VisitorPassStatus.SCHEDULED,
    }


def _clean_calendar_visitor_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    cleaned = cleaned.strip(" \t\r\n'\"`")
    cleaned = re.sub(
        r"^(?:visitor_name|visitor name|person|name)\s*[:=-]\s*", "", cleaned, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"\bopen\s+gate\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" \t\r\n'\"`:-|/.,;\u2013\u2014")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned.lower() in {"unknown", "none", "n/a", "na", "not sure", "no name"}:
        return ""
    if len(cleaned) <= ICLOUD_VISITOR_NAME_MAX_CHARS:
        return cleaned
    return (
        cleaned[:ICLOUD_VISITOR_NAME_MAX_CHARS].rsplit(" ", 1)[0].strip()
        or cleaned[:ICLOUD_VISITOR_NAME_MAX_CHARS]
    )


def _looks_like_calendar_visitor_name(value: str) -> bool:
    cleaned = _clean_calendar_visitor_name(value)
    if not cleaned:
        return False
    if cleaned.lower() in {"clinic", "appointment", "meeting", "reminder", "calendar", "visitor"}:
        return False
    return bool(re.search(r"[A-Za-z]", cleaned))


async def upsert_calendar_event_pass(
    session: AsyncSession,
    visitor_service: VisitorPassService,
    *,
    account: ICloudCalendarAccount,
    event: ICloudCalendarEvent,
    source_reference: str,
    actor: str,
    actor_user_id: uuid.UUID | str | None,
) -> tuple[str, str | None, dict[str, Any] | None]:
    existing = await session.scalar(
        select(VisitorPass)
        .where(VisitorPass.source_reference == source_reference)
        .with_for_update()
        .limit(1)
    )
    valid_from, valid_until = visitor_window_for_event(event)
    extracted_name = await calendar_visitor_name_for_event(event)
    metadata = source_metadata_for_event(
        account,
        event,
        visitor_name=extracted_name.visitor_name,
        visitor_name_source=extracted_name.source,
    )
    if existing is None:
        visitor_pass = await visitor_service.create_pass(
            session,
            visitor_name=extracted_name.visitor_name,
            expected_time=event.starts_at,
            window_minutes=DEFAULT_WINDOW_MINUTES,
            valid_from=valid_from,
            valid_until=valid_until,
            source=ICLOUD_CALENDAR_SOURCE,
            source_reference=source_reference,
            source_metadata=metadata,
            created_by_user_id=actor_user_id,
            actor=actor,
        )
        await session.flush()
        return (
            "created",
            "visitor_pass.created",
            {
                "visitor_pass": serialize_visitor_pass(visitor_pass),
                "source": ICLOUD_CALENDAR_SOURCE,
            },
        )

    if not calendar_pass_can_be_reconciled(existing):
        return "skipped", None, None

    before = visitor_pass_audit_snapshot(existing)
    await visitor_service.update_pass(
        session,
        existing,
        visitor_name=extracted_name.visitor_name,
        expected_time=event.starts_at,
        window_minutes=DEFAULT_WINDOW_MINUTES,
        valid_from=valid_from,
        valid_until=valid_until,
        source_metadata=metadata,
        actor=actor,
        actor_user_id=actor_user_id,
    )
    await session.flush()
    if before == visitor_pass_audit_snapshot(existing):
        return "skipped", None, None
    # Generic visitor update may leave its server on-update timestamp expired.
    # Hydrate it before the atomic account commit and synchronous receipt build.
    await session.refresh(existing, attribute_names=["updated_at"])
    return (
        "updated",
        "visitor_pass.updated",
        {
            "visitor_pass": serialize_visitor_pass(existing),
            "source": ICLOUD_CALENDAR_SOURCE,
        },
    )



def calendar_pass_in_coverage(visitor_pass: VisitorPass, snapshot: CalendarSnapshot) -> bool:
    """Malformed or missing historical coverage never authorizes absence cancellation."""
    metadata = visitor_pass.source_metadata
    value = metadata.get("event_start") if isinstance(metadata, dict) else None
    if not isinstance(value, str):
        return False
    try:
        starts_at = datetime.fromisoformat(value)
    except ValueError:
        return False
    if starts_at.utcoffset() is None:
        return False
    return bool(snapshot.coverage.effective_start <= starts_at.astimezone(UTC) < snapshot.coverage.effective_end)


class CalendarAccountReconciliation:
    def __init__(
        self, claim: CalendarAccountClaim, snapshot: CalendarSnapshot,
        *, work: CalendarWork, actor: str, actor_user_id: uuid.UUID | None,
    ) -> None:
        self.claim, self.snapshot, self.work = claim, snapshot, work
        self.actor, self.actor_user_id = actor, actor_user_id

    async def __call__(
        self, session: AsyncSession, account: ICloudCalendarAccount, context: CalendarRunContext,
    ) -> CalendarAccountApply:
        self.work.check()
        if self.snapshot.coverage != context.coverage:
            raise ValueError("Calendar snapshot does not prove this run's fixed coverage.")
        visitor_service = get_visitor_pass_service()
        seen: set[str] = set()
        events: list[tuple[str, dict[str, Any]]] = []
        matched = created = updated = cancelled = skipped = historical = 0
        for event in self.snapshot.events:
            self.work.check()
            if not event_contains_open_gate(event):
                continue
            matched += 1
            reference = source_reference_for_event(account.id, event)
            seen.add(reference)
            await calendar_sql_budget(session, self.work)
            try:
                change, event_type, payload = await upsert_calendar_event_pass(
                    session, visitor_service, account=account, event=event,
                    source_reference=reference, actor=self.actor, actor_user_id=self.actor_user_id)
            except VisitorPassError:
                skipped += 1
                continue
            created += change == "created"
            updated += change == "updated"
            skipped += change == "skipped"
            if event_type is not None and payload is not None:
                events.append((event_type, payload))
        # Reconcile the complete active account scope in bounded mutation
        # batches; never rebuild an unbounded in-memory candidate list.
        after: uuid.UUID | None = None
        while True:
            await calendar_sql_budget(session, self.work)
            query = select(VisitorPass).where(
                VisitorPass.creation_source == ICLOUD_CALENDAR_SOURCE,
                VisitorPass.source_reference.like(f"icloud:{account.id}:%"),
                VisitorPass.status.in_(VISITOR_PASS_ACTIVE_STATUSES))
            if after is not None:
                query = query.where(VisitorPass.id > after)
            candidates = (await session.scalars(query.order_by(VisitorPass.id).limit(100).with_for_update())).all()
            if not candidates:
                break
            after = candidates[-1].id
            for visitor_pass in candidates:
                self.work.check()
                if visitor_pass.source_reference in seen:
                    continue
                if not calendar_pass_in_coverage(visitor_pass, self.snapshot):
                    historical += 1
                    continue
                await calendar_sql_budget(session, self.work)
                await visitor_service.cancel_pass(session, visitor_pass,
                    actor=self.actor, actor_user_id=self.actor_user_id,
                    reason="iCloud Calendar event no longer contains Open Gate or is absent from its complete fixed sync coverage.")
                await session.flush()
                cancelled += 1
                events.append(("visitor_pass.cancelled", {"visitor_pass": serialize_visitor_pass(visitor_pass),
                    "source": ICLOUD_CALENDAR_SOURCE}))
        return CalendarAccountApply(CalendarAccountCheckpoint(
            account_id=account.id, apple_id=account.apple_id, display_name=account.display_name or account.apple_id,
            attempt_token=self.claim.attempt_token, status="ok", events_scanned=len(self.snapshot.events),
            events_matched=matched, passes_created=created, passes_updated=updated,
            passes_cancelled=cancelled, passes_skipped=skipped, historical_coverage_skipped=historical), tuple(events))
