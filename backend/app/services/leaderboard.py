import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.task_lifecycle import drain_owned
from app.db.session import AsyncSessionLocal
from app.models import AccessEvent, LeaderboardState
from app.models.enums import AccessDecision, AccessDirection, AnomalySeverity
from app.modules.notifications.base import NotificationContext
from app.services.event_bus import event_bus
from app.services.leaderboard_reads import (
    KnownLeader,
    UnknownLeader,
    known_leader_from_state_in_session,
    known_leaders_in_session,
    unknown_leaders_in_session,
)
from app.services.notifications import get_notification_service
from app.services.type_helpers import as_dict
from app.services.vehicle_information import get_vehicle_information_service

logger = get_logger(__name__)

KNOWN_TOP_STATE_KEY = "known_top_plate"
UNKNOWN_INFORMATION_CONCURRENCY = 4


class LeaderboardService:
    """Aggregates plate-read leaderboards and tracks the known-plate top spot."""

    async def get_leaderboard(
        self, *, limit: int = 25, enrich_unknowns: bool = True
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        session = AsyncSessionLocal()
        try:
            known = await known_leaders_in_session(session, limit)
            unknown = await unknown_leaders_in_session(session, limit)
        finally:
            await drain_owned(session.close())

        if enrich_unknowns:
            unknown = await self._enrich_unknowns(unknown)
        else:
            unknown = [
                {
                    **row,
                    "vehicle_information": {
                        "status": "skipped",
                        "information": None,
                        "label": "",
                    },
                }
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

    async def _current_top_known_leader(self, session: AsyncSession) -> KnownLeader | None:
        leaders = await known_leaders_in_session(session, 1)
        return leaders[0] if leaders else None

    async def _leader_from_state(
        self,
        session: AsyncSession,
        state: LeaderboardState | None,
    ) -> dict[str, Any] | None:
        if not state or not state.registration_number:
            return None
        return await known_leader_from_state_in_session(
            session, registration_number=state.registration_number,
            vehicle_id=state.vehicle_id, person_id=state.person_id,
            read_count=state.read_count,
        )

    async def _enrich_unknowns(self, rows: Sequence[UnknownLeader]) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(UNKNOWN_INFORMATION_CONCURRENCY)

        async def enrich(row: UnknownLeader) -> dict[str, Any]:
            async with semaphore:
                return {
                    **row,
                    "vehicle_information": await self._lookup_unknown_vehicle(row["registration_number"]),
                }

        return list(await asyncio.gather(*(enrich(row) for row in rows)))

    async def _lookup_unknown_vehicle(self, registration_number: str) -> dict[str, Any]:
        info = (await get_vehicle_information_service().lookup(registration_number, cached_only=True)).information
        return {"status": "ok" if info.make or info.model else "unavailable",
                "information": info.model_dump(mode="json"),
                "label": " ".join(part for part in (info.colour, info.make, info.model) if part)}

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
            "vehicle_name": str(
                new_leader.get("vehicle_name") or new_leader.get("registration_number") or ""
            ),
            "registration_number": str(new_leader.get("registration_number") or ""),
            "message": (
                f"{new_winner_name} has overtaken {overtaken_name} "
                f"with {int(new_leader.get('read_count') or 0)} reads."
            ),
        }

    def _overtake_notification_facts(self, payload: dict[str, Any]) -> dict[str, str]:
        new_vehicle = (
            payload.get("new_winner", {}).get("vehicle", {})
            if isinstance(payload.get("new_winner"), dict)
            else {}
        )
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


def _winner_name(leader: dict[str, Any] | None) -> str:
    if not leader:
        return "Unknown"
    person = as_dict(leader.get("person"))
    return str(
        person.get("display_name")
        or leader.get("display_name")
        or leader.get("registration_number")
        or "Unknown"
    )


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


@lru_cache
def get_leaderboard_service() -> LeaderboardService:
    return LeaderboardService()
