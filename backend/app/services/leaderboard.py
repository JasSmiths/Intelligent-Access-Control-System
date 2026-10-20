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
from app.models.enums import AnomalySeverity
from app.modules.notifications.base import NotificationContext
from app.services.durable_work import WorkTopic, notify_work
from app.services.event_bus import event_bus
from app.services.leaderboard_dirty import ChartClaim, LeaderboardDirtyStore
from app.services.leaderboard_notifications import chart_notification_id, chart_notification_origin
from app.services.leaderboard_reads import (
    KnownLeader,
    UnknownLeader,
    known_leader_from_state_in_session,
    known_leaders_in_session,
    unknown_leaders_in_session,
)
from app.services.leaderboard_worker import LeaderboardWorker
from app.services.notification_runs import NotificationRunStore
from app.services.type_helpers import as_dict
from app.services.vehicle_information import get_vehicle_information_service
from app.services.workflows.notification_payloads import notification_context_payload

logger = get_logger(__name__)

KNOWN_TOP_STATE_KEY = "known_top_plate"
UNKNOWN_INFORMATION_CONCURRENCY = 4


class LeaderboardService:
    """Aggregates plate-read leaderboards and tracks the known-plate top spot."""

    def __init__(self, dirty_store: LeaderboardDirtyStore | None = None) -> None:
        self.dirty_store = dirty_store or LeaderboardDirtyStore()
        self.worker = LeaderboardWorker(self.dirty_store, self.evaluate_claim)

    async def start(self) -> None:
        await self.worker.start()

    async def stop(self) -> None:
        await self.worker.stop()

    def wake(self) -> None:
        self.worker.wake()

    async def mark_access_in_session(
        self, session: AsyncSession, event: AccessEvent, *, origin_at: datetime | None
    ) -> bool:
        return await self.dirty_store.mark_access_in_session(session, event, origin_at=origin_at)

    async def mark_dirty_in_session(self, session: AsyncSession) -> None:
        await self.dirty_store.mark_dirty_in_session(session)

    async def evaluate_claim(self, claim: ChartClaim) -> None:
        # Aggregation holds neither the dirty row nor any source mutation lock.
        # A valid A calculation commits even when every read admits a trailing B;
        # otherwise continuous arrivals could starve leader progress forever.
        async with self.dirty_store.session() as session:
            top = await self._current_top_known_leader(session)
        payload: dict[str, Any] | None = None
        async with self.dirty_store.session() as session:
            dirty, _ = await self.dirty_store.lock_owned(session, claim)
            if top is not None:
                state = await session.get(LeaderboardState, KNOWN_TOP_STATE_KEY, with_for_update=True)
                if state is None:
                    state = LeaderboardState(key=KNOWN_TOP_STATE_KEY)
                    session.add(state)
                elif not self._same_leader(state, top):
                    payload = self._overtake_payload(top, await self._leader_from_state(session, state))
                self._apply_state(state, top, claim.live_event_id)
            # Flush state before the last fresh clock/lease check. Notification
            # intake joins this same transaction; rollback leaves A dirty.
            await session.flush()
            dirty, now = await self.dirty_store.lock_owned(session, claim)
            if (payload is not None and claim.live_event_id is not None
                    and claim.live_origin_at is not None and claim.live_deadline is not None
                    and claim.live_origin_at <= now < claim.live_deadline):
                context = NotificationContext(
                    event_type="leaderboard_overtake",
                    subject=f"{payload['new_winner_name']} took the Top Charts lead",
                    severity=AnomalySeverity.INFO.value,
                    facts=self._overtake_notification_facts(payload),
                )
                notice = notification_context_payload(context)
                notice["leaderboard_origin"] = chart_notification_origin(
                    claim.generation, claim.live_event_id, claim.live_origin_at
                )
                await NotificationRunStore().enqueue_in_session(
                    session, notice, run_id=chart_notification_id(claim.generation)
                )
            dirty, _ = await self.dirty_store.lock_owned(session, claim)
            self.dirty_store.acknowledge(dirty, claim)

            await notify_work(session, WorkTopic.CHART)
            await session.commit()
        if payload is not None:
            try:
                await event_bus.publish("leaderboard_overtake", payload)
            except Exception:  # noqa: BLE001 - realtime is observational; committed state and intent are durable
                logger.warning("Leaderboard realtime update unavailable")

    async def get_leaderboard(
        self, *, limit: int = 25, enrich_unknowns: bool = True
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 100))
        session = AsyncSessionLocal()
        try:
            known = await known_leaders_in_session(session, limit)
            unknown_rows = await unknown_leaders_in_session(session, limit)
        finally:
            await drain_owned(session.close())

        if enrich_unknowns:
            unknown = await self._enrich_unknowns(unknown_rows)
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
                for row in unknown_rows
            ]

        return {
            "known": known,
            "unknown": unknown,
            "top_known": known[0] if known else None,
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }

    async def _current_top_known_leader(self, session: AsyncSession) -> KnownLeader | None:
        leaders = await known_leaders_in_session(session, 1)
        return leaders[0] if leaders else None

    async def _leader_from_state(
        self,
        session: AsyncSession,
        state: LeaderboardState | None,
    ) -> KnownLeader | None:
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

    def _same_leader(self, state: LeaderboardState, leader: KnownLeader) -> bool:
        return (
            (state.registration_number or "") == str(leader.get("registration_number") or "")
            and _uuid_text(state.vehicle_id) == str(leader.get("vehicle_id") or "")
            and _uuid_text(state.person_id) == str(leader.get("person_id") or "")
        )

    def _apply_state(
        self,
        state: LeaderboardState,
        leader: KnownLeader,
        event_id: uuid.UUID | None,
    ) -> None:
        state.registration_number = str(leader.get("registration_number") or "")
        state.vehicle_id = _coerce_uuid(leader.get("vehicle_id"))
        state.person_id = _coerce_uuid(leader.get("person_id"))
        state.read_count = int(leader.get("read_count") or 0)
        state.last_event_id = event_id

    def _overtake_payload(
        self,
        new_leader: KnownLeader,
        previous_leader: KnownLeader | None,
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


def _winner_name(leader: KnownLeader | None) -> str:
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
