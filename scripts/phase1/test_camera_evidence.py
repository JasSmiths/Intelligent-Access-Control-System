"""Real PostgreSQL access re-resolution; no camera, LLM, actuator or other live I/O.

Collection first requires the existing loopback-only synthetic recovery namespace.
"""
from test_recovery_boundaries import isolated_resources as isolated_resources

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.models import AccessEvent, Person, Presence, SystemSetting, Vehicle
from app.models.enums import AccessDecision, AccessDirection, PresenceState
from app.modules.lpr.base import PlateRead
from app.services.access import execution as execution_owner
from app.services.access.evidence import AccessEvidenceResolver
from app.services.access.execution import AccessExecution
from app.services.access.reads import DebounceWindow, GATE_OBSERVATION_PAYLOAD_KEY
from app.services.lpr_ingest import LprIngestRepository
from app.services.movement.sessions import MovementSessionService
from app.services.movement_ledger import get_movement_ledger_repository
from app.services.settings import get_runtime_config_for_session

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


class Trace:
    trace_id = "synthetic-camera-handoff"

    def start_span(self, *args, **kwargs):
        return SimpleNamespace(finish=lambda **kwargs: None)


@pytest.mark.parametrize("mutation", ["unchanged", "owner", "presence", "deactivated", "runtime_policy"])
async def test_access_execution_re_resolves_current_facts_after_camera_transaction_ends(monkeypatch, mutation):
    async with AsyncSessionLocal() as session:
        original = Person(display_name="Synthetic Original", is_active=True)
        other = Person(display_name="Synthetic Other", is_active=True)
        session.add_all([original, other])
        await session.flush()
        vehicle = Vehicle(registration_number="SYN123", person_id=original.id, is_active=True)
        session.add(vehicle)
        for person in (original, other):
            session.add(Presence(person_id=person.id, state=PresenceState.PRESENT,
                                 last_changed_at=NOW - timedelta(minutes=1)))
        await session.commit()
        original_id, other_id, vehicle_id = original.id, other.id, vehicle.id
        runtime = await get_runtime_config_for_session(session)
    assert runtime.schedule_default_policy == "allow"
    read = PlateRead("SYN123", 0.99, "synthetic-camera-handoff", NOW, {
        "cameraId": "synthetic-camera", "deviceId": "synthetic-device",
        GATE_OBSERVATION_PAYLOAD_KEY: {"state": "closed", "observed_at": NOW.isoformat()},
    })
    entered_camera = asyncio.Event()
    release_camera = asyncio.Event()
    camera_calls = []

    async def inert_camera(self, observation, person, *, trace=None):
        # No probe/preflight transaction or checked-out connection may span this await.
        assert engine.pool.checkedout() == 0
        camera_calls.append((observation, person.id))
        entered_camera.set()
        await release_camera.wait()
        return {"direction": "exit", "confidence": 0.99, "reason": "Synthetic paused evidence"}

    monkeypatch.setattr(AccessEvidenceResolver, "_resolve_duplicate_arrival_with_camera", inert_camera)
    # Exercise the full access transaction and finalizer, with one inert actuator boundary.
    hardware = AsyncMock(return_value=SimpleNamespace(command_id=None, accepted=False))
    monkeypatch.setattr(execution_owner, "open_gate_for_access_event", hardware)
    service = AccessExecution(get_movement_ledger_repository(), MovementSessionService(), LprIngestRepository())
    task = asyncio.create_task(service.execute(DebounceWindow(NOW, NOW, [read]), read=read,
        direction_read=read, runtime=runtime, trace=Trace(), finalize_started_at=NOW, webhook_trace={}))
    try:
        await asyncio.wait_for(entered_camera.wait(), 8)
        async with AsyncSessionLocal() as session:
            if mutation == "owner":
                (await session.get(Vehicle, vehicle_id)).person_id = other_id
            elif mutation == "presence":
                (await session.get(Presence, original_id)).state = PresenceState.EXITED
            elif mutation == "deactivated":
                (await session.get(Person, original_id)).is_active = False
            elif mutation == "runtime_policy":
                session.add(SystemSetting(key="schedule_default_policy", category="access", value={"plain": "deny"}, is_secret=False))
            await session.commit()
        release_camera.set()
        result = await asyncio.wait_for(task, 8)
        assert result is not None
        assert camera_calls == [(read, original_id)]
        if mutation in {"deactivated", "runtime_policy"}:
            assert result.event.decision == AccessDecision.DENIED
            assert result.event.direction == AccessDirection.DENIED
            hardware.assert_not_awaited()
            assert not result.presence_updated
        elif mutation == "unchanged":
            assert result.event.decision == AccessDecision.GRANTED
            assert result.event.direction == AccessDirection.EXIT
            assert result.presence_updated
            hardware.assert_not_awaited()
        else:
            assert result.event.decision == AccessDecision.GRANTED
            assert result.event.direction == AccessDirection.ENTRY
            assert result.event.person_id == (other_id if mutation == "owner" else original_id)
            assert result.direction_resolution["source"] == "gate_state"
            hardware.assert_awaited_once()
            assert not result.presence_updated  # Inert sink has no admission evidence.
        async with AsyncSessionLocal() as session:
            durable = await session.get(AccessEvent, result.event.id)
            assert durable.person_id == result.event.person_id and durable.direction == result.event.direction
            if mutation == "unchanged":
                presence = await session.get(Presence, original_id)
                assert presence.state == PresenceState.EXITED and presence.last_event_id == durable.id
            if mutation == "runtime_policy":
                assert durable.raw_payload["schedule"]["allowed"] is False
                assert result.runtime.schedule_default_policy == "deny"
            assert len(list((await session.scalars(select(AccessEvent))).all())) == 1
    finally:
        release_camera.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # Restore the synthetic fixture prerequisite even when an assertion fails.
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SystemSetting).where(SystemSetting.key == "schedule_default_policy"))
            await session.commit()
