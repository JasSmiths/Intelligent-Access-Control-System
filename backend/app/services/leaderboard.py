import asyncio
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, LeaderboardState, Person, Vehicle
from app.models.enums import AccessDecision, AccessDirection, AnomalySeverity
from app.modules.dvla.vehicle_enquiry import (
    DvlaVehicleEnquiryError,
    display_vehicle_record,
)
from app.modules.notifications.base import NotificationContext
from app.services.dvla import lookup_vehicle_registration, normalize_vehicle_enquiry_response
from app.services.event_bus import event_bus
from app.services.notifications import get_notification_service

logger = get_logger(__name__)

KNOWN_TOP_STATE_KEY = "known_top_plate"
UNKNOWN_DVLA_CONCURRENCY = 4


class LeaderboardService:
    """Aggregates plate-read leaderboards and tracks the known-plate top spot."""

    async def get_leaderboard(self, *, limit: int = 25, enrich_unknowns: bool = True) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        async with AsyncSessionLocal() as session:
            known = await self._known_leaders(session, limit)
            unknown = await self._unknown_leaders(session, limit)

        if enrich_unknowns:
            unknown = await self._enrich_unknowns(unknown)
        else:
            unknown = [
                {**row, "dvla": {"status": "skipped", "vehicle": None, "display_vehicle": None, "label": ""}}
                for row in unknown
            ]

        return {
            "known": known,
            "unknown": unknown,
            "top_known": known[0] if known else None,
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    async def evaluate_known_overtake(self, event_id: uuid.UUID | str) -> dict[str, Any]:
        parsed_event_id = _coerce_uuid(event_id)
        if not parsed_event_id:
            return {"changed": False, "reason": "invalid_event_id"}

        async with AsyncSessionLocal() as session:
            event = await session.get(AccessEvent, parsed_event_id)
            if not self._should_evaluate_event(event):
                return {"changed": False, "reason": "not_known_granted_entry"}

            top = await self._current_top_known_leader(session)
            if not top:
                return {"changed": False, "reason": "no_known_leader"}

            state = await session.get(LeaderboardState, KNOWN_TOP_STATE_KEY)
            if not state:
                state = LeaderboardState(key=KNOWN_TOP_STATE_KEY)
                self._apply_state(state, top, parsed_event_id)
                session.add(state)
                await session.commit()
                return {"changed": False, "initialized": True, "top_known": top}

            previous = await self._leader_from_state(session, state)
            if self._same_leader(state, top):
                self._apply_state(state, top, parsed_event_id)
                await session.commit()
                return {"changed": False, "top_known": top}

            self._apply_state(state, top, parsed_event_id)
            await session.commit()

        payload = self._overtake_payload(top, previous)
        await event_bus.publish("leaderboard_overtake", payload)
        await get_notification_service().notify(
            NotificationContext(
                event_type="leaderboard_overtake",
                subject=f"{payload['new_winner_name']} took the Top Charts lead",
                severity=AnomalySeverity.INFO.value,
                facts=self._overtake_notification_facts(payload),
            )
        )
        return {"changed": True, **payload}

    async def _known_leaders(self, session: AsyncSession, limit: int) -> list[dict[str, Any]]:
        count_expr = func.count(AccessEvent.id)
        last_seen_expr = func.max(AccessEvent.occurred_at)
        aggregate = (
            select(
                AccessEvent.registration_number.label("registration_number"),
                AccessEvent.vehicle_id.label("vehicle_id"),
                AccessEvent.person_id.label("person_id"),
                count_expr.label("read_count"),
                last_seen_expr.label("last_seen_at"),
            )
            .where(
                AccessEvent.vehicle_id.is_not(None),
                AccessEvent.decision == AccessDecision.GRANTED,
                AccessEvent.direction == AccessDirection.ENTRY,
            )
            .group_by(AccessEvent.registration_number, AccessEvent.vehicle_id, AccessEvent.person_id)
            .subquery()
        )
        query = (
            select(
                aggregate.c.registration_number,
                aggregate.c.vehicle_id,
                aggregate.c.person_id,
                aggregate.c.read_count,
                aggregate.c.last_seen_at,
                Vehicle,
                Person,
            )
            .outerjoin(Vehicle, Vehicle.id == aggregate.c.vehicle_id)
            .outerjoin(Person, Person.id == aggregate.c.person_id)
            .order_by(
                aggregate.c.read_count.desc(),
                aggregate.c.last_seen_at.desc(),
                aggregate.c.registration_number.asc(),
            )
            .limit(limit)
        )
        rows = (await session.execute(query)).all()
        return [
            self._serialize_known_leader(
                rank=index,
                registration_number=str(registration_number),
                vehicle_id=vehicle_id,
                person_id=person_id,
                read_count=int(read_count or 0),
                last_seen_at=last_seen_at,
                vehicle=vehicle,
                person=person,
            )
            for index, (
                registration_number,
                vehicle_id,
                person_id,
                read_count,
                last_seen_at,
                vehicle,
                person,
            ) in enumerate(rows, start=1)
        ]

    async def _unknown_leaders(self, session: AsyncSession, limit: int) -> list[dict[str, Any]]:
        count_expr = func.count(AccessEvent.id)
        first_seen_expr = func.min(AccessEvent.occurred_at)
        last_seen_expr = func.max(AccessEvent.occurred_at)
        query = (
            select(
                AccessEvent.registration_number,
                count_expr.label("read_count"),
                first_seen_expr.label("first_seen_at"),
                last_seen_expr.label("last_seen_at"),
            )
            .where(
                AccessEvent.vehicle_id.is_(None),
                AccessEvent.decision == AccessDecision.DENIED,
            )
            .group_by(AccessEvent.registration_number)
            .order_by(count_expr.desc(), last_seen_expr.desc(), AccessEvent.registration_number.asc())
            .limit(limit)
        )
        rows = (await session.execute(query)).all()
        return [
            {
                "rank": index,
                "registration_number": str(registration_number),
                "read_count": int(read_count or 0),
                "first_seen_at": _datetime_iso(first_seen_at),
                "last_seen_at": _datetime_iso(last_seen_at),
                "dvla": {"status": "pending", "vehicle": None, "display_vehicle": None, "label": ""},
            }
            for index, (registration_number, read_count, first_seen_at, last_seen_at)
            in enumerate(rows, start=1)
        ]

    async def _current_top_known_leader(self, session: AsyncSession) -> dict[str, Any] | None:
        leaders = await self._known_leaders(session, 1)
        return leaders[0] if leaders else None

    async def _leader_from_state(
        self,
        session: AsyncSession,
        state: LeaderboardState | None,
    ) -> dict[str, Any] | None:
        if not state or not state.registration_number:
            return None
        vehicle = await session.get(Vehicle, state.vehicle_id) if state.vehicle_id else None
        person = await session.get(Person, state.person_id) if state.person_id else None
        return self._serialize_known_leader(
            rank=1,
            registration_number=state.registration_number,
            vehicle_id=state.vehicle_id,
            person_id=state.person_id,
            read_count=state.read_count,
            last_seen_at=None,
            vehicle=vehicle,
            person=person,
        )

    async def _enrich_unknowns(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(UNKNOWN_DVLA_CONCURRENCY)

        async def enrich(row: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return {**row, "dvla": await self._lookup_unknown_vehicle(row["registration_number"])}

        return list(await asyncio.gather(*(enrich(row) for row in rows)))

    async def _lookup_unknown_vehicle(self, registration_number: str) -> dict[str, Any]:
        try:
            vehicle = await lookup_vehicle_registration(registration_number)
        except DvlaVehicleEnquiryError as exc:
            status = "unconfigured" if _looks_like_unconfigured_dvla(exc) else "failed"
            return {
                "status": status,
                "vehicle": None,
                "display_vehicle": None,
                "label": "",
                "error": str(exc),
            }
        except Exception as exc:
            logger.warning(
                "leaderboard_dvla_lookup_failed",
                extra={"registration_number": registration_number, "error": str(exc)},
            )
            return {
                "status": "failed",
                "vehicle": None,
                "display_vehicle": None,
                "label": "",
                "error": str(exc),
            }

        display_vehicle = display_vehicle_record(vehicle, registration_number)
        return {
            "status": "ok",
            "vehicle": vehicle,
            "display_vehicle": display_vehicle,
            "normalized_vehicle": normalize_vehicle_enquiry_response(
                vehicle,
                registration_number,
                display_vehicle=display_vehicle,
            ).as_payload(),
            "label": _dvla_vehicle_label(display_vehicle),
        }

    def _serialize_known_leader(
        self,
        *,
        rank: int,
        registration_number: str,
        vehicle_id: uuid.UUID | str | None,
        person_id: uuid.UUID | str | None,
        read_count: int,
        last_seen_at: datetime | None,
        vehicle: Vehicle | None,
        person: Person | None,
    ) -> dict[str, Any]:
        vehicle_label = _vehicle_display_name(vehicle, registration_number)
        return {
            "rank": rank,
            "registration_number": registration_number,
            "read_count": read_count,
            "last_seen_at": _datetime_iso(last_seen_at),
            "vehicle_id": _uuid_text(vehicle_id),
            "person_id": _uuid_text(person_id),
            "first_name": person.first_name if person else "",
            "display_name": person.display_name if person else registration_number,
            "vehicle_name": vehicle_label,
            "person": {
                "id": _uuid_text(person.id) if person else None,
                "first_name": person.first_name if person else "",
                "last_name": person.last_name if person else "",
                "display_name": person.display_name if person else registration_number,
                "profile_photo_data_url": person.profile_photo_data_url if person else None,
            },
            "vehicle": {
                "id": _uuid_text(vehicle.id) if vehicle else _uuid_text(vehicle_id),
                "registration_number": vehicle.registration_number if vehicle else registration_number,
                "vehicle_photo_data_url": vehicle.vehicle_photo_data_url if vehicle else None,
                "make": vehicle.make if vehicle else "",
                "model": vehicle.model if vehicle else "",
                "color": vehicle.color if vehicle else "",
                "description": vehicle.description if vehicle else "",
                "display_name": vehicle_label,
            },
        }

    def _should_evaluate_event(self, event: AccessEvent | None) -> bool:
        return bool(
            event
            and event.vehicle_id
            and event.decision == AccessDecision.GRANTED
            and event.direction == AccessDirection.ENTRY
        )

    def _same_leader(self, state: LeaderboardState, leader: dict[str, Any]) -> bool:
        return (
            (state.registration_number or "") == str(leader.get("registration_number") or "")
            and _uuid_text(state.vehicle_id) == str(leader.get("vehicle_id") or "")
            and _uuid_text(state.person_id) == str(leader.get("person_id") or "")
        )

    def _apply_state(
        self,
        state: LeaderboardState,
        leader: dict[str, Any],
        event_id: uuid.UUID,
    ) -> None:
        state.registration_number = str(leader.get("registration_number") or "")
        state.vehicle_id = _coerce_uuid(leader.get("vehicle_id"))
        state.person_id = _coerce_uuid(leader.get("person_id"))
        state.read_count = int(leader.get("read_count") or 0)
        state.last_event_id = event_id

    def _overtake_payload(
        self,
        new_leader: dict[str, Any],
        previous_leader: dict[str, Any] | None,
    ) -> dict[str, Any]:
        new_winner_name = _winner_name(new_leader)
        overtaken_name = _winner_name(previous_leader) if previous_leader else "the previous leader"
        return {
            "new_winner": new_leader,
            "overtaken": previous_leader,
            "new_winner_name": new_winner_name,
            "overtaken_name": overtaken_name,
            "read_count": int(new_leader.get("read_count") or 0),
            "vehicle_name": str(new_leader.get("vehicle_name") or new_leader.get("registration_number") or ""),
            "registration_number": str(new_leader.get("registration_number") or ""),
            "message": (
                f"{new_winner_name} has overtaken {overtaken_name} "
                f"with {int(new_leader.get('read_count') or 0)} reads."
            ),
        }

    def _overtake_notification_facts(self, payload: dict[str, Any]) -> dict[str, str]:
        new_vehicle = payload.get("new_winner", {}).get("vehicle", {}) if isinstance(payload.get("new_winner"), dict) else {}
        return {
            "new_winner_name": str(payload.get("new_winner_name") or ""),
            "overtaken_name": str(payload.get("overtaken_name") or ""),
            "read_count": str(payload.get("read_count") or ""),
            "vehicle_name": str(payload.get("vehicle_name") or ""),
            "registration_number": str(payload.get("registration_number") or ""),
            "vehicle_registration_number": str(payload.get("registration_number") or ""),
            "vehicle_make": str(new_vehicle.get("make") or ""),
            "vehicle_model": str(new_vehicle.get("model") or ""),
            "vehicle_color": str(new_vehicle.get("color") or ""),
            "message": str(payload.get("message") or ""),
        }


def _vehicle_display_name(vehicle: Vehicle | None, fallback: str) -> str:
    if not vehicle:
        return fallback
    description = (vehicle.description or "").strip()
    if description:
        return description
    parts = [vehicle.color, vehicle.make, vehicle.model]
    label = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    return label or vehicle.registration_number or fallback


def _dvla_vehicle_label(display_vehicle: dict[str, Any]) -> str:
    parts = [
        display_vehicle.get("colour") or display_vehicle.get("color"),
        display_vehicle.get("make"),
        display_vehicle.get("model"),
    ]
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def _winner_name(leader: dict[str, Any] | None) -> str:
    if not leader:
        return "Unknown"
    person = leader.get("person") if isinstance(leader.get("person"), dict) else {}
    return str(
        person.get("display_name")
        or leader.get("display_name")
        or leader.get("registration_number")
        or "Unknown"
    )


def _datetime_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _uuid_text(value: uuid.UUID | str | None) -> str:
    return str(value) if value else ""


def _coerce_uuid(value: uuid.UUID | str | Any | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _looks_like_unconfigured_dvla(exc: DvlaVehicleEnquiryError) -> bool:
    return exc.status_code == 400 and "not configured" in str(exc).lower()


@lru_cache
def get_leaderboard_service() -> LeaderboardService:
    return LeaderboardService()
